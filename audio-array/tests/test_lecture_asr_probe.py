import builtins
import hashlib
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_asr_probe as probe


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        patcher = patch.object(probe, 'REPO', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.audio = self.root / 'synthetic.wav'
        self.frames = 7 * probe.RATE + 1
        with wave.open(str(self.audio), 'wb') as audio:
            audio.setparams((1, 2, probe.RATE, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x01\x00' * self.frames)
        model = self.root / 'model'
        model.mkdir()
        (model / 'config.json').write_text('{}')
        (model / 'weights.npz').write_bytes(b'fake model')
        self.metadata = self.root / 'model.json'
        self.metadata.write_text(json.dumps({'local_path': str(model)}))
        self.output = self.root / 'results/audio-experiments/probe'
        self.now = 0
        self.observed_frames = []

    def clock(self):
        return self.now

    def fake(self, chunk, destination, language):
        self.assertEqual('en', language)
        with wave.open(chunk['path'], 'rb') as audio:
            self.observed_frames.append(audio.getnframes())
            self.assertEqual(b'\x01\x00' * audio.getnframes(), audio.readframes(audio.getnframes()))
        self.now += 1
        report = {'inference_queue_wait_seconds': .25, 'inference_seconds': .5,
                  'chunks': [{'raw_result': {'text': 'synthetic'}}]}
        destination.write_text(json.dumps(report))
        return report

    def execute(self, **kwargs):
        return probe.execute(self.audio, self.metadata, self.output,
            transcriber=self.fake, clock=self.clock, **kwargs)

    def test_module_import_is_standalone_and_does_not_load_inference(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name in ('lecture_experiment', 'lecture_live', 'transcribe_local', 'mlx_whisper'):
                raise AssertionError('Unexpected import: ' + name)
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', guarded):
            namespace = runpy.run_path(probe.__file__)
        self.assertIn('execute', namespace)

    def test_inspection_hashes_whole_source_and_selects_exact_frames(self):
        result = probe.inspect_audio(self.audio, .0625625)
        self.assertEqual(1001, result['selected_frames'])
        self.assertEqual(self.frames, result['frames'])
        self.assertEqual(hashlib.sha256(self.audio.read_bytes()).hexdigest(), result['sha256'])
        self.assertEqual(result, probe.inspect_audio(self.audio, result['selected_seconds']))

    def test_inspection_rejects_truncated_invalid_and_subframe_audio(self):
        for seconds in (0, -1, float('nan'), float('inf'), .000001):
            with self.subTest(seconds=seconds), self.assertRaises(probe.ExperimentError):
                probe.inspect_audio(self.audio, seconds)
        self.audio.write_bytes(self.audio.read_bytes()[:-1])
        with self.assertRaisesRegex(probe.ExperimentError, 'truncated'):
            probe.inspect_audio(self.audio)
        self.audio.write_bytes(b'not a WAV')
        with self.assertRaises(probe.ExperimentError):
            probe.inspect_audio(self.audio)

    def test_inspection_rejects_source_identity_change(self):
        with patch.object(probe, '_identity', side_effect=[(1, 2, 3, 4), (1, 2, 3, 5)]):
            with self.assertRaisesRegex(probe.ExperimentError, 'changed during inspection'):
                probe.inspect_audio(self.audio)

    def test_failed_manifest_replace_preserves_previous_json_and_cleans_temporary(self):
        destination = self.root / 'manifest.json'
        probe._save(destination, {'status': 'pending'})
        with patch.object(Path, 'replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                probe._save(destination, {'status': 'completed'})
        self.assertEqual({'status': 'pending'}, json.loads(destination.read_text()))
        self.assertEqual([], list(self.root.glob('.manifest.json.*')))

    def test_frame_partition_preserves_single_frame_tail(self):
        self.assertEqual([(0, 3 * probe.RATE), (3 * probe.RATE, 6 * probe.RATE),
                          (6 * probe.RATE, self.frames)], probe.frame_ranges(self.frames, 3))
        result = self.execute(chunk_seconds=[3])
        self.assertEqual([self.frames, 48000, 48000, 16001], self.observed_frames)
        self.assertEqual('completed', result['status'])
        self.assertEqual(self.frames / probe.RATE, result['runs'][0]['schedule']['covered_audio_seconds'])
        self.assertEqual(.75, result['runs'][0]['calls'][0]['service_seconds'])

    def test_causal_backlog_and_duration_weighted_wait(self):
        calls = [{'index': i, 'start_frame': a * probe.RATE, 'end_frame': b * probe.RATE,
                  'status': 'completed', 'service_seconds': service}
                 for i, (a, b, service) in enumerate([(0, 3, 4), (3, 6, 4), (6, 7, 1)])]
        schedule = probe.simulate_publication(calls)
        self.assertEqual([7, 11, 12], [event['estimated_publication_seconds'] for event in schedule['events']])
        self.assertEqual([0, 1, 4], [event['estimated_backlog_seconds'] for event in schedule['events']])
        self.assertAlmostEqual((3 * 5.5 + 3 * 6.5 + 1 * 5.5) / 7,
                               schedule['duration_weighted_mean_audio_wait_seconds'])

    def test_rolling_is_causal_and_tail_coverage_is_counted_once(self):
        result = self.execute(chunk_seconds=[3], window_seconds=5)
        calls = result['runs'][0]['calls']
        self.assertEqual([0, 16000, self.frames - 80000], [call['start_frame'] for call in calls])
        self.assertEqual([48000, 96000, self.frames], [call['end_frame'] for call in calls])
        self.assertEqual([0, 48000, 96000], [call['new_audio_start_frame'] for call in calls])
        self.assertEqual([self.frames, 48000, 80000, 80000], self.observed_frames)
        schedule = result['runs'][0]['schedule']
        self.assertEqual(self.frames / probe.RATE, schedule['covered_audio_seconds'])
        expected = (3 * 2.25 + 3 * 2.25 + (1 + 1 / probe.RATE) * (.75 + (1 + 1 / probe.RATE) / 2)) / (self.frames / probe.RATE)
        self.assertAlmostEqual(expected, schedule['duration_weighted_mean_audio_wait_seconds'])
        self.assertIn('Do not concatenate', result['configuration']['output_semantics'])
        self.assertEqual('rolling_provisional_snapshots', result['runs'][0]['mode'])
        self.assertEqual(64, len(result['probe_script_sha256']))

    def test_rolling_startup_grows_before_reaching_window_and_has_no_gaps(self):
        calls = probe.planned_calls(16 * probe.RATE + 1, 3, 15)
        self.assertEqual([0, 0, 0, 0, 0, probe.RATE + 1], [call['start_frame'] for call in calls])
        self.assertEqual(16 * probe.RATE + 1, calls[-1]['end_frame'])
        for call in calls:
            self.assertLessEqual(call['start_frame'], call['new_audio_start_frame'])
            self.assertEqual(call['end_frame'], call['new_audio_end_frame'])
        with self.assertRaises(probe.ExperimentError):
            probe.planned_calls(self.frames, 5, 3)

    def test_failure_keeps_completed_failed_pending_and_other_run(self):
        original = self.fake
        count = 0
        def failing(chunk, destination, language):
            nonlocal count
            count += 1
            if count == 3:
                raise RuntimeError('synthetic failure')
            return original(chunk, destination, language)
        with patch.object(self, 'fake', failing):
            result = self.execute(chunk_seconds=[2, 5])
        self.assertEqual('incomplete', result['status'])
        first = result['runs'][0]
        self.assertEqual(['completed', 'failed', 'pending', 'pending'], [c['status'] for c in first['calls']])
        self.assertFalse(first['schedule']['complete'])
        self.assertEqual('completed', result['runs'][1]['status'])
        self.assertEqual(result, json.loads((self.output / 'manifest.json').read_text()))

    def test_failed_warmup_does_not_claim_measured_calls_ran(self):
        with patch.object(self, 'fake', side_effect=RuntimeError('synthetic startup failure')):
            with self.assertRaises(RuntimeError):
                self.execute(chunk_seconds=[3])
        result = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual('incomplete', result['status'])
        self.assertEqual('failed', result['warmup']['status'])
        self.assertEqual('pending', result['runs'][0]['status'])
        self.assertTrue(all(call['status'] == 'pending' for call in result['runs'][0]['calls']))

    def test_repeats_reverse_order_and_warm_only_once(self):
        result = self.execute(chunk_seconds=[2, 5], repeats=2)
        self.assertEqual([2, 5, 5, 2], [run['chunk_seconds'] for run in result['runs']])
        self.assertEqual(1 + 4 + 2 + 2 + 4, len(self.observed_frames))

    def test_refuse_overwrite_and_output_outside_private_root(self):
        self.execute(chunk_seconds=[3])
        before = (self.output / 'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.execute(chunk_seconds=[3])
        self.assertEqual(before, (self.output / 'manifest.json').read_bytes())
        self.output = self.root / 'public'
        with self.assertRaisesRegex(probe.ExperimentError, 'NEW directory'):
            self.execute(chunk_seconds=[3])
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
