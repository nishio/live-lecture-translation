import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_subscription_replay as replay


def rows(count=30, step=10):
    return [{"id": f"s{i}", "start_seconds": i * step, "end_seconds": (i + 1) * step,
             "text": f"Synthetic claim number {i}.", "language": "en", "uncertain": False}
            for i in range(count)]


def fake_generate(messages, schema, **kwargs):
    data = json.loads(messages[1]["content"])
    if "target_groups" in data:
        output = {"blocks": [{"text": "人工文の日本語訳です。", "source_ids": group}
                             for group in data["target_groups"]]}
    else:
        output = {"headline": {"text": "人工の試験です。", "source_ids": [data["transcript"][0]["id"]]},
                  "summary": [], "flow": [], "concepts": [], "questions": [], "translations": []}
    return {"output": output, "usage": {"input_tokens": 100, "output_tokens": 20}, "elapsed_seconds": 1}


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root_patch = patch.object(replay, "ROOT", self.root)
        self.root_patch.start()
        self.source = self.root / "transcript.json"
        self.write_source(rows())

    def tearDown(self):
        self.root_patch.stop()
        self.temporary.cleanup()

    def write_source(self, lines, **extras):
        self.source.write_text(json.dumps({"lines": lines, **extras}))
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def run_replay(self, **overrides):
        args = {"expect_sha256": self.digest, "out_dir": self.root / "results" / "replay",
                "generate": fake_generate, "preflight": lambda: {"auth_mode": "chatgpt", "ready": True}}
        args.update(overrides)
        return replay.run_replay(self.source, **args)

    def test_plan_never_uses_future_sources_or_generated_prose(self):
        lines = rows()
        lines[3]["uncertain"] = True
        lines[4]["language"] = "ja"
        lines[5]["text"] = ""
        plan = replay.plan_replay(lines)
        self.assertEqual(8, plan["model_requests"])
        self.assertEqual(["s3", "s4", "s5"], plan["excluded_source_ids"])
        target_ids = []
        for task in plan["tasks"]:
            data = json.loads(task["request"]["messages"][1]["content"])
            self.assertTrue(all(row["end_seconds"] <= task["cutoff_seconds"] for row in data["transcript"]))
            if task["kind"] == "translation":
                target_ids.extend(task["request"]["target_source_ids"])
                self.assertTrue(all(key not in target_ids for key in ("s3", "s4", "s5")))
            else:
                self.assertFalse(task["request"]["use_previous"])
                self.assertEqual({}, data["previous_context"])
                self.assertEqual([], data["translation_ids"])
                self.assertTrue(all(not row["uncertain"] and row["text"] for row in data["transcript"]))
                self.assertTrue(all(row["end_seconds"] > task["cutoff_seconds"] - 180
                                    for row in data["transcript"]))
        self.assertEqual(plan["eligible_source_ids"], target_ids)

    def test_analysis_previous_cutoff_and_last_100_lines_are_explicit(self):
        lines = rows(130, step=1)
        for row in lines:
            row["text"] = "A."
        plan = replay.plan_replay(lines, translation_interval=130,
                                  analysis_interval=65, max_calls=20)
        analyses = [task for task in plan["tasks"] if task["kind"] == "analysis"]
        self.assertEqual([65, 100], [len(task["request"]["source_line_ids"]) for task in analyses])
        self.assertEqual([f"s{i}" for i in range(30)], analyses[1]["window_omitted_source_ids"])
        data = json.loads(analyses[1]["request"]["messages"][1]["content"])
        self.assertEqual(65, data["previous_through_seconds"])
        self.assertEqual([f"s{i}" for i in range(65, 130)], data["new_source_ids"])

    def test_dense_checkpoint_drains_multiple_fifo_batches(self):
        plan = replay.plan_replay(rows(50, step=1), duration=60)
        translated = [task for task in plan["tasks"] if task["kind"] == "translation"]
        self.assertEqual([24, 24, 2], [len(task["request"]["target_source_ids"]) for task in translated])
        self.assertEqual([60, 60, 60], [task["cutoff_seconds"] for task in translated])
        self.assertEqual([f"s{i}" for i in range(50)],
                         [key for task in translated for key in task["request"]["target_source_ids"]])

    def test_full_success_private_snapshots_and_exact_actual_coverage(self):
        report, directory = self.run_replay()
        self.assertEqual("completed", report["status"])
        self.assertTrue(report["coverage_complete"])
        self.assertEqual([f"s{i}" for i in range(30)], report["covered_source_ids"])
        self.assertEqual([], report["pending_source_ids"])
        self.assertEqual(self.source.read_bytes(), (directory / "source-input.json").read_bytes())
        self.assertEqual(0o600, (directory / "source-input.json").stat().st_mode & 0o777)
        self.assertEqual(0o600, (directory / "plan.json").stat().st_mode & 0o777)
        self.assertEqual(report, json.loads((directory / "report.json").read_text()))
        for item in report["results"]:
            self.assertTrue((directory / item["attempt_path"] / "validated.json").is_file())
        self.assertEqual(0, report["application_api_key_requests"])
        self.assertIsNone(report["measured_api_cost_usd"])
        self.assertFalse(report["real_time_scheduler"])

    def test_failure_preserves_completed_coverage_and_future_pending(self):
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise TimeoutError("do not publish private details")
            return fake_generate(*args, **kwargs)
        report, directory = self.run_replay(generate=fail_second)
        self.assertEqual(2, len(calls))
        self.assertEqual(["completed", "failed"] + ["pending"] * 6,
                         [item["status"] for item in report["results"]])
        self.assertEqual([f"s{i}" for i in range(6)], report["covered_source_ids"])
        self.assertEqual(24, len(report["pending_source_ids"]))
        self.assertNotIn("private details", (directory / "report.json").read_text())

    def test_validation_failure_keeps_reported_usage_without_coverage(self):
        def bad_output(*args, **kwargs):
            return {"output": {"blocks": []}, "usage": {"input_tokens": 100}}
        report, _ = self.run_replay(generate=bad_output)
        self.assertEqual("validation", report["results"][0]["failure_stage"])
        self.assertEqual([], report["covered_source_ids"])
        self.assertFalse(report["results"][0]["usage_uncertain"])
        self.assertEqual("completed", report["results"][0]["transport_status"])

    def test_save_failure_does_not_complete_or_advance_coverage(self):
        save = replay._save
        def fail_output(path, value):
            if path.name == "validated.json":
                raise OSError("full disk")
            save(path, value)
        with patch.object(replay, "_save", side_effect=fail_output):
            report, _ = self.run_replay()
        self.assertEqual("persistence", report["results"][0]["failure_stage"])
        self.assertEqual([], report["covered_source_ids"])
        self.assertEqual("pending", report["results"][1]["status"])

    def test_source_hash_mismatch_and_cap_reject_before_auth_or_inference(self):
        for overrides in ({"expect_sha256": "a" * 64}, {"max_calls": 1}, {"expect_sha256": None}):
            with self.subTest(overrides=overrides), patch.object(replay.codex, "generate") as generate, \
                    patch.object(replay.codex, "check") as check:
                with self.assertRaises(ValueError):
                    self.run_replay(generate=generate, preflight=check, **overrides)
                generate.assert_not_called()
                check.assert_not_called()

    def test_source_drift_stops_frozen_future_work(self):
        def mutate_source(*args, **kwargs):
            self.source.write_text("{}")
            return fake_generate(*args, **kwargs)
        report, _ = self.run_replay(generate=mutate_source)
        self.assertEqual(["completed", "failed"] + ["pending"] * 6,
                         [item["status"] for item in report["results"]])
        self.assertEqual("source_integrity", report["results"][1]["failure_stage"])
        self.assertEqual(6, len(report["covered_source_ids"]))

    def test_uncanonical_input_is_rejected(self):
        edits = [("id", "s1"), ("start_seconds", True), ("end_seconds", float("nan")),
                 ("uncertain", 1), ("text", None), ("language", " ")]
        for key, value in edits:
            with self.subTest(key=key, value=value):
                lines = rows()
                lines[0][key] = value
                self.write_source(lines)
                with self.assertRaises(ValueError):
                    replay.load_input(self.source)
        self.source.write_text('{"lines":[],"lines":[]}')
        with self.assertRaises(ValueError):
            replay.load_input(self.source)

    def test_normalization_preserves_evidence_and_strips_all_unrelated_metadata(self):
        lines = rows(1)
        lines[0]["unrelated"] = "PRIVATE GENERATED OUTPUT"
        lines[0]["translation"] = "PRIVATE GENERATED OUTPUT"
        self.write_source(lines, baseline="PRIVATE GENERATED OUTPUT")
        source = replay.load_input(self.source)
        self.assertIn(b"PRIVATE GENERATED OUTPUT", source["raw"])
        plan = replay.plan_replay(source["lines"])
        self.assertNotIn("PRIVATE GENERATED OUTPUT", json.dumps(plan))
        self.assertEqual(set(rows(1)[0]), set(source["lines"][0]))

    def test_private_output_and_option_bounds(self):
        with self.assertRaises(ValueError):
            self.run_replay(out_dir=self.root / "public")
        for overrides in ({"timeout": 301}, {"timeout": True}, {"duration": float("nan")},
                          {"translation_interval": 0}, {"max_calls": True},
                          {"analysis_window": -1}, {"analysis_interval": 0.00001}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.run_replay(**overrides)

    def test_transport_failure_recovers_usage_from_confined_private_attempt(self):
        def fail_with_usage(*args, **kwargs):
            attempt = kwargs["out_dir"] / "provider"
            attempt.mkdir()
            (attempt / "state.json").write_text(json.dumps({"usage": {"input_tokens": 300},
                                                           "elapsed_seconds": 2}))
            raise replay.codex.CodexTransportError("failed_turn", attempt)
        report, _ = self.run_replay(generate=fail_with_usage)
        failed = report["results"][0]
        self.assertEqual({"input_tokens": 300}, failed["usage"])
        self.assertEqual("01-translation/provider", failed["provider_attempt_path"])
        self.assertEqual("failed_turn", failed["error_reason"])
        self.assertFalse(failed["usage_uncertain"])

    def test_preflight_failures_leave_all_work_pending(self):
        for preflight in (lambda: {"auth_mode": "api"}, lambda: {"auth_mode": "chatgpt", "ready": False}):
            with patch.object(replay.codex, "generate") as generate:
                report, _ = self.run_replay(generate=generate, preflight=preflight)
            generate.assert_not_called()
            self.assertEqual("preflight", report["failure_stage"])
            self.assertTrue(all(item["status"] == "pending" for item in report["results"]))

    def test_cli_requires_authorization_and_hash_and_plan_is_read_only(self):
        with patch.object(replay, "run_replay") as run, contextlib.redirect_stderr(io.StringIO()):
            for args in ([], ["--confirm-subscription-use"], ["--expect-sha256", self.digest]):
                with self.assertRaises(SystemExit):
                    replay.main(["run", str(self.source), *args])
            run.assert_not_called()
        with patch.object(replay.codex, "generate") as generate, patch.object(replay.codex, "check") as check, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, replay.main(["plan", str(self.source)]))
            self.assertEqual(self.digest, json.loads(output.getvalue())["input_sha256"])
        generate.assert_not_called()
        check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
