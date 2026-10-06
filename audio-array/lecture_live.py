#!/usr/bin/env python3
"""Mac mono lecture capture with independent recording and incremental understanding.

The HTTP server binds only to loopback. Nothing records until Start is requested.
Replay supplies only elapsed audio, never a completed lecture analysis. Originals,
ASR responses and generated analyses remain under gitignored data/results.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import queue
import random
import secrets
import shutil
import signal
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import wave
import webbrowser

from lecture_source_policy import SOURCE_POLICY_VERSION, plan_source_policy, source_metadata
from local_inference import InferenceCancelled
from processing_control import (ProcessingStopped, processing_scope,
                                check_processing_allowed, current_cancel_event)

REPO = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).with_name('lecture-dashboard')
MODEL_METADATA = REPO / 'data/event-audio/model-cache/whisper-turbo.json'
ACTIVE = {'starting', 'recording', 'stalled', 'stopping'}
OFFLINE_RECHECK_SECONDS = 30
MAX_AUTO_RETRIES = 3
AUTO_RETRY_WINDOW_SECONDS = 300
AUTO_RETRY_BASE_SECONDS = 2
AUTO_RETRY_MAX_DELAY_SECONDS = 60
TRANSLATION_MAX_WAIT_SECONDS = 30.0
TRANSLATION_LOOKAHEAD_SECONDS = 2.0


def recovery_state():
    return {'attempts': 0, 'started': None, 'next': None, 'delay': None, 'not_before': None,
            'paused': False, 'exhausted': False, 'error': None,
            'manual_requested': False, 'manual_admitted': False}


class LectureOfflineError(RuntimeError):
    pass


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def append_json(path, value):
    with Path(path).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def save_runtime(destination, configuration):
    """Retain the exact runtime source, independently of later working-tree edits."""
    destination = Path(destination)
    sources = [Path(__file__), Path(__file__).with_name('lecture_capture.py'),
               Path(__file__).with_name('lecture_capture_native.swift'),
               Path(__file__).with_name('lecture_analysis.py'),
               Path(__file__).with_name('lecture_translation.py'),
               Path(__file__).with_name('lecture_source_policy.py'),
               Path(__file__).with_name('lecture_cloud_scope.py'),
               Path(__file__).with_name('lecture_readiness.py'),
               Path(__file__).with_name('event_insights.py'),
               Path(__file__).with_name('event_insights_cloud.py'),
               Path(__file__).with_name('local_inference.py'),
               Path(__file__).with_name('processing_control.py'),
               Path(__file__).with_name('lecture_provisional.py'),
               Path(__file__).with_name('catchup_page.py'),
               Path(__file__).with_name('transcribe_local.py'), *ASSETS.glob('*')]
    hashes = {}
    for source in sources:
        if source.is_file():
            relative = source.relative_to(REPO)
            target = destination / 'source-at-start' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            hashes[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
    atomic_json(destination / 'runtime-manifest.json', {'saved_at': time.time(),
        'configuration': configuration, 'source_sha256': hashes})


def blank_state():
    return {'schema_version': 1, 'updated_at': time.time(), 'session': None,
            'processing_stop_requested': False, 'processing_stop_status': None,
            'provisional_asr': {'enabled': False, 'state': 'idle', 'refresh_seconds': 0,
                'window_seconds': 15, 'revision': None, 'window_start_seconds': None,
                'through_seconds': 0, 'ready_at': None, 'started_at': None,
                'published_at': None, 'lines': [], 'error': None},
            'capture': {'state': 'idle', 'audio_seconds': 0, 'last_audio_at': None,
                        'rms_dbfs': None, 'peak_dbfs': None, 'error': None},
            'asr': {'state': 'idle', 'through_seconds': 0, 'queue_seconds': 0, 'error': None,
                    'failed_chunks': []},
            'analysis': {'state': 'idle', 'through_seconds': 0, 'generated_at': None,
                         'provider': 'local', 'model': None, 'error': None, 'result': None},
            'translation': {'enabled': False, 'state': 'idle', 'blocks': [], 'pending_lines': 0,
                'source_policy_version': SOURCE_POLICY_VERSION, 'excluded_sources': [],
                'included_uncertain_lines': 0,
                'ready_lines': 0, 'waiting_lines': 0,
                'excluded_uncertain_lines': 0, 'native_lines': 0, 'through_seconds': 0,
                'error': None, 'generated_at': None, 'worker_alive': False,
                'completion_confirmed': True, 'retry_required': False},
            'lines': [], 'analysis_history': [], 'message': 'マイクを選び、録音を開始してください。'}


def content_lines(lines):
    """Select original rows while keeping derived noise decisions separate."""
    return [row for row, decision in zip(lines, plan_source_policy(lines)) if not decision['exclusion_reason']]


def legacy_source_policy(state):
    # New sessions always have a version. Opening an old saved state must not
    # reinterpret its completed coverage as newly pending work.
    translation = state.get('translation', {})
    if 'source_policy_version' in translation:
        version = translation['source_policy_version']
        if type(version) is not int or version != SOURCE_POLICY_VERSION:
            raise ValueError('保存された翻訳原文ポリシーの版に対応していません。')
        return False
    return bool(state.get('session'))


def exclusion_audit(planned):
    return [{'source_id': row['id'], 'reason': row['exclusion_reason'], 'duplicate_of': row['duplicate_of']}
            for row in planned if row['exclusion_reason']]


def select_analysis_lines(lines, previous, *, window_seconds=180, max_lines=100):
    """Prefer new context, retaining prior evidence when it fits; never future input."""
    usable = content_lines(lines)
    if not usable:
        return []
    end = max(line['end_seconds'] for line in usable)
    recent = [line for line in usable if line['end_seconds'] >= end - window_seconds]
    recent = recent[-max_lines:]
    wanted = set()
    if previous:
        for name in ('headline', 'summary', 'flow', 'questions'):
            items = previous.get(name, [])
            for item in ([items] if isinstance(items, dict) else items):
                wanted.update(item.get('source_ids', []))
    retained = [line for line in usable if line['id'] in wanted and line not in recent]
    remaining = max_lines - len(recent)
    return sorted(retained[-remaining:] + recent if remaining else recent,
                  key=lambda line: (line['start_seconds'], line['id']))


def bounded_analysis_input(lines, previous, *, include_block_translations=False, include_line_translations=True):
    """Select an explicit, auditable recent excerpt within the complete request cap."""
    from lecture_analysis import build_snapshot_request
    from event_insights import InputTooLargeError
    planned = plan_source_policy(lines)
    breaks = [{'start_seconds': line['start_seconds'], 'end_seconds': line['end_seconds']}
              for line in planned if line['exclusion_reason']] if include_block_translations else None
    selected = select_analysis_lines(lines, previous)
    considered = len(selected)
    while selected:
        targets = [line['id'] for line in selected if line.get('language') != 'ja'
                   and not line.get('translation_ja')][-24:] if include_line_translations else []
        try:
            request = build_snapshot_request(selected, previous, translation_ids=targets,
                include_block_translations=include_block_translations, block_translation_breaks=breaks)
            if request['input_bytes'] <= 22000:
                return selected, targets, {'considered_lines': considered, 'selected_lines': len(selected),
                    'source_policy_version': SOURCE_POLICY_VERSION, 'excluded_sources': exclusion_audit(planned),
                    'omitted_for_request_limit': considered - len(selected),
                    'input_bytes': request['input_bytes'], 'policy': 'recent_180_seconds_plus_prior_evidence',
                    'include_block_translations': include_block_translations,
                    'block_translation_breaks': request['block_translation_breaks'],
                    'complete_lecture_coverage': False}
        except InputTooLargeError:
            pass
        if len(selected) == 1:
            raise InputTooLargeError('最新の原文1行が解析入力の上限を超えています。原文は保存されています。')
        selected = selected[1:]
    return [], [], {}


class LocalTranscriber:
    """Reuse the pinned MLX model across workers; hash weights once per app."""
    def __init__(self, metadata=MODEL_METADATA):
        self.metadata_path = Path(metadata)
        self.model = None

    def _check_model(self):
        from transcribe_local import sha256
        check_processing_allowed()
        if self.model is None:
            model = json.loads(self.metadata_path.read_text())
            model_path = Path(model['local_path']).resolve()
            weights = model_path / 'weights.safetensors'
            if not weights.is_file():
                weights = model_path / 'weights.npz'
            paths = [self.metadata_path, weights, model_path / 'config.json']
            identities = {str(p): (p.stat().st_size, p.stat().st_mtime_ns, sha256(p)) for p in paths}
            check_processing_allowed()
            self.model, self.identities = model, identities
        for path, identity in self.identities.items():
            observed = Path(path).stat()
            if (observed.st_size, observed.st_mtime_ns) != identity[:2]:
                raise ValueError('認識モデルが処理中に変更されました。')

    def prepare(self, language):
        """Load and exercise the same model cache before any real audio arrives.

        One synthetic 3-second window, one decode, at most 32 sampled tokens.
        No recognizer output is retained or admitted as source evidence.
        """
        self._check_model()
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        from local_inference import inference_slot
        before = time.monotonic()
        with inference_slot('lecture-asr-prepare', cancel=current_cancel_event()):
            check_processing_allowed()
            acquired = time.monotonic()
            import numpy as np
            import mlx.core as mx
            from mlx_whisper.audio import log_mel_spectrogram, pad_or_trim, N_FRAMES, N_SAMPLES
            from mlx_whisper.transcribe import ModelHolder
            from mlx_whisper.decoding import decode, DecodingOptions
            check_processing_allowed()
            model = ModelHolder.get_model(self.model['local_path'], mx.float16)
            check_processing_allowed()
            mel = log_mel_spectrogram(np.zeros(3 * 16000, dtype=np.float32),
                                     n_mels=model.dims.n_mels, padding=N_SAMPLES)
            mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
            check_processing_allowed()
            decode(model, mel, DecodingOptions(task='transcribe',
                language=None if language == 'auto' else language, temperature=0., sample_len=32))
        return {'input_kind': 'synthetic_silence', 'input_seconds': 3, 'max_decode_tokens': 32,
                'retained_source_lines': 0, 'measured_api_usd': 0,
                'inference_queue_wait_seconds': acquired - before,
                'inference_seconds': time.monotonic() - acquired,
                'model_file_sha256': {path: value[2] for path, value in self.identities.items()}}

    def __call__(self, chunk, destination, language):
        from transcribe_local import read_mono, sha256
        from local_inference import inference_slot
        check_processing_allowed()
        audio = read_mono(chunk['path'])
        self._check_model()
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        before = time.monotonic()
        with inference_slot('lecture-asr', cancel=current_cancel_event()):
            check_processing_allowed()
            acquired = time.monotonic()
            import mlx_whisper
            check_processing_allowed()
            result = mlx_whisper.transcribe(audio, path_or_hf_repo=self.model['local_path'],
                verbose=None, task='transcribe', language=None if language == 'auto' else language,
                temperature=0., condition_on_previous_text=False, initial_prompt=None)
        report = {'schema_version': 1, 'source': str(chunk['path']),
                  'source_sha256': sha256(chunk['path']), 'model': self.model,
                  'model_file_sha256': {p: value[2] for p, value in self.identities.items()},
                  'inference_queue_wait_seconds': acquired - before,
                  'inference_seconds': time.monotonic() - acquired,
                  'chunks': [{'index': 0, 'source_start_seconds': 0,
                              'source_end_seconds': len(audio) / 16000, 'raw_result': result}]}
        atomic_json(destination, report)
        return report


class LectureApp:
    def __init__(self, *, data_root=None, results_root=None, chunk_seconds=15,
                 analysis_interval=None, model_metadata=MODEL_METADATA,
                 transcriber=None, analyzer=None, capture_factory=None, allow_cloud=False, cloud_scope=None,
                 readiness=None, default_model=None, default_language='auto', continuous_translation=False,
                 translation_interval=60, translator=None,
                 provisional_refresh_seconds=0, provisional_window_seconds=15, asr_preparer=None):
        self.data_root = Path(data_root or REPO / 'data/event-audio/mac-live')
        self.results_root = Path(results_root or REPO / 'results/event-audio/mac-live')
        from lecture_provisional import validate_settings
        validate_settings(provisional_refresh_seconds, provisional_window_seconds)
        self.provisional_refresh_seconds = provisional_refresh_seconds
        self.provisional_window_seconds = provisional_window_seconds
        self._provisional_pending = None
        self._provisional_last_end = self._provisional_revision = 0
        self.chunk_seconds = chunk_seconds
        self.analysis_interval = analysis_interval if analysis_interval is not None else (120 if continuous_translation else 30)
        if type(continuous_translation) is not bool or not math.isfinite(translation_interval) or translation_interval <= 0:
            raise ValueError('連続翻訳の設定が不正です。')
        self.continuous_translation = continuous_translation
        self.translation_interval = translation_interval
        self.translator_override = translator
        self.model_metadata = model_metadata
        self.transcriber_override = transcriber
        self.transcriber = transcriber if transcriber is not None else LocalTranscriber(model_metadata)
        self._asr_preparer = asr_preparer or (self.transcriber.prepare if transcriber is None else None)
        self.preparation_thread = None
        self.preparation_cancel = threading.Event()
        self.asr_preparation = {'state': 'idle' if self._asr_preparer else 'ready',
            'started_at': None, 'completed_at': None, 'duration_seconds': None,
            'error': None, 'stop_requested': False, 'preparation_path': None}
        self.analyzer_override = analyzer
        self.capture_factory = capture_factory
        self.allow_cloud = allow_cloud
        self.cloud_scope = cloud_scope
        self.readiness = readiness
        self.default_model = default_model or ('gpt-6.1-sol' if allow_cloud else 'qwen3:4b')
        self.default_language = default_language
        self.started_at = time.time()
        self.lock = threading.RLock()
        self.state = blank_state()
        self.recorder = self.worker = self.source_thread = None
        self.cloud_workers = {'analysis': None, 'translation': None}
        self._cloud_outcomes = {'analysis': None, 'translation': None}
        self.translation_retry_event = threading.Event()
        self._generation_last_started = {'analysis': -math.inf, 'translation': -math.inf}
        self._analysis_last_source_id = None
        self._analysis_manual_required = False
        self._generation_offline = {'analysis': False, 'translation': False}
        self._recovery = {kind: recovery_state() for kind in ('analysis', 'translation')}
        self._retry_jobs = {}
        self._translation_boundary_cache = None
        self.source_done = threading.Event()
        self.stop_source = threading.Event()
        self.retry_event = threading.Event()
        self.audio_queue = queue.Queue()
        self.session_dir = self.result_dir = None
        self.last_persist = 0
        self.closing = False
        self.abort_processing = threading.Event()
        self.accepted_chunks = {}
        self.inference_unconfirmed = False
        self.worker_finishing = False
        self.chunk_completed_at = {}
        self.cloud_baseline_keys = set()

    def _cloud_alive(self, kind=None):
        workers = self.cloud_workers.values() if kind is None else (self.cloud_workers[kind],)
        return any(worker is not None and worker.is_alive() for worker in workers)

    def snapshot(self):
        with self.lock:
            self._refresh_translation_locked()
            self._refresh_stopped_locked()
            self._refresh_provisional_locked()
            schedules = {kind: self._schedule_locked(kind) for kind in ('analysis', 'translation')}
            result = deepcopy(self.state)
            result['updated_at'] = time.time()
            preparation_alive = bool(self.preparation_thread and self.preparation_thread.is_alive())
            if self.result_dir is not None or not self.state.get('session'):
                result['asr_preparation'] = {**deepcopy(self.asr_preparation), 'worker_alive': preparation_alive}
            result['asr']['queue_seconds'] = max(0, result['capture']['audio_seconds'] - result['asr']['through_seconds'])
            result['asr']['schedule'] = self._asr_schedule_locked()
            if result.get('provisional_asr') is not None:
                result['provisional_asr']['schedule'] = self._provisional_schedule_locked()
            cloud_alive = self._cloud_alive()
            result['processing_active'] = bool(preparation_alive or cloud_alive or
                (self.worker and self.worker.is_alive() and not self.worker_finishing))
            result['analysis']['worker_alive'] = bool(self._cloud_alive('analysis') or
                (result['analysis']['provider'] == 'local' and result['analysis']['state'] == 'running'
                 and self.worker and self.worker.is_alive()))
            result['analysis']['completion_confirmed'] = (not result['analysis']['worker_alive'] and
                result['analysis']['state'] not in {'running', 'paused'} and not self.inference_unconfirmed)
            analysis_lines = ([row for row in result['lines'] if not row.get('uncertain')]
                              if legacy_source_policy(result) else content_lines(result['lines']))
            result['analysis']['untranslated_lines'] = sum(1 for row in analysis_lines
                if row.get('language') != 'ja' and not row.get('translation_ja'))
            for kind in ('analysis', 'translation'):
                result[kind]['schedule'] = schedules[kind]
            result['capabilities'] = {'cloud_enabled': self.allow_cloud,
                                     'cloud_models': ['gpt-6-luna', 'gpt-6.1-sol'],
                                     'default_provider': 'openai' if self.allow_cloud else 'local',
                                     'default_model': self.default_model,
                                     'default_language': self.default_language,
                                     'analysis_interval_seconds': self.analysis_interval}
            result['capabilities'].update(continuous_translation=self.continuous_translation,
                continuous_translation_enabled=self.continuous_translation,
                translation_interval_seconds=self.translation_interval,
                analysis_history_paging=True, parallel_cloud_stages=True,
                provisional_refresh_seconds=self.provisional_refresh_seconds,
                provisional_window_seconds=self.provisional_window_seconds)
            from lecture_readiness import AGENDA
            result['capabilities']['agenda'] = deepcopy(AGENDA)
            if self.readiness:
                result['preflight'] = self.readiness.snapshot()
            if self.allow_cloud:
                try:
                    from event_insights_cloud import budget_status
                    result['cloud_budget'] = budget_status()
                except Exception:
                    result['cloud_budget'] = {'error': 'API費用台帳を確認できません。'}
                try:
                    result['cloud_scope'] = self.cloud_scope.status()
                except Exception:
                    result['cloud_scope'] = {'error': '本文送信の残り時間を確認できません。'}
            return result

    def _refresh_stopped_locked(self):
        if not self.state.get('processing_stop_requested') or self.result_dir is None:
            return
        asr = self.state['asr']
        unprocessed_audio = (not self.audio_queue.empty()
                             or asr['through_seconds'] < self.state['capture']['audio_seconds'])
        remaining_audio = not self.source_done.is_set() or unprocessed_audio
        if asr['state'] not in {'running', 'failed'}:
            asr['state'] = 'paused' if remaining_audio or self.inference_unconfirmed else (
                'failed' if asr['failed_chunks'] else 'completed')
        for kind in ('analysis', 'translation'):
            stage = self.state[kind]
            enabled = stage['provider'] != 'off' if kind == 'analysis' else stage['enabled']
            if kind == 'translation':
                pending = bool(stage['pending_lines']) or unprocessed_audio
                stage['retry_required'] = False
            else:
                pending = unprocessed_audio or any(row['end_seconds'] > stage['through_seconds']
                    for row in content_lines(self.state['lines']))
            if enabled and pending and stage['state'] not in {'running', 'failed'}:
                stage['state'] = 'paused'
            elif (enabled and not pending and stage['state'] == 'paused'
                    and self.source_done.is_set() and not asr['failed_chunks']
                    and not self.inference_unconfirmed):
                # A final admitted ASR may add only native/filler rows. Empty
                # eligible coverage needs no translation; analysis still needs
                # evidence of an actually published result.
                if kind == 'translation' or (stage.get('result') and stage.get('generated_at') is not None):
                    stage['state'] = 'completed'
            if kind == 'translation':
                stage['completion_confirmed'] = not stage['worker_alive'] and stage['state'] not in {'running', 'paused'}
        active = bool((self.source_thread and self.source_thread.is_alive() and not self.source_done.is_set())
            or (self.worker and self.worker.is_alive() and not self.worker_finishing)
            or self._cloud_alive() or self.inference_unconfirmed)
        if self.recorder:
            observed = self.recorder.snapshot()
            active = active or not (observed.get('stop_confirmed', False) and observed.get('callbacks_confirmed', False))
        self.state['processing_stop_status'] = 'stopping' if active else 'stopped'

    def _provisional_schedule_locked(self):
        """Schedule of the visible rolling text, separate from canonical ASR."""
        stage = self.state.get('provisional_asr', {})
        interval = stage.get('refresh_seconds', self.provisional_refresh_seconds)
        result = {'state': 'idle', 'reason': 'no_source', 'clock': 'audio',
                  'interval_seconds': interval, 'wait_seconds': interval,
                  'remaining_seconds': None, 'due_at': None}
        def status(state, reason):
            result.update(state=state, reason=reason)
            return result
        if not stage.get('enabled'):
            return status('idle', 'disabled')
        if not self.state['session']:
            return result
        if self.result_dir is None:
            return status('blocked', 'saved_view')
        if stage['state'] == 'running':
            return status('busy', 'request')
        if stage['state'] == 'failed' or stage.get('error'):
            return status('blocked', 'failed')
        if self.state.get('processing_stop_requested'):
            return status('blocked', 'stopped')
        if self.closing or self.abort_processing.is_set() or self.inference_unconfirmed or stage['state'] == 'paused':
            return status('blocked', 'closed')
        if self._provisional_pending is not None:
            return status('busy', 'queued')
        capture, asr = self.state['capture'], self.state['asr']
        if self.source_done.is_set():
            if not self.audio_queue.empty() or asr['state'] == 'running':
                return status('busy', 'queued')
            if capture['state'] == 'failed':
                return status('blocked', 'failed')
            if (stage['state'] == 'completed' and max(stage.get('through_seconds', 0), asr['through_seconds'])
                    + 1e-6 >= capture['audio_seconds']):
                return status('complete', 'no_pending')
            if asr['state'] == 'failed':
                return status('blocked', 'failed')
            return status('idle', 'finalizing')
        if capture['state'] == 'stalled':
            return status('blocked', 'stalled')
        if capture['state'] == 'failed':
            return status('blocked', 'failed')
        if capture['state'] in {'stopping', 'completed'}:
            return status('blocked', 'stopping')
        audio, observed_at = capture.get('audio_seconds'), capture.get('last_audio_at')
        if (type(audio) not in (int, float) or not math.isfinite(audio) or audio < 0
                or type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0):
            return status('blocked', 'unknown')
        if capture['state'] in {'idle', 'starting'} or audio == 0:
            return result
        if (capture['state'] != 'recording' or type(observed_at) not in (int, float)
                or not math.isfinite(observed_at) or not 0 <= time.time() - observed_at <= 12):
            return status('blocked', 'unknown')
        boundary = math.floor(audio / interval) * interval
        covered = max(stage.get('through_seconds', 0), asr['through_seconds'])
        if boundary > covered + 1e-6:
            return status('busy', 'queued')
        result['remaining_seconds'] = max(0, boundary + interval - audio)
        return status('waiting', 'recording')

    def _asr_schedule_locked(self):
        """Report observed chunk progress, never an inference completion ETA."""
        interval = self.chunk_seconds
        result = {'state': 'idle', 'reason': 'no_source', 'clock': 'audio',
                  'interval_seconds': interval, 'wait_seconds': interval,
                  'remaining_seconds': None, 'due_at': None}

        def status(state, reason):
            result.update(state=state, reason=reason)
            return result

        if not self.state['session']:
            return result
        if self.result_dir is None:
            return status('blocked', 'saved_view')
        asr, capture = self.state['asr'], self.state['capture']
        if asr['state'] == 'running':
            return status('busy', 'request')
        if asr.get('failed_chunks') or asr.get('error') or asr['state'] == 'failed':
            return status('blocked', 'failed')
        if self.state.get('processing_stop_requested'):
            return status('blocked', 'stopped')
        if not self.audio_queue.empty():
            return status('busy', 'queued')
        if self.source_done.is_set() and asr['state'] == 'completed' and not self.inference_unconfirmed:
            return status('complete', 'no_pending')
        if self.closing or self.abort_processing.is_set() or self.inference_unconfirmed or asr['state'] == 'paused':
            return status('blocked', 'closed')
        if self.source_done.is_set():
            return status('idle', 'finalizing')
        if capture['state'] == 'stalled':
            return status('blocked', 'stalled')
        if capture['state'] == 'failed':
            return status('blocked', 'failed')
        if capture['state'] in {'stopping', 'completed'}:
            return status('blocked', 'stopping')
        audio, observed_at = capture.get('audio_seconds'), capture.get('last_audio_at')
        if not isinstance(audio, (int, float)) or not math.isfinite(audio) or audio < 0:
            return status('blocked', 'unknown')
        if capture['state'] in {'idle', 'starting'} or audio == 0:
            return result
        # A fresh HTTP response alone is not evidence that PCM is still arriving.
        # Normal capture marks a stall sooner; this also guards old observations
        # when its status callback cannot run. No wall clock is used as audio.
        if (capture['state'] != 'recording' or not isinstance(observed_at, (int, float))
                or not math.isfinite(observed_at) or not 0 <= time.time() - observed_at <= 12):
            return status('blocked', 'unknown')
        if not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
            return status('blocked', 'unknown')
        boundary = math.floor(audio / interval) * interval
        if boundary > asr['through_seconds'] + 1e-6:
            # A full saved chunk may still be in the capture callback dispatcher.
            return status('busy', 'queued')
        result['remaining_seconds'] = interval - (audio % interval)
        return status('waiting', 'recording')

    def _continuous_enabled(self):
        return bool(self.state.get('translation', {}).get('enabled'))

    def _translation_input_finished(self):
        # An empty queue can still have a chunk in the recognizer.
        return (self.source_done.is_set() and self.audio_queue.empty()
                and self.state['asr']['state'] != 'running')

    def _translation_readiness_locked(self, finished, *, refresh=False):
        """Cache pure boundary planning while append-only source evidence is unchanged."""
        from lecture_translation import plan_sentence_translation
        rows = self.state['lines']
        translation = self.state['translation']
        failures = self.state['asr'].get('failed_chunks', [])
        breaks = tuple((row['start_seconds'], row['end_seconds']) for row in failures)
        key = (id(rows), len(rows), rows[-1]['id'] if rows else None,
               len(translation['blocks']), self.state['asr']['through_seconds'], finished, breaks)
        if refresh or self._translation_boundary_cache is None or self._translation_boundary_cache[0] != key:
            covered = {identity for block in translation['blocks'] for identity in block['source_ids']}
            readiness = plan_sentence_translation(rows, covered,
                through_seconds=self.state['asr']['through_seconds'], end_of_input=finished,
                max_wait_seconds=TRANSLATION_MAX_WAIT_SECONDS,
                lookahead_seconds=TRANSLATION_LOOKAHEAD_SECONDS,
                source_breaks=[{'start_seconds': start, 'end_seconds': end} for start, end in breaks])
            self._translation_boundary_cache = (key, readiness)
        readiness = self._translation_boundary_cache[1]
        translation.update(ready_lines=len(readiness['ready_source_ids']),
                           waiting_lines=len(readiness['waiting_source_ids']))
        return readiness

    def _generation_due(self, kind, finished, newest, now, *, manual=None):
        """The scheduler and dashboard share this eligibility calculation."""
        if self.abort_processing.is_set() or self.closing:
            return False, None, 'stopped' if self.state.get('processing_stop_requested') else 'closed'
        if manual is None:
            manual = (self.translation_retry_event if kind == 'translation' else self.retry_event).is_set()
        recovery = self._recovery[kind]
        if manual:
            due = max(now, recovery['not_before'] or now)
            return True, due, 'retry' if due > now else 'manual'
        if recovery['paused'] or recovery['exhausted']:
            return False, None, 'manual_retry'
        if recovery['next'] is not None:
            return True, recovery['next'], 'retry'
        if kind == 'translation':
            wants = bool(self.state['translation']['pending_lines']) and not self.state['translation']['retry_required']
            if wants and not self._retry_jobs.get(kind):
                from lecture_translation import TranslationInputError
                try:
                    if self._translation_readiness_locked(finished)['plan'] is None:
                        return False, None, 'continuation'
                except TranslationInputError:
                    # Preparation records the error once. A status poll must not
                    # drop pending work or throw instead of exposing its status.
                    pass
        else:
            wants = bool(newest) and not self._analysis_manual_required and (
                newest != self._analysis_last_source_id or self._generation_offline[kind])
            # At startup, understanding support should follow the first
            # available translation. A boundary wait holds no cloud slot;
            # publication or a failed attempt releases this initial-only gate.
            translation = self.state['translation']
            translation_recovery = self._recovery['translation']
            if (wants and self._continuous_enabled() and translation['pending_lines']
                    and not math.isfinite(self._generation_last_started['analysis'])
                    and not translation['blocks']
                    and not translation['retry_required']
                    and not translation_recovery['error']
                    and not translation_recovery['paused'] and not translation_recovery['exhausted']):
                return False, None, 'initial_translation'
        if not wants:
            return False, None, 'no_pending'
        offline = self._generation_offline[kind]
        interval = OFFLINE_RECHECK_SECONDS if offline else (self.translation_interval if kind == 'translation' else self.analysis_interval)
        due = self._generation_last_started[kind] + interval
        if finished and not offline:
            due = now
        return True, max(now, due), 'offline' if offline else 'interval'

    def _schedule_locked(self, kind):
        now, wall = time.monotonic(), time.time()
        recovery = self._recovery[kind]
        remaining = max(0, recovery['next'] - now) if recovery['next'] is not None else None
        interval = self.translation_interval if kind == 'translation' else self.analysis_interval
        result = {'state': 'idle', 'reason': 'no_source', 'interval_seconds': interval,
                  'wait_seconds': interval, 'remaining_seconds': None, 'due_at': None,
                  'waiting_for': None,
                  'retry': {'attempts': recovery['attempts'], 'max_attempts': MAX_AUTO_RETRIES,
                            'next_at': wall + remaining if remaining is not None else None,
                            'remaining_seconds': remaining, 'delay_seconds': recovery['delay'],
                            'paused': recovery['paused'], 'exhausted': recovery['exhausted']},
                  'error': deepcopy(recovery['error'])}
        def status(state, reason):
            result.update(state=state, reason=reason)
            return result
        if not self.state['session']:
            return result
        if self.result_dir is None:
            return status('blocked', 'saved_view')
        enabled = self._continuous_enabled() if kind == 'translation' else self.state['analysis']['provider'] != 'off'
        if not enabled:
            return status('idle', 'disabled')
        if self.cloud_workers[kind] is not None:
            return status('busy', 'request')
        if kind == 'analysis' and self.state[kind]['state'] == 'running':
            return status('busy', 'request')
        if self.state.get('processing_stop_requested'):
            return status('blocked', 'stopped')
        if self.closing or self.abort_processing.is_set() or self.inference_unconfirmed:
            return status('blocked', 'closed')
        eligible = content_lines(self.state['lines'])
        newest = eligible[-1]['id'] if eligible else None
        finished = self._translation_input_finished()
        if self._continuous_enabled():
            wants, due, reason = self._generation_due(kind, finished, newest, now,
                                                      manual=recovery['manual_requested'])
        else:
            manual = recovery['manual_requested']
            wants = bool(newest) and (manual or newest != self._analysis_last_source_id)
            delay = OFFLINE_RECHECK_SECONDS if self._generation_offline[kind] else (120 if self.state[kind]['state'] == 'failed' else interval)
            due = now if manual or finished else max(now, self._generation_last_started[kind] + delay)
            reason = 'offline' if self._generation_offline[kind] else 'interval'
        if not wants:
            if self.state[kind]['state'] in {'failed', 'paused'} or recovery['paused'] or recovery['exhausted']:
                return status('blocked', 'manual_retry')
            if reason in {'continuation', 'initial_translation'}:
                return status('waiting', reason)
            return status('complete' if finished else 'idle', 'no_pending' if newest else 'no_source')
        delay = max(0, due - now)
        if delay > 0:
            result.update(remaining_seconds=delay, due_at=wall + delay,
                          wait_seconds=recovery['delay'] if reason == 'retry' else (OFFLINE_RECHECK_SECONDS if reason == 'offline' else interval))
            return status('waiting', reason)
        if not self.audio_queue.empty():
            return status('busy', 'asr')
        result.update(remaining_seconds=delay, due_at=wall + delay,
                      wait_seconds=recovery['delay'] if reason == 'retry' else (OFFLINE_RECHECK_SECONDS if reason == 'offline' else interval))
        return status('due', reason)

    def _refresh_translation_locked(self):
        legacy = legacy_source_policy(self.state)
        translation = self.state.setdefault('translation', deepcopy(blank_state()['translation']))
        covered = {identity for block in translation['blocks'] for identity in block['source_ids']}
        if legacy:
            translation.pop('source_policy_version', None)
            lines = self.state['lines']
            pending = [line for line in lines if str(line.get('text', '')).strip() and not line.get('uncertain')
                       and line.get('language') != 'ja' and line['id'] not in covered]
            translation.update(pending_lines=len(pending),
                excluded_uncertain_lines=sum(bool(line.get('uncertain')) for line in lines),
                native_lines=sum(bool(str(line.get('text', '')).strip()) and not line.get('uncertain')
                                 and line.get('language') == 'ja' for line in lines), worker_alive=False)
            return pending, covered
        lines = plan_source_policy(self.state['lines'])
        pending = [line for line in lines if not line['exclusion_reason']
                   and line.get('language') != 'ja' and line['id'] not in covered]
        translation.update(pending_lines=len(pending),
            source_policy_version=SOURCE_POLICY_VERSION, excluded_sources=exclusion_audit(lines),
            included_uncertain_lines=sum(line['uncertain'] and not line['exclusion_reason']
                                         and line.get('language') != 'ja' for line in lines),
            excluded_uncertain_lines=sum(line['uncertain'] and bool(line['exclusion_reason']) for line in lines),
            native_lines=sum(not line['exclusion_reason']
                             and line.get('language') == 'ja' for line in lines),
            worker_alive=self._cloud_alive('translation'))
        if not pending:
            translation.update(ready_lines=0, waiting_lines=0)
        if translation['enabled'] and pending and translation['state'] == 'completed':
            translation['state'] = 'waiting'
        translation['completion_confirmed'] = not translation['worker_alive'] and translation['state'] not in {'running', 'paused'}
        if translation['enabled'] and pending and self.result_dir is not None and not self.abort_processing.is_set():
            from lecture_translation import TranslationInputError
            try:
                self._translation_readiness_locked(self._translation_input_finished())
            except TranslationInputError:
                translation.update(ready_lines=None, waiting_lines=None)
        return pending, covered

    def persist(self, *, force=False):
        with self.lock:
            if not self.result_dir or (not force and time.monotonic() - self.last_persist < 1):
                return
            atomic_json(self.result_dir / 'state.json', self.snapshot())
            self.last_persist = time.monotonic()

    def _capture_status(self, status, session_id=None):
        with self.lock:
            if session_id and (not self.state['session'] or self.state['session']['id'] != session_id):
                return
            if self.source_done.is_set() and status.get('state') in ACTIVE:
                return
            status = dict(status)
            status['state'] = {'stopped': 'completed', 'error': 'failed'}.get(status.get('state'), status.get('state'))
            self.state['capture'].update(status)
            self._queue_provisional_locked()
        self.persist()

    def _chunk(self, chunk):
        with self.lock:
            if chunk['index'] in self.accepted_chunks:
                if self.accepted_chunks[chunk['index']] != chunk.get('sha256'):
                    raise ValueError('同じ分割番号に異なる音声が届きました。')
                return
            self.accepted_chunks[chunk['index']] = chunk.get('sha256')
            self.chunk_completed_at[chunk['index']] = chunk.get('completed_at')
            self.audio_queue.put(dict(chunk))

    def prepare_asr(self):
        """Asynchronous local-only preparation; never starts a source afterward."""
        with self.lock:
            if self.closing:
                raise RuntimeError('アプリは終了処理中です。')
            if self.state.get('session') and self.result_dir is None:
                raise ValueError('保存結果の閲覧中は認識器を起動しません。')
            if self.preparation_thread and self.preparation_thread.is_alive():
                return self.snapshot()
            if self.asr_preparation['state'] == 'ready':
                return self.snapshot()
            if self.state['capture']['state'] in ACTIVE or any(thread and thread.is_alive()
                    for thread in (self.worker, self.source_thread, *self.cloud_workers.values())):
                raise RuntimeError('録音または処理が進行中です。')
            self.preparation_cancel = threading.Event()
            event = self.preparation_cancel
            path = self.results_root / ('asr-preparation-' + uuid.uuid4().hex + '.json')
            self.asr_preparation = {'state': 'preparing', 'started_at': time.time(),
                'completed_at': None, 'duration_seconds': None, 'error': None,
                'stop_requested': False, 'preparation_path': str(path), 'language': self.default_language}
            def prepare():
                before = time.monotonic()
                try:
                    atomic_json(path, deepcopy(self.asr_preparation))
                    with processing_scope(event):
                        check_processing_allowed()
                        details = self._asr_preparer(self.default_language)
                        check_processing_allowed()
                    with self.lock:
                        if event.is_set():
                            raise ProcessingStopped('認識器の準備は停止されました。')
                        self.asr_preparation.update(state='ready', details=details)
                except (ProcessingStopped, InferenceCancelled):
                    with self.lock:
                        self.asr_preparation.update(state='paused', stop_requested=True)
                except Exception as exc:
                    with self.lock:
                        self.asr_preparation.update(state='failed', error=str(exc)[:1000])
                finally:
                    with self.lock:
                        if event.is_set() and self.asr_preparation['state'] != 'failed':
                            self.asr_preparation.update(state='paused', stop_requested=True)
                        self.asr_preparation.update(completed_at=time.time(), duration_seconds=time.monotonic() - before)
                        try:
                            atomic_json(path, deepcopy(self.asr_preparation))
                        except OSError as exc:
                            self.asr_preparation.update(state='failed', error='準備記録を保存できません: ' + str(exc)[:800])
            self.preparation_thread = threading.Thread(target=prepare, name='lecture-asr-prepare', daemon=False)
            self.preparation_thread.start()
        return self.snapshot()

    def start(self, config, *, replay=None, replay_seconds=None, pace=1):
        if not isinstance(config, dict):
            raise ValueError('開始設定が不正です。')
        if self.readiness and not replay:
            self.readiness.require_recording_ready()
        language, provider = config.get('language', 'auto'), config.get('provider', 'local')
        if language not in {'auto', 'en', 'ja'} or provider not in {'off', 'local', 'openai'}:
            raise ValueError('言語または解析方法が不正です。')
        if provider == 'openai' and not self.allow_cloud:
            raise ValueError('クラウド解析は未有効です。本文送信範囲と予算を確認してから設定してください。')
        if provider == 'openai' and self.cloud_scope is None:
            raise ValueError('クラウドへ送信できる音声の範囲が設定されていません。')
        device = config.get('device', 'default')
        if not isinstance(device, str) or not device or len(device) > 200 or '\n' in device:
            raise ValueError('マイク指定が不正です。')
        model = config.get('model') or (self.default_model if provider == 'openai' else 'qwen3:4b')
        if not isinstance(model, str) or len(model) > 100:
            raise ValueError('モデル指定が不正です。')
        with self.lock:
            if self.closing:
                raise RuntimeError('アプリは終了処理中です。')
            if self.inference_unconfirmed:
                raise RuntimeError('前回のローカル推論の終了が未確認です。状態を確認してから再起動してください。')
            if (self.asr_preparation['state'] != 'ready' or self.asr_preparation['stop_requested']
                    or (self.preparation_thread and self.preparation_thread.is_alive())):
                raise ValueError('音声認識の準備が完了してから開始してください。録音はまだ始まっていません。')
            if (self.state['capture']['state'] in ACTIVE or (self.worker and self.worker.is_alive())
                    or self._cloud_alive()
                    or (self.source_thread and self.source_thread.is_alive())
                    or (self.recorder and not all(self.recorder.snapshot().get(key, False)
                                                  for key in ('stop_confirmed', 'callbacks_confirmed')))):
                raise RuntimeError('録音または処理が進行中です。')
            session_id = 'Lecture-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
            session_dir, result_dir = self.data_root / session_id, self.results_root / session_id
            session_dir.mkdir(parents=True, exist_ok=False)
            result_dir.mkdir(parents=True, exist_ok=False)
            self.session_dir, self.result_dir = session_dir, result_dir
            self.source_done = threading.Event()
            self.stop_source = threading.Event()
            self.retry_event = threading.Event()
            self.translation_retry_event = threading.Event()
            self.audio_queue = queue.Queue()
            self.accepted_chunks = {}
            self.chunk_completed_at = {}
            self.abort_processing = threading.Event()
            self.worker_finishing = False
            self.state = blank_state()
            self._provisional_pending = None
            self._provisional_last_end = self._provisional_revision = 0
            self.state['provisional_asr'].update(enabled=bool(self.provisional_refresh_seconds),
                state='waiting' if self.provisional_refresh_seconds else 'idle',
                refresh_seconds=self.provisional_refresh_seconds, window_seconds=self.provisional_window_seconds)
            self.state['session'] = {'id': session_id, 'title': '講演中の理解サポート',
                                    'source_kind': 'replay' if replay else 'microphone',
                                    'started_at': time.time(), 'language': language,
                                    'data_path': str(self.session_dir), 'result_path': str(self.result_dir)}
            if replay:
                self.state['session']['replay_path'] = str(Path(replay).resolve())
            self.state['capture'].update(state='starting', device=device)
            self.state['asr']['state'] = 'waiting'
            self.state['analysis'].update(state='idle' if provider == 'off' else 'waiting',
                                          provider=provider, model=model)
            self.state['translation'].update(enabled=self.continuous_translation and provider == 'openai',
                state='waiting' if self.continuous_translation and provider == 'openai' else 'idle')
            self._generation_last_started = {'analysis': -math.inf, 'translation': -math.inf}
            self._analysis_last_source_id = None
            self._analysis_manual_required = False
            self._generation_offline = {'analysis': False, 'translation': False}
            self._recovery = {kind: recovery_state() for kind in ('analysis', 'translation')}
            self._retry_jobs = {}
            self._translation_boundary_cache = None
            self.state['message'] = '保存済み音声の逐次再生試験です。' if replay else 'マイクを起動しています。'
            self.recorder = None
            self.worker = self.source_thread = None
            self.cloud_workers = {'analysis': None, 'translation': None}
            self._cloud_outcomes = {'analysis': None, 'translation': None}
            try:
                save_runtime(self.result_dir, {'language': language, 'provider': provider, 'model': model,
                    'chunk_seconds': self.chunk_seconds, 'analysis_interval': self.analysis_interval,
                    'provisional_refresh_seconds': self.provisional_refresh_seconds,
                    'provisional_window_seconds': self.provisional_window_seconds,
                    'continuous_translation': self.continuous_translation,
                    'initial_translation_first': self._continuous_enabled(),
                    'parallel_cloud_stages': True,
                    'translation_interval': self.translation_interval,
                    'translation_source_policy_version': SOURCE_POLICY_VERSION,
                    'translation_max_wait_seconds': TRANSLATION_MAX_WAIT_SECONDS,
                    'translation_lookahead_seconds': TRANSLATION_LOOKAHEAD_SECONDS,
                    'replay': bool(replay), 'pace': pace if replay else None})
                self.cloud_baseline_keys = set()
                if provider == 'openai':
                    import event_insights_cloud as cloud
                    self.cloud_baseline_keys = set(cloud._load_ledger()['requests'])
                    atomic_json(self.result_dir / 'cloud-baseline.json',
                                {'request_keys_before': sorted(self.cloud_baseline_keys)})
                self._cost_report()
                atomic_json(self.result_dir / 'asr-preparation.json', deepcopy(self.asr_preparation))
                self.persist(force=True)
                self.worker = threading.Thread(target=self._process, name='lecture-processing', daemon=False)
                self.source_thread = threading.Thread(target=self._replay if replay else self._microphone,
                    args=(Path(replay), replay_seconds, pace) if replay else (), name='lecture-source', daemon=False)
                self.worker.start()
                self.source_thread.start()
            except Exception as exc:
                self.source_done.set()
                self.stop_source.set()
                self.abort_processing.set()
                self.state['capture'].update(state='failed', error='開始の準備に失敗しました。録音は開始していません。')
                self.state['asr'].update(state='failed', error=str(exc)[:1000])
                self.state['message'] = '保存先と開始設定を確認して、再度開始してください。'
                # Unstarted Thread objects cannot be joined. A worker which did
                # start sees abort_processing after this lock is released.
                if self.source_thread and self.source_thread.ident is None:
                    self.source_thread = None
                if self.worker and self.worker.ident is None:
                    self.worker = None
                try:
                    self.persist(force=True)
                except OSError:
                    pass
                raise
            return self.snapshot()

    def _microphone(self):
        error = None
        try:
            from lecture_capture import CaptureSession
            factory = self.capture_factory or CaptureSession
            session_id = self.state['session']['id']
            self.recorder = factory(self.session_dir / 'audio', device=self.state['capture']['device'],
                chunk_seconds=self.chunk_seconds, on_chunk=self._chunk,
                on_status=lambda value: self._capture_status(value, session_id))
            self.recorder.start()
            while not self.stop_source.wait(.2):
                status = self.recorder.snapshot()
                self._capture_status(status)
                if status.get('state') in {'stopped', 'error', 'completed', 'failed'}:
                    break
        except Exception as exc:
            error = str(exc)[:1000]
        finally:
            try:
                if self.recorder:
                    status = self.recorder.stop()
                    # The durable ledger is authoritative if a callback is late
                    # or failed. Index de-duplication permits safe reconciliation.
                    ledger = self.session_dir / 'audio/chunks.jsonl'
                    if status.get('stop_confirmed') and ledger.is_file():
                        for line in ledger.read_text().splitlines():
                            self._chunk(json.loads(line))
                    if not status.get('stop_confirmed'):
                        error = error or 'マイク録音の終了を確認できません。'
                    if status.get('callback_errors'):
                        error = error or '通知処理に失敗があり、保存台帳から回復しました。'
                    self._capture_status(status)
            except Exception as exc:
                error = error or str(exc)[:1000]
            finally:
                if error:
                    with self.lock:
                        self.state['capture'].update(state='failed', error=error)
                self.source_done.set()
                try:
                    self.persist(force=True)
                except OSError:
                    pass  # Capture's independently persisted raw/ledger survive.

    def _replay(self, source, duration, pace):
        from lecture_capture import PCMChunkWriter
        writer = None
        try:
            if pace not in {0, 1}:
                raise ValueError('再生速度は実時間(1)または試験用の無待機(0)です。')
            with wave.open(str(source)) as stream:
                if (stream.getnchannels(), stream.getframerate(), stream.getsampwidth()) != (1, 16000, 2):
                    raise ValueError('逐次再生は16kHz/PCM16/mono WAVが必要です。')
                length = stream.getnframes() / 16000
                limit = length if duration is None else min(length, duration)
                if not math.isfinite(limit) or limit <= 0:
                    raise ValueError('再生時間が不正です。')
                atomic_json(self.result_dir / 'replay-source.json', {'source': str(source.resolve()),
                    'seconds': limit, 'pace': pace, 'future_audio_supplied': False})
                writer = PCMChunkWriter(self.session_dir / 'audio', chunk_seconds=self.chunk_seconds,
                                        on_chunk=self._chunk)
                before, frames = time.monotonic(), 0
                total = round(limit * 16000)
                self._capture_status({'state': 'recording', 'error': None})
                while frames < total and not self.stop_source.is_set():
                    amount = min(8000, total - frames)
                    deadline = before + (frames + amount) / 16000 * pace
                    if self.stop_source.wait(max(0, deadline - time.monotonic())):
                        break
                    data = stream.readframes(amount)
                    if len(data) != amount * 2:
                        raise ValueError('再生元の音声が途中で切れています。')
                    writer.write(data)
                    frames += amount
                    self._capture_status({'state': 'recording', 'audio_seconds': frames / 16000,
                                          'last_audio_at': time.time()})
                writer.close()
                writer = None
                self._capture_status({'state': 'completed', 'audio_seconds': frames / 16000})
        except Exception as exc:
            self._capture_status({'state': 'failed', 'error': str(exc)[:1000]})
        finally:
            try:
                if writer:
                    writer.close()
            except Exception as exc:
                with self.lock:
                    self.state['capture'].update(state='failed', error=str(exc)[:1000])
            finally:
                self.source_done.set()
                self.persist(force=True)

    def stop(self):
        with self.lock:
            preparation_alive = bool(self.preparation_thread and self.preparation_thread.is_alive())
            # A stop displayed during preparation can arrive just after that
            # thread exits. Preserve it until source admission, too.
            prepared_without_session = (not self.state.get('session')
                and self.asr_preparation['state'] == 'ready'
                and self.asr_preparation.get('started_at') is not None)
            if preparation_alive or prepared_without_session:
                self.preparation_cancel.set()
                self.asr_preparation['stop_requested'] = True
                if self.asr_preparation['state'] == 'ready':
                    self.asr_preparation['state'] = 'paused'
                    atomic_json(self.asr_preparation['preparation_path'], deepcopy(self.asr_preparation))
            if self.result_dir is None:
                return self.snapshot()  # Saved-result views own no processing to stop.
            self.stop_source.set()
            self.abort_processing.set()
            self.retry_event.clear()
            self.translation_retry_event.clear()
            if self.state['session']:
                self.state['processing_stop_requested'] = True
                self.state['message'] = '新しい処理を停止しました。送信済みの処理の終了と音声の保存を確認しています。'
            for recovery in self._recovery.values():
                recovery.update(paused=True, next=None, manual_requested=False, manual_admitted=False)
            if self.state['capture']['state'] in ACTIVE:
                self.state['capture']['state'] = 'stopping'
        self.persist(force=True)
        return self.snapshot()

    def retry_analysis(self):
        with self.lock:
            if self.abort_processing.is_set() or self.state.get('processing_stop_requested'):
                raise RuntimeError('停止したセッションの追加処理はできません。新しく開始してください。')
            if self.closing or self.inference_unconfirmed:
                raise RuntimeError('終了処理中、またはローカル推論の終了が未確認です。')
            if self.worker_finishing and self.worker and self.worker.is_alive():
                raise RuntimeError('前回処理を保存中です。完了してから再試行してください。')
            if self.result_dir is None:
                raise ValueError('保存結果の閲覧中です。新しく録音を開始してください。')
            if not self.state['lines'] or self.state['analysis']['provider'] == 'off':
                raise ValueError('解析対象の原文がありません。')
            if self.state['analysis']['state'] == 'running' or self.cloud_workers['analysis'] is not None:
                raise RuntimeError('解析はすでに進行中です。')
            self._manual_recovery('analysis')
            self.retry_event.set()
            if not self.worker or not self.worker.is_alive():
                self.worker_finishing = False
                self.worker = threading.Thread(target=self._process, name='lecture-retry', daemon=False)
                self.worker.start()
        return self.snapshot()

    def retry_translation(self):
        with self.lock:
            if self.abort_processing.is_set() or self.state.get('processing_stop_requested'):
                raise RuntimeError('停止したセッションの追加処理はできません。新しく開始してください。')
            if self.closing or self.inference_unconfirmed:
                raise RuntimeError('終了処理中、または推論の終了が未確認です。')
            if self.worker_finishing and self.worker and self.worker.is_alive():
                raise RuntimeError('前回処理を保存中です。完了してから再試行してください。')
            if not self.result_dir or not self._continuous_enabled():
                raise ValueError('このセッションでは連続翻訳を実行できません。')
            pending, _ = self._refresh_translation_locked()
            if not pending:
                raise ValueError('未翻訳の対象原文がありません。')
            if self.cloud_workers['translation'] is not None:
                raise RuntimeError('翻訳はすでに進行中です。')
            self._manual_recovery('translation')
            self.translation_retry_event.set()
            if not self.worker or not self.worker.is_alive():
                self.worker_finishing = False
                self.worker = threading.Thread(target=self._process, name='lecture-translation-retry', daemon=False)
                self.worker.start()
        return self.snapshot()

    def _manual_recovery(self, kind):
        previous = self._recovery[kind]
        renewed = recovery_state()
        renewed.update(manual_requested=True, manual_admitted=True)
        now = time.monotonic()
        if previous['not_before'] is not None and previous['not_before'] > now:
            renewed.update(not_before=previous['not_before'], next=previous['not_before'],
                           delay=previous['not_before'] - now, error=previous['error'])
        self._recovery[kind] = renewed

    def pause_retries(self, kind):
        if kind not in {'analysis', 'translation'}:
            raise ValueError('再試行を保留する処理を指定してください。')
        with self.lock:
            if self.abort_processing.is_set():
                raise RuntimeError('停止したセッションの追加処理はできません。')
            if not self.result_dir or not self._continuous_enabled():
                raise ValueError('このセッションでは自動再試行を保留できません。')
            recovery = self._recovery[kind]
            if recovery['next'] is None and not (self.cloud_workers[kind] is not None and recovery['attempts']):
                raise ValueError('保留できる自動再試行がありません。')
            recovery.update(paused=True, next=None, manual_requested=False, manual_admitted=False)
            (self.translation_retry_event if kind == 'translation' else self.retry_event).clear()
            if kind == 'analysis':
                self._analysis_manual_required = True
            else:
                self.state['translation']['retry_required'] = True
            if not (self.cloud_workers[kind] is not None):
                self.state[kind]['state'] = 'failed'
            append_json(self.result_dir / 'generation-events.jsonl',
                        {'at': time.time(), 'stage': kind, 'event': 'retry_paused', 'attempts': recovery['attempts']})
        self.persist(force=True)
        return self.snapshot()

    def _prepare_analysis(self):
        with self.lock:
            if self.closing or self.abort_processing.is_set():
                return None
            previous = deepcopy(self.state['analysis'].get('result'))
            continuous = self._continuous_enabled()
            lines, translation_ids, selection = bounded_analysis_input(self.state['lines'], previous,
                include_block_translations=not continuous, include_line_translations=not continuous)
            if not lines:
                return None
            lines = deepcopy(lines)
            through = max(line['end_seconds'] for line in lines)
            provider, model = self.state['analysis']['provider'], self.state['analysis']['model']
            self.state['analysis'].update(state='running', error=None)
            return {'lines': lines, 'previous': previous, 'translation_ids': list(translation_ids),
                    'selection': deepcopy(selection), 'through': through, 'provider': provider, 'model': model,
                    'session': deepcopy(self.state['session']), 'result_dir': self.result_dir,
                    'stop_event': self.abort_processing}

    def _begin_retry_attempt(self, kind):
        from event_insights_cloud import CloudError
        with self.lock:
            check_processing_allowed()
            recovery = self._recovery[kind]
            if (recovery['paused'] or recovery['attempts'] >= MAX_AUTO_RETRIES
                    or recovery['started'] is None
                    or time.monotonic() >= recovery['started'] + AUTO_RETRY_WINDOW_SECONDS):
                recovery.update(next=None, exhausted=not recovery['paused'])
                raise CloudError('自動再試行の上限に達したか、再試行が保留されました。')
            recovery['attempts'] += 1

    def _analyze(self, *, manual_retry=False, prepared=None, automatic_retry=False):
        job = prepared if prepared is not None else self._prepare_analysis()
        if job is None:
            return
        with processing_scope(job.get('stop_event', self.abort_processing)):
            check_processing_allowed()
            return self._analyze_job(job, manual_retry=manual_retry, automatic_retry=automatic_retry)

    def _analyze_job(self, job, *, manual_retry=False, automatic_retry=False):
        from lecture_analysis import analyze_snapshot
        lines, previous = job['lines'], job['previous']
        translation_ids, selection, through = job['translation_ids'], job['selection'], job['through']
        provider, model, result_dir = job['provider'], job['model'], job['result_dir']
        self.persist(force=True)
        before = time.monotonic()
        generate = self.analyzer_override or analyze_snapshot
        check_processing_allowed()
        if provider == 'openai':
            if self.readiness:
                health = self.readiness.snapshot()
                check_processing_allowed()
                network = next((row for row in health['checks'] if row['id'] == 'network'), None)
                if network and network.get('reachable') is not True:
                    raise LectureOfflineError('インターネット接続待ちです。録音・原文保存は続け、新着原文で自動的に再確認します。')
            check_processing_allowed()
            if automatic_retry:
                self._begin_retry_attempt('analysis')
            check_processing_allowed()
            receipt = self.cloud_scope.reserve(job['session'], model, through)
            with self.lock:
                append_json(result_dir / 'cloud-scope.jsonl', receipt)
        # Manual or policy-admitted retry retains prior charges and rechecks the
        # same text scope and shared budget before another cloud attempt.
        retry_options = {'retry_failed': True} if provider == 'openai' and manual_retry else {}
        check_processing_allowed()
        result = generate(lines, previous, provider=provider, model=model, through_seconds=through,
                          translation_ids=translation_ids, timeout=60 if provider == 'openai' else 120,
                          out_dir=result_dir / 'analyses', include_block_translations=selection['include_block_translations'],
                          block_translation_breaks=selection.get('block_translation_breaks'), **retry_options)
        result['selection'] = selection
        try:
            self._publish_analysis(result, lines, through, selection, before,
                                   session_id=job['session']['id'], result_dir=result_dir)
        except Exception as exc:
            # Generation already returned. Disk/full or serialization errors
            # must not be mistaken for an Ollama request still running.
            exc.local_inference_finished = True
            raise

    def _publish_analysis(self, result, lines, through, selection, before, *, session_id=None, result_dir=None):
        with self.lock:
            if session_id is not None and (not self.state['session'] or self.state['session']['id'] != session_id
                                           or self.result_dir != result_dir):
                return  # A stale completion can never modify a successor session.
            # History is durable before the result is published.
            append_json(self.result_dir / 'analysis-history.jsonl', result)
            by_id = {line['id']: line for line in self.state['lines']}
            for item in result.get('translations', []):
                if item['source_id'] in by_id:
                    by_id[item['source_id']]['translation_ja'] = item['text']
            self.state['analysis'].update(state='completed', result=result,
                through_seconds=through, generated_at=time.time(), error=None)
            historical = deepcopy({key: value for key, value in result.items()
                                   if key != 'translations'})
            self.state.setdefault('analysis_history', []).append(historical)
            self.state['analysis_history'] = self.state['analysis_history'][-60:]
            newest_chunk = max((int(line['id'][1:7]) for line in lines), default=-1)
            ready = self.chunk_completed_at.get(newest_chunk)
            append_json(self.result_dir / 'measurements.jsonl', {'stage': 'analysis',
                'through_seconds': through, 'processing_seconds': time.monotonic() - before,
                'published_at': time.time(), 'capture_seconds': self.state['capture']['audio_seconds'],
                'source_chunk_completed_at': ready,
                'chunk_ready_to_publication_seconds': time.time() - ready if ready else None,
                'browser_render_measured': False})
        self._cost_report(session_id=session_id, result_dir=result_dir)
        self.persist(force=True)

    def _analysis_failed(self, exc, *, session_id=None):
        with self.lock:
            if session_id is not None and (not self.state['session'] or self.state['session']['id'] != session_id):
                return
            if isinstance(exc, (ProcessingStopped, InferenceCancelled)):
                self.state['analysis'].update(state='paused', error=None)
            else:
                self.state['analysis'].update(state='failed', error=str(exc)[:1000])
                from lecture_analysis import SnapshotInputError, SnapshotResponseError
                from event_insights import InputTooLargeError
                if (self.state['analysis']['provider'] == 'local'
                        and not getattr(exc, 'local_inference_finished', False)
                        and not isinstance(exc, (SnapshotInputError, SnapshotResponseError, InputTooLargeError))):
                    self.inference_unconfirmed = True
                    self.state['asr'].update(state='paused',
                        error='ローカル解析の終了を確認できないため、追加推論を保留しました。録音は継続しています。')
        self.persist(force=True)
        try:
            self._cost_report()
        except Exception:
            pass

    def _start_cloud_analysis(self, *, manual_retry):
        with self.lock:
            if self.cloud_workers['analysis'] is not None:
                raise RuntimeError('クラウド解析はすでに進行中です。')
            job = self._prepare_analysis()
            if job is None:
                return
            self._start_cloud_job('analysis', job, manual_retry)

    def _prepare_translation(self):
        with self.lock:
            if self.closing or self.abort_processing.is_set() or not self._continuous_enabled():
                return None
            self._refresh_translation_locked()
            plan = self._translation_readiness_locked(self._translation_input_finished(), refresh=True)['plan']
            if plan is None:
                return None
            self.state['translation'].update(state='running', error=None, retry_required=False)
            return {'plan': deepcopy(plan), 'session': deepcopy(self.state['session']),
                    'result_dir': self.result_dir, 'model': self.state['analysis']['model'],
                    'started_at': time.time(), 'stop_event': self.abort_processing}

    def _translate(self, job, *, manual_retry=False, automatic_retry=False):
        with processing_scope(job.get('stop_event', self.abort_processing)):
            check_processing_allowed()
            return self._translate_job(job, manual_retry=manual_retry, automatic_retry=automatic_retry)

    def _translate_job(self, job, *, manual_retry=False, automatic_retry=False):
        from lecture_translation import translate_batch, TranslationResponseError
        plan = job['plan']
        self.persist(force=True)
        check_processing_allowed()
        before = time.monotonic()
        if self.readiness:
            network = next((row for row in self.readiness.snapshot()['checks'] if row['id'] == 'network'), None)
            check_processing_allowed()
            if network and network.get('reachable') is not True:
                raise LectureOfflineError('通信の復帰を待っています。未翻訳の原文は保存されています。')
        check_processing_allowed()
        if automatic_retry:
            self._begin_retry_attempt('translation')
        check_processing_allowed()
        receipt = self.cloud_scope.reserve(job['session'], job['model'], plan['through_seconds'])
        with self.lock:
            append_json(job['result_dir'] / 'cloud-scope.jsonl', {**receipt, 'stage': 'translation'})
        generate = self.translator_override or translate_batch
        check_processing_allowed()
        result = generate(plan, provider='openai', model=job['model'], out_dir=job['result_dir'] / 'translations',
                          timeout=60, retry_failed=manual_retry)
        blocks = result.get('blocks')
        if (not isinstance(blocks, list) or len(blocks) != len(plan['groups'])
                or [block.get('source_ids') for block in blocks if isinstance(block, dict)] != plan['groups']
                or any(not isinstance(block.get('text'), str) or not block['text'].strip() for block in blocks)):
            raise TranslationResponseError('翻訳結果の根拠が要求した原文と一致しません。')
        if result.get('source_hashes') is not None and result['source_hashes'] != plan.get('source_hashes'):
            raise TranslationResponseError('翻訳結果の原文hashが要求と一致しません。')
        published = time.time()
        with self.lock:
            if (not self.state['session'] or self.state['session']['id'] != job['session']['id']
                    or self.result_dir != job['result_dir']):
                return
            by_id = {line['id']: line for line in self.state['lines']}
            source = {line['id']: line for line in plan['source_lines']}
            pending, covered = self._refresh_translation_locked()
            if any(identity in covered for identity in plan['target_source_ids']):
                raise TranslationResponseError('既に保存した訳を上書きできません。')
            for identity in plan['target_source_ids']:
                if identity not in by_id or identity not in source or any(
                        by_id[identity].get(key, False if key == 'uncertain' else None)
                        != source[identity].get(key, False if key == 'uncertain' else None)
                        for key in ('text', 'start_seconds', 'end_seconds', 'language', 'uncertain')):
                    raise TranslationResponseError('翻訳中に対象原文が変化しました。')
                if source_metadata(by_id[identity]) != source_metadata(source[identity]):
                    raise TranslationResponseError('翻訳中に対象原文の不確実性が変化しました。')
            additions = []
            boundaries = plan['selection'].get('group_boundaries', [])
            for index, block in enumerate(blocks):
                rows = [source[identity] for identity in block['source_ids']]
                stable = json.dumps([job['session']['id'], block['source_ids']], ensure_ascii=False).encode()
                additions.append({'id': 'tr-' + hashlib.sha256(stable).hexdigest()[:24],
                    'text': block['text'], 'source_ids': list(block['source_ids']),
                    'uncertain_source_ids': [row['id'] for row in rows if row['uncertain']],
                    'uncertainty_reasons': {row['id']: row['doubt_reasons'] for row in rows if row['uncertain']},
                    'start_seconds': min(row['start_seconds'] for row in rows),
                    'end_seconds': max(row['end_seconds'] for row in rows),
                    'generated_at': result.get('generated_at', published), 'published_at': published})
                if index < len(boundaries):
                    additions[-1]['boundary_reason'] = boundaries[index]['reason']
            # The history is durable before coverage advances. A write failure
            # retains every pending source ID and never silently skips a batch.
            append_json(job['result_dir'] / 'translation-history.jsonl',
                        {**result, 'selection': deepcopy(plan['selection']), 'blocks': additions,
                         'started_at': job['started_at'], 'published_at': published})
            self.state['translation']['blocks'].extend(additions)
            # Durable history and coverage have committed this request. A later
            # bookkeeping failure must not resend its already saved targets.
            self._retry_jobs.pop('translation', None)
            self.state['translation'].update(state='completed', error=None, retry_required=False,
                generated_at=published, through_seconds=max(self.state['translation']['through_seconds'],
                    max(block['end_seconds'] for block in additions)))
            self._refresh_translation_locked()
            append_json(job['result_dir'] / 'measurements.jsonl', {'stage': 'translation',
                'started_at': job['started_at'], 'published_at': published,
                'processing_seconds': time.monotonic() - before, 'through_seconds': plan['through_seconds'],
                'target_through_seconds': plan['target_through_seconds'],
                'target_source_ids': list(plan['target_source_ids']),
                'source_line_ids': [line['id'] for line in plan['source_lines']], 'browser_render_measured': False})
        self._cost_report(session_id=job['session']['id'], result_dir=job['result_dir'])
        self.persist(force=True)

    def _translation_failed(self, exc, *, session_id=None):
        with self.lock:
            if session_id is not None and (not self.state['session'] or self.state['session']['id'] != session_id):
                return
            pending, _ = self._refresh_translation_locked()
            if isinstance(exc, (ProcessingStopped, InferenceCancelled)):
                self.state['translation'].update(state='paused', error=None, retry_required=False)
            else:
                offline = isinstance(exc, LectureOfflineError)
                self.state['translation'].update(state='waiting' if offline else 'failed', error=str(exc)[:1000],
                    retry_required=bool(pending) and not offline)
        self.persist(force=True)
        try:
            self._cost_report()
        except Exception:
            pass

    def _start_cloud_job(self, kind, job, manual_retry, automatic_retry=False):
        with self.lock:
            if self.closing or job.get('stop_event', self.abort_processing).is_set():
                raise ProcessingStopped('新しい処理は停止されています。')
            if self.cloud_workers[kind] is not None:
                raise RuntimeError('この処理はすでに進行中です。')
            self._cloud_outcomes[kind] = None
            self._retry_jobs[kind] = job
            self.state[kind].update(state='running', error=None)
            if kind == 'translation':
                self.state[kind]['retry_required'] = False
            def generate():
                error = None
                try:
                    if kind == 'translation':
                        self._translate(job, manual_retry=manual_retry, automatic_retry=automatic_retry)
                    else:
                        self._analyze(manual_retry=manual_retry, prepared=job, automatic_retry=automatic_retry)
                except Exception as exc:
                    error = exc
                    try:
                        failure = self._translation_failed if kind == 'translation' else self._analysis_failed
                        failure(exc, session_id=job['session']['id'])
                    except Exception:
                        pass  # Preserve the original failure if status storage fails.
                finally:
                    with self.lock:
                        self._cloud_outcomes[kind] = {'error': error, 'kind': kind, 'session_id': job['session']['id']}
            self.cloud_workers[kind] = threading.Thread(target=generate, name='lecture-cloud-' + kind, daemon=False)
            try:
                self.cloud_workers[kind].start()
            except Exception:
                self.cloud_workers[kind] = None
                raise

    def _take_cloud_outcomes(self):
        outcomes = []
        with self.lock:
            for kind, worker in self.cloud_workers.items():
                if worker is None or worker.is_alive():
                    continue
                worker.join()
                outcomes.append(self._cloud_outcomes[kind] or {
                    'kind': kind, 'error': RuntimeError('クラウド処理の完了結果がありません。')})
                self.cloud_workers[kind] = None
                self._cloud_outcomes[kind] = None
        return outcomes

    def _cost_report(self, *, session_id=None, result_dir=None):
        # Both stages can finish together. Serialize the complete read/compute/
        # replace with publication so an older report cannot overwrite a newer one.
        with self.lock:
            return self._cost_report_locked(session_id=session_id, result_dir=result_dir)

    def _cost_report_locked(self, *, session_id=None, result_dir=None):
        with self.lock:
            if session_id is not None and (not self.state['session'] or self.state['session']['id'] != session_id
                                           or self.result_dir != result_dir):
                return
            result_dir = self.result_dir
            provider = self.state['analysis']['provider']
            baseline_keys = set(self.cloud_baseline_keys)
        if not result_dir:
            return
        successful = []
        for name in ('analysis-history.jsonl', 'translation-history.jsonl'):
            history = result_dir / name
            if history.exists():
                successful.extend(json.loads(row) for row in history.read_text().splitlines())
        cloud = provider == 'openai'
        cost = {
            'updated_at': time.time(), 'provider': provider,
            'additional_api_usd': None if cloud else 0,
            'successful_generation_api_usd': sum(row.get('cost_usd') or 0 for row in successful),
            'note': ('Cloud request ledger is authoritative, including failed or unknown requests.' if cloud
                     else 'No cloud generation was requested by this session.'),
            'codex_usage': 'not measured', 'electricity': 'not measured'}
        if cloud:
            import event_insights_cloud as adapter
            fingerprints = set()
            for path in [*(result_dir / 'analyses').glob('*/payload.json'),
                         *(result_dir / 'translations').glob('*/payload.json')]:
                encoded = json.dumps(json.loads(path.read_text()), ensure_ascii=False, sort_keys=True).encode()
                fingerprints.add(hashlib.sha256(encoded).hexdigest())
            entries = {key: row for key, row in adapter._load_ledger()['requests'].items()
                       if key not in baseline_keys and row.get('fingerprint', key) in fingerprints}
            confirmed = sum(adapter.confirmed_nanodollars(row) for row in entries.values()) / 1e9
            held = sum(row['charged_nanodollars'] - adapter.confirmed_nanodollars(row) for row in entries.values()) / 1e9
            cost.update(confirmed_api_usd=confirmed, retained_reservation_usd=held,
                        additional_api_usd=confirmed if not held else None,
                        matching_request_count=len(entries), daily_budget=adapter.budget_status())
        atomic_json(result_dir / 'cost-report.json', cost)

    def _generation_completed(self, outcome):
        from event_insights_cloud import CloudError
        kind, exc = outcome.get('kind', 'analysis'), outcome['error']
        with self.lock:
            if outcome.get('session_id') is not None and (not self.state['session']
                    or outcome['session_id'] != self.state['session']['id']):
                return
            recovery = self._recovery[kind]
            if isinstance(exc, (ProcessingStopped, InferenceCancelled)):
                recovery.update(next=None, paused=True, manual_requested=False, manual_admitted=False)
                self.state[kind]['state'] = 'paused'
                return
            offline = isinstance(exc, LectureOfflineError)
            self._generation_offline[kind] = offline
            if not offline:
                recovery['manual_admitted'] = False
            if kind == 'analysis':
                self._analysis_manual_required = bool(exc) and not offline
            if exc is None:
                recovery.update(next=None, started=None, exhausted=False, paused=self.abort_processing.is_set(), error=None)
                self._retry_jobs.pop(kind, None)
                return
            details = (exc.diagnostics() if isinstance(exc, CloudError) else
                       {'category': 'offline' if offline else 'local_or_validation', 'retryable': False,
                        'http_status': None, 'provider_code': None, 'retry_after_seconds': None})
            recovery['error'] = details
            recovery['next'] = None
            now = time.monotonic()
            if not offline:
                recovery['not_before'] = now + details['retry_after_seconds'] if details.get('retry_after_seconds') is not None else None
            if recovery['started'] is None and not offline:
                recovery['started'] = now
            if (not offline and details['retryable'] and not recovery['paused']
                    and not self.closing and not self.abort_processing.is_set()):
                delay = min(AUTO_RETRY_MAX_DELAY_SECONDS, AUTO_RETRY_BASE_SECONDS * 2 ** recovery['attempts'])
                delay *= random.uniform(1.0, 1.25)
                delay = max(delay, details.get('retry_after_seconds') or 0)
                allowed = (recovery['attempts'] < MAX_AUTO_RETRIES
                           and now + delay < recovery['started'] + AUTO_RETRY_WINDOW_SECONDS)
                recovery.update(delay=delay, next=now + delay if allowed else None, exhausted=not allowed)
                if allowed:
                    self.state[kind]['state'] = 'waiting'
                    if kind == 'translation':
                        self.state[kind]['retry_required'] = False
                    else:
                        self._analysis_manual_required = False
            if recovery['paused'] and not self.abort_processing.is_set():
                if kind == 'translation':
                    self.state[kind].update(state='failed', retry_required=True)
                else:
                    self._analysis_manual_required = True
                    self.state[kind]['state'] = 'failed'
            append_json(self.result_dir / 'generation-events.jsonl', {
                'at': time.time(), 'stage': kind, 'event': 'failed', 'error': details,
                'auto_attempts': recovery['attempts'], 'retry_delay_seconds': recovery['delay'] if recovery['next'] is not None else None,
                'exhausted': recovery['exhausted']})
        self.persist(force=True)

    def _continuous_step(self, finished, newest):
        """Admit each cloud stage independently, with at most one job per stage."""
        with self.lock:
            if self.closing or self.abort_processing.is_set():
                return False
            pending, _ = self._refresh_translation_locked()
            now = time.monotonic()
            waiting = any(self.cloud_workers.values())
            for kind in ('translation', 'analysis'):
                if self.cloud_workers[kind] is not None:
                    continue
                recovery = self._recovery[kind]
                if (recovery['started'] is not None and (recovery['next'] is not None or self._generation_offline[kind])
                        and now >= recovery['started'] + AUTO_RETRY_WINDOW_SECONDS):
                    recovery.update(next=None, exhausted=True)
                    self.state[kind]['state'] = 'failed'
                    if kind == 'analysis':
                        self._analysis_manual_required = True
                    else:
                        self.state[kind]['retry_required'] = True
                wants, due, reason = self._generation_due(kind, finished, newest, now)
                waiting = waiting or wants
                if not wants or due > now:
                    continue
                event = self.translation_retry_event if kind == 'translation' else self.retry_event
                requested = event.is_set()
                event.clear()
                recovery['manual_requested'] = False
                admitted = recovery['manual_admitted']
                automatic = not (requested or admitted) and (reason == 'retry' or recovery['started'] is not None)
                retry_job = self._retry_jobs.get(kind) if automatic or requested or admitted else None
                if automatic or requested or admitted:
                    recovery['next'] = None
                else:
                    self._recovery[kind] = recovery_state()
                try:
                    job = retry_job or (self._prepare_translation() if kind == 'translation' else self._prepare_analysis())
                    if job is not None:
                        self._generation_last_started[kind] = now
                        if kind == 'analysis' and retry_job is None:
                            self._analysis_last_source_id = newest
                        self._start_cloud_job(kind, job, requested or admitted or automatic, automatic_retry=automatic)
                except Exception as exc:
                    failure = self._translation_failed if kind == 'translation' else self._analysis_failed
                    failure(exc)
                    self._generation_completed({'kind': kind, 'error': exc})
            if not pending and self.state['translation']['state'] not in ('failed', 'running', 'paused'):
                self.state['translation']['state'] = 'completed' if finished else 'waiting'
            return bool(waiting or any(self.cloud_workers.values()))

    def _queue_provisional_locked(self):
        if (not self.provisional_refresh_seconds or self.result_dir is None
                or self.abort_processing.is_set() or self.closing or self.source_done.is_set()):
            return
        from lecture_provisional import pending_window
        window = pending_window(self.state['capture']['audio_seconds'], self._provisional_last_end,
            self.provisional_refresh_seconds, self.provisional_window_seconds,
            final=self.state['capture']['state'] in {'completed', 'failed'})
        if window is None:
            return
        self._provisional_revision += 1
        self._provisional_last_end = window['end_frame']
        # Replacing this descriptor coalesces stale previews; canonical chunks
        # remain in their own FIFO and are never removed by preview scheduling.
        self._provisional_pending = {**window, 'revision': self._provisional_revision,
            'ready_at': time.time(), 'stop_event': self.abort_processing}
        stage = self.state['provisional_asr']
        if stage['state'] in {'idle', 'completed'}:
            stage['state'] = 'waiting'

    def _refresh_provisional_locked(self):
        stage = self.state.get('provisional_asr')
        if not stage or not stage['enabled'] or self.result_dir is None:
            return
        if stage['through_seconds'] <= self.state['asr']['through_seconds']:
            stage['lines'] = []
        if stage['state'] == 'running':
            return  # An admitted inference is still owned by the ASR worker.
        if (self._provisional_pending is not None
                and self._provisional_pending['through_seconds'] <= self.state['asr']['through_seconds']):
            self._provisional_pending = None
        drained = (self.source_done.is_set() and self.audio_queue.empty()
                   and self._provisional_pending is None
                   and self.state['asr']['through_seconds'] >= self.state['capture']['audio_seconds'])
        if self.abort_processing.is_set():
            if stage['state'] != 'failed':
                stage['state'] = 'completed' if drained else 'paused'
        elif self.source_done.is_set() and self.audio_queue.empty() and self._provisional_pending is None:
            if stage['state'] != 'failed':
                stage['state'] = 'completed'

    def _process_provisional(self):
        from catchup_page import build_lines
        from lecture_provisional import write_window
        with self.lock:
            if (self.abort_processing.is_set() or self.closing or self.inference_unconfirmed
                    or not self.audio_queue.empty() or self._provisional_pending is None):
                return False
            job, self._provisional_pending = self._provisional_pending, None
            if job['through_seconds'] <= self.state['asr']['through_seconds']:
                self._refresh_provisional_locked()
                return False
            session_id = self.state['session']['id']
            session_dir, result_dir = self.session_dir, self.result_dir
            language = self.state['session']['language']
            self.state['provisional_asr']['state'] = 'running'
        started_at, before = time.time(), time.monotonic()
        preview_path = session_dir / 'provisional' / f"preview-{job['revision']:06d}.wav"
        try:
            with processing_scope(job['stop_event']):
                check_processing_allowed()
                self.persist(force=True)
                check_processing_allowed()
                chunk = write_window(session_dir / 'audio/raw.pcm', preview_path, job)
                check_processing_allowed()
                report = self.transcriber(chunk, result_dir / 'provisional-asr' / f"{job['revision']:06d}.json", language)
            lines = build_lines({'segments': [{'index': chunk['index'], 'start_seconds': chunk['start_seconds']}]},
                                {chunk['index']: report})
            for index, line in enumerate(lines):
                line['id'] = f"p{job['revision']:06d}-l{index:04d}"
                line['boundary_context'] = 'revisable rolling recognition; not translation source evidence'
            event = {key: job[key] for key in ('revision', 'window_start_seconds', 'through_seconds', 'ready_at')}
            # The preview WAV is a copy of saved raw PCM and is removed below;
            # the frame range and hash let it be re-derived from raw.pcm.
            event.update(started_at=started_at, lines=lines,
                         audio={'start_frame': chunk['start_frame'], 'end_frame': chunk['end_frame'],
                                'pcm_sha256': chunk['pcm_sha256'], 'wav_retained': False})
            with self.lock:
                if not self.state['session'] or self.state['session']['id'] != session_id:
                    return False
                event.update(published_at=time.time(), processing_seconds=time.monotonic() - before)
                append_json(result_dir / 'provisional-history.jsonl', event)
                append_json(result_dir / 'measurements.jsonl', {key: value for key, value in
                    {**event, 'stage': 'provisional_asr', 'capture_seconds': self.state['capture']['audio_seconds'],
                     'browser_render_measured': False}.items() if key != 'lines'})
                self.state['provisional_asr'].update({key: value for key, value in event.items()
                                                     if key not in {'processing_seconds', 'audio'}},
                                                    state='completed', error=None)
        except (ProcessingStopped, InferenceCancelled):
            with self.lock:
                self.state['provisional_asr']['state'] = 'paused'
                append_json(result_dir / 'provisional-events.jsonl', {'event': 'cancelled_before_dispatch',
                    'revision': job['revision'], 'window_start_seconds': job['window_start_seconds'],
                    'through_seconds': job['through_seconds'], 'at': time.time()})
        except Exception as exc:
            with self.lock:
                self.state['provisional_asr'].update(state='failed', error=str(exc)[:1000])
                append_json(result_dir / 'provisional-events.jsonl', {'event': 'failed',
                    'revision': job['revision'], 'window_start_seconds': job['window_start_seconds'],
                    'through_seconds': job['through_seconds'], 'at': time.time(), 'error': str(exc)[:1000]})
        finally:
            try:
                # Previews accumulate at several times the raw audio rate.
                preview_path.unlink(missing_ok=True)
            except OSError as exc:
                with self.lock:
                    append_json(result_dir / 'provisional-events.jsonl', {'event': 'preview_audio_not_removed',
                        'revision': job['revision'], 'at': time.time(), 'error': str(exc)[:1000]})
            self.persist(force=True)
        return True

    def _process(self):
        last_analysis = -math.inf
        last_analyzed_line = None
        failure_count = 0
        offline_failure = False
        try:
            from catchup_page import build_lines
            while True:
                for outcome in self._take_cloud_outcomes():
                    if self._continuous_enabled():
                        self._generation_completed(outcome)
                    else:
                        failure_count = failure_count + 1 if outcome['error'] else 0
                        offline_failure = isinstance(outcome['error'], LectureOfflineError)
                        self._generation_offline['analysis'] = offline_failure
                if self.abort_processing.is_set():
                    with self.lock:
                        if self.state['asr']['state'] != 'failed':
                            self.state['asr'].update(state='paused', error=None)
                        pending = [worker for worker in self.cloud_workers.values() if worker is not None]
                    if pending:
                        for worker in pending:
                            worker.join(.1)
                        continue
                    with self.lock:
                        for kind in ('analysis', 'translation'):
                            if self.state[kind]['state'] in {'running', 'waiting'}:
                                self.state[kind]['state'] = 'paused'
                        self._refresh_stopped_locked()
                    break
                chunk = None
                try:
                    chunk = self.audio_queue.get(timeout=.2)
                except queue.Empty:
                    pass
                if chunk is not None:
                    try:
                        with self.lock:
                            self.state['asr']['state'] = 'running'
                        began = time.monotonic()
                        with processing_scope(self.abort_processing):
                            check_processing_allowed()
                            report = self.transcriber(chunk, self.result_dir / 'asr' / f"{chunk['index']:06d}.json",
                                                      self.state['session']['language'])
                        lines = build_lines({'segments': [{'index': chunk['index'],
                                                           'start_seconds': chunk['start_seconds']}]},
                                            {chunk['index']: report})
                        for index, line in enumerate(lines):
                            line['id'] = f"c{chunk['index']:06d}-l{index:04d}"
                            line['boundary_context'] = 'non-overlapping audio; incomplete sentences may need following speech'
                        with self.lock:
                            # The transcript is durable before lines are published. A write
                            # failure leaves this chunk failed and absent from state.
                            append_json(self.result_dir / 'transcript.jsonl', {'chunk': chunk, 'lines': lines})
                            self.state['lines'].extend(lines)
                            self.state['asr'].update(state='waiting', through_seconds=chunk['end_seconds'])
                            try:
                                append_json(self.result_dir / 'measurements.jsonl', {'stage': 'asr',
                                    'through_seconds': chunk['end_seconds'], 'processing_seconds': time.monotonic() - began,
                                    'published_at': time.time(), 'capture_seconds': self.state['capture']['audio_seconds'],
                                    'source_chunk_completed_at': chunk.get('completed_at'),
                                    'chunk_ready_to_publication_seconds': (time.time() - chunk['completed_at']
                                        if chunk.get('completed_at') else None), 'browser_render_measured': False})
                            except OSError as exc:
                                # The published chunk stays recognized; only its timing is missing.
                                self.state['asr']['measurement_error'] = str(exc)[:1000]
                    except (ProcessingStopped, InferenceCancelled):
                        # It never entered inference; keep the exact audio chunk pending.
                        self.audio_queue.put(chunk)
                        with self.lock:
                            self.state['asr'].update(state='paused', error=None)
                    except Exception as exc:
                        with self.lock:
                            self.state['asr'].update(state='failed', error=str(exc)[:1000])
                            self.state['asr']['failed_chunks'].append({'index': chunk['index'],
                                'start_seconds': chunk['start_seconds'], 'end_seconds': chunk['end_seconds']})
                        append_json(self.result_dir / 'failed-chunks.jsonl', {'chunk': chunk, 'error': str(exc)[:1000]})
                    finally:
                        self.audio_queue.task_done()
                    self.persist(force=True)
                else:
                    self._process_provisional()
                finished = self.source_done.is_set() and self.audio_queue.empty()
                with self.lock:
                    if finished and self._provisional_pending is not None:
                        if self._provisional_pending['through_seconds'] <= self.state['asr']['through_seconds']:
                            self._provisional_pending = None
                        elif not self.abort_processing.is_set():
                            continue
                    self._refresh_provisional_locked()
                    eligible = content_lines(self.state['lines'])
                    newest = eligible[-1]['id'] if eligible else None
                    provider = self.state['analysis']['provider']
                    enabled = provider != 'off'
                    analysis_available = self.cloud_workers['analysis'] is None
                if self._continuous_enabled():
                    waiting = self._continuous_step(finished, newest) if self.audio_queue.empty() else True
                    if finished:
                        with self.lock:
                            if not waiting and not any(self.cloud_workers.values()) and not self.retry_event.is_set() and not self.translation_retry_event.is_set():
                                self.worker_finishing = True
                                break
                    continue
                requested = self.retry_event.is_set()
                due = time.monotonic() - last_analysis >= (self.analysis_interval if not failure_count else (OFFLINE_RECHECK_SECONDS if offline_failure else 120))
                if (not self.abort_processing.is_set() and enabled and newest and analysis_available
                        and (requested or newest != last_analyzed_line) and self.audio_queue.empty()
                        and (requested or finished or due)):
                    # A user can request retry after the earlier eligibility
                    # read. Consume the latest request atomically with its flag.
                    with self.lock:
                        if self.closing or self.abort_processing.is_set():
                            continue
                        requested = self.retry_event.is_set()
                        self.retry_event.clear()
                        self._recovery['analysis']['manual_requested'] = False
                    last_analysis = time.monotonic()
                    last_analyzed_line = newest
                    self._generation_last_started['analysis'] = last_analysis
                    self._analysis_last_source_id = newest
                    try:
                        if provider == 'openai':
                            self._start_cloud_analysis(manual_retry=requested)
                        else:
                            self._analyze(manual_retry=requested)
                            failure_count = 0
                            offline_failure = False
                    except Exception as exc:
                        failure_count += 1
                        offline_failure = isinstance(exc, LectureOfflineError)
                        self._analysis_failed(exc)
                        if self.inference_unconfirmed:
                            break
                if finished:
                    with self.lock:
                        if not any(self.cloud_workers.values()) and not self.retry_event.is_set():
                            self.worker_finishing = True
                            break
            with self.lock:
                if self.abort_processing.is_set():
                    self._refresh_stopped_locked()
                if self.state['asr']['state'] != 'paused' and not self.abort_processing.is_set():
                    self.state['asr']['state'] = 'failed' if self.state['asr']['failed_chunks'] else 'completed'
                if self.state['analysis']['state'] == 'waiting':
                    self.state['analysis']['state'] = 'idle'
                failures = (self.state['capture']['state'] == 'failed' or
                            self.state['asr']['state'] == 'failed' or self.state['analysis']['state'] == 'failed'
                            or self.state['translation']['state'] == 'failed'
                            or (self.state['provisional_asr']['enabled']
                                and (self.state['provisional_asr']['state'] == 'failed'
                                     or self.state['provisional_asr']['error'])))
                self.state['message'] = ('追加推論を保留しました。録音と保存状態は別に確認してください。'
                    if self.abort_processing.is_set() or self.state['asr']['state'] == 'paused' else
                    '処理を終了しました。一部に失敗があります。保存された音声と状態を確認してください。'
                    if failures else '録音と処理を終了しました。音声・原文・分析は保存されています。')
        except Exception as exc:
            with self.lock:
                self.state['asr'].update(state='failed', error=str(exc)[:1000])
        finally:
            # Even a processing/storage exception must not orphan a live cloud
            # request. close() has its own bounded wait and reports this owner
            # thread as still active until the request actually ends.
            with self.lock:
                pending = list(self.cloud_workers.values())
            for worker in pending:
                if worker is not None:
                    worker.join()
            for outcome in self._take_cloud_outcomes():
                if self._continuous_enabled():
                    self._generation_completed(outcome)
            with self.lock:
                self.worker_finishing = True
            self.persist(force=True)

    def close(self, timeout=20):
        with self.lock:
            self.closing = True
            self.preparation_cancel.set()
            if self.preparation_thread and self.preparation_thread.is_alive():
                self.asr_preparation['stop_requested'] = True
            self.stop_source.set()
            self.abort_processing.set()
            if self.state['capture']['state'] in ACTIVE:
                self.state['capture']['state'] = 'stopping'
        if self.readiness:
            self.readiness.close()
        deadline = time.monotonic() + timeout
        for thread in (self.preparation_thread, self.source_thread, self.worker, *self.cloud_workers.values()):
            if thread:
                thread.join(max(0, deadline - time.monotonic()))
        complete = not any(thread and thread.is_alive()
                           for thread in (self.preparation_thread, self.source_thread, self.worker, *self.cloud_workers.values()))
        if self.recorder:
            observed = self.recorder.snapshot()
            complete = complete and observed.get('stop_confirmed', False) and observed.get('callbacks_confirmed', False)
        with self.lock:
            if not complete:
                self.state['message'] = '終了待ちが時間上限に達しました。残った音声・処理状態を確認してください。'
        self.persist(force=True)
        return complete


def make_server(app, port=8776):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log bearer URL or private transcript content.

        def send(self, status, body, kind='application/json; charset=utf-8', *, authenticate=False):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            if authenticate:
                # Browser cookies share a host across ports; previews must not
                # overwrite another lecture server's authentication.
                self.send_header('Set-Cookie', f'lecture_session_{self.server.server_port}={token}; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def host_ok(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def authorized(self):
            try:
                jar = cookies.SimpleCookie(self.headers.get('Cookie', ''))
                value = jar.get(f'lecture_session_{self.server.server_port}')
                return bool(value and value.value.isascii() and secrets.compare_digest(value.value, token))
            except (cookies.CookieError, TypeError):
                return False

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {'error': 'Hostを確認してください。'})
            parsed = urlsplit(self.path)
            supplied = parse_qs(parsed.query).get('token', [''])[0]
            login = parsed.path == '/' and supplied.isascii() and secrets.compare_digest(supplied, token)
            if not (login or self.authorized()):
                return self.send(403, {'error': '起動時に表示されたURLから開いてください。'})
            if parsed.path == '/api/identity':
                return self.send(200, {'app_id': 'live-lecture-translation', 'schema_version': 1,
                    'cloud_enabled': bool(getattr(app, 'allow_cloud', False)),
                    'continuous_translation_enabled': bool(getattr(app, 'continuous_translation', False)),
                    'process_id': os.getpid(),
                    'started_at': getattr(app, 'started_at', None)})
            if parsed.path == '/api/state':
                return self.send(200, app.snapshot())
            if parsed.path == '/api/analysis-history':
                from lecture_history import read_history_page
                query = parse_qs(parsed.query)
                expected_session = query.get('session_id', [''])[0]
                with app.lock:
                    current_session = (app.state.get('session') or {}).get('id')
                    directory = app.result_dir or getattr(app, 'history_result_dir', None)
                if not expected_session or expected_session != current_session or directory is None:
                    return self.send(409, {'error': '現在のセッションの保存履歴を選んでください。'})
                try:
                    page = read_history_page(directory, session_id=current_session,
                        limit=int(query.get('limit', ['30'])[0]), cursor=query.get('cursor', [None])[0],
                        through_generated_at=float(query['before'][0]) if 'before' in query else None)
                except (ValueError, TypeError):
                    return self.send(400, {'error': '履歴の範囲を確認できません。画面を再読み込みしてください。'})
                except OSError:
                    return self.send(500, {'error': '保存履歴を読み込めません。保存先を確認してください。'})
                with app.lock:
                    if (app.state.get('session') or {}).get('id') != current_session:
                        return self.send(409, {'error': 'セッションが変わりました。最新の状態を取得してください。'})
                return self.send(200, page)
            if parsed.path == '/api/devices':
                try:
                    from lecture_capture import list_devices
                    return self.send(200, {'devices': list_devices()})
                except Exception as exc:
                    return self.send(200, {'devices': [], 'error': str(exc)[:500]})
            names = {'/': ('index.html', 'text/html; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if parsed.path not in names:
                return self.send(404, {'error': 'Not found'})
            name, kind = names[parsed.path]
            return self.send(200, (ASSETS / name).read_bytes(), kind, authenticate=login)

        def do_POST(self):
            origin = self.headers.get('Origin')
            expected = f'http://127.0.0.1:{self.server.server_port}'
            if not self.host_ok() or not self.authorized() or (origin and origin != expected):
                return self.send(403, {'error': 'この画面から操作してください。'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send(415, {'error': 'JSONが必要です。'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('要求の長さが不正です。')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('JSONオブジェクトが必要です。')
                if self.path == '/api/prepare-asr':
                    result = app.prepare_asr()
                elif self.path == '/api/start':
                    result = app.start(payload)
                elif self.path == '/api/stop':
                    result = app.stop()
                elif self.path == '/api/retry-analysis':
                    result = app.retry_analysis()
                elif self.path == '/api/retry-translation':
                    result = app.retry_translation()
                elif self.path == '/api/pause-retries':
                    result = app.pause_retries(payload.get('stage'))
                else:
                    return self.send(404, {'error': 'Not found'})
                return self.send(200, result)
            except (ValueError, TypeError) as exc:
                return self.send(400, {'error': str(exc)[:500]})
            except RuntimeError as exc:
                return self.send(409, {'error': str(exc)[:500]})
            except OSError:
                return self.send(500, {'error': '保存先または音声入力を確認してください。'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    return server, f'http://127.0.0.1:{server.server_port}/?token={token}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8776)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--chunk-seconds', type=int, choices=range(5, 31), default=15)
    parser.add_argument('--provisional-refresh-seconds', type=float, default=3,
                        help='refresh rolling provisional recognition; 0 disables it')
    parser.add_argument('--provisional-window-seconds', type=float, default=15)
    parser.add_argument('--analysis-interval', type=int)
    parser.add_argument('--continuous-translation', action='store_true')
    parser.add_argument('--translation-interval', type=int, default=60)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--results-root', type=Path)
    parser.add_argument('--model-metadata', type=Path, default=MODEL_METADATA)
    parser.add_argument('--replay', type=Path)
    parser.add_argument('--duration', type=float)
    parser.add_argument('--pace', type=int, choices=(0, 1), default=1)
    parser.add_argument('--provider', choices=('off', 'local', 'openai'), default='local')
    parser.add_argument('--model')
    parser.add_argument('--language', choices=('auto', 'en', 'ja'))
    parser.add_argument('--allow-cloud', action='store_true', help='enable only after text transmission and budget authorization')
    parser.add_argument('--cloud-authorization', type=Path,
        help='explicit user-approved JSON with text scope and date-specific API budgets')
    parser.add_argument('--key-file', type=Path, help='read OPENAI_API_KEY only from this explicitly selected file')
    parser.add_argument('--exit-after-replay', action='store_true')
    parser.add_argument('--view-session', type=Path, help='display a saved state.json without recording or changing it')
    args = parser.parse_args()
    if args.analysis_interval is not None and args.analysis_interval < 1:
        parser.error('--analysis-interval must be positive')
    if args.translation_interval < 1:
        parser.error('--translation-interval must be positive')
    if args.allow_cloud and args.cloud_authorization is None:
        parser.error('--allow-cloud requires --cloud-authorization with explicit text scope and daily budgets')
    data_root = args.data_root or REPO / ('data/event-audio/mac-live-continuous' if args.continuous_translation
                                        else 'data/event-audio/mac-live')
    results_root = args.results_root or REPO / ('results/event-audio/mac-live-continuous' if args.continuous_translation
                                              else 'results/event-audio/mac-live')
    scope = None
    if args.allow_cloud:
        from lecture_cloud_scope import CloudScope
        scope = CloudScope(args.cloud_authorization)
    if args.key_file:
        import event_insights_cloud as cloud
        os.environ.pop('OPENAI_API_KEY', None)
        cloud.DOTENV_PATH = args.key_file.expanduser().resolve()
    if args.allow_cloud:
        import event_insights_cloud as cloud
        cloud.configure_budget_authorization(args.cloud_authorization)
    from lecture_readiness import LectureReadiness
    readiness = LectureReadiness(data_root=data_root,
        model_metadata=args.model_metadata, allow_cloud=args.allow_cloud, cloud_scope=scope)
    language = args.language or ('en' if args.allow_cloud else 'auto')
    model = args.model or ('gpt-6.1-sol' if args.allow_cloud else 'qwen3:4b')
    interval = args.analysis_interval or (120 if args.continuous_translation and args.allow_cloud else
                                         90 if args.allow_cloud else 30)
    app = LectureApp(chunk_seconds=args.chunk_seconds, analysis_interval=interval,
                     model_metadata=args.model_metadata, allow_cloud=args.allow_cloud, cloud_scope=scope,
                     readiness=readiness, default_model=model, default_language=language,
                     data_root=data_root, results_root=results_root,
                     continuous_translation=args.continuous_translation, translation_interval=args.translation_interval,
                     provisional_refresh_seconds=args.provisional_refresh_seconds,
                     provisional_window_seconds=args.provisional_window_seconds)
    if args.view_session:
        saved = json.loads(args.view_session.read_text())
        if saved.get('schema_version') != 1 or not isinstance(saved.get('lines'), list):
            parser.error('保存状態の形式が不正です。')
        try:
            legacy_source_policy(saved)
        except ValueError as exc:
            parser.error(str(exc))
        app.state = saved
        # CLI-selected directory only; never follow a path embedded in saved JSON.
        app.history_result_dir = args.view_session.resolve().parent
        app.state['message'] = '保存済みの結果を表示しています。現在の録音ではありません。'
        if app.state['capture']['state'] in ACTIVE:
            app.state['capture'].update(state='failed', error='保存時は録音中でした。現在の状態は未確認です。')
        for stage in ('asr', 'analysis', 'translation', 'provisional_asr'):
            if app.state.get(stage, {}).get('state') in {'running', 'waiting'}:
                app.state[stage].update(state='paused', error='保存時の処理です。現在は実行していません。')
    server, url = make_server(app, args.port)
    launch = results_root
    launch.mkdir(parents=True, exist_ok=True)
    # Ephemeral replay/view servers must not replace the launcher's live URL.
    url_path = launch / ('url.txt' if server.server_port == 8776 else f'url-{server.server_port}.txt')
    url_path.write_text(url + '\n')
    print(url, flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if args.open:
        webbrowser.open(url)
    replay_pending = bool(args.replay)
    if not args.view_session:
        app.prepare_asr()
    try:
        while not stop.wait(.1):
            if replay_pending:
                with app.lock:
                    preparation = app.asr_preparation['state']
                    preparing = bool(app.preparation_thread and app.preparation_thread.is_alive())
                    if not preparing:
                        if preparation != 'ready' or app.asr_preparation['stop_requested']:
                            break
                        replay_model = args.model or ('gpt-6.1-sol' if args.provider == 'openai' else 'qwen3:4b')
                        app.start({'language': language, 'provider': args.provider, 'model': replay_model},
                                  replay=args.replay, replay_seconds=args.duration, pace=args.pace)
                        replay_pending = False
            if (args.exit_after_replay and args.replay and not replay_pending
                    and app.source_done.is_set() and not app.worker.is_alive()):
                break
    finally:
        complete = app.close(timeout=130)
        server.shutdown()
        server.server_close()
    state = app.snapshot()
    if not complete or replay_pending or (args.exit_after_replay and any(state[name]['state'] in {'failed', 'paused'}
                                                    for name in ('capture', 'asr', 'analysis', 'translation'))):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
