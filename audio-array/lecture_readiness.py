"""Read-only readiness checks for a local conference recorder; never starts audio."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import threading
import time


AGENDA = {'date': '', 'timezone': 'Asia/Tokyo', 'items': []}


def nearest_existing(path):
    path = Path(path).resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def storage_check(path):
    parent = nearest_existing(path)
    free = shutil.disk_usage(parent).free
    if not os.access(parent, os.W_OK):
        return {'id': 'storage', 'label': '音声の保存先', 'state': 'blocked',
                'message': '保存先に書き込めません。'}
    # Six hours of raw PCM plus chunk WAVs is about 1.4 GB. Leave headroom
    # for source copies, transcripts, and safe shutdown; do not fill the disk.
    if free < 2 * 1024 ** 3:
        return {'id': 'storage', 'label': '音声の保存先', 'state': 'blocked',
                'message': '空き容量が2 GB未満です。保存先の空きを確保してください。', 'free_bytes': free}
    return {'id': 'storage', 'label': '音声の保存先', 'state': 'ready',
            'message': f'空き {free / 1024 ** 3:.1f} GB', 'free_bytes': free}


def network_available():
    # No API key, audio, transcript, HTTP body or API request is sent.
    # TLS only establishes reachability, never API authentication or quota.
    context = ssl.create_default_context()
    with socket.create_connection(('api.openai.com', 443), timeout=3) as raw:
        raw.settimeout(3)
        with context.wrap_socket(raw, server_hostname='api.openai.com'):
            return True


class LectureReadiness:
    def __init__(self, *, data_root, model_metadata, allow_cloud=False, cloud_scope=None,
                 network_probe=network_available, refresh_seconds=30, network_timeout=4):
        self.data_root, self.model_metadata = Path(data_root), Path(model_metadata)
        self.allow_cloud, self.cloud_scope = allow_cloud, cloud_scope
        self.network_probe, self.refresh_seconds = network_probe, refresh_seconds
        self.network_timeout = network_timeout
        self.network_thread = None
        self.network_result = False
        self.lock = threading.Lock()
        self.thread = None
        self.closed = False
        self.value = {'state': 'checking', 'checks': [], 'checked_at': None,
                      'message': '開始前の準備を確認しています。録音は開始しません。'}

    def refresh(self):
        with self.lock:
            if self.closed or (self.thread and self.thread.is_alive()):
                return
            self.thread = threading.Thread(target=self._run, name='lecture-readiness', daemon=True)
            self.thread.start()

    def snapshot(self):
        with self.lock:
            result = deepcopy(self.value)
        if result['checked_at'] is None or time.time() - result['checked_at'] > self.refresh_seconds:
            self.refresh()
        return result

    def _run(self):
        checks = []
        try:
            checks.append(storage_check(self.data_root))
        except OSError:
            checks.append({'id': 'storage', 'label': '音声の保存先', 'state': 'blocked', 'message': '保存先を確認できません。'})
        try:
            metadata = json.loads(self.model_metadata.read_text())
            path = Path(metadata['local_path'])
            modules = all(importlib.util.find_spec(name) for name in ('numpy', 'mlx_whisper'))
            if not modules or not (path / 'config.json').is_file() or not any((path / name).is_file() for name in ('weights.safetensors', 'weights.npz')):
                raise ValueError()
            checks.append({'id': 'asr', 'label': '音声認識', 'state': 'ready', 'message': '保存済みモデルを使用します。'})
        except (OSError, ValueError, KeyError, TypeError):
            checks.append({'id': 'asr', 'label': '音声認識', 'state': 'blocked', 'message': '専用Pythonまたは保存済みモデルを確認してください。'})
        try:
            from lecture_capture import ensure_native_helper
            ensure_native_helper()
            checks.append({'id': 'capture', 'label': '録音の準備', 'state': 'ready', 'message': '録音機能を準備しました。開始後に音声の受信を確認します。'})
        except Exception:
            checks.append({'id': 'capture', 'label': '録音の準備', 'state': 'blocked', 'message': '録音用helperを準備できません。起動ターミナルを確認してください。'})
        if self.allow_cloud:
            try:
                import event_insights_cloud as cloud
                scope = self.cloud_scope.status()
                budget = cloud.budget_status()
                key = cloud.has_api_key()
                ready = scope['authorized_today'] and scope['remaining_seconds'] > 0 and key and budget['spent_usd'] < budget['budget_usd']
                checks.append({'id': 'cloud', 'label': '和訳・解説', 'state': 'ready' if ready else 'warning',
                    'message': '送信範囲・API設定・予算を確認しました。' if ready else '文字解析の範囲・API設定・残額を確認してください。録音と原文保存は使用できます。'})
            except Exception:
                checks.append({'id': 'cloud', 'label': '和訳・解説', 'state': 'warning', 'message': 'API設定・送信範囲を確認できません。録音と原文保存は使用できます。'})
            # Publish mandatory local checks before DNS/TLS. DNS may ignore a
            # socket timeout; it must never hold the recording Start button.
            self._publish(checks + [{'id': 'network', 'label': 'インターネット', 'state': 'warning',
                'message': '接続を確認中です。録音と原文保存は使用できます。', 'reachable': None}])
            reachable = self._bounded_network_probe()
            checks.append({'id': 'network', 'label': 'インターネット', 'state': 'ready' if reachable else 'warning',
                'message': 'API接続先に到達しました。' if reachable else '解説の接続待ちです。録音と原文保存は続けられます。',
                'reachable': reachable})
        self._publish(checks)

    def _bounded_network_probe(self):
        # Retain one outstanding daemon probe rather than creating an unbounded
        # series of threads if the system resolver never returns.
        if self.network_thread and self.network_thread.is_alive():
            return False
        self.network_result = False
        def probe():
            try:
                self.network_result = bool(self.network_probe())
            except Exception:
                self.network_result = False
        self.network_thread = threading.Thread(target=probe, name='lecture-network-check', daemon=True)
        self.network_thread.start()
        self.network_thread.join(self.network_timeout)
        return bool(not self.network_thread.is_alive() and self.network_result)

    def _publish(self, checks):
        blocked = any(row['state'] == 'blocked' for row in checks)
        value = {'state': 'blocked' if blocked else 'ready', 'checked_at': time.time(), 'checks': checks,
                 'message': '開始できません。準備の表示を確認してください。' if blocked else 'マイクを確認して、録音を開始できます。'}
        with self.lock:
            self.value = value

    def require_recording_ready(self):
        # Recheck free space just before start, without waiting on networking.
        check = storage_check(self.data_root)
        if check['state'] == 'blocked':
            raise ValueError(check['message'])
        status = self.snapshot()
        if status['state'] != 'ready':
            raise ValueError(status['message'])

    def close(self):
        with self.lock:
            self.closed = True
