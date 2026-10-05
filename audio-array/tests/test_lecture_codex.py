"""CPU-only transport tests: the executable below never makes network calls."""
import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_codex as codex


MESSAGES = [{"role": "system", "content": "Translate the supplied synthetic sentence."},
            {"role": "user", "content": "The fictional bus is blue."}]
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["text"],
          "properties": {"text": {"type": "string"}}}
USAGE = {"input_tokens": 50, "cached_input_tokens": 0, "output_tokens": 12}


class CodexTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root_patch = patch.object(codex, "ROOT", self.base)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.executable = self.base / "fake-codex"
        self.control = self.base / "control.json"
        self.calls = self.base / "calls.jsonl"
        self.control.write_text("{}")
        self.executable.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, signal, subprocess, sys, time
base = pathlib.Path(__file__).resolve().parent
control = json.loads((base / "control.json").read_text())
with (base / "calls.jsonl").open("a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd(),
                          "cwd_files": os.listdir(".")}) + "\n")
if "--version" in sys.argv:
    print("codex-cli " + control.get("version", "0.159.0-alpha.12.1"))
    raise SystemExit(0)
if "login" in sys.argv:
    print(control.get("auth", "Logged in using ChatGPT"), file=sys.stderr)
    raise SystemExit(control.get("auth_exit", 0))
if "exec" not in sys.argv:
    raise SystemExit(90)
prompt = sys.stdin.read()
(base / "captured-prompt.txt").write_text(prompt)
if control.get("timeout"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (base / "child.pid").write_text(str(child.pid))
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
events = control.get("events", [
    {"type": "thread.started", "thread_id": "synthetic"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
    {"type": "turn.completed", "usage": {
        "input_tokens": 50, "cached_input_tokens": 0, "output_tokens": 12}}])
for event in events:
    print(event if isinstance(event, str) else json.dumps(event), flush=True)
print(control.get("stderr", ""), file=sys.stderr)
path = pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1])
if not control.get("no_output"):
    path.write_text(control.get("output", '{"text":"架空のバスは青い。"}'))
