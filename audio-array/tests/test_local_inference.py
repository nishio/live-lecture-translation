import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_inference import InferenceCancelled, InferenceTimeout, inference_slot, read_inference_state


CHILD = """
import sys
from local_inference import inference_slot
print('started', flush=True)
with inference_slot('child-asr', lock_dir=sys.argv[1], heartbeat_interval=0.05):
    print('acquired', flush=True)
    sys.stdin.readline()
print('released', flush=True)
"""


class LocalInferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name) / "locks"
        self.children = []
        self.pending_output = {}

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream:
                    stream.close()
        self.tmp.cleanup()

    def start_child(self, code=CHILD, *args):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        child = subprocess.Popen([sys.executable, "-u", "-c", code, str(self.directory), *args],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=env, text=True, bufsize=1)
        self.children.append(child)
        return child

    def line(self, child, expected, timeout=3):
        pending = self.pending_output.get(child.pid, b"")
        deadline = time.monotonic() + timeout
        while b"\n" not in pending:
            ready, _, _ = select.select([child.stdout], [], [], max(0, deadline - time.monotonic()))
            self.assertTrue(ready, f"Timed out waiting for {expected}; process={child.poll()}")
            chunk = os.read(child.stdout.fileno(), 4096)
            if not chunk:
                self.fail(f"Child exited before {expected}; stderr={child.stderr.read()}")
            pending += chunk
        actual, _, pending = pending.partition(b"\n")
        self.pending_output[child.pid] = pending
        self.assertEqual(actual.decode().strip(), expected)

    def wait_state(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = read_inference_state(self.directory)
            if predicate(state):
                return state
            time.sleep(0.01)
        self.fail(f"State condition not reached: {state}")

    def test_cross_process_exclusion_and_waiter_report(self):
        with inference_slot("parent-ollama", lock_dir=self.directory):
            child = self.start_child()
            self.line(child, "started")
            state = self.wait_state(lambda s: s["fresh_waiter_reports"] == 1)
            self.assertEqual(state["owner"]["job_label"], "parent-ollama")
            self.assertEqual(state["waiters"][0]["pid"], child.pid)
            self.assertFalse(select.select([child.stdout], [], [], 0.15)[0])
        self.line(child, "acquired")
        child.stdin.write("done\n")
        child.stdin.flush()
        self.line(child, "released")
        self.assertEqual(child.wait(timeout=3), 0)
        self.assertIsNone(read_inference_state(self.directory)["owner"])

    def test_sigkill_releases_kernel_lock_despite_stale_owner_file(self):
        child = self.start_child()
        self.line(child, "started")
        self.line(child, "acquired")
        child.kill()
        child.wait(timeout=3)
        state = read_inference_state(self.directory)
        self.assertEqual(state["owner"]["record_state"], "process_not_alive")
        self.assertEqual(state["kernel_lock_state"], "not_probed")
        with inference_slot("next-job", lock_dir=self.directory, timeout=0.5):
            self.assertEqual(read_inference_state(self.directory)["owner"]["pid"], os.getpid())

    def test_wait_timeout_does_not_steal_or_delete_owner(self):
        child = self.start_child()
        self.line(child, "started")
        self.line(child, "acquired")
        began = time.monotonic()
        with self.assertRaises(InferenceTimeout):
            with inference_slot("blocked", lock_dir=self.directory, timeout=0.15):
                self.fail("Blocked process entered inference")
        self.assertGreaterEqual(time.monotonic() - began, 0.14)
        state = read_inference_state(self.directory)
        self.assertEqual(state["owner"]["pid"], child.pid)
        self.assertEqual(state["waiters"], [])

    def test_zero_timeout_gets_free_slot_and_fails_immediately_when_busy(self):
        with inference_slot("first", lock_dir=self.directory, timeout=0):
            with self.assertRaises(InferenceTimeout):
                with inference_slot("nested", lock_dir=self.directory, timeout=0):
                    self.fail("Lock must not be reentrant")

    def test_wait_cancellation_and_pre_cancelled_free_slot(self):
        cancelled = threading.Event()
        with inference_slot("owner", lock_dir=self.directory):
            timer = threading.Timer(0.05, cancelled.set)
            timer.start()
            try:
                with self.assertRaises(InferenceCancelled):
                    with inference_slot("cancelled", lock_dir=self.directory, cancel=cancelled):
                        self.fail("Cancelled wait entered inference")
            finally:
                timer.join()
        with self.assertRaises(InferenceCancelled):
            with inference_slot("pre-cancelled", lock_dir=self.directory, cancel=cancelled):
                self.fail("Pre-cancelled work entered inference")
        self.assertEqual(read_inference_state(self.directory)["waiters"], [])

    def test_body_failure_and_cooperative_cancellation_release(self):
        cancelled = threading.Event()
        with self.assertRaises(InferenceCancelled):
            with inference_slot("running", lock_dir=self.directory, cancel=cancelled) as lease:
                cancelled.set()
                lease.check_cancelled()
        with inference_slot("after-failure", lock_dir=self.directory, timeout=0):
            pass
        with self.assertRaises(ValueError):
            lease.fileno()

    def test_owner_heartbeat_and_read_only_missing_directory(self):
        self.assertIsNone(read_inference_state(self.directory)["owner"])
        self.assertFalse(self.directory.exists())
        with inference_slot("heartbeat", lock_dir=self.directory, heartbeat_interval=0.03):
            original = read_inference_state(self.directory)["owner"]["updated_at"]
            state = self.wait_state(lambda s: s["owner"]["updated_at"] != original)
            self.assertEqual(state["owner"]["record_state"], "fresh_report")

    def test_live_pid_stale_record_is_not_current_and_unknown_fields_not_echoed(self):
        self.directory.mkdir()
        path = self.directory / "owner.json"
        value = {"pid": os.getpid(), "updated_unix": time.time() - 60,
                 "job_label": "old-job", "transcript": "must not be returned"}
        path.write_text(json.dumps(value))
        before = path.read_bytes()
        record = read_inference_state(self.directory)["owner"]
        self.assertTrue(record["pid_alive"])
        self.assertEqual(record["record_state"], "stale")
        self.assertNotIn("transcript", record)
        self.assertEqual(path.read_bytes(), before)

    def test_corrupt_record_and_future_timestamp_are_unconfirmed(self):
        self.directory.mkdir()
        path = self.directory / "owner.json"
        path.write_text("{broken")
        self.assertEqual(read_inference_state(self.directory)["owner"]["record_state"], "unreadable")
        path.write_text(json.dumps({"pid": os.getpid(), "updated_unix": time.time() + 100}))
        self.assertEqual(read_inference_state(self.directory)["owner"]["record_state"], "unknown_timestamp")

    def test_explicit_fd_inheritance_retains_lock_after_parent_death(self):
        # Child worker holds only the inherited FD. Killing its parent must not
        # permit another inference job to overlap that still-running worker.
        worker_pid_file = Path(self.tmp.name) / "worker.pid"
        worker_stop = Path(self.tmp.name) / "worker.stop"
        code = r"""
import os, subprocess, sys
from local_inference import inference_slot
with inference_slot('parent-worker', lock_dir=sys.argv[1]) as lease:
    worker_code = 'import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid()));\nwhile not Path(sys.argv[2]).exists(): time.sleep(0.02)'
    worker = subprocess.Popen([sys.executable, '-c', worker_code, sys.argv[2], sys.argv[3]], pass_fds=(lease.fileno(),), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('acquired', flush=True)
    worker.wait()
"""
        parent = self.start_child(code, str(worker_pid_file), str(worker_stop))
        worker_pid = None
        worker_released = False
        try:
            self.line(parent, "acquired")
            deadline = time.monotonic() + 3
            while not worker_pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            worker_pid = int(worker_pid_file.read_text())
            parent.kill()
            parent.wait(timeout=3)
            with self.assertRaises(InferenceTimeout):
                with inference_slot("must-wait", lock_dir=self.directory, timeout=0.1):
                    self.fail("Inherited child lock was released early")
            worker_stop.touch()
            with inference_slot("after-worker", lock_dir=self.directory, timeout=3):
                worker_released = True
        finally:
            worker_stop.touch()
            if worker_pid is not None and not worker_released:
                try:
                    os.kill(worker_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_labels_and_time_settings_reject_invalid_input(self):
        for kwargs in ({"job_label": "free form audio text"}, {"job_label": "asr", "timeout": -1},
                       {"job_label": "asr", "timeout": float("nan")},
                       {"job_label": "asr", "poll_interval": 0}):
            with self.assertRaises(ValueError):
                with inference_slot(lock_dir=self.directory, **kwargs):
                    pass


if __name__ == "__main__":
    unittest.main()
