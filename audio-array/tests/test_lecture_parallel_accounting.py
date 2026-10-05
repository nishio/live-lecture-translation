"""Concurrent accounting with temporary ledgers and synthetic transport only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import event_insights_cloud as cloud
import lecture_live as live
from processing_control import ProcessingStopped, processing_scope


SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}},
          "required": ["summary"], "additionalProperties": False}


def messages(kind):
    return [{"role": "user", "content": kind}]


def response(kind):
    return {"model": cloud.DEFAULT_MODEL, "service_tier": "default", "status": "completed",
            "output": [{"type": "message", "status": "completed", "content": [
                {"type": "output_text", "text": json.dumps({"summary": "synthetic " + kind})}]}],
            "usage": {"input_tokens": 1000, "output_tokens": 100 if kind == "translation" else 200,
                      "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300}}}


class ParallelAccountingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for patcher in (patch.object(cloud, "STATE_DIR", self.root / "ledger"),
                        patch.object(cloud, "BUDGET_AUTHORIZATION_PATH", None),
                        patch.object(cloud, "_api_key", return_value="synthetic-test-key"),
                        patch.object(cloud, "_day", return_value="2030-06-01")):
            patcher.start()
            self.addCleanup(patcher.stop)
        authorization = self.root / "authorization.json"
        authorization.write_text(json.dumps({"human_approved": True, "destination": cloud.API_URL,
            "raw_audio_allowed": False, "allowed_dates": ["2030-06-01"],
            "daily_budget_usd_by_date": {"2030-06-01": .01}}))
        cloud.configure_budget_authorization(authorization)

    def test_concurrent_publications_cannot_replace_new_cost_report_with_older_snapshot(self):
        app = live.LectureApp(data_root=self.root / "data", results_root=self.root / "results",
                              transcriber=lambda *args: None)
        app.result_dir = self.root / "session"
        app.result_dir.mkdir()
        app.state["analysis"]["provider"] = "openai"
        ledger = {"requests": {}}
        for kind, amount in (("translation", 10_000_000), ("analysis", 20_000_000)):
            payload = {"synthetic_stage": kind}
            directory = app.result_dir / ("translations" if kind == "translation" else "analyses") / "synthetic"
            directory.mkdir(parents=True)
            (directory / "payload.json").write_text(json.dumps(payload))
            fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            ledger["requests"][fingerprint] = {"fingerprint": fingerprint, "charged_nanodollars": amount,
                "state": "completed" if kind == "translation" else "reserved"}
            if kind == "analysis":
                analysis_identity = fingerprint
        live.append_json(app.result_dir / "translation-history.jsonl", {"cost_usd": .01})
        entered, release, publishing, published = (threading.Event() for _ in range(4))
        errors, written = [], []
        original_write = live.atomic_json

        def write(path, value):
            if Path(path).name == "cost-report.json":
                if threading.current_thread().name == "older-report":
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("synthetic report wait timed out")
                written.append(value["successful_generation_api_usd"])
            original_write(path, value)

        def old_report():
            try:
                app._cost_report()
            except Exception as exc:
                errors.append(exc)

        def new_publication():
            try:
                publishing.set()
                with app.lock:
                    live.append_json(app.result_dir / "analysis-history.jsonl", {"cost_usd": .02})
                    ledger["requests"][analysis_identity]["state"] = "completed"
                app._cost_report()
                published.set()
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=old_report, name="older-report"),
                   threading.Thread(target=new_publication)]
        with patch.object(live, "atomic_json", write), \
                patch.object(cloud, "_load_ledger", side_effect=lambda: deepcopy(ledger)), \
                patch.object(cloud, "budget_status", return_value={}):
            workers[0].start()
            try:
                self.assertTrue(entered.wait(3))
                workers[1].start()
                self.assertTrue(publishing.wait(3))
                self.assertFalse(published.wait(.1), "Publication cannot pass an older report's pending replace")
            finally:
                release.set()
                for worker in workers:
                    if worker.ident is not None:
                        worker.join(3)
            self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual([], errors)
        self.assertEqual([.01, .03], written)
        saved = json.loads((app.result_dir / "cost-report.json").read_text())
        self.assertEqual(.03, saved["successful_generation_api_usd"])
        self.assertEqual(.03, saved["confirmed_api_usd"])
        self.assertEqual(.03, saved["additional_api_usd"])
        self.assertEqual(0, saved["retained_reservation_usd"])
        self.assertEqual(2, saved["matching_request_count"])

    def run_parallel_transport(self, *, stop_after_admission=False, failed_kind=None):
        entered = {kind: threading.Event() for kind in ("translation", "analysis")}
        release = {kind: threading.Event() for kind in entered}
        finished = {kind: threading.Event() for kind in entered}
        stop = threading.Event()
        results, errors, completion_order = {}, {}, []

        def post(payload, key, timeout):
            kind = payload["input"][-1]["content"]
            entered[kind].set()
            if not release[kind].wait(3):
                raise RuntimeError("synthetic transport wait timed out")
            if kind == failed_kind:
                raise cloud.CloudError("synthetic transport failure", category="transport", retryable=True)
            return response(kind)

        def generate(kind):
            try:
                with processing_scope(stop):
                    results[kind] = cloud.generate(messages(kind), SCHEMA, validate=json.loads)
            except Exception as exc:
                errors[kind] = exc
            finally:
                completion_order.append(kind)
                finished[kind].set()

        workers = {kind: threading.Thread(target=generate, args=(kind,)) for kind in entered}
        with patch.object(cloud, "_post", side_effect=post) as transport:
            workers["translation"].start()
            try:
                self.assertTrue(entered["translation"].wait(3))
                workers["analysis"].start()
                self.assertTrue(entered["analysis"].wait(3), "The first request must not hold the network slot")
                ledger = cloud._load_ledger()["requests"]
                self.assertEqual(2, len(ledger))
                self.assertTrue(all(entry["state"] == "reserved" for entry in ledger.values()))
                with self.assertRaises(cloud.BudgetExceededError):
                    cloud.generate(messages("x" * 40000), SCHEMA, validate=json.loads)
                self.assertEqual(2, transport.call_count)
                if stop_after_admission:
                    stop.set()
                    with processing_scope(stop), self.assertRaises(ProcessingStopped):
                        cloud.generate(messages("new work"), SCHEMA, validate=json.loads)
                    self.assertEqual(2, transport.call_count)
                release["analysis"].set()
                self.assertTrue(finished["analysis"].wait(3))
                self.assertFalse(finished["translation"].is_set())
                release["translation"].set()
                self.assertTrue(finished["translation"].wait(3))
            finally:
                for event in release.values():
                    event.set()
                for worker in workers.values():
                    if worker.ident is not None:
                        worker.join(3)
            self.assertFalse(any(worker.is_alive() for worker in workers.values()))
        self.assertEqual(["analysis", "translation"], completion_order)
        return results, errors, cloud._load_ledger()["requests"]

    def test_parallel_requests_settle_reverse_order_without_losing_budget_or_cache(self):
        results, errors, entries = self.run_parallel_transport()
        self.assertEqual({}, errors)
        self.assertEqual(2, len(entries))
        self.assertTrue(all(entry["state"] == "completed" for entry in entries.values()))
        expected = sum(cloud._actual_cost(response(kind), cloud.DEFAULT_MODEL)[0] for kind in results)
        self.assertEqual(expected, sum(entry["charged_nanodollars"] for entry in entries.values()))
        self.assertEqual(0, cloud.budget_status()["reserved_usd"])
        before = deepcopy(entries)
        with patch.object(cloud, "_post", side_effect=AssertionError("cached requests must not send")):
            for kind in results:
                cached = cloud.generate(messages(kind), SCHEMA, validate=json.loads)
                self.assertTrue(cached["cache_hit"])
                self.assertEqual({"summary": "synthetic " + kind}, cached["result"])
        self.assertEqual(before, cloud._load_ledger()["requests"])

    def test_stop_preserves_one_completed_peer_and_other_sent_unknown_charge(self):
        results, errors, entries = self.run_parallel_transport(stop_after_admission=True, failed_kind="translation")
        self.assertEqual({"analysis"}, set(results))
        self.assertEqual({"translation"}, set(errors))
        self.assertIsInstance(errors["translation"], cloud.CloudError)
        self.assertEqual(["completed", "failed"], sorted(entry["state"] for entry in entries.values()))
        failed = next(entry for entry in entries.values() if entry["state"] == "failed")
        self.assertEqual(failed["reserved_nanodollars"], failed["charged_nanodollars"])
        self.assertGreater(failed["charged_nanodollars"], 0)
        completed = next(entry for entry in entries.values() if entry["state"] == "completed")
        self.assertEqual(cloud._actual_cost(response("analysis"), cloud.DEFAULT_MODEL)[0],
                         completed["charged_nanodollars"])
        self.assertEqual(failed["charged_nanodollars"] / cloud.NANODOLLARS,
                         cloud.budget_status()["reserved_usd"])


if __name__ == "__main__":
    unittest.main()
