#!/usr/bin/env python3
"""Explicit, offline local-ASR experiment; no capture or cloud requests.

Recognition is private. Publication times are a simulation from measured calls,
not an observation of the live application, browser, or comprehension quality.
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
import stat
import sys
import time
import uuid
import wave


RATE = 16000
REPO = Path(__file__).resolve().parents[1]
MODEL_METADATA = REPO / 'data/event-audio/model-cache/whisper-turbo.json'


class ExperimentError(ValueError):
    pass


def _identity(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def inspect_audio(path, seconds=None):
    """Validate and hash the complete stable WAV before selecting initial frames."""
    path = Path(path).expanduser().resolve(strict=True)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ExperimentError('Input must be a regular WAV file.')
    if seconds is not None and (not math.isfinite(seconds) or seconds <= 0):
        raise ExperimentError('--seconds must be finite and positive.')
    with path.open('rb') as source:
        before = os.fstat(source.fileno())
        try:
            with wave.open(source, 'rb') as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, RATE, 'NONE'):
                    raise ExperimentError('Use an uncompressed 16 kHz mono PCM16 WAV; automatic conversion is not performed.')
                frames = audio.getnframes()
                if frames <= 0:
                    raise ExperimentError('The WAV contains no audio frames.')
                observed_bytes = 0
                while block := audio.readframes(65536):
                    observed_bytes += len(block)
                if observed_bytes != frames * 2:
                    raise ExperimentError('The WAV is truncated or has incomplete PCM frames.')
        except (wave.Error, EOFError) as exc:
            raise ExperimentError('Cannot read this WAV; use uncompressed 16 kHz mono PCM16 audio.') from exc
        source.seek(0)
        digest = hashlib.sha256()
        while block := source.read(1024 * 1024):
            digest.update(block)
        if _identity(before) != _identity(os.fstat(source.fileno())) or _identity(before) != _identity(path.stat()):
            raise ExperimentError('Input changed during inspection; keep it unchanged and check again.')
    selected_frames = frames if seconds is None else min(frames, int(Decimal(str(seconds)) * RATE))
    if selected_frames <= 0:
        raise ExperimentError('--seconds must select at least one audio frame.')
    return {'path': str(path), 'sha256': digest.hexdigest(), 'file_bytes': before.st_size,
            'sample_rate': RATE, 'channels': 1, 'sample_width_bytes': 2,
            'frames': frames, 'duration_seconds': frames / RATE,
            'selected_frames': selected_frames, 'selected_seconds': selected_frames / RATE}


def _save(path, value):
    """Replace a manifest only after a complete, synced JSON write succeeds."""
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


def frame_ranges(frames, chunk_seconds):
    """Partition every selected frame, retaining a possibly short final chunk."""
    if type(chunk_seconds) is not int or not 2 <= chunk_seconds <= 30:
        raise ExperimentError('Chunk seconds must be integers from 2 through 30.')
    width = RATE * chunk_seconds
    return [(start, min(start + width, frames)) for start in range(0, frames, width)]


def planned_calls(frames, step_seconds, window_seconds=None):
    """Rolling snapshots contain only available audio; coverage counts new frames."""
    if window_seconds is not None and (type(window_seconds) is not int or
            not step_seconds <= window_seconds <= 30):
        raise ExperimentError('Window seconds must be an integer from the step duration through 30.')
    return [{'index': i, 'start_frame': start if window_seconds is None else max(0, end - window_seconds * RATE),
             'end_frame': end, 'new_audio_start_frame': start, 'new_audio_end_frame': end, 'status': 'pending'}
            for i, (start, end) in enumerate(frame_ranges(frames, step_seconds))]


def simulate_publication(calls):
    """Causal single-worker schedule; external lock wait is excluded.

    Audio becomes available at each chunk end. Service includes measured call
    overhead and decoding, minus observed external inference-lock contention.
    Waiting is averaged uniformly over audio duration, not over unequal chunks.
    Failed/pending calls have no publication and leave coverage incomplete.
    """
    available = weighted_wait = covered = 0.0
    events = []
    for call in calls:
        if call['status'] != 'completed':
            break
        start = call.get('new_audio_start_frame', call['start_frame']) / RATE
        end = call.get('new_audio_end_frame', call['end_frame']) / RATE
        service = call['service_seconds']
        if not math.isfinite(service) or service < 0:
            raise ExperimentError('Service time must be finite and nonnegative.')
        begin = max(end, available)
        available = begin + service
        duration = end - start
        mean_wait = available - (start + end) / 2
        weighted_wait += duration * mean_wait
        covered += duration
        events.append({'index': call['index'], 'audio_end_seconds': end,
            'input_window_start_seconds': call['start_frame'] / RATE,
            'new_audio_start_seconds': start,
            'estimated_start_seconds': begin, 'estimated_publication_seconds': available,
            'estimated_backlog_seconds': begin - end,
            'estimated_mean_audio_wait_seconds': mean_wait,
            'estimated_max_audio_wait_seconds': available - start})
    return {'kind': 'estimated_offline_single_worker_schedule',
        'includes': 'Chunk buffering, measured call service, and simulated backlog.',
        'excludes': 'Warmup, external lock contention, capture, live scheduling, and browser rendering.',
        'complete': len(events) == len(calls), 'covered_audio_seconds': covered,
        'duration_weighted_mean_audio_wait_seconds': weighted_wait / covered if covered else None,
        'max_audio_wait_seconds': max((event['estimated_max_audio_wait_seconds'] for event in events), default=None),
        'final_publication_seconds': available if events else None, 'events': events}


def _write_chunk(source, destination, start, end):
    with wave.open(str(source), 'rb') as audio:
        audio.setpos(start)
        pcm = audio.readframes(end - start)
    if len(pcm) != (end - start) * 2:
        raise ExperimentError('Source audio changed or became truncated.')
    with wave.open(str(destination), 'wb') as audio:
        audio.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        audio.writeframes(pcm)


def _model_identity(metadata_path):
    path = Path(metadata_path).expanduser().resolve(strict=True)
    encoded = path.read_bytes()
    metadata = json.loads(encoded)
    model = Path(metadata['local_path']).expanduser().resolve(strict=True)
    weights = model / 'weights.safetensors'
    if not weights.is_file():
        weights = model / 'weights.npz'
    if not weights.is_file() or not (model / 'config.json').is_file():
        raise ExperimentError('Existing local model weights and config are required; no download is performed.')
    return {'metadata_path': str(path), 'metadata_sha256': hashlib.sha256(encoded).hexdigest(),
        'metadata': metadata, 'model_path': str(model), 'weights_bytes': weights.stat().st_size}


def execute(audio, metadata, output, *, seconds=None, chunk_seconds=(3, 5, 10, 15, 30),
            repeats=1, window_seconds=None, transcriber=None, clock=time.monotonic):
    source = inspect_audio(audio, seconds)
    if source['selected_seconds'] > 360:
        raise ExperimentError('Select at most 360 seconds with --seconds.')
    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ExperimentError('Repeats must be from 1 through 10.')
    if not chunk_seconds or len(set(chunk_seconds)) != len(chunk_seconds):
        raise ExperimentError('Supply distinct chunk durations.')
    planned = {size: planned_calls(source['selected_frames'], size, window_seconds) for size in chunk_seconds}
    identity = _model_identity(metadata)
    root = (REPO / 'results' / 'audio-experiments').resolve()
    if not root.is_relative_to(REPO.resolve() / 'results'):
        raise ExperimentError('Private experiment root must remain in repository results/.')
    output = Path(output).expanduser().resolve()
    if output == root or not output.is_relative_to(root):
        raise ExperimentError('Output must be a NEW directory under results/audio-experiments/.')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700, exist_ok=False)
    manifest_path = output / 'manifest.json'
    runs = []
    for repeat in range(repeats):
        # Reverse alternate repeats to expose some warm/cache/order sensitivity.
        order = list(chunk_seconds) if repeat % 2 == 0 else list(reversed(chunk_seconds))
        for size in order:
            runs.append({'repeat': repeat + 1, 'chunk_seconds': size, 'status': 'pending',
                'mode': 'rolling_provisional_snapshots' if window_seconds is not None else 'independent_chunks',
                'window_seconds': window_seconds, 'calls': [dict(call) for call in planned[size]]})
    manifest = {'schema_version': 1, 'status': 'running',
        'probe_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'started_at': datetime.now(timezone.utc).isoformat(), 'input': source, 'model': identity,
        'configuration': {'language': 'en', 'temperature': 0, 'condition_on_previous_text': False,
            'initial_prompt': None, 'chunk_seconds': list(chunk_seconds), 'repeats': repeats,
            'window_seconds': window_seconds,
            'output_semantics': ('Overlapping provisional recognition snapshots. Do not concatenate; no revision reconciliation or finalization.'
                                 if window_seconds is not None else 'Independent non-overlapping chunks.'),
            'order': 'Requested order on odd repeats; reverse order on even repeats.'},
        'warmup': {'status': 'pending', 'start_frame': 0, 'end_frame': min(source['selected_frames'], 15 * RATE)},
        'runs': runs,
        'measurement_limits': ['Offline saved-audio calls, not live speech-to-display latency.',
            'Estimated waiting averages every audio instant uniformly, including silence.',
            'No semantic reference or recognition-accuracy claim is supplied by this tool.',
            'service_seconds = call_wall_seconds - inference_queue_wait_seconds; file I/O is included.',
            'Warmup includes model initialization and is excluded from measured schedules.'],
        'cost': {'measured_api_usd': 0, 'development_assistant_usage': 'unmeasured', 'electricity': 'unmeasured'}}
    _save(manifest_path, manifest)
    def recognize(call, name):
        wav_path, result_path = output / (name + '.wav'), output / (name + '.json')
        call.update(status='running', result_path=result_path.name)
        _save(manifest_path, manifest)
        started = clock()
        try:
            _write_chunk(source['path'], wav_path, call['start_frame'], call['end_frame'])
            report = transcriber({'path': str(wav_path)}, result_path, 'en')
            wall = clock() - started
            queue = float(report['inference_queue_wait_seconds'])
            decode = float(report['inference_seconds'])
            if any(not math.isfinite(value) or value < 0 for value in (wall, queue, decode)) or queue > wall:
                raise ExperimentError('Invalid measured inference timing.')
            call.update(status='completed', call_wall_seconds=wall,
                inference_queue_wait_seconds=queue, inference_seconds=decode,
                service_seconds=wall - queue)
            if report.get('model_file_sha256'):
                identity['model_file_sha256'] = report['model_file_sha256']
        except BaseException as exc:
            call.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                call_wall_seconds=clock() - started, error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            _save(manifest_path, manifest)

    started = clock()
    try:
        if transcriber is None:
            from lecture_live import LocalTranscriber
            transcriber = LocalTranscriber(identity['metadata_path'])
        recognize(manifest['warmup'], 'warmup')
        for run in runs:
            run['status'] = 'running'
            run_started = clock()
            try:
                for call in run['calls']:
                    recognize(call, f"repeat-{run['repeat']}-chunk-{run['chunk_seconds']}-{call['index']:04d}")
                run['status'] = 'completed'
            except Exception:
                run['status'] = 'failed'
            except BaseException:
                run['status'] = 'interrupted'
                raise
            finally:
                run['wall_seconds'] = clock() - run_started
                run['schedule'] = simulate_publication(run['calls'])
                _save(manifest_path, manifest)
        # Check all original bytes again before declaring the experiment complete.
        current = inspect_audio(source['path'], seconds)
        if current != source:
            raise ExperimentError('Source changed during the experiment; results are unverified.')
        manifest['status'] = 'completed' if all(run['status'] == 'completed' for run in runs) else 'incomplete'
    except BaseException as exc:
        manifest.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'incomplete',
                        error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        manifest['wall_seconds'] = clock() - started
        manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
        _save(manifest_path, manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    parser.add_argument('--model-metadata', type=Path, default=MODEL_METADATA)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, help='Select initial audio, at most 360 seconds.')
    parser.add_argument('--chunk-seconds', nargs='+', type=int, default=[3, 5, 10, 15, 30])
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--window-seconds', type=int,
                        help='Causal trailing window; chunk-seconds becomes the update step. Snapshots are provisional.')
    args = parser.parse_args(argv)
    try:
        result = execute(args.audio, args.model_metadata, args.output, seconds=args.seconds,
                         chunk_seconds=args.chunk_seconds, repeats=args.repeats, window_seconds=args.window_seconds)
        print(json.dumps({'status': result['status'], 'manifest': str(args.output / 'manifest.json')}))
        return 0 if result['status'] == 'completed' else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'ASR probe failed: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('ASR probe interrupted; completed, failed, and pending calls remain in the manifest.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
