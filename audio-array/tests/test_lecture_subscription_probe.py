import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_subscription_probe as probe


def fake_generate(messages, schema, **kwargs):
    data = json.loads(messages[1]["content"])
    if "target_groups" in data:
        output = {"blocks": [{"text": "人工の試験文です。", "source_ids": group}
                             for group in data["target_groups"]]}
    else:
        output = {"headline": {"text": "人工の実験について。", "source_ids": ["s1"]},
                  "summary": [], "flow": [], "concepts": [], "questions": [], "translations": []}
    return {"output": output, "usage": {"input_tokens": 100, "output_tokens": 20},
            "elapsed_seconds": 1.25}


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root_patch = patch.object(probe, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temporary.cleanup()

    def run_probe(self, **overrides):
        args = {"out_dir": self.root / "results" / "probe", "generate": fake_generate,
                "preflight": lambda: {"auth_mode": "chatgpt"}}
        args.update(overrides)
        return probe.run_probe(**args)

    def test_fixture_keeps_uncertainty_and_exact_target_boundaries(self):
        self.assertTrue(next(line for line in probe.synthetic_lines() if line["id"] == "u1")["uncertain"])
        translation, analysis = probe.workloads()
        self.assertEqual([["s1", "s2", "s3"], ["s4", "s5", "s6"]], translation[1]["groups"])
        translation_data = json.loads(translation[1]["messages"][1]["content"])
        self.assertIn("u1", translation_data["context_source_ids"])
        self.assertTrue(next(row for row in translation_data["transcript"] if row["id"] == "u1")["uncertain"])
        analysis_data = json.loads(analysis[1]["messages"][1]["content"])
        self.assertNotIn("u1", [line["id"] for line in analysis_data["transcript"]])
        self.assertEqual([], analysis[1]["translation_ids"])
        self.assertFalse(analysis[1]["use_previous"])

    def test_success_persists_before_marking_complete_and_separates_cost(self):
        report, directory = self.run_probe(repetitions=2)
        self.assertEqual("completed", report["status"])
        self.assertEqual(4, len(report["results"]))
        for result in report["results"]:
            self.assertEqual("completed", result["status"])
            self.assertTrue((directory / result["attempt_path"] / "validated.json").is_file())
        self.assertEqual(["s1", "s2", "s3", "s4", "s5", "s6"], report["results"][0]["covered_source_ids"])
        self.assertEqual(report, json.loads((directory / "report.json").read_text()))
        self.assertEqual(0, report["application_api_key_requests"])
        self.assertIsNone(report["measured_api_cost_usd"])
        self.assertEqual("not automatically assessed", report["semantic_quality"])

    def test_analysis_only_does_not_rerun_translation(self):
        report, _ = self.run_probe(workload="analysis")
        self.assertEqual("completed", report["status"])
        self.assertEqual(["analysis"], [item["kind"] for item in report["results"]])
        self.assertEqual(1, probe.plan_summary(workload="analysis")["model_requests"])
        with self.assertRaises(ValueError):
            self.run_probe(workload="unexpected")

    def test_invalid_source_coverage_is_failed_not_completed_and_stops(self):
        calls = []
        def wrong(*args, **kwargs):
            calls.append(1)
            return {"output": {"blocks": [{"text": "不正な結果", "source_ids": ["u1"]}]},
                    "usage": {"input_tokens": 100, "output_tokens": 20}}
        report, directory = self.run_probe(generate=wrong, repetitions=3)
        self.assertEqual(1, len(calls))
        self.assertEqual("failed", report["status"])
        self.assertEqual("completed", report["results"][0]["transport_status"])
        self.assertEqual("validation", report["results"][0]["failure_stage"])
        self.assertFalse(report["results"][0]["usage_uncertain"])
        self.assertEqual(["failed"] + ["pending"] * 5, [item["status"] for item in report["results"]])
        self.assertFalse((directory / "01-translation" / "validated.json").exists())

    def test_analysis_failure_preserves_completed_translation_and_pending_work(self):
        calls = []
        def fail_analysis(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise TimeoutError("private diagnostic must not be copied")
            return fake_generate(*args, **kwargs)
        report, directory = self.run_probe(generate=fail_analysis, repetitions=2)
        self.assertEqual(["completed", "failed", "pending", "pending"],
                         [item["status"] for item in report["results"]])
        self.assertNotIn("private diagnostic", (directory / "report.json").read_text())
        self.assertTrue(report["results"][1]["usage_uncertain"])

    def test_persistence_failure_never_advances_coverage(self):
        save = probe._save
        def fail_validated(path, value):
            if path.name == "validated.json":
                raise OSError("disk failure")
            save(path, value)
        with patch.object(probe, "_save", side_effect=fail_validated):
            report, _ = self.run_probe()
        self.assertEqual("failed", report["status"])
        self.assertEqual("persistence", report["results"][0]["failure_stage"])
        self.assertNotIn("covered_source_ids", report["results"][0])
        self.assertFalse(report["results"][0]["usage_uncertain"])
        self.assertEqual("pending", report["results"][1]["status"])

    def test_preflight_failure_does_not_call_model(self):
        def reject():
            raise RuntimeError("not ChatGPT")
        with patch.object(probe.codex, "generate") as generate:
            report, _ = self.run_probe(preflight=reject, generate=generate)
        generate.assert_not_called()
        self.assertEqual("preflight", report["failure_stage"])
        self.assertEqual(["pending", "pending"], [item["status"] for item in report["results"]])

    def test_cancel_keeps_pending_work(self):
        def cancel(*args, **kwargs):
            raise KeyboardInterrupt()
        report, _ = self.run_probe(generate=cancel)
        self.assertEqual("cancelled", report["status"])
        self.assertEqual("pending", report["results"][1]["status"])

    def test_no_inference_without_confirmation(self):
        with patch.object(probe, "run_probe") as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                probe.main(["run"])
        run.assert_not_called()

    def test_plan_is_pure_and_bounded(self):
        with patch.object(probe.codex, "check") as check, patch.object(probe.codex, "generate") as generate:
            self.assertEqual(6, probe.plan_summary(3)["model_requests"])
        check.assert_not_called()
        generate.assert_not_called()
        for repetitions in (0, 4, True):
            with self.assertRaises(ValueError):
                self.run_probe(repetitions=repetitions)
        for timeout in (0, 301, float("nan"), True):
            with self.assertRaises(ValueError):
                self.run_probe(timeout=timeout)
        with self.assertRaises(ValueError):
            self.run_probe(out_dir=self.root / "public")


if __name__ == "__main__":
    unittest.main()
