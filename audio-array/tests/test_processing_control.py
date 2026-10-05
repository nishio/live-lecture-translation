from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from processing_control import (ProcessingStopped, check_processing_allowed,
                                current_cancel_event, processing_scope)


class ProcessingControlTest(unittest.TestCase):
    def test_nested_scopes_restore_session_even_after_exception(self):
        stopped, active = threading.Event(), threading.Event()
        stopped.set()
        with processing_scope(stopped):
            with self.assertRaises(ProcessingStopped) as caught:
                check_processing_allowed()
            self.assertTrue(caught.exception.local_inference_finished)
            with self.assertRaises(ValueError):
                with processing_scope(active):
                    check_processing_allowed()
                    raise ValueError("synthetic")
            self.assertIs(stopped, current_cancel_event())
        self.assertIsNone(current_cancel_event())
        check_processing_allowed()

    def test_new_session_does_not_replace_older_workers_event(self):
        old, new = threading.Event(), threading.Event()
        bound, inspect = threading.Event(), threading.Event()
        outcome = []

        def worker():
            with processing_scope(old):
                bound.set()
                if not inspect.wait(2):
                    outcome.append("timeout")
                    return
                try:
                    check_processing_allowed()
                except ProcessingStopped:
                    outcome.append("stopped")
                else:
                    outcome.append("admitted")

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(bound.wait(2))
            old.set()
            with processing_scope(new):
                check_processing_allowed()
                inspect.set()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertIs(new, current_cancel_event())
            self.assertEqual(["stopped"], outcome)
        finally:
            inspect.set()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
