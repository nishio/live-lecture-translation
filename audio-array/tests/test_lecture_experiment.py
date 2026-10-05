import contextlib
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_experiment as experiment


class ExperimentTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.audio = self.root / 'synthetic.wav'
        self.write_audio()
        self.model = self.root / 'model'
        self.model.mkdir()
        (self.model / 'config.json').write_text('{}')
        (self.model / 'weights.npz').write_bytes(b'synthetic-not-a-model')
        self.metadata = self.root / 'model.json'
        self.metadata.write_text(json.dumps({'local_path': str(self.model)}))
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        for patcher in (patch.object(experiment, 'REPO', self.root),
                        contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr)):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)

    def write_audio(self, *, channels=1, rate=16000, width=2, frames=16000):
        with wave.open(str(self.audio), 'wb') as audio:
            audio.setparams((channels, width, rate, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0' * frames * width * channels)

    def invoke(self, action='check', *extra):
        self.stdout.seek(0)
        self.stdout.truncate()
        self.stderr.seek(0)
        self.stderr.truncate()
        return experiment.main([action, str(self.audio), '--model-metadata', str(self.metadata), *extra])

    def plan(self, *extra):
        self.assertEqual(0, self.invoke('check', *extra), self.stderr.getvalue())
        data = json.loads(self.stdout.getvalue())
        return {key: value for key, value in data.items() if key not in ('check_only', 'runtime_and_authorization_checked')}

    def fake_run(self, *, stage='completed', code=0, interrupted=False, write_state=True,
                 mutate_input=False, audio_seconds=1):
        def run(command, log_path, on_started):
            on_started(1234)
            self.assertEqual('0', command[command.index('--port') + 1])
            self.assertNotIn('--open', command)
            results = Path(command[command.index('--results-root') + 1]) / 'Lecture-synthetic'
            results.mkdir()
            if write_state:
                state = {'processing_active': False, **{name: {'state': stage,
                    'completion_confirmed': stage == 'completed', 'pending_lines': 0, 'through_seconds': 1,
                    'audio_seconds': audio_seconds}
                    for name in ('capture', 'asr', 'translation', 'analysis')}}
                (results / 'state.json').write_text(json.dumps(state))
                (results / 'cost-report.json').write_text(json.dumps({'additional_api_usd': 0,
                    'codex_usage': 'not measured', 'electricity': 'not measured'}))
            if mutate_input:
                self.write_audio(frames=8000)
            return {'returncode': code, 'interrupted': interrupted, 'shutdown_confirmed': not interrupted}
        return run

    def test_check_reads_complete_audio_and_never_starts_or_writes(self):
        before = sorted(self.root.rglob('*'))
        with patch.object(experiment, '_preflight', side_effect=AssertionError('runtime touched')), \
                patch.object(experiment, '_run_child', side_effect=AssertionError('child started')):
            result = self.plan('--seconds', '.25')
        self.assertEqual(before, sorted(self.root.rglob('*')))
        self.assertEqual(1, result['input']['duration_seconds'])
        self.assertEqual(4000, result['input']['selected_frames'])
        self.assertEqual(hashlib.sha256(self.audio.read_bytes()).hexdigest(), result['input']['sha256'])
        self.assertEqual('off', result['configuration']['provider'])
        self.assertEqual(0, result['cost']['planned_api_usd'])
        self.assertIn('not current pricing', result['cost']['conditions'])

    def test_help_is_safe_without_audio_or_runtime(self):
        with patch.object(experiment, 'inspect_audio', side_effect=AssertionError('audio read')):
            for args in (['--help'], ['run', '--help']):
                with self.assertRaises(SystemExit) as caught:
                    experiment.main(args)
                self.assertEqual(0, caught.exception.code)

    def test_check_rejects_unsupported_and_empty_audio(self):
        for settings in ({'channels': 2}, {'rate': 44100}, {'width': 1}, {'frames': 0}):
            with self.subTest(settings=settings):
                self.write_audio(**settings)
                self.assertEqual(2, self.invoke())

    def test_check_rejects_truncated_or_non_wav_input(self):
        self.audio.write_bytes(self.audio.read_bytes()[:-8])
        self.assertEqual(2, self.invoke())
        self.assertIn('truncated', self.stderr.getvalue())
        self.audio.write_bytes(b'not audio')
        self.assertEqual(2, self.invoke())

    def test_duration_must_be_positive_and_finite_and_is_bounded_by_file(self):
        for seconds in ('0', '-1', 'nan', 'inf', '.000001'):
            with self.subTest(seconds=seconds):
                self.assertEqual(2, self.invoke('check', '--seconds', seconds))
        self.assertEqual(1, self.plan('--seconds', '500')['input']['selected_seconds'])

    def test_fractional_frame_duration_is_stable_across_repeated_inspection(self):
        self.write_audio(frames=1001)
        original = experiment.inspect_audio(self.audio)
        self.assertEqual(original, experiment.inspect_audio(self.audio, original['selected_seconds']))

    def test_cloud_is_explicit_and_check_does_not_read_authorization(self):
        self.assertEqual(2, self.invoke('check', '--cloud'))
        self.assertEqual(2, self.invoke('check', '--model', 'gpt-6-luna'))
        with patch.object(experiment, '_preflight', side_effect=AssertionError('auth touched')):
            plan = self.plan('--cloud', '--authorization', str(self.root / 'not-read.json'))
        self.assertEqual('gpt-6.1-sol', plan['configuration']['model'])
        self.assertIsNone(plan['cost']['planned_api_usd'])

    def test_command_reuses_existing_replay_with_isolated_port_and_cloud_options(self):
        plan = self.plan('--cloud', '--authorization', str(self.root / 'auth.json'),
                         '--key-file', str(self.root / 'key.env'), '--model', 'gpt-6-luna',
                         '--pace', 'accelerated', '--chunk-seconds', '5')
        command = experiment.command_for(plan, self.root / 'data-run', self.root / 'result-run')
        self.assertEqual('0', command[command.index('--pace') + 1])
        self.assertEqual('gpt-6-luna', command[command.index('--model') + 1])
        self.assertEqual('openai', command[command.index('--provider') + 1])
        self.assertIn('--allow-cloud', command)
        self.assertIn('--exit-after-replay', command)
        self.assertEqual(str(self.audio), command[command.index('--replay') + 1])

    def test_model_preflight_failure_starts_no_process_or_run(self):
        self.metadata.unlink()
        with patch.object(experiment, '_run_child', side_effect=AssertionError('child started')):
            self.assertEqual(2, self.invoke('run'))
        self.assertFalse((self.root / 'results').exists())

    def test_cloud_rejects_unlisted_source_before_asr_and_keeps_ledger_unchanged(self):
        import lecture_cloud_scope as scope
        today = datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat()
        auth = self.root / 'auth.json'
        auth.write_text(json.dumps({'human_approved': True, 'allowed_dates': [today],
            'allowed_models': ['gpt-6.1-sol'], 'microphone_date': today,
            'microphone_max_seconds': 21600, 'replay_sources': []}))
        ledger = self.root / 'scope-ledger'
        with patch.object(scope, 'DEFAULT_LEDGER_DIR', ledger), \
                patch.object(experiment, '_run_child', side_effect=AssertionError('child started')):
            self.assertEqual(2, self.invoke('run', '--cloud', '--authorization', str(auth)))
        self.assertIn('exact input path', self.stderr.getvalue())
        self.assertFalse(ledger.exists())
        self.assertFalse((self.root / 'results').exists())

    def test_run_preserves_same_source_and_uses_new_private_directories(self):
        with patch.object(experiment, '_run_child', side_effect=self.fake_run()):
            self.assertEqual(0, self.invoke('run', '--label', 'baseline'))
            first = Path(json.loads(self.stdout.getvalue())['manifest'])
            self.assertEqual(0, self.invoke('run', '--label', 'five-second', '--chunk-seconds', '5'))
            second = Path(json.loads(self.stdout.getvalue())['manifest'])
        a, b = (json.loads(path.read_text()) for path in (first, second))
        self.assertNotEqual(first.parent, second.parent)
        self.assertEqual(a['input']['sha256'], b['input']['sha256'])
        self.assertEqual('completed', a['status'])
        self.assertEqual(15, a['configuration']['chunk_seconds'])
        self.assertEqual(5, b['configuration']['chunk_seconds'])
        self.assertTrue(first.is_relative_to(self.root / 'results/audio-experiments'))
        self.assertEqual(0, a['cost_report']['additional_api_usd'])

    def test_process_exit_is_not_proof_of_completion(self):
        for options in ({'stage': 'failed'}, {'code': 2}, {'write_state': False}, {'interrupted': True},
                        {'audio_seconds': .5}):
            with self.subTest(options=options), patch.object(experiment, '_run_child', side_effect=self.fake_run(**options)):
                self.assertEqual(2, self.invoke('run'))
                output = json.loads(self.stdout.getvalue())
                self.assertFalse(output['completion_confirmed'])
                self.assertEqual('incomplete', output['status'])
                self.assertTrue(Path(output['manifest']).is_file())

    def test_changed_input_after_processing_is_incomplete(self):
        with patch.object(experiment, '_run_child', side_effect=self.fake_run(mutate_input=True)):
            self.assertEqual(2, self.invoke('run'))
        saved = json.loads(Path(json.loads(self.stdout.getvalue())['manifest']).read_text())
        self.assertFalse(saved['input_unchanged'])
        self.assertFalse(saved['completion_confirmed'])

    def test_refuses_symlinked_output_root_outside_repository(self):
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        (self.root / 'data').symlink_to(elsewhere, target_is_directory=True)
        with patch.object(experiment, '_run_child', side_effect=AssertionError('child started')):
            self.assertEqual(2, self.invoke('run'))
        self.assertEqual([], list(elsewhere.iterdir()))

    def test_existing_replay_engine_with_synthetic_asr_produces_a_checked_result(self):
        import lecture_live as live
        def asr(chunk, _destination, _language):
            duration = chunk['end_seconds'] - chunk['start_seconds']
            return {'chunks': [{'index': 0, 'source_start_seconds': 0, 'source_end_seconds': duration,
                'raw_result': {'language': 'en', 'segments': [{'start': 0, 'end': duration,
                    'text': 'A synthetic sentence for the experiment test.'}]}}]}
        def run(command, _log_path, on_started):
            on_started(1234)
            app = live.LectureApp(data_root=Path(command[command.index('--data-root') + 1]),
                results_root=Path(command[command.index('--results-root') + 1]),
                transcriber=asr, continuous_translation=True)
            try:
                app.start({'provider': 'off', 'language': 'en'}, replay=self.audio, pace=0)
                app.source_thread.join(5)
                app.worker.join(5)
                self.assertFalse(app.worker.is_alive())
            finally:
                complete = app.close(5)
            return {'returncode': 0, 'interrupted': False, 'shutdown_confirmed': complete}
        with patch.object(experiment, '_run_child', side_effect=run), \
                patch.object(live.LocalTranscriber, '__call__', side_effect=AssertionError('real ASR')):
            self.assertEqual(0, self.invoke('run', '--pace', 'accelerated'))
        output = json.loads(self.stdout.getvalue())
        self.assertEqual('completed', output['stages']['asr']['state'])
        manifest = json.loads(Path(output['manifest']).read_text())
        self.assertTrue(Path(manifest['state_path']).is_file())
        self.assertEqual(1, manifest['stages']['asr']['through_seconds'])


if __name__ == '__main__':
    unittest.main()
