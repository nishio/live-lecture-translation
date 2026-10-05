"""CPU-only preview windows, dispatch and provenance tests."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_live as live
from lecture_provisional import RATE, pending_window, validate_settings, write_window
from test_lecture_live import fake_asr


class ProvisionalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / 'raw.pcm'
        self.raw.write_bytes(b'\x01\x00' * RATE * 40)

    def prepared_app(self, transcriber=None):
        app = live.LectureApp(data_root=self.root / 'data', results_root=self.root / 'results',
            provisional_refresh_seconds=3, provisional_window_seconds=15)
        app.session_dir, app.result_dir = self.root / 'session', self.root / 'result'
        (app.session_dir / 'audio').mkdir(parents=True)
        app.result_dir.mkdir()
        (app.session_dir / 'audio/raw.pcm').write_bytes(self.raw.read_bytes())
        app.state['session'] = {'id': 'synthetic-preview', 'language': 'en', 'started_at': time.time()}
        app.state['analysis']['provider'] = 'off'
        app.state['asr']['state'] = 'waiting'
        app.state['provisional_asr'].update(enabled=True, state='waiting', refresh_seconds=3, window_seconds=15)
        app.transcriber = transcriber or Mock(side_effect=fake_asr)
        return app

    def capture(self, app, seconds):
        app._capture_status({'state': 'recording', 'audio_seconds': seconds, 'last_audio_at': time.time()})

    def wait_until(self, predicate, seconds=4):
        end = time.monotonic() + seconds
        while not predicate() and time.monotonic() < end:
            time.sleep(.01)
        self.assertTrue(predicate())

    def wave_file(self, seconds=3):
        path = self.root / 'source.wav'
        with wave.open(str(path), 'wb') as out:
            out.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            out.writeframes(b'\x01\x00' * RATE * seconds)
        return path

    def test_window_startup_trailing_bounds_and_final_partial(self):
        ends = [3, 6, 9, 12, 15, 18]
        previous = 0
        for end in ends:
            window = pending_window(end + .4, previous, 3, 15)
            self.assertEqual(end, window['through_seconds'])
            self.assertEqual(max(0, end - 15), window['window_start_seconds'])
            previous = window['end_frame']
        self.assertIsNone(pending_window(18.4, previous, 3, 15))
        self.assertEqual(18.4, pending_window(18.4, previous, 3, 15, final=True)['through_seconds'])
        self.assertIsNone(pending_window(30, previous, 0, 15))

    def test_settings_reject_invalid_before_work(self):
        for refresh, window in ((-1, 15), (.01, 15), (3, 2), (3, 31), (True, 15), (3, float('nan'))):
            with self.subTest(refresh=refresh, window=window), self.assertRaises(ValueError):
                validate_settings(refresh, window)
        for refresh, window in ((0, 15), (.1, .1), (3, 15), (30, 30)):
            validate_settings(refresh, window)

    def test_copy_reads_only_confirmed_frames_and_rejects_short_storage(self):
        window = {**pending_window(18, 0, 3, 15), 'revision': 1, 'ready_at': time.time()}
        output = self.root / 'preview.wav'
        chunk = write_window(self.raw, output, window)
        with wave.open(str(output), 'rb') as source:
            self.assertEqual(15 * RATE, source.getnframes())
        self.assertEqual((3, 18), (chunk['start_seconds'], chunk['end_seconds']))
        short = self.root / 'short.pcm'
        short.write_bytes(b'\x01\x00' * RATE)
        with self.assertRaises(ValueError):
            write_window(short, self.root / 'missing.wav', window)
        self.assertFalse((self.root / 'missing.wav').exists())

    def test_pending_coalesces_and_never_enters_canonical_evidence(self):
        app = self.prepared_app()
        before = time.time()
        for seconds in (3, 6, 9, 18):
            self.capture(app, seconds)
        self.assertEqual(4, app._provisional_pending['revision'])
        self.assertTrue(app._process_provisional())
        self.assertEqual(1, app.transcriber.call_count)
        chunk = app.transcriber.call_args.args[0]
        self.assertEqual((3, 18), (chunk['start_seconds'], chunk['end_seconds']))
        state = app.snapshot()
        self.assertEqual([], state['lines'])
        self.assertEqual(0, state['asr']['through_seconds'])
        self.assertTrue(all(row['id'].startswith('p000004-') for row in state['provisional_asr']['lines']))
        self.assertFalse((app.result_dir / 'transcript.jsonl').exists())
        history = [json.loads(row) for row in (app.result_dir / 'provisional-history.jsonl').read_text().splitlines()]
        self.assertEqual(1, len(history))
        event = history[0]
        self.assertLessEqual(before, event['ready_at'])
        self.assertLessEqual(event['ready_at'], event['started_at'])
        self.assertLessEqual(event['started_at'], event['published_at'])
        self.assertLessEqual(event['published_at'], time.time())
        self.assertEqual('provisional_asr', json.loads((app.result_dir / 'measurements.jsonl').read_text())['stage'])

    def test_publication_time_includes_wait_for_publication_lock(self):
        entered, release = threading.Event(), threading.Event()
        decoded, attempting_publish = threading.Event(), threading.Event()
        def transcribe(chunk, *args):
            entered.set()
            self.assertTrue(release.wait(3))
            report = fake_asr(chunk, *args)
            decoded.set()
            return report
        app = self.prepared_app(transcriber=transcribe)
        self.capture(app, 3)
        class ObservedLock:
            def __init__(self):
                self.lock = threading.RLock()
            def __enter__(self):
                if threading.current_thread() is worker and decoded.is_set():
                    attempting_publish.set()
                self.lock.acquire()
                return self
            def __exit__(self, *args):
                self.lock.release()
        app.lock = ObservedLock()
        wall = [time.time() + 1]
        worker = threading.Thread(target=app._process_provisional)
        with patch.object(live.time, 'time', side_effect=lambda: wall[0]):
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                with app.lock:
                    release.set()
                    self.assertTrue(attempting_publish.wait(3))
                    wall[0] += 10
                    lock_released_at = wall[0]
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        event = json.loads((app.result_dir / 'provisional-history.jsonl').read_text())
        self.assertEqual(lock_released_at, event['published_at'])
        self.assertLess(event['started_at'], event['published_at'])
        self.assertEqual(event['published_at'], app.state['provisional_asr']['published_at'])

    def test_failure_retains_last_published_snapshot_then_next_update_recovers(self):
        app = self.prepared_app()
        self.capture(app, 3)
        app._process_provisional()
        old = app.snapshot()['provisional_asr']
        self.capture(app, 6)
        app.transcriber = Mock(side_effect=ValueError('synthetic preview failure'))
        app._process_provisional()
        failed = app.snapshot()['provisional_asr']
        self.assertEqual('failed', failed['state'])
        self.assertEqual(old['lines'], failed['lines'])
        self.assertEqual(old['published_at'], failed['published_at'])
        self.assertEqual([], app.state['asr']['failed_chunks'])
        event = json.loads((app.result_dir / 'provisional-events.jsonl').read_text())
        self.assertEqual(('failed', 2), (event['event'], event['revision']))
        self.capture(app, 9)
        app.transcriber = Mock(side_effect=fake_asr)
        app._process_provisional()
        self.assertEqual('completed', app.state['provisional_asr']['state'])
        self.assertIsNone(app.state['provisional_asr']['error'])
        self.assertEqual(3, app.state['provisional_asr']['revision'])

    def test_canonical_fifo_has_priority_and_eof_drops_covered_preview(self):
        app = self.prepared_app()
        self.capture(app, 18)
        for index, start, end in ((0, 0, 15), (1, 15, 18)):
            app.audio_queue.put({'index': index, 'start_seconds': start, 'end_seconds': end,
                                 'completed_at': time.time()})
        self.assertFalse(app._process_provisional())
        app.transcriber.assert_not_called()
        app.source_done.set()
        app._process()
        self.assertEqual(2, app.transcriber.call_count)
        self.assertTrue(all(not call.args[0].get('provisional') for call in app.transcriber.call_args_list))
        self.assertEqual(18, app.state['asr']['through_seconds'])
        self.assertIsNone(app._provisional_pending)
        self.assertEqual('completed', app.snapshot()['provisional_asr']['state'])
        self.assertEqual([], app.snapshot()['provisional_asr']['lines'])

    def test_preview_failure_does_not_fail_canonical_asr_but_remains_in_summary(self):
        def transcribe(chunk, *args):
            if chunk.get('provisional'):
                raise ValueError('synthetic preview failure')
            return fake_asr(chunk, *args)
        app = self.prepared_app(transcriber=transcribe)
        self.capture(app, 3)
        app._process_provisional()
        app.audio_queue.put({'index': 0, 'start_seconds': 0, 'end_seconds': 3,
                             'completed_at': time.time()})
        app.source_done.set()
        app._process()
        self.assertEqual('completed', app.state['asr']['state'])
        self.assertEqual('failed', app.state['provisional_asr']['state'])
        self.assertIn('一部に失敗', app.state['message'])
        self.assertEqual([], app.state['asr']['failed_chunks'])

    def test_stop_before_dispatch_and_after_persist_do_not_infer(self):
        app = self.prepared_app()
        self.capture(app, 3)
        def stop_during_preparation(*args, **kwargs):
            app.abort_processing.set()
        with patch.object(app, 'persist', side_effect=stop_during_preparation):
            app._process_provisional()
        app.transcriber.assert_not_called()
        self.assertEqual('paused', app.state['provisional_asr']['state'])
        self.assertEqual('cancelled_before_dispatch', json.loads(
            (app.result_dir / 'provisional-events.jsonl').read_text())['event'])
        self.capture(app, 6)
        self.assertFalse(app._process_provisional())

    def test_paced_replay_records_real_preview_arrivals_and_canonical_eof(self):
        audio = self.wave_file()
        app = live.LectureApp(data_root=self.root / 'data', results_root=self.root / 'results',
            chunk_seconds=1, transcriber=fake_asr, provisional_refresh_seconds=.5,
            provisional_window_seconds=2)
        app.start({'provider': 'off'}, replay=audio, pace=1)
        self.addCleanup(lambda: app.close(3))
        app.source_thread.join(5)
        app.worker.join(5)
        self.assertFalse(app.worker.is_alive())
        self.assertEqual('completed', app.state['asr']['state'])
        self.assertEqual(3, app.state['asr']['through_seconds'])
        self.assertEqual([], app.state['provisional_asr']['lines'])
        self.assertTrue(all(row['id'].startswith('c') for row in app.state['lines']))
        events = [json.loads(row) for row in (app.result_dir / 'provisional-history.jsonl').read_text().splitlines()]
        self.assertGreater(len(events), 0)
        self.assertTrue(all(event['published_at'] - app.state['session']['started_at'] >= event['through_seconds'] for event in events))
        config = json.loads((app.result_dir / 'runtime-manifest.json').read_text())
        self.assertEqual(.5, config['configuration']['provisional_refresh_seconds'])
        self.assertIn('audio-array/lecture_provisional.py', config['source_sha256'])
        self.assertTrue(app.close(.1))
        self.assertEqual('completed', app.snapshot()['provisional_asr']['state'])

    def test_stop_during_preview_saves_admitted_result_but_does_not_drain_tail(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def blocked(chunk, *args):
            calls.append(chunk)
            entered.set()
            self.assertTrue(release.wait(5))
            return fake_asr(chunk, *args)
        app = live.LectureApp(data_root=self.root / 'data', results_root=self.root / 'results',
            chunk_seconds=1, transcriber=blocked, provisional_refresh_seconds=.5,
            provisional_window_seconds=2)
        app.start({'provider': 'off'}, replay=self.wave_file(), pace=1)
        try:
            self.assertTrue(entered.wait(3))
            self.assertTrue(calls[0].get('provisional'))
            app.stop()
            state = app.snapshot()
            self.assertEqual('running', state['provisional_asr']['state'])
            self.assertEqual('stopping', state['processing_stop_status'])
            self.assertTrue(state['processing_active'])
            with self.assertRaises(RuntimeError):
                app.start({'provider': 'off'}, replay=self.wave_file(), pace=0)
        finally:
            release.set()
            app.source_thread.join(3)
            app.worker.join(3)
        self.assertFalse(app.worker.is_alive())
        self.assertEqual(1, len(calls))
        self.assertEqual(0, app.state['asr']['through_seconds'])
        self.assertGreater(app.audio_queue.qsize(), 0)
        self.assertTrue((app.result_dir / 'provisional-history.jsonl').exists())
        self.assertEqual('paused', app.state['provisional_asr']['state'])
        self.assertEqual('stopped', app.snapshot()['processing_stop_status'])


if __name__ == '__main__':
    unittest.main()
