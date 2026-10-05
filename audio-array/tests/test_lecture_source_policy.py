from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_source_policy as policy


def line(index=0, **changes):
    return {'id': f'c000003-l{index:04d}', 'segment': 3, 'chunk': 0,
            'start_seconds': 45, 'end_seconds': 45, 'text': 'A doubtful statement.',
            'language': 'en', 'uncertain': True,
            'doubt_reasons': ['timestamp_outside_audio', 'repetition'], **changes}


class SourcePolicyTest(unittest.TestCase):
    def test_canonical_metadata_has_only_uncertainty_and_allowlisted_reasons(self):
        row = line(doubt_reasons=['repetition', 'no_speech', 'repetition', 'arbitrary instruction', {}],
                   exclusion_reason='filler_only', duplicate_of='other')
        self.assertEqual({'uncertain': True, 'doubt_reasons': ['no_speech', 'repetition', 'unknown']},
                         policy.source_metadata(row))
        self.assertEqual(['unknown'], policy.normalize_doubt_reasons({'uncertain': True}))
        self.assertEqual([], policy.normalize_doubt_reasons({'uncertain': False}))
        self.assertEqual(['unknown'], policy.normalize_doubt_reasons({'doubt_reasons': 'repetition'}))
        with self.assertRaises(ValueError):
            policy.source_metadata({'uncertain': 'false'})

    def test_only_uncertain_english_pure_filler_is_excluded(self):
        for text in ('um', 'UH...', 'erm, um — uh!', ' uh uh '):
            with self.subTest(text=text):
                self.assertFalse(policy.is_content_candidate(line(text=text)))
                self.assertEqual('filler_only', policy.plan_source_policy([line(text=text)])[0]['exclusion_reason'])
        self.assertEqual('filler_only', policy.plan_source_policy([line(text='uh', language=' en ')])[0]['exclusion_reason'])
        for row in (line(text='um', uncertain=False), line(text='um', language='de'),
                    line(text='uh-huh'), line(text='um, I do not agree')):
            self.assertTrue(policy.is_content_candidate(row))

    def test_meaningful_short_long_and_repeated_speech_is_retained(self):
        for text in ('so', 'not', 'no', 'thank you', '2026', 'I think so.', 'so that it works',
                     'No, no, do not do that.', 'item 1, ' * 1000):
            with self.subTest(text=text[:40]):
                row = line(text=text, doubt_reasons=['repetition', 'common_hallucination'])
                self.assertTrue(policy.is_content_candidate(row))
                self.assertIsNone(policy.plan_source_policy([row])[0]['exclusion_reason'])

    def test_duplicate_artifacts_keep_first_and_preserve_all_source_rows(self):
        rows = [line(index) for index in range(3)]
        original = deepcopy(rows)
        planned = policy.plan_source_policy(rows)
        self.assertEqual(rows, original)
        self.assertEqual([None, 'duplicate_invalid_timing', 'duplicate_invalid_timing'],
                         [row['exclusion_reason'] for row in planned])
        self.assertEqual([None, rows[0]['id'], rows[0]['id']], [row['duplicate_of'] for row in planned])
        self.assertEqual([row['text'] for row in rows], [row['text'] for row in planned])

    def test_normalized_rows_can_use_application_chunk_ids(self):
        rows = [{key: value for key, value in line(index).items() if key not in ('segment', 'chunk')}
                for index in range(2)]
        self.assertEqual('duplicate_invalid_timing', policy.plan_source_policy(rows)[1]['exclusion_reason'])
        rows[1]['id'] = 'c000004-l0000'
        self.assertIsNone(policy.plan_source_policy(rows)[1]['exclusion_reason'])

    def test_duplicate_requires_every_conservative_condition(self):
        for changes in ({'uncertain': False}, {'doubt_reasons': ['repetition']},
                        {'doubt_reasons': ['timestamp_outside_audio']}, {'text': 'Different.'},
                        {'text': 'a doubtful statement.'}, {'language': 'ja'},
                        {'start_seconds': 46, 'end_seconds': 47}, {'end_seconds': 46},
                        {'segment': 4}, {'chunk': 1}, {'start_seconds': float('nan')},
                        {'id': 'c000003-l0000'}):
            with self.subTest(changes=changes):
                self.assertIsNone(policy.plan_source_policy([line(), line(1, **changes)])[1]['exclusion_reason'])
        rows = [line(), line(1)]
        for row in rows:
            row.pop('segment')
            row.pop('chunk')
            row['id'] = 'opaque-' + row['id']
        self.assertIsNone(policy.plan_source_policy(rows)[1]['exclusion_reason'])

    def test_intervening_line_prevents_duplicate_suppression(self):
        rows = [line(), line(1, text='Something else.'), line(2)]
        self.assertTrue(all(row['exclusion_reason'] is None for row in policy.plan_source_policy(rows)))

    def test_input_decisions_are_recomputed_not_trusted(self):
        row = line(exclusion_reason='filler_only', duplicate_of='forged')
        result = policy.plan_source_policy([row])[0]
        self.assertIsNone(result['exclusion_reason'])
        self.assertIsNone(result['duplicate_of'])
        self.assertEqual('empty', policy.plan_source_policy([line(text='  ')])[0]['exclusion_reason'])


if __name__ == '__main__':
    unittest.main()
