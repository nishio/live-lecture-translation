"""CPU/fake HTTP verification; no recorder, cloud, ASR or listener is started."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lecture_demo import DemoTimeline, DemoDataError, make_server


class DemoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.lines = [{'id': 'one', 'text': 'original one', 'start_seconds': 0, 'end_seconds': 10, 'language': 'en'},
                      {'id': 'two', 'text': 'original two', 'start_seconds': 10, 'end_seconds': 20, 'language': 'en'}]
        self.write('state.json', {'session': {'id': 'test', 'started_at': 1000, 'language': 'en'}, 'capture': {'audio_seconds': 20},
                    'analysis': {'model': 'gpt-6.1-sol'}, 'lines': [{**line, 'translation_ja': 'future final translation'} for line in self.lines]})
        self.write('cost-report.json', {'confirmed_api_usd': .02})
        self.write('runtime-manifest.json', {'configuration': {'analysis_interval': 90}})
        self.transcripts = [{'chunk': {'end_seconds': 10, 'completed_at': 1010}, 'lines': [self.lines[0]]},
                            {'chunk': {'end_seconds': 20, 'completed_at': 1020}, 'lines': [self.lines[1]]}]
        self.measurements = [{'stage': 'asr', 'published_at': 1012, 'processing_seconds': 2, 'capture_seconds': 10, 'through_seconds': 10},
                             {'stage': 'analysis', 'published_at': 1015, 'processing_seconds': 3, 'capture_seconds': 10, 'through_seconds': 10},
                             {'stage': 'asr', 'published_at': 1022, 'processing_seconds': 2, 'capture_seconds': 20, 'through_seconds': 20},
                             {'stage': 'analysis', 'published_at': 1030, 'processing_seconds': 4, 'capture_seconds': 20, 'through_seconds': 20}]
        self.results = [{'through_seconds': 10, 'generated_at': 900, 'headline': {'text': 'first', 'source_ids': ['one']}, 'source_line_ids': ['one'],
                         'translations': [{'source_id': 'one', 'text': 'first translation'}], 'cache_hit': True},
                        {'through_seconds': 20, 'generated_at': 1029, 'headline': {'text': 'second', 'source_ids': ['two']}, 'source_line_ids': ['one', 'two'],
                         'translations': [{'source_id': 'one', 'text': 'updated translation'}, {'source_id': 'two', 'text': 'second translation'}]}]
        self.write_rows('transcript.jsonl', self.transcripts)
        self.write_rows('measurements.jsonl', self.measurements)
        self.write_rows('analysis-history.jsonl', self.results)
        self.timeline = DemoTimeline(self.directory)

    def write(self, name, value): (self.directory / name).write_text(json.dumps(value))
    def write_rows(self, name, rows): (self.directory / name).write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def test_originals_do_not_arrive_before_asr_publication(self):
        self.assertEqual(self.timeline.snapshot(11.999)['lines'], [])
        self.assertEqual([line['id'] for line in self.timeline.snapshot(12)['lines']], ['one'])
        self.assertEqual([line['id'] for line in self.timeline.snapshot(21.999)['lines']], ['one'])
        self.assertEqual([line['id'] for line in self.timeline.snapshot(22)['lines']], ['one', 'two'])

    def test_translations_and_history_use_publication_not_cached_generation(self):
        before = self.timeline.snapshot(14.999)
        self.assertNotIn('translation_ja', before['lines'][0])
        self.assertEqual(before['analysis_history'], [])
        self.assertIsNone(before['analysis']['result'])
        first = self.timeline.snapshot(15)
        self.assertEqual(first['lines'][0]['translation_ja'], 'first translation')
        self.assertEqual(first['analysis']['generated_at'], 1015)
        self.assertEqual(first['analysis']['result']['recorded_generated_at'], 900)
        self.assertEqual(len(first['analysis_history']), 1)
        self.assertNotIn('translations', first['analysis_history'][0])
        later = self.timeline.snapshot(29.999)
        self.assertEqual(later['lines'][0]['translation_ja'], 'first translation')
        self.assertNotIn('translation_ja', later['lines'][1])
        final = self.timeline.snapshot(30)
        self.assertEqual(final['lines'][0]['translation_ja'], 'updated translation')
        self.assertEqual(final['lines'][1]['translation_ja'], 'second translation')

    def test_analysis_stage_matches_published_result_between_requests(self):
        self.assertEqual(self.timeline.snapshot(11)['analysis']['state'], 'waiting')
        self.assertEqual(self.timeline.snapshot(13)['analysis']['state'], 'running')
        self.assertEqual(self.timeline.snapshot(20)['analysis']['state'], 'completed')
        self.assertEqual(self.timeline.snapshot(28)['analysis']['state'], 'running')
        self.assertEqual(self.timeline.snapshot(30)['analysis']['state'], 'completed')

    def test_returned_snapshots_cannot_mutate_future_or_rewound_state(self):
        final = self.timeline.snapshot(30)
        final['lines'][0]['text'] = 'tampered'; final['analysis']['result']['headline']['text'] = 'tampered'
        self.assertEqual(self.timeline.snapshot(30)['lines'][0]['text'], 'original one')
        self.assertEqual(self.timeline.snapshot(15)['analysis']['result']['headline']['text'], 'first')
        self.assertNotIn('translation_ja', self.timeline.snapshot(12)['lines'][0])

    def test_cost_and_scope_are_read_only_record_metadata(self):
        state = self.timeline.snapshot(30)
        self.assertNotIn('cloud_budget', state); self.assertNotIn('cloud_scope', state)
        self.assertEqual(state['demo']['additional_api_usd'], 0)
        self.assertFalse(state['capabilities']['cloud_enabled'])
        self.assertEqual(self.timeline.metadata()['recorded_api_usd'], .02)
        self.assertEqual(self.timeline.metadata()['cache_hits'], 1)

    def test_missing_or_future_records_fail_instead_of_inventing_time(self):
        self.write_rows('measurements.jsonl', self.measurements[:-1])
        with self.assertRaises(DemoDataError): DemoTimeline(self.directory)
        self.write_rows('measurements.jsonl', self.measurements)
        result = deepcopy(self.results); result[0]['translations'].append({'source_id': 'two', 'text': 'premature'})
        self.write_rows('analysis-history.jsonl', result)
        with self.assertRaises(DemoDataError): DemoTimeline(self.directory)

    def test_invalid_cursor_rejected(self):
        for value in [-1, 31, float('nan'), float('inf'), True, '12']:
            with self.subTest(value=value), self.assertRaises(DemoDataError): self.timeline.snapshot(value)

    def test_block_translations_follow_publication_and_rewind(self):
        result = deepcopy(self.results)
        result[-1]['block_translations'] = [{'text': 'まとまった訳', 'source_ids': ['one', 'two']}]
        self.write_rows('analysis-history.jsonl', result)
        timeline = DemoTimeline(self.directory)
        self.assertNotIn('block_translations', timeline.snapshot(29.999)['analysis']['result'])
        self.assertEqual(timeline.snapshot(30)['analysis']['result']['block_translations'][0]['source_ids'], ['one', 'two'])
        self.assertNotIn('block_translations', timeline.snapshot(15)['analysis']['result'])
        result[0]['block_translations'] = [{'text': '早すぎる', 'source_ids': ['two']}]
        self.write_rows('analysis-history.jsonl', result)
        with self.assertRaises(DemoDataError): DemoTimeline(self.directory)

    def test_simulation_is_labelled_and_preparation_cost_separate(self):
        path = self.directory / 'state.json'
        state = json.loads(path.read_text())
        state['display_simulation'] = {'source_label': '表示シミュレーション', 'timing_note': '保存ASR＋実測生成時間', 'preparation_api_usd': .05}
        self.write('state.json', state)
        timeline = DemoTimeline(self.directory)
        self.assertTrue(timeline.metadata()['display_simulation'])
        self.assertEqual(timeline.metadata()['source_label'], '表示シミュレーション')
        self.assertEqual(timeline.metadata()['preparation_api_usd'], .05)
        self.assertEqual(timeline.metadata()['demo_additional_api_usd'], 0)

    def test_no_input_files_change(self):
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        for seconds in [0, 12, 15, 20, 29.999, 30, 0]: self.timeline.snapshot(seconds)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.directory.iterdir()})

    def test_http_auth_assets_and_post_rejection_without_socket(self):
        class FakeServer:
            def __init__(self, address, handler): self.server_port = 9999; self.handler = handler
        with patch('lecture_demo.ThreadingHTTPServer', FakeServer):
            server, url = make_server(self.timeline, 9999)
        token = parse_qs(urlsplit(url).query)['token'][0]
        def request(path, cookie='', method='GET', host='127.0.0.1:9999'):
            handler = object.__new__(server.handler)
            handler.path = path; handler.headers = {'Host': host, 'Cookie': cookie}; handler.server = server
            handler.wfile = io.BytesIO(); handler.request_version = 'HTTP/1.1'; handler.requestline = method + ' ' + path; handler.command = method
            getattr(handler, 'do_' + method)()
            raw = handler.wfile.getvalue(); header, body = raw.split(b'\r\n\r\n', 1)
            return int(header.split()[1]), header.decode(), body
        self.assertEqual(request('/api/demo')[0], 403)
        status, headers, html = request('/?token=' + token)
        self.assertEqual(status, 200); self.assertIn('lecture_demo=', headers); self.assertNotIn('lecture_session=', headers)
        self.assertIn(b'demo-toolbar', html); self.assertIn('Content-Security-Policy', headers)
        cookie = 'lecture_demo=' + token
        self.assertEqual(request('/api/state?at=12', cookie)[0], 200)
        self.assertEqual(request('/api/state?at=nan', cookie)[0], 400)
        self.assertEqual(request('/api/state?at=0&at=20', cookie)[0], 400)
        self.assertEqual(request('/api/demo', cookie, host='evil.example')[0], 403)
        self.assertEqual(request('/api/start', cookie, method='POST')[0], 405)
        self.assertEqual(request('/api/retry-analysis', cookie, method='POST')[0], 405)
        self.assertEqual(request('/../../state.json', cookie)[0], 404)
        self.assertEqual(json.loads(request('/api/identity', cookie)[2])['app_id'], 'live-lecture-demo')
        renderer = request('/app.js', cookie)[2].decode()
        self.assertIn('(function(document,module)', renderer)
        self.assertIn('})(undefined,{exports:{}})', renderer)

    def test_production_port_forbidden_without_binding(self):
        with patch('lecture_demo.ThreadingHTTPServer') as listener:
            with self.assertRaises(ValueError): make_server(self.timeline, 8776)
            listener.assert_not_called()


if __name__ == '__main__': unittest.main()
