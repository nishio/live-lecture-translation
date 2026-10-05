"""Offline estimates use synthetic settings and published aggregate evidence."""
import contextlib
from decimal import Decimal
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_cost_estimate as estimate


class CadenceCostTests(unittest.TestCase):
    def settings(self, translation=60, analysis=120):
        return {'schema_version': 1, 'translation_interval_seconds': translation,
                'analysis_interval_seconds': analysis}

    def test_audited_baseline_and_recommended_scenario(self):
        report = estimate.build_estimate(self.settings(30, 60), 6)
        baseline = report['comparisons'][0]['translation_volume_constant']
        chosen = report['selected']['translation_volume_constant']
        self.assertEqual('1.302315', baseline['usd_per_hour'])
        self.assertEqual('2.340030', chosen['usd_per_hour'])
        self.assertEqual('14.040180', chosen['total_usd'])
        self.assertEqual('1.037715', chosen['increase_usd_per_hour_vs_60_120'])
        self.assertEqual('2.604630', report['selected']['fixed_request_size']['usd_per_hour'])
        self.assertTrue(report['estimate_only'])
        self.assertEqual('0', report['additional_api_cost_of_estimation_usd'])

    def test_translation_volume_and_analysis_frequency_are_separate(self):
        rows = estimate.build_estimate(self.settings())['comparisons']
        self.assertEqual(1, len({r['translation_volume_constant']['translation_output_usd_per_hour'] for r in rows}))
        self.assertEqual(2 * Decimal(rows[0]['translation_volume_constant']['analysis_usd_per_hour']),
                         Decimal(rows[1]['translation_volume_constant']['analysis_usd_per_hour']))
        for row in rows:
            self.assertNotIn('fixed_duration_slot_load', row)
            self.assertTrue(all(Decimal(load) < 1 for load in row['fixed_duration_stage_load'].values()))
        self.assertEqual('0.672909', rows[3]['fixed_duration_stage_load']['translation'])
        self.assertEqual('0.369752', rows[3]['fixed_duration_stage_load']['analysis'])
        self.assertFalse(rows[3]['warnings'])
        shorter = estimate.build_estimate(self.settings(3, 60))['selected']
        self.assertTrue(any('15-second' in note for note in shorter['warnings']))
        self.assertTrue(any(note.startswith('translation:') for note in shorter['warnings']))
        self.assertFalse(any(note.startswith('analysis:') for note in shorter['warnings']))

    def test_invalid_duration_and_settings_cannot_produce_a_quote(self):
        for hours in [0, -1, 'NaN', 'Infinity', '-Infinity', 'not-hours', 25, True]:
            with self.subTest(hours=hours), self.assertRaises(ValueError):
                estimate.build_estimate(self.settings(), hours)
        for translation in [False, 0, 30.0, 3601]:
            with self.subTest(translation=translation), self.assertRaises(ValueError):
                estimate.build_estimate(self.settings(translation, 60))

    def test_cli_json_reads_only_selected_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lecture settings.json'
            path.write_text(json.dumps(self.settings(20, 60)))
            original = path.read_bytes()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, estimate.main(['--settings', str(path), '--hours', '.5', '--json']))
            report = json.loads(out.getvalue())
            self.assertEqual(20, report['selected']['translation_interval_seconds'])
            self.assertEqual('1.322670', report['selected']['translation_volume_constant']['total_usd'])
            self.assertEqual(original, path.read_bytes())
            self.assertEqual([path], list(Path(directory).iterdir()))

    def test_cli_uses_frozen_interval_pair_and_rejects_ambiguous_input(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, estimate.main(['--translation-interval', '30', '--analysis-interval', '60']))
        self.assertIn('$2.34/時', out.getvalue())
        self.assertIn('上下限ではありません', out.getvalue())
        for args in [['--translation-interval', '30'],
                     ['--settings', 'missing.json', '--translation-interval', '30', '--analysis-interval', '60']]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, estimate.main(args))


if __name__ == '__main__':
    unittest.main()
