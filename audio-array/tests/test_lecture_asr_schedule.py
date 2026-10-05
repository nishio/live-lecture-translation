"""CPU-only schedule observations; no capture or model is started."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_live as live


class AsrScheduleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = live.LectureApp(chunk_seconds=15)
        self.app.result_dir = Path(self.temp.name)
        self.app.state['session'] = {'id': 'synthetic'}
        self.app.state['capture'].update(state='recording', audio_seconds=4,
                                         last_audio_at=100)
        self.app.state['asr']['state'] = 'waiting'
        self.clock = patch.object(live.time, 'time', return_value=100)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def schedule(self):
        return self.app.snapshot()['asr']['schedule']

    def test_wait_uses_saved_audio_progress_and_configured_chunk_length(self):
        schedule = self.schedule()
        self.assertEqual(('waiting', 'recording', 'audio'),
                         (schedule['state'], schedule['reason'], schedule['clock']))
        self.assertEqual(11, schedule['remaining_seconds'])
        self.assertEqual(15, schedule['interval_seconds'])
        self.assertEqual(15, schedule['wait_seconds'])
        self.assertIsNone(schedule['due_at'])
        self.app.state['capture']['audio_seconds'] = 19
        self.app.state['asr']['through_seconds'] = 15
        self.assertEqual(11, self.schedule()['remaining_seconds'])
        self.app.chunk_seconds = 7.5
        self.assertEqual(3.5, self.schedule()['remaining_seconds'])

    def test_wall_clock_passage_does_not_invent_received_audio(self):
        with patch.object(live.time, 'time', return_value=105):
            self.assertEqual(11, self.schedule()['remaining_seconds'])
        with patch.object(live.time, 'time', return_value=113):
            schedule = self.schedule()
        self.assertEqual(('blocked', 'unknown', None),
                         (schedule['state'], schedule['reason'], schedule['remaining_seconds']))

    def test_no_session_or_first_audio_does_not_start_a_countdown(self):
        self.app.state['session'] = None
        self.assertEqual('idle', self.schedule()['state'])
        self.app.state['session'] = {'id': 'synthetic'}
        self.app.state['capture'].update(state='starting', audio_seconds=0,
                                         last_audio_at=None)
        self.assertEqual('no_source', self.schedule()['reason'])
        self.assertIsNone(self.schedule()['remaining_seconds'])

    def test_busy_asr_and_queued_audio_do_not_offer_completion_estimates(self):
        self.app.state['asr']['state'] = 'running'
        self.assertEqual(('busy', 'request', None), tuple(self.schedule()[key]
                         for key in ('state', 'reason', 'remaining_seconds')))
        self.app.state['asr']['state'] = 'waiting'
        self.app.audio_queue.put({'index': 0})
        self.assertEqual(('busy', 'queued', None), tuple(self.schedule()[key]
                         for key in ('state', 'reason', 'remaining_seconds')))

    def test_completed_chunk_awaiting_callback_does_not_reset_circle(self):
        self.app.state['capture']['audio_seconds'] = 15
        self.assertEqual('queued', self.schedule()['reason'])
        self.app.state['asr']['through_seconds'] = 15
        self.assertEqual(15, self.schedule()['remaining_seconds'])

    def test_stalled_stopping_failed_and_unknown_input_have_no_countdown(self):
        for capture_state, reason in [('stalled', 'stalled'), ('stopping', 'stopping'),
                                      ('completed', 'stopping'), ('failed', 'failed'),
                                      ('unknown', 'unknown')]:
            with self.subTest(capture_state=capture_state):
                self.app.state['capture']['state'] = capture_state
                schedule = self.schedule()
                self.assertEqual(('blocked', reason, None), tuple(schedule[key]
                                 for key in ('state', 'reason', 'remaining_seconds')))
        self.app.state['capture'].update(state='recording', last_audio_at=None)
        self.assertEqual('unknown', self.schedule()['reason'])

    def test_saved_and_closed_views_do_not_animate(self):
        self.app.result_dir = None
        self.assertEqual('saved_view', self.schedule()['reason'])
        self.app.result_dir = Path(self.temp.name)
        self.app.abort_processing.set()
        self.assertEqual(('blocked', 'closed', None), tuple(self.schedule()[key]
                         for key in ('state', 'reason', 'remaining_seconds')))

    def test_completion_requires_confirmed_asr_and_source_end(self):
        self.app.source_done.set()
        self.assertEqual(('idle', 'finalizing'), tuple(self.schedule()[key]
                         for key in ('state', 'reason')))
        self.app.state['asr']['state'] = 'completed'
        self.assertEqual('complete', self.schedule()['state'])
        self.app.inference_unconfirmed = True
        self.assertEqual('blocked', self.schedule()['state'])
        self.app.inference_unconfirmed = False
        self.app.source_done.clear()
        self.assertNotEqual('complete', self.schedule()['state'])

    def test_persisted_failures_never_become_success(self):
        self.app.source_done.set()
        self.app.state['asr']['state'] = 'completed'
        self.app.state['asr']['failed_chunks'] = [{'index': 0}]
        self.assertEqual(('blocked', 'failed'), tuple(self.schedule()[key]
                         for key in ('state', 'reason')))
        self.app.state['asr']['failed_chunks'] = []
        self.app.state['asr']['error'] = 'Synthetic unresolved failure.'
        self.assertEqual('failed', self.schedule()['reason'])


if __name__ == '__main__':
    unittest.main()
