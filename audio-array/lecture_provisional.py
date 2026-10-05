"""Causal audio windows for revisable display, never canonical source evidence."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import wave

RATE = 16000


def validate_settings(refresh_seconds, window_seconds):
    if (type(refresh_seconds) not in (int, float) or not math.isfinite(refresh_seconds)
            or not 0 <= refresh_seconds <= 30 or (refresh_seconds and refresh_seconds < .1)
            or type(window_seconds) not in (int, float) or not math.isfinite(window_seconds)
            or not max(refresh_seconds, .1) <= window_seconds <= 30):
        raise ValueError('速報の更新間隔と音声窓が不正です。')


def pending_window(available_seconds, previous_end_frame, refresh_seconds, window_seconds, *, final=False):
    """Return only the latest arrived boundary; do not build an old-window queue."""
    if not refresh_seconds or not math.isfinite(available_seconds) or available_seconds <= 0:
        return None
    frames = round(available_seconds * RATE)
    step = round(refresh_seconds * RATE)
    end = frames if final else frames // step * step
    if end <= previous_end_frame:
        return None
    start = max(0, end - round(window_seconds * RATE))
    return {'start_frame': start, 'end_frame': end,
            'window_start_seconds': start / RATE, 'through_seconds': end / RATE}


def write_window(raw_path, destination, window):
    """Copy exactly an already-confirmed PCM interval, without reading future bytes."""
    start, end = window['start_frame'], window['end_frame']
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError('速報の音声範囲が不正です。')
    with Path(raw_path).open('rb') as source:
        source.seek(start * 2)
        pcm = source.read((end - start) * 2)
    if len(pcm) != (end - start) * 2:
        raise ValueError('速報に必要な保存済み音声を確認できません。')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.wav.part')
    try:
        with temporary.open('xb') as handle:
            with wave.open(handle, 'wb') as stream:
                stream.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
                stream.writeframes(pcm)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {'index': window['revision'], 'path': str(destination), 'frames': end - start,
            'start_frame': start, 'end_frame': end, 'start_seconds': start / RATE,
            'end_seconds': end / RATE, 'sample_rate': RATE, 'channels': 1,
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'pcm_sha256': hashlib.sha256(pcm).hexdigest(),
            'completed_at': window['ready_at'], 'provisional': True}
