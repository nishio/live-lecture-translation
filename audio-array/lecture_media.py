"""Prepare an explicitly selected local media file for the lecture pipeline.

No recognition, model access, network requests or persistent cache writes occur
here. A caller owns any durable copy; temporary conversions live only inside
``prepared_audio``. The original file is never rewritten.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import hashlib
import math
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import wave


class MediaError(ValueError):
    pass


SAMPLE_RATE = 16000
# Deliberately exclude playlist, concatenation and image-sequence demuxers.
# MOV external data references remain disabled by ffmpeg's default (enable_drefs
# and use_absolute_path are never enabled). These demuxer-specific switches
# cannot be passed for other containers because ffmpeg rejects unused options.
LOCAL_FORMATS = ('aac,aiff,asf,avi,flac,matroska,webm,mov,mp4,m4a,3gp,3g2,mj2,'
                 'mp3,mpeg,mpegts,ogg,wav,au,caf,ac3,eac3,amr')


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_identity(path):
    try:
        path = Path(path).expanduser().resolve(strict=True)
        if not stat.S_ISREG(path.stat().st_mode):
            raise MediaError('Input must be a regular local audio or video file.')
        with path.open('rb') as source:
            before = os.fstat(source.fileno())
            digest = hashlib.sha256()
            while block := source.read(1024 * 1024):
                digest.update(block)
            if (_signature(before) != _signature(os.fstat(source.fileno())) or
                    _signature(before) != _signature(path.stat())):
                raise MediaError('Input changed during inspection; keep it unchanged and check again.')
        return ({'path': str(path), 'sha256': digest.hexdigest(), 'file_bytes': before.st_size},
                _signature(before))
    except OSError as exc:
        raise MediaError(f'Cannot read local input file: {exc}') from exc


def source_identity(path):
    """Return the complete regular-file identity without invoking a decoder."""
    return _read_identity(path)[0]


def _selected_frames(seconds):
    if seconds is None:
        return None
    try:
        valid = math.isfinite(seconds) and seconds > 0
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise MediaError('--seconds must be finite and positive.')
    frames = int(Decimal(str(seconds)) * SAMPLE_RATE)
    if frames <= 0:
        raise MediaError('--seconds must select at least one audio frame.')
    return frames


def _validate_riff(path):
    """Catch incomplete RIFF containers before a decoder can repair them."""
    with path.open('rb') as source:
        header = source.read(12)
        if len(header) < 12 or header[:4] != b'RIFF' or header[8:12] != b'WAVE':
            return
        size = path.stat().st_size
        declared_end = struct.unpack('<I', header[4:8])[0] + 8
        if declared_end > size or declared_end < 12:
            raise MediaError('The WAV is truncated or has an invalid RIFF length.')
        position = 12
        while position < declared_end:
            source.seek(position)
            chunk = source.read(8)
            if len(chunk) != 8:
                raise MediaError('The WAV is truncated or has an incomplete chunk header.')
            length = struct.unpack('<I', chunk[4:])[0]
            end = position + 8 + length
            if end > declared_end:
                raise MediaError('The WAV is truncated or has incomplete PCM frames.')
            position = end + (length % 2)
        if position != declared_end:
            raise MediaError('The WAV is truncated or has incomplete chunk padding.')


def _pcm_frames(path, *, allow_other_format=False):
    _validate_riff(path)
    try:
        with wave.open(str(path), 'rb') as audio:
            parameters = (audio.getnchannels(), audio.getsampwidth(),
                          audio.getframerate(), audio.getcomptype())
            # Even a noncanonical PCM WAV must be intact before conversion.
            frames = audio.getnframes()
            frame_bytes = audio.getnchannels() * audio.getsampwidth()
            observed = 0
            while block := audio.readframes(65536):
                observed += len(block)
            if observed != frames * frame_bytes:
                raise MediaError('The WAV is truncated or has incomplete PCM frames.')
            if frames <= 0:
                raise MediaError('The WAV contains no audio frames.')
            if parameters != (1, 2, SAMPLE_RATE, 'NONE'):
                if allow_other_format:
                    return None
                raise MediaError('Use an uncompressed 16 kHz mono PCM16 WAV.')
            return frames
    except (wave.Error, EOFError) as exc:
        if allow_other_format:
            return None
        raise MediaError('Cannot read this WAV; use uncompressed 16 kHz mono PCM16 audio.') from exc


def inspect_pcm_audio(path, seconds=None):
    """Strict, complete canonical-WAV inspection, compatible with old plans."""
    selected = _selected_frames(seconds)
    identity, signature = _read_identity(path)
    path = Path(identity['path'])
    frames = _pcm_frames(path)
    after, after_signature = _read_identity(path)
    if identity != after or signature != after_signature:
        raise MediaError('Input changed during inspection; keep it unchanged and check again.')
    selected = frames if selected is None else min(frames, selected)
    return {**identity, 'sample_rate': SAMPLE_RATE, 'channels': 1,
            'sample_width_bytes': 2, 'frames': frames, 'duration_seconds': frames / SAMPLE_RATE,
            'selected_frames': selected, 'selected_seconds': selected / SAMPLE_RATE}


def find_ffmpeg():
    """Prefer setup's bundled decoder; never download a runtime dependency."""
    try:
        import imageio_ffmpeg
        candidate = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    except (ImportError, RuntimeError, OSError):
        pass
    candidate = shutil.which('ffmpeg')
    if candidate:
        return candidate
    raise MediaError('Audio/video conversion requires ffmpeg. Run ./setup.command again to '
                     'install the bundled decoder, or install ffmpeg, then retry.')


