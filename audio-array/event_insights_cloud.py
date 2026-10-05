"""Text-only OpenAI Responses calls with a persistent, fail-closed daily budget.

The ledger stores hashes and token/cost counts, never credentials or source text.
Validated responses are cached under gitignored data/event-audio/dashboard.
Reservations survive crashes, timeouts and failed validation. Each call makes
one attempt; the application may authorize a bounded retry as a separate call.
"""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import tempfile
import time
from urllib import error, request
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data/event-audio/dashboard"
DOTENV_PATH = None  # Only an explicitly selected file; otherwise use OPENAI_API_KEY.
BUDGET_AUTHORIZATION_PATH = None  # Explicit app configuration; never inferred from env.
MAX_BUDGET_AUTHORIZATION_BYTES = 1024 * 1024
API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6-luna"
# Official Standard short-context prices verified 2026-10-03:
# https://developers.openai.com/api/docs/models/gpt-6-luna
# https://developers.openai.com/api/docs/models/gpt-6.1-sol
# https://developers.openai.com/api/docs/pricing
# USD / million tokens (input / cached / cache-write / output):
# Luna .10 / .01 / .125 / .50; Sol 2 / .10 / 2.50 / 10.
# Integer nanodollars per token avoid floating-point budget rounding.
PRICES = {DEFAULT_MODEL: {"input": 100, "cached": 10, "write": 125, "output": 500},
          "gpt-6.1-sol": {"input": 2000, "cached": 100, "write": 2500, "output": 10000}}
REASONING_EFFORTS = {DEFAULT_MODEL: "none", "gpt-6.1-sol": "low"}
PRICING_DATE = "2026-10-03"
NANODOLLARS = 1_000_000_000
MAX_OUTPUT_TOKENS = 6144
MAX_BODY_BYTES = 100_000  # Always below the 272K long-context pricing boundary.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class CloudError(RuntimeError):
    """Public diagnostics contain only fixed categories and allowlisted codes."""
    def __init__(self, message, *, category='unknown', retryable=False, http_status=None,
                 provider_code=None, retry_after_seconds=None):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.http_status = http_status
        self.provider_code = provider_code
        self.retry_after_seconds = retry_after_seconds

    def diagnostics(self):
        return {'category': self.category, 'retryable': self.retryable,
                'http_status': self.http_status, 'provider_code': self.provider_code,
                'retry_after_seconds': self.retry_after_seconds}


class BudgetExceededError(CloudError):
    def __init__(self, message):
        super().__init__(message, category='budget')


def _retry_after(headers):
    value = headers.get('Retry-After') if headers else None
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        delay = float(value)
        return delay if math.isfinite(delay) and delay >= 0 else None
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            delay = date.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, delay) if math.isfinite(delay) else None


def _http_error(exc):
    # Error bodies may contain source text or credentials. Retain only known
    # machine codes, never the message, arbitrary type, headers, or request ID.
    known = {'rate_limit_exceeded', 'insufficient_quota', 'billing_hard_limit_reached',
             'billing_not_active', 'usage_limit_reached', 'invalid_api_key',
             'invalid_request_error', 'server_error'}
    codes = set()
    try:
        raw = exc.read(16385)
        body = json.loads(raw) if len(raw) <= 16384 else None
        detail = body.get('error') if isinstance(body, dict) else None
        if isinstance(detail, dict):
            codes = {value for value in (detail.get('code'), detail.get('type'))
                     if isinstance(value, str) and value in known}
    except (OSError, ValueError, TypeError):
        pass
    finally:
        exc.close()
    quota = codes & {'insufficient_quota', 'billing_hard_limit_reached', 'billing_not_active', 'usage_limit_reached'}
    status = exc.code
    if quota:
        category, retryable, code = 'quota', False, sorted(quota)[0]
    elif status in (401, 403) or 'invalid_api_key' in codes:
        category, retryable, code = 'authentication', False, 'invalid_api_key' if 'invalid_api_key' in codes else None
    elif status == 429:
        category, retryable, code = ('rate_limit', True, 'rate_limit_exceeded') if 'rate_limit_exceeded' in codes else ('unknown', False, None)
    elif status == 408 or 500 <= status < 600:
        category, retryable, code = 'server', True, 'server_error' if 'server_error' in codes else None
    else:
        category, retryable, code = 'invalid_request', False, 'invalid_request_error' if 'invalid_request_error' in codes else None
    return CloudError(f'OpenAI APIがHTTP {status}を返しました。', category=category,
        retryable=retryable, http_status=status, provider_code=code,
        retry_after_seconds=_retry_after(exc.headers))


