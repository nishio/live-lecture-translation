"""Independent lifecycle regressions; synthetic PCM and fake inference only."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_capture
import lecture_live


def synthetic_asr(chunk, *args):
    seconds = chunk['end_seconds'] - chunk['start_seconds']
    return {'chunks': [{'index': 0, 'source_start_seconds': 0,
                        'source_end_seconds': seconds,
                        'raw_result': {'language': 'en', 'segments': [
                            {'start': 0, 'end': seconds,
                             'text': 'A synthetic statement about a new idea.'}]}}]}


def synthetic_analysis(lines, previous, **options):
    item = {'text': '合成テストの要点', 'source_ids': [lines[-1]['id']]}
    return {'headline': item, 'summary': [item], 'flow': [item], 'concepts': [],
            'questions': [], 'translations': [], 'through_seconds': options['through_seconds']}


class LectureLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.wav = self.root / 'synthetic.wav'
        with wave.open(str(self.wav), 'wb') as handle:
            handle.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            handle.writeframes(b'\x01\x00' * 40000)  # 2.5 s, including a final partial chunk.

    def app(self, **options):
        app = lecture_live.LectureApp(data_root=self.root / 'data', results_root=self.root / 'results',
            chunk_seconds=1, transcriber=options.pop('transcriber', synthetic_asr),
            analyzer=options.pop('analyzer', synthetic_analysis), **options)
        self.addCleanup(lambda: app.close(timeout=3))
        return app

    def settle(self, app, timeout=5):
        deadline = time.monotonic() + timeout
        for worker in (app.source_thread, app.worker):
            if worker:
                worker.join(max(0, deadline - time.monotonic()))
                self.assertFalse(worker.is_alive(), 'owned worker did not terminate')

    def test_observation_failure_still_stops_recorder(self):
        class FailingObservation:
            def __init__(self, *args, **kwargs):
                self.stopped = False

            def start(self):
                return self

            def snapshot(self):
                if not self.stopped:
                    raise RuntimeError('synthetic observation failure')
                return {'state': 'stopped', 'stop_confirmed': True, 'callbacks_confirmed': True}

            def stop(self):
                self.stopped = True
                return self.snapshot()

        app = self.app(capture_factory=FailingObservation)
        app.start({'provider': 'off'})
        self.settle(app)
        self.assertTrue(app.recorder.stopped)
        self.assertEqual('failed', app.snapshot()['capture']['state'])
        self.assertIn('observation failure', app.snapshot()['capture']['error'])
        self.assertTrue(app.close(timeout=.1))

    def test_delayed_last_callback_is_recovered_once_and_blocks_restart(self):
        release, delivered = threading.Event(), threading.Event()
        callbacks = []

        class LateCallback:
            def __init__(self, output_dir, **options):
                self.path = Path(output_dir)
                self.callback = options['on_chunk']
                self.thread = None

            def start(self):
                writer = lecture_capture.PCMChunkWriter(self.path, chunk_seconds=1)
                writer.write(b'\x01\x00' * 16000)
                writer.close()
                self.chunk = json.loads((self.path / 'chunks.jsonl').read_text().splitlines()[0])

            def snapshot(self):
                return {'state': 'stopped', 'stop_confirmed': True,
                        'callbacks_confirmed': delivered.is_set(), 'audio_seconds': 1}

            def stop(self):
                if self.thread is None:
                    def callback():
                        release.wait(5)
                        self.callback(self.chunk)
                        delivered.set()
                    self.thread = threading.Thread(target=callback, daemon=True)
                    self.thread.start()
                    callbacks.append(self.thread)
                return self.snapshot()

        def remember(chunk, *args):
            calls.append(chunk['index'])
            return synthetic_asr(chunk, *args)

        calls = []
        app = self.app(capture_factory=LateCallback, transcriber=remember)
        # Release before app cleanup even when an assertion fails.
        self.addCleanup(release.set)
        try:
            app.start({'provider': 'off'})
            self.settle(app)
            self.assertEqual([0], calls)
            self.assertEqual(1, app.snapshot()['asr']['through_seconds'])
            with self.assertRaises(RuntimeError):
                app.start({'provider': 'off'})
            self.assertFalse(app.close(timeout=.01))
        finally:
            release.set()
            for thread in callbacks:
                thread.join(2)
        self.assertTrue(delivered.is_set())
        self.assertTrue(app.audio_queue.empty())
        self.assertEqual([0], calls)
        self.assertTrue(app.close(timeout=.1))

    def test_final_partial_replay_chunk_precedes_source_done(self):
        app = self.app()
        app.start({'provider': 'off'}, replay=self.wav, pace=0)
        self.settle(app)
        ledger = [json.loads(row) for row in (app.session_dir / 'audio/chunks.jsonl').read_text().splitlines()]
        self.assertEqual([16000, 16000, 8000], [item['frames'] for item in ledger])
        self.assertEqual([0, 1, 2], [line['segment'] for line in app.snapshot()['lines']])
        self.assertEqual(2.5, app.snapshot()['asr']['through_seconds'])
        self.assertEqual(80000, (app.session_dir / 'audio/raw.pcm').stat().st_size)
        self.assertTrue(app.source_done.is_set())
        self.assertTrue(app.audio_queue.empty())

    def test_replay_close_error_signals_source_done_and_keeps_raw(self):
        real_writer = lecture_capture.PCMChunkWriter

        class CloseFailure(real_writer):
            def close(self):
                super().close()
                raise OSError('synthetic finalization failure')

        app = self.app()
        with patch.object(lecture_capture, 'PCMChunkWriter', CloseFailure):
            app.start({'provider': 'off'}, replay=self.wav, pace=0)
            self.settle(app)
        self.assertTrue(app.source_done.is_set())
        self.assertEqual('failed', app.snapshot()['capture']['state'])
        self.assertEqual(80000, (app.session_dir / 'audio/raw.pcm').stat().st_size)

    def test_close_is_idempotent_and_rejects_new_work(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(chunk, *args):
            entered.set()
            release.wait(5)
            return synthetic_asr(chunk, *args)

        app = self.app(transcriber=blocked)
        self.addCleanup(release.set)
        app.start({'provider': 'local'}, replay=self.wav, pace=0)
        try:
            self.assertTrue(entered.wait(2))
            app.source_thread.join(2)
            self.assertFalse(app.source_thread.is_alive())
            self.assertFalse(app.close(timeout=.01))
            with self.assertRaises(RuntimeError):
                app.start({'provider': 'off'}, replay=self.wav)
            with self.assertRaises(RuntimeError):
                app.retry_analysis()
        finally:
            release.set()
            self.settle(app)
        self.assertTrue(app.close(timeout=.1))
        self.assertTrue(app.close(timeout=.1))
        self.assertEqual(80000, (app.session_dir / 'audio/raw.pcm').stat().st_size)

    def test_retry_at_worker_finalization_is_explicitly_rejected_then_allowed(self):
        entered, release = threading.Event(), threading.Event()
        app = self.app()
        self.addCleanup(release.set)
        original_persist = app.persist

        def block_final_persist(*args, **kwargs):
            original_persist(*args, **kwargs)
            if (threading.current_thread() is app.worker and app.worker_finishing
                    and not entered.is_set()):
                entered.set()
                release.wait(5)

        app.persist = block_final_persist
        app.start({'provider': 'local'}, replay=self.wav, pace=0)
        try:
            self.assertTrue(entered.wait(3))
            self.assertTrue(app.worker.is_alive())
            with self.assertRaises(RuntimeError):
                app.retry_analysis()
            self.assertFalse(app.retry_event.is_set())
        finally:
            release.set()
            self.settle(app)
        app.retry_analysis()
        self.settle(app)
        self.assertFalse(app.retry_event.is_set())
        self.assertEqual('completed', app.snapshot()['analysis']['state'])

    def test_retry_requested_before_finalization_is_processed(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def wait_for_source(chunk, *args):
            self.assertTrue(app.source_done.wait(3))
            return synthetic_asr(chunk, *args)

        app = self.app(transcriber=wait_for_source)
        self.addCleanup(release.set)
        original_analyze = app._analyze

        def finish_analysis(**options):
            calls.append(1)
            original_analyze(**options)
            if len(calls) == 1:
                entered.set()
                release.wait(5)

        app._analyze = finish_analysis
        app.start({'provider': 'local'}, replay=self.wav, pace=0)
        try:
            self.assertTrue(entered.wait(3))
            app.retry_analysis()
        finally:
            release.set()
            self.settle(app)
        self.assertEqual(2, len(calls))
        self.assertFalse(app.retry_event.is_set())

    def test_ambiguous_local_failure_pauses_inference_but_preserves_recording(self):
        def timeout(*args, **kwargs):
            raise TimeoutError('synthetic local connection timeout')

        app = self.app(analyzer=timeout, analysis_interval=1)
        app.start({'provider': 'local'}, replay=self.wav, pace=1)
        self.settle(app)
        status = app.snapshot()
        self.assertTrue(app.inference_unconfirmed)
        self.assertEqual('paused', status['asr']['state'])
        self.assertEqual('failed', status['analysis']['state'])
        self.assertEqual('completed', status['capture']['state'])
        self.assertEqual(80000, (app.session_dir / 'audio/raw.pcm').stat().st_size)
        self.assertGreater(app.audio_queue.qsize(), 0)
        with self.assertRaises(RuntimeError):
            app.retry_analysis()
        with self.assertRaises(RuntimeError):
            app.start({'provider': 'local'}, replay=self.wav)

    def test_unreachable_ollama_does_not_pause_later_asr(self):
        from event_insights import ModelUnavailableError

        def unreachable(*args, **kwargs):
            error = ModelUnavailableError('synthetic refused connection')
            error.local_inference_finished = True
            raise error

        app = self.app(analyzer=unreachable, analysis_interval=1)
        app.start({'provider': 'local'}, replay=self.wav, pace=1)
        self.settle(app)
        self.assertFalse(app.inference_unconfirmed)
        self.assertEqual('completed', app.snapshot()['asr']['state'])
        self.assertEqual(2.5, app.snapshot()['asr']['through_seconds'])
        self.assertEqual('failed', app.snapshot()['analysis']['state'])

    def test_transcript_write_failure_keeps_chunk_out_of_published_state(self):
        real_append = lecture_live.append_json

        def append(path, value):
            if Path(path).name == 'transcript.jsonl' and value['chunk']['index'] == 1:
                raise OSError('synthetic full disk')
            return real_append(path, value)

        app = self.app(analysis_interval=1000)
        with patch.object(lecture_live, 'append_json', side_effect=append):
            app.start({'provider': 'local'}, replay=self.wav, pace=0)
            self.settle(app)
        status = app.snapshot()
        published = {line['id'][:7] for line in status['lines']}
        self.assertNotIn('c000001', published)
        self.assertIn('c000000', published)
        self.assertEqual([1], [chunk['index'] for chunk in status['asr']['failed_chunks']])
        saved = [json.loads(row)['chunk']['index'] for row in
                 (app.result_dir / 'transcript.jsonl').read_text().splitlines()]
        self.assertNotIn(1, saved)

    def test_known_model_response_failure_does_not_pause_later_asr(self):
        from lecture_analysis import SnapshotResponseError

        def rejected_response(*args, **kwargs):
            raise SnapshotResponseError('synthetic invalid response')

        app = self.app(analyzer=rejected_response, analysis_interval=1)
        app.start({'provider': 'local'}, replay=self.wav, pace=1)
        self.settle(app)
        self.assertFalse(app.inference_unconfirmed)
        self.assertEqual('completed', app.snapshot()['asr']['state'])
        self.assertEqual(2.5, app.snapshot()['asr']['through_seconds'])
        self.assertEqual('failed', app.snapshot()['analysis']['state'])


if __name__ == '__main__':
    unittest.main()
