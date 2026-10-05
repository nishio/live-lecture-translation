import json
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_live as live


def fake_asr(chunk, destination, language):
    return {'chunks': [{'index': 0, 'source_start_seconds': 0,
                        'source_end_seconds': chunk['end_seconds'] - chunk['start_seconds'],
                        'raw_result': {'language': 'en', 'segments': [{'start': 0,
                            'end': chunk['end_seconds'] - chunk['start_seconds'],
                            'text': 'This is a synthetic lecture statement.'}]}}]}


def fake_analysis(lines, previous, **kwargs):
    item = {'text': '合成の講演要点', 'source_ids': [lines[-1]['id']]}
    return {'headline': item, 'summary': [item], 'flow': [item], 'concepts': [], 'questions': [],
            'translations': [{'source_id': key, 'text': '合成の訳'} for key in kwargs['translation_ids']],
            'through_seconds': kwargs['through_seconds'], 'generated_at': time.time()}


class LiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / 'fixture.wav'
        with wave.open(str(self.audio), 'wb') as out:
            out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            out.writeframes(b'\x01\x00' * 16000 * 3)

    def app(self, **kwargs):
        if isinstance(kwargs.get('cloud_scope'), Mock):
            kwargs['cloud_scope'].status.return_value = {'max_seconds': 21600, 'remaining_seconds': 21600}
        return live.LectureApp(data_root=self.root / 'data', results_root=self.root / 'results',
            chunk_seconds=1, transcriber=kwargs.pop('transcriber', fake_asr),
            analyzer=kwargs.pop('analyzer', fake_analysis), **kwargs)

    def wait(self, app, seconds=5):
        app.source_thread.join(seconds)
        app.worker.join(seconds)
        self.assertFalse(app.source_thread.is_alive())
        self.assertFalse(app.worker.is_alive())

    def test_replay_is_saved_and_processed_with_timestamps(self):
        app = self.app()
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        self.wait(app)
        state = app.snapshot()
        self.assertEqual('completed', state['capture']['state'])
        self.assertEqual('completed', state['asr']['state'])
        self.assertEqual(3, state['asr']['through_seconds'])
        self.assertEqual([0, 1, 2], [row['start_seconds'] for row in state['lines']])
        self.assertEqual(96000, (app.session_dir / 'audio/raw.pcm').stat().st_size)
        self.assertEqual(3, len((app.result_dir / 'transcript.jsonl').read_text().splitlines()))
        self.assertEqual(0, json.loads((app.result_dir / 'cost-report.json').read_text())['additional_api_usd'])
        manifest = json.loads((app.result_dir / 'runtime-manifest.json').read_text())
        self.assertEqual(1, manifest['configuration']['translation_source_policy_version'])
        import hashlib
        for relative, digest in manifest['source_sha256'].items():
            self.assertEqual(digest, hashlib.sha256((app.result_dir / 'source-at-start' / relative).read_bytes()).hexdigest())
        measurements = [json.loads(row) for row in (app.result_dir / 'measurements.jsonl').read_text().splitlines()]
        self.assertTrue(all(row['chunk_ready_to_publication_seconds'] >= 0 for row in measurements))
        self.assertTrue(all(row['browser_render_measured'] is False for row in measurements))
        self.assertFalse(json.loads((app.result_dir / 'state.json').read_text())['processing_active'])

    def test_blocked_analysis_does_not_block_recording_or_lose_audio(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(8))
            return fake_analysis(*args, **kwargs)
        app = self.app(analyzer=blocked, analysis_interval=1)
        with patch('lecture_analysis.analyze_snapshot', fake_analysis):
            app.start({'provider': 'local'}, replay=self.audio, pace=1)
            try:
                self.assertTrue(entered.wait(3))
                app.source_thread.join(5)
                self.assertFalse(app.source_thread.is_alive())
                self.assertEqual(3, app.snapshot()['capture']['audio_seconds'])
                self.assertEqual(96000, (app.session_dir / 'audio/raw.pcm').stat().st_size)
                self.assertGreater(app.audio_queue.qsize(), 0)
            finally:
                release.set()
                self.wait(app)
        self.assertEqual(3, app.snapshot()['asr']['through_seconds'])
        snapshots = app.snapshot()['analysis_history']
        self.assertGreaterEqual(len(snapshots), 2)
        self.assertLess(snapshots[0]['through_seconds'], snapshots[-1]['through_seconds'])
        self.assertNotIn('translations', snapshots[0])
        self.assertEqual('c000000-l0000', snapshots[0]['headline']['source_ids'][0])

    def test_duplicate_start_is_rejected(self):
        app = self.app()
        app.start({'provider': 'off'}, replay=self.audio, pace=1)
        try:
            with self.assertRaises(RuntimeError):
                app.start({'provider': 'off'}, replay=self.audio)
        finally:
            app.stop()
            self.wait(app)

    def test_cloud_wait_keeps_asr_moving_and_drains_one_frozen_request_at_a_time(self):
        entered, release = threading.Event(), threading.Event()
        scope = Mock()
        scope.reserve.return_value = {'approved': True}
        calls, active, peak = [], 0, 0
        def blocked(lines, previous, **options):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            calls.append((lines, previous, options))
            try:
                if len(calls) == 1:
                    entered.set()
                    self.assertTrue(release.wait(8))
                return fake_analysis(lines, previous, **options)
            finally:
                active -= 1
        app = self.app(analyzer=blocked, allow_cloud=True, cloud_scope=scope, analysis_interval=1)
        app.start({'provider': 'openai'}, replay=self.audio, pace=1)
        try:
            self.assertTrue(entered.wait(3))
            app.source_thread.join(4)
            deadline = time.monotonic() + 2
            while app.snapshot()['asr']['through_seconds'] < 3 and time.monotonic() < deadline:
                time.sleep(.01)
            state = app.snapshot()
            self.assertEqual(3, state['asr']['through_seconds'])
            self.assertTrue(app.audio_queue.empty())
            self.assertEqual(1, len(calls))
            self.assertEqual(1, calls[0][2]['through_seconds'])
            self.assertEqual(1, len(calls[0][0]))
            self.assertIsNot(calls[0][0][0], app.state['lines'][0])
            self.assertEqual(96000, (app.session_dir / 'audio/raw.pcm').stat().st_size)
            self.assertTrue(app.worker.is_alive())
            self.assertTrue(state['processing_active'])
            self.assertTrue(state['analysis']['worker_alive'])
            self.assertFalse(state['analysis']['completion_confirmed'])
            with self.assertRaises(RuntimeError):
                app.start({'provider': 'off'}, replay=self.audio, pace=0)
            with self.assertRaises(RuntimeError):
                app.retry_analysis()
            self.assertFalse(app.retry_event.is_set())
        finally:
            release.set()
            self.wait(app)
        state = app.snapshot()
        self.assertEqual(1, peak)
        self.assertEqual([1, 3], [call[2]['through_seconds'] for call in calls])
        self.assertTrue(all('retry_failed' not in call[2] for call in calls))
        self.assertTrue(all(call[2]['include_block_translations'] for call in calls))
        self.assertEqual(3, state['analysis']['through_seconds'])
        self.assertEqual('completed', state['asr']['state'])
        self.assertTrue(all(row.get('translation_ja') for row in state['lines']))
        self.assertTrue(all(worker is None for worker in app.cloud_workers.values()))
        self.assertFalse(state['processing_active'])
        self.assertTrue(state['analysis']['completion_confirmed'])
        self.assertFalse(json.loads((app.result_dir / 'state.json').read_text())['processing_active'])

    def test_stop_preserves_final_partial_without_new_asr_or_analysis(self):
        entered, release = threading.Event(), threading.Event()
        scope = Mock()
        scope.reserve.return_value = {'approved': True}
        through = []
        def blocked(lines, previous, **options):
            through.append(options['through_seconds'])
            if len(through) == 1:
                entered.set()
                self.assertTrue(release.wait(8))
            return fake_analysis(lines, previous, **options)
        app = self.app(analyzer=blocked, allow_cloud=True, cloud_scope=scope)
        app.start({'provider': 'openai'}, replay=self.audio, pace=1)
        try:
            self.assertTrue(entered.wait(3))
            deadline = time.monotonic() + 2
            while app.snapshot()['capture']['audio_seconds'] < 1.5 and time.monotonic() < deadline:
                time.sleep(.01)
            app.stop()
            app.source_thread.join(3)
            self.assertEqual(1.5, app.snapshot()['capture']['audio_seconds'])
            self.assertEqual(1, app.snapshot()['asr']['through_seconds'])
            self.assertEqual('stopping', app.snapshot()['processing_stop_status'])
            self.assertTrue(app.worker.is_alive())
        finally:
            release.set()
            self.wait(app)
        self.assertEqual([1], through)
        self.assertEqual(48000, (app.session_dir / 'audio/raw.pcm').stat().st_size)
        self.assertEqual(1, app.snapshot()['analysis']['through_seconds'])
        self.assertEqual(1, app.audio_queue.qsize())
        state = app.snapshot()
        self.assertEqual('paused', state['asr']['state'])
        self.assertEqual('paused', state['analysis']['state'])
        self.assertEqual('stopped', state['processing_stop_status'])
        self.assertFalse(state['analysis']['completion_confirmed'])
        self.assertEqual('stopped', json.loads((app.result_dir / 'state.json').read_text())['processing_stop_status'])

    def test_stop_finishes_only_inflight_asr_and_start_creates_fresh_session(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def transcribe(chunk, *args):
            calls.append(chunk['index'])
            entered.set()
            self.assertTrue(release.wait(5))
            return fake_asr(chunk, *args)
        analyzer = Mock(side_effect=fake_analysis)
        app = self.app(transcriber=transcribe, analyzer=analyzer)
        app.start({'provider': 'local'}, replay=self.audio, pace=0)
        try:
            self.assertTrue(entered.wait(2))
            app.source_thread.join(2)
            old_id, old_dir = app.state['session']['id'], app.result_dir
            state = app.stop()
            self.assertTrue(state['processing_stop_requested'])
            self.assertEqual('running', state['asr']['state'])
            self.assertEqual('stopping', state['processing_stop_status'])
            for operation in (app.retry_analysis, app.retry_translation):
                with self.assertRaises(RuntimeError):
                    operation()
            with self.assertRaises(RuntimeError):
                app.start({'provider': 'off'}, replay=self.audio, pace=0)
        finally:
            release.set()
            self.wait(app)
        self.assertEqual([0], calls)
        analyzer.assert_not_called()
        self.assertEqual(2, app.audio_queue.qsize())
        self.assertEqual([], app.state['asr']['failed_chunks'])
        self.assertFalse((old_dir / 'failed-chunks.jsonl').exists())
        state = app.snapshot()
        self.assertEqual('stopped', state['processing_stop_status'])
        self.assertEqual('paused', state['asr']['state'])
        self.assertEqual('stopped', state['asr']['schedule']['reason'])
        self.assertEqual('stopped', state['analysis']['schedule']['reason'])
        saved = json.loads((old_dir / 'state.json').read_text())
        self.assertEqual('stopped', saved['processing_stop_status'])
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        self.wait(app)
        self.assertNotEqual(old_id, app.state['session']['id'])
        self.assertFalse(app.snapshot()['processing_stop_requested'])
        self.assertEqual('completed', app.state['asr']['state'])
        self.assertEqual(saved, json.loads((old_dir / 'state.json').read_text()))

    def test_local_transcriber_checks_stop_after_slot_acquisition(self):
        from processing_control import ProcessingStopped, processing_scope
        event = threading.Event()
        seen = []
        @contextmanager
        def slot(label, **options):
            seen.append(options['cancel'])
            event.set()
            yield None
        transcribe = live.LocalTranscriber()
        transcribe.model = {'local_path': 'synthetic-unused-model'}
        transcribe.identities = {}
        mlx = Mock()
        with patch('transcribe_local.read_mono', return_value=[]), \
                patch('local_inference.inference_slot', slot), \
                patch.dict(sys.modules, {'mlx_whisper': mlx}), processing_scope(event):
            with self.assertRaises(ProcessingStopped):
                transcribe({'path': self.audio}, self.root / 'unused.json', 'auto')
        self.assertEqual([event], seen)
        mlx.transcribe.assert_not_called()
        self.assertFalse((self.root / 'unused.json').exists())

    def test_frozen_job_cannot_run_with_new_session_stop_event(self):
        from processing_control import ProcessingStopped
        app = self.app()
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        self.wait(app)
        job = app._prepare_analysis()
        old_event = job['stop_event']
        app.stop()
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        self.wait(app)
        self.assertIsNot(old_event, app.abort_processing)
        self.assertTrue(old_event.is_set())
        self.assertFalse(app.abort_processing.is_set())
        with self.assertRaises(ProcessingStopped):
            app._analyze(prepared=job)

    def test_stop_after_scope_reservation_blocks_unsent_analysis(self):
        entered, release = threading.Event(), threading.Event()
        def reserve(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return {'approved': True}
        scope = Mock()
        scope.reserve.side_effect = reserve
        analyzer = Mock(side_effect=fake_analysis)
        app = self.app(analyzer=analyzer, allow_cloud=True, cloud_scope=scope)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        try:
            self.assertTrue(entered.wait(3))
            app.stop()
        finally:
            release.set()
            self.wait(app)
        analyzer.assert_not_called()
        state = app.snapshot()
        self.assertEqual('paused', state['analysis']['state'])
        self.assertFalse(app.inference_unconfirmed)
        self.assertIsNone(state['analysis']['schedule']['retry']['next_at'])
        self.assertTrue((app.result_dir / 'cloud-scope.jsonl').exists())
        self.assertFalse((app.result_dir / 'analysis-history.jsonl').exists())

    def test_asr_cancelled_before_dispatch_keeps_chunk_pending(self):
        from local_inference import InferenceCancelled
        entered, release = threading.Event(), threading.Event()
        def unsent(chunk, *args):
            entered.set()
            self.assertTrue(release.wait(5))
            raise InferenceCancelled('synthetic slot wait cancelled')
        app = self.app(transcriber=unsent)
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        try:
            self.assertTrue(entered.wait(2))
            app.source_thread.join(2)
            app.stop()
        finally:
            release.set()
            self.wait(app)
        self.assertEqual(3, app.audio_queue.qsize())
        self.assertEqual(0, app.state['asr']['through_seconds'])
        self.assertEqual([], app.state['asr']['failed_chunks'])
        self.assertFalse((app.result_dir / 'failed-chunks.jsonl').exists())
        self.assertEqual('paused', app.state['asr']['state'])

    def test_close_timeout_keeps_blocked_cloud_owned_and_stops_new_analysis(self):
        entered, release = threading.Event(), threading.Event()
        scope = Mock()
        scope.reserve.return_value = {'approved': True}
        calls = []
        def blocked(lines, previous, **options):
            calls.append(options)
            entered.set()
            self.assertTrue(release.wait(8))
            return fake_analysis(lines, previous, **options)
        app = self.app(analyzer=blocked, allow_cloud=True, cloud_scope=scope)
        app.start({'provider': 'openai'}, replay=self.audio, pace=1)
        try:
            self.assertTrue(entered.wait(3))
            self.assertFalse(app.close(timeout=.02))
            state = app.snapshot()
            self.assertTrue(state['processing_active'])
            self.assertTrue(state['analysis']['worker_alive'])
            self.assertFalse(state['analysis']['completion_confirmed'])
            self.assertTrue(app.worker.is_alive())
            self.assertFalse(app.cloud_workers['analysis'].daemon)
            with self.assertRaises(RuntimeError):
                app.start({'provider': 'off'}, replay=self.audio, pace=0)
        finally:
            release.set()
            self.wait(app)
        self.assertEqual(1, len(calls))
        self.assertTrue(app.close(.1))
        self.assertEqual('paused', app.snapshot()['asr']['state'])
        self.assertFalse(app.snapshot()['processing_active'])

    def test_stale_cloud_publication_cannot_modify_successor_session(self):
        app = self.app()
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        self.wait(app)
        old_job = app._prepare_analysis()
        old_result = fake_analysis(old_job['lines'], old_job['previous'],
            translation_ids=old_job['translation_ids'], through_seconds=old_job['through'])
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        self.wait(app)
        before = json.dumps(app.state, sort_keys=True)
        app._publish_analysis(old_result, old_job['lines'], old_job['through'], old_job['selection'],
            time.monotonic(), session_id=old_job['session']['id'], result_dir=old_job['result_dir'])
        self.assertEqual(before, json.dumps(app.state, sort_keys=True))
        self.assertFalse((app.result_dir / 'analysis-history.jsonl').exists())

    def test_failed_chunk_remains_visible_after_later_success(self):
        def failing(chunk, *args):
            if chunk['index'] == 1:
                raise ValueError('synthetic ASR failure')
            return fake_asr(chunk, *args)
        app = self.app(transcriber=failing)
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        self.wait(app)
        state = app.snapshot()
        self.assertEqual('failed', state['asr']['state'])
        self.assertEqual([1], [x['index'] for x in state['asr']['failed_chunks']])
        self.assertEqual(3, state['asr']['through_seconds'])
        self.assertEqual(96000, (app.session_dir / 'audio/raw.pcm').stat().st_size)

    def test_cloud_requires_explicit_runtime_enablement(self):
        app = self.app()
        with self.assertRaises(ValueError):
            app.start({'provider': 'openai'}, replay=self.audio)
        self.assertFalse((self.root / 'data').exists())

    def test_offline_keeps_audio_and_asr_then_manual_retry_recovers(self):
        scope = Mock()
        scope.reserve.return_value = {'approved': True}
        readiness = Mock()
        readiness.snapshot.return_value = {'state': 'ready', 'checked_at': time.time(),
            'checks': [{'id': 'network', 'reachable': False}]}
        analyzer = Mock(side_effect=fake_analysis)
        app = self.app(allow_cloud=True, cloud_scope=scope, readiness=readiness, analyzer=analyzer)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.wait(app)
        state = app.snapshot()
        self.assertEqual('completed', state['capture']['state'])
        self.assertEqual('completed', state['asr']['state'])
        self.assertEqual('failed', state['analysis']['state'])
        self.assertEqual(96000, (app.session_dir / 'audio/raw.pcm').stat().st_size)
        scope.reserve.assert_not_called()
        analyzer.assert_not_called()
        readiness.snapshot.return_value['checks'][0]['reachable'] = True
        app.retry_analysis()
        app.worker.join(5)
        self.assertFalse(app.worker.is_alive())
        self.assertEqual('completed', app.snapshot()['analysis']['state'])
        self.assertTrue(analyzer.call_args.kwargs['retry_failed'])
        self.assertEqual(1, scope.reserve.call_count)

    def test_readiness_rejects_start_without_creating_session(self):
        readiness = Mock()
        readiness.require_recording_ready.side_effect = ValueError('保存先の空き不足')
        app = self.app(readiness=readiness)
        with self.assertRaisesRegex(ValueError, '空き不足'):
            app.start({'provider': 'off'})
        self.assertIsNone(app.state['session'])
        self.assertFalse((self.root / 'data').exists())

    def test_new_audio_after_reconnect_resumes_analysis_without_a_button(self):
        readiness = Mock()
        health = {'state': 'ready', 'checked_at': time.time(), 'checks': [{'id': 'network', 'reachable': False}]}
        readiness.snapshot.side_effect = lambda: health
        scope = Mock()
        scope.reserve.return_value = {'approved': True}
        published_while_recording = []
        def analyze(*args, **kwargs):
            published_while_recording.append(not app.source_done.is_set())
            return fake_analysis(*args, **kwargs)
        app = self.app(allow_cloud=True, cloud_scope=scope, readiness=readiness, analyzer=analyze)
        with patch.object(live, 'OFFLINE_RECHECK_SECONDS', .01):
            app.start({'provider': 'openai'}, replay=self.audio, pace=1)
            try:
                deadline = time.monotonic() + 2
                while app.state['analysis']['state'] != 'failed' and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual('failed', app.state['analysis']['state'])
                scope.reserve.assert_not_called()
                health['checks'][0]['reachable'] = True
                self.wait(app)
            finally:
                app.stop()
        self.assertTrue(any(published_while_recording))
        self.assertEqual('completed', app.state['analysis']['state'])

    def test_bad_replay_marks_capture_failure_not_completed(self):
        app = self.app()
        app.start({'provider': 'off'}, replay=self.root / 'missing.wav')
        self.wait(app)
        state = app.snapshot()
        self.assertEqual('failed', state['capture']['state'])
        self.assertIn('失敗', state['message'])

    def test_context_retains_old_evidence_and_meaningful_uncertain_rows(self):
        lines = [{'id': str(i), 'text': 't', 'start_seconds': i, 'end_seconds': i + 1,
                  'uncertain': i == 8} for i in range(10)]
        selected = live.select_analysis_lines(lines, {'headline': {'text': 'previous', 'source_ids': ['0']}},
                                              window_seconds=2, max_lines=4)
        self.assertEqual(['0', '7', '8', '9'], [x['id'] for x in selected])
        self.assertTrue(selected[2]['uncertain'])

    def test_context_excludes_only_explicit_filler_without_changing_raw_source(self):
        lines = [{'id': 'filler', 'text': 'um, uh...', 'start_seconds': 0, 'end_seconds': 1,
                  'language': 'en', 'uncertain': True, 'doubt_reasons': ['repetition']},
                 {'id': 'meaning', 'text': 'So we should not remove 17 cases.', 'start_seconds': 1,
                  'end_seconds': 2, 'language': 'en', 'uncertain': True, 'doubt_reasons': ['no_speech']}]
        original = json.dumps(lines)
        selected = live.select_analysis_lines(lines, None)
        self.assertEqual(['meaning'], [row['id'] for row in selected])
        self.assertEqual(['no_speech'], selected[0]['doubt_reasons'])
        self.assertEqual(original, json.dumps(lines))

    def test_saved_legacy_state_keeps_uncertain_exclusion_counts_without_policy_upgrade(self):
        app = self.app()
        for field in ('source_policy_version', 'included_uncertain_lines', 'excluded_sources'):
            app.state['translation'].pop(field, None)
        app.state['translation'].update(enabled=True, state='completed', pending_lines=0,
                                        excluded_uncertain_lines=1)
        app.state['lines'] = [{'id': 'c000000-l0000', 'text': 'Do not remove 17 cases.',
            'start_seconds': 0, 'end_seconds': 1, 'language': 'en', 'uncertain': True,
            'doubt_reasons': ['no_speech']}]
        app.state['session'] = {'id': 'saved-legacy-session'}
        app.state['capture'].update(state='completed', audio_seconds=1)
        app.state['asr'].update(state='completed', through_seconds=1)
        app.state['analysis']['state'] = 'completed'
        snapshot = app.snapshot()
        self.assertEqual(0, snapshot['translation']['pending_lines'])
        self.assertEqual(1, snapshot['translation']['excluded_uncertain_lines'])
        self.assertEqual(0, snapshot['analysis']['untranslated_lines'])
        self.assertNotIn('source_policy_version', snapshot['translation'])
        self.assertNotIn('included_uncertain_lines', snapshot['translation'])
        self.assertNotIn('excluded_sources', snapshot['translation'])
        self.assertFalse(snapshot['processing_active'])

    def test_saved_current_policy_state_counts_meaningful_uncertain_pending_source(self):
        app = self.app()
        app.state['translation'].update(enabled=True, state='waiting')
        app.state['session'] = {'id': 'saved-policy-session'}
        app.state['lines'] = [{'id': 'c000000-l0000', 'text': 'Do not remove 17 cases.',
            'start_seconds': 0, 'end_seconds': 1, 'language': 'en', 'uncertain': True,
            'doubt_reasons': ['no_speech']}]
        snapshot = app.snapshot()
        self.assertEqual(1, snapshot['translation']['source_policy_version'])
        self.assertEqual(1, snapshot['translation']['pending_lines'])
        self.assertEqual(1, snapshot['translation']['included_uncertain_lines'])
        self.assertEqual(0, snapshot['translation']['excluded_uncertain_lines'])
        self.assertEqual(1, snapshot['analysis']['untranslated_lines'])

    def test_saved_unknown_source_policy_is_rejected_without_changing_state(self):
        for version in (0, 2, True, '1', None, 1.0):
            with self.subTest(version=version):
                app = self.app()
                app.state['session'] = {'id': 'saved-unsupported-policy'}
                app.state['translation'].update(source_policy_version=version, enabled=True,
                    state='completed', pending_lines=0, excluded_uncertain_lines=1)
                app.state['lines'] = [{'id': 'c000000-l0000', 'text': 'Do not remove 17 cases.',
                    'start_seconds': 0, 'end_seconds': 1, 'language': 'en', 'uncertain': True,
                    'doubt_reasons': ['no_speech']}]
                original = json.dumps(app.state, sort_keys=True)
                with self.assertRaisesRegex(ValueError, 'ポリシー'):
                    app.snapshot()
                self.assertEqual(original, json.dumps(app.state, sort_keys=True))
                self.assertIsNone(app.worker)
                self.assertIsNone(app.result_dir)

    def test_close_blocks_new_work_before_waiting_for_readiness(self):
        readiness = Mock()
        app = self.app(readiness=readiness)
        observed = []
        readiness.close.side_effect = lambda: observed.append((
            app.abort_processing.is_set(), app.stop_source.is_set(), app.closing))
        self.assertTrue(app.close(.1))
        self.assertEqual([(True, True, True)], observed)

    def test_stop_preserves_existing_asr_failures(self):
        app = self.app(transcriber=Mock(side_effect=ValueError('synthetic ASR failure')))
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        self.wait(app)
        failed = list(app.state['asr']['failed_chunks'])
        records = (app.result_dir / 'failed-chunks.jsonl').read_text()
        state = app.stop()
        self.assertEqual('failed', state['asr']['state'])
        self.assertEqual(failed, state['asr']['failed_chunks'])
        self.assertEqual(records, (app.result_dir / 'failed-chunks.jsonl').read_text())
        self.assertEqual('stopped', state['processing_stop_status'])

    def test_shutdown_reports_unfinished_work(self):
        entered, release = threading.Event(), threading.Event()
        def slow_asr(*args):
            entered.set()
            release.wait(5)
            return fake_asr(*args)
        app = self.app(transcriber=slow_asr)
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(app.close(timeout=.01))
            self.assertIn('終了待ち', app.snapshot()['message'])
        finally:
            release.set()
            self.wait(app)

    def test_large_context_selects_visible_excerpt_without_mutating_original(self):
        lines = [{'id': f'c{i:06d}-l0000', 'text': 'A complete synthetic statement. ' * 20,
                  'start_seconds': i * 3, 'end_seconds': i * 3 + 3, 'language': 'en'} for i in range(100)]
        original = json.dumps(lines)
        selected, targets, evidence = live.bounded_analysis_input(lines, None)
        self.assertLess(len(selected), len(lines))
        self.assertEqual(lines[-1], selected[-1])
        self.assertLessEqual(evidence['input_bytes'], 22000)
        self.assertGreater(evidence['omitted_for_request_limit'], 0)
        self.assertFalse(evidence['complete_lecture_coverage'])
        self.assertLessEqual(len(targets), 24)
        self.assertEqual(original, json.dumps(lines))

    def test_live_block_request_uses_the_same_input_budget_as_generation(self):
        from lecture_analysis import build_snapshot_request
        lines = [{'id': f'c{i:06d}-l0000', 'text': 'A complete synthetic statement. ' * 20,
                  'start_seconds': i * 3, 'end_seconds': i * 3 + 3, 'language': 'en'} for i in range(100)]
        selected, targets, selection = live.bounded_analysis_input(lines, None, include_block_translations=True)
        request = build_snapshot_request(selected, None, translation_ids=targets, include_block_translations=True)
        self.assertTrue(selection['include_block_translations'])
        self.assertEqual(request['input_bytes'], selection['input_bytes'])
        self.assertLessEqual(request['input_bytes'], 22000)
        self.assertEqual(lines[-1], selected[-1])

    def test_live_freezes_filler_breaks_without_adding_their_text_to_model_input(self):
        from lecture_analysis import build_snapshot_request
        lines = [{'id': 'a', 'text': 'The first statement.', 'start_seconds': 0, 'end_seconds': 4, 'language': 'en'},
                 {'id': 'uncertain', 'text': 'um, uh...', 'start_seconds': 4, 'end_seconds': 5,
                  'language': 'en', 'uncertain': True},
                 {'id': 'b', 'text': 'The next statement.', 'start_seconds': 5, 'end_seconds': 9, 'language': 'en'}]
        calls = []
        def generate(selected, previous, **kwargs):
            calls.append((selected, kwargs))
            return fake_analysis(selected, previous, **kwargs)
        app = self.app(analyzer=generate)
        app.state['lines'] = lines
        app.state['session'] = {'id': 'synthetic-session'}
        app.result_dir = self.root / 'results'
        prepared = app._prepare_analysis()
        self.assertEqual(['a', 'b'], [row['id'] for row in prepared['lines']])
        self.assertEqual(['a', 'b'], prepared['translation_ids'])
        breaks = prepared['selection']['block_translation_breaks']
        self.assertEqual([{'start_seconds': 4, 'end_seconds': 5}], breaks)
        lines[1]['end_seconds'] = 8  # Future app changes cannot mutate an inflight request.
        with patch.object(app, 'persist'), patch.object(app, '_publish_analysis'):
            app._analyze(prepared=prepared)
        self.assertEqual(breaks, calls[0][1]['block_translation_breaks'])
        request = build_snapshot_request(calls[0][0], translation_ids=prepared['translation_ids'],
            include_block_translations=True, block_translation_breaks=calls[0][1]['block_translation_breaks'])
        self.assertEqual([['a'], ['b']], request['block_translation_groups'])
        self.assertEqual(request['input_bytes'], prepared['selection']['input_bytes'])
        self.assertNotIn('um, uh...', json.dumps(request['messages']))

    def test_live_freezes_uncertainty_reasons_and_keeps_meaningful_evidence(self):
        from lecture_analysis import build_snapshot_request
        app = self.app()
        app.state['lines'] = [{'id': 'c000000-l0000', 'text': 'Do not remove 17 cases.',
            'start_seconds': 0, 'end_seconds': 4, 'language': 'en', 'uncertain': True,
            'doubt_reasons': ['no_speech']}]
        app.state['session'] = {'id': 'synthetic-session'}
        app.result_dir = self.root / 'results'
        prepared = app._prepare_analysis()
        self.assertEqual(['c000000-l0000'], prepared['translation_ids'])
        self.assertEqual([], prepared['selection']['block_translation_breaks'])
        app.state['lines'][0]['doubt_reasons'].append('repetition')
        request = build_snapshot_request(prepared['lines'], translation_ids=prepared['translation_ids'])
        payload = json.loads(request['messages'][1]['content'])
        self.assertEqual('Do not remove 17 cases.', payload['transcript'][0]['text'])
        self.assertTrue(payload['transcript'][0]['uncertain'])
        self.assertEqual(['no_speech'], payload['transcript'][0]['doubt_reasons'])

    def test_failed_startup_preparation_allows_later_start(self):
        app = self.app()
        with patch('lecture_live.save_runtime', side_effect=OSError('synthetic full disk')):
            with self.assertRaises(OSError):
                app.start({'provider': 'off'}, replay=self.audio, pace=0)
        self.assertEqual('failed', app.snapshot()['capture']['state'])
        self.assertTrue(app.source_done.is_set())
        self.assertIsNone(app.worker)
        self.assertIsNone(app.source_thread)
        self.assertTrue(app.close(.1))
        app.closing = False
        app.start({'provider': 'off'}, replay=self.audio, pace=0)
        self.wait(app)
        self.assertEqual('completed', app.snapshot()['capture']['state'])

    def test_analysis_publication_failure_does_not_claim_inference_is_running(self):
        app = self.app()
        with patch.object(app, '_publish_analysis', side_effect=OSError('synthetic analysis disk error')):
            app.start({'provider': 'local'}, replay=self.audio, pace=0)
            self.wait(app)
        self.assertFalse(app.inference_unconfirmed)
        self.assertEqual('completed', app.snapshot()['asr']['state'])
        self.assertEqual('failed', app.snapshot()['analysis']['state'])

    def test_cloud_cost_tracks_confirmed_usage_or_keeps_unknown_reservation(self):
        import hashlib
        from event_insights_cloud import CloudError
        for failed in (False, True):
            with self.subTest(failed=failed):
                ledger = {'requests': {}}
                def cloud_result(lines, previous, **kwargs):
                    payload = {'model': 'synthetic-cost-test'}
                    directory = kwargs['out_dir'] / 'synthetic-call'
                    directory.mkdir(parents=True)
                    (directory / 'payload.json').write_text(json.dumps(payload))
                    key = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                    ledger['requests'][key] = {'fingerprint': key, 'state': 'failed' if failed else 'completed',
                                               'charged_nanodollars': 250000}
                    if failed:
                        raise CloudError('synthetic unknown request')
                    result = fake_analysis(lines, previous, **kwargs)
                    result['cost_usd'] = .00025
                    return result
                scope = Mock()
                scope.reserve.return_value = {'synthetic_scope_receipt': True}
                app = self.app(analyzer=cloud_result, allow_cloud=True, cloud_scope=scope)
                with patch('event_insights_cloud._load_ledger', return_value=ledger), \
                     patch('event_insights_cloud.budget_status', return_value={'budget_usd': 1}):
                    app.start({'provider': 'openai'}, replay=self.audio, pace=0)
                    self.wait(app)
                cost = json.loads((app.result_dir / 'cost-report.json').read_text())
                self.assertEqual(.00025 if not failed else None, cost['additional_api_usd'])
                self.assertEqual(.00025 if failed else 0, cost['retained_reservation_usd'])
                self.assertEqual('completed', app.snapshot()['asr']['state'])
                self.assertTrue(scope.reserve.called)

    def test_only_manual_cloud_retry_is_forwarded_after_fresh_scope_reservation(self):
        from event_insights_cloud import CloudError
        from lecture_cloud_scope import CloudScopeError
        calls = []
        order = []
        scope = Mock()
        def reserve(*args):
            order.append('scope')
            return {'synthetic_scope_receipt': True}
        scope.reserve.side_effect = reserve
        def transcribe(chunk, *args):
            self.assertTrue(app.source_done.wait(3))
            return fake_asr(chunk, *args)
        def generate(lines, previous, **kwargs):
            self.assertEqual('scope', order[-1])
            order.append('generate')
            calls.append(kwargs.get('retry_failed', False))
            if not kwargs.get('retry_failed'):
                raise CloudError('synthetic first failure')
            return fake_analysis(lines, previous, **kwargs)
        app = self.app(transcriber=transcribe, analyzer=generate, allow_cloud=True, cloud_scope=scope)
        with patch('event_insights_cloud._load_ledger', return_value={'requests': {}}), \
                patch('event_insights_cloud.budget_status', return_value={'budget_usd': 1}):
            app.start({'provider': 'openai'}, replay=self.audio, pace=0)
            self.wait(app)
            self.assertEqual([False], calls)
            app.retry_analysis()
            self.wait(app)
            self.assertEqual([False, True], calls)
            self.assertEqual(['scope', 'generate', 'scope', 'generate'], order)
            self.assertEqual('completed', app.snapshot()['analysis']['state'])
            # Revocation or exhaustion of the text scope blocks even manual retry.
            scope.reserve.side_effect = CloudScopeError('synthetic scope withdrawal')
            app.retry_analysis()
            self.wait(app)
            self.assertEqual([False, True], calls)
            self.assertEqual(3, scope.reserve.call_count)
            self.assertEqual('failed', app.snapshot()['analysis']['state'])

    def test_local_manual_retry_does_not_receive_cloud_retry_option(self):
        calls = []
        def generate(lines, previous, **kwargs):
            calls.append(kwargs)
            return fake_analysis(lines, previous, **kwargs)
        app = self.app(analyzer=generate)
        app.start({'provider': 'local'}, replay=self.audio, pace=0)
        self.wait(app)
        before = len(calls)
        app.retry_analysis()
        self.wait(app)
        self.assertEqual(before + 1, len(calls))
        self.assertTrue(all('retry_failed' not in options for options in calls))

    def test_manual_retry_arriving_after_eligibility_read_is_not_lost(self):
        entered_asr, release_asr = threading.Event(), threading.Event()
        eligibility_read, release_read = threading.Event(), threading.Event()
        self.addCleanup(release_asr.set)
        self.addCleanup(release_read.set)
        calls = []
        scope = Mock()
        scope.reserve.return_value = {'synthetic_scope_receipt': True}
        def transcribe(chunk, *args):
            entered_asr.set()
            self.assertTrue(release_asr.wait(3))
            self.assertTrue(app.source_done.wait(3))
            return fake_asr(chunk, *args)
        def generate(lines, previous, **kwargs):
            calls.append(kwargs.get('retry_failed', False))
            return fake_analysis(lines, previous, **kwargs)
        app = self.app(transcriber=transcribe, analyzer=generate, allow_cloud=True, cloud_scope=scope)
        class PauseEligibility(threading.Event):
            armed = True
            def is_set(event):
                observed = super().is_set()
                if (event.armed and threading.current_thread() is app.worker
                        and app.audio_queue.empty()):
                    event.armed = False
                    eligibility_read.set()
                    release_read.wait(3)
                return observed
        with patch('event_insights_cloud._load_ledger', return_value={'requests': {}}), \
                patch('event_insights_cloud.budget_status', return_value={'budget_usd': 1}):
            app.start({'provider': 'openai'}, replay=self.audio, pace=0)
            try:
                self.assertTrue(entered_asr.wait(3))
                app.retry_event = PauseEligibility()
                release_asr.set()
                self.assertTrue(eligibility_read.wait(3))
                # The worker already observed False, but has not consumed it yet.
                app.retry_analysis()
            finally:
                release_read.set()
                release_asr.set()
                self.wait(app)
            self.assertEqual([True], calls)
            self.assertEqual(1, scope.reserve.call_count)
            self.assertFalse(app.retry_event.is_set())


if __name__ == '__main__':
    unittest.main()