def _copy_pcm(source, destination, frames):
    with wave.open(str(source), 'rb') as original, wave.open(str(destination), 'wb') as out:
        out.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
        remaining = frames
        while remaining:
            block = original.readframes(min(65536, remaining))
            if not block or len(block) % 2:
                raise MediaError('The WAV changed or ended while preparing audio.')
            out.writeframesraw(block)
            remaining -= len(block) // 2


def _decode(source, destination, selected):
    executable = find_ffmpeg()
    raw = destination.with_suffix('.pcm')
    filters = 'aresample=16000'
    if selected is not None:
        # Do not stop input decoding early: a broken suffix must not be hidden.
        filters += f',atrim=end_sample={min(selected, 2**63 - 1)}'
    command = [executable, '-hide_banner', '-nostdin', '-loglevel', 'error',
               '-xerror', '-err_detect', 'explode', '-protocol_whitelist', 'file,pipe',
               '-format_whitelist', LOCAL_FORMATS,
               '-i', str(source), '-map', '0:a:0', '-vn', '-sn', '-dn', '-af', filters,
               '-ac', '1', '-ar', str(SAMPLE_RATE), '-c:a', 'pcm_s16le',
               '-f', 's16le', '-y', str(raw)]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            detail = result.stderr.decode('utf-8', errors='replace').strip()[-1200:]
            raise MediaError('Cannot decode this local media file; it may be damaged, have no audio, '
                             f'or use an unsupported format. {detail}')
        raw_bytes = raw.stat().st_size
        if raw_bytes <= 0 or raw_bytes % 2:
            raise MediaError('Decoded input has no complete audio frames.')
        with raw.open('rb') as pcm, wave.open(str(destination), 'wb') as out:
            out.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
            while block := pcm.read(1024 * 1024):
                out.writeframesraw(block)
        version = subprocess.run([executable, '-version'], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if version.returncode != 0:
            raise MediaError('Cannot identify the installed ffmpeg decoder; run ./setup.command again.')
        lines = version.stdout.decode('utf-8', errors='replace').splitlines()
        return lines[0] if lines else 'unknown'
    except OSError as exc:
        raise MediaError(f'Cannot run the audio decoder; run ./setup.command again. {exc}') from exc
    finally:
        raw.unlink(missing_ok=True)


@contextmanager
def prepared_audio(path, seconds=None):
    """Yield canonical audio plus source/conversion metadata; clean up on exit.

Canonical untrimmed WAVs retain their original path and exact bytes. Converted
or shortened files have a deterministic minimal PCM WAV header. Source hashes
and inode metadata are checked before publication and after the caller's work.
"""
    selected = _selected_frames(seconds)
    source, signature = _read_identity(path)
    source_path = Path(source['path'])

    def unchanged():
        after, after_signature = _read_identity(source_path)
        if after != source or after_signature != signature:
            raise MediaError('Input changed while preparing or using audio; keep it unchanged and retry.')

    frames = _pcm_frames(source_path, allow_other_format=True)
    if frames is not None and (selected is None or selected >= frames):
        audio = inspect_pcm_audio(source_path)
        unchanged()
        try:
            yield {'source': source, 'input': audio,
                   'conversion': {'required': False, 'requested_seconds': seconds, 'engine': 'none'}}
        finally:
            unchanged()
        return

    with tempfile.TemporaryDirectory(prefix='lecture-media-') as temporary:
        destination = Path(temporary) / 'audio.wav'
        conversion = {'required': True, 'requested_seconds': seconds,
                      'engine': 'python-wave' if frames is not None else 'ffmpeg'}
        if frames is not None:
            _copy_pcm(source_path, destination, min(selected, frames))
        else:
            conversion['ffmpeg_version'] = _decode(source_path, destination, selected)
        audio = inspect_pcm_audio(destination)
        unchanged()
        try:
            yield {'source': source, 'input': audio, 'conversion': conversion}
        finally:
            unchanged()
