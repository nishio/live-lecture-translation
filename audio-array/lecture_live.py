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

REPO = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).with_name('lecture-dashboard')
MODEL_METADATA = REPO / 'data/event-audio/model-cache/whisper-turbo.json'
ACTIVE = {'starting', 'recording', 'stalled', 'stopping'}
OFFLINE_RECHECK_SECONDS = 30


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
               Path(__file__).with_name('lecture_cloud_scope.py'),
               Path(__file__).with_name('lecture_readiness.py'),
               Path(__file__).with_name('event_insights.py'),
               Path(__file__).with_name('event_insights_cloud.py'),
               Path(__file__).with_name('local_inference.py'),
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
            'capture': {'state': 'idle', 'audio_seconds': 0, 'last_audio_at': None,
                        'rms_dbfs': None, 'peak_dbfs': None, 'error': None},
            'asr': {'state': 'idle', 'through_seconds': 0, 'queue_seconds': 0, 'error': None,
                    'failed_chunks': []},
            'analysis': {'state': 'idle', 'through_seconds': 0, 'generated_at': None,
                         'provider': 'local', 'model': None, 'error': None, 'result': None},
            'translation': {'enabled': False, 'state': 'idle', 'blocks': [], 'pending_lines': 0,
                'excluded_uncertain_lines': 0, 'native_lines': 0, 'through_seconds': 0,
                'error': None, 'generated_at': None, 'worker_alive': False,
                'completion_confirmed': True, 'retry_required': False},
            'lines': [], 'analysis_history': [], 'message': 'マイクを選び、録音を開始してください。'}


