#!/usr/bin/env python3
"""Import local audio/video and explicitly run the existing lecture pipeline.

Check may decode into temporary files, removed on exit, but never starts
recognition, network activity or persistent writes. Run creates a new private
experiment and retains its normalized audio without changing the original.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import uuid

from lecture_media import inspect_pcm_audio as inspect_audio, prepared_audio, source_identity


REPO = Path(__file__).resolve().parents[1]
APP_SCRIPT = Path(__file__).with_name('lecture_live.py')
MODEL_METADATA = REPO / 'data/event-audio/model-cache/whisper-turbo.json'
HISTORICAL_USD_PER_HOUR = Decimal('1.26175')


class ExperimentError(ValueError):
    pass


def _import_path(sha256):
    root = (REPO / 'data/audio-imports').resolve()
    if not root.is_relative_to(REPO.resolve() / 'data'):
        raise ExperimentError('data/audio-imports must remain inside the ignored repository directory.')
    target = root / sha256 / 'audio.wav'
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ExperimentError('Prepared audio must not redirect outside its private import directory.')
    return target.resolve()


def _planned_audio(path, seconds):
    with prepared_audio(path, seconds) as prepared:
        audio = dict(prepared['input'])
        conversion = dict(prepared['conversion'])
        if conversion['required']:
            audio['path'] = str(_import_path(audio['sha256']))
        return dict(prepared['source']), audio, conversion


def build_plan(args):
    if args.cloud and args.authorization is None:
        raise ExperimentError('--cloud requires --authorization for this replay source and API budget.')
    if not args.cloud and (args.authorization is not None or args.key_file is not None or args.model is not None):
        raise ExperimentError('--authorization, --key-file and --model require --cloud.')
    if args.label and (len(args.label) > 80 or any(ord(char) < 32 for char in args.label)):
        raise ExperimentError('--label must be at most 80 characters without control characters.')
    source, audio, conversion = _planned_audio(args.audio, args.seconds)
    historical = HISTORICAL_USD_PER_HOUR * Decimal(str(audio['selected_seconds'])) / 3600
    return {'schema_version': 1, 'source': source, 'input': audio, 'conversion': conversion, 'label': args.label,
            'configuration': {'provider': 'openai' if args.cloud else 'off',
                'model': (args.model or 'gpt-6.1-sol') if args.cloud else None,
                'language': args.language, 'pace': args.pace, 'chunk_seconds': args.chunk_seconds,
                'translation_interval_seconds': args.translation_interval,
                'analysis_interval_seconds': args.analysis_interval,
                'model_metadata': str(args.model_metadata.expanduser().resolve())},
            'cost': {'planned_api_usd': None if args.cloud else 0,
                'historical_sol_example_usd': float(historical.quantize(Decimal('.000001'))),
                'historical_usd_per_hour': float(HISTORICAL_USD_PER_HOUR),
                'source': 'docs/experiments/development-handoff.md#partial-live-cost-observation',
                'conditions': 'Historical Sol live translation/analysis at 60/120 seconds; not current pricing, a quote, or a budget guarantee. Accelerated replay and changed models/schedules can differ.',
                'exclusions': 'No extra allowance for startup/final work, failures, reservations, or other budget users; development-assistant usage and electricity unmeasured.'},
            'cloud_authorization': str(args.authorization.expanduser().resolve()) if args.cloud else None,
            'key_file': str(args.key_file.expanduser().resolve()) if args.key_file else None,
            'measurement_limits': ['Fresh local ASR, not reuse of saved recognition.',
                'A saved-file run does not test microphone capture or speech-to-browser latency.',
                'Completion and valid source references do not establish semantic accuracy.']}


def _preflight(plan):
    """Check local files and existing permissions without reserving or sending."""
    if sys.version_info < (3, 12):
        raise ExperimentError('Run requires Python 3.12 or later; use the repository environment.')
    metadata_path = Path(plan['configuration']['model_metadata'])
    try:
        metadata = json.loads(metadata_path.read_text())
        model_path = Path(metadata['local_path'])
        if not (model_path / 'config.json').is_file() or not any(
                (model_path / name).is_file() for name in ('weights.npz', 'weights.safetensors')):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise ExperimentError('Local ASR model is not prepared. Run setup.command separately before this experiment.') from None
    if plan['configuration']['provider'] != 'openai':
        return
    from lecture_cloud_scope import CloudScope
    import event_insights_cloud as cloud
    scope = CloudScope(plan['cloud_authorization'])
    authorized = scope._read_authorization()
    if authorized['replay_sources'].get(plan['input']['path']) != plan['input']['sha256']:
        raise ExperimentError('Authorization must list this exact input path and SHA256 in replay_sources.')
    status = scope.status()
    if not status['send_authorized_today'] or plan['configuration']['model'] not in status['allowed_models']:
        raise ExperimentError('Today or the selected model is outside the existing replay authorization.')
    previous_auth, previous_key = cloud.BUDGET_AUTHORIZATION_PATH, cloud.DOTENV_PATH
    previous_env = os.environ.get('OPENAI_API_KEY')
    try:
        cloud.configure_budget_authorization(plan['cloud_authorization'])
        if plan['key_file']:
            os.environ.pop('OPENAI_API_KEY', None)
            cloud.DOTENV_PATH = Path(plan['key_file'])
        if not cloud.has_api_key():
            raise ExperimentError('Supply OPENAI_API_KEY or --key-file; no key contents will be printed.')
        budget = cloud.budget_status()
        if budget['spent_usd'] >= budget['budget_usd']:
            raise ExperimentError('No shared API budget remains; existing charges and reservations are preserved.')
    finally:
        cloud.BUDGET_AUTHORIZATION_PATH, cloud.DOTENV_PATH = previous_auth, previous_key
        if previous_env is not None:
            os.environ['OPENAI_API_KEY'] = previous_env
        else:
            os.environ.pop('OPENAI_API_KEY', None)


def _private_root(kind):
    root = (REPO / kind / 'audio-experiments').resolve()
    if not root.is_relative_to(REPO.resolve() / kind):
        raise ExperimentError(f'{kind}/audio-experiments must remain inside the ignored repository directory.')
    return root


def _save(path, value):
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _materialize_input(plan):
    if source_identity(plan['source']['path']) != plan['source']:
        raise ExperimentError('Original input changed after the check; check it again.')
    if not plan['conversion']['required']:
        if inspect_audio(plan['input']['path']) != plan['input']:
            raise ExperimentError('Input changed after the check; check it again.')
        return
    target = _import_path(plan['input']['sha256'])
    if str(target) != plan['input']['path']:
        raise ExperimentError('Prepared input location changed after the check.')
    with prepared_audio(plan['source']['path'], plan['conversion']['requested_seconds']) as prepared:
        actual = {**prepared['input'], 'path': str(target)}
        if prepared['source'] != plan['source'] or actual != plan['input']:
            raise ExperimentError('Converted input changed after the check; check it again.')
        if target.exists():
            if inspect_audio(target) != plan['input']:
                raise ExperimentError('Existing prepared audio has changed; it will not be overwritten.')
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if _import_path(plan['input']['sha256']) != target:
            raise ExperimentError('Prepared input directory changed during import.')
        temporary = target.with_name('.audio.' + uuid.uuid4().hex + '.wav')
        try:
            with temporary.open('xb') as destination, Path(prepared['input']['path']).open('rb') as source:
                os.chmod(temporary, 0o600)
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            if {**inspect_audio(temporary), 'path': str(target)} != plan['input']:
                raise ExperimentError('Prepared audio copy could not be verified.')
            try:
                os.link(temporary, target)  # Atomic publication without overwriting another importer.
            except FileExistsError:
                pass
            if target.is_symlink() or inspect_audio(target) != plan['input']:
                raise ExperimentError('Existing prepared audio does not match this input.')
        finally:
            temporary.unlink(missing_ok=True)


def command_for(plan, data_root, results_root):
    config = plan['configuration']
    command = [sys.executable, str(APP_SCRIPT), '--port', '0', '--exit-after-replay',
        '--replay', plan['input']['path'], '--duration', str(plan['input']['selected_seconds']),
        '--pace', '1' if config['pace'] == 'realtime' else '0', '--provider', config['provider'],
        '--language', config['language'], '--chunk-seconds', str(config['chunk_seconds']),
        '--model-metadata', config['model_metadata'], '--data-root', str(data_root),
        '--results-root', str(results_root), '--continuous-translation',
        '--translation-interval', str(config['translation_interval_seconds']),
        '--analysis-interval', str(config['analysis_interval_seconds'])]
    if config['provider'] == 'openai':
        command += ['--allow-cloud', '--cloud-authorization', plan['cloud_authorization'], '--model', config['model']]
        if plan['key_file']:
            command += ['--key-file', plan['key_file']]
    return command


def _run_child(command, log_path, on_started):
    """Forward interruption to the isolated replay and retain unconfirmed exits."""
    interrupted = False
    with log_path.open('xb') as log:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        def stop(_signal, _frame):
            nonlocal interrupted
            interrupted = True
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            on_started(child.pid)
            deadline = None
            while True:
                try:
                    return {'returncode': child.wait(timeout=.25), 'interrupted': interrupted,
                            'shutdown_confirmed': True}
                except subprocess.TimeoutExpired:
                    if interrupted:
                        deadline = deadline or time.monotonic() + 140
                        if time.monotonic() >= deadline:
                            return {'returncode': None, 'interrupted': True, 'shutdown_confirmed': False}
        except BaseException:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
                try:
                    child.wait(timeout=140)
                except subprocess.TimeoutExpired:
                    pass  # The manifest must remain incomplete; never claim a stopped child.
            raise
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def _read_outcome(results_root, cloud, expected_frames):
    states = list(results_root.glob('Lecture-*/state.json'))
    if len(states) != 1:
        return {'completion_confirmed': False, 'stages': {}, 'cost_report': None}
    state = json.loads(states[0].read_text())
    if not isinstance(state, dict) or any(not isinstance(state.get(name), dict)
            for name in ('capture', 'asr', 'translation', 'analysis')):
        raise ExperimentError('The saved stage states could not be verified.')
    stages = {name: {key: state.get(name, {}).get(key) for key in
              ('state', 'completion_confirmed', 'through_seconds', 'audio_seconds', 'pending_lines')}
              for name in ('capture', 'asr', 'translation', 'analysis')}
    complete = not state.get('processing_active', True)
    required = ('capture', 'asr', 'translation', 'analysis') if cloud else ('capture', 'asr')
    complete = complete and all(stages[name]['state'] == 'completed'
        and stages[name]['completion_confirmed'] is not False for name in required)
    audio_seconds = stages['capture']['audio_seconds']
    complete = (complete and type(audio_seconds) in (int, float) and math.isfinite(audio_seconds)
                and round(audio_seconds * 16000) == expected_frames)
    if cloud:
        complete = complete and stages['translation']['pending_lines'] == 0
    cost_path = states[0].with_name('cost-report.json')
    cost = json.loads(cost_path.read_text()) if cost_path.is_file() else None
    if cost is not None and not isinstance(cost, dict):
        raise ExperimentError('The saved cost report could not be verified.')
    return {'completion_confirmed': bool(complete), 'stages': stages,
            'state_path': str(states[0]), 'cost_report_path': str(cost_path) if cost is not None else None,
            'cost_report': None if cost is None else {key: cost.get(key) for key in
                ('additional_api_usd', 'confirmed_api_usd', 'retained_reservation_usd', 'codex_usage', 'electricity')}}


def execute(plan):
    _preflight(plan)
    _materialize_input(plan)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:10]
    data_root, results_root = (_private_root(kind) / run_id for kind in ('data', 'results'))
    data_root.mkdir(parents=True, exist_ok=False)
    results_root.mkdir(parents=True, exist_ok=False)
    manifest_path = results_root / 'experiment.json'
    manifest = {**plan, 'run_id': run_id, 'status': 'starting', 'started_at': time.time(),
                'data_root': str(data_root), 'results_root': str(results_root), 'completion_confirmed': False,
                'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'media_preparation_sha256': hashlib.sha256(Path(__file__).with_name('lecture_media.py').read_bytes()).hexdigest()}
    _save(manifest_path, manifest)
    started = time.monotonic()
    def on_started(pid):
        manifest.update(status='running', process_id=pid)
        _save(manifest_path, manifest)
    try:
        child = _run_child(command_for(plan, data_root, results_root), results_root / 'process.log', on_started)
        outcome = _read_outcome(results_root, plan['configuration']['provider'] == 'openai',
                                plan['input']['selected_frames'])
        try:
            unchanged = inspect_audio(plan['input']['path']) == plan['input']
        except (OSError, ValueError):
            unchanged = False
        try:
            source_unchanged = source_identity(plan['source']['path']) == plan['source']
        except (OSError, ValueError):
            source_unchanged = False
        complete = (child['returncode'] == 0 and child['shutdown_confirmed'] and not child['interrupted']
                    and outcome['completion_confirmed'] and unchanged and source_unchanged)
        manifest.update(outcome, child=child, source_unchanged=source_unchanged, input_unchanged=unchanged, completion_confirmed=complete,
                        status='completed' if complete else 'incomplete')
    except (OSError, ValueError) as exc:
        manifest.update(status='incomplete', completion_confirmed=False, error=str(exc))
    finally:
        manifest.update(finished_at=time.time(), elapsed_seconds=time.monotonic() - started)
        _save(manifest_path, manifest)
    return manifest_path, manifest


def _positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='action', required=True)
    for action in ('check', 'run'):
        sub = subparsers.add_parser(action, help='Check local media without persistent writes' if action == 'check' else 'Start a new explicit recognition experiment')
        sub.add_argument('audio', type=Path)
        sub.add_argument('--seconds', type=float, help='Use only this prefix (whole file by default)')
        sub.add_argument('--label', default='')
        sub.add_argument('--language', choices=('en', 'ja', 'auto'), default='en')
        sub.add_argument('--pace', choices=('realtime', 'accelerated'), default='realtime')
        sub.add_argument('--chunk-seconds', type=int, choices=range(5, 31), default=15)
        sub.add_argument('--translation-interval', type=_positive_int, default=60)
        sub.add_argument('--analysis-interval', type=_positive_int, default=120)
        sub.add_argument('--model-metadata', type=Path, default=MODEL_METADATA)
        sub.add_argument('--cloud', action='store_true', help='Opt into authorized cloud text translation and analysis')
        sub.add_argument('--model', choices=('gpt-6-luna', 'gpt-6.1-sol'))
        sub.add_argument('--authorization', type=Path)
        sub.add_argument('--key-file', type=Path)
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args)
        if args.action == 'check':
            print(json.dumps({'check_only': True, 'runtime_and_authorization_checked': False, **plan}, ensure_ascii=False, indent=2))
            return 0
        manifest_path, result = execute(plan)
        print(json.dumps({'manifest': str(manifest_path), 'status': result['status'],
                          'completion_confirmed': result['completion_confirmed'],
                          'stages': result.get('stages', {}), 'cost_report': result.get('cost_report'),
                          'error': result.get('error')}, ensure_ascii=False, indent=2))
        return 0 if result['completion_confirmed'] else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Experiment not started: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
