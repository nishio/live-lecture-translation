"""Persistently reserve the human-approved lecture text transmission scope.

This module never records audio, reads API keys, sends requests, or manages money.
The authorization file is a machine-readable copy of the user's approval, not
proof of approval on its own. Call ``reserve`` immediately before cloud inference.
Reservations are permanent even if inference or the separate dollar budget fails.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid
from zoneinfo import ZoneInfo


DEFAULT_LEDGER_DIR = Path(__file__).resolve().parents[1] / 'data/event-audio/mac-live-cloud-scope'
SUPPORTED_MODELS = frozenset({'gpt-6-luna', 'gpt-6.1-sol'})
JST = ZoneInfo('Asia/Tokyo')
NANOSECONDS = 1_000_000_000
MAX_AUTHORIZATION_BYTES = 1024 * 1024
MAX_LEDGER_BYTES = 8 * 1024 * 1024
MAX_MICROPHONE_SECONDS = 6 * 60 * 60


class CloudScopeError(RuntimeError):
    """No cloud request is permitted after this error; recording is independent."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CloudScopeError('送信範囲のJSONに重複キーがあります。')
        result[key] = value
    return result


def _invalid_constant(_value):
    raise CloudScopeError('送信範囲のJSONに有限でない数値があります。')


def _read_json(path, maximum):
    try:
        with path.open('rb') as handle:
            raw = handle.read(maximum + 1)
        if len(raw) > maximum:
            raise CloudScopeError('送信範囲の記録がサイズ上限を超えています。')
        data = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant)
    except (OSError, ValueError, UnicodeError) as exc:
        raise CloudScopeError('送信範囲の記録を読み取れません。記録を消さずに確認してください。') from exc
    if not isinstance(data, dict):
        raise CloudScopeError('送信範囲の記録はJSONオブジェクトである必要があります。')
    return data, hashlib.sha256(raw).hexdigest()


def _iso_date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise CloudScopeError(f'{label}の日付が不正です。')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise CloudScopeError(f'{label}の日付が不正です。') from exc


def _number(value, label, *, positive=False):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0 and (not positive or value > 0)
    except OverflowError:
        valid = False
    if not valid:
        raise CloudScopeError(f'{label}は有限の{"正" if positive else "非負"}の数値が必要です。')
    return value


def _nanos(seconds):
    # Round conservatively upward, without accumulating floating-point sums.
    return int((Decimal(str(seconds)) * NANOSECONDS).to_integral_value(rounding=ROUND_CEILING))


def _absolute_path(value, label):
    if not isinstance(value, str) or not value or '\0' in value or not Path(value).is_absolute():
        raise CloudScopeError(f'{label}には絶対パスが必要です。')
    try:
        return Path(value).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise CloudScopeError(f'{label}のパスを確認できません。') from exc


def _file_sha(path):
    try:
        if not path.is_file():
            raise CloudScopeError('許可対象の再生音声ファイルがありません。')
        with path.open('rb') as handle:
            before = os.fstat(handle.fileno())
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            after = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise CloudScopeError('照合中に再生音声が変更されました。')
        return digest
    except OSError as exc:
        raise CloudScopeError('再生音声のSHAを確認できません。') from exc


