import hashlib
import errno
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_capture


def read_chunks(directory):
    return [json.loads(line) for line in (directory / "chunks.jsonl").read_text().splitlines()]


class PCMChunkWriterTest(unittest.TestCase):
    def test_arbitrary_boundaries_exact_pcm_frames_hash_and_final_partial(self):
        with tempfile.TemporaryDirectory() as base:
            directory = Path(base) / "capture"
            delivered = []
            writer = lecture_capture.PCMChunkWriter(directory, chunk_seconds=1,
                                                     sample_rate=4, on_chunk=delivered.append)
            pcm = struct.pack("<11h", *range(-5, 6))
            for size in (1, 2, 7, 3, 9):
                writer.write(pcm[:size])
                pcm = pcm[size:]
            self.assertEqual(2, len(delivered))
            writer.close()
            self.assertEqual([], writer.close())
            original = struct.pack("<11h", *range(-5, 6))
            self.assertEqual(original, (directory / "raw.pcm").read_bytes())
            self.assertEqual([4, 4, 3], [chunk["frames"] for chunk in delivered])
            self.assertEqual([0, 4, 8], [chunk["start_frame"] for chunk in delivered])
            self.assertEqual([4, 8, 11], [chunk["end_frame"] for chunk in delivered])
            self.assertEqual([False, False, True], [chunk["partial"] for chunk in delivered])
            decoded = b""
            for chunk in read_chunks(directory):
                path = Path(chunk["path"])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), chunk["sha256"])
                with wave.open(str(path), "rb") as wav:
                    self.assertEqual((1, 2, 4), (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()))
                    payload = wav.readframes(wav.getnframes())
                    self.assertEqual(hashlib.sha256(payload).hexdigest(), chunk["pcm_sha256"])
                    decoded += payload
            self.assertEqual(original, decoded)
            final = json.loads((directory / "pcm.json").read_text())
            self.assertEqual(11, final["frames"])
            self.assertEqual(2.75, final["audio_seconds"])
            self.assertEqual(hashlib.sha256(original).hexdigest(), final["raw_sha256"])
            self.assertTrue(final["closed"])
            with self.assertRaises(RuntimeError):
                writer.write(b"\0\0")

    def test_raw_visible_before_chunk_and_odd_byte_is_preserved(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / "capture"
            writer = lecture_capture.PCMChunkWriter(path)
            writer.write(b"\1\0\2")
            self.assertEqual(b"\1\0\2", (path / "raw.pcm").read_bytes())
            self.assertEqual([], read_chunks(path))
            writer.close()
            self.assertEqual(1, writer.snapshot()["trailing_bytes"])
            with wave.open(str(Path(read_chunks(path)[0]["path"])), "rb") as wav:
                self.assertEqual(b"\1\0", wav.readframes(99))

    def test_recovery_reconstructs_chunks_without_changing_source(self):
        with tempfile.TemporaryDirectory() as base:
            source = Path(base) / "crashed.pcm"
            raw = struct.pack("<100h", *range(100)) + b"X"
            source.write_bytes(raw)
            output = Path(base) / "recovered"
            result = lecture_capture.recover_raw(source, output, sample_rate=10, chunk_seconds=3)
            self.assertEqual(raw, source.read_bytes())
            self.assertEqual(raw, (output / "raw.pcm").read_bytes())
            self.assertEqual(100, result["frames"])
            self.assertEqual(1, result["trailing_bytes"])
            self.assertEqual([30, 30, 30, 10], [c["frames"] for c in read_chunks(output)])
            with self.assertRaises(FileExistsError):
                lecture_capture.recover_raw(source, output)

    def test_existing_directory_and_invalid_parameters_refused(self):
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(FileExistsError):
                lecture_capture.PCMChunkWriter(base)
            for seconds in (0, -1, math.nan, math.inf, True):
                with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                    lecture_capture.PCMChunkWriter(Path(base) / "invalid", chunk_seconds=seconds)
            self.assertFalse((Path(base) / "invalid").exists())

    def test_wav_failure_keeps_raw_available_for_recovery(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / "capture"
            writer = lecture_capture.PCMChunkWriter(path, sample_rate=4, chunk_seconds=1)
            raw = struct.pack("<6h", *range(6))
            with mock.patch.object(writer, "_emit", side_effect=OSError("derived storage failed")):
                with self.assertRaises(OSError):
                    writer.write(raw)
                self.assertEqual(raw, (path / "raw.pcm").read_bytes())
                with self.assertRaises(OSError):
                    writer.close()
            self.assertTrue(writer.snapshot()["closed"])
            result = lecture_capture.recover_raw(path / "raw.pcm", Path(base) / "recovery",
                                                sample_rate=4, chunk_seconds=1)
            self.assertEqual(6, result["frames"])
            self.assertEqual(2, result["chunks"])

    def test_storage_full_after_short_raw_write_preserves_actual_bytes(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / 'capture'
            writer = lecture_capture.PCMChunkWriter(path, sample_rate=4, chunk_seconds=1)
            original = writer._raw
            class FullAfterPrefix:
                written = False
                def write(self, data):
                    if self.written:
                        raise OSError(errno.ENOSPC, 'synthetic disk full')
                    self.written = True
                    return original.write(data[:5])
                def fileno(self): return original.fileno()
                def close(self): return original.close()
            writer._raw = FullAfterPrefix()
            raw = b'\1\0\2\0\3\0\4\0'
            with self.assertRaises(OSError) as raised:
                writer.write(raw)
            self.assertEqual(errno.ENOSPC, raised.exception.errno)
            writer.close()
            self.assertEqual(raw[:5], (path / 'raw.pcm').read_bytes())
            metadata = json.loads((path / 'pcm.json').read_text())
            self.assertEqual(5, metadata['raw_bytes'])
            self.assertEqual(2, metadata['frames'])
            self.assertEqual(1, metadata['trailing_bytes'])
            self.assertEqual(hashlib.sha256(raw[:5]).hexdigest(), metadata['raw_sha256'])
            self.assertEqual([2], [chunk['frames'] for chunk in read_chunks(path)])

    def test_failed_fsync_does_not_publish_unconfirmed_progress(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / 'capture'
            writer = lecture_capture.PCMChunkWriter(path)
            writer.write(b'\1\0')
            raw_file, raw_fd = writer._raw, writer._raw.fileno()
            fsync = lecture_capture.os.fsync
            def fail_raw_sync(fd):
                if not raw_file.closed and fd == raw_fd:
                    raise OSError(errno.EIO, 'synthetic raw sync failure')
                return fsync(fd)
            with mock.patch.object(lecture_capture.os, 'fsync', fail_raw_sync):
                with self.assertRaises(OSError):
                    writer.write(b'\2\0')
                self.assertEqual(1, writer.snapshot()['frames'])
                with self.assertRaises(OSError):
                    writer.close()
            last_confirmed = writer.snapshot()
            self.assertEqual(1, last_confirmed['frames'])
            self.assertEqual(4, last_confirmed['observed_raw_bytes'])
            self.assertFalse(last_confirmed['storage_sync_confirmed'])
            metadata = json.loads((path / 'pcm.json').read_text())
            self.assertEqual(4, metadata['raw_bytes'])
            self.assertFalse(metadata['storage_sync_confirmed'])
            self.assertEqual(b'\1\0\2\0', (path / 'raw.pcm').read_bytes())


class DevicesTest(unittest.TestCase):
    def test_audio_list_only_and_nonzero_listing_exit(self):
        stderr = """[AVFoundation indev @ 0xab] AVFoundation video devices:
[AVFoundation indev @ 0xab] [0] Camera
[AVFoundation indev @ 0xab] AVFoundation audio devices:
[AVFoundation indev @ 0xab] [0] MacBook Pro Microphone
[AVFoundation indev @ 0xab] [1] External Mic
[in#0 @ 0xab] Error opening input: Input/output error
"""
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 1, "", stderr)

        self.assertEqual([{"id": "0", "name": "MacBook Pro Microphone"},
                          {"id": "1", "name": "External Mic"}],
                         lecture_capture.list_devices(runner=run, backend="ffmpeg"))
        self.assertEqual("", calls[0][0][-1])
        self.assertNotIn("shell", calls[0][1])

    def test_listing_failure_is_not_empty_success(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "Unknown input format: avfoundation")
        with self.assertRaises(RuntimeError):
            lecture_capture.list_devices(runner=run, backend="ffmpeg")

    def test_native_device_list_preserves_coreaudio_ids(self):
        def run(command, **kwargs):
            self.assertEqual(["/synthetic/native-helper", "--list-devices"], command)
            return subprocess.CompletedProcess(command, 0,
                json.dumps([{"id": "coreaudio:42", "name": "Synthetic Mic", "uid": "test"}]), "")
        devices = lecture_capture.list_devices(runner=run, native_helper="/synthetic/native-helper")
        self.assertEqual("coreaudio:42", devices[0]["id"])


@unittest.skipUnless(os.environ.get("LLT_RUN_NATIVE_TESTS") == "1" and
                     sys.platform == "darwin" and shutil.which("swiftc"),
                     "set LLT_RUN_NATIVE_TESTS=1 for optional macOS Swift synthetic tests")
class NativeSyntheticTest(unittest.TestCase):
    """Actual native conversion and queue tests; never opens a microphone."""

    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.helper = lecture_capture.ensure_native_helper(cache_dir=directory.name)

    def run_helper(self, *arguments):
        result = subprocess.run([str(self.helper), *arguments], capture_output=True, timeout=20, check=True)
        return result.stdout, json.loads(result.stderr)

    def test_sample_buffer_copy_preserves_audio_and_exact_resampled_duration(self):
        for rate in (16000, 44100, 48000):
            with self.subTest(rate=rate):
                direct, _ = self.run_helper("--self-test", str(rate), "2")
                pipeline, diagnostic = self.run_helper("--self-test-pipeline", str(rate), "2")
                self.assertEqual(32000 * 2, len(pipeline))
                self.assertEqual(direct, pipeline)
                self.assertEqual(rate * 2, diagnostic["queue"]["input_frames"])
                self.assertEqual(0, diagnostic["queue"]["sample_time_discontinuities"])
                self.assertEqual(0, diagnostic["queue"]["overflows"])
                self.assertAlmostEqual(2, diagnostic["queue"]["input_host_span_seconds"], places=7)

    def test_native_clock_gap_and_queue_overflow_are_observable_and_retained(self):
        output, diagnostic = self.run_helper("--self-test-queue")
        self.assertEqual(b"", output)
        self.assertEqual(5, diagnostic["discontinuity"]["missing_input_frames"])
        self.assertEqual(1, diagnostic["discontinuity"]["sample_time_discontinuities"])
        self.assertIn("error", diagnostic["discontinuity"])
        self.assertEqual(1, diagnostic["overflow"]["overflows"])
        self.assertEqual(128, diagnostic["overflow"]["queue_peak_buffers"])
        self.assertIn("error", diagnostic["overflow"])


class CaptureSessionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "capture"

    def factory(self, script, calls=None):
        def spawn(command, **kwargs):
            if calls is not None:
                calls.append((command, kwargs))
            return subprocess.Popen([sys.executable, "-u", "-c", script], **kwargs)
        return spawn

    def session(self, script, **kwargs):
        capture = lecture_capture.CaptureSession(
            self.path, process_factory=self.factory(script), backend="ffmpeg", **kwargs)
        self.addCleanup(capture.stop)
        return capture

    def wait_until(self, predicate, timeout=3):
        until = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= until:
                self.fail("timed out waiting for fake capture")
            time.sleep(0.01)

    def test_capture_has_fixed_audio_only_command_and_stop_preserves_tail(self):
        script = "import os,signal,time; signal.signal(signal.SIGINT, lambda *_: exit(0)); os.write(1, b'\\1\\0' * 2500); time.sleep(60)"
        chunks = []
        calls = []
        capture = lecture_capture.CaptureSession(
            self.path, device="2", chunk_seconds=0.1, on_chunk=chunks.append,
            process_factory=self.factory(script, calls), backend="ffmpeg")
        self.addCleanup(capture.stop)
        capture.start()
        self.wait_until(lambda: capture.snapshot()["frames"] == 2500)
        result = capture.stop()
        self.assertEqual("stopped", result["state"])
        self.assertTrue(result["stop_confirmed"])
        self.assertTrue(result["callbacks_confirmed"])
        self.assertEqual([1600, 900], [c["frames"] for c in chunks])
        self.assertEqual(b"\1\0" * 2500, (self.path / "raw.pcm").read_bytes())
        self.assertEqual("none:2", calls[0][0][calls[0][0].index("-i") + 1])
        self.assertIn("16000", calls[0][0])
        self.assertEqual("32", calls[0][0][calls[0][0].index("-probesize") + 1])
        self.assertEqual("1", calls[0][0][calls[0][0].index("-analyzeduration") + 1])
        self.assertEqual("1", calls[0][0][calls[0][0].index("-flush_packets") + 1])
        self.assertLess(calls[0][0].index("-analyzeduration"), calls[0][0].index("-i"))
        self.assertIsNotNone(result["first_audio_at"])
        self.assertGreaterEqual(result["startup_seconds"], 0)
        self.assertEqual(subprocess.DEVNULL, calls[0][1]["stdin"])
        self.assertTrue(json.loads((self.path / "capture.json").read_text())["stop_confirmed"])
        self.assertEqual(result["frames"], capture.stop()["frames"])

    def test_silence_stall_and_stop_are_distinct(self):
        wall, mono = [1000.0], [0.0]
        script = "import os,signal,time; signal.signal(signal.SIGINT, lambda *_: exit(0)); os.write(1, b'\\0\\0' * 100); time.sleep(60)"
        capture = self.session(script, clock=lambda: wall[0], monotonic=lambda: mono[0])
        capture.start()
        self.wait_until(lambda: capture.snapshot()["last_audio_at"] is not None)
        status = capture.snapshot()
        self.assertEqual("recording", status["state"])
        self.assertEqual("silent", status["signal"])
        self.assertEqual(-120.0, status["rms_dbfs"])
        self.assertEqual(1000.0, status["last_audio_at"])
        self.assertEqual(1000.0, status["first_audio_at"])
        self.assertEqual(0.0, status["startup_seconds"])
        mono[0] = 4
        self.assertEqual("stalled", capture.snapshot()["state"])
        self.assertEqual("unknown", capture.snapshot()["signal"])
        self.assertFalse(capture.snapshot()["stop_confirmed"])
        self.assertEqual("stopped", capture.stop()["state"])

    def test_startup_watchdog_stops_hung_input_and_keeps_flush(self):
        mono = [0.0]
        script = "import os,signal,time; signal.signal(signal.SIGINT, lambda *_: (os.write(1,b'\\1\\0'*17),exit(0))); os.write(2,b'ready'); time.sleep(60)"
        capture = self.session(script, monotonic=lambda: mono[0])
        capture.start()
        self.wait_until(lambda: capture.diagnostics_path.read_bytes() == b'ready')
        mono[0] = 44.9
        capture._check_input_timeout(mono[0])
        self.assertIsNone(capture.snapshot()['watchdog_error'])
        mono[0] = 45
        self.wait_until(lambda: not capture.snapshot()['reader_alive'])
        result = capture.stop()
        self.assertEqual('error', result['state'])
        self.assertIn('No initial PCM', result['error'])
        self.assertEqual(17, result['frames'])
        self.assertEqual(b'\1\0' * 17, (self.path / 'raw.pcm').read_bytes())
        self.assertTrue(result['stop_confirmed'])
        self.assertFalse(result['forced_termination'])
        self.assertEqual(0, result['returncode'])

    def test_input_watchdog_distinguishes_silent_frames_and_keeps_tail(self):
        mono = [0.0]
        script = "import os,signal,time; signal.signal(signal.SIGINT, lambda *_: (os.write(1,b'\\2\\0'*19),exit(0))); os.write(1,b'\\0\\0'*100); time.sleep(60)"
        capture = self.session(script, monotonic=lambda: mono[0])
        capture.start()
        self.wait_until(lambda: capture.snapshot()['frames'] == 100)
        self.assertEqual('silent', capture.snapshot()['signal'])
        mono[0] = 9.9
        capture._check_input_timeout(mono[0])
        self.assertIsNone(capture.snapshot()['watchdog_error'])
        mono[0] = 10
        self.wait_until(lambda: not capture.snapshot()['reader_alive'])
        result = capture.stop()
        self.assertEqual('error', result['state'])
        self.assertIn('PCM input stopped', result['watchdog_error'])
        self.assertEqual(119, result['frames'])
        self.assertEqual(b'\0\0' * 100 + b'\2\0' * 19, (self.path / 'raw.pcm').read_bytes())
        self.assertTrue(result['callbacks_confirmed'])
        self.assertFalse(result['forced_termination'])

    def test_watchdog_forces_unresponsive_child_after_flush_deadline(self):
        mono = [0.0]
        script = "import os,signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); os.write(1,b'\\1\\0'*100); time.sleep(60)"
        capture = self.session(script, monotonic=lambda: mono[0], stop_timeout=.25)
        capture.start()
        self.wait_until(lambda: capture.snapshot()['frames'] == 100)
        mono[0] = 10
        self.wait_until(lambda: capture.snapshot()['watchdog_error'] is not None)
        self.assertTrue(capture.snapshot()['reader_alive'])
        mono[0] = 10.3
        self.wait_until(lambda: not capture.snapshot()['reader_alive'])
        result = capture.stop()
        self.assertEqual('error', result['state'])
        self.assertTrue(result['forced_termination'])
        self.assertTrue(result['stop_confirmed'])
        self.assertEqual(100, result['frames'])

    def test_storage_error_stops_child_and_retains_written_raw(self):
        original = lecture_capture.PCMChunkWriter.write
        def full_after_write(writer, data):
            original(writer, data)
            raise OSError(errno.ENOSPC, 'synthetic full disk after PCM persistence')
        script = "import os,time; os.write(1,b'\\3\\0'*123); time.sleep(60)"
        with mock.patch.object(lecture_capture.PCMChunkWriter, 'write', full_after_write):
            capture = self.session(script)
            capture.start()
            self.wait_until(lambda: not capture.snapshot()['reader_alive'])
            result = capture.stop()
        self.assertEqual('error', result['state'])
        self.assertIn('OSError', result['error'])
        self.assertTrue(result['stop_confirmed'])
        self.assertEqual(b'\3\0' * 123, (self.path / 'raw.pcm').read_bytes())
        self.assertTrue(json.loads((self.path / 'pcm.json').read_text())['closed'])

    def test_blocked_fsync_keeps_status_responsive_and_progress_last_confirmed(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        script = "import os,signal,time; signal.signal(signal.SIGINT,lambda *_:exit(0)); os.write(1,b'\\1\\0'*100); time.sleep(.2); os.write(1,b'\\2\\0'*150); time.sleep(60)"
        capture = self.session(script, stop_timeout=.05)
        real_fsync = lecture_capture.os.fsync
        blocked = [False]
        def blocking_fsync(fd):
            if (threading.current_thread().name == 'lecture-capture' and not blocked[0]
                    and fd == capture._writer._raw.fileno() and capture._writer._raw_bytes > 200):
                blocked[0] = True
                entered.set()
                release.wait(5)
            return real_fsync(fd)
        with mock.patch.object(lecture_capture.os, 'fsync', blocking_fsync):
            capture.start()
            try:
                self.assertTrue(entered.wait(3))
                before = time.monotonic()
                status = capture.snapshot()
                self.assertLess(time.monotonic() - before, .2)
                self.assertEqual(100, status['frames'])
                self.assertTrue(status['reader_alive'])
                self.assertFalse(status['stop_confirmed'])
                before = time.monotonic()
                stopping = capture.stop()
                self.assertLess(time.monotonic() - before, .5)
                self.assertEqual(100, stopping['frames'])
                self.assertTrue(stopping['reader_alive'])
                self.assertFalse(stopping['stop_confirmed'])
                self.assertEqual('stopping', stopping['state'])
            finally:
                release.set()
                self.wait_until(lambda: not capture.snapshot()['reader_alive'])
            final = capture.stop()
        self.assertEqual(250, final['frames'])
        self.assertTrue(final['stop_confirmed'])
        self.assertEqual(b'\1\0' * 100 + b'\2\0' * 150, (self.path / 'raw.pcm').read_bytes())

    def test_zero_pcm_updates_deadline_and_invalid_watchdog_values_rejected(self):
        for field in ('startup_timeout', 'input_timeout'):
            for value in (0, -1, True, None, float('nan'), float('inf')):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    lecture_capture.CaptureSession(self.path, **{field: value})
        mono = [0.0]
        script = "import os,signal,time; signal.signal(signal.SIGINT, lambda *_: exit(0)); [(os.write(1,b'\\0\\0'*100),time.sleep(.03)) for _ in range(200)]"
        capture = self.session(script, monotonic=lambda: mono[0])
        capture.start()
        self.wait_until(lambda: capture.snapshot()['frames'] >= 100)
        for index in range(1, 5):
            previous = capture.snapshot()['frames']
            mono[0] = index * 2
            self.wait_until(lambda: capture.snapshot()['frames'] > previous)
            self.assertEqual('silent', capture.snapshot()['signal'])
            self.assertIsNone(capture.snapshot()['watchdog_error'])
        self.assertEqual('stopped', capture.stop()['state'])

    def test_unexpected_eof_error_retains_pcm_and_callbacks(self):
        capture = self.session("import os; os.write(1, b'\\1\\0' * 200)")
        capture.start()
        self.wait_until(lambda: capture.snapshot()["state"] == "error")
        result = capture.stop()
        self.assertIn("ended unexpectedly", result["error"])
        self.assertEqual(200, result["frames"])
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual(200, read_chunks(self.path)[0]["frames"])

    def test_callback_block_cannot_stop_capture_and_stop_reports_pending(self):
        release = threading.Event()
        entered = threading.Event()
        self.addCleanup(release.set)

        def slow(chunk):
            entered.set()
            release.wait(3)

        script = "import os,signal,time; signal.signal(signal.SIGINT, lambda *_: exit(0)); os.write(1, b'\\1\\0' * 6400); time.sleep(60)"
        capture = self.session(script, chunk_seconds=0.1, on_chunk=slow, stop_timeout=0.1)
        capture.start()
        self.assertTrue(entered.wait(2))
        self.wait_until(lambda: capture.snapshot()["frames"] == 6400)
        result = capture.stop()
        self.assertEqual(4, result["chunks"])
        self.assertTrue(result["stop_confirmed"])
        self.assertFalse(result["callbacks_confirmed"])
        self.assertEqual(3, result["pending_chunks"])
        release.set()
        self.wait_until(lambda: capture.snapshot()["callbacks_confirmed"])

    def test_ignored_sigint_forced_stop_is_explicit(self):
        script = "import os,signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); os.write(1, b'\\1\\0'); time.sleep(60)"
        capture = self.session(script, stop_timeout=0.1)
        capture.start()
        self.wait_until(lambda: capture.snapshot()["frames"] == 1)
        result = capture.stop()
        self.assertTrue(result["forced_termination"])
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual("error", result["state"])
        self.assertEqual(1, read_chunks(self.path)[0]["frames"])

    def test_start_failure_is_observable_and_empty_raw_preserved(self):
        def fail(*args, **kwargs):
            raise FileNotFoundError("ffmpeg unavailable")
        capture = lecture_capture.CaptureSession(self.path, process_factory=fail, backend="ffmpeg")
        with self.assertRaises(FileNotFoundError):
            capture.start()
        self.assertEqual("error", capture.snapshot()["state"])
        self.assertTrue(capture.stop()["stop_confirmed"])
        self.assertEqual(b"", (self.path / "raw.pcm").read_bytes())

    def test_existing_output_refused_before_process_or_device_open(self):
        self.path.mkdir()
        started = []
        capture = lecture_capture.CaptureSession(self.path, process_factory=lambda *a, **k: started.append(True), backend="ffmpeg")
        with self.assertRaises(FileExistsError):
            capture.start()
        self.assertEqual([], started)

    def test_callback_failure_does_not_stop_persistence(self):
        def fail(chunk):
            raise RuntimeError("consumer error")
        capture = self.session("import os; os.write(1, b'\\1\\0' * 3200)",
                               chunk_seconds=0.1, on_chunk=fail)
        capture.start()
        self.wait_until(lambda: capture.snapshot()["callbacks_confirmed"])
        result = capture.stop()
        self.assertEqual(3200, result["frames"])
        self.assertEqual(2, result["callback_errors"])

    def test_partial_pcm_frame_is_error_with_original_byte_preserved(self):
        capture = self.session("import os,signal,time; signal.signal(signal.SIGINT, lambda *_: exit(0)); os.write(1, b'\\1\\0X'); time.sleep(60)")
        capture.start()
        self.wait_until(lambda: capture.snapshot()["raw_bytes"] == 3)
        result = capture.stop()
        self.assertEqual("error", result["state"])
        self.assertIn("incomplete PCM frame", result["error"])
        self.assertEqual(b"\1\0X", (self.path / "raw.pcm").read_bytes())

    def test_stop_before_start_does_not_open_microphone(self):
        called = []
        capture = lecture_capture.CaptureSession(self.path, process_factory=lambda *a, **k: called.append(1), backend="ffmpeg")
        self.assertEqual("stopped", capture.stop()["state"])
        self.assertEqual([], called)
        self.assertFalse(self.path.exists())
        with self.assertRaises(RuntimeError):
            capture.start()

    def test_native_is_default_and_diagnostics_error_is_preserved(self):
        calls = []
        script = "import os,signal,time; signal.signal(signal.SIGINT, lambda *_: exit(0)); os.write(1,b'\\1\\0'*100); os.write(2,b'{\"event\":\"stopped\",\"error\":\"Input sample clock discontinuity\",\"missing_input_frames\":128}\\n'); time.sleep(60)"
        capture = lecture_capture.CaptureSession(self.path, device="coreaudio:42",
            native_helper="/synthetic/native-helper", process_factory=self.factory(script, calls))
        self.addCleanup(capture.stop)
        capture.start()
        self.wait_until(lambda: capture.snapshot()["frames"] == 100)
        result = capture.stop()
        self.assertEqual(["/synthetic/native-helper", "--capture", "--device", "coreaudio:42"], calls[0][0])
        self.assertEqual("native", result["backend"])
        self.assertEqual("error", result["state"])
        self.assertEqual(128, result["native_diagnostics"]["missing_input_frames"])
        self.assertEqual(100, read_chunks(self.path)[0]["frames"])
        self.assertEqual("/synthetic/native-helper", result["native_helper"]["path"])
        self.assertIsNone(result["native_helper"]["identity"])
        self.assertEqual("FileNotFoundError", result["native_helper"]["metadata_read_error"])

    def test_native_helper_provenance_is_frozen_and_durable(self):
        helper = Path(self.directory.name) / "cached-helper"
        helper.write_bytes(b"synthetic executable; process_factory supplies the test process")
        metadata = {"identity": "synthetic-build", "source_sha256": "a" * 64,
                    "binary_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
                    "compiler_version": "Synthetic Swift"}
        build_path = helper.with_name("build.json")
        build_path.write_text(json.dumps(metadata))
        calls = []
        script = "import os,signal,time; signal.signal(signal.SIGINT, lambda *_: exit(0)); os.write(1,b'\\1\\0'*100); time.sleep(60)"
        capture = lecture_capture.CaptureSession(self.path, native_helper=helper,
            process_factory=self.factory(script, calls))
        self.addCleanup(capture.stop)
        capture.start()
        self.wait_until(lambda: capture.snapshot()["frames"] == 100)
        observed = capture.snapshot()["native_helper"]
        self.assertEqual(str(helper.resolve()), observed["path"])
        self.assertEqual(str(build_path.resolve()), observed["build_metadata_path"])
        self.assertEqual(str(helper.resolve()), calls[0][0][0])
        for key, value in metadata.items():
            self.assertEqual(value, observed[key])
        self.assertEqual(metadata["binary_sha256"], observed["observed_binary_sha256"])
        self.assertTrue(observed["binary_matches_metadata"])
        # Later cache changes cannot rewrite the identity used by this attempt.
        build_path.write_text('{"identity":"different-build"}')
        observed["identity"] = "caller-mutated-snapshot"
        final = capture.stop()
        self.assertEqual("synthetic-build", final["native_helper"]["identity"])
        persisted = json.loads((self.path / "capture.json").read_text())
        self.assertEqual(final["native_helper"], persisted["native_helper"])


if __name__ == "__main__":
    unittest.main()
