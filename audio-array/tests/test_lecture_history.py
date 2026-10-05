import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lecture_history import read_history_page


def row(i, **extra):
    return {'through_seconds': i, 'generated_at': i,
            'headline': {'text': f'Synthetic point {i}', 'source_ids': [f's{i}']},
            'translations': [{'source_id': f's{i}', 'text': 'synthetic'}], **extra}


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'analysis-history.jsonl'

    def write(self, items):
        self.path.write_text(''.join(json.dumps(item) + '\n' for item in items))

    def page(self, **kw):
        return read_history_page(self.root, session_id='session-a', **kw)

    def test_pages_go_beyond_sixty_and_survive_append(self):
        self.write([row(i, artifact_dir='/private/synthetic', unexpected='not for UI') for i in range(75)])
        first = self.page(limit=60, through_generated_at=100)
        self.assertEqual(list(range(74, 14, -1)), [r['through_seconds'] for r in first['items']])
        self.assertNotIn('translations', first['items'][0])
        self.assertNotIn('artifact_dir', first['items'][0])
        self.assertNotIn('unexpected', first['items'][0])
        with self.path.open('a') as f:
            f.write(json.dumps(row(80)) + '\n')
        second = self.page(cursor=first['next_cursor'])
        self.assertEqual(list(range(14, -1, -1)), [r['through_seconds'] for r in second['items']])
        self.assertFalse(second['has_more'])
        self.assertIsNone(second['next_cursor'])

    def test_cutoff_and_bad_or_incomplete_records_are_explicit(self):
        self.write([row(1), row(2, generated_at=None), row(3, generated_at=100), {'bad': True}])
        with self.path.open('ab') as f:
            f.write(b'not-json\n' + json.dumps(row(4)).encode())
        result = self.page(through_generated_at=50)
        self.assertEqual([1], [r['through_seconds'] for r in result['items']])
        self.assertEqual(dict(malformed=2, incomplete=1, missing_generated_at=1, future=1), result['skipped'])
        with patch('lecture_history.time.time', return_value=2):
            self.assertEqual(2, self.page(through_generated_at=1000)['snapshot_generated_at'])

    def test_cursor_rejects_other_session_and_modified_snapshot(self):
        self.write([row(1), row(2)])
        cursor = self.page(limit=1)['next_cursor']
        with self.assertRaises(ValueError):
            read_history_page(self.root, session_id='session-b', cursor=cursor)
        with self.assertRaises(ValueError):
            self.page(cursor=cursor, through_generated_at=1)
        self.write([row(3), row(4)])
        with self.assertRaises(ValueError):
            self.page(cursor=cursor)
        self.path.unlink()
        with self.assertRaises(ValueError):
            self.page(cursor=cursor)

    def test_invalid_limits_cursor_and_empty_history(self):
        self.assertEqual([], self.page()['items'])
        for limit in (0, 61, True, '30'):
            with self.assertRaises(ValueError):
                self.page(limit=limit)
        for cursor in ('bad', '', 'a' * 2049, 3):
            with self.assertRaises(ValueError):
                self.page(cursor=cursor)

    def test_size_limit_and_symlink_are_rejected_without_reading_other_files(self):
        self.write([row(1)])
        with patch('lecture_history.MAX_HISTORY_BYTES', 10):
            with self.assertRaisesRegex(ValueError, 'read limit'):
                self.page()
        target = self.root / 'synthetic-other.jsonl'
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            self.page()

    def test_equal_generation_times_keep_persisted_order(self):
        self.write([row(i, generated_at=2) for i in range(3)])
        first = self.page(limit=1)
        second = self.page(cursor=first['next_cursor'])
        self.assertEqual([2, 1, 0], [r['through_seconds'] for r in first['items'] + second['items']])

    def test_historical_block_translations_remain_available(self):
        blocks = [{'text': 'Synthetic historical translation', 'source_ids': ['s1']}]
        self.write([row(1, block_translations=blocks)])
        self.assertEqual(blocks, self.page()['items'][0]['block_translations'])
