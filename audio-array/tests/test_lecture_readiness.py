import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_readiness as ready


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / 'model'
        self.model.mkdir()
        (self.model / 'config.json').write_text('{}')
        (self.model / 'weights.npz').write_bytes(b'fixture')
        self.metadata = self.root / 'metadata.json'
        self.metadata.write_text(json.dumps({'local_path': str(self.model)}))

    def run_check(self, network=True, cloud=False):
        scope = Mock()
        scope.status.return_value = {'authorized_today': True, 'remaining_seconds': 21600}
        service = ready.LectureReadiness(data_root=self.root, model_metadata=self.metadata,
            allow_cloud=cloud, cloud_scope=scope, network_probe=lambda: network)
        with patch('importlib.util.find_spec', return_value=True), patch('lecture_capture.ensure_native_helper'), \
                patch('event_insights_cloud.has_api_key', return_value=True), \
                patch('event_insights_cloud.budget_status', return_value={'spent_usd': 0, 'budget_usd': 10}):
            service._run()
        return service

    def test_offline_is_warning_and_recording_can_start(self):
        service = self.run_check(network=False, cloud=True)
        self.assertEqual('ready', service.snapshot()['state'])
        network = next(x for x in service.snapshot()['checks'] if x['id'] == 'network')
        self.assertEqual('warning', network['state'])
        service.require_recording_ready()

    def test_missing_model_blocks_recording_before_start(self):
        (self.model / 'weights.npz').unlink()
        service = self.run_check()
        self.assertEqual('blocked', service.snapshot()['state'])
        with self.assertRaises(ValueError):
            service.require_recording_ready()

    def test_space_is_rechecked_after_readiness_cached_success(self):
        service = self.run_check()
        with patch.object(ready.shutil, 'disk_usage', return_value=Mock(free=1024)):
            with self.assertRaisesRegex(ValueError, '2 GB'):
                service.require_recording_ready()

    def test_cloud_probe_sends_no_request_and_creates_no_ledger(self):
        before = set(self.root.rglob('*'))
        with patch('event_insights_cloud._post', side_effect=AssertionError('API must not be called')):
            service = self.run_check(network=True, cloud=True)
        self.assertEqual('ready', service.snapshot()['state'])
        self.assertEqual(before, set(self.root.rglob('*')))

    def test_snapshot_copy_cannot_mutate_state_and_close_stops_refresh(self):
        service = self.run_check()
        snapshot = service.snapshot()
        snapshot['checks'].clear()
        self.assertTrue(service.snapshot()['checks'])
        service.close()
        service.refresh()
        self.assertIsNone(service.thread)

    def test_hung_dns_does_not_block_local_start_or_accumulate_probe_threads(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def hung():
            calls.append(1)
            entered.set()
            release.wait(5)
            return True
        service = ready.LectureReadiness(data_root=self.root, model_metadata=self.metadata,
            allow_cloud=True, cloud_scope=Mock(), network_probe=hung, network_timeout=.03)
        with patch('importlib.util.find_spec', return_value=True), patch('lecture_capture.ensure_native_helper'), \
                patch('event_insights_cloud.has_api_key', return_value=True), \
                patch('event_insights_cloud.budget_status', return_value={'spent_usd': 0, 'budget_usd': 10}):
            service.cloud_scope.status.return_value = {'authorized_today': True, 'remaining_seconds': 21600}
            try:
                service.refresh()
                self.assertTrue(entered.wait(1))
                service.require_recording_ready()
                service.thread.join(1)
                self.assertFalse(service.thread.is_alive())
                self.assertEqual('ready', service.snapshot()['state'])
                self.assertTrue(service.network_thread.is_alive())
                service._run()
                self.assertEqual(1, len(calls))
            finally:
                release.set()
                service.network_thread.join(1)
                service.close()


if __name__ == '__main__':
    unittest.main()
