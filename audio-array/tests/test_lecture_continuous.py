"""CPU-only integration of ASR, FIFO translation, analysis, and shutdown."""
from copy import deepcopy
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
from test_lecture_live import fake_analysis, fake_asr


def fake_translation(plan, **options):
    return {'blocks': [{'text': '文脈を保った日本語訳。', 'source_ids': list(group)} for group in plan['groups']],
            'source_hashes': plan['source_hashes'], 'target_source_ids': plan['target_source_ids'],
            'through_seconds': plan['through_seconds'], 'target_through_seconds': plan['target_through_seconds'],
            'generated_at': time.time(), 'cost_usd': 0}


def fake_uncertain_asr(chunk, *args):
    result = fake_asr(chunk, *args)
    result['chunks'][0]['raw_result']['segments'][0].update(no_speech_prob=.99, avg_logprob=-4)
    return result


class ContinuousTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.wave(3)
        self.apps = []
        self.addCleanup(self.close_apps)

    def close_apps(self):
        for app in self.apps:
            app.close(3)

    def wave(self, seconds):
        path = self.root / f'{seconds}-seconds.wav'
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            for _ in range(seconds):
                stream.writeframes(b'\x01\x00' * 16000)
        return path

    def app(self, **options):
        scope = Mock()
        scope.reserve.return_value = {'approved': True}
        scope.status.return_value = {'remaining_seconds': 21600}
        app = live.LectureApp(data_root=self.root / 'data', results_root=self.root / 'results',
            chunk_seconds=options.pop('chunk_seconds', 1), continuous_translation=True,
            allow_cloud=True, cloud_scope=scope, transcriber=options.pop('transcriber', fake_asr),
            analyzer=options.pop('analyzer', fake_analysis), translator=options.pop('translator', fake_translation),
            **options)
        self.apps.append(app)
        return app

    def wait(self, app, timeout=8):
        app.source_thread.join(timeout)
        app.worker.join(timeout)
        self.assertFalse(app.source_thread.is_alive())
        self.assertFalse(app.worker.is_alive())

    def until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_all_eligible_source_is_translated_fifo_beyond_analysis_window(self):
        plans, analyses = [], []
        def transcribe(chunk, *args):
            self.assertTrue(app.source_done.wait(3))
            value = fake_asr(chunk, *args)
            raw = value['chunks'][0]['raw_result']
            if chunk['index'] == 3:
                raw['segments'][0].update(no_speech_prob=.99, avg_logprob=-4)
            if chunk['index'] == 8:
                raw['language'] = 'ja'
                raw['segments'][0]['text'] = '日本語の原文です。'
            return value
        def translate(plan, **options):
            plans.append(deepcopy(plan))
            self.assertFalse(options['retry_failed'])
            return fake_translation(plan, **options)
        def analyze(lines, previous, **options):
            analyses.append((deepcopy(lines), options))
            self.assertEqual([], options['translation_ids'])
            self.assertFalse(options['include_block_translations'])
            return fake_analysis(lines, previous, **options)
        app = self.app(chunk_seconds=15, transcriber=transcribe, translator=translate, analyzer=analyze)
        app.start({'provider': 'openai'}, replay=self.wave(300), pace=0)
        self.wait(app)
        state = app.snapshot()
        eligible = [row['id'] for row in state['lines'] if row['language'] != 'ja']
        covered = [identity for block in state['translation']['blocks'] for identity in block['source_ids']]
        self.assertEqual(eligible, covered)
        self.assertEqual(19, len(covered))
        self.assertEqual(1, state['translation']['native_lines'])
        self.assertEqual(0, state['translation']['excluded_uncertain_lines'])
        self.assertEqual(1, state['translation']['included_uncertain_lines'])
        self.assertEqual(1, state['translation']['source_policy_version'])
        self.assertEqual(0, state['translation']['pending_lines'])
        self.assertEqual('completed', state['translation']['state'])
        self.assertEqual('c000000-l0000', plans[0]['target_source_ids'][0])
        self.assertGreater(analyses[0][0][0]['start_seconds'], 0)
        self.assertEqual(300, state['translation']['through_seconds'])
        self.assertGreater(len(plans), 1)
        history = [json.loads(row) for row in (app.result_dir / 'translation-history.jsonl').read_text().splitlines()]
        self.assertEqual(state['translation']['blocks'], [block for item in history for block in item['blocks']])
        measures = [json.loads(row) for row in (app.result_dir / 'measurements.jsonl').read_text().splitlines()]
        translations = [row for row in measures if row['stage'] == 'translation']
        self.assertEqual(len(plans), len(translations))
        self.assertTrue(all(row['published_at'] >= row['started_at'] for row in translations))
        self.assertFalse(state['processing_active'])

    def test_first_ready_translation_precedes_analysis_without_initial_interval_wait(self):
        order, first_source_active = [], []
        def translate(plan, **options):
            if not order:
                first_source_active.append(not app.source_done.is_set())
            order.append('translation')
            return fake_translation(plan, **options)
        def analyze(*args, **options):
            self.assertTrue(app.state['translation']['blocks'])
            order.append('analysis')
            return fake_analysis(*args, **options)
        app = self.app(translator=translate, analyzer=analyze)
        app.start({'provider': 'openai'}, replay=self.wave(4), pace=1)
        self.wait(app)
        self.assertEqual(['translation', 'analysis'], order[:2])
        self.assertEqual([True], first_source_active)
        self.assertEqual(0, app.snapshot()['translation']['pending_lines'])
        manifest = json.loads((app.result_dir / 'runtime-manifest.json').read_text())
        self.assertTrue(manifest['configuration']['initial_translation_first'])

    def test_blocked_translation_does_not_block_asr_and_final_drain_preserves_blocks(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        plans, order, active, peak = [], [], 0, 0
        def translate(plan, **options):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            order.append('translation')
            plans.append(deepcopy(plan))
            try:
                if len(plans) == 1:
                    entered.set()
                    self.assertTrue(release.wait(7))
                return fake_translation(plan, **options)
            finally:
                active -= 1
        def analyze(*args, **options):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            order.append('analysis')
            try:
                return fake_analysis(*args, **options)
            finally:
                active -= 1
        app = self.app(translator=translate, analyzer=analyze, translation_interval=.01, analysis_interval=.01)
        app.start({'provider': 'openai'}, replay=self.wave(5), pace=1)
        try:
            self.assertTrue(entered.wait(4))
            self.until(lambda: app.snapshot()['asr']['through_seconds'] == 5)
            self.assertTrue(app.worker.is_alive())
            state = app.snapshot()
            self.assertTrue(state['translation']['worker_alive'])
            self.assertFalse(state['analysis']['worker_alive'])
            self.assertFalse(state['translation']['completion_confirmed'])
            self.assertEqual(5, state['translation']['pending_lines'])
            self.assertLess(plans[0]['through_seconds'], 5)
            with self.assertRaises(RuntimeError):
                app.start({'provider': 'off'}, replay=self.audio)
        finally:
            release.set()
            self.wait(app)
        self.assertEqual(1, peak)
        self.assertEqual(['translation', 'analysis', 'translation'], order[order.index('translation'):])
        self.assertEqual(0, app.snapshot()['translation']['pending_lines'])
        self.assertEqual(5, len({identity for block in app.state['translation']['blocks'] for identity in block['source_ids']}))

    def test_cloud_failure_preserves_pending_and_requires_explicit_translation_retry(self):
        attempts = []
        def transcribe(chunk, *args):
            # This fixture tests one failed final request and its exact retry.
            # Live startup prefixes and later fresh batches are tested separately.
            self.assertTrue(app.source_done.wait(3))
            return fake_uncertain_asr(chunk, *args)
        def translate(plan, **options):
            attempts.append((deepcopy(plan), options['retry_failed']))
            if not options['retry_failed']:
                raise RuntimeError('synthetic API outcome unknown')
            return fake_translation(plan, **options)
        app = self.app(translator=translate, transcriber=transcribe)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.wait(app)
        state = app.snapshot()
        self.assertEqual('completed', state['asr']['state'])
        self.assertEqual('failed', state['translation']['state'])
        self.assertEqual(3, state['translation']['pending_lines'])
        self.assertEqual(3, state['translation']['included_uncertain_lines'])
        self.assertEqual(0, state['translation']['excluded_uncertain_lines'])
        self.assertTrue(all(row['uncertain'] for row in attempts[0][0]['source_lines']))
        self.assertTrue(all(row['doubt_reasons'] == ['no_speech'] for row in attempts[0][0]['source_lines']))
        self.assertTrue(state['translation']['retry_required'])
        self.assertEqual([], state['translation']['blocks'])
        self.assertEqual(1, len(attempts))
        app.retry_translation()
        self.wait(app)
        self.assertEqual([False, True], [manual for _, manual in attempts])
        self.assertEqual(attempts[0][0], attempts[1][0])
        self.assertEqual(0, app.snapshot()['translation']['pending_lines'])
        self.assertEqual('completed', app.snapshot()['translation']['state'])

    def test_offline_precheck_resumes_pending_without_manual_api_retry(self):
        health = {'checks': [{'id': 'network', 'reachable': False}]}
        readiness = Mock()
        readiness.snapshot.side_effect = lambda: health
        translator = Mock(side_effect=fake_translation)
        app = self.app(readiness=readiness, translator=translator)
        with patch.object(live, 'OFFLINE_RECHECK_SECONDS', .02):
            app.start({'provider': 'openai'}, replay=self.audio, pace=0)
            self.until(lambda: app.snapshot()['translation']['state'] == 'waiting'
                       and app.snapshot()['translation']['error'] is not None)
            self.assertTrue(app.worker.is_alive())
            app.cloud_scope.reserve.assert_not_called()
            translator.assert_not_called()
            health['checks'][0]['reachable'] = True
            self.wait(app)
        self.assertFalse(translator.call_args.kwargs['retry_failed'])
        self.assertEqual(0, app.snapshot()['translation']['pending_lines'])

    def test_final_admitted_native_asr_does_not_leave_phantom_pending_translation(self):
        entered, release = threading.Event(), threading.Event()
        def transcribe(chunk, *args):
            self.assertTrue(app.source_done.wait(3))
            entered.set()
            self.assertTrue(release.wait(5))
            result = fake_asr(chunk, *args)
            result['chunks'][0]['raw_result']['language'] = 'ja'
            return result
        translator = Mock(side_effect=fake_translation)
        analyzer = Mock(side_effect=fake_analysis)
        app = self.app(transcriber=transcribe, translator=translator, analyzer=analyzer)
        app.start({'provider': 'openai'}, replay=self.wave(1), pace=0)
        try:
            self.assertTrue(entered.wait(3))
            state = app.stop()
            self.assertEqual('paused', state['translation']['state'])
        finally:
            release.set()
            self.wait(app)
        translator.assert_not_called()
        analyzer.assert_not_called()
        state = app.snapshot()
        self.assertEqual('completed', state['asr']['state'])
        self.assertEqual('completed', state['translation']['state'])
        self.assertTrue(state['translation']['completion_confirmed'])
        self.assertEqual(0, state['translation']['pending_lines'])
        self.assertEqual('paused', state['analysis']['state'])
        self.assertIsNone(state['analysis']['result'])

    def test_stop_at_caught_up_boundary_keeps_completed_generation_evidence(self):
        app = self.app()
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.wait(app)
        self.assertEqual('completed', app.state['analysis']['state'])
        self.assertEqual('completed', app.state['translation']['state'])
        # Model the source-finalization gap with no additional captured audio.
        app.source_done.clear()
        state = app.stop()
        self.assertEqual('completed', state['analysis']['state'])
        self.assertEqual('completed', state['translation']['state'])
        self.assertEqual(0, state['translation']['pending_lines'])
        app.source_done.set()
        app.persist(force=True)
        saved = json.loads((app.result_dir / 'state.json').read_text())
        self.assertEqual('completed', saved['asr']['state'])
        self.assertEqual('completed', saved['analysis']['state'])
        self.assertEqual('completed', saved['translation']['state'])
        self.assertEqual('stopped', saved['processing_stop_status'])

    def test_stop_ends_offline_wait_without_new_attempts(self):
        health = {'checks': [{'id': 'network', 'reachable': False}]}
        readiness = Mock()
        readiness.snapshot.side_effect = lambda: health
        translator = Mock(side_effect=fake_translation)
        analyzer = Mock(side_effect=fake_analysis)
        app = self.app(readiness=readiness, translator=translator, analyzer=analyzer)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.until(lambda: app.snapshot()['translation']['state'] == 'waiting'
                   and app.snapshot()['translation']['error'] is not None)
        app.stop()
        self.wait(app)
        health['checks'][0]['reachable'] = True
        self.assertFalse(app._continuous_step(True, app.state['lines'][-1]['id']))
        app.cloud_scope.reserve.assert_not_called()
        translator.assert_not_called()
        analyzer.assert_not_called()
        state = app.snapshot()
        self.assertEqual('paused', state['translation']['state'])
        self.assertEqual('stopped', state['processing_stop_status'])
        for kind in ('analysis', 'translation'):
            self.assertIsNone(state[kind]['schedule']['due_at'])
            self.assertIsNone(state[kind]['schedule']['retry']['next_at'])
        self.assertFalse(state['translation']['retry_required'])
        for action in (app.retry_analysis, app.retry_translation):
            with self.assertRaises(RuntimeError):
                action()

    def test_close_timeout_keeps_translation_owned_without_publishing_future_jobs(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def translate(plan, **options):
            entered.set()
            self.assertTrue(release.wait(6))
            return fake_translation(plan, **options)
        translator = Mock(side_effect=translate)
        app = self.app(translator=translator, translation_interval=.01)
        app.start({'provider': 'openai'}, replay=self.audio, pace=1)
        try:
            self.assertTrue(entered.wait(4))
            self.assertFalse(app.close(.01))
            self.assertTrue(app.snapshot()['translation']['worker_alive'])
            self.assertTrue(app.snapshot()['processing_active'])
            with self.assertRaises(RuntimeError):
                app.retry_translation()
        finally:
            release.set()
            self.wait(app)
        self.assertEqual(1, translator.call_count)
        self.assertTrue(app.close(.1))

    def test_stop_retains_final_partial_and_pending_translation_after_inflight(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        through = []
        def translate(plan, **options):
            through.append(plan['target_through_seconds'])
            if len(through) == 1:
                entered.set()
                self.assertTrue(release.wait(6))
            return fake_translation(plan, **options)
        app = self.app(translator=translate, translation_interval=.01)
        app.start({'provider': 'openai'}, replay=self.wave(6), pace=1)
        try:
            self.assertTrue(entered.wait(4))
            self.until(lambda: app.snapshot()['capture']['audio_seconds'] >= 3.5)
            app.stop()
            app.source_thread.join(3)
            self.assertLess(app.snapshot()['asr']['through_seconds'], 3.5)
            self.assertEqual('stopping', app.snapshot()['processing_stop_status'])
        finally:
            release.set()
            self.wait(app)
        self.assertEqual([1], through)
        state = app.snapshot()
        self.assertGreater(state['translation']['pending_lines'], 0)
        self.assertEqual('paused', state['translation']['state'])
        self.assertFalse(state['translation']['retry_required'])
        self.assertFalse(state['translation']['completion_confirmed'])
        self.assertEqual('stopped', state['processing_stop_status'])
        self.assertEqual('stopped', state['translation']['schedule']['reason'])
        self.assertEqual([], state['asr']['failed_chunks'])
        self.assertGreater(app.audio_queue.qsize(), 0)
        with self.assertRaises(RuntimeError):
            app.retry_translation()
        self.assertEqual(112000, (app.session_dir / 'audio/raw.pcm').stat().st_size)

    def test_stop_after_scope_reservation_blocks_unsent_translation(self):
        entered, release = threading.Event(), threading.Event()
        def reserve(*args):
            if app.cloud_kind == 'translation':
                entered.set()
                self.assertTrue(release.wait(5))
            return {'approved': True}
        translator = Mock(side_effect=fake_translation)
        def transcribe(chunk, *args):
            self.assertTrue(app.source_done.wait(3))
            return fake_asr(chunk, *args)
        app = self.app(translator=translator, transcriber=transcribe)
        app.cloud_scope.reserve.side_effect = reserve
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        try:
            self.assertTrue(entered.wait(3))
            app.stop()
        finally:
            release.set()
            self.wait(app)
        translator.assert_not_called()
        state = app.snapshot()
        self.assertEqual('paused', state['translation']['state'])
        self.assertEqual(3, state['translation']['pending_lines'])
        self.assertFalse(state['translation']['retry_required'])
        self.assertIsNone(state['translation']['schedule']['retry']['next_at'])
        events = app.result_dir / 'generation-events.jsonl'
        self.assertFalse(events.exists())
        self.assertFalse((app.result_dir / 'translation-history.jsonl').exists())

    def test_translation_requests_count_in_cost_report_including_failed_reservation(self):
        import hashlib
        for failed in (False, True):
            with self.subTest(failed=failed):
                ledger = {'requests': {}}
                def translate(plan, **options):
                    directory = options['out_dir'] / 'batch'
                    directory.mkdir(parents=True)
                    payload = {'model': 'synthetic-translation-only'}
                    (directory / 'payload.json').write_text(json.dumps(payload))
                    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                    ledger['requests'][fingerprint] = {'fingerprint': fingerprint,
                        'state': 'failed' if failed else 'completed', 'charged_nanodollars': 250000}
                    if failed:
                        raise RuntimeError('synthetic outcome unknown')
                    return {**fake_translation(plan, **options), 'cost_usd': .00025}
                app = self.app(translator=translate)
                with patch('event_insights_cloud._load_ledger', return_value=ledger), \
                        patch('event_insights_cloud.budget_status', return_value={'budget_usd': 10}):
                    app.start({'provider': 'openai'}, replay=self.audio, pace=0)
                    self.wait(app)
                report = json.loads((app.result_dir / 'cost-report.json').read_text())
                self.assertEqual(1, report['matching_request_count'])
                self.assertEqual(.00025 if failed else 0, report['retained_reservation_usd'])
                self.assertEqual(None if failed else .00025, report['additional_api_usd'])

    def test_late_measurement_failure_does_not_retry_already_published_sources(self):
        attempts = []
        original_append = live.append_json
        failed = False
        def transcribe(chunk, *args):
            self.assertTrue(app.source_done.wait(3))
            return fake_asr(chunk, *args)
        def translate(plan, **options):
            attempts.append((list(plan['target_source_ids']), options['retry_failed']))
            if len(attempts) == 1:
                with app.lock:
                    app.state['lines'].append({'id': 'c999999-l0000', 'text': 'New synthetic source.',
                        'language': 'en', 'start_seconds': 3, 'end_seconds': 4, 'uncertain': False})
                    app.state['asr']['through_seconds'] = 4
            return fake_translation(plan, **options)
        def append(path, value):
            nonlocal failed
            if Path(path).name == 'measurements.jsonl' and value.get('stage') == 'translation' and not failed:
                failed = True
                raise OSError('synthetic late measurement write failure')
            return original_append(path, value)
        app = self.app(transcriber=transcribe, translator=translate)
        with patch.object(live, 'append_json', side_effect=append):
            app.start({'provider': 'openai'}, replay=self.audio, pace=0)
            self.wait(app)
            saved = deepcopy(app.state['translation']['blocks'])
            state = app.snapshot()['translation']
            self.assertEqual('failed', state['state'])
            self.assertIn('synthetic late measurement write failure', state['error'])
            self.assertTrue(state['retry_required'])
            self.assertEqual(1, state['pending_lines'])
            self.assertEqual(1, len(attempts))
            app.retry_translation()
            self.wait(app)
        self.assertEqual([(['c000000-l0000', 'c000001-l0000', 'c000002-l0000'], False),
                          (['c999999-l0000'], True)], attempts)
        state = app.snapshot()['translation']
        self.assertEqual(saved, state['blocks'][:len(saved)])
        self.assertEqual(0, state['pending_lines'])
        self.assertEqual('completed', state['state'])
        history = [json.loads(row) for row in (app.result_dir / 'translation-history.jsonl').read_text().splitlines()]
        self.assertEqual(state['blocks'], [block for item in history for block in item['blocks']])


    def test_failed_history_write_does_not_advance_translation_coverage(self):
        original = live.append_json
        def append(path, value):
            if Path(path).name == 'translation-history.jsonl':
                raise OSError('synthetic full disk')
            return original(path, value)
        app = self.app()
        with patch.object(live, 'append_json', side_effect=append):
            app.start({'provider': 'openai'}, replay=self.audio, pace=0)
            self.wait(app)
        self.assertEqual([], app.state['translation']['blocks'])
        self.assertEqual(3, app.snapshot()['translation']['pending_lines'])
        self.assertTrue(app.state['translation']['retry_required'])
        self.assertEqual(96000, (app.session_dir / 'audio/raw.pcm').stat().st_size)

    def test_local_mode_does_not_claim_continuous_translation(self):
        translator = Mock(side_effect=AssertionError('unexpected cloud translation'))
        app = self.app(translator=translator)
        app.start({'provider': 'local'}, replay=self.audio, pace=0)
        self.wait(app)
        self.assertFalse(app.snapshot()['translation']['enabled'])
        translator.assert_not_called()

    def test_transient_failures_recover_same_frozen_input_without_losing_source(self):
        from event_insights_cloud import CloudError
        attempts, analyses = [], []
        def translate(plan, **options):
            attempts.append((deepcopy(plan), options['retry_failed']))
            if len(attempts) == 1:
                raise CloudError('通信失敗', category='transport', retryable=True)
            return fake_translation(plan, **options)
        def analyze(*args, **options):
            analyses.append(options.get('retry_failed', False))
            if len(analyses) == 1:
                raise CloudError('一時的な制限', category='rate_limit', retryable=True, http_status=429)
            return fake_analysis(*args, **options)
        app = self.app(translator=translate, analyzer=analyze)
        with patch.object(live, 'AUTO_RETRY_BASE_SECONDS', .01), patch.object(live.random, 'uniform', return_value=1):
            app.start({'provider': 'openai'}, replay=self.audio, pace=0)
            self.wait(app)
        state = app.snapshot()
        self.assertEqual([False, True], [manual for _, manual in attempts])
        self.assertEqual([False, True], analyses[:2])
        self.assertFalse(any(analyses[2:]), 'Newer source may get a fresh final analysis after the frozen retry')
        self.assertEqual(attempts[0][0], attempts[1][0])
        self.assertEqual(0, state['translation']['pending_lines'])
        self.assertEqual('complete', state['translation']['schedule']['state'])
        self.assertEqual(1, state['translation']['schedule']['retry']['attempts'])
        self.assertEqual('completed', state['asr']['state'])
        events = [json.loads(row) for row in (app.result_dir / 'generation-events.jsonl').read_text().splitlines()]
        self.assertEqual({'analysis', 'translation'}, {event['stage'] for event in events})
        self.assertTrue(all(event['error']['retryable'] for event in events))

    def test_retry_limit_preserves_failure_pending_ids_and_final_state(self):
        from event_insights_cloud import CloudError
        translate = Mock(side_effect=CloudError('通信失敗', category='transport', retryable=True))
        app = self.app(translator=translate)
        with patch.object(live, 'AUTO_RETRY_BASE_SECONDS', .01), patch.object(live.random, 'uniform', return_value=1):
            app.start({'provider': 'openai'}, replay=self.audio, pace=0)
            self.wait(app)
        state = app.snapshot()
        self.assertEqual(4, translate.call_count)
        self.assertEqual(3, state['translation']['pending_lines'])
        self.assertEqual([], state['translation']['blocks'])
        self.assertEqual('failed', state['translation']['state'])
        self.assertEqual('blocked', state['translation']['schedule']['state'])
        self.assertTrue(state['translation']['schedule']['retry']['exhausted'])
        self.assertTrue(state['translation']['retry_required'])
        self.assertIn('失敗', state['message'])

    def test_retry_after_wait_can_be_paused_and_manual_does_not_bypass_server_delay(self):
        from event_insights_cloud import CloudError
        translate = Mock(side_effect=CloudError('一時制限', category='rate_limit', retryable=True,
                                               http_status=429, retry_after_seconds=15))
        app = self.app(translator=translate)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.until(lambda: app.snapshot()['translation']['schedule']['reason'] == 'retry')
        schedule = app.snapshot()['translation']['schedule']
        self.assertEqual('waiting', schedule['state'])
        self.assertEqual(15, schedule['wait_seconds'])
        self.assertGreater(schedule['remaining_seconds'], 13)
        app.pause_retries('translation')
        self.wait(app)
        self.assertEqual(1, translate.call_count)
        self.assertTrue(app.snapshot()['translation']['schedule']['retry']['paused'])
        app.retry_translation()
        self.until(lambda: app.snapshot()['translation']['schedule']['state'] == 'waiting')
        self.assertGreater(app.snapshot()['translation']['schedule']['remaining_seconds'], 12)
        self.assertEqual(1, translate.call_count)
        app.pause_retries('translation')
        app.worker.join(3)
        self.assertFalse(app.worker.is_alive())

    def test_long_server_delay_and_nonretryable_errors_do_not_loop(self):
        from event_insights_cloud import CloudError, BudgetExceededError
        errors = [CloudError('未確認429', http_status=429),
                  CloudError('quota', category='quota'), CloudError('key', category='authentication'),
                  CloudError('bad JSON', category='invalid_response'), BudgetExceededError('予算上限'),
                  CloudError('long delay', category='server', retryable=True, retry_after_seconds=3600)]
        for error in errors:
            with self.subTest(category=error.category):
                translate = Mock(side_effect=error)
                app = self.app(translator=translate)
                app.start({'provider': 'openai'}, replay=self.audio, pace=0)
                self.wait(app)
                self.assertEqual(1, translate.call_count)
                schedule = app.snapshot()['translation']['schedule']
                self.assertEqual('blocked', schedule['state'])
                self.assertIsNone(schedule['remaining_seconds'])
                self.assertEqual(error.category, schedule['error']['category'])

    def test_schedule_reports_eligibility_not_completion_and_saved_views(self):
        app = self.app()
        self.assertEqual('idle', app.snapshot()['translation']['schedule']['state'])
        app.state['session'] = {'id': 'synthetic'}
        app.state['translation']['enabled'] = True
        app.state['lines'] = [{'id': 'one', 'text': 'Synthetic source.', 'start_seconds': 0,
                              'end_seconds': 1, 'language': 'en', 'uncertain': False}]
        app.state['asr']['through_seconds'] = 3
        self.assertEqual('saved_view', app.snapshot()['translation']['schedule']['reason'])
        app.result_dir = self.root / 'synthetic-result'
        app._generation_last_started['translation'] = time.monotonic()
        schedule = app.snapshot()['translation']['schedule']
        self.assertEqual('waiting', schedule['state'])
        self.assertGreater(schedule['remaining_seconds'], 59)
        self.assertEqual(60, schedule['wait_seconds'])
        app.cloud_worker = Mock()
        app.cloud_worker.is_alive.return_value = True
        app.cloud_kind = 'analysis'
        schedule = app.snapshot()['translation']['schedule']
        self.assertEqual(('busy', 'shared_slot', None), (schedule['state'], schedule['reason'], schedule['remaining_seconds']))
        app.cloud_worker = None
        app.source_done.set()
        self.assertEqual('due', app.snapshot()['translation']['schedule']['state'])
        app.result_dir = None  # This hand-built state has no writable session.

    def test_offline_during_recovery_keeps_original_job_and_attempt_budget(self):
        from event_insights_cloud import CloudError
        health = {'checks': [{'id': 'network', 'reachable': True}]}
        readiness = Mock()
        readiness.snapshot.side_effect = lambda: health
        attempts, attempt_budgets = [], []
        def translate(plan, **options):
            attempts.append((deepcopy(plan), options['retry_failed']))
            attempt_budgets.append(app._recovery['translation']['attempts'])
            if len(attempts) == 1:
                health['checks'][0]['reachable'] = False
                raise CloudError('通信失敗', category='transport', retryable=True)
            return fake_translation(plan, **options)
        app = self.app(readiness=readiness, translator=translate)
        with patch.object(live, 'AUTO_RETRY_BASE_SECONDS', .01), patch.object(live, 'OFFLINE_RECHECK_SECONDS', .02):
            app.start({'provider': 'openai'}, replay=self.audio, pace=0)
            self.until(lambda: app.snapshot()['translation']['schedule']['reason'] == 'offline')
            self.assertEqual(1, len(attempts))
            self.assertEqual(0, app.snapshot()['translation']['schedule']['retry']['attempts'])
            health['checks'][0]['reachable'] = True
            self.wait(app)
        self.assertEqual([False, True], [retry for _, retry in attempts[:2]])
        self.assertFalse(any(retry for _, retry in attempts[2:]),
                         'Source arriving after the initial request gets a fresh final batch')
        self.assertEqual(attempts[0][0], attempts[1][0])
        self.assertEqual([0, 1], attempt_budgets[:2])
        self.assertTrue(all(value == 0 for value in attempt_budgets[2:]))
        covered = [identity for block in app.snapshot()['translation']['blocks'] for identity in block['source_ids']]
        self.assertEqual([row['id'] for row in app.state['lines']], covered)

    def test_retry_window_expires_while_waiting_without_resending(self):
        from event_insights_cloud import CloudError
        translate = Mock(side_effect=CloudError('一時制限', category='server', retryable=True, retry_after_seconds=15))
        app = self.app(translator=translate)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.until(lambda: app.snapshot()['translation']['schedule']['reason'] == 'retry')
        with app.lock:
            app._recovery['translation']['started'] = time.monotonic() - live.AUTO_RETRY_WINDOW_SECONDS - 1
        self.wait(app)
        self.assertEqual(1, translate.call_count)
        self.assertTrue(app.snapshot()['translation']['schedule']['retry']['exhausted'])
        self.assertEqual('failed', app.snapshot()['translation']['state'])

    def test_manual_retry_intent_and_input_survive_offline_wait_for_both_stages(self):
        from event_insights_cloud import CloudError
        for kind in ('analysis', 'translation'):
            with self.subTest(stage=kind):
                health = {'checks': [{'id': 'network', 'reachable': True}]}
                readiness = Mock()
                readiness.snapshot.side_effect = lambda: health
                calls = []
                def generate(*args, **options):
                    calls.append((deepcopy(args), options.get('retry_failed', False)))
                    if len(calls) == 1:
                        raise CloudError('前回のAPI結果は未確認')
                    return (fake_analysis if kind == 'analysis' else fake_translation)(*args, **options)
                app = self.app(readiness=readiness, **({'analyzer': generate} if kind == 'analysis' else {'translator': generate}))
                with patch.object(live, 'OFFLINE_RECHECK_SECONDS', .02):
                    app.start({'provider': 'openai'}, replay=self.audio, pace=0)
                    self.wait(app)
                    self.assertEqual(1, len(calls))
                    health['checks'][0]['reachable'] = False
                    (app.retry_analysis if kind == 'analysis' else app.retry_translation)()
                    self.until(lambda: app.snapshot()[kind]['schedule']['reason'] == 'offline')
                    self.assertEqual(1, len(calls), 'Connectivity precheck must not send the manual retry')
                    with app.lock:
                        app.state['lines'].append({'id': 'c999999-l0000', 'text': 'New synthetic source.',
                                                  'language': 'en', 'start_seconds': 3, 'end_seconds': 4,
                                                  'uncertain': False})
                        app.state['asr']['through_seconds'] = 4
                    health['checks'][0]['reachable'] = True
                    self.wait(app)
                self.assertEqual([False, True], [retry for _, retry in calls[:2]])
                self.assertEqual(calls[0][0], calls[1][0], 'The admitted failed job stays frozen across reconnection')
                self.assertFalse(any(retry for _, retry in calls[2:]))
                self.assertEqual('completed', app.snapshot()[kind]['state'])

    def boundary_app(self, text='We can act only if', through=1):
        app = self.app()
        app.result_dir = self.root / ('boundary-' + str(len(self.apps)))
        app.result_dir.mkdir()
        self.addCleanup(setattr, app, 'result_dir', None)
        app.state['session'] = {'id': 'synthetic-boundary'}
        app.state['translation'].update(enabled=True, state='waiting')
        app.state['asr'].update(state='waiting', through_seconds=through)
        app.state['lines'] = [{'id': 'c000000-l0000', 'text': text, 'start_seconds': 0,
                              'end_seconds': 1, 'language': 'en', 'uncertain': False}]
        app._analysis_last_source_id = 'c000000-l0000'
        app._generation_last_started['translation'] = time.monotonic() - 61
        return app

    def startup_app(self, text='A finished sentence.', through=3):
        app = self.boundary_app(text=text, through=through)
        app.state['analysis'].update(provider='openai', state='waiting')
        app._analysis_last_source_id = None
        app._generation_last_started = {'analysis': -float('inf'), 'translation': -float('inf')}
        return app

    def test_startup_schedule_translation_due_analysis_waits_then_normal_cadence(self):
        app = self.startup_app()
        state = app.snapshot()
        self.assertEqual('due', state['translation']['schedule']['state'])
        self.assertEqual(('waiting', 'initial_translation', None),
            tuple(state['analysis']['schedule'][key] for key in ('state', 'reason', 'remaining_seconds')))
        with patch.object(app, '_start_cloud_job') as start:
            app._continuous_step(False, 'c000000-l0000')
            self.assertEqual('translation', start.call_args.args[0])
            self.assertEqual('due', app.snapshot()['analysis']['schedule']['state'])
            self.assertGreater(app.snapshot()['translation']['schedule']['remaining_seconds'], 59)
            app._continuous_step(False, 'c000000-l0000')
            self.assertEqual(['translation', 'analysis'], [call.args[0] for call in start.call_args_list])

    def test_initial_boundary_wait_releases_at_existing_processed_audio_timeout(self):
        app = self.startup_app(text='The result depends on', through=1)
        state = app.snapshot()
        self.assertEqual('continuation', state['translation']['schedule']['reason'])
        self.assertEqual('initial_translation', state['analysis']['schedule']['reason'])
        with patch.object(app, '_start_cloud_job') as start:
            self.assertFalse(app._continuous_step(False, 'c000000-l0000'))
            start.assert_not_called()
            self.assertEqual(-float('inf'), app._generation_last_started['translation'])
            app.state['asr']['through_seconds'] = 31
            app._continuous_step(False, 'c000000-l0000')
            self.assertEqual('translation', start.call_args.args[0])
            self.assertEqual('timeout', start.call_args.args[1]['plan']['selection']['group_boundaries'][0]['reason'])
            app._continuous_step(False, 'c000000-l0000')
            self.assertEqual('analysis', start.call_args.args[0])

    def test_native_or_no_eligible_initial_source_does_not_block_analysis_or_finish(self):
        app = self.startup_app()
        app.state['lines'][0].update(language='ja', text='合成の日本語の発言です。')
        self.assertEqual('due', app.snapshot()['analysis']['schedule']['state'])
        with patch.object(app, '_start_cloud_job') as start:
            app._continuous_step(False, 'c000000-l0000')
            self.assertEqual('analysis', start.call_args.args[0])
        app = self.startup_app(text='um')
        app.state['lines'][0]['uncertain'] = True
        with patch.object(app, '_start_cloud_job') as start:
            self.assertFalse(app._continuous_step(True, None))
            start.assert_not_called()
        self.assertEqual(0, app.snapshot()['translation']['pending_lines'])

    def test_initial_translation_preparation_failure_releases_analysis(self):
        app = self.startup_app()
        with patch.object(app, '_prepare_translation', side_effect=ValueError('synthetic invalid plan')), \
                patch.object(app, '_start_cloud_job') as start:
            app._continuous_step(False, 'c000000-l0000')
            start.assert_not_called()
            self.assertEqual('failed', app.state['translation']['state'])
            self.assertEqual(1, app.snapshot()['translation']['pending_lines'])
            self.assertEqual('due', app.snapshot()['analysis']['schedule']['state'])
            app._continuous_step(False, 'c000000-l0000')
            self.assertEqual('analysis', start.call_args.args[0])

    def test_first_translation_retry_wait_does_not_starve_analysis(self):
        from event_insights_cloud import CloudError
        order = []
        def translate(*args, **options):
            order.append('translation')
            raise CloudError('synthetic retry wait', category='rate_limit', retryable=True,
                             http_status=429, retry_after_seconds=15)
        def analyze(*args, **options):
            order.append('analysis')
            return fake_analysis(*args, **options)
        app = self.app(translator=translate, analyzer=analyze)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.until(lambda: 'analysis' in order)
        self.assertEqual(['translation', 'analysis'], order[:2])
        self.assertGreater(app.snapshot()['translation']['schedule']['retry']['remaining_seconds'], 10)
        app.pause_retries('translation')
        self.wait(app)
        self.assertEqual(1, order.count('translation'))
        self.assertEqual(3, app.snapshot()['translation']['pending_lines'])

    def test_manual_analysis_can_bypass_initial_boundary_wait_but_stop_cannot(self):
        app = self.startup_app(text='The result depends on', through=1)
        app.retry_event.set()
        with patch.object(app, '_start_cloud_job') as start:
            app._continuous_step(False, 'c000000-l0000')
            self.assertEqual('analysis', start.call_args.args[0])
        app = self.startup_app()
        app.stop()
        app.retry_event.set()
        app.translation_retry_event.set()
        with patch.object(app, '_start_cloud_job') as start:
            self.assertFalse(app._continuous_step(False, 'c000000-l0000'))
            start.assert_not_called()
        state = app.snapshot()
        for kind in ('translation', 'analysis'):
            self.assertEqual('stopped', state[kind]['schedule']['reason'])

    def test_continuation_wait_neither_calls_cloud_nor_consumes_cadence(self):
        app = self.boundary_app()
        previous = app._generation_last_started['translation']
        state = app.snapshot()['translation']
        self.assertEqual(('waiting', 'continuation', None),
                         (state['schedule']['state'], state['schedule']['reason'], state['schedule']['remaining_seconds']))
        self.assertEqual((1, 0, 1), (state['pending_lines'], state['ready_lines'], state['waiting_lines']))
        with patch.object(app, '_start_cloud_job') as start:
            self.assertFalse(app._continuous_step(False, 'c000000-l0000'))
            start.assert_not_called()
        self.assertEqual(previous, app._generation_last_started['translation'])
        app.cloud_scope.reserve.assert_not_called()
        app.state['lines'].append({'id': 'c000001-l0000', 'text': 'everyone agrees.',
            'start_seconds': 1, 'end_seconds': 4, 'language': 'en', 'uncertain': False})
        app.state['asr']['through_seconds'] = 6
        app._analysis_last_source_id = 'c000001-l0000'
        self.assertEqual('due', app.snapshot()['translation']['schedule']['state'])
        with patch.object(app, '_start_cloud_job') as start:
            self.assertTrue(app._continuous_step(False, 'c000001-l0000'))
            self.assertEqual(['c000000-l0000', 'c000001-l0000'], start.call_args.args[1]['plan']['target_source_ids'])
        self.assertGreater(app._generation_last_started['translation'], previous)

    def test_wall_clock_and_capture_progress_do_not_force_unrecognized_tail(self):
        app = self.boundary_app()
        app.state['capture']['audio_seconds'] = 900
        with patch.object(live.time, 'monotonic', return_value=time.monotonic() + 10000):
            self.assertEqual('continuation', app.snapshot()['translation']['schedule']['reason'])
            self.assertIsNone(app._prepare_translation())
        app.state['asr']['through_seconds'] = 31
        job = app._prepare_translation()
        self.assertEqual('timeout', job['plan']['selection']['group_boundaries'][0]['reason'])

    def test_boundary_counts_refresh_while_cloud_slot_is_busy(self):
        app = self.boundary_app(text='A finished sentence.', through=3)
        self.assertEqual(1, app.snapshot()['translation']['ready_lines'])
        app.cloud_worker = Mock()
        app.cloud_worker.is_alive.return_value = True
        app.cloud_kind = 'analysis'
        self.addCleanup(setattr, app, 'cloud_worker', None)
        app.state['lines'].append({'id': 'c000001-l0000', 'text': 'And only if',
            'start_seconds': 3, 'end_seconds': 4, 'language': 'en', 'uncertain': False})
        app.state['asr']['through_seconds'] = 4
        state = app.snapshot()['translation']
        self.assertEqual((2, 1, 1), (state['pending_lines'], state['ready_lines'], state['waiting_lines']))
        self.assertEqual('shared_slot', state['schedule']['reason'])

    def test_source_end_waits_for_inflight_asr_and_does_not_consume_empty_preparation(self):
        app = self.boundary_app()
        app.source_done.set()
        app.state['asr']['state'] = 'running'
        self.assertIsNone(app._prepare_translation())
        self.assertEqual('continuation', app.snapshot()['translation']['schedule']['reason'])
        app.state['asr']['state'] = 'waiting'
        self.assertEqual('end_of_input', app._prepare_translation()['plan']['selection']['group_boundaries'][0]['reason'])
        app.state['translation']['state'] = 'waiting'
        previous = app._generation_last_started['translation']
        with patch.object(app, '_prepare_translation', return_value=None), patch.object(app, '_start_cloud_job') as start:
            app._continuous_step(True, 'c000000-l0000')
            start.assert_not_called()
        self.assertEqual(previous, app._generation_last_started['translation'])

    def test_final_fragment_is_saved_with_reason_and_finishes(self):
        plans = []
        def unfinished(chunk, *args):
            result = fake_uncertain_asr(chunk, *args)
            result['chunks'][0]['raw_result']['segments'][0]['text'] = 'The result depends on'
            return result
        def translate(plan, **options):
            plans.append(deepcopy(plan))
            return fake_translation(plan, **options)
        app = self.app(transcriber=unfinished, translator=translate)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.wait(app)
        state = app.snapshot()['translation']
        self.assertEqual(0, state['pending_lines'])
        self.assertEqual('completed', state['state'])
        self.assertEqual(3, state['included_uncertain_lines'])
        self.assertEqual(0, state['excluded_uncertain_lines'])
        self.assertEqual(['end_of_input'], [block['boundary_reason'] for block in state['blocks']])
        source_ids = [row['id'] for row in app.state['lines']]
        self.assertEqual(source_ids, state['blocks'][0]['uncertain_source_ids'])
        self.assertEqual({identity: ['no_speech'] for identity in source_ids},
                         state['blocks'][0]['uncertainty_reasons'])
        history = json.loads((app.result_dir / 'translation-history.jsonl').read_text().splitlines()[0])
        self.assertEqual(plans[0]['selection'], history['selection'])
        self.assertEqual(1, history['selection']['source_policy_version'])
        saved = json.loads((app.result_dir / 'state.json').read_text())
        self.assertEqual(state['blocks'], saved['translation']['blocks'])
        self.assertEqual(source_ids, history['blocks'][0]['uncertain_source_ids'])

    def test_only_uncertain_meaningful_source_triggers_final_analysis_and_translation(self):
        analyses = []
        def analyze(lines, previous, **options):
            analyses.append(deepcopy(lines))
            return fake_analysis(lines, previous, **options)
        app = self.app(transcriber=fake_uncertain_asr, analyzer=analyze)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.wait(app)
        state = app.snapshot()
        self.assertTrue(analyses)
        self.assertTrue(all(row['uncertain'] for batch in analyses for row in batch))
        self.assertEqual('completed', state['analysis']['state'])
        self.assertEqual('completed', state['translation']['state'])
        self.assertEqual(0, state['translation']['pending_lines'])
        self.assertEqual(3, len([identity for block in state['translation']['blocks'] for identity in block['source_ids']]))
        self.assertFalse(state['processing_active'])

    def test_filler_only_source_finishes_with_explicit_exclusion_audit(self):
        def filler(chunk, *args):
            result = fake_uncertain_asr(chunk, *args)
            result['chunks'][0]['raw_result']['segments'][0]['text'] = 'um, uh...'
            return result
        translate = Mock(side_effect=AssertionError('Excluded filler must not create a translation request'))
        analyze = Mock(side_effect=AssertionError('Excluded filler must not create an analysis request'))
        app = self.app(transcriber=filler, translator=translate, analyzer=analyze)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.wait(app)
        state = app.snapshot()
        self.assertEqual(3, len(state['lines']))
        self.assertTrue(all(row['text'] == 'um, uh...' for row in state['lines']))
        self.assertEqual(0, state['translation']['pending_lines'])
        self.assertEqual(0, state['translation']['included_uncertain_lines'])
        self.assertEqual(3, state['translation']['excluded_uncertain_lines'])
        self.assertEqual([{'source_id': row['id'], 'reason': 'filler_only', 'duplicate_of': None}
                          for row in state['lines']], state['translation']['excluded_sources'])
        self.assertFalse(state['processing_active'])
        translate.assert_not_called()
        analyze.assert_not_called()

    def test_reason_only_source_change_rejects_inflight_translation_publication(self):
        from lecture_translation import TranslationResponseError
        app = self.boundary_app(text='Do not remove 17 cases.', through=3)
        app.state['lines'][0].update(uncertain=True, doubt_reasons=['no_speech'])
        prepared = app._prepare_translation()
        self.assertIsNotNone(prepared)
        frozen = deepcopy(prepared['plan'])
        app.state['lines'][0]['doubt_reasons'] = ['repetition']
        with self.assertRaisesRegex(TranslationResponseError, '変化'):
            app._translate(prepared)
        self.assertEqual(frozen, prepared['plan'])
        self.assertEqual([], app.state['translation']['blocks'])
        self.assertEqual(1, app.snapshot()['translation']['pending_lines'])
        self.assertFalse((app.result_dir / 'translation-history.jsonl').exists())

    def test_failed_asr_span_splits_translation_and_retains_failed_evidence(self):
        def broken(chunk, *args):
            if chunk['index'] == 1:
                raise ValueError('synthetic recognition failure')
            result = fake_asr(chunk, *args)
            result['chunks'][0]['raw_result']['segments'][0]['text'] = 'An unfinished condition'
            return result
        app = self.app(transcriber=broken)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.wait(app)
        state = app.snapshot()
        self.assertEqual([1], [row['index'] for row in state['asr']['failed_chunks']])
        self.assertEqual('failed', state['asr']['state'])
        self.assertEqual([['c000000-l0000'], ['c000002-l0000']],
                         [block['source_ids'] for block in state['translation']['blocks']])
        self.assertEqual('source_gap', state['translation']['blocks'][0]['boundary_reason'])


if __name__ == '__main__':
    unittest.main()