def select_analysis_lines(lines, previous, *, window_seconds=180, max_lines=100):
    """Prefer new context, retaining prior evidence when it fits; never future input."""
    usable = [line for line in lines if not line.get('uncertain') and line.get('text', '').strip()]
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
    breaks = [{'start_seconds': line['start_seconds'], 'end_seconds': line['end_seconds']}
              for line in lines if line.get('uncertain')] if include_block_translations else None
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
    """Reuse the pinned MLX model in one worker; hash weights once per session."""
    def __init__(self, metadata=MODEL_METADATA):
        self.metadata_path = Path(metadata)
        self.model = None

    def __call__(self, chunk, destination, language):
        from transcribe_local import read_mono, sha256
        from local_inference import inference_slot
        audio = read_mono(chunk['path'])
        if self.model is None:
            self.model = json.loads(self.metadata_path.read_text())
            model_path = Path(self.model['local_path']).resolve()
            weights = model_path / 'weights.safetensors'
            if not weights.is_file():
                weights = model_path / 'weights.npz'
            paths = [self.metadata_path, weights, model_path / 'config.json']
            self.identities = {str(p): (p.stat().st_size, p.stat().st_mtime_ns, sha256(p)) for p in paths}
        for path, identity in self.identities.items():
            observed = Path(path).stat()
            if (observed.st_size, observed.st_mtime_ns) != identity[:2]:
                raise ValueError('認識モデルが処理中に変更されました。')
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        before = time.monotonic()
        with inference_slot('lecture-asr'):
            acquired = time.monotonic()
            import mlx_whisper
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
                 translation_interval=60, translator=None):
        self.data_root = Path(data_root or REPO / 'data/event-audio/mac-live')
        self.results_root = Path(results_root or REPO / 'results/event-audio/mac-live')
        self.chunk_seconds = chunk_seconds
        self.analysis_interval = analysis_interval if analysis_interval is not None else (120 if continuous_translation else 30)
        if type(continuous_translation) is not bool or not math.isfinite(translation_interval) or translation_interval <= 0:
            raise ValueError('連続翻訳の設定が不正です。')
        self.continuous_translation = continuous_translation
        self.translation_interval = translation_interval
        self.translator_override = translator
        self.model_metadata = model_metadata
        self.transcriber_override = transcriber
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
        self.cloud_worker = None
        self._cloud_outcome = None
        self.cloud_kind = None
        self.translation_retry_event = threading.Event()
        self._generation_last_started = {'analysis': -math.inf, 'translation': -math.inf}
        self._generation_last_kind = None
        self._analysis_last_source_id = None
        self._analysis_manual_required = False
        self._generation_offline = {'analysis': False, 'translation': False}
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

    def snapshot(self):
        with self.lock:
            self._refresh_translation_locked()
            result = deepcopy(self.state)
            result['updated_at'] = time.time()
            result['asr']['queue_seconds'] = max(0, result['capture']['audio_seconds'] - result['asr']['through_seconds'])
            cloud_alive = bool(self.cloud_worker and self.cloud_worker.is_alive())
            result['processing_active'] = bool(cloud_alive or
                (self.worker and self.worker.is_alive() and not self.worker_finishing))
            result['analysis']['worker_alive'] = bool((cloud_alive and self.cloud_kind == 'analysis') or
                (result['analysis']['provider'] == 'local' and result['analysis']['state'] == 'running'
                 and self.worker and self.worker.is_alive()))
            result['analysis']['completion_confirmed'] = (not result['analysis']['worker_alive'] and
                result['analysis']['state'] != 'running' and not self.inference_unconfirmed)
            result['analysis']['untranslated_lines'] = sum(1 for row in result['lines']
                if not row.get('uncertain') and row.get('language') != 'ja' and not row.get('translation_ja'))
            result['capabilities'] = {'cloud_enabled': self.allow_cloud,
                                     'cloud_models': ['gpt-6-luna', 'gpt-6.1-sol'],
                                     'default_provider': 'openai' if self.allow_cloud else 'local',
                                     'default_model': self.default_model,
                                     'default_language': self.default_language,
                                     'analysis_interval_seconds': self.analysis_interval}
            result['capabilities'].update(continuous_translation=self.continuous_translation,
                continuous_translation_enabled=self.continuous_translation,
                translation_interval_seconds=self.translation_interval)
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

    def _continuous_enabled(self):
        return bool(self.state.get('translation', {}).get('enabled'))

    def _refresh_translation_locked(self):
        translation = self.state.setdefault('translation', deepcopy(blank_state()['translation']))
        covered = {identity for block in translation['blocks'] for identity in block['source_ids']}
        lines = self.state['lines']
        pending = [line for line in lines if str(line.get('text', '')).strip() and not line.get('uncertain')
                   and line.get('language') != 'ja' and line['id'] not in covered]
        translation.update(pending_lines=len(pending),
            excluded_uncertain_lines=sum(bool(line.get('uncertain')) for line in lines),
            native_lines=sum(bool(str(line.get('text', '')).strip()) and not line.get('uncertain')
                             and line.get('language') == 'ja' for line in lines),
            worker_alive=bool(self.cloud_worker and self.cloud_worker.is_alive() and self.cloud_kind == 'translation'))
        if translation['enabled'] and pending and translation['state'] == 'completed':
            translation['state'] = 'waiting'
        translation['completion_confirmed'] = not translation['worker_alive'] and translation['state'] != 'running'
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
            if (self.state['capture']['state'] in ACTIVE or (self.worker and self.worker.is_alive())
                    or (self.cloud_worker and self.cloud_worker.is_alive())
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
            self._generation_last_started = {'analysis': -math.inf, 'translation': time.monotonic()}
            self._generation_last_kind = None
            self._analysis_last_source_id = None
            self._analysis_manual_required = False
            self._generation_offline = {'analysis': False, 'translation': False}
            self.state['message'] = '保存済み音声の逐次再生試験です。' if replay else 'マイクを起動しています。'
            self.recorder = None
            self.worker = self.source_thread = None
            self.cloud_worker = None
            self._cloud_outcome = None
            self.cloud_kind = None
            try:
                save_runtime(self.result_dir, {'language': language, 'provider': provider, 'model': model,
                    'chunk_seconds': self.chunk_seconds, 'analysis_interval': self.analysis_interval,
                    'continuous_translation': self.continuous_translation,
                    'translation_interval': self.translation_interval,
                    'replay': bool(replay), 'pace': pace if replay else None})
                self.cloud_baseline_keys = set()
                if provider == 'openai':
                    import event_insights_cloud as cloud
                    self.cloud_baseline_keys = set(cloud._load_ledger()['requests'])
                    atomic_json(self.result_dir / 'cloud-baseline.json',
                                {'request_keys_before': sorted(self.cloud_baseline_keys)})
                self._cost_report()
                self.transcriber = self.transcriber_override or LocalTranscriber(self.model_metadata)
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
            if self.state['capture']['state'] in ACTIVE:
                self.state['capture']['state'] = 'stopping'
                self.state['message'] = '録音を終了し、保存済みの音声を処理しています。'
                self.stop_source.set()
        self.persist(force=True)
        return self.snapshot()

    def retry_analysis(self):
        with self.lock:
            if self.closing or self.inference_unconfirmed:
                raise RuntimeError('終了処理中、またはローカル推論の終了が未確認です。')
            if self.worker_finishing and self.worker and self.worker.is_alive():
                raise RuntimeError('前回処理を保存中です。完了してから再試行してください。')
            if self.result_dir is None:
                raise ValueError('保存結果の閲覧中です。新しく録音を開始してください。')
            if not self.state['lines'] or self.state['analysis']['provider'] == 'off':
                raise ValueError('解析対象の原文がありません。')
            if self.state['analysis']['state'] == 'running' or self.cloud_worker is not None:
                raise RuntimeError('解析はすでに進行中です。')
            self.retry_event.set()
            if not self.worker or not self.worker.is_alive():
                self.worker_finishing = False
                self.worker = threading.Thread(target=self._process, name='lecture-retry', daemon=False)
                self.worker.start()
        return self.snapshot()

    def retry_translation(self):
        with self.lock:
            if self.closing or self.inference_unconfirmed:
                raise RuntimeError('終了処理中、または推論の終了が未確認です。')
            if self.worker_finishing and self.worker and self.worker.is_alive():
                raise RuntimeError('前回処理を保存中です。完了してから再試行してください。')
            if not self.result_dir or not self._continuous_enabled():
                raise ValueError('このセッションでは連続翻訳を実行できません。')
            pending, _ = self._refresh_translation_locked()
            if not pending:
                raise ValueError('未翻訳の対象原文がありません。')
            if self.cloud_worker is not None:
                raise RuntimeError('翻訳または分析がすでに進行中です。')
            self.translation_retry_event.set()
            if not self.worker or not self.worker.is_alive():
                self.worker_finishing = False
                self.worker = threading.Thread(target=self._process, name='lecture-translation-retry', daemon=False)
                self.worker.start()
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
                    'session': deepcopy(self.state['session']), 'result_dir': self.result_dir}

    def _analyze(self, *, manual_retry=False, prepared=None):
        from lecture_analysis import analyze_snapshot
        job = prepared if prepared is not None else self._prepare_analysis()
        if job is None:
            return
        lines, previous = job['lines'], job['previous']
        translation_ids, selection, through = job['translation_ids'], job['selection'], job['through']
        provider, model, result_dir = job['provider'], job['model'], job['result_dir']
        self.persist(force=True)
        before = time.monotonic()
        generate = self.analyzer_override or analyze_snapshot
        if provider == 'openai':
            if self.readiness:
                health = self.readiness.snapshot()
                network = next((row for row in health['checks'] if row['id'] == 'network'), None)
                if network and network.get('reachable') is not True:
                    raise LectureOfflineError('インターネット接続待ちです。録音・原文保存は続け、新着原文で自動的に再確認します。')
            receipt = self.cloud_scope.reserve(job['session'], model, through)
            append_json(result_dir / 'cloud-scope.jsonl', receipt)
        # Only the explicit retry action can authorize another cloud attempt.
        # The adapter retains prior charges and enforces its budget again.
        retry_options = {'retry_failed': True} if provider == 'openai' and manual_retry else {}
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
            append_json(self.result_dir / 'analysis-history.jsonl', result)
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
            if self.cloud_worker is not None:
                raise RuntimeError('クラウド解析はすでに進行中です。')
            job = self._prepare_analysis()
            if job is None:
                return
            self._start_cloud_job('analysis', job, manual_retry)

    def _prepare_translation(self):
        from lecture_translation import plan_translation
        with self.lock:
            if self.closing or self.abort_processing.is_set() or not self._continuous_enabled():
                return None
            _, covered = self._refresh_translation_locked()
            plan = plan_translation(deepcopy(self.state['lines']), covered, flush=True)
            if plan is None:
                return None
            self.state['translation'].update(state='running', error=None, retry_required=False)
            return {'plan': deepcopy(plan), 'session': deepcopy(self.state['session']),
                    'result_dir': self.result_dir, 'model': self.state['analysis']['model'],
                    'started_at': time.time()}

    def _translate(self, job, *, manual_retry=False):
        from lecture_translation import translate_batch, TranslationResponseError
        plan = job['plan']
        self.persist(force=True)
        before = time.monotonic()
        if self.readiness:
            network = next((row for row in self.readiness.snapshot()['checks'] if row['id'] == 'network'), None)
            if network and network.get('reachable') is not True:
                raise LectureOfflineError('通信の復帰を待っています。未翻訳の原文は保存されています。')
        receipt = self.cloud_scope.reserve(job['session'], job['model'], plan['through_seconds'])
        append_json(job['result_dir'] / 'cloud-scope.jsonl', {**receipt, 'stage': 'translation'})
        generate = self.translator_override or translate_batch
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
            additions = []
            for block in blocks:
                rows = [source[identity] for identity in block['source_ids']]
                stable = json.dumps([job['session']['id'], block['source_ids']], ensure_ascii=False).encode()
                additions.append({'id': 'tr-' + hashlib.sha256(stable).hexdigest()[:24],
                    'text': block['text'], 'source_ids': list(block['source_ids']),
                    'start_seconds': min(row['start_seconds'] for row in rows),
                    'end_seconds': max(row['end_seconds'] for row in rows),
                    'generated_at': result.get('generated_at', published), 'published_at': published})
            # The history is durable before coverage advances. A write failure
            # retains every pending source ID and never silently skips a batch.
            append_json(job['result_dir'] / 'translation-history.jsonl',
                        {**result, 'blocks': additions, 'started_at': job['started_at'], 'published_at': published})
            self.state['translation']['blocks'].extend(additions)
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
            offline = isinstance(exc, LectureOfflineError)
            self.state['translation'].update(state='waiting' if offline else 'failed', error=str(exc)[:1000],
                retry_required=bool(pending) and not offline)
        self.persist(force=True)
        try:
            self._cost_report()
        except Exception:
            pass

    def _start_cloud_job(self, kind, job, manual_retry):
        with self.lock:
            if self.cloud_worker is not None:
                raise RuntimeError('翻訳または分析がすでに進行中です。')
            self._cloud_outcome = None
            self.cloud_kind = kind
            def generate():
                error = None
                try:
                    if kind == 'translation':
                        self._translate(job, manual_retry=manual_retry)
                    else:
                        self._analyze(manual_retry=manual_retry, prepared=job)
                except Exception as exc:
                    error = exc
                    try:
                        failure = self._translation_failed if kind == 'translation' else self._analysis_failed
                        failure(exc, session_id=job['session']['id'])
                    except Exception:
                        pass  # Preserve the original failure if status storage fails.
                finally:
                    with self.lock:
                        self._cloud_outcome = {'error': error, 'kind': kind, 'session_id': job['session']['id']}
            self.cloud_worker = threading.Thread(target=generate, name='lecture-cloud-' + kind, daemon=False)
            try:
                self.cloud_worker.start()
            except Exception:
                self.cloud_worker = None
                self.cloud_kind = None
                raise

    def _take_cloud_outcome(self):
        with self.lock:
            if self.cloud_worker is None or self.cloud_worker.is_alive():
                return None
            self.cloud_worker.join()
            outcome = self._cloud_outcome or {'error': RuntimeError('クラウド解析の完了結果がありません。')}
            self.cloud_worker = None
            self.cloud_kind = None
            self._cloud_outcome = None
            return outcome

    def _cost_report(self, *, session_id=None, result_dir=None):
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
            held = sum(row['charged_nanodollars'] for row in entries.values() if row['state'] != 'completed') / 1e9
            confirmed = sum(row['charged_nanodollars'] for row in entries.values() if row['state'] == 'completed') / 1e9
            cost.update(confirmed_api_usd=confirmed, retained_reservation_usd=held,
                        additional_api_usd=confirmed if not held else None,
                        matching_request_count=len(entries), daily_budget=adapter.budget_status())
        atomic_json(result_dir / 'cost-report.json', cost)

    def _generation_completed(self, outcome):
        kind = outcome.get('kind', 'analysis')
        self._generation_offline[kind] = isinstance(outcome['error'], LectureOfflineError)
        if kind == 'analysis':
            self._analysis_manual_required = bool(outcome['error']) and not self._generation_offline[kind]

    def _continuous_step(self, finished, newest):
        """Schedule one generation fairly; retain failures and their pending IDs."""
        with self.lock:
            if self.closing or self.abort_processing.is_set():
                return False
            if self.cloud_worker is not None:
                return True
            pending, _ = self._refresh_translation_locked()
            now = time.monotonic()
            manual = {'analysis': self.retry_event.is_set(), 'translation': self.translation_retry_event.is_set()}
            wants = {
                'translation': bool(pending) and (manual['translation'] or not self.state['translation']['retry_required']),
                'analysis': bool(newest) and (manual['analysis'] or (not self._analysis_manual_required
                    and (newest != self._analysis_last_source_id or self._generation_offline['analysis'])))}
            due = {}
            for kind, interval in (('translation', self.translation_interval), ('analysis', self.analysis_interval)):
                elapsed = now - self._generation_last_started[kind]
                due[kind] = wants[kind] and (manual[kind] or (
                    elapsed >= OFFLINE_RECHECK_SECONDS if self._generation_offline[kind]
                    else finished or elapsed >= interval))
            choices = [kind for kind in ('analysis', 'translation') if due[kind]]
            if not choices:
                if not pending and self.state['translation']['state'] not in ('failed', 'running', 'paused'):
                    self.state['translation']['state'] = 'completed' if finished else 'waiting'
                return bool(wants['translation'] or wants['analysis']) if finished else False
            kind = (choices[0] if len(choices) == 1 else
                    ('translation' if self._generation_last_kind == 'analysis' else 'analysis'))
            event = self.translation_retry_event if kind == 'translation' else self.retry_event
            requested = event.is_set()
            event.clear()
            self._generation_last_started[kind] = now
            self._generation_last_kind = kind
            if kind == 'analysis':
                self._analysis_last_source_id = newest
            try:
                job = self._prepare_translation() if kind == 'translation' else self._prepare_analysis()
                if job is not None:
                    self._start_cloud_job(kind, job, requested)
            except Exception as exc:
                failure = self._translation_failed if kind == 'translation' else self._analysis_failed
                failure(exc)
                self._generation_completed({'kind': kind, 'error': exc})
            return True

    def _process(self):
        last_analysis = -math.inf
        last_analyzed_line = None
        failure_count = 0
        offline_failure = False
        try:
            from catchup_page import build_lines
            while True:
                outcome = self._take_cloud_outcome()
                if outcome is not None:
                    if self._continuous_enabled():
                        self._generation_completed(outcome)
                    else:
                        failure_count = failure_count + 1 if outcome['error'] else 0
                        offline_failure = isinstance(outcome['error'], LectureOfflineError)
                if self.abort_processing.is_set():
                    with self.lock:
                        self.state['asr'].update(state='paused', error='アプリ終了のため残りの認識を保留しました。音声は保存されています。')
                        pending = self.cloud_worker
                    if pending is not None:
                        pending.join(.2)
                        continue
                    with self.lock:
                        pending_lines, _ = self._refresh_translation_locked()
                        if self._continuous_enabled() and pending_lines and self.state['translation']['state'] != 'failed':
                            self.state['translation'].update(state='paused', error='アプリ終了のため未翻訳の原文を保留しました。')
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
                        report = self.transcriber(chunk, self.result_dir / 'asr' / f"{chunk['index']:06d}.json",
                                                  self.state['session']['language'])
                        lines = build_lines({'segments': [{'index': chunk['index'],
                                                           'start_seconds': chunk['start_seconds']}]},
                                            {chunk['index']: report})
                        for index, line in enumerate(lines):
                            line['id'] = f"c{chunk['index']:06d}-l{index:04d}"
                            line['boundary_context'] = 'non-overlapping audio; incomplete sentences may need following speech'
                        with self.lock:
                            self.state['lines'].extend(lines)
                            self.state['asr'].update(state='waiting', through_seconds=chunk['end_seconds'])
                            append_json(self.result_dir / 'transcript.jsonl', {'chunk': chunk, 'lines': lines})
                            append_json(self.result_dir / 'measurements.jsonl', {'stage': 'asr',
                                'through_seconds': chunk['end_seconds'], 'processing_seconds': time.monotonic() - began,
                                'published_at': time.time(), 'capture_seconds': self.state['capture']['audio_seconds'],
                                'source_chunk_completed_at': chunk.get('completed_at'),
                                'chunk_ready_to_publication_seconds': (time.time() - chunk['completed_at']
                                    if chunk.get('completed_at') else None), 'browser_render_measured': False})
                    except Exception as exc:
                        with self.lock:
                            self.state['asr'].update(state='failed', error=str(exc)[:1000])
                            self.state['asr']['failed_chunks'].append({'index': chunk['index'],
                                'start_seconds': chunk['start_seconds'], 'end_seconds': chunk['end_seconds']})
                        append_json(self.result_dir / 'failed-chunks.jsonl', {'chunk': chunk, 'error': str(exc)[:1000]})
                    finally:
                        self.audio_queue.task_done()
                    self.persist(force=True)
                finished = self.source_done.is_set() and self.audio_queue.empty()
                with self.lock:
                    eligible = [line for line in self.state['lines'] if not line.get('uncertain') and line.get('text', '').strip()]
                    newest = eligible[-1]['id'] if eligible else None
                    provider = self.state['analysis']['provider']
                    enabled = provider != 'off'
                    analysis_available = self.cloud_worker is None
                if self._continuous_enabled():
                    waiting = self._continuous_step(finished, newest) if self.audio_queue.empty() else True
                    if finished:
                        with self.lock:
                            if not waiting and self.cloud_worker is None and not self.retry_event.is_set() and not self.translation_retry_event.is_set():
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
                    last_analysis = time.monotonic()
                    last_analyzed_line = newest
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
                        if self.cloud_worker is None and not self.retry_event.is_set():
                            self.worker_finishing = True
                            break
            with self.lock:
                if self.state['asr']['state'] != 'paused':
                    self.state['asr']['state'] = 'failed' if self.state['asr']['failed_chunks'] else 'completed'
                if self.state['analysis']['state'] == 'waiting':
                    self.state['analysis']['state'] = 'idle'
                failures = (self.state['capture']['state'] == 'failed' or
                            self.state['asr']['state'] == 'failed' or self.state['analysis']['state'] == 'failed'
                            or self.state['translation']['state'] == 'failed')
                self.state['message'] = ('追加推論を保留しました。録音と保存状態は別に確認してください。'
                    if self.state['asr']['state'] == 'paused' else
                    '処理を終了しました。一部に失敗があります。保存された音声と状態を確認してください。'
                    if failures else '録音と処理を終了しました。音声・原文・分析は保存されています。')
        except Exception as exc:
            with self.lock:
                self.state['asr'].update(state='failed', error=str(exc)[:1000])
        finally:
            # Even a processing/storage exception must not orphan a live cloud
            # request. close() has its own bounded wait and reports this owner
            # thread as still active until the request actually ends.
            pending = self.cloud_worker
            if pending is not None:
                pending.join()
                self._take_cloud_outcome()
            with self.lock:
                self.worker_finishing = True
            self.persist(force=True)

    def close(self, timeout=20):
        if self.readiness:
            self.readiness.close()
        with self.lock:
            self.closing = True
            self.stop_source.set()
            self.abort_processing.set()
            if self.state['capture']['state'] in ACTIVE:
                self.state['capture']['state'] = 'stopping'
        deadline = time.monotonic() + timeout
        for thread in (self.source_thread, self.worker, self.cloud_worker):
            if thread:
                thread.join(max(0, deadline - time.monotonic()))
        complete = not any(thread and thread.is_alive()
                           for thread in (self.source_thread, self.worker, self.cloud_worker))
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
                if self.path == '/api/start':
                    result = app.start(payload)
                elif self.path == '/api/stop':
                    result = app.stop()
                elif self.path == '/api/retry-analysis':
                    result = app.retry_analysis()
                elif self.path == '/api/retry-translation':
                    result = app.retry_translation()
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
                     continuous_translation=args.continuous_translation, translation_interval=args.translation_interval)
    if args.view_session:
        saved = json.loads(args.view_session.read_text())
        if saved.get('schema_version') != 1 or not isinstance(saved.get('lines'), list):
            parser.error('保存状態の形式が不正です。')
        app.state = saved
        app.state['message'] = '保存済みの結果を表示しています。現在の録音ではありません。'
        if app.state['capture']['state'] in ACTIVE:
            app.state['capture'].update(state='failed', error='保存時は録音中でした。現在の状態は未確認です。')
        for stage in ('asr', 'analysis', 'translation'):
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
    if args.replay:
        replay_model = args.model or ('gpt-6.1-sol' if args.provider == 'openai' else 'qwen3:4b')
        app.start({'language': language, 'provider': args.provider, 'model': replay_model},
                  replay=args.replay, replay_seconds=args.duration, pace=args.pace)
    try:
        while not stop.wait(.5):
            if args.exit_after_replay and args.replay and app.source_done.is_set() and not app.worker.is_alive():
                break
    finally:
        complete = app.close(timeout=130)
        server.shutdown()
        server.server_close()
    state = app.snapshot()
    if not complete or (args.exit_after_replay and any(state[name]['state'] in {'failed', 'paused'}
                                                    for name in ('capture', 'asr', 'analysis', 'translation'))):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
