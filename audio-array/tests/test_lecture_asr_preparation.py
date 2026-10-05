"""Synthetic startup preparation tests; never load a real model or capture audio."""
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_live as live
from processing_control import ProcessingStopped, processing_scope
from test_lecture_live import fake_asr


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def app(self, preparer):
        app = live.LectureApp(data_root=self.root / 'data', results_root=self.root / 'results',
            transcriber=fake_asr, asr_preparer=preparer, default_language='en')
        self.addCleanup(app.close, .1)
        return app

    def finish_preparation(self, app):
        app.preparation_thread.join(3)
        self.assertFalse(app.preparation_thread.is_alive())
        return app.snapshot()['asr_preparation']

    def test_async_preparation_gates_capture_and_never_starts_it(self):
        entered, release = threading.Event(), threading.Event()
        def prepare(language):
            self.assertEqual('en', language)
            entered.set()
            self.assertTrue(release.wait(3))
            return {'retained_source_lines': 0}
        app = self.app(prepare)
        self.assertEqual('idle', app.snapshot()['asr_preparation']['state'])
        self.assertFalse(entered.is_set())
        before = time.monotonic()
        state = app.prepare_asr()
        try:
            self.assertLess(time.monotonic() - before, 1)
            self.assertTrue(entered.wait(1))
            self.assertEqual('preparing', state['asr_preparation']['state'])
            self.assertTrue(state['processing_active'])
            self.assertIsNone(state['session'])
            self.assertEqual('idle', state['capture']['state'])
            self.assertIsNone(app.source_thread)
            self.assertFalse((self.root / 'data').exists())
            with self.assertRaisesRegex(ValueError, '準備'):
                app.start({'provider': 'off'})
        finally:
            release.set()
        state = self.finish_preparation(app)
        self.assertEqual('ready', state['state'])
        self.assertEqual('en', state['language'])
        self.assertEqual(0, state['details']['retained_source_lines'])
        self.assertIsNone(app.source_thread)
        self.assertEqual([], app.snapshot()['lines'])
        saved = json.loads(Path(state['preparation_path']).read_text())
        self.assertEqual('ready', saved['state'])
        self.assertGreaterEqual(saved['completed_at'], saved['started_at'])

    def test_stop_is_responsive_and_preserves_admitted_preparation_as_paused(self):
        entered, release = threading.Event(), threading.Event()
        def prepare(_language):
            entered.set()
            self.assertTrue(release.wait(3))
        app = self.app(prepare)
        app.prepare_asr()
        try:
            self.assertTrue(entered.wait(1))
            before = time.monotonic()
            state = app.stop()
            self.assertLess(time.monotonic() - before, 1)
            self.assertTrue(state['asr_preparation']['stop_requested'])
            self.assertEqual('preparing', state['asr_preparation']['state'])
            self.assertTrue(state['processing_active'])
            self.assertIsNone(state['session'])
            with self.assertRaises(ValueError):
                app.start({'provider': 'off'})
        finally:
            release.set()
        preparation = self.finish_preparation(app)
        self.assertEqual('paused', preparation['state'])
        self.assertFalse(app.snapshot()['processing_active'])
        self.assertEqual('paused', json.loads(Path(preparation['preparation_path']).read_text())['state'])
        self.assertIsNone(app.source_thread)

    def test_late_stop_after_preparation_thread_exits_prevents_source_admission(self):
        app = self.app(lambda _language: {})
        app.prepare_asr()
        self.assertEqual('ready', self.finish_preparation(app)['state'])
        state = app.stop()
        self.assertEqual('paused', state['asr_preparation']['state'])
        self.assertTrue(state['asr_preparation']['stop_requested'])
        with self.assertRaises(ValueError):
            app.start({'provider': 'off'})
        saved = json.loads(Path(state['asr_preparation']['preparation_path']).read_text())
        self.assertEqual('paused', saved['state'])
        self.assertIsNone(app.source_thread)

    def test_stop_in_ready_thread_exit_window_is_persisted(self):
        app = self.app(lambda _language: {})
        app.prepare_asr()
        self.finish_preparation(app)
        original_thread = app.preparation_thread
        try:
            app.preparation_thread = Mock()
            app.preparation_thread.is_alive.return_value = True
            state = app.stop()
            self.assertEqual('paused', state['asr_preparation']['state'])
            self.assertTrue(state['asr_preparation']['stop_requested'])
            app.preparation_thread.is_alive.return_value = False
            with self.assertRaises(ValueError):
                app.start({'provider': 'off'})
        finally:
            app.preparation_thread = original_thread

    def test_failure_can_retry_without_starting_audio(self):
        preparer = Mock(side_effect=[ValueError('synthetic initialization failure'), {'retained_source_lines': 0}])
        app = self.app(preparer)
        app.prepare_asr()
        failed = self.finish_preparation(app)
        self.assertEqual('failed', failed['state'])
        self.assertIn('synthetic initialization failure', failed['error'])
        with self.assertRaises(ValueError):
            app.start({'provider': 'off'})
        app.prepare_asr()
        ready = self.finish_preparation(app)
        self.assertEqual('ready', ready['state'])
        self.assertIsNone(ready['error'])
        self.assertNotEqual(failed['preparation_path'], ready['preparation_path'])
        self.assertEqual('failed', json.loads(Path(failed['preparation_path']).read_text())['state'])
        self.assertEqual(2, preparer.call_count)
        self.assertIsNone(app.source_thread)

    def test_close_reports_unfinished_preparation_and_prevents_retry(self):
        entered, release = threading.Event(), threading.Event()
        def prepare(_language):
            entered.set()
            self.assertTrue(release.wait(3))
        app = self.app(prepare)
        app.prepare_asr()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(app.close(.01))
            self.assertTrue(app.preparation_cancel.is_set())
            self.assertTrue(app.snapshot()['processing_active'])
            with self.assertRaises(RuntimeError):
                app.prepare_asr()
        finally:
            release.set()
        self.assertEqual('paused', self.finish_preparation(app)['state'])
        self.assertTrue(app.close(.1))

    def test_session_reuses_transcriber_and_clock_starts_after_preparation(self):
        preparer = Mock(return_value={'retained_source_lines': 0})
        app = self.app(preparer)
        transcriber = app.transcriber
        app.prepare_asr()
        preparation = self.finish_preparation(app)
        app.prepare_asr()
        self.assertEqual(1, preparer.call_count)
        audio = self.root / 'synthetic.wav'
        with wave.open(str(audio), 'wb') as out:
            out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            out.writeframes(b'\x01\x00' * 1600)
        app.start({'provider': 'off', 'language': 'ja'}, replay=audio, pace=0)
        app.source_thread.join(3)
        app.worker.join(3)
        self.assertFalse(app.worker.is_alive())
        self.assertIs(app.transcriber, transcriber)
        state = app.snapshot()
        self.assertGreaterEqual(state['session']['started_at'], preparation['completed_at'])
        self.assertEqual('ja', state['session']['language'])
        self.assertEqual('en', state['asr_preparation']['language'])
        saved = json.loads((app.result_dir / 'asr-preparation.json').read_text())
        self.assertEqual(preparation['completed_at'], saved['completed_at'])
        self.assertEqual('completed', state['asr']['state'])

    def test_saved_result_view_never_starts_preparation_or_rewrites_it(self):
        preparer = Mock()
        app = self.app(preparer)
        app.state['session'] = {'id': 'synthetic-archive'}
        app.state['asr_preparation'] = {'state': 'ready', 'completed_at': 100}
        with self.assertRaises(ValueError):
            app.prepare_asr()
        self.assertEqual({'state': 'ready', 'completed_at': 100}, app.stop()['asr_preparation'])
        preparer.assert_not_called()
        self.assertIsNone(app.preparation_thread)

    def test_prepare_http_returns_before_work_and_stop_remains_available(self):
        entered, release = threading.Event(), threading.Event()
        def prepare(_language):
            entered.set()
            self.assertTrue(release.wait(3))
        app = self.app(prepare)
        server, url = live.make_server(app, port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        port = server.server_port
        token = parse_qs(urlsplit(url).query)['token'][0]
        def post(path):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
            try:
                connection.request('POST', path, '{}', headers={
                    'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}',
                    'Cookie': f'lecture_session_{port}={token}'})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        try:
            code, state = post('/api/prepare-asr')
            self.assertEqual(200, code)
            self.assertEqual('preparing', state['asr_preparation']['state'])
            self.assertTrue(entered.wait(1))
            code, state = post('/api/stop')
            self.assertEqual(200, code)
            self.assertTrue(state['asr_preparation']['stop_requested'])
            self.assertIsNone(state['session'])
        finally:
            release.set()
            server.shutdown()
            thread.join(2)
            server.server_close()
        self.assertEqual('paused', self.finish_preparation(app)['state'])


class LocalPreparationTest(unittest.TestCase):
    def test_failed_model_identity_initialization_is_atomic_and_retryable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / 'metadata.json'
            metadata.write_text(json.dumps({'local_path': str(root)}))
            (root / 'weights.safetensors').write_bytes(b'synthetic weights')
            (root / 'config.json').write_text('{}')
            transcriber = live.LocalTranscriber(metadata)
            with patch('transcribe_local.sha256', side_effect=OSError('synthetic hash failure')):
                with self.assertRaises(OSError):
                    transcriber._check_model()
            self.assertIsNone(transcriber.model)
            self.assertFalse(hasattr(transcriber, 'identities'))
            with patch('transcribe_local.sha256', return_value='synthetic-digest') as digest:
                transcriber._check_model()
                self.assertEqual(3, digest.call_count)
                transcriber._check_model()
                self.assertEqual(3, digest.call_count)
            self.assertEqual(3, len(transcriber.identities))

    def test_prepare_decodes_one_bounded_synthetic_window_and_retains_no_output(self):
        transcriber = live.LocalTranscriber('unused')
        transcriber.model, transcriber.identities = {'local_path': 'synthetic-model'}, {}
        modules = {name: ModuleType(name) for name in ('mlx', 'mlx.core', 'mlx_whisper',
            'mlx_whisper.audio', 'mlx_whisper.transcribe', 'mlx_whisper.decoding')}
        modules['mlx'].core = modules['mlx.core']
        modules['mlx.core'].float16 = 'synthetic-float16'
        model = SimpleNamespace(dims=SimpleNamespace(n_mels=128))
        holder = Mock()
        holder.get_model.return_value = model
        modules['mlx_whisper.transcribe'].ModelHolder = holder
        audio = modules['mlx_whisper.audio']
        audio.N_FRAMES, audio.N_SAMPLES = 3000, 480000
        audio.log_mel_spectrogram = Mock(return_value='synthetic-mel')
        audio.pad_or_trim = Mock()
        audio.pad_or_trim.return_value.astype.return_value = 'synthetic-fp16-mel'
        decoding = modules['mlx_whisper.decoding']
        decoding.DecodingOptions = Mock(return_value='synthetic-options')
        decoding.decode = Mock(return_value={'text': 'MUST NOT BE RETAINED'})
        slot_calls = []
        @contextmanager
        def slot(owner, **options):
            slot_calls.append((owner, options))
            yield
        event = threading.Event()
        with patch.dict(sys.modules, modules), patch.object(transcriber, '_check_model'), \
                patch('local_inference.inference_slot', slot), processing_scope(event):
            result = transcriber.prepare('en')
        self.assertEqual([('lecture-asr-prepare', {'cancel': event})], slot_calls)
        holder.get_model.assert_called_once_with('synthetic-model', 'synthetic-float16')
        samples = audio.log_mel_spectrogram.call_args.args[0]
        self.assertEqual(48000, len(samples))
        self.assertFalse(samples.any())
        self.assertEqual({'n_mels': 128, 'padding': 480000}, audio.log_mel_spectrogram.call_args.kwargs)
        audio.pad_or_trim.assert_called_once_with('synthetic-mel', 3000, axis=-2)
        decoding.DecodingOptions.assert_called_once_with(task='transcribe', language='en', temperature=0., sample_len=32)
        decoding.decode.assert_called_once_with(model, 'synthetic-fp16-mel', 'synthetic-options')
        self.assertEqual(0, result['retained_source_lines'])
        self.assertEqual(0, result['measured_api_usd'])
        self.assertNotIn('MUST NOT BE RETAINED', json.dumps(result))

    def test_cancel_after_inference_lock_wait_prevents_model_dispatch(self):
        transcriber = live.LocalTranscriber('unused')
        event = threading.Event()
        @contextmanager
        def slot(_owner, *, cancel):
            self.assertIs(event, cancel)
            event.set()
            yield
        with patch.object(transcriber, '_check_model'), \
                patch('local_inference.inference_slot', slot), processing_scope(event):
            with self.assertRaises(ProcessingStopped):
                transcriber.prepare('en')


if __name__ == '__main__':
    unittest.main()
