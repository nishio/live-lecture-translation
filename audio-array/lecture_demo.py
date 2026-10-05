#!/usr/bin/env python3
"""Read-only, publication-timed replay of saved lecture UI results.

Imports no recorder, ASR, cloud client, or live server. All inputs are read once;
the only network listener is authenticated loopback HTTP on a separate port.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs, urlsplit
import wave

from lecture_source_policy import SOURCE_POLICY_VERSION, plan_source_policy


HERE = Path(__file__).resolve().parent
PRODUCTION_ASSETS = HERE / 'lecture-dashboard'
DEMO_ASSETS = HERE / 'lecture-demo'


class DemoDataError(ValueError):
    pass


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def load_json(path):
    def reject_constant(_):
        raise DemoDataError('記録に有限でない数値があります。')
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_constant)


def load_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def source_policy_version(record):
    """Only an explicit supported marker opts saved evidence into new policy."""
    if not isinstance(record, dict) or 'source_policy_version' not in record:
        return None
    version = record['source_policy_version']
    if type(version) is not int or version != SOURCE_POLICY_VERSION:
        raise DemoDataError('保存された翻訳原文ポリシーの版に対応していません。')
    return version


class DemoAudio:
    """One explicitly selected immutable WAV; no request can select another path."""
    def __init__(self, path, expected_seconds):
        try:
            self.data = Path(path).expanduser().resolve(strict=True).read_bytes()
            with wave.open(io.BytesIO(self.data), 'rb') as source:
                if (source.getnchannels(), source.getframerate(), source.getsampwidth(), source.getcomptype()) != (1, 16000, 2, 'NONE'):
                    raise DemoDataError('音声は16 kHz・モノラル・16 bit PCM WAVを指定してください。')
                frames = source.getnframes()
                if frames <= 0:
                    raise DemoDataError('音声にフレームがありません。')
                remaining = frames
                while remaining:
                    part = source.readframes(min(65536, remaining))
                    if not part or len(part) % 2:
                        raise DemoDataError('WAVの音声フレームが途中で切れています。')
                    remaining -= len(part) // 2
                self.seconds = frames / 16000
                if abs(self.seconds - expected_seconds) > 1 / 16000:
                    raise DemoDataError('指定音声の長さが保存セッションと一致しません。')
        except (OSError, EOFError, wave.Error) as exc:
            raise DemoDataError('指定したWAV音声を読み込めません。') from exc


class DemoTimeline:
    def __init__(self, session_dir, audio_file=None):
        self.directory = Path(session_dir).resolve()
        final = load_json(self.directory / 'state.json')
        self.final = deepcopy(final)
        self.simulation = deepcopy(final.get('display_simulation', {}))
        self.session = deepcopy(final['session'])
        self.started_at = self.session['started_at']
        if not numeric(self.started_at):
            raise DemoDataError('セッション開始時刻がありません。')
        self.model = final['analysis']['model']
        self.audio_seconds = final['capture']['audio_seconds']
        if not numeric(self.audio_seconds) or self.audio_seconds <= 0:
            raise DemoDataError('記録された音声長が不正です。')
        self.audio = DemoAudio(audio_file, self.audio_seconds) if audio_file is not None else None
        if self.audio:
            self.audio_seconds = self.audio.seconds
        self.audio_start_seconds = 0
        self.cost = load_json(self.directory / 'cost-report.json')
        self.configuration = load_json(self.directory / 'runtime-manifest.json')['configuration']
        if self.audio and (self.simulation or self.configuration.get('pace') != 1):
            raise DemoDataError('音声同期はpace=1の実記録だけに対応します。加速処理や表示シミュレーションには指定できません。')
        measurements = load_rows(self.directory / 'measurements.jsonl')
        transcripts = load_rows(self.directory / 'transcript.jsonl')
        histories = self._optional_rows('analysis-history.jsonl')
        asr = [row for row in measurements if row.get('stage') == 'asr']
        analyses = [row for row in measurements if row.get('stage') == 'analysis']
        if len(asr) != len(transcripts) or len(analyses) != len(histories):
            raise DemoDataError('公開時刻と原文・解析の記録数が一致しません。推定で補いません。')
        self.asr_events = []
        self.analysis_events = []
        self.translation_events = []
        self.continuous = bool(final.get('translation', {}).get('enabled'))
        self.source_policy_version = source_policy_version(final.get('translation', {}))
        if 'translation_source_policy_version' in self.configuration:
            configured_policy = source_policy_version({'source_policy_version': self.configuration['translation_source_policy_version']})
            self.source_policy_version = self.source_policy_version or configured_policy
        self.failures = self._optional_rows('generation-events.jsonl')
        self.failures = [row for row in self.failures if row.get('event') == 'failed'
                         and row.get('stage') in {'analysis', 'translation'}]
        if any(not numeric(row.get('at')) or row['at'] < self.started_at for row in self.failures):
            raise DemoDataError('失敗記録の時刻を確認できません。')
        self.failures.sort(key=lambda row: row['at'])
        self.capture_markers = [(self.started_at, 0)]
        line_publications = {}
        self.all_lines = {}
        for transcript in transcripts:
            chunk = transcript['chunk']
            matches = [row for row in asr if abs(row['through_seconds'] - chunk['end_seconds']) < .00001]
            if len(matches) != 1:
                raise DemoDataError('ASR分割に対応する公開時刻を一意に確認できません。')
            measurement = matches[0]
            self._validate_measurement(measurement)
            lines = deepcopy(transcript['lines'])
            for line in lines:
                # Never borrow final-state translations. They were not available yet.
                line.pop('translation_ja', None)
                identity = line['id']
                if identity in self.all_lines:
                    raise DemoDataError('原文IDが重複しています。')
                self.all_lines[identity] = deepcopy(line)
                line_publications[identity] = measurement['published_at']
            if not numeric(chunk.get('completed_at')) or chunk['completed_at'] > measurement['published_at']:
                raise DemoDataError('音声分割とASRの公開順を確認できません。')
            self.capture_markers.append((chunk['completed_at'], chunk['end_seconds']))
            self.capture_markers.append((measurement['published_at'], measurement['capture_seconds']))
            self.asr_events.append({'at': measurement['published_at'], 'began_at': measurement['published_at'] - measurement['processing_seconds'],
                                    'ready_at': chunk['completed_at'],
                                    'through_seconds': chunk['end_seconds'], 'lines': lines})
        final_lines = {line['id']: line for line in final['lines']}
        if set(final_lines) != set(self.all_lines) or any(final_lines[identity]['text'] != line['text'] for identity, line in self.all_lines.items()):
            raise DemoDataError('保存された最終原文と分割記録が一致しません。')
        for result, measurement in zip(histories, analyses):
            self._validate_measurement(measurement)
            if abs(result['through_seconds'] - measurement['through_seconds']) > .00001:
                raise DemoDataError('分析履歴と公開記録の音声位置が一致しません。')
            for identity in result.get('source_line_ids', []):
                if identity not in line_publications or line_publications[identity] > measurement['published_at']:
                    raise DemoDataError('分析の根拠に未公開の原文が含まれます。')
            for translation in result.get('translations', []):
                identity = translation['source_id']
                if identity not in line_publications or line_publications[identity] > measurement['published_at']:
                    raise DemoDataError('和訳に未公開の原文が含まれます。')
            for block in result.get('block_translations', []):
                ids = block.get('source_ids', [])
                if not ids or any(identity not in result.get('source_line_ids', []) or
                                  identity not in line_publications or line_publications[identity] > measurement['published_at']
                                  for identity in ids):
                    raise DemoDataError('まとまり訳に未公開または入力外の原文が含まれます。')
            item = deepcopy(result)
            item['recorded_generated_at'] = item.get('generated_at')
            # Publication, including cache hits, is the only reveal/age clock.
            item['generated_at'] = measurement['published_at']
            self.analysis_events.append({'at': measurement['published_at'], 'began_at': measurement['published_at'] - measurement['processing_seconds'], 'result': item})
            self.capture_markers.append((measurement['published_at'], measurement['capture_seconds']))
        self._load_translations(measurements, line_publications)
        self.asr_events.sort(key=lambda item: item['at'])
        self.analysis_events.sort(key=lambda item: item['at'])
        self.translation_events.sort(key=lambda item: item['at'])
        self._load_provisional()
        if not self.asr_events:
            raise DemoDataError('再現できる原文記録がありません。')
        self.capture_markers.sort()
        self.capture_end = max(row['chunk']['completed_at'] for row in transcripts)
        last_times = [self.capture_end, *(event['at'] for event in self.asr_events),
                      *(event['at'] for event in self.analysis_events), *(event['at'] for event in self.translation_events),
                      *(event['at'] for event in self.failures),
                      *(event['published_at'] for event in self.provisional_events),
                      *(event['at'] for event in self.provisional_failures)]
        if self.audio:
            last_times.append(self.started_at + self.audio_start_seconds + self.audio_seconds)
        self.duration = max(last_times) - self.started_at
        self.initial_seconds = 0

    def _optional_rows(self, name):
        path = self.directory / name
        return load_rows(path) if path.is_file() else []

    def _load_provisional(self):
        """Keep rolling previews separate from immutable source/translation evidence."""
        self.provisional_events = self._optional_rows('provisional-history.jsonl')
        self.provisional_failures = self._optional_rows('provisional-events.jsonl')
        final = self.final.get('provisional_asr', {})
        self.provisional_enabled = final.get('enabled') is True
        self.provisional_refresh = final.get('refresh_seconds', 3)
        self.provisional_window = final.get('window_seconds', 15)
        if not self.provisional_enabled and (self.provisional_events or self.provisional_failures):
            raise DemoDataError('速報の公開記録と保存状態が一致しません。')
        if (not numeric(self.provisional_refresh) or self.provisional_refresh < 0
                or (self.provisional_enabled and self.provisional_refresh == 0)
                or not numeric(self.provisional_window) or self.provisional_window <= 0):
            raise DemoDataError('速報の更新間隔・音声窓が不正です。')
        ids = set(self.all_lines)
        last_revision, last_through, last_published = 0, 0, self.started_at
        for event in self.provisional_events:
            fields = ('window_start_seconds', 'through_seconds', 'ready_at', 'started_at', 'published_at', 'processing_seconds')
            if (not isinstance(event, dict) or any(not numeric(event.get(key)) for key in fields)
                    or type(event.get('revision')) is not int or event['revision'] <= last_revision
                    or not 0 <= event['window_start_seconds'] < event['through_seconds'] <= self.audio_seconds
                    or event['through_seconds'] <= last_through
                    or event['through_seconds'] - event['window_start_seconds'] > self.provisional_window + .00001
                    or not self.started_at <= event['ready_at'] <= event['started_at'] <= event['published_at']
                    or event['published_at'] < last_published or event['processing_seconds'] < 0
                    or not isinstance(event.get('lines'), list)):
                raise DemoDataError('速報の版・音声範囲・実公開時刻が不正です。')
            for line in event['lines']:
                if (not isinstance(line, dict) or not isinstance(line.get('id'), str) or not line['id'] or line['id'] in ids
                        or not isinstance(line.get('text'), str)
                        or not numeric(line.get('start_seconds')) or not numeric(line.get('end_seconds'))
                        or not event['window_start_seconds'] - .00051 <= line['start_seconds'] <= line['end_seconds'] <= event['through_seconds'] + .00051):
                    raise DemoDataError('速報の原文ID・本文・音声範囲が不正です。')
                ids.add(line['id'])
            self.capture_markers.append((event['ready_at'], event['through_seconds']))
            last_revision, last_through, last_published = event['revision'], event['through_seconds'], event['published_at']
        for event in self.provisional_failures:
            if (not isinstance(event, dict) or event.get('event') not in {'failed', 'cancelled_before_dispatch'}
                    or type(event.get('revision')) is not int or event['revision'] <= 0
                    or not numeric(event.get('at')) or event['at'] < self.started_at
                    or (event['event'] == 'failed' and not isinstance(event.get('error'), str))
                    or not numeric(event.get('window_start_seconds')) or not numeric(event.get('through_seconds'))
                    or not 0 <= event['window_start_seconds'] < event['through_seconds'] <= self.audio_seconds):
                raise DemoDataError('速報の失敗記録が不正です。')
        self.provisional_failures.sort(key=lambda event: event['at'])
        if self.provisional_enabled:
            if self.provisional_events:
                last = self.provisional_events[-1]
                if any(final.get(key) != last[key] for key in ('revision', 'through_seconds', 'published_at')):
                    raise DemoDataError('速報の最終版と公開記録が一致しません。')
                cleared = not final.get('lines') and self.final.get('asr', {}).get('through_seconds', 0) >= last['through_seconds']
                if not cleared and final.get('lines') != last['lines']:
                    raise DemoDataError('保存された速報の本文と公開記録が一致しません。')
            elif final.get('lines') or final.get('published_at') is not None:
                raise DemoDataError('速報の実公開記録がありません。推定で補いません。')

    def _provisional_snapshot(self, at, asr_through):
        published = [event for event in self.provisional_events if event['published_at'] <= at]
        latest = published[-1] if published else None
        result = {'enabled': self.provisional_enabled, 'state': 'waiting',
                  'refresh_seconds': self.provisional_refresh, 'window_seconds': self.provisional_window,
                  'revision': None, 'window_start_seconds': 0, 'through_seconds': 0,
                  'ready_at': None, 'started_at': None, 'published_at': None, 'lines': [], 'error': None}
        if latest:
            result.update(deepcopy(latest), state='completed')
            # No invented word alignment across the independent ASR paths.
            if asr_through >= latest['through_seconds']:
                result['lines'] = []
        outcomes = [event for event in self.provisional_failures if event['at'] <= at
                    and (latest is None or event['at'] > latest['published_at'])]
        if outcomes:
            failed = [event for event in outcomes if event['event'] == 'failed']
            result.update(state='failed' if outcomes[-1]['event'] == 'failed' else 'paused',
                          error=failed[-1]['error'] if failed else None)
        elif any(event['started_at'] <= at < event['published_at'] for event in self.provisional_events):
            result['state'] = 'running'
        if at >= self.started_at + self.duration:
            final = self.final.get('provisional_asr', {})
            if final.get('state') in {'failed', 'paused'}:
                result.update(state=final['state'], error=final.get('error'))
        return result

    def _load_translations(self, measurements, line_publications):
        histories = self._optional_rows('translation-history.jsonl')
        measured = [row for row in measurements if row.get('stage') == 'translation']
        if len(histories) != len(measured) or (histories and not self.continuous):
            raise DemoDataError('連続翻訳の履歴と公開記録が一致しません。')
        used = set()
        seen_ids, covered = set(), set()
        blocks = []
        for history in histories:
            published = history.get('published_at')
            started = history.get('started_at')
            if not numeric(published) or not numeric(started) or not self.started_at <= started <= published:
                raise DemoDataError('連続翻訳の開始・公開時刻を確認できません。')
            matches = [(index, row) for index, row in enumerate(measured)
                       if index not in used and row.get('published_at') == published and row.get('started_at') == started]
            if len(matches) != 1:
                raise DemoDataError('連続翻訳に対応する公開記録が一意でありません。')
            index, measurement = matches[0]
            used.add(index)
            if (not numeric(measurement.get('processing_seconds')) or measurement['processing_seconds'] < 0
                    or not numeric(measurement.get('through_seconds'))):
                raise DemoDataError('連続翻訳の処理記録が不正です。')
            selection = history.get('selection', {})
            policy_version = source_policy_version(selection)
            excluded = self._translation_exclusions(selection, started, line_publications) if policy_version else {}
            additions = deepcopy(history.get('blocks'))
            if not isinstance(additions, list) or not additions:
                raise DemoDataError('連続翻訳のまとまりがありません。')
            target_ids = []
            for block in additions:
                identities = block.get('source_ids')
                if (not isinstance(block.get('id'), str) or not block['id'] or block['id'] in seen_ids
                        or not isinstance(block.get('text'), str) or not block['text'].strip()
                        or block.get('published_at') != published or not isinstance(identities, list) or not identities):
                    raise DemoDataError('連続翻訳のID・本文・公開時刻が不正です。')
                for identity in identities:
                    source = self.all_lines.get(identity) if isinstance(identity, str) else None
                    if (source is None or identity in covered or line_publications[identity] > started
                            or (source.get('uncertain') and policy_version is None)
                            or identity in excluded or source.get('language') == 'ja' or not source.get('text', '').strip()):
                        raise DemoDataError('連続翻訳の根拠が重複・未公開・対象外です。')
                    covered.add(identity)
                seen_ids.add(block['id'])
                target_ids.extend(identities)
            if target_ids != measurement.get('target_source_ids'):
                raise DemoDataError('連続翻訳の対象IDが公開記録と一致しません。')
            blocks.extend(additions)
            self.translation_events.append({'at': published, 'began_at': started, 'blocks': additions,
                'source_policy_version': policy_version, 'excluded_sources': list(excluded.values())})
        final_blocks = self.final.get('translation', {}).get('blocks', [])
        if self.continuous and blocks != final_blocks:
            raise DemoDataError('保存された最終翻訳と公開履歴が一致しません。')

    def _translation_exclusions(self, selection, started, line_publications):
        """Check frozen request exclusions without reclassifying accepted history."""
        rows = selection.get('excluded_sources', [])
        if not isinstance(rows, list):
            raise DemoDataError('連続翻訳の除外記録が不正です。')
        exclusions = {}
        for row in rows:
            if not isinstance(row, dict):
                raise DemoDataError('連続翻訳の除外記録が不正です。')
            identity, reason = row.get('source_id'), row.get('reason')
            if (not isinstance(identity, str) or identity not in self.all_lines or identity in exclusions
                    or line_publications[identity] > started or not isinstance(reason, str) or not reason.strip()):
                raise DemoDataError('連続翻訳の除外根拠が重複・未公開・不正です。')
            duplicate = row.get('duplicate_of')
            if duplicate is not None and (not isinstance(duplicate, str) or duplicate == identity
                    or duplicate not in self.all_lines or line_publications[duplicate] > started):
                raise DemoDataError('連続翻訳の重複参照が不正です。')
            exclusions[identity] = deepcopy(row)
        return exclusions

    def _validate_measurement(self, row):
        if any(not numeric(row.get(key)) for key in ('published_at', 'through_seconds', 'processing_seconds', 'capture_seconds')):
            raise DemoDataError('公開時刻・処理時間・音声位置が欠けています。')
        if row['published_at'] < self.started_at or row['processing_seconds'] < 0 or not 0 <= row['capture_seconds'] <= self.audio_seconds:
            raise DemoDataError('処理記録の時刻・音声位置が不正です。')

    def metadata(self):
        source_label = '保存セッションの表示再現'
        source_label = self.simulation.get('source_label', source_label)
        landmarks = [{'seconds': 0, 'label': '開始時'}]
        if len(self.analysis_events) > 2:
            # A recorded cloud generation was in flight while ASR progressed.
            event = self.analysis_events[-2]
            landmarks.append({'seconds': max(0, event['at'] - self.started_at - 5), 'label': '解説の到着前'})
        if self.analysis_events:
            landmarks.append({'seconds': self.analysis_events[0]['at'] - self.started_at,
                              'label': '最初の整理'})
        landmarks.append({'seconds': self.duration, 'label': '最後の整理'})
        return {'schema_version': 1, 'session_id': self.session['id'], 'source_label': source_label,
                'started_at': self.started_at, 'audio_seconds': self.audio_seconds, 'duration_seconds': self.duration,
                'audio_url': '/audio.wav' if self.audio else None, 'audio_start_seconds': self.audio_start_seconds,
                'audio_alignment': 'session-start approximation' if self.audio else None,
                'initial_seconds': self.initial_seconds, 'model': self.model, 'configuration': self.configuration,
                'line_count': len(self.all_lines), 'analysis_count': len(self.analysis_events),
                'translation_count': len(self.translation_events),
                'provisional_count': len(self.provisional_events),
                'cache_hits': sum(bool(event['result'].get('cache_hit')) for event in self.analysis_events),
                'recorded_api_usd': self.cost.get('confirmed_api_usd'), 'demo_additional_api_usd': 0,
                'display_simulation': bool(self.simulation), 'preparation_api_usd': self.simulation.get('preparation_api_usd'),
                'landmarks': landmarks,
                'timing_note': self.simulation.get('timing_note',
                    '原文・翻訳・分析は記録された公開時刻で表示。ブラウザ描画遅延は未計測。'
                    + ('厳密な音声開始時刻は未記録。音声はセッション開始からの近似同期で、再生位置を音声進行として表示。' if self.audio
                       else '音声進行は記録された観測点。'))}

    def _stage(self, kind, events, at):
        active = any(event['began_at'] <= at < event['at'] for event in events)
        published = [event for event in events if event['at'] <= at]
        state = 'running' if active else ('completed' if published else 'waiting')
        error, diagnostics = None, None
        failures = [event for event in self.failures if event['stage'] == kind and event['at'] <= at]
        if not active and failures and failures[-1]['at'] > max((event['at'] for event in published), default=-math.inf):
            state, diagnostics = 'failed', deepcopy(failures[-1].get('error'))
            error = '記録された処理に失敗があります。'
        if at >= self.started_at + self.duration:
            final = self.final.get(kind, {})
            if final.get('state') in {'failed', 'paused'}:
                state, error = final['state'], final.get('error')
                diagnostics = deepcopy(final.get('schedule', {}).get('error'))
            elif final.get('state') in {'running', 'waiting'}:
                state, error = 'paused', final.get('error') or '保存時の処理は未完了です。この再生では実行しません。'
        return state, error, diagnostics

    def _schedule(self, kind, events, at, state, capture_seconds, diagnostics=None):
        interval = self.configuration.get('chunk_seconds' if kind == 'asr' else kind + '_interval',
                                          15 if kind == 'asr' else 60 if kind == 'translation' else 120)
        if not numeric(interval) or interval <= 0:
            interval = None
        schedule = {'state': 'idle', 'reason': 'no_pending', 'clock': 'audio' if kind == 'asr' else 'wall',
                    'interval_seconds': interval, 'wait_seconds': interval,
                    'remaining_seconds': None, 'due_at': None, 'error': diagnostics}
        if state in {'failed', 'paused'}:
            return {**schedule, 'state': 'blocked', 'reason': 'failed' if kind == 'asr' else 'manual_retry'}
        if state == 'running':
            return {**schedule, 'state': 'busy', 'reason': 'request'}
        future = [event for event in events if event['at'] > at]
        if kind == 'asr':
            if future and future[0]['ready_at'] <= at:
                return {**schedule, 'state': 'busy', 'reason': 'queued'}
            if capture_seconds < self.audio_seconds and interval is not None:
                boundary = min(self.audio_seconds, (math.floor(capture_seconds / interval) + 1) * interval)
                return {**schedule, 'state': 'waiting', 'reason': 'recording',
                        'remaining_seconds': max(0, boundary - capture_seconds)}
            return {**schedule, 'state': 'complete' if not future else 'idle',
                    'reason': 'no_pending' if not future else 'finalizing'}
        if kind == 'translation' and not self.continuous:
            return {**schedule, 'reason': 'disabled'}
        if future:
            other = self.analysis_events if kind == 'translation' else self.translation_events
            if any(event['began_at'] <= at < event['at'] for event in other):
                return {**schedule, 'state': 'busy', 'reason': 'shared_slot'}
            due = min(event['began_at'] for event in future)
            remaining = max(0, due - at)
            return {**schedule, 'state': 'waiting' if remaining else 'due', 'reason': 'interval',
                    'remaining_seconds': remaining, 'due_at': due,
                    'wait_seconds': max(interval or 0, remaining)}
        if kind == 'translation' and self.continuous and state == 'waiting':
            return {**schedule, 'state': 'blocked', 'reason': 'saved_view'}
        return {**schedule, 'state': 'complete' if at >= self.capture_end else 'idle'}

    def snapshot(self, seconds):
        if not numeric(seconds) or not 0 <= seconds <= self.duration + .001:
            raise DemoDataError('指定時刻がデモの範囲外です。')
        seconds = min(seconds, self.duration)
        at = self.started_at + seconds
        asr = [event for event in self.asr_events if event['at'] <= at]
        analyses = [event for event in self.analysis_events if event['at'] <= at]
        translation_events = [event for event in self.translation_events if event['at'] <= at]
        blocks = [deepcopy(block) for event in translation_events for block in event['blocks']]
        covered = {identity for block in blocks for identity in block['source_ids']}
        lines = [deepcopy(line) for event in asr for line in event['lines']]
        translations = {}
        for event in ([] if self.continuous else analyses):
            for translation in event['result'].get('translations', []):
                translations[translation['source_id']] = translation['text']
        for line in lines:
            if line['id'] in translations:
                line['translation_ja'] = translations[line['id']]
        capture_seconds = max(value for observed_at, value in self.capture_markers if observed_at <= at)
        if self.audio:
            capture_seconds = max(0, min(self.audio_seconds, seconds - self.audio_start_seconds))
        asr_through = asr[-1]['through_seconds'] if asr else 0
        active_asr = any(event['began_at'] <= at < event['at'] for event in self.asr_events)
        asr_state = 'completed' if len(asr) == len(self.asr_events) else ('running' if active_asr else 'waiting')
        asr_error, failed_chunks = None, []
        capture_state, capture_error = 'completed', None
        if at >= self.started_at + self.duration:
            saved_asr = self.final.get('asr', {})
            if saved_asr.get('state') in {'failed', 'paused'} or saved_asr.get('failed_chunks'):
                asr_state = saved_asr.get('state', 'failed')
                asr_error = saved_asr.get('error')
                failed_chunks = deepcopy(saved_asr.get('failed_chunks', []))
            elif saved_asr.get('state') in {'running', 'waiting'}:
                asr_state, asr_error = 'paused', '保存時の認識は未完了です。この再生では実行しません。'
            saved_capture = self.final.get('capture', {})
            if saved_capture.get('state') in {'failed', 'starting', 'recording', 'stopping', 'stalled'}:
                capture_state, capture_error = 'failed', saved_capture.get('error') or '保存時の音声入力の完了は未確認です。'
        analysis_state, analysis_error, analysis_diagnostics = self._stage('analysis', self.analysis_events, at)
        translation_state, translation_error, translation_diagnostics = self._stage('translation', self.translation_events, at)
        if self.source_policy_version is not None:
            planned = plan_source_policy(lines)
            excluded_sources = [{'source_id': line['id'], 'reason': line['exclusion_reason'],
                                 'duplicate_of': line['duplicate_of']}
                                for line in planned if line['exclusion_reason'] is not None]
            eligible = [line for line in planned if line['exclusion_reason'] is None and line.get('language') != 'ja']
            included_uncertain = sum(line['uncertain'] for line in eligible)
            excluded_uncertain = sum(line['uncertain'] and line['exclusion_reason'] is not None for line in planned)
            native = sum(line.get('language') == 'ja' and line['exclusion_reason'] is None for line in planned)
        else:
            # Historical selection was different. Never manufacture new pending
            # work merely because the replay application has a newer policy.
            eligible = [line for line in lines if line.get('language') != 'ja' and not line.get('uncertain') and line.get('text', '').strip()]
            excluded_sources = []
            excluded_uncertain = sum(bool(line.get('uncertain')) for line in lines)
            native = sum(line.get('language') == 'ja' and not line.get('uncertain') for line in lines)
        pending = sum(line['id'] not in covered for line in eligible)
        if self.continuous and pending and translation_state == 'completed':
            translation_state = 'waiting'
        result = deepcopy(analyses[-1]['result']) if analyses else None
        history = []
        for event in analyses:
            item = deepcopy(event['result']); item.pop('translations', None); history.append(item)
        session = {key: self.session[key] for key in ('id', 'started_at', 'language')}
        session.update(title=self.session.get('title') or '保存結果を時系列で表示', source_kind='replay')
        # Capture is deliberately not represented as a current live recording.
        return {'schema_version': 1, 'updated_at': at, 'session': session, 'processing_active': False,
                'capture': {'state': capture_state, 'audio_seconds': capture_seconds, 'last_audio_at': None, 'rms_dbfs': None, 'peak_dbfs': None, 'error': capture_error, 'device': ''},
                'asr': {'state': asr_state, 'through_seconds': asr_through, 'queue_seconds': max(0, capture_seconds - asr_through),
                        'error': asr_error, 'failed_chunks': failed_chunks,
                        'schedule': self._schedule('asr', self.asr_events, at, asr_state, capture_seconds)},
                'analysis': {'state': analysis_state, 'through_seconds': result['through_seconds'] if result else 0,
                             'generated_at': analyses[-1]['at'] if analyses else None, 'provider': self.final['analysis'].get('provider', 'openai'),
                             'model': self.model, 'error': analysis_error, 'result': result,
                             'schedule': self._schedule('analysis', self.analysis_events, at, analysis_state, capture_seconds, analysis_diagnostics)},
                'translation': {'enabled': self.continuous, 'state': translation_state if self.continuous else 'disabled',
                                'blocks': blocks, 'covered_source_ids': [identity for block in blocks for identity in block['source_ids']],
                                'through_seconds': max((block.get('end_seconds', 0) for block in blocks), default=0),
                                'generated_at': translation_events[-1]['at'] if translation_events else None,
                                'pending_lines': pending if self.continuous else 0,
                                'excluded_uncertain_lines': excluded_uncertain,
                                'native_lines': native,
                                **({'source_policy_version': self.source_policy_version, 'excluded_sources': excluded_sources,
                                    'included_uncertain_lines': included_uncertain}
                                   if self.source_policy_version is not None else {}),
                                'error': translation_error, 'retry_required': False, 'worker_alive': False,
                                'schedule': self._schedule('translation', self.translation_events, at, translation_state, capture_seconds, translation_diagnostics)},
                'lines': lines, 'analysis_history': history,
                **({'provisional_asr': self._provisional_snapshot(at, asr_through)} if self.provisional_enabled else {}),
                'capabilities': {'cloud_enabled': False, 'continuous_translation': self.continuous, 'analysis_history_paging': False},
                'message': '', 'demo': {'cursor_seconds': seconds, 'at': at, 'published_lines': len(lines), 'published_analyses': len(analyses),
                                       'published_translations': len(translation_events),
                                       'untranslated_lines': pending if self.continuous else sum(not line.get('translation_ja') for line in eligible),
                                       'audio_input_ended': at >= self.capture_end, 'additional_api_usd': 0}}


def audio_range(header, size):
    """Return one inclusive HTTP byte range; reject ambiguous/multiple ranges."""
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
    if not match or not any(match.groups()):
        raise ValueError('Invalid byte range')
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError('Invalid suffix range')
        return max(0, size - suffix), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise ValueError('Unsatisfiable byte range')
    return start, end


def make_server(timeline, port=8777):
    if port == 8776:
        raise ValueError('デモは会場用アプリの8776を使いません。別ポートを指定してください。')
    token = secrets.token_urlsafe(32)
    html = (PRODUCTION_ASSETS / 'index.html').read_text()
    html = html.replace('<title>講演ライブノート</title>', '<title>保存結果のUIデモ</title>')
    html = html.replace('<link rel="stylesheet" href="style.css">', '<link rel="stylesheet" href="style.css"><link rel="stylesheet" href="demo.css">')
    html = html.replace('<script src="app.js" defer></script>', '<script src="app.js" defer></script><script src="demo.js" defer></script>')
    html = html.replace('<body>', '<body class="lecture-demo">' + (DEMO_ASSETS / 'toolbar.html').read_text())
    # The renderer is unchanged. Its existing CommonJS export is mounted by the
    # demo with an injected clock/transport, suppressing only production auto-boot.
    renderer = '(function(document,module){\n' + (PRODUCTION_ASSETS / 'app.js').read_text() + '\nwindow.LectureDemoRenderer=module.exports;\n})(undefined,{exports:{}});\n'
    assets = {'/': (html.encode(), 'text/html; charset=utf-8'), '/app.js': (renderer.encode(), 'text/javascript; charset=utf-8'),
              '/style.css': ((PRODUCTION_ASSETS / 'style.css').read_bytes(), 'text/css; charset=utf-8'),
              '/demo.js': ((DEMO_ASSETS / 'demo.js').read_bytes(), 'text/javascript; charset=utf-8'),
              '/demo.css': ((DEMO_ASSETS / 'demo.css').read_bytes(), 'text/css; charset=utf-8')}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def send(self, status, value, kind='application/json; charset=utf-8', login=False, headers=None):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self'; object-src 'none'; frame-ancestors 'none'")
            if login: self.send_header('Set-Cookie', f'lecture_demo_{self.server.server_port}={token}; HttpOnly; SameSite=Strict; Path=/')
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command == 'HEAD': return
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass

        def host_ok(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def authorized(self):
            try:
                cookie = cookies.SimpleCookie(self.headers.get('Cookie', '')).get(f'lecture_demo_{self.server.server_port}')
                return bool(cookie and cookie.value.isascii() and secrets.compare_digest(cookie.value, token))
            except (cookies.CookieError, TypeError): return False

        def do_GET(self):
            if not self.host_ok(): return self.send(403, {'error': 'Hostを確認してください。'})
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            supplied = query.get('token', [''])[0]
            login = parsed.path == '/' and supplied.isascii() and secrets.compare_digest(supplied, token)
            if not (login or self.authorized()): return self.send(403, {'error': 'デモの起動URLから開いてください。'})
            if parsed.path in assets: return self.send(200, *assets[parsed.path], login=login)
            if parsed.path == '/audio.wav' and timeline.audio:
                data = timeline.audio.data
                size = len(data)
                requested = self.headers.get('Range')
                if requested is None:
                    return self.send(200, data, 'audio/wav', headers={'Accept-Ranges': 'bytes'})
                try:
                    start, end = audio_range(requested, size)
                except (ValueError, OverflowError):
                    return self.send(416, b'', 'audio/wav', headers={'Accept-Ranges': 'bytes', 'Content-Range': f'bytes */{size}'})
                return self.send(206, data[start:end + 1], 'audio/wav',
                                 headers={'Accept-Ranges': 'bytes', 'Content-Range': f'bytes {start}-{end}/{size}'})
            if parsed.path == '/api/identity': return self.send(200, {'app_id': 'live-lecture-demo', 'schema_version': 1, 'read_only': True})
            if parsed.path == '/api/demo': return self.send(200, timeline.metadata())
            if parsed.path == '/api/devices': return self.send(200, {'devices': [], 'error': 'このデモではマイクを使用しません。'})
            if parsed.path == '/api/state':
                try:
                    values = query.get('at', [str(timeline.initial_seconds)])
                    if len(values) != 1: raise ValueError('時刻は1件だけ指定してください。')
                    return self.send(200, timeline.snapshot(float(values[0])))
                except (ValueError, OverflowError) as exc: return self.send(400, {'error': str(exc)})
            return self.send(404, {'error': 'Not found'})

        do_HEAD = do_GET

        def do_POST(self):
            if not self.host_ok() or not self.authorized(): return self.send(403, {'error': '認証が必要です。'})
            return self.send(405, {'error': '閲覧専用です。録音・認識・API送信の操作はありません。'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    return server, f'http://127.0.0.1:{server.server_port}/?token={token}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8777)
    parser.add_argument('--audio-file', type=Path, help='explicit matching 16 kHz mono PCM16 WAV for authenticated browser playback')
    parser.add_argument('--check', action='store_true', help='validate saved records without starting a server')
    args = parser.parse_args()
    timeline = DemoTimeline(args.session, audio_file=args.audio_file)
    if args.check:
        print(json.dumps(timeline.metadata(), ensure_ascii=False, indent=2)); return
    server, url = make_server(timeline, args.port)
    print('保存結果の表示再現・追加API $0。録音・ASR・外部APIは実行しません。'
          + ('指定した保存音声をブラウザで再生できます。' if timeline.audio else '音声再生なし。'), flush=True)
    print(url, flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__':
    main()
