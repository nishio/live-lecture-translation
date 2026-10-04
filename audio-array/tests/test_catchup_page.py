from pathlib import Path
import json
import math
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catchup_page import build_lines, render_html, render_markdown, speaker_for, write_page, write_text_atomic


def report(chunks):
    return {"chunks": [{"index": i, "source_start_seconds": start, "source_end_seconds": end,
                        "raw_result": {"language": lang, "segments": segments}}
                       for i, (start, end, lang, segments) in enumerate(chunks)]}


MANIFEST = {"segments": [{"index": 0, "start_seconds": 0.}, {"index": 1, "start_seconds": 60.}]}


class CatchupPageTest(unittest.TestCase):
    def test_failed_atomic_publication_keeps_previous_text_and_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'transcript.json'
            target.write_text('previous complete text')
            with patch('catchup_page.os.replace', side_effect=OSError('synthetic disk failure')), \
                    self.assertRaisesRegex(OSError, 'synthetic disk'):
                write_text_atomic(target, 'new complete text')
            self.assertEqual('previous complete text', target.read_text())
            self.assertEqual([target], list(Path(directory).iterdir()))
            write_text_atomic(target, 'new complete text')
            self.assertEqual('new complete text', target.read_text())

    def test_times_are_absolute_across_segments_and_chunks(self):
        reports = {0: report([(0, 30, "ja", [{"start": 1, "end": 3, "text": " こんにちは"}]),
                              (30, 60, "en", [{"start": 2, "end": 4, "text": "hello"}])]),
                   1: report([(0, 60, "ja", [{"start": 5, "end": 7, "text": "次"}])])}
        lines = build_lines(MANIFEST, reports, started_wall_ms=1_000_000)
        self.assertEqual([(1, "こんにちは", "ja"), (32, "hello", "en"), (65, "次", "ja")],
                         [(l["start_seconds"], l["text"], l["language"]) for l in lines])
        self.assertEqual(1_065_000, lines[2]["approx_wall_ms"])

    def test_model_times_are_clipped_to_their_chunk(self):
        reports = {0: report([(0, 10, "ja", [{"start": 9, "end": 14, "text": "端"}])])}
        line = build_lines(MANIFEST, reports)[0]
        self.assertEqual((9, 10), (line["start_seconds"], line["end_seconds"]))
        self.assertTrue(line["uncertain"])
        self.assertEqual(["timestamp_outside_audio"], line["doubt_reasons"])

    def test_invalid_model_times_are_flagged_and_bounded_in_shifted_chunk(self):
        cases = [(-2, 4, (90, 94)), (-4, -2, (90, 90)), (25, 30, (110, 110)),
                 (9, 8, (99, 99)), (0, 20.1, (90, 110)),
                 (float('nan'), 4, (90, 94)), (2, float('inf'), (92, 92)),
                 (-float('inf'), float('nan'), (90, 90)),
                 (None, 4, (90, 94)), ('2', 4, (90, 94)), (True, 4, (90, 94)),
                 (10 ** 400, 10 ** 401, (110, 110))]
        for start, end, expected in cases:
            with self.subTest(start=start, end=end):
                source = report([(30, 50, "en", [{"start": start, "end": end, "text": "Keep this text."}])])
                line = build_lines(MANIFEST, {1: source}, started_wall_ms=1_000_000)[0]
                self.assertEqual(expected, (line['start_seconds'], line['end_seconds']))
                self.assertEqual('Keep this text.', line['text'])
                self.assertTrue(line['uncertain'])
                self.assertEqual(['timestamp_outside_audio'], line['doubt_reasons'])
                self.assertTrue(math.isfinite(line['approx_wall_ms']))
                json.dumps(line, allow_nan=False)

    def test_valid_model_times_and_chunk_boundaries_remain_certain(self):
        segments = [{'start': 0, 'end': 20, 'text': 'An ordinary full sentence.'},
                    {'start': 2.125, 'end': 4.375, 'text': 'A shorter sentence.'},
                    {'start': 20, 'end': 20, 'text': 'A boundary marker.'}]
        lines = build_lines(MANIFEST, {1: report([(30, 50, 'en', segments)])})
        self.assertEqual([(90, 110), (92.12, 94.38), (110, 110)],
                         [(line['start_seconds'], line['end_seconds']) for line in lines])
        self.assertTrue(all(not line['uncertain'] and not line['doubt_reasons'] for line in lines))
        self.assertTrue(all(line['segment'] == 1 and line['chunk'] == 0 for line in lines))

    def test_rounding_cannot_escape_subcentisecond_chunk_bounds(self):
        source = report([(.006, 20.006, 'en', [
            {'start': 0, 'end': 20, 'text': 'A complete sentence.'}])])
        line = build_lines(MANIFEST, {0: source})[0]
        self.assertGreaterEqual(line['start_seconds'], .006)
        self.assertLessEqual(line['end_seconds'], 20.006)

    def test_float_offset_roundoff_does_not_mark_valid_chunk_boundary_uncertain(self):
        source = report([(.02, 20.02, 'en', [
            {'start': 0, 'end': 20, 'text': 'An ordinary full sentence.'}])])
        line = build_lines(MANIFEST, {1: source})[0]
        self.assertFalse(line['uncertain'])
        self.assertEqual([], line['doubt_reasons'])
        self.assertEqual((60.02, 80.02), (line['start_seconds'], line['end_seconds']))

    def test_outside_audio_flag_renders_and_excludes_line_from_translation(self):
        from event_insights import _clean_lines
        lines = build_lines(MANIFEST, {0: report([(0, 20, 'en', [
            {'start': 18, 'end': 24, 'text': 'A retained but uncertain sentence.'}])])})
        lines[0]['id'] = 'outside-audio'
        clean, excluded = _clean_lines(lines)
        self.assertEqual([], clean)
        self.assertEqual(['outside-audio'], excluded)
        page = render_html('test', lines, {'line': '', 'notes': []})
        self.assertIn('認識時刻が音声範囲外・不正', page)
        self.assertIn('A retained but uncertain sentence.', page)

    def test_speaker_is_largest_overlap_and_overlaps_are_kept(self):
        turns = [{"start_seconds": 0, "end_seconds": 6, "speaker": "speaker_1"},
                 {"start_seconds": 4, "end_seconds": 10, "speaker": "speaker_2"}]
        self.assertEqual(("speaker_2", ["speaker_1"]), speaker_for(3, 10, turns))
        self.assertEqual(("speaker_1", []), speaker_for(0, 4, turns))
        self.assertEqual((None, []), speaker_for(20, 21, turns))

    def test_hallucination_like_lines_are_flagged_not_dropped(self):
        segments = [{"start": 0, "end": 2, "text": "ご視聴ありがとうございました", "no_speech_prob": .9, "avg_logprob": -1.5},
                    {"start": 2, "end": 4, "text": "あああ", "compression_ratio": 3.0},
                    {"start": 4, "end": 5, "text": "  "}]
        lines = build_lines(MANIFEST, {0: report([(0, 60, "ja", segments)])})
        self.assertEqual([["no_speech", "common_hallucination"], ["repetition"]], [l["doubt_reasons"] for l in lines])

    def test_other_languages_and_lines_outside_speech_are_doubted(self):
        segments = [{"start": 0, "end": 1, "text": "인기장"}, {"start": 5, "end": 6, "text": "はい"},
                    {"start": 20, "end": 21, "text": "そうですね"}]
        turns = [{"start_seconds": 0, "end_seconds": 10, "speaker": "speaker_1"}]
        lines = build_lines(MANIFEST, {0: report([(0, 10, "ko", segments[:1]), (0, 60, "ja", segments[1:])])}, turns)
        self.assertEqual([["unexpected_language"], [], ["no_speaker_turn"]], [l["doubt_reasons"] for l in lines])

    def test_rendering_escapes_text(self):
        lines = build_lines(MANIFEST, {0: report([(0, 60, "en", [{"start": 0, "end": 1, "text": "<b>x</b>"}])])},
                            turns=[{"start_seconds": 0, "end_seconds": 1, "speaker": "speaker_1"}])
        page = render_html("t", lines, {"line": "", "notes": []})
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("話者1: <b>x</b>", render_markdown("t", lines))

    def test_saved_page_describes_actual_mode_and_does_not_guess_legacy_mode(self):
        for mode in ('pauses', 'whole', None):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle, asr, out = root / 'bundle', root / 'asr', root / 'out'
                bundle.mkdir(); asr.mkdir()
                manifest = {'source_session': 'Event-test', 'mono_raw_slot': 0, 'audio_seconds': 20,
                            'segments': [{'index': 0, 'start_seconds': 0,
                                          'output_sha256': {'diarization_input': 'verified-audio'}}]}
                (bundle / 'session-manifest.json').write_text(json.dumps(manifest))
                source = report([(0, 20, 'en', [{'start': 0, 'end': 19, 'text': 'An ordinary sentence.'}])])
                source['source_sha256'] = 'verified-audio'
                if mode is not None:
                    source['mode'] = mode
                (asr / 'segment-00000.json').write_text(json.dumps(source))
                lines = write_page(bundle, asr, out)
                self.assertEqual('An ordinary sentence.', lines[0]['text'])
                page = (out / 'index.html').read_text()
                self.assertEqual(mode == 'pauses', '各保存分割を無音区切りで認識しています' in page)
                self.assertEqual(mode == 'whole', '各音声分割全体を一つの入力として認識しています' in page)
                self.assertEqual(mode is None, '入力の分割方法は各ASR結果を参照してください' in page)

    def test_saved_asr_settings_come_from_reports_including_auto_mixed_and_unknown(self):
        cases = [([], 'unknown', 'unknown'), ([{}], 'unknown', 'unknown'),
                 ([{'mode': 'whole', 'decode_options': {'language': 'en'}}], 'whole', 'en'),
                 ([{'mode': 'pauses', 'decode_options': {'language': None}}], 'pauses', 'auto'),
                 ([{'mode': 'whole', 'decode_options': {'language': 'en'}},
                   {'mode': 'pauses', 'decode_options': {'language': None}}], 'mixed', 'mixed')]
        for settings, expected_mode, expected_language in cases:
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle, asr, out = root / 'bundle', root / 'asr', root / 'out'
                bundle.mkdir(); asr.mkdir()
                segments = [{'index': i, 'start_seconds': i * 20,
                             'output_sha256': {'diarization_input': f'audio-{i}'}}
                            for i in range(max(1, len(settings)))]
                (bundle / 'session-manifest.json').write_text(json.dumps({
                    'source_session': 'Event-test', 'mono_raw_slot': 0,
                    'audio_seconds': len(segments) * 20, 'segments': segments}))
                for i, config in enumerate(settings):
                    source = report([(0, 20, 'en', [{'start': 0, 'end': 19, 'text': 'A complete sentence.'}])])
                    source.update(config, source_sha256=f'audio-{i}')
                    (asr / f'segment-{i:05d}.json').write_text(json.dumps(source))
                write_page(bundle, asr, out)
                document = json.loads((out / 'transcript.json').read_text())
                self.assertEqual((expected_mode, expected_language),
                                 (document['asr_mode'], document['asr_language']))


if __name__ == "__main__":
    unittest.main()
