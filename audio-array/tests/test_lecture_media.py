import hashlib
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_media as media


class MediaFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.audio = self.root / 'synthetic audio.wav'
        self.write_audio(self.audio)

    def write_audio(self, path, frames=16000, channels=1, rate=16000, width=2):
        sample = struct.pack('<h', 1200) if width == 2 else b'\x80' * width
        with wave.open(str(path), 'wb') as audio:
            audio.setparams((channels, width, rate, 0, 'NONE', 'not compressed'))
            audio.writeframes(sample * frames * channels)

class MediaTest(MediaFixture, unittest.TestCase):
    def test_canonical_wav_retains_identity_and_bytes_without_decoder(self):
        original = self.audio.read_bytes()
        with patch.object(media, 'find_ffmpeg', side_effect=AssertionError('decoder unnecessary')):
            with media.prepared_audio(self.audio) as prepared:
                self.assertEqual(str(self.audio), prepared['input']['path'])
                self.assertEqual(hashlib.sha256(original).hexdigest(), prepared['source']['sha256'])
                self.assertEqual(prepared['source']['sha256'], prepared['input']['sha256'])
                self.assertEqual(16000, prepared['input']['selected_frames'])
                self.assertEqual({'required': False, 'requested_seconds': None, 'engine': 'none'},
                                 prepared['conversion'])
        self.assertEqual(original, self.audio.read_bytes())

    def test_prefix_is_exact_and_temporary_and_deterministic(self):
        hashes = []
        for _ in range(2):
            with media.prepared_audio(self.audio, seconds=.12349) as prepared:
                output = Path(prepared['input']['path'])
                hashes.append(prepared['input']['sha256'])
                self.assertNotEqual(self.audio, output)
                self.assertEqual(1975, prepared['input']['frames'])
                self.assertEqual(1975, prepared['input']['selected_frames'])
                self.assertEqual(1975 / 16000, prepared['input']['selected_seconds'])
                self.assertEqual('python-wave', prepared['conversion']['engine'])
                self.assertEqual(44 + 1975 * 2, output.stat().st_size)
                self.assertTrue(output.exists())
            self.assertFalse(output.exists())
            self.assertFalse(output.parent.exists())
        self.assertEqual(hashes[0], hashes[1])
        self.assertEqual(16000, media.inspect_pcm_audio(self.audio)['frames'])

    def test_overlong_prefix_keeps_full_canonical_file(self):
        with media.prepared_audio(self.audio, seconds=500) as prepared:
            self.assertEqual(str(self.audio), prepared['input']['path'])
            self.assertEqual(1, prepared['input']['selected_seconds'])
            self.assertFalse(prepared['conversion']['required'])

    def test_inspect_preserves_full_file_and_selected_duration(self):
        checked = media.inspect_pcm_audio(self.audio, seconds=.25)
        self.assertEqual(1, checked['duration_seconds'])
        self.assertEqual(.25, checked['selected_seconds'])
        self.write_audio(self.audio, frames=1001)
        checked = media.inspect_pcm_audio(self.audio)
        self.assertEqual(checked, media.inspect_pcm_audio(self.audio, checked['selected_seconds']))

    def test_invalid_duration_and_non_regular_file_are_rejected(self):
        for seconds in (0, -1, math.nan, math.inf, .000001, 'bad'):
            with self.subTest(seconds=seconds), self.assertRaises(media.MediaError):
                with media.prepared_audio(self.audio, seconds=seconds):
                    self.fail('invalid duration was accepted')
        for source in (self.root, self.root / 'missing', 'https://example.com/audio.mp3'):
            with self.subTest(source=source), self.assertRaises(media.MediaError):
                with media.prepared_audio(source):
                    self.fail('non-local-file input was accepted')

    def test_empty_and_truncated_wav_are_not_repaired_even_for_short_prefix(self):
        for channels in (1, 2):
            self.write_audio(self.audio, channels=channels)
            self.audio.write_bytes(self.audio.read_bytes()[:-8])
            with patch.object(media, 'find_ffmpeg', side_effect=AssertionError('truncated WAV decoded')):
                with self.assertRaisesRegex(media.MediaError, 'truncated'):
                    with media.prepared_audio(self.audio, seconds=.1):
                        pass
        self.write_audio(self.audio, frames=0)
        with self.assertRaisesRegex(media.MediaError, 'no audio'):
            with media.prepared_audio(self.audio):
                pass

    def test_temp_cleanup_when_caller_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'caller failed'):
            with media.prepared_audio(self.audio, seconds=.25) as prepared:
                temporary = Path(prepared['input']['path'])
                raise RuntimeError('caller failed')
        self.assertFalse(temporary.parent.exists())

    def test_source_mutation_after_yield_is_rejected_and_temp_removed(self):
        with self.assertRaisesRegex(media.MediaError, 'changed'):
            with media.prepared_audio(self.audio, seconds=.25) as prepared:
                temporary = Path(prepared['input']['path'])
                self.write_audio(self.audio, frames=8000)
        self.assertFalse(temporary.parent.exists())

    def test_source_replacement_with_identical_bytes_is_rejected(self):
        with self.assertRaisesRegex(media.MediaError, 'changed'):
            with media.prepared_audio(self.audio):
                replacement = self.root / 'replacement.wav'
                replacement.write_bytes(self.audio.read_bytes())
                replacement.replace(self.audio)

    def test_source_mutation_during_preparation_is_rejected(self):
        original_copy = media._copy_pcm

        def mutate(source, destination, frames):
            original_copy(source, destination, frames)
            self.write_audio(source, frames=8000)

        with patch.object(media, '_copy_pcm', side_effect=mutate):
            with self.assertRaisesRegex(media.MediaError, 'changed'):
                with media.prepared_audio(self.audio, seconds=.25):
                    self.fail('mutated input was published')

    def test_decoder_resolution_prefers_bundle_and_missing_dependency_is_actionable(self):
        class Bundle:
            @staticmethod
            def get_ffmpeg_exe():
                return sys.executable

        with patch.dict(sys.modules, {'imageio_ffmpeg': Bundle}), \
                patch.object(media.shutil, 'which', side_effect=AssertionError('system fallback used')):
            self.assertEqual(sys.executable, media.find_ffmpeg())
        with patch.dict(sys.modules, {'imageio_ffmpeg': None}), patch.object(media.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(media.MediaError, r'setup\.command'):
                media.find_ffmpeg()


class DecoderTest(MediaFixture, unittest.TestCase):
    # Decoder integration uses only short synthetic local files. It invokes no
    # model, microphone, network service or paid API.
    def setUp(self):
        super().setUp()
        try:
            self.decoder = media.find_ffmpeg()
        except media.MediaError:
            self.skipTest('ffmpeg is unavailable; synthetic decoder integration needs setup.command')

    def encode(self, destination, *options):
        result = subprocess.run([self.decoder, '-hide_banner', '-loglevel', 'error', '-nostdin',
                                 '-i', str(self.audio), *options, '-y', str(destination)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors='replace'))

    def test_mp3_m4a_and_video_convert_locally_with_exact_prefix(self):
        specifications = (('speech with spaces.mp3', ['-c:a', 'libmp3lame']),
                          ('speech.m4a', ['-c:a', 'aac']),
                          ('speech.mp4', ['-c:a', 'aac']))
        for filename, options in specifications:
            with self.subTest(filename=filename):
                source = self.root / filename
                self.encode(source, *options)
                original = source.read_bytes()
                with media.prepared_audio(source, seconds=.345) as prepared:
                    self.assertEqual(5520, prepared['input']['frames'])
                    self.assertEqual(5520, prepared['input']['selected_frames'])
                    self.assertEqual('ffmpeg', prepared['conversion']['engine'])
                    self.assertIn('ffmpeg version', prepared['conversion']['ffmpeg_version'])
                    self.assertEqual(hashlib.sha256(original).hexdigest(), prepared['source']['sha256'])
                    self.assertEqual(44 + 5520 * 2, prepared['input']['file_bytes'])
                self.assertEqual(original, source.read_bytes())

    def test_video_first_audio_track_only(self):
        video = self.root / 'video and two sound tracks.mp4'
        command = [self.decoder, '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-f', 'lavfi', '-i', 'color=c=black:s=16x16:d=1:r=2',
                   '-i', str(self.audio), '-f', 'lavfi', '-i', 'sine=frequency=880:duration=1',
                   '-map', '0:v:0', '-map', '1:a:0', '-map', '2:a:0', '-c:v', 'mpeg4',
                   '-c:a', 'aac', '-shortest', '-y', str(video)]
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors='replace'))
        with media.prepared_audio(video, seconds=.5) as prepared:
            with wave.open(prepared['input']['path'], 'rb') as audio:
                values = struct.unpack('<8000h', audio.readframes(8000))
                # The first track is synthetic constant PCM; the second is a
                # zero-mean tone. This establishes explicit first-track mapping.
                self.assertGreater(sum(values) / len(values), 700)

    def test_noncanonical_pcm_converts_and_has_stable_bytes(self):
        self.write_audio(self.audio, channels=2, rate=44100, frames=44100)
        hashes = []
        for _ in range(2):
            with media.prepared_audio(self.audio) as prepared:
                hashes.append(prepared['input']['sha256'])
                self.assertEqual(16000, prepared['input']['frames'])
                self.assertEqual(1, prepared['input']['channels'])
                self.assertEqual('ffmpeg', prepared['conversion']['engine'])
        self.assertEqual(hashes[0], hashes[1])

    def test_malformed_and_no_audio_are_rejected(self):
        malformed = self.root / 'broken.mp3'
        malformed.write_bytes(b'This is not an audio file.')
        with self.assertRaisesRegex(media.MediaError, 'Cannot decode'):
            with media.prepared_audio(malformed):
                pass
        video = self.root / 'silent-video.mp4'
        result = subprocess.run([self.decoder, '-hide_banner', '-loglevel', 'error', '-nostdin',
                                 '-f', 'lavfi', '-i', 'color=c=black:s=16x16:d=0.5:r=2',
                                 '-c:v', 'mpeg4', '-y', str(video)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors='replace'))
        with self.assertRaisesRegex(media.MediaError, 'no audio'):
            with media.prepared_audio(video):
                pass

    def test_playlists_cannot_open_other_files(self):
        for filename, content in (
                ('list.ffconcat', f"ffconcat version 1.0\nfile '{self.audio}'\n"),
                ('list.m3u8', f'#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n{self.audio}\n#EXT-X-ENDLIST\n')):
            playlist = self.root / filename
            playlist.write_text(content)
            with self.subTest(filename=filename), self.assertRaisesRegex(media.MediaError, 'Cannot decode'):
                with media.prepared_audio(playlist):
                    pass


if __name__ == '__main__':
    unittest.main()