def _fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path, value):
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class CloudScope:
    """Authorize replay hashes or a shared microphone allowance up to six hours.

    ``session`` contains ``id``, ``source_kind`` and epoch-seconds ``started_at``;
    replay sessions also require an absolute ``replay_path``. No paths, dates or
    source identities are inferred from a display title or a session ID.
    ``now`` may be injected for CPU-only tests. Construction only reads JSON.
    """

    def __init__(self, authorization_path, ledger_dir=None, *, now=time.time):
        self.authorization_path = Path(authorization_path).resolve()
        self.ledger_dir = Path(ledger_dir or DEFAULT_LEDGER_DIR).resolve()
        self.ledger_path = self.ledger_dir / 'ledger.json'
        if not callable(now):
            raise TypeError('now must be callable')
        self.now = now
        self._read_authorization()

    def _read_authorization(self):
        raw, fingerprint = _read_json(self.authorization_path, MAX_AUTHORIZATION_BYTES)
        if raw.get('human_approved') is not True:
            raise CloudScopeError('本文送信の承認が確認できません。クラウド解析は開始しません。')
        dates = raw.get('allowed_dates')
        models = raw.get('allowed_models')
        sources = raw.get('replay_sources')
        if not isinstance(dates, list) or not dates:
            raise CloudScopeError('本文送信を許可された日付がありません。')
        dates = [_iso_date(item, '送信許可') for item in dates]
        if len(set(dates)) != len(dates):
            raise CloudScopeError('送信許可の日付が重複しています。')
        if (not isinstance(models, list) or not models or any(not isinstance(model, str) or model not in SUPPORTED_MODELS for model in models)
                or len(set(models)) != len(models)):
            raise CloudScopeError('許可モデルはLuna/Solの明示されたモデルIDが必要です。')
        microphone_date = _iso_date(raw.get('microphone_date'), 'マイク録音の許可')
        if microphone_date not in dates:
            raise CloudScopeError('マイク録音の許可日が送信許可日と一致しません。')
        maximum = _number(raw.get('microphone_max_seconds'), 'マイク録音の許可秒数', positive=True)
        if maximum > MAX_MICROPHONE_SECONDS:
            raise CloudScopeError('マイク録音の許可は累計6時間を超えられません。')
        if not isinstance(sources, list):
            raise CloudScopeError('再生音声の許可リストが不正です。')
        replay_sources = {}
        for entry in sources:
            if not isinstance(entry, dict):
                raise CloudScopeError('再生音声の許可項目が不正です。')
            path = str(_absolute_path(entry.get('path'), '許可する再生音声'))
            sha = entry.get('sha256')
            if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
                raise CloudScopeError('許可する再生音声のSHA256が不正です。')
            if path in replay_sources:
                raise CloudScopeError('再生音声の許可パスが重複しています。')
            replay_sources[path] = sha
        return {'allowed_dates': dates, 'allowed_models': models, 'replay_sources': replay_sources,
                'microphone_date': microphone_date, 'microphone_max_nanoseconds': _nanos(maximum),
                'authorization_sha256': fingerprint}

    def _identity(self, session, through, authorized, now):
        if not isinstance(session, dict):
            raise CloudScopeError('クラウド解析のセッション情報がありません。')
        identity = session.get('id')
        if (not isinstance(identity, str) or not identity.strip() or len(identity) > 200
                or any(ord(character) < 32 for character in identity)):
            raise CloudScopeError('クラウド解析のセッションIDが不正です。')
        started = _number(session.get('started_at'), '録音開始時刻', positive=True)
        if started > now:
            raise CloudScopeError('録音開始時刻が現在より未来です。')
        try:
            started_date = datetime.fromtimestamp(started, JST).date().isoformat()
        except (ValueError, OverflowError, OSError) as exc:
            raise CloudScopeError('録音開始時刻の日付を確認できません。') from exc
        kind = session.get('source_kind')
        record = {'source_kind': kind, 'started_at': started, 'recording_date': started_date}
        if kind == 'microphone':
            if started_date != authorized['microphone_date']:
                raise CloudScopeError('このマイク録音日は本文送信の許可範囲外です。録音自体は継続できます。')
            # A recording crossing midnight is not entirely on the approved day.
            end_of_day = datetime.combine(date.fromisoformat(started_date), datetime.min.time(), JST).timestamp() + 86400
            if Decimal(str(started)) + Decimal(through) / NANOSECONDS > Decimal(str(end_of_day)):
                raise CloudScopeError('許可日の終わりを越えるマイク音声は送信できません。')
        elif kind == 'replay':
            path = _absolute_path(session.get('replay_path'), '再生音声')
            expected = authorized['replay_sources'].get(str(path))
            if expected is None:
                raise CloudScopeError('この再生音声は本文送信の許可リストにありません。')
            if _file_sha(path) != expected:
                raise CloudScopeError('再生音声のSHAが許可された原音と一致しません。')
            record.update(replay_path=str(path), replay_sha256=expected)
        else:
            raise CloudScopeError('不明な音声入力元をクラウドへ送信できません。')
        return identity, record

    def _load_ledger(self, authorized):
        if not self.ledger_path.exists():
            return {'schema_version': 1, 'microphone_date': authorized['microphone_date'], 'sessions': {}}
        ledger, _ = _read_json(self.ledger_path, MAX_LEDGER_BYTES)
        if type(ledger.get('schema_version')) is not int or ledger['schema_version'] != 1 or ledger.get('microphone_date') != authorized['microphone_date'] or not isinstance(ledger.get('sessions'), dict):
            raise CloudScopeError('共有の送信範囲台帳の形式または許可日が一致しません。台帳は初期化しません。')
        for identity, record in ledger['sessions'].items():
            if not isinstance(identity, str) or not isinstance(record, dict) or record.get('source_kind') not in {'microphone', 'replay'}:
                raise CloudScopeError('送信範囲台帳のセッション記録が不正です。')
            if type(record.get('max_through_nanoseconds')) is not int or record['max_through_nanoseconds'] < 0:
                raise CloudScopeError('送信範囲台帳の予約秒数が不正です。')
            started = _number(record.get('started_at'), '台帳の開始時刻', positive=True)
            _iso_date(record.get('recording_date'), '台帳の録音')
            try:
                if datetime.fromtimestamp(started, JST).date().isoformat() != record['recording_date']:
                    raise CloudScopeError('台帳の開始時刻と録音日が一致しません。')
            except (ValueError, OverflowError, OSError) as exc:
                raise CloudScopeError('台帳の開始時刻を確認できません。') from exc
            if record['source_kind'] == 'microphone' and record['recording_date'] != authorized['microphone_date']:
                raise CloudScopeError('台帳に許可日以外のマイク録音があります。')
            if record['source_kind'] == 'replay':
                _absolute_path(record.get('replay_path'), '台帳の再生音声')
                if not isinstance(record.get('replay_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', record['replay_sha256']):
                    raise CloudScopeError('台帳の再生音声の同一性を確認できません。')
        return ledger

    def status(self):
        """Read the microphone allowance without reserving or creating files.

        ``authorized_today`` concerns microphone recordings made today; the
        broader ``send_authorized_today`` also covers approved replay sources.
        Neither is a send authorization: ``reserve`` must still recheck the
        actual session immediately before inference. Invalid or unreadable data
        raises CloudScopeError rather than reporting an invented zero balance.
        """
        authorized = self._read_authorization()
        ledger = self._load_ledger(authorized)
        now = _number(self.now(), '送信確認時刻', positive=True)
        try:
            today = datetime.fromtimestamp(now, JST).date().isoformat()
        except (ValueError, OverflowError, OSError) as exc:
            raise CloudScopeError('送信日の確認に失敗しました。') from exc
        used = sum(record['max_through_nanoseconds'] for record in ledger['sessions'].values()
                   if record['source_kind'] == 'microphone')
        maximum = authorized['microphone_max_nanoseconds']
        send_authorized = today in authorized['allowed_dates']
        return {'checked_at': now, 'allowed_dates': authorized['allowed_dates'],
                'microphone_date': authorized['microphone_date'],
                'authorized_today': send_authorized and today == authorized['microphone_date'],
                'send_authorized_today': send_authorized,
                'max_seconds': maximum / NANOSECONDS, 'used_seconds': used / NANOSECONDS,
                'remaining_seconds': max(0, maximum - used) / NANOSECONDS,
                'quota_exhausted': used >= maximum, 'allowed_models': authorized['allowed_models'],
                'authorization_sha256': authorized['authorization_sha256'],
                'ledger_path': str(self.ledger_path)}

    def reserve(self, session, model, through_seconds):
        """Persist a non-refundable high-water reservation before sending text.

        Same-session retries/model comparisons charge only growth in audio extent.
        Different microphone sessions share one cumulative maximum. Integer
        nanoseconds keep summation conservative and deterministic across processes.
        """
        through = _nanos(_number(through_seconds, '解析対象の音声位置'))
        descriptor = None
        try:
            self.ledger_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.ledger_dir / 'scope.lock', os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            authorized = self._read_authorization()  # Observe withdrawals/changes on every send.
            now = _number(self.now(), '送信確認時刻', positive=True)
            try:
                today = datetime.fromtimestamp(now, JST).date().isoformat()
            except (ValueError, OverflowError, OSError) as exc:
                raise CloudScopeError('送信日の確認に失敗しました。') from exc
            if today not in authorized['allowed_dates']:
                raise CloudScopeError('今日は本文送信を許可された日付ではありません。')
            if model not in authorized['allowed_models']:
                raise CloudScopeError('このモデルは本文送信の許可範囲外です。')
            identity, source = self._identity(session, through, authorized, now)
            ledger = self._load_ledger(authorized)
            old = ledger['sessions'].get(identity)
            if old and any(old.get(key) != value for key, value in source.items()):
                raise CloudScopeError('同じセッションIDの入力元または開始時刻が変わっています。')
            previous = old['max_through_nanoseconds'] if old else 0
            maximum = max(previous, through)
            increase = maximum - previous
            used = sum(record['max_through_nanoseconds'] for record in ledger['sessions'].values() if record['source_kind'] == 'microphone')
            delta = increase if source['source_kind'] == 'microphone' else 0
            total = used + delta
            if total > authorized['microphone_max_nanoseconds']:
                hours = authorized['microphone_max_nanoseconds'] / (3600 * NANOSECONDS)
                raise CloudScopeError(f'マイク録音の本文送信が共有の累計{hours:g}時間の許可範囲を超えます。録音は停止しません。')
            ledger['sessions'][identity] = {**source, 'max_through_nanoseconds': maximum,
                'last_model': model, 'last_reserved_at': now, 'authorization_sha256': authorized['authorization_sha256']}
            ledger['updated_at'] = now
            _atomic_json(self.ledger_path, ledger)
            return {'session_id': identity, 'source_kind': source['source_kind'], 'model': model,
                'authorized_date': today, 'through_seconds': through / NANOSECONDS,
                'session_reserved_seconds': maximum / NANOSECONDS,
                'delta_seconds': delta / NANOSECONDS, 'microphone_reserved_seconds': total / NANOSECONDS,
                'microphone_remaining_seconds': (authorized['microphone_max_nanoseconds'] - total) / NANOSECONDS,
                'authorization_sha256': authorized['authorization_sha256'], 'ledger_path': str(self.ledger_path)}
        except OSError as exc:
            raise CloudScopeError('送信範囲の予約を保存できません。クラウド送信は開始しません。') from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
