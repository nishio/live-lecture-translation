import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lecture_cloud_scope import CloudScope, CloudScopeError, NANOSECONDS


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()


class CloudScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.audio = self.root / 'approved-audio.wav'
        self.audio.write_bytes(b'only a hash fixture, never played or transcribed')
        self.auth_path = self.root / 'authorization.json'
        self.ledger = self.root / 'scope'
        self.authorization = {'human_approved': True, 'allowed_dates': ['2030-06-03', '2030-06-04'],
            'allowed_models': ['gpt-6-luna', 'gpt-6.1-sol'],
            'replay_sources': [{'path': str(self.audio), 'sha256': hashlib.sha256(self.audio.read_bytes()).hexdigest()}],
            'microphone_date': '2030-06-04', 'microphone_max_seconds': 14400}
        self.now = stamp('2030-06-04T16:00:00')
        self.write_auth()

    def write_auth(self):
        self.auth_path.write_text(json.dumps(self.authorization), encoding='utf-8')

    def scope(self):
        return CloudScope(self.auth_path, self.ledger, now=lambda: self.now)

    def microphone(self, identity='mic-1', start='2030-06-04T10:00:00'):
        return {'id': identity, 'source_kind': 'microphone', 'started_at': stamp(start)}

    def replay(self, identity='replay-1'):
        return {'id': identity, 'source_kind': 'replay', 'started_at': stamp('2030-06-03T18:00:00'), 'replay_path': str(self.audio)}

    def test_init_only_reads_authorization(self):
        self.scope()
        self.assertFalse(self.ledger.exists())

    def test_missing_or_unapproved_authorization_fails_closed(self):
        for approved in (False, 'true', 1, None):
            with self.subTest(approved=approved):
                self.authorization['human_approved'] = approved
                self.write_auth()
                with self.assertRaises(CloudScopeError):
                    self.scope()
        self.auth_path.unlink()
        with self.assertRaises(CloudScopeError):
            self.scope()

    def test_authorization_rejects_bad_scope_fields(self):
        original = copy.deepcopy(self.authorization)
        cases = [('allowed_dates', ['not-a-date']), ('allowed_dates', []), ('allowed_dates', ['2030-06-04', '2030-06-04']),
                 ('allowed_models', ['other-model']), ('allowed_models', ['gpt-6-luna', 'gpt-6-luna']),
                 ('microphone_date', '2030-06-05'), ('microphone_max_seconds', 21601),
                 ('microphone_max_seconds', True), ('microphone_max_seconds', -1), ('replay_sources', None),
                 ('replay_sources', [{'path': 'relative.wav', 'sha256': '0' * 64}]),
                 ('replay_sources', [{'path': str(self.audio), 'sha256': 'not-a-sha'}])]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.authorization = {**original, field: value}
                self.write_auth()
                with self.assertRaises(CloudScopeError):
                    self.scope()

    def test_duplicate_json_keys_and_nonfinite_values_rejected(self):
        self.auth_path.write_text('{"human_approved":true,"human_approved":false}')
        with self.assertRaises(CloudScopeError): self.scope()
        self.auth_path.write_text('{"human_approved":true,"x":NaN}')
        with self.assertRaises(CloudScopeError): self.scope()

    def test_zero_microphone_allowance_authorizes_only_listed_replay(self):
        self.authorization['microphone_max_seconds'] = 0
        self.write_auth()
        scope = self.scope()
        status = scope.status()
        self.assertFalse(status['authorized_today'])
        self.assertTrue(status['send_authorized_today'])
        self.assertEqual((0, 0, 0), tuple(status[key]
                         for key in ('max_seconds', 'used_seconds', 'remaining_seconds')))
        self.assertTrue(status['quota_exhausted'])
        for duration in (0, .000000001, 60):
            with self.subTest(duration=duration), self.assertRaisesRegex(CloudScopeError, '許可されていません'):
                scope.reserve(self.microphone(), 'gpt-6-luna', duration)
        self.assertFalse(scope.ledger_path.exists())
        receipt = scope.reserve(self.replay(), 'gpt-6-luna', 2400)
        self.assertEqual(receipt['session_reserved_seconds'], 2400)
        self.assertEqual(receipt['microphone_reserved_seconds'], 0)
        self.assertEqual(receipt['microphone_remaining_seconds'], 0)
        unlisted = self.root / 'unlisted.wav'
        unlisted.write_bytes(self.audio.read_bytes())
        with self.assertRaisesRegex(CloudScopeError, '許可リスト'):
            scope.reserve({**self.replay('unlisted'), 'replay_path': str(unlisted)}, 'gpt-6-luna', 60)
        self.audio.write_bytes(b'synthetic changed hash')
        with self.assertRaisesRegex(CloudScopeError, 'SHA'):
            scope.reserve(self.replay(), 'gpt-6-luna', 2400)

    def test_zero_microphone_revocation_preserves_reservations_and_replay_permission(self):
        scope = self.scope()
        scope.reserve(self.microphone(), 'gpt-6-luna', 60)
        before = json.loads(scope.ledger_path.read_text())['sessions']['mic-1']
        self.authorization['microphone_max_seconds'] = 0
        self.write_auth()
        with self.assertRaisesRegex(CloudScopeError, '許可されていません'):
            scope.reserve(self.microphone(), 'gpt-6-luna', 60)
        receipt = scope.reserve(self.replay(), 'gpt-6-luna', 2400)
        self.assertEqual(receipt['microphone_reserved_seconds'], 60)
        self.assertEqual(receipt['microphone_remaining_seconds'], 0)
        self.assertEqual(before, json.loads(scope.ledger_path.read_text())['sessions']['mic-1'])
        self.assertEqual(scope.status()['used_seconds'], 60)

    def test_same_microphone_session_only_reserves_growth(self):
        scope = self.scope()
        first = scope.reserve(self.microphone(), 'gpt-6-luna', 60)
        self.assertEqual(first['delta_seconds'], 60)
        self.assertEqual(first['microphone_reserved_seconds'], 60)
        retry = scope.reserve(self.microphone(), 'gpt-6.1-sol', 60)
        self.assertEqual(retry['delta_seconds'], 0)
        lower = scope.reserve(self.microphone(), 'gpt-6-luna', 30)
        self.assertEqual(lower['session_reserved_seconds'], 60)
        higher = scope.reserve(self.microphone(), 'gpt-6-luna', 120)
        self.assertEqual(higher['delta_seconds'], 60)
        self.assertEqual(higher['microphone_reserved_seconds'], 120)
        self.assertEqual(higher['microphone_remaining_seconds'], 14280)

    def test_reservation_survives_caller_failure_and_new_instance(self):
        self.scope().reserve(self.microphone(), 'gpt-6-luna', 400)
        # No release/rollback exists after an API or separate dollar-budget failure.
        receipt = self.scope().reserve(self.microphone('another'), 'gpt-6-luna', 200)
        self.assertEqual(receipt['microphone_reserved_seconds'], 600)

    def test_all_microphone_sessions_share_four_hours(self):
        scope = self.scope()
        scope.reserve(self.microphone('first'), 'gpt-6-luna', 10000)
        receipt = scope.reserve(self.microphone('second'), 'gpt-6.1-sol', 4400)
        self.assertEqual(receipt['microphone_remaining_seconds'], 0)
        before = scope.ledger_path.read_bytes()
        with self.assertRaisesRegex(CloudScopeError, '累計4時間'):
            scope.reserve(self.microphone('third'), 'gpt-6-luna', .000000001)
        self.assertEqual(scope.ledger_path.read_bytes(), before)
        self.assertEqual(scope.reserve(self.microphone('first'), 'gpt-6.1-sol', 10000)['delta_seconds'], 0)

    def test_fractional_high_water_sums_are_conservative(self):
        scope = self.scope()
        self.assertEqual(scope.reserve(self.microphone('a'), 'gpt-6-luna', .1)['delta_seconds'], .1)
        self.assertEqual(scope.reserve(self.microphone('b'), 'gpt-6-luna', .2)['microphone_reserved_seconds'], .3)
        saved = json.loads(scope.ledger_path.read_text())
        self.assertEqual(saved['sessions']['a']['max_through_nanoseconds'], 100000000)

    def test_expanded_six_hour_authorization_preserves_existing_four_hour_ledger(self):
        scope = self.scope()
        scope.reserve(self.microphone(), 'gpt-6-luna', 14400)
        self.authorization['microphone_max_seconds'] = 21600
        self.write_auth()
        self.assertEqual(scope.status()['used_seconds'], 14400)
        self.assertEqual(scope.status()['remaining_seconds'], 7200)
        fifth = scope.reserve(self.microphone(), 'gpt-6.1-sol', 18000)
        self.assertEqual(fifth['delta_seconds'], 3600)
        self.assertEqual(fifth['microphone_remaining_seconds'], 3600)
        sixth = scope.reserve(self.microphone(), 'gpt-6-luna', 21600)
        self.assertEqual(sixth['microphone_remaining_seconds'], 0)
        before = scope.ledger_path.read_bytes()
        with self.assertRaisesRegex(CloudScopeError, '累計6時間'):
            scope.reserve(self.microphone('next'), 'gpt-6-luna', .000000001)
        self.assertEqual(scope.ledger_path.read_bytes(), before)

    def test_six_hours_is_shared_across_sessions_with_exact_fractional_boundary(self):
        self.authorization['microphone_max_seconds'] = 21600
        self.write_auth()
        scope = self.scope()
        scope.reserve(self.microphone('first'), 'gpt-6-luna', 18000)
        scope.reserve(self.microphone('second'), 'gpt-6-luna', 3599.999999999)
        self.assertEqual(scope.status()['remaining_seconds'], .000000001)
        scope.reserve(self.microphone('third'), 'gpt-6.1-sol', .000000001)
        status = scope.status()
        self.assertEqual(status['used_seconds'], 21600)
        self.assertTrue(status['quota_exhausted'])
        with self.assertRaises(CloudScopeError):
            scope.reserve(self.microphone('fourth'), 'gpt-6-luna', .000000001)

    def test_status_is_pure_and_does_not_create_or_modify_files_or_read_audio(self):
        scope = self.scope()
        with (mock.patch('lecture_cloud_scope._atomic_json', side_effect=AssertionError('write')),
              mock.patch('lecture_cloud_scope._file_sha', side_effect=AssertionError('audio read')),
              mock.patch('pathlib.Path.mkdir', side_effect=AssertionError('mkdir'))):
            empty = scope.status()
        self.assertEqual(empty['used_seconds'], 0)
        self.assertEqual(empty['remaining_seconds'], 14400)
        self.assertFalse(self.ledger.exists())
        scope.reserve(self.microphone(), 'gpt-6-luna', 1800)
        scope.reserve(self.replay(), 'gpt-6-luna', 2400)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        status = scope.status()
        self.assertEqual(status['used_seconds'], 1800)
        self.assertEqual(status['remaining_seconds'], 12600)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()})

    def test_status_distinguishes_microphone_day_and_fails_closed_after_revocation_or_corruption(self):
        scope = self.scope()
        for day, microphone, send in [('2030-06-03T23:00:00', False, True),
                                      ('2030-06-04T10:00:00', True, True),
                                      ('2030-06-05T10:00:00', False, False)]:
            self.now = stamp(day)
            value = scope.status()
            self.assertEqual(value['authorized_today'], microphone)
            self.assertEqual(value['send_authorized_today'], send)
        self.authorization['human_approved'] = False
        self.write_auth()
        with self.assertRaises(CloudScopeError): scope.status()
        self.authorization['human_approved'] = True
        self.write_auth()
        self.now = stamp('2030-06-04T16:00:00')
        scope.reserve(self.microphone(), 'gpt-6-luna', 1)
        scope.ledger_path.write_text('{broken')
        with self.assertRaises(CloudScopeError): scope.status()
        self.assertEqual(scope.ledger_path.read_text(), '{broken')

    def test_status_preserves_usage_when_authorized_limit_is_reduced(self):
        scope = self.scope()
        scope.reserve(self.microphone(), 'gpt-6-luna', 10000)
        self.authorization['microphone_max_seconds'] = 9000
        self.write_auth()
        value = scope.status()
        self.assertEqual(value['max_seconds'], 9000)
        self.assertEqual(value['used_seconds'], 10000)
        self.assertEqual(value['remaining_seconds'], 0)
        self.assertTrue(value['quota_exhausted'])

    def test_today_microphone_is_outside_tomorrow_authorization(self):
        self.now = stamp('2030-06-03T23:30:00')
        with self.assertRaisesRegex(CloudScopeError, 'マイク録音日'):
            self.scope().reserve(self.microphone(start='2030-06-03T20:00:00'), 'gpt-6-luna', 60)
        self.assertFalse((self.ledger / 'ledger.json').exists())

    def test_yesterday_microphone_still_rejected_on_approved_send_day(self):
        with self.assertRaises(CloudScopeError):
            self.scope().reserve(self.microphone(start='2030-06-03T20:00:00'), 'gpt-6-luna', 60)

    def test_jst_date_boundary_and_expired_send_date(self):
        self.now = stamp('2030-06-04T00:00:00')
        receipt = self.scope().reserve(self.replay(), 'gpt-6-luna', 60)
        self.assertEqual(receipt['authorized_date'], '2030-06-04')
        self.now = stamp('2030-06-05T00:00:00')
        with self.assertRaisesRegex(CloudScopeError, '日付'):
            self.scope().reserve(self.replay(), 'gpt-6-luna', 60)

    def test_future_start_and_microphone_crossing_midnight_rejected(self):
        with self.assertRaises(CloudScopeError):
            self.scope().reserve(self.microphone(start='2030-06-04T17:00:00'), 'gpt-6-luna', 60)
        self.now = stamp('2030-06-04T23:59:50')
        with self.assertRaisesRegex(CloudScopeError, '許可日の終わり'):
            self.scope().reserve(self.microphone(start='2030-06-04T23:59:00'), 'gpt-6-luna', 61)

    def test_replay_requires_whitelisted_path_and_actual_hash_each_time(self):
        scope = self.scope()
        receipt = scope.reserve(self.replay(), 'gpt-6-luna', 2400)
        self.assertEqual(receipt['microphone_reserved_seconds'], 0)
        same_bytes = self.root / 'not-approved.wav'; same_bytes.write_bytes(self.audio.read_bytes())
        with self.assertRaisesRegex(CloudScopeError, '許可リスト'):
            scope.reserve({**self.replay('copy'), 'replay_path': str(same_bytes)}, 'gpt-6-luna', 60)
        self.audio.write_bytes(b'modified audio')
        with self.assertRaisesRegex(CloudScopeError, 'SHA'):
            scope.reserve(self.replay(), 'gpt-6-luna', 2400)

    def test_replay_file_deleted_and_relative_path_rejected(self):
        self.audio.unlink()
        with self.assertRaises(CloudScopeError):
            self.scope().reserve(self.replay(), 'gpt-6-luna', 60)
        with self.assertRaises(CloudScopeError):
            self.scope().reserve({**self.replay(), 'replay_path': 'relative.wav'}, 'gpt-6-luna', 60)

    def test_unknown_model_source_invalid_seconds_rejected(self):
        scope = self.scope()
        for seconds in (True, -1, float('nan'), float('inf'), '60', 10 ** 1000):
            with self.subTest(seconds=str(seconds)[:20]), self.assertRaises(CloudScopeError):
                scope.reserve(self.microphone(), 'gpt-6-luna', seconds)
        with self.assertRaises(CloudScopeError): scope.reserve(self.microphone(), 'other-model', 60)
        with self.assertRaises(CloudScopeError): scope.reserve({**self.microphone(), 'source_kind': 'unknown'}, 'gpt-6-luna', 60)

    def test_session_id_reuse_cannot_replace_source_or_start(self):
        scope = self.scope(); scope.reserve(self.microphone(), 'gpt-6-luna', 60)
        with self.assertRaises(CloudScopeError):
            scope.reserve(self.microphone(start='2030-06-04T10:01:00'), 'gpt-6-luna', 60)
        with self.assertRaises(CloudScopeError):
            scope.reserve(self.replay('mic-1'), 'gpt-6-luna', 60)

    def test_authorization_is_reloaded_and_does_not_reset_quota(self):
        scope = self.scope(); first = scope.reserve(self.microphone(), 'gpt-6-luna', 60)
        self.authorization['notes'] = 'Non-authoritative annotation changed'
        self.write_auth()
        second = scope.reserve(self.microphone('other'), 'gpt-6.1-sol', 60)
        self.assertEqual(second['microphone_reserved_seconds'], 120)
        self.assertNotEqual(first['authorization_sha256'], second['authorization_sha256'])
        self.authorization['human_approved'] = False; self.write_auth()
        with self.assertRaises(CloudScopeError): scope.reserve(self.microphone(), 'gpt-6-luna', 120)

    def test_corrupt_ledger_is_not_reset(self):
        scope = self.scope(); scope.reserve(self.microphone(), 'gpt-6-luna', 60)
        scope.ledger_path.write_text('{broken')
        with self.assertRaises(CloudScopeError): scope.reserve(self.microphone(), 'gpt-6-luna', 90)
        self.assertEqual(scope.ledger_path.read_text(), '{broken')

    def test_invalid_ledger_records_fail_closed(self):
        scope = self.scope(); scope.reserve(self.microphone(), 'gpt-6-luna', 60)
        original = json.loads(scope.ledger_path.read_text())
        for field, value in [('max_through_nanoseconds', -1), ('max_through_nanoseconds', True), ('recording_date', '2030-06-03'), ('source_kind', 'unknown')]:
            with self.subTest(field=field):
                ledger = copy.deepcopy(original); ledger['sessions']['mic-1'][field] = value
                scope.ledger_path.write_text(json.dumps(ledger))
                with self.assertRaises(CloudScopeError): scope.reserve(self.microphone(), 'gpt-6-luna', 90)

    def test_storage_error_does_not_authorize_send(self):
        scope = self.scope()
        with mock.patch('lecture_cloud_scope._atomic_json', side_effect=OSError('disk unavailable')):
            with self.assertRaises(CloudScopeError): scope.reserve(self.microphone(), 'gpt-6-luna', 60)
        self.assertFalse(scope.ledger_path.exists())

    def test_post_replace_fsync_failure_preserves_conservative_reservation(self):
        scope = self.scope()
        with mock.patch('lecture_cloud_scope._fsync_dir', side_effect=OSError('fsync error')):
            with self.assertRaises(CloudScopeError): scope.reserve(self.microphone(), 'gpt-6-luna', 60)
        receipt = self.scope().reserve(self.microphone(), 'gpt-6-luna', 60)
        self.assertEqual(receipt['delta_seconds'], 0)
        self.assertEqual(receipt['microphone_reserved_seconds'], 60)

    def test_multiple_processes_cannot_overspend_shared_scope(self):
        source = """import json,sys
sys.path.insert(0,sys.argv[1])
from lecture_cloud_scope import CloudScope, CloudScopeError
scope=CloudScope(sys.argv[2],sys.argv[3],now=lambda:float(sys.argv[5]))
session={'id':sys.argv[4],'source_kind':'microphone','started_at':float(sys.argv[6])}
try:
 scope.reserve(session,'gpt-6-luna',8000)
 print('reserved')
except CloudScopeError:
 print('refused')
"""
        processes = [subprocess.Popen([sys.executable, '-c', source, str(Path(__file__).resolve().parents[1]), str(self.auth_path),
                     str(self.ledger), f'process-{index}', str(self.now), str(self.microphone()['started_at'])],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for index in range(3)]
        outputs = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr)
            outputs.append(stdout.strip())
        self.assertEqual(outputs.count('reserved'), 1)
        self.assertEqual(outputs.count('refused'), 2)
        ledger = json.loads((self.ledger / 'ledger.json').read_text())
        self.assertEqual(sum(row['max_through_nanoseconds'] for row in ledger['sessions'].values()), 8000 * NANOSECONDS)


if __name__ == '__main__':
    unittest.main()
