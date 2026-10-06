import hashlib
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import event_insights_cloud as cloud
import event_insights as insights
from processing_control import ProcessingStopped, processing_scope


MESSAGES = [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": "synthetic"}]
SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}},
          "required": ["summary"], "additionalProperties": False}


def response(usage=True, model=None):
    result = {"model": model or cloud.DEFAULT_MODEL, "service_tier": "default", "status": "completed",
              "output": [{"type": "message", "status": "completed", "content": [
                  {"type": "output_text", "text": '{"summary":"合成試験です。"}'}]}]}
    if usage:
        result["usage"] = {"input_tokens": 1000, "output_tokens": 100,
                           "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300}}
    return result


class CloudInsightsTest(unittest.TestCase):
    def test_stopped_before_or_during_ledger_wait_never_reserves_or_sends(self):
        stop = threading.Event()
        stop.set()
        with processing_scope(stop), patch.object(cloud, '_post') as post:
            with self.assertRaises(ProcessingStopped):
                self.generate()
        post.assert_not_called()
        self.assertEqual({}, cloud._load_ledger()['requests'])

        stop.clear()
        real_lock = cloud._locked_ledger

        @contextmanager
        def waited_lock():
            with real_lock() as ledger:
                stop.set()
                yield ledger

        with processing_scope(stop), patch.object(cloud, '_locked_ledger', waited_lock), \
                patch.object(cloud, '_post') as post:
            with self.assertRaises(ProcessingStopped):
                self.generate()
        post.assert_not_called()
        self.assertEqual({}, cloud._load_ledger()['requests'])

    def test_stop_during_reservation_records_unsent_without_erasing_history(self):
        stop = threading.Event()
        real_write = cloud._atomic_json

        def stop_after_reservation(path, data):
            real_write(path, data)
            if path.name == 'cloud-budget.json' and any(
                    entry['state'] == 'reserved' for entry in data['requests'].values()):
                stop.set()

        with processing_scope(stop), patch.object(cloud, '_atomic_json', stop_after_reservation), \
                patch.object(cloud, '_post') as post:
            with self.assertRaises(ProcessingStopped):
                self.generate()
        post.assert_not_called()
        cancelled = cloud._load_ledger()['requests']
        entry = next(iter(cancelled.values()))
        self.assertEqual('cancelled_unsent', entry['state'])
        self.assertEqual(0, entry['charged_nanodollars'])
        self.assertGreater(entry['reserved_nanodollars'], 0)
        self.assertEqual({'category': 'cancelled', 'retryable': False, 'request_sent': False}, entry['error'])
        self.assertEqual(0, cloud.budget_status()['spent_usd'])
        self.assertEqual(0, cloud.budget_status()['reserved_usd'])

        # A later session may submit this input; the cancelled attempt remains
        # separately identifiable and never turns into a charged request.
        with processing_scope(threading.Event()), patch.object(cloud, '_post', return_value=response()):
            self.generate()
        after = cloud._load_ledger()['requests']
        self.assertEqual(2, len(after))
        for identity, item in cancelled.items():
            self.assertEqual(item, after[identity])
        self.assertEqual(['cancelled_unsent', 'completed'], [item['state'] for item in after.values()])

    def test_unsent_retry_preserves_earlier_unknown_charge(self):
        with patch.object(cloud, '_post', side_effect=cloud.CloudError('synthetic transport failure')):
            with self.assertRaises(cloud.CloudError):
                self.generate()
        before = cloud._load_ledger()['requests']
        stop = threading.Event()
        real_write = cloud._atomic_json

        def stop_after_reservation(path, data):
            real_write(path, data)
            if any(item['state'] == 'reserved' for item in data.get('requests', {}).values()):
                stop.set()

        with processing_scope(stop), patch.object(cloud, '_atomic_json', stop_after_reservation), \
                patch.object(cloud, '_post') as post:
            with self.assertRaises(ProcessingStopped):
                self.generate(retry_failed=True)
        post.assert_not_called()
        after = cloud._load_ledger()['requests']
        for identity, item in before.items():
            self.assertEqual(item, after[identity])
        self.assertEqual(['failed', 'cancelled_unsent'], [item['state'] for item in after.values()])
        with patch.object(cloud, '_post') as post:
            with self.assertRaises(cloud.CloudError):
                self.generate()
        post.assert_not_called()

    def test_transport_checkpoint_prevents_send_after_request_preparation(self):
        stop = threading.Event()
        with processing_scope(stop), patch.object(cloud.request, 'build_opener') as factory:
            opener = factory.return_value
            factory.side_effect = lambda *args: (stop.set(), opener)[1]
            with self.assertRaises(ProcessingStopped):
                self.generate()
        opener.open.assert_not_called()
        entry = next(iter(cloud._load_ledger()['requests'].values()))
        self.assertEqual('cancelled_unsent', entry['state'])
        self.assertEqual(0, entry['charged_nanodollars'])

    def test_stop_after_admission_still_settles_response_or_unknown_failure(self):
        stop = threading.Event()

        def dispatched(*args):
            stop.set()
            return response()

        with processing_scope(stop), patch.object(cloud, '_post', side_effect=dispatched):
            result = self.generate()
        self.assertTrue(result['usage_confirmed'])
        entry = next(iter(cloud._load_ledger()['requests'].values()))
        self.assertEqual('completed', entry['state'])
        self.assertGreater(entry['charged_nanodollars'], 0)

        stop.clear()
        with processing_scope(stop), patch.object(cloud, '_post', side_effect=dispatched):
            with self.assertRaises(ProcessingStopped):
                cloud.generate(MESSAGES + [{'role': 'user', 'content': 'other synthetic'}], SCHEMA,
                               validate=json.loads,
                               observe_response=lambda response: (_ for _ in ()).throw(ProcessingStopped()))
        last = list(cloud._load_ledger()['requests'].values())[-1]
        self.assertEqual('failed', last['state'])
        self.assertGreater(last['charged_nanodollars'], 0)

    def test_stop_during_sent_transport_failure_keeps_unknown_charge(self):
        stop = threading.Event()

        def dispatched(*args, **kwargs):
            stop.set()
            raise URLError('synthetic transport interruption')

        with processing_scope(stop), patch.object(cloud.request, 'build_opener') as factory:
            factory.return_value.open.side_effect = dispatched
            with self.assertRaises(cloud.CloudError) as caught:
                self.generate()
        factory.return_value.open.assert_called_once()
        self.assertEqual('transport', caught.exception.category)
        entry = next(iter(cloud._load_ledger()['requests'].values()))
        self.assertEqual('failed', entry['state'])
        self.assertGreater(entry['charged_nanodollars'], 0)
        self.assertEqual(entry['reserved_nanodollars'], entry['charged_nanodollars'])

    def test_http_failures_have_sanitized_retry_diagnostics(self):
        cases = [(429, 'rate_limit_exceeded', 'rate_limit', True),
                 (429, 'insufficient_quota', 'quota', False),
                 (429, 'unknown_private_code', 'unknown', False),
                 (401, 'invalid_api_key', 'authentication', False),
                 (400, 'invalid_request_error', 'invalid_request', False),
                 (503, 'server_error', 'server', True), (408, None, 'server', True)]
        for status, code, category, retryable in cases:
            with self.subTest(status=status, code=code):
                body = json.dumps({'error': {'code': code, 'message': 'PRIVATE TEXT secret-token',
                                             'type': 'PRIVATE TYPE'}}).encode()
                upstream = HTTPError(cloud.API_URL, status, 'PRIVATE TEXT',
                                     {'Retry-After': '12', 'x-request-id': 'PRIVATE ID'}, io.BytesIO(body))
                with patch.object(cloud.request, 'build_opener') as opener:
                    opener.return_value.open.side_effect = upstream
                    with self.assertRaises(cloud.CloudError) as caught:
                        cloud._post({}, 'synthetic-key', 1)
                diagnostic = caught.exception.diagnostics()
                self.assertEqual((category, retryable, status, 12),
                    (diagnostic['category'], diagnostic['retryable'], diagnostic['http_status'], diagnostic['retry_after_seconds']))
                self.assertNotIn('PRIVATE', str(caught.exception) + json.dumps(diagnostic))
                self.assertNotIn('unknown_private_code', json.dumps(diagnostic))

    def test_quota_wins_over_rate_limit_type_and_retry_after_is_bounded_input(self):
        body = json.dumps({'error': {'code': 'insufficient_quota', 'type': 'rate_limit_exceeded'}}).encode()
        exc = cloud._http_error(HTTPError(cloud.API_URL, 429, '', {}, io.BytesIO(body)))
        self.assertEqual('quota', exc.category)
        self.assertFalse(exc.retryable)
        with patch.object(cloud.time, 'time', return_value=0):
            self.assertEqual(120, cloud._retry_after({'Retry-After': 'Thu, 01 Jan 1970 00:02:00 GMT'}))
        for value in ('NaN', 'inf', '-1', 'PRIVATE', '1' * 200):
            self.assertIsNone(cloud._retry_after({'Retry-After': value}))
        for raw in (b'not JSON PRIVATE', b'x' * 20000):
            exc = cloud._http_error(HTTPError(cloud.API_URL, 503, '', {}, io.BytesIO(raw)))
            self.assertTrue(exc.retryable)
            self.assertNotIn('PRIVATE', str(exc))

    def test_transport_error_never_leaks_details_or_retries_in_adapter(self):
        with patch.object(cloud.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = URLError('PRIVATE secret-url')
            with self.assertRaises(cloud.CloudError) as caught:
                cloud._post({}, 'synthetic-key', 1)
            opener.return_value.open.assert_called_once()
        self.assertTrue(caught.exception.retryable)
        self.assertEqual('transport', caught.exception.category)
        self.assertNotIn('PRIVATE', str(caught.exception))

    def test_failed_ledger_keeps_sanitized_diagnostics_and_reserves_retry(self):
        failure = cloud.CloudError('通信失敗', category='transport', retryable=True)
        with patch.object(cloud, '_post', side_effect=failure):
            with self.assertRaises(cloud.CloudError):
                self.generate()
        first = next(iter(cloud._load_ledger()['requests'].values()))
        self.assertEqual(failure.diagnostics(), first['error'])
        held = first['charged_nanodollars']
        with patch.object(cloud, '_post', return_value=response()):
            self.generate(retry_failed=True)
        entries = list(cloud._load_ledger()['requests'].values())
        self.assertEqual(['failed', 'completed'], [item['state'] for item in entries])
        self.assertEqual(held, entries[0]['charged_nanodollars'])
        self.assertGreater(entries[1]['charged_nanodollars'], 0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in [patch.object(cloud, "STATE_DIR", Path(self.temp.name) / "state"),
                        patch.object(cloud, "DOTENV_PATH", Path(self.temp.name) / ".env"),
                        patch.object(cloud, "BUDGET_AUTHORIZATION_PATH", None),
                        patch.dict(cloud.os.environ, {"OPENAI_API_KEY": "test-only-not-a-real-key"}, clear=True),
                        patch.object(cloud, "_day", return_value="2030-06-01")]:
            patcher.start()
            self.addCleanup(patcher.stop)
        cloud.configure_budget_authorization(self.budget_authorization(
            allowed_dates=["2030-06-01", "2030-06-02"],
            daily_budget_usd_by_date={"2030-06-01": 1, "2030-06-02": 1}))

    def generate(self, **kwargs):
        return cloud.generate(MESSAGES, SCHEMA, validate=json.loads, **kwargs)

    def budget_authorization(self, **changes):
        value = {"human_approved": True, "destination": cloud.API_URL, "raw_audio_allowed": False,
                 "allowed_dates": ["2030-06-03", "2030-06-04"],
                 "daily_budget_usd_by_date": {"2030-06-03": 1, "2030-06-04": 10}}
        value.update(changes)
        path = Path(self.temp.name) / "budget-authorization.json"
        path.write_text(json.dumps(value))
        return path

    def test_only_explicit_dates_and_budgets_authorize_spending(self):
        path = self.budget_authorization()
        with patch.object(cloud, "_api_key", side_effect=AssertionError("key read")), \
                patch.object(cloud, "_post", side_effect=AssertionError("API")):
            cloud.configure_budget_authorization(path)
            for day, expected in [("2030-06-03", 1), ("2030-06-04", 10), ("2030-06-05", 0)]:
                with patch.object(cloud, "_day", return_value=day):
                    self.assertEqual(expected, cloud.budget_status()["budget_usd"])
            with patch.object(cloud, "_day", return_value="2030-06-04"), \
                    patch.object(cloud, "BUDGET_AUTHORIZATION_PATH", None):
                self.assertEqual(0, cloud.budget_status()["budget_usd"])
        self.assertFalse(cloud.STATE_DIR.exists())

    def test_date_budget_rejects_unauthorized_scope_dates_and_amounts(self):
        cases = [{"human_approved": False}, {"destination": "https://example.invalid"},
                 {"raw_audio_allowed": True}, {"daily_budget_usd_by_date": {}},
                 {"daily_budget_usd_by_date": {"2030-06-05": 10}},
                 {"daily_budget_usd_by_date": {"2030-06-04": True}},
                 {"daily_budget_usd_by_date": {"2030-06-04": "10"}},
                 {"daily_budget_usd_by_date": {"2030-06-04": -1}},
                 {"allowed_dates": ["2030-06-04", "2030-06-04"]}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(cloud.CloudError):
                cloud.configure_budget_authorization(self.budget_authorization(**changes))
        path = self.budget_authorization()
        path.write_text('{"human_approved":true,"human_approved":false}')
        with self.assertRaises(cloud.CloudError): cloud.configure_budget_authorization(path)
        path.write_text('{"bad":NaN}')
        with self.assertRaises(cloud.CloudError): cloud.configure_budget_authorization(path)

    def test_arbitrary_explicit_date_can_authorize_more_than_ten_dollars(self):
        path = self.budget_authorization(allowed_dates=["2031-07-12"],
            daily_budget_usd_by_date={"2031-07-12": 12.5})
        cloud.configure_budget_authorization(path)
        with patch.object(cloud, "_day", return_value="2031-07-12"), \
                patch.object(cloud, "_api_key", side_effect=AssertionError("key read")), \
                patch.object(cloud, "_post", side_effect=AssertionError("API")):
            self.assertEqual(12.5, cloud.budget_status()["budget_usd"])
        self.assertFalse(cloud.STATE_DIR.exists())

    def test_missing_or_wrong_day_authorization_never_sends_request(self):
        for authorization in (None, self.budget_authorization()):
            with self.subTest(authorization=authorization), \
                    patch.object(cloud, "BUDGET_AUTHORIZATION_PATH", authorization), \
                    patch.object(cloud, "_post") as api:
                self.assertEqual(0, cloud.budget_status()["budget_usd"])
                with self.assertRaises(cloud.BudgetExceededError):
                    self.generate()
                api.assert_not_called()
                self.assertEqual({}, cloud._load_ledger()["requests"])

    def test_invalid_configuration_cannot_fall_back_to_previous_approval(self):
        path = Path(self.temp.name) / "revoked.json"
        for content in ('{"human_approved":false}', '{broken'):
            path.write_text(content)
            with self.subTest(content=content), patch.object(cloud, "_post") as api:
                with self.assertRaises(cloud.CloudError):
                    cloud.configure_budget_authorization(path)
                with self.assertRaises(cloud.CloudError):
                    self.generate()
                api.assert_not_called()
                self.assertEqual({}, cloud._load_ledger()["requests"])

    def test_budget_and_ledger_share_one_observed_day_across_midnight(self):
        cloud.configure_budget_authorization(self.budget_authorization())
        with patch.object(cloud, "_day", side_effect=["2030-06-03", "2030-06-04"]) as clock:
            status = cloud.budget_status()
            self.assertEqual("2030-06-03", status["budget_date"])
            self.assertEqual(1, status["budget_usd"])
            self.assertEqual(1, clock.call_count)
        cloud._atomic_json(cloud.STATE_DIR / "cloud-budget.json", {"schema_version": 1,
                           "requests": {"c" * 64: {"day": "2030-06-03", "state": "failed",
                           "charged_nanodollars": 999_999_999}}})
        with patch.object(cloud, "_day", side_effect=["2030-06-03", "2030-06-04"]), \
                patch.object(cloud, "_post") as api, self.assertRaises(cloud.BudgetExceededError):
            self.generate()
        api.assert_not_called()

    def test_budget_revocation_or_corruption_blocks_next_request_without_ledger_reset(self):
        path = self.budget_authorization()
        cloud.configure_budget_authorization(path)
        with patch.object(cloud, "_day", return_value="2030-06-04"), \
                patch.object(cloud, "_post", return_value=response()) as api:
            self.generate()
            before = (cloud.STATE_DIR / "cloud-budget.json").read_bytes()
            self.budget_authorization(human_approved=False)
            with self.assertRaises(cloud.CloudError):
                cloud.generate(MESSAGES + [{"role": "user", "content": "next"}], SCHEMA, validate=json.loads)
            self.assertEqual(1, api.call_count)
            self.assertEqual(before, (cloud.STATE_DIR / "cloud-budget.json").read_bytes())
            path.write_text('{broken')
            with self.assertRaises(cloud.CloudError): cloud.budget_status()
            self.assertEqual(before, (cloud.STATE_DIR / "cloud-budget.json").read_bytes())

    def test_ten_dollar_limit_keeps_shared_history_unknown_reservations_and_blocks_excess(self):
        cloud.configure_budget_authorization(self.budget_authorization())
        old = {"day": "2030-06-03", "state": "completed", "model": cloud.DEFAULT_MODEL,
               "charged_nanodollars": 143466175}
        held = {"day": "2030-06-04", "state": "failed", "model": cloud.DEFAULT_MODEL,
                "charged_nanodollars": 9_000_000_000, "reserved_nanodollars": 9_000_000_000}
        cloud._atomic_json(cloud.STATE_DIR / "cloud-budget.json", {"schema_version": 1,
                           "requests": {"a" * 64: old, "b" * 64: held}})
        with patch.object(cloud, "_day", return_value="2030-06-04"), \
                patch.object(cloud, "_post", return_value=response()) as api:
            result = self.generate()
            self.assertEqual(10, result["budget_usd"])
            self.assertEqual(9, result["reserved_usd"])
            ledger = cloud._load_ledger()
            self.assertEqual(old, ledger["requests"]["a" * 64])
            self.assertEqual(held, ledger["requests"]["b" * 64])
            ledger["requests"]["b" * 64]["charged_nanodollars"] = 9_999_999_999
            cloud._atomic_json(cloud.STATE_DIR / "cloud-budget.json", ledger)
            before = (cloud.STATE_DIR / "cloud-budget.json").read_bytes()
            with self.assertRaises(cloud.BudgetExceededError):
                cloud.generate(MESSAGES + [{"role": "user", "content": "more"}], SCHEMA, validate=json.loads)
            self.assertEqual(1, api.call_count)
            self.assertEqual(before, (cloud.STATE_DIR / "cloud-budget.json").read_bytes())

    def test_atomic_reservation_precedes_request_and_success_settles_usage(self):
        def call(payload, key, timeout):
            state = cloud.budget_status()
            self.assertGreater(state["reserved_usd"], 0)
            self.assertFalse(payload["store"])
            self.assertEqual("none", payload["reasoning"]["effort"])
            self.assertEqual("default", payload["service_tier"])
            self.assertEqual("disabled", payload["truncation"])
            self.assertNotIn("tools", payload)
            self.assertLessEqual(payload["max_output_tokens"], 6144)
            return response()
        with patch.object(cloud, "_post", side_effect=call):
            result = self.generate()
        expected = (500 * 100 + 200 * 10 + 300 * 125 + 100 * 500) / 1e9
        self.assertEqual(expected, result["cost_usd"])
        self.assertEqual(expected, cloud.budget_status()["spent_usd"])
        self.assertEqual(0, cloud.budget_status()["reserved_usd"])
        self.assertTrue(result["usage_confirmed"])
        ledger_text = (cloud.STATE_DIR / "cloud-budget.json").read_text()
        self.assertNotIn("test-only-not-a-real-key", ledger_text)
        self.assertNotIn("synthetic", ledger_text)
        self.assertNotIn("合成試験", ledger_text)

    def test_underestimate_blocks_budget_without_reporting_it_as_measured_cost(self):
        measured_response = response()
        measured_response["usage"] = {"input_tokens": 200000, "output_tokens": 100,
                                      "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}
        with patch.object(cloud, "_post", return_value=measured_response):
            result = self.generate()
        measured = (200000 * 100 + 100 * 500) / 1e9
        self.assertAlmostEqual(measured, result["cost_usd"])
        self.assertTrue(result["usage_confirmed"])
        self.assertAlmostEqual(1 - measured, result["budget_hold_usd"])
        status = cloud.budget_status()
        self.assertEqual(1, status["spent_usd"])
        self.assertAlmostEqual(1 - measured, status["reserved_usd"])
        entry, = cloud._load_ledger()["requests"].values()
        self.assertEqual(("completed", 1_000_000_000, 20_050_000),
                         (entry["state"], entry["charged_nanodollars"], entry["measured_nanodollars"]))
        self.assertEqual(20_050_000, cloud.confirmed_nanodollars(entry))
        other = [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": "other"}]
        with patch.object(cloud, "_post", side_effect=AssertionError("API")):
            with self.assertRaises(cloud.BudgetExceededError):
                cloud.generate(other, SCHEMA, validate=json.loads)

    def test_pure_payload_keeps_legacy_luna_bytes_and_hash(self):
        original = json.dumps([MESSAGES, SCHEMA], sort_keys=True)
        with patch.object(cloud, "_api_key", side_effect=AssertionError("key read")), \
                patch.object(cloud, "_locked_ledger", side_effect=AssertionError("ledger access")), \
                patch.object(cloud, "_post", side_effect=AssertionError("network")):
            payload = cloud.build_payload(MESSAGES, SCHEMA)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.assertEqual(489, len(encoded))
        self.assertEqual("f014129aa0b932ff243535fc38e35a98d03cb445823598f72f7afc0c6b37762c",
                         hashlib.sha256(encoded).hexdigest())
        self.assertEqual(original, json.dumps([MESSAGES, SCHEMA], sort_keys=True))
        self.assertEqual("gpt-6-luna", cloud.DEFAULT_MODEL)
        self.assertFalse(cloud.STATE_DIR.exists())

    def test_sol_payload_changes_only_model_and_fixed_effort(self):
        luna = cloud.build_payload(MESSAGES, SCHEMA)
        sol = cloud.build_payload(MESSAGES, SCHEMA, "gpt-6.1-sol")
        self.assertEqual("low", sol["reasoning"]["effort"])
        self.assertEqual({"input": 2000, "cached": 100, "write": 2500, "output": 10000},
                         cloud.PRICES["gpt-6.1-sol"])
        sol["model"], sol["reasoning"] = luna["model"], luna["reasoning"]
        self.assertEqual(luna, sol)

    def test_sol_reservation_precedes_request_and_uses_sol_usage_rates(self):
        def call(payload, key, timeout):
            self.assertEqual("gpt-6.1-sol", payload["model"])
            self.assertEqual("low", payload["reasoning"]["effort"])
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            expected = (len(encoded) + 4096) * 2500 + 6144 * 10000
            entry, = cloud._load_ledger()["requests"].values()
            self.assertEqual(expected, entry["reserved_nanodollars"])
            self.assertEqual(expected, entry["charged_nanodollars"])
            self.assertEqual("reserved", entry["state"])
            return response(model="gpt-6.1-sol")
        with patch.object(cloud, "_post", side_effect=call):
            result = self.generate(model="gpt-6.1-sol")
        expected = (500 * 2000 + 200 * 100 + 300 * 2500 + 100 * 10000) / 1e9
        self.assertEqual(expected, result["cost_usd"])
        self.assertTrue(result["usage_confirmed"])
        self.assertEqual(0, result["reserved_usd"])

    def test_models_use_separate_cache_and_ledger_identities(self):
        with patch.object(cloud, "_post", side_effect=[response(), response(model="gpt-6.1-sol")]) as api:
            luna = self.generate()
            sol = self.generate(model="gpt-6.1-sol")
            again = self.generate(model="gpt-6.1-sol")
        self.assertEqual(2, api.call_count)
        self.assertEqual(2, len(cloud._load_ledger()["requests"]))
        self.assertTrue(again["cache_hit"])
        self.assertNotEqual(luna["cost_usd"], sol["cost_usd"])

    def test_sol_failure_holds_reservation_and_blocks_automatic_retry(self):
        with patch.object(cloud, "_post", side_effect=cloud.CloudError("timeout")) as api:
            with self.assertRaises(cloud.CloudError):
                self.generate(model="gpt-6.1-sol")
            held = cloud.budget_status()
            with self.assertRaisesRegex(cloud.CloudError, "再送しません"):
                self.generate(model="gpt-6.1-sol")
        api.assert_called_once()
        self.assertEqual(held, cloud.budget_status())
        self.assertGreater(held["reserved_usd"], .05)

    def test_sol_insufficient_daily_budget_stops_before_network(self):
        cloud.configure_budget_authorization(self.budget_authorization(
            allowed_dates=["2030-06-01"], daily_budget_usd_by_date={"2030-06-01": .05}))
        with patch.object(cloud, "_post") as api:
            with self.assertRaises(cloud.BudgetExceededError):
                self.generate(model="gpt-6.1-sol")
        api.assert_not_called()
        self.assertEqual({}, cloud._load_ledger()["requests"])

    def test_response_observer_sees_raw_response_before_validation_and_not_cache(self):
        raw, observed = response(model="gpt-6.1-sol"), []
        def validate(text):
            self.assertEqual([raw], observed)
            return json.loads(text)
        with patch.object(cloud, "_post", return_value=raw) as api:
            first = cloud.generate(MESSAGES, SCHEMA, model="gpt-6.1-sol", validate=validate,
                                   observe_response=observed.append)
            second = cloud.generate(MESSAGES, SCHEMA, model="gpt-6.1-sol", validate=validate,
                                    observe_response=observed.append)
        api.assert_called_once()
        self.assertEqual([raw], observed)
        self.assertIs(raw, observed[0])
        self.assertEqual(first["result"], second["result"])
        self.assertTrue(second["cache_hit"])

    def test_response_observer_keeps_raw_invalid_responses(self):
        for invalid in ("wrong_model", "invalid_json"):
            with self.subTest(invalid=invalid):
                raw, observed = response(), []
                if invalid == "wrong_model":
                    raw["model"] = "unexpected"
                else:
                    raw["output"][0]["content"][0]["text"] = "broken"
                with patch.object(cloud, "_post", return_value=raw):
                    with self.assertRaises((cloud.CloudError, ValueError)):
                        cloud.generate(MESSAGES + [{"role": "user", "content": invalid}], SCHEMA,
                                       validate=json.loads, observe_response=observed.append)
                self.assertEqual([raw], observed)
        self.assertTrue(all(item["state"] == "failed" and
                            item["charged_nanodollars"] == item["reserved_nanodollars"]
                            for item in cloud._load_ledger()["requests"].values()))

    def test_response_observer_failure_holds_reservation(self):
        def fail(raw):
            raise OSError("failed to save response")
        with patch.object(cloud, "_post", return_value=response()) as api:
            with self.assertRaisesRegex(OSError, "failed to save"):
                self.generate(observe_response=fail)
            with self.assertRaisesRegex(cloud.CloudError, "再送しません"):
                self.generate(observe_response=fail)
        api.assert_called_once()
        entry, = cloud._load_ledger()["requests"].values()
        self.assertEqual("failed", entry["state"])
        self.assertEqual(entry["reserved_nanodollars"], entry["charged_nanodollars"])

    def test_noncallable_response_observer_stops_before_network(self):
        with patch.object(cloud, "_post") as api:
            with self.assertRaises(cloud.CloudError):
                self.generate(observe_response="invalid")
        api.assert_not_called()
        self.assertFalse(cloud.STATE_DIR.exists())

    def test_identical_success_is_cached_without_another_request(self):
        with patch.object(cloud, "_post", return_value=response()) as api:
            first = self.generate()
            second = self.generate()
        api.assert_called_once()
        self.assertEqual(first["result"], second["result"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["spent_usd"], second["spent_usd"])

    def test_timeout_keeps_full_reservation_and_prevents_retry_after_restart(self):
        with patch.object(cloud, "_post", side_effect=cloud.CloudError("timeout")) as api:
            with self.assertRaises(cloud.CloudError):
                self.generate()
            before = cloud.budget_status()
            with self.assertRaisesRegex(cloud.CloudError, "再送しません") as blocked:
                self.generate()
        api.assert_called_once()
        self.assertEqual('previous_attempt_failed', blocked.exception.category)
        self.assertFalse(blocked.exception.retryable)
        self.assertGreater(before["reserved_usd"], 0)
        self.assertEqual(before, cloud.budget_status())

    def test_manual_retry_reserves_new_attempt_without_refunding_failed_attempt(self):
        with patch.object(cloud, "_post", side_effect=cloud.CloudError("timeout")):
            with self.assertRaises(cloud.CloudError):
                self.generate()
        held = cloud.budget_status()["spent_usd"]
        with patch.object(cloud, "_post", return_value=response()) as api:
            result = self.generate(retry_failed=True)
            cached = self.generate()
        api.assert_called_once()
        self.assertAlmostEqual(held + result["cost_usd"], result["spent_usd"])
        self.assertTrue(cached["cache_hit"])
        states = [item["state"] for item in cloud._load_ledger()["requests"].values()]
        self.assertEqual(["failed", "completed"], states)

    def test_manual_retry_of_crashed_attempt_keeps_its_unknown_charge(self):
        # A process crash leaves the pre-request reservation as 'reserved'.
        with patch.object(cloud, "_post", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.generate()
        held = cloud.budget_status()["spent_usd"]
        with patch.object(cloud, "_post", side_effect=AssertionError("resent")):
            with self.assertRaisesRegex(cloud.CloudError, "完了を確認できていません") as blocked:
                self.generate()
        self.assertEqual('previous_attempt_unresolved', blocked.exception.category)
        with patch.object(cloud, "_post", return_value=response()):
            result = self.generate(retry_failed=True)
        self.assertAlmostEqual(held + result["cost_usd"], result["spent_usd"])
        self.assertEqual(held, result["reserved_usd"])

    def test_invalid_grounding_keeps_reservation(self):
        with patch.object(cloud, "_post", return_value=response()):
            with self.assertRaises(ValueError):
                cloud.generate(MESSAGES, SCHEMA, validate=lambda text: (_ for _ in ()).throw(ValueError()))
        self.assertGreater(cloud.budget_status()["reserved_usd"], 0)

    def test_unknown_usage_keeps_reservation_but_caches_valid_result(self):
        for usage in [None, {"input_tokens": 1000, "output_tokens": 100,
                             "input_tokens_details": {"cached_tokens": 0}}]:
            with self.subTest(usage=usage):
                payload = response(False)
                if usage is not None:
                    payload["usage"] = usage
                with patch.object(cloud, "_post", return_value=payload):
                    result = cloud.generate(MESSAGES + [{"role": "user", "content": str(usage)}],
                                            SCHEMA, validate=json.loads)
                self.assertFalse(result["usage_confirmed"])
                self.assertGreater(result["reserved_usd"], 0)

    def test_explicit_budget_excess_stops_before_network_and_env_cannot_raise_it(self):
        cloud.configure_budget_authorization(self.budget_authorization(
            allowed_dates=["2030-06-01"], daily_budget_usd_by_date={"2030-06-01": .001}))
        with patch.object(cloud, "_post") as api:
            with self.assertRaises(cloud.BudgetExceededError):
                self.generate()
            api.assert_not_called()
        with patch.dict(cloud.os.environ, {"LLT_LLM_DAILY_BUDGET_USD": "100"}):
            self.assertEqual(.001, cloud.budget_status()["budget_usd"])

    def test_parallel_requests_cannot_overbook_budget(self):
        entered, release = threading.Event(), threading.Event()
        failures = []
        def slow(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return response()
        def worker():
            try:
                cloud.generate([{"role": "user", "content": "a" * 40000}], SCHEMA, validate=json.loads)
            except Exception as exc:
                failures.append(exc)
        cloud.configure_budget_authorization(self.budget_authorization(
            allowed_dates=["2030-06-01"], daily_budget_usd_by_date={"2030-06-01": .01}))
        with patch.object(cloud, "_post", side_effect=slow) as api:
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(cloud.BudgetExceededError):
                    cloud.generate([{"role": "user", "content": "b" * 40000}], SCHEMA, validate=json.loads)
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual([], failures)
            api.assert_called_once()

    def test_day_rollover_keeps_old_reservations_and_separates_daily_budget(self):
        with patch.object(cloud, "_post", return_value=response(False)):
            self.generate()
        with patch.object(cloud, "_day", return_value="2030-06-02"):
            self.assertEqual(0, cloud.budget_status()["spent_usd"])
        self.assertGreater(cloud.budget_status()["spent_usd"], 0)

    def test_corrupt_ledger_fails_closed(self):
        cloud.STATE_DIR.mkdir()
        (cloud.STATE_DIR / "cloud-budget.json").write_text("broken")
        with patch.object(cloud, "_post") as api, self.assertRaises(cloud.CloudError):
            self.generate()
        api.assert_not_called()

    def test_unknown_model_audio_or_oversized_input_is_rejected_before_network(self):
        with patch.object(cloud, "_post") as api:
            with self.assertRaises(cloud.CloudError):
                self.generate(model="gpt-6-astra")
            with self.assertRaises(cloud.CloudError):
                cloud.generate([{"role": "user", "content": [{"type": "input_audio"}]}],
                               SCHEMA, validate=json.loads)
            with self.assertRaises(cloud.CloudError):
                cloud.generate([{"role": "user", "content": "a" * 100001}], SCHEMA, validate=json.loads)
            api.assert_not_called()

    def test_fixed_origin_no_proxy_and_no_redirect(self):
        with patch.object(cloud.request, "build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{}'
            payload = cloud.build_payload(MESSAGES, SCHEMA, "gpt-6.1-sol")
            cloud._post(payload, "not-a-real-key", 10)
            req = opener.return_value.open.call_args.args[0]
            self.assertEqual("https://api.openai.com/v1/responses", req.full_url)
            self.assertEqual(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"), req.data)
            self.assertEqual({}, opener.call_args.args[0].proxies)
        with self.assertRaises(cloud.CloudError):
            cloud._NoRedirect().redirect_request(None, None, 307, "", {}, "https://example.com")

    def test_default_selects_cloud_with_key_and_local_without_key(self):
        self.assertEqual("openai", insights._provider())
        self.assertEqual("ollama-local", insights._provider("qwen3:4b"))
        with patch.dict(cloud.os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual("ollama-local", insights._provider())
        self.assertEqual("openai", insights.probe_model()["provider"])
        self.assertEqual(1, insights.probe_model()["budget_usd"])

    def test_dotenv_read_does_not_execute_shell_syntax(self):
        marker = Path(self.temp.name) / "should-not-exist"
        cloud.DOTENV_PATH.write_text('OPENAI_API_KEY="$(touch ' + str(marker) + ')"\n')
        with patch.dict(cloud.os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(cloud.CloudError):
                cloud.has_api_key()
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
