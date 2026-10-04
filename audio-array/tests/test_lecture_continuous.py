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
        eligible = [row['id'] for row in state['lines'] if not row['uncertain'] and row['language'] != 'ja']
        covered = [identity for block in state['translation']['blocks'] for identity in block['source_ids']]
        self.assertEqual(eligible, covered)
        self.assertEqual(18, len(covered))
        self.assertEqual(1, state['translation']['native_lines'])
        self.assertEqual(1, state['translation']['excluded_uncertain_lines'])
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
        app.start({'provider': 'openai'}, replay=self.audio, pace=1)
        try:
            self.assertTrue(entered.wait(4))
            self.until(lambda: app.snapshot()['asr']['through_seconds'] == 3)
            self.assertTrue(app.worker.is_alive())
            state = app.snapshot()
            self.assertTrue(state['translation']['worker_alive'])
            self.assertFalse(state['analysis']['worker_alive'])
            self.assertFalse(state['translation']['completion_confirmed'])
            self.assertEqual(3, state['translation']['pending_lines'])
            self.assertLess(plans[0]['through_seconds'], 3)
            with self.assertRaises(RuntimeError):
                app.start({'provider': 'off'}, replay=self.audio)
        finally:
            release.set()
            self.wait(app)
        self.assertEqual(1, peak)
        self.assertEqual(['analysis', 'translation', 'analysis', 'translation'], order)
        self.assertEqual(0, app.snapshot()['translation']['pending_lines'])
        self.assertEqual(3, len({identity for block in app.state['translation']['blocks'] for identity in block['source_ids']}))

    def test_cloud_failure_preserves_pending_and_requires_explicit_translation_retry(self):
        attempts = []
        def translate(plan, **options):
            attempts.append((deepcopy(plan), options['retry_failed']))
            if not options['retry_failed']:
                raise RuntimeError('synthetic API outcome unknown')
            return fake_translation(plan, **options)
        app = self.app(translator=translate)
        app.start({'provider': 'openai'}, replay=self.audio, pace=0)
        self.wait(app)
        state = app.snapshot()
        self.assertEqual('completed', state['asr']['state'])
        self.assertEqual('failed', state['translation']['state'])
        self.assertEqual(3, state['translation']['pending_lines'])
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

    def test_stop_drains_final_partial_chunk_after_inflight_translation(self):
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
        app.start({'provider': 'openai'}, replay=self.audio, pace=1)
        try:
            self.assertTrue(entered.wait(4))
            self.until(lambda: app.snapshot()['capture']['audio_seconds'] >= 1.5)
            app.stop()
            app.source_thread.join(3)
            self.until(lambda: app.snapshot()['asr']['through_seconds'] == 1.5)
        finally:
            release.set()
            self.wait(app)
        self.assertEqual([1, 1.5], through)
        self.assertEqual(0, app.snapshot()['translation']['pending_lines'])
        self.assertEqual(48000, (app.session_dir / 'audio/raw.pcm').stat().st_size)

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


if __name__ == '__main__':
    unittest.main()
