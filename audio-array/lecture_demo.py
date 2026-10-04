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
import json
import math
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit


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


class DemoTimeline:
    def __init__(self, session_dir):
        self.directory = Path(session_dir).resolve()
        final = load_json(self.directory / 'state.json')
        self.simulation = deepcopy(final.get('display_simulation', {}))
        self.session = deepcopy(final['session'])
        self.started_at = self.session['started_at']
        if not numeric(self.started_at):
            raise DemoDataError('セッション開始時刻がありません。')
        self.model = final['analysis']['model']
        self.audio_seconds = final['capture']['audio_seconds']
        if not numeric(self.audio_seconds) or self.audio_seconds <= 0:
            raise DemoDataError('記録された音声長が不正です。')
        self.cost = load_json(self.directory / 'cost-report.json')
        self.configuration = load_json(self.directory / 'runtime-manifest.json')['configuration']
        measurements = load_rows(self.directory / 'measurements.jsonl')
        transcripts = load_rows(self.directory / 'transcript.jsonl')
        histories = load_rows(self.directory / 'analysis-history.jsonl')
        asr = [row for row in measurements if row.get('stage') == 'asr']
        analyses = [row for row in measurements if row.get('stage') == 'analysis']
        if len(asr) != len(transcripts) or len(analyses) != len(histories):
            raise DemoDataError('公開時刻と原文・解析の記録数が一致しません。推定で補いません。')
        self.asr_events = []
        self.analysis_events = []
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
        self.asr_events.sort(key=lambda item: item['at'])
        self.analysis_events.sort(key=lambda item: item['at'])
        if not self.asr_events or not self.analysis_events:
            raise DemoDataError('再現できる原文・分析記録がありません。')
        self.capture_markers.sort()
        self.capture_end = max(row['chunk']['completed_at'] for row in transcripts)
        self.duration = max(self.capture_end, self.asr_events[-1]['at'], self.analysis_events[-1]['at']) - self.started_at
        self.initial_seconds = (self.analysis_events[-2]['at'] if len(self.analysis_events) > 1 else self.analysis_events[-1]['at']) - self.started_at + .001
        self.initial_seconds = min(self.duration, self.initial_seconds)

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
        landmarks.append({'seconds': self.initial_seconds, 'label': '訳と論点を見る' if self.simulation else '論点・概念が揃う時点'})
        landmarks.append({'seconds': self.duration, 'label': '最後の整理'})
        return {'schema_version': 1, 'session_id': self.session['id'], 'source_label': source_label,
                'started_at': self.started_at, 'audio_seconds': self.audio_seconds, 'duration_seconds': self.duration,
                'initial_seconds': self.initial_seconds, 'model': self.model, 'configuration': self.configuration,
                'line_count': len(self.all_lines), 'analysis_count': len(self.analysis_events),
                'cache_hits': sum(bool(event['result'].get('cache_hit')) for event in self.analysis_events),
                'recorded_api_usd': self.cost.get('confirmed_api_usd'), 'demo_additional_api_usd': 0,
                'display_simulation': bool(self.simulation), 'preparation_api_usd': self.simulation.get('preparation_api_usd'),
                'landmarks': landmarks,
                'timing_note': self.simulation.get('timing_note', '原文・和訳・分析はmeasurements.jsonlの公開時刻で表示。ブラウザ描画遅延は未計測。音声進行は記録された観測点。')}

    def snapshot(self, seconds):
        if not numeric(seconds) or not 0 <= seconds <= self.duration + .001:
            raise DemoDataError('指定時刻がデモの範囲外です。')
        seconds = min(seconds, self.duration)
        at = self.started_at + seconds
        asr = [event for event in self.asr_events if event['at'] <= at]
        analyses = [event for event in self.analysis_events if event['at'] <= at]
        lines = [deepcopy(line) for event in asr for line in event['lines']]
        translations = {}
        for event in analyses:
            for translation in event['result'].get('translations', []):
                translations[translation['source_id']] = translation['text']
        for line in lines:
            if line['id'] in translations:
                line['translation_ja'] = translations[line['id']]
        capture_seconds = max(value for observed_at, value in self.capture_markers if observed_at <= at)
        asr_through = asr[-1]['through_seconds'] if asr else 0
        active_asr = any(event['began_at'] <= at < event['at'] for event in self.asr_events)
        active_analysis = any(event['began_at'] <= at < event['at'] for event in self.analysis_events)
        asr_state = 'completed' if len(asr) == len(self.asr_events) else ('running' if active_asr else 'waiting')
        analysis_state = 'running' if active_analysis else ('completed' if analyses else 'waiting')
        result = deepcopy(analyses[-1]['result']) if analyses else None
        history = []
        for event in analyses:
            item = deepcopy(event['result']); item.pop('translations', None); history.append(item)
        session = {key: self.session[key] for key in ('id', 'started_at', 'language')}
        session.update(title='保存結果を時系列で表示', source_kind='replay')
        # Capture is deliberately not represented as a current live recording.
        return {'schema_version': 1, 'updated_at': at, 'session': session, 'processing_active': False,
                'capture': {'state': 'completed', 'audio_seconds': capture_seconds, 'last_audio_at': None, 'rms_dbfs': None, 'peak_dbfs': None, 'error': None, 'device': ''},
                'asr': {'state': asr_state, 'through_seconds': asr_through, 'queue_seconds': max(0, capture_seconds - asr_through), 'error': None, 'failed_chunks': []},
                'analysis': {'state': analysis_state, 'through_seconds': result['through_seconds'] if result else 0,
                             'generated_at': analyses[-1]['at'] if analyses else None, 'provider': 'openai', 'model': self.model, 'error': None, 'result': result},
                'lines': lines, 'analysis_history': history, 'capabilities': {'cloud_enabled': False},
                'message': '', 'demo': {'cursor_seconds': seconds, 'at': at, 'published_lines': len(lines), 'published_analyses': len(analyses),
                                       'untranslated_lines': sum(line.get('language') != 'ja' and not line.get('uncertain') and not line.get('translation_ja') for line in lines),
                                       'audio_input_ended': at >= self.capture_end, 'additional_api_usd': 0}}


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

        def send(self, status, value, kind='application/json; charset=utf-8', login=False):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            if login: self.send_header('Set-Cookie', f'lecture_demo={token}; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass

        def host_ok(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def authorized(self):
            try:
                cookie = cookies.SimpleCookie(self.headers.get('Cookie', '')).get('lecture_demo')
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

        def do_POST(self):
            if not self.host_ok() or not self.authorized(): return self.send(403, {'error': '認証が必要です。'})
            return self.send(405, {'error': '閲覧専用です。録音・認識・API送信の操作はありません。'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    return server, f'http://127.0.0.1:{server.server_port}/?token={token}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8777)
    parser.add_argument('--check', action='store_true', help='validate saved records without starting a server')
    args = parser.parse_args()
    timeline = DemoTimeline(args.session)
    if args.check:
        print(json.dumps(timeline.metadata(), ensure_ascii=False, indent=2)); return
    server, url = make_server(timeline, args.port)
    print('保存結果の表示再現・追加API $0。録音・ASR・外部API・音声再生は行いません。', flush=True)
    print(url, flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__':
    main()
