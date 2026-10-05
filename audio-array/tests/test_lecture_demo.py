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
import wave

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

    def continuous_fixture(self):
        """Two translation publications; the last arrives after audio/analysis."""
        state = json.loads((self.directory / 'state.json').read_text())
        extra = [{'id': 'uncertain', 'text': 'unclear', 'start_seconds': 15, 'end_seconds': 16, 'language': 'en', 'uncertain': True},
                 {'id': 'native', 'text': '日本語', 'start_seconds': 16, 'end_seconds': 17, 'language': 'ja'}]
        state['lines'].extend(extra)
        state['session']['title'] = 'Synthetic saved lecture'
        self.transcripts[1]['lines'].extend(extra)
        first = {'id': 'tr-one', 'text': 'first block', 'source_ids': ['one'], 'start_seconds': 0,
                 'end_seconds': 10, 'generated_at': 900, 'published_at': 1018}
        second = {'id': 'tr-two', 'text': 'second block', 'source_ids': ['two'], 'start_seconds': 10,
                  'end_seconds': 20, 'generated_at': 1034, 'published_at': 1035}
        state['translation'] = {'enabled': True, 'state': 'completed', 'blocks': [first, second]}
        self.write('state.json', state)
        self.write('runtime-manifest.json', {'configuration': {'analysis_interval': 90, 'translation_interval': 60,
                                                               'chunk_seconds': 10, 'pace': 1}})
        histories = [{'started_at': 1015, 'published_at': 1018, 'blocks': [first]},
                     {'started_at': 1031, 'published_at': 1035, 'blocks': [second]}]
        measured = [{'stage': 'translation', 'started_at': row['started_at'], 'published_at': row['published_at'],
                     'processing_seconds': row['published_at'] - row['started_at'], 'through_seconds': block['end_seconds'],
                     'target_source_ids': block['source_ids']} for row, block in zip(histories, [first, second])]
        results = deepcopy(self.results)
        for row in results:
            row['translations'] = []
        self.write_rows('transcript.jsonl', self.transcripts)
        self.write_rows('analysis-history.jsonl', results)
        self.write_rows('translation-history.jsonl', histories)
        self.write_rows('measurements.jsonl', self.measurements + measured)
        return histories, measured

    def wav_fixture(self, seconds=20, rate=16000, channels=1):
        path = self.directory / 'synthetic.wav'
        with wave.open(str(path), 'wb') as output:
            output.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
            output.writeframes(b'\x00\x00' * int(seconds * rate) * channels)
        return path

    def provisional_fixture(self):
        events = []
        for revision, through, published, value in [(1, 3, 1004, 'early temporary words'),
                                                     (2, 6, 1007, 'corrected temporary words'),
                                                     (4, 18, 1019, 'later temporary words')]:
            start = max(0, through - 15)
            events.append({'revision': revision, 'window_start_seconds': start, 'through_seconds': through,
                           'ready_at': 1000 + through, 'started_at': 1000 + through + .1,
                           'published_at': published, 'processing_seconds': .9,
                           'lines': [{'id': f'p{revision:06d}-l0000', 'text': value, 'language': 'en',
                                      'start_seconds': start, 'end_seconds': through, 'uncertain': False}]})
        state = json.loads((self.directory / 'state.json').read_text())
        state['asr'] = {'state': 'completed', 'through_seconds': 20}
        state['provisional_asr'] = {**deepcopy(events[-1]), 'enabled': True, 'state': 'completed',
                                    'refresh_seconds': 3, 'window_seconds': 15, 'error': None, 'lines': []}
        self.write('state.json', state)
        self.write_rows('provisional-history.jsonl', events)
        self.write_rows('provisional-events.jsonl', [{'event': 'failed', 'revision': 3,
            'window_start_seconds': 0, 'through_seconds': 9, 'at': 1010, 'error': 'synthetic preview failure'}])
        return events

    def fake_http(self, timeline=None, port=9999):
        class FakeServer:
            def __init__(self, address, handler): self.server_port = port; self.handler = handler
        with patch('lecture_demo.ThreadingHTTPServer', FakeServer):
            server, url = make_server(timeline or self.timeline, port)
        token = parse_qs(urlsplit(url).query)['token'][0]
        def request(path, cookie='', method='GET', host=None, byte_range=None):
            handler = object.__new__(server.handler)
            handler.path = path
            handler.headers = {'Host': host or f'127.0.0.1:{port}', 'Cookie': cookie}
            if byte_range is not None: handler.headers['Range'] = byte_range
            handler.server = server
            handler.wfile = io.BytesIO(); handler.request_version = 'HTTP/1.1'; handler.requestline = method + ' ' + path; handler.command = method
            getattr(handler, 'do_' + method)()
            raw = handler.wfile.getvalue(); header, body = raw.split(b'\r\n\r\n', 1)
            return int(header.split()[1]), header.decode(), body
        return request, token, f'lecture_demo_{port}={token}'

    def test_originals_do_not_arrive_before_asr_publication(self):
        self.assertEqual(self.timeline.snapshot(11.999)['lines'], [])
        self.assertEqual([line['id'] for line in self.timeline.snapshot(12)['lines']], ['one'])
        self.assertEqual([line['id'] for line in self.timeline.snapshot(21.999)['lines']], ['one'])
        self.assertEqual([line['id'] for line in self.timeline.snapshot(22)['lines']], ['one', 'two'])

    def test_preview_schedule_uses_three_second_clock_and_recorded_admission(self):
        self.provisional_fixture()
        self.write('runtime-manifest.json', {'configuration': {'chunk_seconds': 15, 'pace': 1}})
        timeline = DemoTimeline(self.directory, audio_file=self.wav_fixture())
        for cursor, remaining in ((0, 3), (2.5, .5), (4, 2), (7, 2)):
            state = timeline.snapshot(cursor)
            schedule = state['provisional_asr']['schedule']
            self.assertEqual(('waiting', 'recording', remaining),
                             (schedule['state'], schedule['reason'], schedule['remaining_seconds']))
            self.assertEqual(3, schedule['interval_seconds'])
            self.assertEqual(15, state['asr']['schedule']['interval_seconds'])
        for cursor, reason in ((3.05, 'queued'), (3.5, 'request'), (6.05, 'queued'), (6.5, 'request')):
            state = timeline.snapshot(cursor)
            schedule = state['provisional_asr']['schedule']
            self.assertEqual(('busy', reason), (schedule['state'], schedule['reason']))
            self.assertIsNone(schedule['remaining_seconds'])
            self.assertIsNone(schedule['due_at'])
        self.assertEqual('failed', timeline.snapshot(10)['provisional_asr']['schedule']['reason'])
        # A later actually admitted request is busy even while its prior failure remains visible.
        self.assertEqual('request', timeline.snapshot(18.5)['provisional_asr']['schedule']['reason'])
        self.assertEqual('complete', timeline.snapshot(timeline.duration)['provisional_asr']['schedule']['state'])
        self.assertEqual(3, timeline.snapshot(0)['provisional_asr']['schedule']['remaining_seconds'])
        self.assertEqual([], timeline.snapshot(0)['provisional_asr']['lines'])

    def test_preview_schedule_never_resets_countdown_during_long_first_request(self):
        events = self.provisional_fixture()[:1]
        events[0].update(published_at=1020.167, processing_seconds=17.067)
        self.write_rows('provisional-history.jsonl', events)
        self.write_rows('provisional-events.jsonl', [])
        state = json.loads((self.directory / 'state.json').read_text())
        state['provisional_asr'].update(events[0], lines=[])
        self.write('state.json', state)
        self.write('runtime-manifest.json', {'configuration': {'chunk_seconds': 15, 'pace': 1}})
        timeline = DemoTimeline(self.directory, audio_file=self.wav_fixture())
        for cursor in (3.5, 6, 10, 15, 20.166):
            state = timeline.snapshot(cursor)
            schedule = state['provisional_asr']['schedule']
            self.assertEqual(('busy', 'request'), (schedule['state'], schedule['reason']))
            self.assertIsNone(schedule['remaining_seconds'])
            self.assertEqual([], state['provisional_asr']['lines'])
        self.assertEqual(1, timeline.snapshot(20.167)['provisional_asr']['revision'])
        self.assertNotIn('provisional_asr', self.timeline.snapshot(0))
        self.assertEqual(15, self.timeline.snapshot(0)['asr']['schedule']['interval_seconds'])

    def test_provisional_publications_replace_only_the_temporary_view(self):
        events = self.provisional_fixture()
        timeline = DemoTimeline(self.directory)
        self.assertEqual(timeline.snapshot(3.999)['provisional_asr']['lines'], [])
        first = timeline.snapshot(4)
        self.assertEqual(first['lines'], [])
        self.assertEqual(first['provisional_asr']['lines'], events[0]['lines'])
        self.assertEqual(timeline.snapshot(6.999)['provisional_asr']['lines'], events[0]['lines'])
        revised = timeline.snapshot(7)
        self.assertEqual(revised['provisional_asr']['lines'], events[1]['lines'])
        self.assertEqual(revised['lines'], [])
        self.assertEqual(revised['analysis_history'], [])
        self.assertEqual(timeline.snapshot(19)['provisional_asr']['lines'], events[2]['lines'])
        self.assertEqual([line['id'] for line in timeline.snapshot(19)['lines']], ['one'])
        self.assertEqual(timeline.snapshot(22)['provisional_asr']['lines'], [])
        self.assertEqual(timeline.metadata()['provisional_count'], 3)
        self.assertEqual(timeline.metadata()['line_count'], 2)
        # Rewind reconstructs the exact earlier revision, never the final text.
        revised['provisional_asr']['lines'][0]['text'] = 'tampered'
        self.assertEqual(timeline.snapshot(7)['provisional_asr']['lines'], events[1]['lines'])
        self.assertEqual(timeline.snapshot(4)['provisional_asr']['lines'], events[0]['lines'])
        self.assertEqual(timeline.snapshot(0)['provisional_asr']['lines'], [])

    def test_provisional_failure_preserves_prior_words_until_a_real_success(self):
        events = self.provisional_fixture()
        timeline = DemoTimeline(self.directory)
        self.assertNotEqual(timeline.snapshot(9.999)['provisional_asr']['state'], 'failed')
        failed = timeline.snapshot(10)['provisional_asr']
        self.assertEqual(failed['state'], 'failed')
        self.assertEqual(failed['error'], 'synthetic preview failure')
        self.assertEqual(failed['lines'], events[1]['lines'])
        # Canonical recognition catching up removes only the preview text, not its failure.
        self.assertEqual(timeline.snapshot(12)['provisional_asr']['lines'], [])
        self.assertEqual(timeline.snapshot(12)['provisional_asr']['state'], 'failed')
        self.assertIsNone(timeline.snapshot(19)['provisional_asr']['error'])
        self.assertEqual(timeline.snapshot(7)['provisional_asr']['state'], 'completed')

    def test_provisional_history_is_required_and_validated_without_retiming(self):
        events = self.provisional_fixture()
        for mutate in [lambda rows: rows.pop(),
                       lambda rows: rows[1].update(published_at=1005),
                       lambda rows: rows[1].update(revision=1),
                       lambda rows: rows[1]['lines'][0].update(id=rows[0]['lines'][0]['id']),
                       lambda rows: rows[1]['lines'][0].update(id='one'),
                       lambda rows: rows[1]['lines'][0].update(end_seconds=99)]:
            bad = deepcopy(events); mutate(bad)
            self.write_rows('provisional-history.jsonl', bad)
            with self.assertRaises(DemoDataError): DemoTimeline(self.directory)
        (self.directory / 'provisional-history.jsonl').unlink()
        with self.assertRaises(DemoDataError): DemoTimeline(self.directory)

    def test_old_runs_do_not_gain_provisional_text_and_new_replay_is_read_only(self):
        self.assertNotIn('provisional_asr', self.timeline.snapshot(12))
        self.provisional_fixture()
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        timeline = DemoTimeline(self.directory)
        for seconds in [0, 4, 7, 10, 19, 30, 4]: timeline.snapshot(seconds)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.directory.iterdir()})

    def test_disabled_provisional_accepts_zero_refresh_without_a_history(self):
        state = json.loads((self.directory / 'state.json').read_text())
        state['provisional_asr'] = {'enabled': False, 'state': 'idle', 'refresh_seconds': 0,
                                    'window_seconds': 15, 'revision': None, 'lines': [], 'published_at': None}
        self.write('state.json', state)
        timeline = DemoTimeline(self.directory)
        self.assertNotIn('provisional_asr', timeline.snapshot(12))

    def test_cancelled_preview_is_paused_at_its_recorded_time_and_keeps_prior_failure(self):
        self.provisional_fixture()
        outcomes = [{'event': 'failed', 'revision': 3, 'window_start_seconds': 0,
                     'through_seconds': 9, 'at': 1010, 'error': 'synthetic preview failure'},
                    {'event': 'cancelled_before_dispatch', 'revision': 5, 'window_start_seconds': 5,
                     'through_seconds': 20, 'at': 1032}]
        self.write_rows('provisional-events.jsonl', outcomes)
        timeline = DemoTimeline(self.directory)
        self.assertEqual(timeline.duration, 32)
        self.assertEqual(timeline.snapshot(31.999)['provisional_asr']['state'], 'completed')
        self.assertEqual(timeline.snapshot(32)['provisional_asr']['state'], 'paused')
        self.assertIsNone(timeline.snapshot(32)['provisional_asr']['error'])
        self.assertEqual(timeline.snapshot(10)['provisional_asr']['state'], 'failed')

    def test_preview_millisecond_rounding_does_not_rewrite_recorded_evidence(self):
        events = self.provisional_fixture()
        events[-1]['lines'][0]['start_seconds'] = events[-1]['window_start_seconds'] - .0004
        events[-1]['lines'][0]['end_seconds'] = events[-1]['through_seconds'] + .0004
        self.write_rows('provisional-history.jsonl', events)
        timeline = DemoTimeline(self.directory)
        self.assertEqual(timeline.snapshot(19)['provisional_asr']['lines'], events[-1]['lines'])

    def test_provisional_ids_cannot_be_borrowed_as_translation_evidence(self):
        self.provisional_fixture()
        results = deepcopy(self.results)
        results[-1]['translations'].append({'source_id': 'p000001-l0000', 'text': 'not canonical evidence'})
        self.write_rows('analysis-history.jsonl', results)
        with self.assertRaises(DemoDataError): DemoTimeline(self.directory)

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
        request, token, cookie = self.fake_http()
        self.assertEqual(request('/api/demo')[0], 403)
        status, headers, html = request('/?token=' + token)
        self.assertEqual(status, 200); self.assertIn('lecture_demo_9999=', headers); self.assertNotIn('lecture_session=', headers)
        self.assertIn(b'demo-toolbar', html); self.assertIn('Content-Security-Policy', headers)
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

    def test_time_zero_default_and_optional_audio_metadata(self):
        self.assertEqual(self.timeline.metadata()['initial_seconds'], 0)
        self.assertIsNone(self.timeline.metadata()['audio_url'])
        request, _, cookie = self.fake_http()
        self.assertEqual(json.loads(request('/api/state', cookie)[2])['lines'], [])
        self.assertEqual(request('/audio.wav', cookie)[0], 404)

    def test_continuous_translation_publication_tail_and_rewind(self):
        histories, _ = self.continuous_fixture()
        timeline = DemoTimeline(self.directory)
        self.assertEqual(timeline.duration, 35)
        self.assertEqual(timeline.snapshot(0)['session']['title'], 'Synthetic saved lecture')
        self.assertEqual(timeline.snapshot(17.999)['translation']['blocks'], [])
        first = timeline.snapshot(18)
        self.assertEqual(first['translation']['blocks'], histories[0]['blocks'])
        self.assertEqual(first['translation']['pending_lines'], 0)
        later = timeline.snapshot(22)
        self.assertEqual(later['translation']['pending_lines'], 1)
        self.assertEqual(later['translation']['excluded_uncertain_lines'], 1)
        self.assertNotIn('included_uncertain_lines', later['translation'])
        self.assertEqual(later['translation']['native_lines'], 1)
        self.assertEqual(len(timeline.snapshot(34.999)['translation']['blocks']), 1)
        final = timeline.snapshot(35)
        self.assertEqual(final['translation']['covered_source_ids'], ['one', 'two'])
        self.assertEqual(final['translation']['pending_lines'], 0)
        self.assertEqual(final['demo']['untranslated_lines'], 0)
        self.assertNotIn('translation_ja', final['lines'][0])
        final['translation']['blocks'][0]['text'] = 'mutated'
        self.assertEqual(timeline.snapshot(18)['translation']['blocks'][0]['text'], 'first block')
        self.assertEqual(timeline.snapshot(0)['translation']['blocks'], [])

    def test_continuous_translation_rejects_missing_mismatched_and_future_evidence(self):
        histories, measured = self.continuous_fixture()
        for change in ('missing_measurement', 'wrong_target', 'future_target', 'missing_history', 'different_final'):
            with self.subTest(change=change):
                modified = deepcopy(measured)
                current = deepcopy(histories)
                if change == 'missing_measurement': modified.pop()
                if change == 'wrong_target': modified[0]['target_source_ids'] = ['two']
                if change == 'future_target':
                    current[0]['blocks'][0]['source_ids'] = ['two']
                    modified[0]['target_source_ids'] = ['two']
                if change == 'missing_history': current.pop()
                if change == 'different_final': current[1]['blocks'][0]['text'] = 'not saved final'
                self.write_rows('measurements.jsonl', self.measurements + modified)
                self.write_rows('translation-history.jsonl', current)
                with self.assertRaises(DemoDataError): DemoTimeline(self.directory)

    def uncertain_translation_fixture(self):
        histories, measured = self.continuous_fixture()
        state = json.loads((self.directory / 'state.json').read_text())
        for lines in (state['lines'], self.transcripts[1]['lines']):
            row = next(line for line in lines if line['id'] == 'uncertain')
            row.update(text='They are not limited to one party.', doubt_reasons=['timestamp_outside_audio'])
        state['translation']['source_policy_version'] = 1
        for history in histories:
            history['selection'] = {'source_policy_version': 1, 'excluded_sources': []}
        histories[1]['selection']['excluded_sources'] = [{'source_id': 'native', 'reason': 'native_language'}]
        block = histories[1]['blocks'][0]
        block.update(source_ids=['two', 'uncertain'], uncertain_source_ids=['uncertain'],
                     uncertainty_reasons={'uncertain': ['timestamp_outside_audio']})
        measured[1]['target_source_ids'] = ['two', 'uncertain']
        state['translation']['blocks'] = [block for history in histories for block in history['blocks']]
        self.write('state.json', state)
        self.write_rows('transcript.jsonl', self.transcripts)
        self.write_rows('translation-history.jsonl', histories)
        self.write_rows('measurements.jsonl', self.measurements + measured)
        return histories, measured

    def test_versioned_translation_preserves_uncertain_negation_and_rewind(self):
        histories, _ = self.uncertain_translation_fixture()
        timeline = DemoTimeline(self.directory)
        self.assertEqual(timeline.snapshot(22)['translation']['pending_lines'], 2)
        self.assertEqual(timeline.snapshot(22)['translation']['excluded_uncertain_lines'], 0)
        self.assertEqual(timeline.snapshot(22)['translation']['included_uncertain_lines'], 1)
        self.assertEqual(timeline.snapshot(18)['translation']['included_uncertain_lines'], 0)
        self.assertEqual(len(timeline.snapshot(34.999)['translation']['blocks']), 1)
        final = timeline.snapshot(35)
        self.assertEqual(final['translation']['covered_source_ids'], ['one', 'two', 'uncertain'])
        self.assertEqual(final['translation']['included_uncertain_lines'], 1)
        self.assertEqual(final['translation']['blocks'][1], histories[1]['blocks'][0])
        original = next(line for line in final['lines'] if line['id'] == 'uncertain')
        self.assertTrue(original['uncertain'])
        self.assertEqual(original['doubt_reasons'], ['timestamp_outside_audio'])
        self.assertEqual(timeline.snapshot(18)['translation']['covered_source_ids'], ['one'])
        final['translation']['blocks'][1]['uncertainty_reasons']['uncertain'].append('mutated')
        self.assertEqual(timeline.snapshot(35)['translation']['blocks'][1]['uncertainty_reasons'],
                         {'uncertain': ['timestamp_outside_audio']})

    def test_uncertain_translation_needs_explicit_supported_history_policy(self):
        histories, _ = self.uncertain_translation_fixture()
        for version in (None, 0, 2, True, '1'):
            with self.subTest(version=version):
                modified = deepcopy(histories)
                if version is None:
                    modified[1]['selection'].pop('source_policy_version')
                else:
                    modified[1]['selection']['source_policy_version'] = version
                self.write_rows('translation-history.jsonl', modified)
                with self.assertRaises(DemoDataError): DemoTimeline(self.directory)

    def test_new_session_counts_require_an_explicit_recorded_policy(self):
        self.uncertain_translation_fixture()
        state = json.loads((self.directory / 'state.json').read_text())
        configuration = json.loads((self.directory / 'runtime-manifest.json').read_text())
        for marker in ('state', 'configuration', 'both', 'neither'):
            with self.subTest(marker=marker):
                final, manifest = deepcopy(state), deepcopy(configuration)
                if marker in ('configuration', 'neither'):
                    final['translation'].pop('source_policy_version')
                if marker in ('configuration', 'both'):
                    manifest['configuration']['translation_source_policy_version'] = 1
                self.write('state.json', final)
                self.write('runtime-manifest.json', manifest)
                snapshot = DemoTimeline(self.directory).snapshot(22)
                self.assertEqual(snapshot['translation']['pending_lines'], 1 if marker == 'neither' else 2)
                self.assertEqual(snapshot['translation']['excluded_uncertain_lines'], 1 if marker == 'neither' else 0)
        for invalid in (2, True, '1', None):
            with self.subTest(invalid=invalid):
                manifest = deepcopy(configuration)
                manifest['configuration']['translation_source_policy_version'] = invalid
                self.write('runtime-manifest.json', manifest)
                with self.assertRaises(DemoDataError): DemoTimeline(self.directory)

    def test_versioned_failed_session_keeps_meaningful_pending_and_exclusion_audit(self):
        self.continuous_fixture()
        state = json.loads((self.directory / 'state.json').read_text())
        for lines in (state['lines'], self.transcripts[1]['lines']):
            next(line for line in lines if line['id'] == 'uncertain').update(
                text='They are not limited to one party.', doubt_reasons=['timestamp_outside_audio'])
            lines.extend([
                {'id': 'filler', 'text': 'Um, uh.', 'language': 'en', 'uncertain': True,
                 'start_seconds': 17, 'end_seconds': 18, 'doubt_reasons': ['no_speech']},
                {'id': 'c000001-l0000', 'text': 'A meaningful repeated clause.', 'language': 'en',
                 'uncertain': True, 'start_seconds': 18, 'end_seconds': 18,
                 'doubt_reasons': ['timestamp_outside_audio', 'repetition']},
                {'id': 'c000001-l0001', 'text': 'A meaningful repeated clause.', 'language': 'en',
                 'uncertain': True, 'start_seconds': 18, 'end_seconds': 18,
                 'doubt_reasons': ['timestamp_outside_audio', 'repetition']},
            ])
        state['translation'].update(source_policy_version=1, state='failed', blocks=[], error='recorded failure')
        self.write('state.json', state)
        self.write_rows('transcript.jsonl', self.transcripts)
        self.write_rows('measurements.jsonl', [row for row in self.measurements if row['stage'] == 'asr'])
        self.write_rows('translation-history.jsonl', [])
        self.write_rows('analysis-history.jsonl', [])
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        timeline = DemoTimeline(self.directory)
        final = timeline.snapshot(timeline.duration)
        self.assertEqual(final['translation']['state'], 'failed')
        self.assertEqual(final['translation']['pending_lines'], 4)
        self.assertEqual(final['translation']['excluded_uncertain_lines'], 2)
        self.assertEqual(final['translation']['included_uncertain_lines'], 2)
        self.assertEqual(final['translation']['native_lines'], 1)
        self.assertEqual(final['translation']['excluded_sources'], [
            {'source_id': 'filler', 'reason': 'filler_only', 'duplicate_of': None},
            {'source_id': 'c000001-l0001', 'reason': 'duplicate_invalid_timing', 'duplicate_of': 'c000001-l0000'},
        ])
        self.assertNotIn('exclusion_reason', final['lines'][-1])
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.directory.iterdir()})

    def test_versioned_translation_rejects_excluded_or_invalid_sources(self):
        histories, measured = self.uncertain_translation_fixture()
        state = json.loads((self.directory / 'state.json').read_text())
        for change in ('excluded_target', 'future_exclusion', 'unknown_exclusion', 'duplicate_exclusion',
                       'self_duplicate', 'unknown_duplicate', 'native_target', 'unknown_target', 'duplicate_target', 'blank_target'):
            with self.subTest(change=change):
                current, measurement, final = deepcopy(histories), deepcopy(measured), deepcopy(state)
                transcripts = deepcopy(self.transcripts)
                exclusions = current[1]['selection']['excluded_sources']
                if change == 'excluded_target': exclusions.append({'source_id': 'uncertain', 'reason': 'repeated_text', 'duplicate_of': 'two'})
                if change == 'future_exclusion': current[0]['selection']['excluded_sources'] = deepcopy(exclusions)
                if change == 'unknown_exclusion': exclusions.append({'source_id': 'missing', 'reason': 'empty'})
                if change == 'duplicate_exclusion': exclusions.extend(deepcopy(exclusions))
                if change == 'self_duplicate': exclusions[0]['duplicate_of'] = 'native'
                if change == 'unknown_duplicate': exclusions[0]['duplicate_of'] = 'missing'
                if change in ('native_target', 'unknown_target', 'duplicate_target'):
                    target = {'native_target': 'native', 'unknown_target': 'missing', 'duplicate_target': 'two'}[change]
                    current[1]['blocks'][0]['source_ids'].append(target)
                    measurement[1]['target_source_ids'].append(target)
                if change == 'blank_target':
                    for lines in (final['lines'], transcripts[1]['lines']):
                        next(line for line in lines if line['id'] == 'uncertain')['text'] = ' '
                final['translation']['blocks'] = [block for history in current for block in history['blocks']]
                self.write('state.json', final)
                self.write_rows('transcript.jsonl', transcripts)
                self.write_rows('translation-history.jsonl', current)
                self.write_rows('measurements.jsonl', self.measurements + measurement)
                with self.assertRaises(DemoDataError): DemoTimeline(self.directory)

    def test_recorded_schedules_and_audio_progress(self):
        self.continuous_fixture()
        timeline = DemoTimeline(self.directory, audio_file=self.wav_fixture())
        self.assertEqual(timeline.metadata()['audio_seconds'], 20)
        self.assertEqual(timeline.metadata()['audio_url'], '/audio.wav')
        self.assertEqual(timeline.metadata()['audio_start_seconds'], 0)
        self.assertEqual(timeline.metadata()['audio_alignment'], 'session-start approximation')
        early = timeline.snapshot(5.5)
        self.assertEqual(early['capture']['audio_seconds'], 5.5)
        self.assertEqual(early['asr']['schedule']['remaining_seconds'], 4.5)
        self.assertEqual(timeline.snapshot(11)['asr']['schedule']['reason'], 'request')
        self.assertEqual(timeline.snapshot(16)['translation']['schedule']['reason'], 'request')
        self.assertIsNone(timeline.snapshot(16)['translation']['schedule']['remaining_seconds'])
        self.assertEqual(timeline.snapshot(23)['translation']['schedule']['remaining_seconds'], 8)
        self.assertEqual(timeline.snapshot(28)['translation']['schedule']['reason'], 'shared_slot')
        self.assertEqual(timeline.snapshot(35)['translation']['schedule']['state'], 'complete')
        self.assertEqual(timeline.snapshot(35)['capture']['audio_seconds'], 20)

    def initial_translation_fixture(self, marker=True):
        self.continuous_fixture()
        runtime = json.loads((self.directory / 'runtime-manifest.json').read_text())
        if marker is not None:
            runtime['configuration']['initial_translation_first'] = marker
        self.write('runtime-manifest.json', runtime)
        measured = [json.loads(line) for line in (self.directory / 'measurements.jsonl').read_text().splitlines()]
        first_analysis = next(row for row in measured if row['stage'] == 'analysis')
        first_analysis.update(published_at=1020, processing_seconds=2)  # starts after translation publication at 1018
        self.write_rows('measurements.jsonl', measured)
        return measured

    def test_initial_translation_schedule_follows_recorded_admission_and_publication(self):
        self.initial_translation_fixture()
        timeline = DemoTimeline(self.directory)
        self.assertNotEqual(timeline.snapshot(11.999)['analysis']['schedule']['reason'], 'initial_translation')
        for at in (12, 14.999):
            state = timeline.snapshot(at)
            self.assertEqual(state['analysis']['schedule']['reason'], 'initial_translation')
            self.assertEqual(state['analysis']['schedule']['state'], 'waiting')
            self.assertIsNone(state['analysis']['schedule']['remaining_seconds'])
            self.assertIsNone(state['analysis']['schedule']['due_at'])
            self.assertIsNone(state['analysis']['result'])
            self.assertEqual(state['translation']['blocks'], [])
        for at in (15, 17.999):
            self.assertEqual(timeline.snapshot(at)['analysis']['schedule']['reason'], 'shared_slot')
        first_translation = timeline.snapshot(18)
        self.assertEqual(first_translation['analysis']['schedule']['reason'], 'request')
        self.assertEqual(first_translation['translation']['blocks'][0]['published_at'], 1018)
        self.assertIsNone(first_translation['analysis']['result'])
        self.assertIsNone(timeline.snapshot(19.999)['analysis']['result'])
        self.assertEqual(timeline.snapshot(20)['analysis']['generated_at'], 1020)
        self.assertEqual(timeline.snapshot(12)['analysis']['schedule']['reason'], 'initial_translation', 'Rewinding restores only the recorded initial wait')

    def test_initial_translation_failure_releases_saved_analysis_without_fake_success(self):
        measured = self.initial_translation_fixture()
        self.write_rows('translation-history.jsonl', [])
        self.write_rows('measurements.jsonl', [row for row in measured if row['stage'] != 'translation'])
        state = json.loads((self.directory / 'state.json').read_text())
        state['translation'].update(state='failed', error='synthetic preparation failure', blocks=[])
        self.write('state.json', state)
        self.write_rows('generation-events.jsonl', [{'stage': 'translation', 'event': 'failed', 'at': 1014,
                                                    'error': {'category': 'local_or_validation'}}])
        timeline = DemoTimeline(self.directory)
        self.assertEqual(timeline.snapshot(13.999)['analysis']['schedule']['reason'], 'initial_translation')
        failed = timeline.snapshot(14)
        self.assertNotEqual(failed['analysis']['schedule']['reason'], 'initial_translation')
        self.assertEqual(failed['translation']['state'], 'failed')
        self.assertEqual(failed['translation']['blocks'], [])
        self.assertIsNone(failed['analysis']['result'])
        self.assertEqual(timeline.snapshot(20)['analysis']['generated_at'], 1020)
        self.assertEqual(timeline.snapshot(20)['translation']['blocks'], [])

    def test_initial_translation_policy_never_reorders_old_or_conflicting_histories(self):
        original_state = json.loads((self.directory / 'state.json').read_text())
        original_transcripts = deepcopy(self.transcripts)
        def reset_sources():
            self.write('state.json', original_state)
            self.transcripts = deepcopy(original_transcripts)
        for marker in (None, False, 'true', 1):
            reset_sources()
            self.initial_translation_fixture(marker)
            timeline = DemoTimeline(self.directory)
            self.assertNotEqual(timeline.snapshot(12)['analysis']['schedule']['reason'], 'initial_translation')
            self.assertEqual(timeline.snapshot(18)['translation']['blocks'][0]['published_at'], 1018)
            self.assertEqual(timeline.snapshot(20)['analysis']['generated_at'], 1020)
        reset_sources()
        self.continuous_fixture()  # Historical analysis at 1015 precedes translation at 1018.
        runtime = json.loads((self.directory / 'runtime-manifest.json').read_text())
        runtime['configuration']['initial_translation_first'] = True
        self.write('runtime-manifest.json', runtime)
        timeline = DemoTimeline(self.directory)
        self.assertNotEqual(timeline.snapshot(12)['analysis']['schedule']['reason'], 'initial_translation')
        self.assertEqual(timeline.snapshot(15)['analysis']['generated_at'], 1015)
        self.assertEqual(timeline.snapshot(15)['translation']['blocks'], [])
        reset_sources()
        self.initial_translation_fixture()
        self.write_rows('generation-events.jsonl', [{'stage': 'analysis', 'event': 'failed', 'at': 1017,
                                                    'error': {'category': 'local_or_validation'}}])
        timeline = DemoTimeline(self.directory)
        self.assertNotEqual(timeline.snapshot(12)['analysis']['schedule']['reason'], 'initial_translation', 'An earlier failed analysis has unknown admission time; never invent its ordering')

    def test_failed_generation_is_not_completion_and_preserves_reservation(self):
        self.continuous_fixture()
        state = json.loads((self.directory / 'state.json').read_text())
        error = {'category': 'quota', 'provider_code': 'insufficient_quota'}
        for kind in ('translation', 'analysis'):
            state[kind].update(state='failed', error='quota failed', schedule={'error': error})
        state['translation']['blocks'] = []
        self.write('state.json', state)
        self.write_rows('measurements.jsonl', [row for row in self.measurements if row['stage'] == 'asr'])
        self.write_rows('translation-history.jsonl', [])
        self.write_rows('analysis-history.jsonl', [])
        self.write_rows('generation-events.jsonl', [{'at': 1014, 'stage': kind, 'event': 'failed', 'error': error} for kind in ('translation', 'analysis')])
        self.write('cost-report.json', {'confirmed_api_usd': 0, 'retained_reservation_usd': .2, 'additional_api_usd': None})
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        timeline = DemoTimeline(self.directory)
        self.assertEqual(timeline.snapshot(13)['analysis']['state'], 'waiting')
        self.assertEqual(timeline.snapshot(14)['analysis']['state'], 'failed')
        final = timeline.snapshot(timeline.duration)
        self.assertEqual(final['translation']['state'], 'failed')
        self.assertEqual(final['translation']['pending_lines'], 2)
        self.assertEqual(final['translation']['schedule']['error'], error)
        self.assertEqual(timeline.cost['retained_reservation_usd'], .2)
        self.assertIsNone(timeline.cost['additional_api_usd'])
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.directory.iterdir()})

    def test_audio_rejects_invalid_mismatched_or_accelerated_input(self):
        self.continuous_fixture()
        for name, contents in [('not-wave.bin', b'not a WAV'), ('truncated.wav', b'RIFF')]:
            path = self.directory / name; path.write_bytes(contents)
            with self.subTest(name=name), self.assertRaises(DemoDataError): DemoTimeline(self.directory, audio_file=path)
        for options in ({'seconds': 19}, {'channels': 2}, {'rate': 8000}):
            with self.subTest(options=options), self.assertRaises(DemoDataError):
                DemoTimeline(self.directory, audio_file=self.wav_fixture(**options))
        path = self.wav_fixture()
        path.write_bytes(path.read_bytes()[:-2])
        with self.assertRaises(DemoDataError): DemoTimeline(self.directory, audio_file=path)
        self.wav_fixture()
        self.write('runtime-manifest.json', {'configuration': {'pace': 0}})
        with self.assertRaises(DemoDataError): DemoTimeline(self.directory, audio_file=path)
        DemoTimeline(self.directory)  # Legacy display-only replay remains usable.
        self.write('runtime-manifest.json', {'configuration': {'pace': 1}})
        state = json.loads((self.directory / 'state.json').read_text()); state['display_simulation'] = {'source_label': 'synthetic'}
        self.write('state.json', state)
        with self.assertRaises(DemoDataError): DemoTimeline(self.directory, audio_file=path)

    def test_audio_auth_fixed_file_range_head_and_port_scoped_cookie(self):
        self.continuous_fixture()
        path = self.wav_fixture()
        expected = path.read_bytes()
        timeline = DemoTimeline(self.directory, audio_file=path)
        request, token, cookie = self.fake_http(timeline)
        self.assertEqual(request('/audio.wav')[0], 403)
        self.assertEqual(request('/audio.wav', cookie, host='evil.example')[0], 403)
        self.assertEqual(request('/audio.wav', 'lecture_demo_9998=' + token)[0], 403)
        self.assertEqual(request('/audio.wav', cookie)[2], expected)
        status, headers, data = request('/audio.wav', cookie, byte_range='bytes=0-43')
        self.assertEqual((status, data), (206, expected[:44]))
        self.assertIn(f'Content-Range: bytes 0-43/{len(expected)}', headers)
        self.assertIn('Accept-Ranges: bytes', headers)
        self.assertIn("media-src 'self'", headers)
        for byte_range, wanted in [('bytes=44-', expected[44:]), ('bytes=-20', expected[-20:]),
                                   ('bytes=4-9999999', expected[4:])]:
            with self.subTest(byte_range=byte_range):
                self.assertEqual(request('/audio.wav', cookie, byte_range=byte_range)[2], wanted)
        for byte_range in ('bytes=99999999-', 'bytes=8-4', 'bytes=-0', 'bytes=0-1,4-5', 'items=0-1', 'bytes=-'):
            with self.subTest(byte_range=byte_range):
                status, headers, data = request('/audio.wav', cookie, byte_range=byte_range)
                self.assertEqual((status, data), (416, b''))
                self.assertIn(f'Content-Range: bytes */{len(expected)}', headers)
        status, headers, data = request('/audio.wav', cookie, method='HEAD')
        self.assertEqual((status, data), (200, b''))
        self.assertIn(f'Content-Length: {len(expected)}', headers)
        path.write_bytes(b'replaced')
        self.assertEqual(request('/audio.wav?path=/etc/passwd', cookie)[2], expected)
        self.assertEqual(request('/../../synthetic.wav', cookie)[0], 404)

    def test_production_port_forbidden_without_binding(self):
        with patch('lecture_demo.ThreadingHTTPServer') as listener:
            with self.assertRaises(ValueError): make_server(self.timeline, 8776)
            listener.assert_not_called()


if __name__ == '__main__': unittest.main()