def _api_key():
    """Read only the authorized key, without executing .env or exporting secrets."""
    value = os.environ.get("OPENAI_API_KEY", "").strip()
    if not value and DOTENV_PATH is not None and DOTENV_PATH.is_file():
        if DOTENV_PATH.stat().st_size > 65536:
            raise CloudError(".envが大きすぎるためAPIキーを読み取れません。")
        for line in DOTENV_PATH.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*)$", line)
            if match:
                try:
                    parts = shlex.split(match.group(1), comments=True)
                except ValueError as exc:
                    raise CloudError(".envのAPIキー設定形式を確認してください。") from exc
                value = parts[0] if len(parts) == 1 else ""
                break
    if value and (any(char.isspace() for char in value) or any(ord(char) < 33 for char in value)):
        raise CloudError("APIキー設定形式を確認してください。")
    return value


def has_api_key():
    return bool(_api_key())


def _day():
    return datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()


def _unique_budget_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate authorization field")
        result[key] = value
    return result


def _read_budget_authorization(path):
    """Read the copied human authorization, without keys, ledger writes, or API."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_BUDGET_AUTHORIZATION_BYTES + 1)
        if len(raw) > MAX_BUDGET_AUTHORIZATION_BYTES:
            raise ValueError("authorization too large")
        value = json.loads(raw, object_pairs_hook=_unique_budget_fields,
                           parse_float=Decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(value, dict) or value.get("human_approved") is not True
                or value.get("destination") != API_URL or value.get("raw_audio_allowed") is not False):
            raise ValueError("unapproved destination or content scope")
        dates = value.get("allowed_dates")
        amounts = value.get("daily_budget_usd_by_date")
        if (not isinstance(dates, list) or not dates or any(not isinstance(day, str) for day in dates)
                or len(set(dates)) != len(dates) or not isinstance(amounts, dict) or not amounts):
            raise ValueError("missing date-specific budget")
        for day in dates:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise ValueError("invalid date")
            datetime.strptime(day, "%Y-%m-%d")
        budgets = {}
        for day, amount in amounts.items():
            if day not in dates or type(amount) not in (int, Decimal):
                raise ValueError("budget outside authorized dates or invalid amount")
            amount = Decimal(amount)
            if not amount.is_finite() or amount < 0:
                raise ValueError("invalid explicitly approved amount")
            # Round down, never granting a fractional nanodollar above the cap.
            budgets[day] = int(amount * NANODOLLARS)
        return budgets
    except (OSError, ValueError, TypeError, UnicodeError, InvalidOperation, OverflowError) as exc:
        raise CloudError("日付別のAPI予算承認を確認できません。記録を消さずに確認してください。") from exc


def configure_budget_authorization(path):
    """Activate a copied human-approved budget file in this process only.

    This record does not create authorization by itself. The caller must already
    have the user's approval. The same file is re-read before each reservation;
    revocation/corruption fails closed. Existing shared ledger entries are never
    changed here. An invalid requested file remains selected so callers cannot
    accidentally continue under an older, more permissive authorization.
    """
    global BUDGET_AUTHORIZATION_PATH
    BUDGET_AUTHORIZATION_PATH = Path(path).expanduser().resolve()
    _read_budget_authorization(BUDGET_AUTHORIZATION_PATH)


def _budget(day=None):
    day = _day() if day is None else day
    if BUDGET_AUTHORIZATION_PATH is not None:
        approved = _read_budget_authorization(BUDGET_AUTHORIZATION_PATH)
        if day in approved:
            return approved[day]
    # No implicit allowance: a key, an environment variable, or an approval for
    # a different date cannot enable spending today.
    return 0


def _load_ledger():
    path = STATE_DIR / "cloud-budget.json"
    if not path.exists():
        return {"schema_version": 1, "requests": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data["requests"], dict):
            raise ValueError()
        for key, item in data["requests"].items():
            if (not re.fullmatch(r"[0-9a-f]{64}", key) or not isinstance(item, dict)
                    or not isinstance(item.get("day"), str)
                    or type(item.get("charged_nanodollars")) is not int or item["charged_nanodollars"] < 0
                    or item.get("state") not in {"reserved", "failed", "completed", "completed_usage_unknown"}):
                raise ValueError()
        return data
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        raise CloudError("費用ledgerを読み取れないためクラウド処理を停止しました。") from exc


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".cloud-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _locked_ledger():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "cloud-budget.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield _load_ledger()
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _spent(ledger, day):
    return sum(item["charged_nanodollars"] for item in ledger["requests"].values() if item["day"] == day)


def budget_status():
    # Atomic replacement makes this read safe without creating a file during probe.
    ledger, day = _load_ledger(), _day()
    budget = _budget(day)
    spent = _spent(ledger, day)
    held = sum(item["charged_nanodollars"] for item in ledger["requests"].values()
               if item["day"] == day and item["state"] != "completed")
    return {"spent_usd": spent / NANODOLLARS, "budget_usd": budget / NANODOLLARS,
            "reserved_usd": held / NANODOLLARS, "budget_date": day, "budget_timezone": "Asia/Tokyo",
            "pricing_date": PRICING_DATE}


def probe_model(model=None):
    model = model or DEFAULT_MODEL
    result = {"available": False, "provider": "openai", "model": model,
              "spent_usd": None, "budget_usd": None, "reserved_usd": None}
    try:
        result.update(budget_status())
        if model not in PRICES:
            raise CloudError("費用確認済みのクラウドモデルはgpt-6-lunaとgpt-6.1-solです。")
        if not has_api_key():
            raise CloudError("OPENAI_API_KEYが未設定です。")
        if result["spent_usd"] >= result["budget_usd"]:
            raise BudgetExceededError("本日のクラウド予算上限に達しました。")
        result.update(available=True, message="OpenAIで文字起こしと選択した参照情報を処理します。")
    except (CloudError, OSError) as exc:
        result["message"] = str(exc) if isinstance(exc, CloudError) else "クラウド設定を読み取れません。"
    return result


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CloudError("APIのHTTPリダイレクトを拒否しました。")


def _post(payload, key, timeout):
    req = request.Request(API_URL, data=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        raise _http_error(exc) from None
    except (error.URLError, TimeoutError, OSError) as exc:
        raise CloudError("OpenAI APIの通信が失敗またはタイムアウトしました。予約額は保持されます。",
                         category='transport', retryable=True) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CloudError("OpenAI APIの応答がサイズ上限を超えました。", category='invalid_response')
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CloudError("OpenAI APIの応答が不正なJSONです。", category='invalid_response') from None


def _output_text(response):
    if not isinstance(response, dict) or response.get("status") != "completed" or response.get("error"):
        raise CloudError("OpenAI APIの応答が完了しませんでした。", category='invalid_response')
    output = response.get("output")
    if not isinstance(output, list):
        raise CloudError("OpenAI APIの出力形式が不正です。", category='invalid_response')
    parts = []
    for item in output:
        if not isinstance(item, dict):
            raise CloudError("OpenAI APIの出力形式が不正です。", category='invalid_response')
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("status") != "completed":
            raise CloudError("OpenAI APIが予期しない出力を返しました。", category='invalid_response')
        content = item.get("content")
        if not isinstance(content, list):
            raise CloudError("OpenAI APIの出力形式が不正です。", category='invalid_response')
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                raise CloudError("OpenAI APIが要約を返しませんでした。", category='invalid_response')
            parts.append(part["text"])
    if not parts:
        raise CloudError("OpenAI APIの応答が空です。", category='invalid_response')
    return "".join(parts)


def _actual_cost(response, model):
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None, None
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        return None, None
    values = [usage.get("input_tokens"), usage.get("output_tokens"),
              details.get("cached_tokens"), details.get("cache_write_tokens")]
    # Missing cache-write usage is not equivalent to zero; hold the reservation.
    if any(type(value) is not int or value < 0 for value in values):
        return None, None
    incoming, outgoing, cached, written = values
    if cached + written > incoming or incoming > 272000 or outgoing > MAX_OUTPUT_TOKENS:
        return None, None
    rate = PRICES[model]
    cost = ((incoming - cached - written) * rate["input"] + cached * rate["cached"]
            + written * rate["write"] + outgoing * rate["output"])
    return cost, {"input_tokens": incoming, "output_tokens": outgoing,
                  "cached_tokens": cached, "cache_write_tokens": written}


def _compatible_schema(schema):
    # uniqueItems is checked by our validator and is outside OpenAI's subset.
    if isinstance(schema, dict):
        result = {key: _compatible_schema(value) for key, value in schema.items() if key != "uniqueItems"}
        if result.get("type") == "array" and "items" not in result:
            result["items"] = {"type": "string"}
        return result
    if isinstance(schema, list):
        return [_compatible_schema(value) for value in schema]
    return schema


def build_payload(messages, schema, model=None):
    """Build a bounded text-only payload without credentials, I/O or inference."""
    model = model or DEFAULT_MODEL
    if model not in PRICES or model not in REASONING_EFFORTS:
        raise CloudError("費用確認済みのクラウドモデルはgpt-6-lunaとgpt-6.1-solです。")
    if not isinstance(messages, list) or any(not isinstance(item, dict) or set(item) != {"role", "content"}
            or item.get("role") not in {"system", "developer", "user"}
            or not isinstance(item.get("content"), str) for item in messages):
        raise CloudError("クラウド入力は文字起こしのテキストだけに限定されています。")
    payload = {"model": model, "input": messages, "store": False, "stream": False,
               "reasoning": {"effort": REASONING_EFFORTS[model]}, "service_tier": "default",
               "max_output_tokens": MAX_OUTPUT_TOKENS, "truncation": "disabled",
               "text": {"format": {"type": "json_schema", "name": "event_insights",
                                    "strict": True, "schema": _compatible_schema(schema)}}}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_BODY_BYTES:
        raise CloudError("クラウド入力が上限を超えました。入力は省略していません。")
    return payload


def generate(messages, schema, *, model=None, timeout=180, retry_failed=False, validate,
             observe_response=None):
    """Reserve, infer once, observe the raw response, validate, then settle usage.

    An optional observer receives the raw response before validation, only for a
    fresh request. Observer failures keep the reservation; cache hits do not call it.
    """
    if observe_response is not None and not callable(observe_response):
        raise CloudError("応答の記録先は呼び出し可能な関数で指定してください。")
    payload = build_payload(messages, schema, model)
    model = payload["model"]
    key = _api_key()
    if not key:
        raise CloudError("OPENAI_API_KEYが未設定です。", category='configuration')
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    identity = fingerprint
    cache_path = STATE_DIR / "cloud-cache" / (fingerprint + ".json")
    # Each UTF-8 byte as a token, plus protocol/schema overhead, is conservative
    # for this model's byte-fallback tokenizer. Reserve the highest input rate.
    estimate = (len(encoded) + 4096) * max(PRICES[model][k] for k in ("input", "cached", "write"))
    estimate += MAX_OUTPUT_TOKENS * PRICES[model]["output"]
    with _locked_ledger() as ledger:
        previous = [item for key, item in ledger["requests"].items()
                    if item.get("fingerprint", key) == fingerprint]
        if previous:
            if any(item["state"].startswith("completed") for item in previous) and cache_path.is_file():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                # Revalidate after a software/schema change; no network retries.
                checked = validate(cached["output_text"])
                return {**cached, "result": checked, "cache_hit": True, **budget_status()}
            if not retry_failed:
                raise CloudError("同じ入力は処理中または前回失敗済みです。自動では再送しません。失敗後は手動で再試行できます。")
            # An explicitly admitted retry reserves another complete attempt. The previous
            # failed/unknown request remains charged; never refund it to retry.
            identity = hashlib.sha256(f"{fingerprint}:{len(previous)}".encode()).hexdigest()
        day = _day()
        budget = _budget(day)
        if _spent(ledger, day) + estimate > budget:
            raise BudgetExceededError("本日のクラウド予算に収まらないため、送信を止めました。")
        ledger["requests"][identity] = {"day": day, "state": "reserved", "model": model,
                                        "fingerprint": fingerprint,
                                        "reserved_nanodollars": estimate, "charged_nanodollars": estimate,
                                        "pricing_date": PRICING_DATE}
        _atomic_json(STATE_DIR / "cloud-budget.json", ledger)
    try:
        response = _post(payload, key, timeout)
        if observe_response is not None:
            observe_response(response)
        returned_model = response.get("model", "") if isinstance(response, dict) else ""
        if (not isinstance(returned_model, str) or not returned_model.startswith(model)
                or response.get("service_tier") not in (None, "default")):
            raise CloudError("APIが指定外のモデルまたは料金tierを返しました。予約額は保持されます。", category='invalid_response')
        text = _output_text(response)
        result = validate(text)
        actual, usage = _actual_cost(response, model)
        if actual is not None and actual > estimate:
            # Never reduce an underestimated charge. Block all further spending.
            actual = max(actual, budget)
        charged = estimate if actual is None else actual
        cached = {"output_text": text, "result": result, "usage": usage,
                  "cost_usd": charged / NANODOLLARS, "usage_confirmed": actual is not None,
                  "model": model, "provider": "openai", "cache_hit": False}
        with _locked_ledger() as ledger:
            _atomic_json(cache_path, cached)
            ledger["requests"][identity].update(
                state="completed" if actual is not None else "completed_usage_unknown",
                charged_nanodollars=charged, usage=usage)
            _atomic_json(STATE_DIR / "cloud-budget.json", ledger)
        return {**cached, **budget_status()}
    except Exception as exc:
        # Preserve the reservation even if we do not know whether the call ran.
        with _locked_ledger() as ledger:
            entry = ledger["requests"][identity]
            entry["state"] = "failed"
            entry['error'] = exc.diagnostics() if isinstance(exc, CloudError) else {'category': 'invalid_response', 'retryable': False}
            entry["charged_nanodollars"] = max(entry["charged_nanodollars"], entry["reserved_nanodollars"])
            _atomic_json(STATE_DIR / "cloud-budget.json", ledger)
        raise