raise SystemExit(control.get("exit_code", 0))
''', encoding="utf-8")
        self.executable.chmod(0o700)

    def settings(self, **values):
        self.control.write_text(json.dumps(values))

    def run_trial(self, **kwargs):
        return codex.generate(MESSAGES, SCHEMA, executable=str(self.executable),
                              out_dir=self.base / "results", **kwargs)

    def call_log(self):
        return [json.loads(row) for row in self.calls.read_text().splitlines()]

    def assert_failure(self, reason):
        with self.assertRaises(codex.CodexTransportError) as caught:
            self.run_trial()
        self.assertEqual(reason, caught.exception.reason)
        state = json.loads((caught.exception.attempt_dir / "state.json").read_text())
        self.assertEqual("failed", state["status"])
        self.assertFalse(state["completion_confirmed"])
        self.assertEqual(reason, state["reason"])
        return state, caught.exception

    def test_check_is_read_only_and_refuses_api_key_auth(self):
        result = codex.check(executable=str(self.executable))
        self.assertTrue(result["ready"])
        self.assertEqual("chatgpt", result["auth_mode"])
        self.assertEqual("0.159.0-alpha.12.1", result["version"])
        self.assertFalse(result["inference_started"])
        self.assertEqual(2, len(self.call_log()))
        self.settings(auth="Logged in using an API key - PRIVATE_KEY_MARKER")
        with self.assertRaises(codex.CodexTransportError) as caught:
            codex.check(executable=str(self.executable))
        self.assertEqual("chatgpt_login_required", caught.exception.reason)
        self.assertNotIn("PRIVATE_KEY", str(caught.exception))

    def test_success_is_persisted_with_usage_and_private_files(self):
        result = self.run_trial()
        self.assertEqual({"text": "架空のバスは青い。"}, result["output"])
        self.assertEqual(USAGE, result["usage"])
        self.assertEqual("reported", result["usage_status"])
        self.assertEqual("completed", result["status"])
        self.assertIsNone(result["measured_api_cost_usd"])
        self.assertIsNone(result["subscription_allowance_consumed"])
        self.assertGreater(result["elapsed_seconds"], 0)
        attempt = Path(result["attempt_dir"])
        self.assertEqual(0o700, stat.S_IMODE(attempt.stat().st_mode))
        for name in ("state.json", "messages.json", "original-schema.json", "schema.json", "prompt.txt",
                     "stdout.jsonl", "stderr.txt", "response.json"):
            self.assertTrue((attempt / name).is_file(), name)
            self.assertEqual(0o600, stat.S_IMODE((attempt / name).stat().st_mode))
        self.assertEqual(MESSAGES, json.loads((attempt / "messages.json").read_text()))

    def test_schema_conversion_is_pure_and_duplicate_sources_still_fail_validation(self):
        import event_insights_cloud as cloud
        import lecture_analysis as analysis
        schema = analysis._schema(["a"], [])
        original = copy.deepcopy(schema)
        adapted = codex._compatible_schema(schema)
        self.assertEqual(cloud._compatible_schema(schema), adapted)
        self.assertNotIn("uniqueItems", json.dumps(adapted))
        self.assertIn("uniqueItems", json.dumps(schema))
        self.assertEqual(original, schema)
        self.assertEqual({"type": "array", "items": {"type": "string"}},
                         codex._compatible_schema({"type": "array", "uniqueItems": True}))
        value = {"headline": {"text": "架空のバスの説明。", "source_ids": ["a", "a"]},
                 "summary": [], "flow": [], "questions": [], "concepts": [], "translations": []}
        self.settings(output=json.dumps(value, ensure_ascii=False))
        result = codex.generate(MESSAGES, schema, executable=str(self.executable),
                                out_dir=self.base / "results")
        attempt = Path(result["attempt_dir"])
        self.assertEqual(original, json.loads((attempt / "original-schema.json").read_text()))
        self.assertEqual(adapted, json.loads((attempt / "schema.json").read_text()))
        self.assertEqual(original, schema)
        # CLI acceptance is transport completion, not application publication.
        with self.assertRaises(analysis.SnapshotResponseError):
            analysis.validate_snapshot_response(result["output"], ["a"])
        result["output"]["headline"]["source_ids"] = ["a"]
        analysis.validate_snapshot_response(result["output"], ["a"])

    def test_missing_usage_is_unknown_never_zero_cost(self):
        self.settings(events=[{"type": "turn.completed"}])
        result = self.run_trial()
        self.assertIsNone(result["usage"])
        self.assertEqual("unknown", result["usage_status"])
        self.assertIsNone(result["measured_api_cost_usd"])

    def test_failed_turn_at_exit_zero_is_failure_and_logs_remain_private(self):
        self.settings(events=[{"type": "turn.failed", "error": {"message": "SECRET"}}],
                      stderr="PRIVATE_STDERR")
        state, error = self.assert_failure("failed_turn")
        self.assertTrue(state["process_started"])
        self.assertIsNone(state["inference_started"])
        self.assertIsNone(state["usage"])
        self.assertEqual("unknown", state["usage_status"])
        self.assertNotIn("SECRET", str(error))
        self.assertIn("PRIVATE_STDERR", (error.attempt_dir / "stderr.txt").read_text())

    def test_error_event_even_with_completion_is_failure(self):
        self.settings(events=[{"type": "error", "message": "secret"},
                              {"type": "turn.completed", "usage": USAGE}])
        state, _ = self.assert_failure("failed_turn")
        self.assertEqual(USAGE, state["usage"])

    def test_nonzero_exit_even_with_completion_is_failure(self):
        self.settings(exit_code=7)
        state, _ = self.assert_failure("process_failed")
        self.assertEqual(7, state["returncode"])
        self.assertEqual(USAGE, state["usage"])

    def test_no_terminal_completion_is_not_success(self):
        self.settings(events=[{"type": "turn.started"}])
        self.assert_failure("completion_unconfirmed")

    def test_tool_execution_or_unknown_event_fails_closed(self):
        for event, reason in (
                ({"type": "item.started", "item": {"type": "command_execution"}},
                 "unexpected_tool_or_item"),
                ({"type": "item.completed", "item": {"type": "mcp_tool_call"}},
                 "unexpected_tool_or_item"),
                ({"type": "future.event"}, "unsupported_event"),
                ("NOT_JSON", "invalid_events")):
            with self.subTest(event=event):
                self.settings(events=[event, {"type": "turn.completed", "usage": USAGE}])
                self.assert_failure(reason)

    def test_only_exact_disabled_code_mode_startup_advisory_is_nonfatal(self):
        message = next(iter(codex.KNOWN_STARTUP_ADVISORIES))
        self.settings(events=[
            {"type": "item.completed", "item": {"type": "error", "message": message}},
            {"type": "turn.completed", "usage": USAGE}])
        result = self.run_trial()
        self.assertEqual({"code_mode_disabled": 1}, result["startup_advisories"])
        self.assertEqual(1, result["startup_advisory_count"])
        state = json.loads((Path(result["attempt_dir"]) / "state.json").read_text())
        self.assertEqual(result["startup_advisories"], state["startup_advisories"])
        for kind, item_message in (("item.completed", message + " Other error."),
                                   ("item.completed", message[:-1]),
                                   ("item.started", message)):
            with self.subTest(kind=kind, message=item_message):
                self.settings(events=[
                    {"type": kind, "item": {"type": "error", "message": item_message}},
                    {"type": "turn.completed", "usage": USAGE}])
                self.assert_failure("unexpected_tool_or_item")
        self.settings(events=[{"type": "error", "message": message},
                              {"type": "turn.completed", "usage": USAGE}])
        self.assert_failure("failed_turn")

    def test_auth_refusal_never_starts_generation(self):
        self.settings(auth="Not logged in", auth_exit=1)
        state, _ = self.assert_failure("chatgpt_login_required")
        self.assertFalse(state["inference_started"])
        self.assertEqual("not_started", state["usage_status"])
        self.assertTrue(all("exec" not in entry["argv"] for entry in self.call_log()))

    def test_malformed_missing_or_duplicate_key_output_is_failure(self):
        for output in ("", "{broken", "[]", '{"x":1,"x":2}', '{"x":NaN}'):
            with self.subTest(output=output):
                self.settings(output=output)
                self.assert_failure("invalid_output")

    def test_environment_working_directory_and_capabilities_are_isolated(self):
        secrets = {"OPENAI_API_KEY": "secret", "CODEX_API_KEY": "secret",
                   "OPENAI_BASE_URL": "https://example.invalid",
                   "CODEX_ACCESS_TOKEN": "secret", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "other",
                   "CODEX_THREAD_ID": "parent", "OTHER_PLUGIN_SECRET": "secret",
                   "CODEX_HOME": str(self.base / "existing-auth-home")}
        with patch.dict(os.environ, secrets):
            self.run_trial()
        calls = self.call_log()
        self.assertEqual(3, len(calls))
        for call in calls:
            self.assertEqual([], call["cwd_files"])
            self.assertFalse(Path(call["cwd"]).is_relative_to(codex.ROOT))
            self.assertEqual(secrets["CODEX_HOME"], call["env"]["CODEX_HOME"])
            for key in secrets:
                if key != "CODEX_HOME":
                    self.assertNotIn(key, call["env"])
        argv = calls[-1]["argv"]
        for flag in ("--no-daemon", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                     "--skip-git-repo-check", "--json", "read-only", "never",
                     'forced_login_method="chatgpt"', "project_doc_max_bytes=0",
                     'web_search="disabled"', "features.skip_host_skill_discovery=true"):
            self.assertIn(flag, argv)
        self.assertIn("suppress_unstable_features_warning=true", argv)
        for feature in codex.DISABLED_FEATURES:
            self.assertIn(feature, argv)
            self.assertEqual("--disable", argv[argv.index(feature) - 1])
        self.assertNotIn("resume", argv)
        self.assertNotIn("--with-api-key", argv)
        self.assertIn("fictional bus", (self.base / "captured-prompt.txt").read_text())

    def test_timeout_kills_own_process_group_and_retains_unknown_usage(self):
        self.settings(timeout=True)
        with self.assertRaises(codex.CodexTransportError) as caught:
            self.run_trial(timeout=0.4)
        self.assertEqual("timeout", caught.exception.reason)
        attempt = caught.exception.attempt_dir
        state = json.loads((attempt / "state.json").read_text())
        self.assertTrue(state["process_started"])
        self.assertIsNone(state["inference_started"])
        self.assertEqual("unknown", state["usage_status"])
        self.assertEqual("failed", state["status"])
        child_pid = int((self.base / "child.pid").read_text())
        # macOS init may take a moment to reap a killed orphan. A zombie is also
        # stopped; verify with a read-only ps instead of risking another signal.
        import subprocess
        child = subprocess.run(["ps", "-p", str(child_pid), "-o", "stat="],
                               capture_output=True, text=True)
        self.assertTrue(not child.stdout.strip() or child.stdout.strip().startswith("Z"),
                        child.stdout)

    def test_identical_calls_make_new_attempts_without_reusing_a_cache(self):
        first = self.run_trial()
        second = self.run_trial()
        self.assertNotEqual(first["attempt_dir"], second["attempt_dir"])
        self.assertEqual(2, sum("exec" in call["argv"] for call in self.call_log()))

    def test_unknown_cli_version_refuses_before_login_or_generation(self):
        self.settings(version="99.0.0")
        self.assert_failure("unsupported_cli_version")
        self.assertEqual(1, len(self.call_log()))

    def test_post_spawn_storage_failure_stops_the_started_process(self):
        self.settings(timeout=True)
        original_write = codex._write
        original_popen = codex.subprocess.Popen
        processes = []
        failed_once = False

        def record_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process

        def fail_started_write(path, value):
            nonlocal failed_once
            if path.name == "state.json" and value.get("process_started") and not failed_once:
                failed_once = True
                raise OSError("synthetic disk failure")
            return original_write(path, value)

        with patch.object(codex, "_write", side_effect=fail_started_write), \
                patch.object(codex.subprocess, "Popen", side_effect=record_process):
            self.assert_failure("local_process_or_io_error")
        self.assertEqual(3, len(processes))
        self.assertTrue(all(process.poll() is not None for process in processes))

    def test_rejects_nonprivate_repository_destination_and_invalid_input(self):
        with self.assertRaises(codex.CodexTransportError) as caught:
            codex.generate(MESSAGES, SCHEMA, out_dir=codex.ROOT / "docs")
        self.assertEqual("private_output_directory_required", caught.exception.reason)
        with self.assertRaises(codex.CodexTransportError) as caught:
            codex.generate(MESSAGES, SCHEMA, out_dir=self.base.parent / "unignored")
        self.assertEqual("private_output_directory_required", caught.exception.reason)
        for timeout in (0, -1, float("nan"), True):
            with self.assertRaises(codex.CodexTransportError):
                self.run_trial(timeout=timeout)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
