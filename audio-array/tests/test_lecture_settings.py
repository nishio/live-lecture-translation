"""Strict settings and isolated launcher fixtures; no models, capture, or network."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
REPO = SOURCE.parent
sys.path.insert(0, str(SOURCE))
import lecture_settings as settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "lecture settings.json"
        self.valid = {"schema_version": 1, "translation_interval_seconds": 30,
                      "analysis_interval_seconds": 60}

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")
        return self.path

    def test_defaults_are_independent_and_do_not_read_a_file(self):
        first = settings.load_settings()
        self.assertEqual(first, {"schema_version": 1, "translation_interval_seconds": 60,
                                 "analysis_interval_seconds": 120})
        first["translation_interval_seconds"] = 1
        self.assertEqual(settings.load_settings()["translation_interval_seconds"], 60)

    def test_valid_explicit_file_and_validation_return_copies(self):
        self.assertEqual(settings.load_settings(self.write(self.valid)), self.valid)
        validated = settings.validate_settings(self.valid)
        self.assertEqual(validated, self.valid)
        self.assertIsNot(validated, self.valid)

    def test_required_and_unknown_keys(self):
        for value in [[], None, 1, "settings", {}, {**self.valid, "extra": 1}]:
            with self.subTest(value=value), self.assertRaises(settings.SettingsError):
                settings.load_settings(self.write(value))
        for key in self.valid:
            value = dict(self.valid)
            value.pop(key)
            with self.subTest(missing=key), self.assertRaises(settings.SettingsError):
                settings.load_settings(self.write(value))

    def test_schema_must_be_exact_integer_one(self):
        for value in [True, False, 1.0, "1", 0, 2, None]:
            with self.subTest(value=value), self.assertRaises(settings.SettingsError):
                settings.load_settings(self.write({**self.valid, "schema_version": value}))

    def test_intervals_require_positive_bounded_integers(self):
        for name in ("translation_interval_seconds", "analysis_interval_seconds"):
            for value in [True, False, 0, -1, 3601, 30.0, 1.5, "30", None, [], {}]:
                with self.subTest(name=name, value=value), self.assertRaises(settings.SettingsError):
                    settings.load_settings(self.write({**self.valid, name: value}))
            for value in [1, 3600]:
                self.assertEqual(settings.load_settings(self.write({**self.valid, name: value}))[name], value)

    def test_duplicates_are_rejected_even_when_values_agree(self):
        for key, value in self.valid.items():
            self.path.write_text(json.dumps(self.valid)[:-1] + f', "{key}": {value}' + "}")
            with self.subTest(key=key), self.assertRaisesRegex(settings.SettingsError, "Duplicate"):
                settings.load_settings(self.path)

    def test_nonfinite_and_overflowed_json_numbers_are_rejected(self):
        for value in ["NaN", "Infinity", "-Infinity", "1e999"]:
            self.path.write_text('{"schema_version":1,"translation_interval_seconds":' + value +
                                 ',"analysis_interval_seconds":60}')
            with self.subTest(value=value), self.assertRaises(settings.SettingsError):
                settings.load_settings(self.path)

    def test_file_size_limit_is_in_bytes_and_accepts_exact_boundary(self):
        raw = json.dumps(self.valid).encode()
        self.path.write_bytes(raw + b" " * (settings.MAX_SETTINGS_BYTES - len(raw)))
        self.assertEqual(settings.load_settings(self.path), self.valid)
        self.path.write_bytes(self.path.read_bytes() + b" ")
        with self.assertRaisesRegex(settings.SettingsError, "16 KiB"):
            settings.load_settings(self.path)

    def test_bad_json_encoding_and_missing_file_are_errors(self):
        for raw in [b"", b"{} trailing", b"\xff", b"\xef\xbb\xbf{}", b"[" * 2000]:
            self.path.write_bytes(raw)
            with self.subTest(raw=raw[:10]), self.assertRaises(settings.SettingsError):
                settings.load_settings(self.path)
        self.path.unlink()
        with self.assertRaises(settings.SettingsError):
            settings.load_settings(self.path)
        with self.assertRaises(settings.SettingsError):
            settings.load_settings(self.path.parent)

    def test_example_is_valid_but_defaults_remain_unchanged(self):
        self.assertEqual(settings.load_settings(REPO / "config/lecture.example.json"), self.valid)
        self.assertEqual(settings.load_settings()["analysis_interval_seconds"], 120)


@unittest.skipUnless(shutil.which("zsh"), "launcher requires zsh")
class LauncherSettingsTests(unittest.TestCase):
    """Use copied source plus synthetic executable boundaries, never the real app."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lecture settings ")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "checkout with spaces"
        self.repo.mkdir()
        (self.repo / "audio-array").mkdir()
        (self.repo / ".venv/bin").mkdir(parents=True)
        (self.repo / "config").mkdir()
        self.log = self.repo / "calls.jsonl"
        self.env = {**os.environ, "SYNTHETIC_CALLS": str(self.log), "SYNTHETIC_LISTENER": "0"}
        self.env.pop("SYNTHETIC_MUTATE_SETTINGS", None)
        self.python = self.repo / ".venv/bin/python"
        self.python.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
