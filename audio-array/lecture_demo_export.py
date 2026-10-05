#!/usr/bin/env python3
"""Export explicitly approved lecture content as a portable, read-only demo.

This is a field allowlist, not a copy of a runtime directory. Exporting does not
grant permission to publish the selected source. Review the content and its
license first. No capture, inference, cloud client or ledger is imported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from lecture_demo import DemoDataError, DemoTimeline, load_json, load_rows, numeric


# Every nested object has its own allowlist. Unknown keys never survive export;
# primitive type checks prevent a new nested object hiding inside a scalar field.
NUMBER = (int, float)
OPTIONAL_NUMBER = (int, float, type(None))
OPTIONAL_TEXT = (str, type(None))
TEXTS = [str]
LINE = {
    'id': str, 'text': str, 'language': str, 'start_seconds': NUMBER,
    'end_seconds': NUMBER, 'segment': int, 'chunk': int, 'uncertain': bool,
    'doubt_reasons': TEXTS, 'boundary_context': str,
}
SOURCE_ITEM = {'text': str, 'source_ids': TEXTS}
CONCEPT = {'term': str, 'explanation': str, 'basis': str, 'source_ids': TEXTS}
ERROR = {'category': str, 'retryable': bool, 'http_status': (int, type(None)),
         'provider_code': OPTIONAL_TEXT, 'retry_after_seconds': OPTIONAL_NUMBER}
EXCLUSION = {'source_id': str, 'reason': str, 'duplicate_of': OPTIONAL_TEXT}
BLOCK = {'id': str, 'text': str, 'source_ids': TEXTS, 'uncertain_source_ids': TEXTS,
         'start_seconds': NUMBER, 'end_seconds': NUMBER, 'generated_at': 'time',
         'published_at': 'time', 'boundary_reason': str,
         'uncertainty_reasons': ('mapping', TEXTS)}
USAGE = {'input_tokens': int, 'output_tokens': int, 'cached_tokens': int, 'cache_write_tokens': int}
RESULT = {
    'headline': SOURCE_ITEM, 'summary': [SOURCE_ITEM], 'flow': [SOURCE_ITEM],
    'questions': [SOURCE_ITEM], 'concepts': [CONCEPT],
    'translations': [{'source_id': str, 'text': str}], 'block_translations': [BLOCK],
    'through_seconds': NUMBER, 'generated_at': 'time', 'generation_seconds': NUMBER,
    'source_line_ids': TEXTS, 'uncertain_source_ids': TEXTS,
    'source_ranges': [{'source_id': str, 'start_seconds': NUMBER, 'end_seconds': NUMBER}],
    'provider': str, 'model': str, 'semantic_quality_verified': bool,
    'reference_context_used': bool, 'evidence_scope': str,
    'usage': USAGE, 'cost_usd': NUMBER, 'usage_confirmed': bool, 'cache_hit': bool,
    'cached_request_cost_usd': NUMBER,
}
PREVIEW = {'revision': (int, type(None)), 'window_start_seconds': NUMBER,
           'through_seconds': NUMBER, 'ready_at': 'time', 'started_at': 'time',
           'published_at': 'time', 'processing_seconds': NUMBER, 'lines': [LINE]}
STAGE = {'state': str, 'error': 'error_text', 'through_seconds': NUMBER,
         'schedule': {'error': ERROR}}
STATE = {
    'schema_version': int,
    'session': {'id': str, 'title': str, 'started_at': 'time', 'language': str},
    'capture': {'state': str, 'audio_seconds': NUMBER, 'error': 'error_text'},
    'asr': {**STAGE, 'failed_chunks': [{'index': int, 'error': 'error_text'}]},
    'analysis': {**STAGE, 'provider': str, 'model': str},
    'translation': {**STAGE, 'enabled': bool, 'blocks': [BLOCK], 'source_policy_version': int},
    'provisional_asr': {**PREVIEW, 'enabled': bool, 'state': str,
                        'refresh_seconds': NUMBER, 'window_seconds': NUMBER, 'error': 'error_text'},
    'lines': [LINE], 'processing_stop_requested': bool,
}
CONFIGURATION = {
    'language': str, 'provider': str, 'model': str, 'chunk_seconds': NUMBER,
    'analysis_interval': NUMBER, 'translation_interval': NUMBER,
    'provisional_refresh_seconds': NUMBER, 'provisional_window_seconds': NUMBER,
    'continuous_translation': bool, 'initial_translation_first': bool,
    'parallel_cloud_stages': bool, 'translation_source_policy_version': int,
    'translation_max_wait_seconds': NUMBER, 'translation_lookahead_seconds': NUMBER,
    'replay': bool, 'pace': NUMBER,
}
MEASUREMENT = {
    'stage': str, 'published_at': 'time', 'started_at': 'time', 'ready_at': 'time',
    'processing_seconds': NUMBER, 'capture_seconds': NUMBER, 'through_seconds': NUMBER,
    'target_source_ids': TEXTS, 'target_through_seconds': NUMBER,
    'browser_render_measured': bool,
}
GENERATION_EVENT = {'event': str, 'at': 'time', 'stage': str, 'error': ERROR,
                    'auto_attempts': int, 'retry_delay_seconds': NUMBER, 'exhausted': bool}
PREVIEW_EVENT = {'event': str, 'at': 'time', 'revision': int, 'window_start_seconds': NUMBER,
                 'through_seconds': NUMBER, 'error': 'error_text'}
COST = {'provider': str, 'additional_api_usd': OPTIONAL_NUMBER,
        'successful_generation_api_usd': NUMBER, 'confirmed_api_usd': NUMBER,
        'retained_reservation_usd': NUMBER, 'matching_request_count': int}


def project(value, schema, origin, location='record'):
    """Keep only documented playback fields and rebase absolute event times."""
    if schema == 'time':
        if value is None:
            return None
        if not numeric(value):
            raise DemoDataError(f'{location}: invalid timestamp')
        return value - origin
    if schema == 'error_text':
        if value is None:
            return None
        if not isinstance(value, str):
            raise DemoDataError(f'{location}: invalid error')
        # Runtime exception strings may include paths or request bodies. Keep the
        # failure and typed diagnostics, but never publish arbitrary exceptions.
        return '記録された処理に失敗があります（詳細な実行環境情報は公開対象外）。'
    if isinstance(schema, dict):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise DemoDataError(f'{location}: expected object')
        return {key: project(value[key], child, origin, f'{location}.{key}')
                for key, child in schema.items() if key in value}
    if isinstance(schema, list):
        if not isinstance(value, list):
            raise DemoDataError(f'{location}: expected array')
        return [project(item, schema[0], origin, location + '[]') for item in value]
    if isinstance(schema, tuple) and schema[0] == 'mapping':
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise DemoDataError(f'{location}: expected source-ID mapping')
        return {key: project(item, schema[1], origin, location + '[]') for key, item in value.items()}
    allowed = schema if isinstance(schema, tuple) else (schema,)
    if type(value) not in allowed or (type(value) in NUMBER and not numeric(value)):
        raise DemoDataError(f'{location}: invalid scalar type')
    return value


def export_demo(session, audio_file, output):
    """Create a new package atomically; never overwrite an existing destination."""
    session, audio_file, output = Path(session), Path(audio_file), Path(output)
    if output.exists():
        raise DemoDataError('Export destination already exists; review it before replacing it.')
    original = DemoTimeline(session, audio_file=audio_file)
    if original.simulation:
        raise DemoDataError('A recorded audio demo is required.')
    origin = original.started_at
    state = project(load_json(session / 'state.json'), STATE, origin)
    configuration = project(original.configuration, CONFIGURATION, origin)
    files = {
        'state.json': state,
        'runtime-manifest.json': {'configuration': configuration},
        'cost-report.json': project(original.cost, COST, origin),
    }
    schemas = {
        'transcript.jsonl': {'chunk': {'index': int, 'start_seconds': NUMBER,
                                      'end_seconds': NUMBER, 'completed_at': 'time'}, 'lines': [LINE]},
        'measurements.jsonl': MEASUREMENT,
        'analysis-history.jsonl': RESULT,
        'translation-history.jsonl': {
            'started_at': 'time', 'published_at': 'time', 'blocks': [BLOCK],
            'selection': {'source_policy_version': int, 'excluded_sources': [EXCLUSION]},
            'cache_hit': bool, 'cost_usd': NUMBER, 'usage_confirmed': bool,
        },
        'provisional-history.jsonl': PREVIEW,
        'generation-events.jsonl': GENERATION_EVENT,
        'provisional-events.jsonl': PREVIEW_EVENT,
    }
    rows = {}
    for name, schema in schemas.items():
        if (session / name).is_file():
            source = load_rows(session / name)
            if name == 'measurements.jsonl':
                source = [row for row in source if row.get('stage') in {'asr', 'analysis', 'translation'}]
            if name == 'generation-events.jsonl':
                source = [row for row in source if row.get('event') == 'failed'
                          and row.get('stage') in {'analysis', 'translation'}]
            rows[name] = [project(row, schema, origin) for row in source]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.demo-export-', dir=output.parent) as temporary:
        package = Path(temporary) / 'demo'
        target = package / 'session'
        target.mkdir(parents=True)
        for name, value in files.items():
            (target / name).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
        for name, records in rows.items():
            (target / name).write_text(''.join(json.dumps(row, ensure_ascii=False, allow_nan=False,
                separators=(',', ':')) + '\n' for row in records), encoding='utf-8')
        (package / 'audio.wav').write_bytes(original.audio.data)
        exported = DemoTimeline(target, audio_file=package / 'audio.wav')
        if (exported.duration != original.duration or len(exported.all_lines) != len(original.all_lines)
                or len(exported.failures) != len(original.failures)):
            raise DemoDataError('Export changed recorded timing, source coverage or failure evidence.')
        manifest = {'schema_version': 1, 'clock': 'seconds relative to the recorded session start',
                    'audio_seconds': exported.audio_seconds, 'duration_seconds': exported.duration,
                    'line_count': len(exported.all_lines), 'translation_count': len(exported.translation_events),
                    'analysis_count': len(exported.analysis_events), 'provisional_count': len(exported.provisional_events),
                    'generation_failure_count': len(exported.failures),
                    'files': {str(path.relative_to(package)): {'bytes': path.stat().st_size,
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                        for path in sorted(package.rglob('*')) if path.is_file()}}
        (package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        package.rename(output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--audio-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_demo(args.session, args.audio_file, args.output), indent=2))


if __name__ == '__main__':
    main()
