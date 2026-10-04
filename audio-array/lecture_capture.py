"""Durable mono microphone capture, independent of lecture inference.

Only ``CaptureSession.start`` opens a microphone. Device listing is read-only.
``raw.pcm`` is authoritative signed little-endian PCM16, mono, 16 kHz. WAVs
and the frame ledger can be regenerated with ``recover_raw`` after a crash.
AVFoundation syntax: https://ffmpeg.org/ffmpeg-devices.html#avfoundation
"""
from __future__ import annotations

from array import array
from collections import deque
import hashlib
import fcntl
import json
import math
import os
import platform
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Callable
import wave


SAMPLE_RATE = 16000
NATIVE_SOURCE = Path(__file__).with_name("lecture_capture_native.swift")
NATIVE_CACHE = Path(__file__).resolve().parents[1] / "data/event-audio/native-capture"


def ensure_native_helper(cache_dir=None, *, compiler="swiftc") -> Path:
    """Build the local native source once per source/toolchain hash; no downloads."""
    executable = shutil.which(compiler)
    if not executable:
        raise RuntimeError("Swift compiler is unavailable; install Apple's command line tools before capture")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True,
                             check=True, timeout=15).stdout
    source_bytes = NATIVE_SOURCE.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    identity = hashlib.sha256((source_sha + version + platform.machine() + "live-lecture-native-build-v1").encode()).hexdigest()
    directory = Path(cache_dir or NATIVE_CACHE).resolve() / identity[:20]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "lecture_capture_native"
    metadata = directory / "build.json"
    with (directory / "build.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if target.is_file() and metadata.is_file():
            try:
                saved = json.loads(metadata.read_text())
            except (ValueError, OSError):
                saved = {}
            if (isinstance(saved, dict) and saved.get("identity") == identity and os.access(target, os.X_OK)
                    and saved.get("binary_sha256") == hashlib.sha256(target.read_bytes()).hexdigest()):
                return target
        plist = directory / "Info.plist"
        plist.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>org.live-lecture-translation.capture-native</string>
<key>CFBundleName</key><string>Lecture Microphone Capture</string>
<key>NSMicrophoneUsageDescription</key><string>講演音声をMac内へ保存し、講演中の理解を支援します。</string>
</dict></plist>''', encoding="utf-8")
        temporary = directory / f"lecture_capture_native.build-{os.getpid()}"
        snapshot_source = directory / NATIVE_SOURCE.name
        snapshot_source.write_bytes(source_bytes)
        command = [executable, "-O", "-module-cache-path", str(directory.parent / "module-cache"),
                   str(snapshot_source), "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT",
                   "-Xlinker", "__info_plist", "-Xlinker", str(plist), "-o", str(temporary)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            (directory / "build.log").write_text(result.stderr, encoding="utf-8")
            if result.returncode:
                raise RuntimeError(f"Native microphone helper compilation failed; inspect {directory / 'build.log'}")
            os.replace(temporary, target)
            _atomic_json(metadata, {"identity": identity, "source_sha256": source_sha,
                         "compiler_version": version.strip(),
                         "binary_sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
            _fsync_dir(directory)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def _native_helper_provenance(helper: Path) -> dict:
    """Freeze the executable/build metadata used by this capture attempt."""
    helper = helper.resolve()
    metadata = helper.parent / "build.json"
    record = {"path": str(helper), "build_metadata_path": str(metadata),
              "identity": None, "source_sha256": None, "binary_sha256": None,
              "compiler_version": None, "observed_binary_sha256": None,
              "binary_matches_metadata": None}
    try:
        record["observed_binary_sha256"] = hashlib.sha256(helper.read_bytes()).hexdigest()
    except OSError as exc:
        record["binary_read_error"] = type(exc).__name__
    try:
        saved = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("build metadata must be an object")
        for key in ("identity", "source_sha256", "binary_sha256", "compiler_version"):
            if isinstance(saved.get(key), str):
                record[key] = saved[key]
    except (OSError, ValueError) as exc:
        record["metadata_read_error"] = type(exc).__name__
    if record["binary_sha256"] and record["observed_binary_sha256"]:
        record["binary_matches_metadata"] = record["binary_sha256"] == record["observed_binary_sha256"]
    return record


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def parse_devices(stderr: str) -> list[dict]:
    """Read audio indices only; do not confuse video devices with microphones."""
    audio = False
    devices = []
    for line in stderr.splitlines():
        if "AVFoundation audio devices:" in line:
            audio = True
            continue
        if "AVFoundation video devices:" in line:
            audio = False
            continue
        if audio:
            match = re.search(r"\[(\d+)\]\s+(.+)$", line)
            if match:
                devices.append({"id": match.group(1), "name": match.group(2).strip()})
    return devices


def list_devices(ffmpeg: str = "ffmpeg", *, runner=None, backend="native", native_helper=None) -> list[dict]:
    """Enumerate native CoreAudio inputs, or explicit legacy FFmpeg inputs."""
    if backend == "native":
        helper = Path(native_helper) if native_helper else ensure_native_helper()
        result = (runner or subprocess.run)([str(helper), "--list-devices"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=15, check=False)
        if result.returncode:
            raise RuntimeError("Could not enumerate native CoreAudio input devices")
        devices = json.loads(result.stdout)
        if not isinstance(devices, list) or any(not isinstance(item, dict)
                or not str(item.get("id", "")).startswith("coreaudio:")
                or not isinstance(item.get("name"), str) for item in devices):
            raise RuntimeError("Native audio device list was malformed")
        return devices
    if backend != "ffmpeg":
        raise ValueError("backend must be native or ffmpeg")
    result = (runner or subprocess.run)(
        [ffmpeg, "-hide_banner", "-nostdin", "-f", "avfoundation",
         "-list_devices", "true", "-i", ""],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=15, check=False,
    )
    # FFmpeg intentionally exits nonzero after a successful device listing.
    if "AVFoundation audio devices:" not in result.stderr:
        raise RuntimeError("Could not enumerate AVFoundation audio inputs; check FFmpeg and microphone permission")
    return parse_devices(result.stderr)


def _levels(data: bytes) -> tuple[float | None, float | None]:
    if not data:
        return None, None
    values = array("h")
    values.frombytes(data[:len(data) // 2 * 2])
    if not values:
        return None, None
    if sys.byteorder != "little":
        values.byteswap()
    peak = max(abs(value) for value in values)
    rms = math.sqrt(sum(value * value for value in values) / len(values))
    return (max(-120.0, 20 * math.log10(rms / 32768)) if rms else -120.0,
            max(-120.0, 20 * math.log10(peak / 32768)) if peak else -120.0)


class PCMChunkWriter:
    """Single-writer durable PCM storage, also usable by a paced replay.

    Each write is persisted to raw before deriving WAV chunks. A trailing odd
    byte is retained in raw and reported, never silently presented as a frame.
    Callbacks run synchronously here; CaptureSession dispatches them separately.
    Existing output directories, including empty ones, are rejected.
    """

    def __init__(self, output_dir, chunk_seconds: float = 15,
                 sample_rate: int = SAMPLE_RATE, on_chunk: Callable | None = None):
        if (isinstance(sample_rate, bool) or not isinstance(sample_rate, int)
                or sample_rate <= 0):
            raise ValueError("sample_rate must be a positive integer")
        if (not isinstance(chunk_seconds, (int, float)) or isinstance(chunk_seconds, bool)
                or not math.isfinite(chunk_seconds) or chunk_seconds <= 0):
            raise ValueError("chunk_seconds must be positive and finite")
        self.chunk_frames = round(chunk_seconds * sample_rate)
        if self.chunk_frames < 1:
            raise ValueError("chunk_seconds must span at least one frame")
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(exist_ok=False)
        self.sample_rate = sample_rate
        self.on_chunk = on_chunk
        self.raw_path = self.output_dir / "raw.pcm"
        self._raw = self.raw_path.open("xb", buffering=0)
        self._ledger = (self.output_dir / "chunks.jsonl").open("x", encoding="utf-8")
        self._pending = bytearray()
        self._raw_sha = hashlib.sha256()
        self._raw_bytes = 0
        self._derived_frames = 0
        self._count = 0
        self._closed = False
        self._lock = threading.RLock()
        self._snapshot_lock = threading.Lock()
        self._metadata = {
            "format": "s16le", "sample_rate": sample_rate, "channels": 1,
            "sample_width": 2, "chunk_frames": self.chunk_frames,
            "raw_path": str(self.raw_path), "created_at": time.time(),
        }
        self._cached_snapshot = self._storage_snapshot()
        _atomic_json(self.output_dir / "pcm.json", self._metadata)
        _fsync_dir(self.output_dir)

    def write(self, data: bytes) -> list[dict]:
        """Append arbitrary byte boundaries, returning newly completed chunks."""
        with self._lock:
            if self._closed:
                raise RuntimeError("PCM writer is closed")
            if not data:
                return []
            # Unbuffered writes can be short; only account for actual bytes.
            view = memoryview(data)
            while view:
                written = self._raw.write(view)
                if not written:
                    raise OSError("PCM storage made no progress")
                piece = view[:written]
                self._raw_sha.update(piece)
                self._raw_bytes += written
                self._pending.extend(piece)
                view = view[written:]
            os.fsync(self._raw.fileno())
            self._publish_snapshot()
            chunks = []
            size = self.chunk_frames * 2
            while len(self._pending) >= size:
                chunks.append(self._emit(size))
            return chunks

    def _emit(self, size: int) -> dict:
        data = bytes(self._pending[:size])
        path = self.output_dir / f"chunk-{self._count:06d}.wav"
        temporary = path.with_suffix(".wav.part")
        with temporary.open("xb") as handle:
            with wave.open(handle, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(self.sample_rate)
                wav.writeframes(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_dir(self.output_dir)
        frames = size // 2
        chunk = {
            "index": self._count, "path": str(path), "frames": frames,
            "start_frame": self._derived_frames,
            "end_frame": self._derived_frames + frames,
            "start_seconds": self._derived_frames / self.sample_rate,
            "end_seconds": (self._derived_frames + frames) / self.sample_rate,
            "sample_rate": self.sample_rate, "channels": 1,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "pcm_sha256": hashlib.sha256(data).hexdigest(),
            "completed_at": time.time(),
            "partial": frames < self.chunk_frames,
        }
        self._ledger.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        self._ledger.flush()
        os.fsync(self._ledger.fileno())
        del self._pending[:size]
        self._derived_frames += frames
        self._count += 1
        self._publish_snapshot()
        if self.on_chunk:
            self.on_chunk(dict(chunk))
        return chunk

    def snapshot(self) -> dict:
        # Never wait behind filesystem I/O. During a blocked write/fsync return
        # the last confirmed checkpoint; CaptureSession still shows reader alive.
        with self._snapshot_lock:
            return dict(self._cached_snapshot)

    def _storage_snapshot(self) -> dict:
        """Current writer-owned accounting; use only while serializing writes."""
        return {
            **self._metadata, "raw_bytes": self._raw_bytes,
            "frames": self._raw_bytes // 2,
            "audio_seconds": self._raw_bytes // 2 / self.sample_rate,
            "derived_frames": self._derived_frames, "chunks": self._count,
            "trailing_bytes": self._raw_bytes % 2,
            "raw_sha256": self._raw_sha.hexdigest(), "closed": self._closed,
            "storage_sync_confirmed": True,
        }

    def _publish_snapshot(self) -> None:
        # Called only after raw fsync, and after derived ledger fsync. The
        # expensive I/O and audio copying never hold this short status lock.
        value = self._storage_snapshot()
        with self._snapshot_lock:
            self._cached_snapshot = value

    def close(self) -> list[dict]:
        """Flush a final partial chunk and close files; repeated calls are safe."""
        with self._lock:
            if self._closed:
                return []
            chunks = []
            raw_sync_confirmed = False
            try:
                os.fsync(self._raw.fileno())
                raw_sync_confirmed = True
                self._publish_snapshot()
                size = len(self._pending) // 2 * 2
                if size:
                    chunks.append(self._emit(size))
            finally:
                self._raw.close()
                self._ledger.close()
                self._closed = True
                if raw_sync_confirmed:
                    self._publish_snapshot()
                else:
                    with self._snapshot_lock:
                        self._cached_snapshot.update(closed=True, storage_sync_confirmed=False,
                                                     observed_raw_bytes=self._raw_bytes)
                # Preserve actual file accounting even when final fsync failed;
                # the flag keeps those bytes distinct from confirmed progress.
                metadata = self._storage_snapshot()
                metadata['storage_sync_confirmed'] = raw_sync_confirmed
                _atomic_json(self.output_dir / "pcm.json", metadata)
                _fsync_dir(self.output_dir)
            return chunks


def recover_raw(raw_path, output_dir, *, chunk_seconds: float = 15,
                sample_rate: int = SAMPLE_RATE, on_chunk: Callable | None = None) -> dict:
    """Rebuild into a NEW directory, retaining the original raw/ledger untouched."""
    with Path(raw_path).open("rb") as source:
        writer = PCMChunkWriter(output_dir, chunk_seconds, sample_rate, on_chunk)
        try:
            while data := source.read(65536):
                writer.write(data)
        finally:
            writer.close()
    return writer.snapshot()


class CaptureSession:
    """Native Mac capture with durable storage and isolated callback delivery.

    backend='ffmpeg' explicitly selects the legacy AVFoundation path. It is
    retained for diagnostics; some FFmpeg versions overwrite unread audio.

    on_chunk should enqueue work, not execute inference. Even a blocked callback
    does not block capture: pending chunks remain in memory and in chunks.jsonl.
    Status notifications coalesce while delivery is slow. stop() reports callback
    and process completion separately; consumers can recover undelivered chunks
    from the durable ledger. This object can be started only once.
    """

    def __init__(self, output_dir, device: str = "default", chunk_seconds: float = 15,
                 on_chunk: Callable | None = None, on_status: Callable | None = None,
                 *, ffmpeg: str = "ffmpeg", stall_seconds: float = 3,
                 stop_timeout: float = 5, process_factory=None,
                 clock: Callable = time.time, monotonic: Callable = time.monotonic,
                 backend: str = "native", native_helper=None,
                 startup_timeout: float = 45, input_timeout: float = 10):
        device = str(device)
        if backend not in ("native", "ffmpeg"):
            raise ValueError("backend must be native or ffmpeg")
        if (not device or device == "none" or any(c in device for c in ("\n", "\r", "\0"))
                or (backend == "ffmpeg" and ":" in device)):
            raise ValueError("device must be a valid input identifier, name, or default")
        if not math.isfinite(stall_seconds) or stall_seconds <= 0:
            raise ValueError("stall_seconds must be positive and finite")
        if not math.isfinite(stop_timeout) or stop_timeout <= 0:
            raise ValueError("stop_timeout must be positive and finite")
        for name, value in (("startup_timeout", startup_timeout), ("input_timeout", input_timeout)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be positive and finite")
        self.output_dir = Path(output_dir).resolve()
        self.device = device
        self.chunk_seconds = chunk_seconds
        self.ffmpeg = ffmpeg
        self.backend = backend
        self.native_helper = Path(native_helper) if native_helper else None
        self._native_helper_provenance = None
        self.diagnostics_path = self.output_dir / ("native.stderr.jsonl" if backend == "native" else "ffmpeg.stderr.log")
        self._native_diagnostics = None
        self.stall_seconds = stall_seconds
        self.stop_timeout = stop_timeout
        self.startup_timeout = startup_timeout
        self.input_timeout = input_timeout
        self._factory = process_factory or subprocess.Popen
        self._clock, self._monotonic = clock, monotonic
        self.on_chunk, self.on_status = on_chunk, on_status
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._notify = threading.Condition()
        self._chunks = deque()
        self._latest_status = None
        self._dispatch_finished = False
        self._callback_active = False
        self._callback_errors = 0
        self._stop_requested = threading.Event()
        self._reader = self._dispatcher = None
        self._process = self._writer = self._stderr = None
        self._state = "new"
        self._error = None
        self._started_at = self._started_mono = None
        self._process_started_mono = None
        self._watchdog_triggered_mono = self._watchdog_error = None
        self._first_audio_at = self._first_audio_mono = None
        self._last_audio_at = self._last_audio_mono = None
        self._max_audio_gap = 0.0
        self._rms = self._peak = None
        self._meter_tail = b""
        self._killed = False

    def start(self):
        with self._lock:
            if self._state != "new":
                raise RuntimeError("CaptureSession can only be started once")
            # Refuse existing output before acquiring a microphone.
            self._writer = PCMChunkWriter(self.output_dir, self.chunk_seconds,
                                          on_chunk=self._enqueue_chunk)
            self._state = "starting"
            self._started_at, self._started_mono = self._clock(), self._monotonic()
            try:
                self._stderr = self.diagnostics_path.open("xb", buffering=0)
                command = [self.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning",
                           "-f", "avfoundation", "-probesize", "32", "-analyzeduration", "1",
                           "-i", f"none:{self.device}",
                           "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                           "-c:a", "pcm_s16le", "-f", "s16le", "-flush_packets", "1", "pipe:1"]
                # AVFoundation supplies PCM format metadata. Bound probing and
                # flush output packets to avoid file-oriented startup buffering.
                # analyzeduration=0 can select FFmpeg's automatic 5 s default;
                # use 1 microsecond. Do not discard initial packets via nobuffer.
                if self.backend == "native":
                    helper = (self.native_helper or ensure_native_helper()).resolve()
                    self._native_helper_provenance = _native_helper_provenance(helper)
                    command = [str(helper), "--capture", "--device", self.device]
                # No shell, secrets, network destinations, or inherited stdin.
                self._process = self._factory(command, stdin=subprocess.DEVNULL,
                                              stdout=subprocess.PIPE, stderr=self._stderr,
                                              bufsize=0, start_new_session=True)
                self._process_started_mono = self._monotonic()
                self._dispatcher = threading.Thread(target=self._dispatch, name="lecture-notify", daemon=True)
                self._reader = threading.Thread(target=self._read, name="lecture-capture", daemon=True)
                self._persist()
                self._dispatcher.start()
                self._reader.start()
            except Exception as exc:
                self._state = "error"
                self._error = f"Capture startup failed: {type(exc).__name__}"
                if self._process and self._process.poll() is None:
                    self._process.kill()
                    self._process.wait(timeout=self.stop_timeout)
                self._writer.close()
                if self._stderr:
                    self._stderr.close()
                with self._notify:
                    self._dispatch_finished = True
                    self._notify.notify_all()
                self._persist()
                raise
        return self

    def _enqueue_chunk(self, chunk: dict) -> None:
        if not self.on_chunk:
            return
        with self._notify:
            self._chunks.append(chunk)
            self._notify.notify_all()

    def _queue_status(self) -> None:
        if self.on_status:
            status = self.snapshot()
            with self._notify:
                self._latest_status = status
                self._notify.notify_all()

    def _dispatch(self) -> None:
        while True:
            with self._notify:
                self._notify.wait_for(lambda: self._chunks or self._latest_status is not None
                                      or self._dispatch_finished)
                if self._chunks:
                    callback, value = self.on_chunk, self._chunks.popleft()
                elif self._latest_status is not None:
                    callback, value = self.on_status, self._latest_status
                    self._latest_status = None
                elif self._dispatch_finished:
                    return
                self._callback_active = True
            try:
                callback(value)
            except Exception:
                # A display/consumer error must never stop audio persistence.
                with self._notify:
                    self._callback_errors += 1
            finally:
                with self._notify:
                    self._callback_active = False

    def _read(self) -> None:
        selector = selectors.DefaultSelector()
        last_status = 0.0
        try:
            selector.register(self._process.stdout, selectors.EVENT_READ)
            while True:
                ready = selector.select(timeout=0.2)
                if ready:
                    data = os.read(self._process.stdout.fileno(), 32768)
                    if not data:
                        break
                    received_at, received_mono = self._clock(), self._monotonic()
                    # Writer takes no session lock; snapshots do not hold it
                    # while requesting writer data either.
                    self._writer.write(data)
                    metered = self._meter_tail + data
                    aligned = len(metered) // 2 * 2
                    rms, peak = _levels(metered[:aligned])
                    self._meter_tail = metered[aligned:]
                    with self._lock:
                        if self._first_audio_at is None:
                            self._first_audio_at, self._first_audio_mono = received_at, received_mono
                        if self._last_audio_mono is not None:
                            self._max_audio_gap = max(self._max_audio_gap, received_mono - self._last_audio_mono)
                        self._last_audio_at, self._last_audio_mono = received_at, received_mono
                        if rms is not None:
                            self._rms, self._peak = rms, peak
                        if not self._stop_requested.is_set():
                            self._state = "recording"
                now = self._monotonic()
                self._check_input_timeout(now)
                if now - last_status >= 0.5:
                    self._persist()
                    self._queue_status()
                    last_status = now
            code = self._process.wait(timeout=self.stop_timeout)
            with self._lock:
                if not self._stop_requested.is_set():
                    self._error = self._error or f"Audio input ended unexpectedly ({self.backend} exit {code}); see {self.diagnostics_path.name}"
                elif self.backend == "native" and code != 0:
                    self._error = self._error or f"Native audio capture reported an error (exit {code}); see {self.diagnostics_path.name}"
        except Exception as exc:
            with self._lock:
                self._error = self._watchdog_error or f"Audio capture failed: {type(exc).__name__}; raw PCM retained"
            if self._process.poll() is None:
                self._process.kill()
                self._killed = True
                try:
                    self._process.wait(timeout=self.stop_timeout)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            selector.close()
            self._process.stdout.close()
            try:
                self._writer.close()
            except Exception as exc:
                with self._lock:
                    self._error = f"PCM finalization failed: {type(exc).__name__}; raw PCM retained"
            self._stderr.close()
            if self.backend == "native":
                try:
                    for line in self.diagnostics_path.read_text(encoding="utf-8", errors="replace").splitlines():
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(record, dict):
                            self._native_diagnostics = record
                            if record.get("error"):
                                self._error = self._watchdog_error or str(record["error"])
                except OSError:
                    pass
            with self._lock:
                if self._killed:
                    self._error = self._error or f"{self.backend} capture required forced termination; raw PCM retained"
                if self._writer.snapshot()["trailing_bytes"]:
                    self._error = self._error or "Input ended with an incomplete PCM frame; trailing byte retained in raw.pcm"
                self._state = "error" if self._error else "stopped"
            try:
                self._persist()
                self._queue_status()
            finally:
                with self._notify:
                    self._dispatch_finished = True
                    self._notify.notify_all()

    def _check_input_timeout(self, now: float) -> None:
        """Fail a silent pipe, requesting a flush before bounded termination.

        PCM containing zeros is still input. An ordinary UI stall is only a
        warning; these longer deadlines prevent a live but stuck subprocess
        from remaining active forever. No device retry or restart is implicit.
        """
        request_stop = force_stop = False
        with self._lock:
            if self._watchdog_triggered_mono is None and not self._stop_requested.is_set():
                reference = (self._last_audio_mono if self._last_audio_mono is not None
                             else self._process_started_mono)
                limit = self.input_timeout if self._last_audio_mono is not None else self.startup_timeout
                if reference is not None and now - reference >= limit:
                    self._watchdog_error = (
                        f"PCM input stopped arriving for {self.input_timeout:g} seconds; saved audio retained"
                        if self._last_audio_mono is not None else
                        f"No initial PCM arrived within {self.startup_timeout:g} seconds; check microphone permission and device")
                    self._error = self._watchdog_error
                    self._watchdog_triggered_mono = now
                    self._state = "stopping"
                    self._stop_requested.set()
                    request_stop = True
            if (self._watchdog_triggered_mono is not None
                    and now - self._watchdog_triggered_mono >= self.stop_timeout
                    and self._process.poll() is None):
                self._killed = True
                force_stop = True
        try:
            if force_stop:
                self._process.kill()
            elif request_stop:
                self._process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass

    def snapshot(self) -> dict:
        # No nested session/writer lock: the recorder can always make progress.
        pcm = self._writer.snapshot() if self._writer else {}
        with self._lock:
            state = self._state
            elapsed = None if self._started_mono is None else self._monotonic() - self._started_mono
            age = None if self._last_audio_mono is None else max(0.0, self._monotonic() - self._last_audio_mono)
            if state in ("starting", "recording") and (
                    (age is not None and age >= self.stall_seconds)
                    or (age is None and elapsed is not None and elapsed >= self.stall_seconds)):
                state = "stalled"
            signal_state = "unknown" if self._rms is None or state == "stalled" else (
                "silent" if self._rms < -60 else "present")
            with self._notify:
                pending = len(self._chunks)
                active = self._callback_active
                errors = self._callback_errors
            process_exited = self._process is None or self._process.poll() is not None
            reader_alive = bool(self._reader and self._reader.is_alive())
            dispatcher_alive = bool(self._dispatcher and self._dispatcher.is_alive())
            return {
                "state": state, "signal": signal_state, "device": self.device, "backend": self.backend,
                "sample_rate": SAMPLE_RATE, "channels": 1,
                "audio_seconds": pcm.get("audio_seconds", 0),
                "frames": pcm.get("frames", 0), "chunks": pcm.get("chunks", 0),
                "raw_bytes": pcm.get("raw_bytes", 0), "trailing_bytes": pcm.get("trailing_bytes", 0),
                "rms_dbfs": self._rms, "peak_dbfs": self._peak,
                "started_at": self._started_at, "last_audio_at": self._last_audio_at,
                "first_audio_at": self._first_audio_at,
                "startup_seconds": (self._first_audio_mono - self._started_mono
                                    if self._first_audio_mono is not None else None),
                "max_audio_gap_seconds": self._max_audio_gap,
                "timing_basis": "PCM pipe receipt; delivery gaps alone do not prove lost audio",
                "startup_timeout_seconds": self.startup_timeout,
                "input_timeout_seconds": self.input_timeout,
                "watchdog_error": self._watchdog_error,
                "ffmpeg_options": ({"probesize": 32, "analyzeduration": 1, "flush_packets": 1}
                                   if self.backend == "ffmpeg" else None),
                "diagnostics_path": str(self.diagnostics_path), "native_diagnostics": self._native_diagnostics,
                "native_helper": (dict(self._native_helper_provenance)
                                  if self._native_helper_provenance is not None else None),
                "last_audio_age_seconds": age, "observed_at": self._clock(),
                "error": self._error, "process_id": self._process.pid if self._process else None,
                "returncode": self._process.poll() if self._process else None,
                "process_exited": process_exited, "reader_alive": reader_alive,
                "forced_termination": self._killed,
                "stop_confirmed": process_exited and not reader_alive,
                "pending_chunks": pending, "callback_active": active,
                "callback_errors": errors, "callbacks_confirmed": not dispatcher_alive,
                "output_dir": str(self.output_dir), "raw_path": str(self.output_dir / "raw.pcm"),
                "ledger_path": str(self.output_dir / "chunks.jsonl"),
            }

    def _persist(self) -> None:
        # Only startup, the reader, or post-join stop call writes capture.json.
        _atomic_json(self.output_dir / "capture.json", self.snapshot())

    def stop(self) -> dict:
        """Request SIGINT, then bounded kill/wait; return observed completion."""
        with self._stop_lock:
            with self._lock:
                self._stop_requested.set()
                process = self._process
                if self._state == "new":
                    self._state = "stopped"
                elif self._state in ("starting", "recording", "stalled"):
                    self._state = "stopping"
            if process and process.poll() is None:
                try:
                    process.send_signal(signal.SIGINT)
                    process.wait(timeout=self.stop_timeout)
                except subprocess.TimeoutExpired:
                    self._killed = True
                    process.kill()
                    try:
                        process.wait(timeout=self.stop_timeout)
                    except subprocess.TimeoutExpired:
                        with self._lock:
                            self._error = f"{self.backend} capture termination could not be confirmed"
                except ProcessLookupError:
                    pass
            if (self._reader and self._reader.ident is not None
                    and self._reader is not threading.current_thread()):
                self._reader.join(timeout=self.stop_timeout)
            if (self._dispatcher and self._dispatcher.ident is not None
                    and self._dispatcher is not threading.current_thread()):
                self._dispatcher.join(timeout=self.stop_timeout)
            result = self.snapshot()
            if self._writer and not result["reader_alive"]:
                self._persist()
            return result
