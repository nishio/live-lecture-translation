from pathlib import Path
from contextlib import contextmanager
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transcribe_local import pause_chunks, read_mono, sha256, transcribe_file


class LocalAsrTest(unittest.TestCase):
    def test_pause_cut_preserves_every_sample_including_quiet_edges(self):
        rng = np.random.default_rng(43)
        audio = np.r_[np.zeros(16000), rng.normal(0, .05, 32000), np.zeros(9600),
                      rng.normal(0, .05, 48000), np.zeros(16000)].astype(np.float32)
        chunks, detector = pause_chunks(audio, threshold_dbfs=-50)
        self.assertEqual([(0, 52800), (52800, len(audio))], chunks)
        np.testing.assert_array_equal(np.concatenate([audio[a:b] for a, b in chunks]), audio)
        self.assertEqual(48000, detector["candidates"][0]["quiet_start_frame"])
        self.assertEqual(57600, detector["candidates"][0]["quiet_end_frame"])

    def test_no_pause_is_not_a_license_to_drop_audio(self):
        audio = np.full(48000, .05, dtype=np.float32)
        chunks, _ = pause_chunks(audio, threshold_dbfs=-50)
        self.assertEqual([(0, len(audio))], chunks)
        chunks, _ = pause_chunks(np.zeros(48000, dtype=np.float32))
        self.assertEqual([(0, 48000)], chunks)

    def test_short_pause_and_invalid_detector_parameters(self):
        audio = np.r_[np.full(32000, .05), np.zeros(3200), np.full(32000, .05)]
        chunks, _ = pause_chunks(audio, threshold_dbfs=-50)
        self.assertEqual([(0, len(audio))], chunks)
        for options in ({"threshold_dbfs": float("nan")}, {"minimum_pause": 0}, {"minimum_chunk": -1}):
            with self.assertRaises(ValueError):
                pause_chunks(audio, **options)

    def test_only_bounded_16k_mono_pcm_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.wav"
            for rate, channels in [(48000, 1), (16000, 2), (16000, 1)]:
                with wave.open(str(path), "wb") as stream:
                    stream.setnchannels(channels); stream.setframerate(rate); stream.setsampwidth(2)
                    stream.writeframes(b"\x01\x00" * 16000 * channels)
                if rate == 16000 and channels == 1:
                    self.assertEqual(16000, len(read_mono(path)))
                else:
                    with self.assertRaisesRegex(ValueError, "16kHz mono"):
                        read_mono(path)

    def test_whole_decode_keeps_context_and_every_pcm_sample_across_internal_pauses(self):
        # Two close pauses surround a short speech span, as in the observed replay failure.
        # Check the exact samples reaching the decoder, not only the reported boundaries.
        pcm = np.r_[np.full(160000, 1200), np.zeros(12800), np.full(32000, -2300),
                    np.zeros(12800), np.full(240000, 3400)].astype('<i2')
        pcm[159999], pcm[172800], pcm[204799], pcm[217600] = 123, -456, 789, -987
        captured = []
        active_slots = []
        slot_calls = []

        @contextmanager
        def slot(label):
            slot_calls.append(label)
            active_slots.append(label)
            try:
                yield
            finally:
                active_slots.remove(label)

        def fake_decode(audio, **options):
            self.assertEqual(['asr'], active_slots)
            captured.append(audio.copy())
            return {'text': 'Synthetic decoder result.', 'language': 'en', 'segments': [
                {'start': 0, 'end': len(audio) / 16000, 'text': 'Synthetic decoder result.'}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav = root / 'context.wav'
            with wave.open(str(wav), 'wb') as stream:
                stream.setnchannels(1); stream.setframerate(16000); stream.setsampwidth(2)
                stream.writeframes(pcm.tobytes())
            source_hash = sha256(wav)
            model = root / 'model'
            model.mkdir()
            (model / 'config.json').write_text('{}')
            (model / 'weights.npz').write_bytes(b'not a real model; decoder is mocked')
            metadata = root / 'model.json'
            metadata.write_text(json.dumps({'repo': 'synthetic-test', 'local_path': str(model)}))
            with patch.dict(sys.modules, {'mlx_whisper': SimpleNamespace(transcribe=fake_decode)}), \
                    patch('transcribe_local.importlib.metadata.version', return_value='test'), \
                    patch('transcribe_local.inference_slot', side_effect=slot), \
                    patch.dict('os.environ', {}):
                whole = transcribe_file(wav, metadata, root / 'whole.json', mode='whole', language='en')
                self.assertEqual(1, len(captured))
                np.testing.assert_array_equal(pcm, (captured[0] * 32768).astype('<i2'))
                self.assertEqual((0, len(pcm)), (whole['chunks'][0]['source_start_frame'],
                                                whole['chunks'][0]['source_end_frame']))
                captured.clear()
                pauses = transcribe_file(wav, metadata, root / 'pauses.json', mode='pauses',
                                         threshold_dbfs=-50, language='en')
                self.assertGreater(len(captured), 1)
                self.assertTrue(any(len(chunk) < 3 * 16000 for chunk in captured))
                np.testing.assert_array_equal(pcm, (np.concatenate(captured) * 32768).astype('<i2'))
                self.assertEqual(len(pcm), sum(c['source_end_frame'] - c['source_start_frame']
                                              for c in pauses['chunks']))
                self.assertEqual(['asr', 'asr'], slot_calls)
                self.assertEqual([], active_slots)
                self.assertGreaterEqual(pauses['inference_queue_wait_seconds'], 0)
                with patch.dict(sys.modules, {'mlx_whisper': SimpleNamespace(
                        transcribe=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('decoder failed')))}), \
                        self.assertRaisesRegex(RuntimeError, 'decoder failed'):
                    transcribe_file(wav, metadata, root / 'failed.json', mode='whole', language='en')
                self.assertEqual([], active_slots)
                self.assertFalse((root / 'failed.json').exists())
            self.assertEqual(source_hash, sha256(wav))
