"""Synthetic export/round-trip checks; no real source, inference or HTTP listener."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_lecture_demo as fixtures
from lecture_demo import DemoDataError, DemoTimeline
from lecture_demo_export import export_demo


class DemoExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DemoTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.continuous_fixture()
        self.fixture.provisional_fixture()
        self.session = self.fixture.directory
        self.audio = self.fixture.wav_fixture()
        self.fixture.write('cost-report.json', {'confirmed_api_usd': .02,
            'retained_reservation_usd': .2, 'additional_api_usd': None,
            'daily_budget': {'private_ledger_path': '/private/synthetic-ledger.json'}})
        self.fixture.write_rows('generation-events.jsonl', [
            {'stage': 'translation', 'event': 'failed', 'at': 1014,
             'error': {'category': 'server', 'http_status': 503, 'retryable': True,
                       'raw_response': 'PRIVATE_SENTINEL'}, 'auto_attempts': 0}])
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.target = Path(temporary.name) / 'demo'

    def export(self):
        return export_demo(self.session, self.audio, self.target)

    def test_export_keeps_publications_uncertainty_failures_cost_and_audio(self):
        original = DemoTimeline(self.session, self.audio)
        before = {path.name: path.read_bytes() for path in self.session.iterdir()}
        manifest = self.export()
        exported = DemoTimeline(self.target / 'session', self.target / 'audio.wav')
        self.assertEqual(exported.started_at, 0)
        self.assertEqual(exported.duration, original.duration)
        self.assertEqual(exported.audio.data, original.audio.data)
        self.assertEqual(exported.cost['retained_reservation_usd'], .2)
        self.assertIsNone(exported.cost['additional_api_usd'])
        self.assertEqual(exported.cost['confirmed_api_usd'], .02)
        self.assertEqual(exported.failures[0]['error']['http_status'], 503)
        self.assertEqual(manifest['generation_failure_count'], 1)
        for cursor in (0, 3.999, 4, 7, 10, 12, 14, 15, 18, 19, 22, 28, 30, 34.999, 35, 4, 0):
            with self.subTest(cursor=cursor):
                expected, actual = original.snapshot(cursor), exported.snapshot(cursor)
                self.assertEqual(actual['lines'], expected['lines'])
                self.assertEqual(actual['translation']['covered_source_ids'], expected['translation']['covered_source_ids'])
                self.assertEqual(actual['translation']['pending_lines'], expected['translation']['pending_lines'])
                self.assertEqual(actual['provisional_asr']['lines'], expected['provisional_asr']['lines'])
                for kind in ('asr', 'translation', 'analysis', 'provisional_asr'):
                    self.assertEqual(actual[kind]['state'], expected[kind]['state'])
                    self.assertEqual(actual[kind]['schedule']['reason'], expected[kind]['schedule']['reason'])
                for a, b in zip(actual['translation']['blocks'], expected['translation']['blocks']):
                    self.assertEqual(a['text'], b['text'])
                    self.assertEqual(a['published_at'], b['published_at'] - original.started_at)
                for a, b in zip(actual['analysis_history'], expected['analysis_history']):
                    self.assertEqual(a['headline'], b['headline'])
                    self.assertEqual(a['generated_at'], b['generated_at'] - original.started_at)
                    self.assertEqual(a['recorded_generated_at'], b['recorded_generated_at'] - original.started_at)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.session.iterdir()})

    def test_unknown_nested_runtime_fields_cannot_leak(self):
        def contaminate(value):
            if isinstance(value, dict):
                for item in list(value.values()):
                    contaminate(item)
                value['private_new_field'] = {'path': '/Users/private/PRIVATE_SENTINEL',
                                               'payload': 'PRIVATE_SENTINEL'}
            elif isinstance(value, list):
                for item in value:
                    contaminate(item)
        for path in self.session.iterdir():
            if path.suffix not in {'.json', '.jsonl'}:
                continue
            if path.suffix == '.json':
                value = json.loads(path.read_text())
                contaminate(value)
                self.fixture.write(path.name, value)
            else:
                values = [json.loads(line) for line in path.read_text().splitlines()]
                for value in values:
                    contaminate(value)
                self.fixture.write_rows(path.name, values)
        self.export()
        combined = '\n'.join(path.read_text() for path in self.target.rglob('*')
                             if path.is_file() and path.suffix in {'.json', '.jsonl'})
        for forbidden in ('PRIVATE_SENTINEL', '/Users/private', 'private_new_field', 'private_ledger_path'):
            self.assertNotIn(forbidden, combined)

    def test_unexpected_object_in_allowed_scalar_is_rejected_without_output(self):
        state = json.loads((self.session / 'state.json').read_text())
        state['analysis']['model'] = {'private_path': '/Users/private/model'}
        self.fixture.write('state.json', state)
        with self.assertRaises(DemoDataError):
            self.export()
        self.assertFalse(self.target.exists())

    def test_deterministic_export_refuses_to_overwrite(self):
        manifest = self.export()
        first = {str(path.relative_to(self.target)): path.read_bytes()
                 for path in self.target.rglob('*') if path.is_file()}
        with self.assertRaises(DemoDataError):
            self.export()
        second_target = self.target.with_name('another-demo')
        self.assertEqual(manifest, export_demo(self.session, self.audio, second_target))
        self.assertEqual(first, {str(path.relative_to(second_target)): path.read_bytes()
                               for path in second_target.rglob('*') if path.is_file()})


if __name__ == '__main__':
    unittest.main()