args = sys.argv[1:]
entry = {"kind": "python", "argv": args}
if args[0] == "-":
    entry["preflight"] = True
    sys.stdin.read()
with open(os.environ["SYNTHETIC_CALLS"], "a") as log:
    log.write(json.dumps(entry) + "\\n")
if args[0] == "-" or any(arg.endswith(("lecture_live.py", "lecture_launcher.py")) for arg in args):
    raise SystemExit(0)
if args[0].endswith("lecture_cost_estimate.py") and os.environ.get("SYNTHETIC_MUTATE_SETTINGS"):
    pathlib.Path(os.environ["SYNTHETIC_MUTATE_SETTINGS"]).write_text("invalid after first read")
os.execv(sys.executable, [sys.executable, *args])
''')
        self.python.chmod(0o755)
        probe = self.repo / "synthetic port probe"
        probe.write_text("#!" + sys.executable + "\n" + '''
import json, os
with open(os.environ["SYNTHETIC_CALLS"], "a") as log:
    log.write(json.dumps({"kind": "probe"}) + "\\n")
raise SystemExit(0 if os.environ["SYNTHETIC_LISTENER"] == "1" else 1)
''')
        probe.chmod(0o755)
        launcher = (REPO / "start.command").read_text()
        self.assertEqual(launcher.count("/usr/sbin/lsof"), 1)
        (self.repo / "start.command").write_text(launcher.replace("/usr/sbin/lsof", shlex.quote(str(probe))))
        shutil.copy2(SOURCE / "lecture_settings.py", self.repo / "audio-array/lecture_settings.py")
        # The real estimator has separate arithmetic tests. This fixture checks
        # the launcher's process boundaries and exactly forwarded settings.
        (self.repo / "audio-array/lecture_cost_estimate.py").write_text('''
import argparse, json, math
from lecture_settings import load_settings, validate_settings
p = argparse.ArgumentParser()
p.add_argument("--settings")
p.add_argument("--hours", type=float, default=1)
p.add_argument("--translation-interval", type=int)
p.add_argument("--analysis-interval", type=int)
a = p.parse_args()
if not math.isfinite(a.hours) or not 0 < a.hours <= 24:
    p.error("hours must be finite and in (0, 24]")
if a.translation_interval is None and a.analysis_interval is None:
    value = load_settings(a.settings)
else:
    if a.settings:
        p.error("settings cannot be combined with interval arguments")
    value = validate_settings({"schema_version": 1, "translation_interval_seconds": a.translation_interval,
                               "analysis_interval_seconds": a.analysis_interval})
print("estimate " + json.dumps({**value, "hours": a.hours}))
''')
        self.path = self.repo / "config" / "custom $(not-a-command) settings.json"
        self.path.write_text(json.dumps({"schema_version": 1, "translation_interval_seconds": 30,
                                         "analysis_interval_seconds": 45}))

    def run_launcher(self, *args):
        return subprocess.run([shutil.which("zsh"), str(self.repo / "start.command"), *map(str, args)],
                              env=self.env, cwd=self.temp.name, capture_output=True, text=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_estimate_bypasses_cloud_authorization_preflight_and_existing_app(self):
        self.env["SYNTHETIC_LISTENER"] = "1"
        result = self.run_launcher("--estimate", "--cloud", "--check", "--settings", self.path, "--hours", "2.5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"translation_interval_seconds": 30', result.stdout)
        self.assertIn('"hours": 2.5', result.stdout)
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["argv"], ["audio-array/lecture_cost_estimate.py", "--settings", str(self.path), "--hours", "2.5"])
        self.assertFalse((self.repo / "results").exists())
        self.assertFalse((self.repo / "audio-array/__pycache__").exists())

    def test_estimate_can_run_without_setup_venv(self):
        self.python.unlink()
        result = self.run_launcher("--estimate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"translation_interval_seconds": 60', result.stdout)
        self.assertEqual(self.calls(), [])

    def test_default_start_does_not_read_unrequested_private_json(self):
        (self.repo / "config/lecture.json").write_text("intentionally invalid")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = self.calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("--translation-interval") + 1], "60")
        self.assertEqual(argv[argv.index("--analysis-interval") + 1], "120")
        self.assertIn("翻訳 60 秒 / 整理 120 秒", result.stdout)
        self.assertFalse(any("lecture_cost_estimate.py" in " ".join(call.get("argv", [])) for call in self.calls()))

    def test_cloud_estimate_and_launch_use_one_read_even_if_file_changes(self):
        self.env["SYNTHETIC_MUTATE_SETTINGS"] = str(self.path)
        auth = self.repo / "private authorization.json"
        key = self.repo / "private key.env"
        result = self.run_launcher("--cloud", "--settings", self.path, "--authorization", auth,
                                   "--key-file", key, "--hours", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        settings_calls = [call for call in calls if call.get("argv", [None])[0] == "audio-array/lecture_settings.py"]
        self.assertEqual(len(settings_calls), 1)
        estimate = next(call for call in calls if call.get("argv", [None])[0] == "audio-array/lecture_cost_estimate.py")
        self.assertEqual(estimate["argv"], ["audio-array/lecture_cost_estimate.py", "--hours", "2", "--translation-interval", "30", "--analysis-interval", "45"])
        self.assertLess(calls.index(estimate), next(i for i, call in enumerate(calls) if call.get("preflight")))
        argv = calls[-1]["argv"]
        self.assertEqual(argv[argv.index("--translation-interval") + 1], "30")
        self.assertEqual(argv[argv.index("--analysis-interval") + 1], "45")
        self.assertEqual(argv[argv.index("--cloud-authorization") + 1], str(auth))
        self.assertEqual(argv[argv.index("--key-file") + 1], str(key))
        self.assertIn('"analysis_interval_seconds": 45', result.stdout)
        self.assertEqual(self.path.read_text(), "invalid after first read")

    def test_existing_app_is_preserved_and_new_intervals_are_not_forwarded(self):
        self.env["SYNTHETIC_LISTENER"] = "1"
        result = self.run_launcher("--settings", self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("変更した間隔は既存アプリに反映されません。停止して終了後に再起動", result.stdout)
        calls = self.calls()
        self.assertEqual(calls[-1]["argv"][0], "audio-array/lecture_launcher.py")
        self.assertNotIn("--translation-interval", calls[-1]["argv"])
        self.assertFalse(any(call.get("preflight") for call in calls))

    def test_invalid_settings_stop_before_probe_and_preflight(self):
        self.path.write_text('{"schema_version":1,"translation_interval_seconds":false,"analysis_interval_seconds":60}')
        result = self.run_launcher("--settings", self.path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("integer", result.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0]["argv"][0], "audio-array/lecture_settings.py")

    def test_invalid_estimate_settings_have_no_runtime_side_effects(self):
        self.path.write_text("invalid")
        result = self.run_launcher("--estimate", "--settings", self.path)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0]["argv"][0], "audio-array/lecture_cost_estimate.py")

    def test_invalid_hours_stop_before_probe_and_preflight(self):
        for value in ["0", "-1", "nan", "inf", "25", "bad"]:
            if self.log.exists():
                self.log.unlink()
            with self.subTest(value=value):
                result = self.run_launcher("--cloud", "--authorization", "unused.json", "--hours", value)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([call["kind"] for call in self.calls()], ["python", "python"])
                self.assertFalse(any(call.get("preflight") for call in self.calls()))

    def test_check_runs_preflight_without_launch(self):
        result = self.run_launcher("--check", "--settings", self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.calls()[-1]["preflight"])
        self.assertFalse(any("lecture_live.py" in " ".join(call.get("argv", [])) for call in self.calls()))

    def test_missing_values_and_unknown_flags_fail_before_python(self):
        for args in [("--settings",), ("--hours",), ("--unknown",), ("--hours", "1")]:
            with self.subTest(args=args):
                result = self.run_launcher(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
