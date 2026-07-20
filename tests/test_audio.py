import struct
import contextlib
import io
import queue
import threading
import unittest

import numpy as np

from audio import AudioPlayer, PCMConverter, PlaybackItem, SpeechTempoConverter
from playback_profiles import playback_profile


class PCMConverterTests(unittest.TestCase):
    def test_24k_mono_to_24k_stereo_duration(self):
        mono = b"".join(struct.pack("<h", 500) for _ in range(2_400))
        output = PCMConverter(24_000, 24_000, 1, 2).convert(mono)
        self.assertEqual(len(output) // 4, 2_400)

    def test_48k_stereo_to_16k_mono_duration(self):
        frames = 4_800  # 100 ms at 48 kHz
        stereo = b"".join(struct.pack("<hh", 1000, 1000) for _ in range(frames))
        output = PCMConverter(48_000, 16_000, 2, 1).convert(stereo)
        output_frames = len(output) // 2
        self.assertTrue(1_595 <= output_frames <= 1_605)

    def test_24k_mono_to_48k_stereo_duration(self):
        mono = b"".join(struct.pack("<h", 500) for _ in range(2_400))
        output = PCMConverter(24_000, 48_000, 1, 2).convert(mono)
        output_frames = len(output) // 4
        self.assertTrue(4_795 <= output_frames <= 4_805)


class SpeechTempoConverterTests(unittest.TestCase):
    def test_110_percent_speed_preserves_pitch(self):
        rate = 24_000
        duration_seconds = 5
        timeline = np.arange(rate * duration_seconds) / rate
        source = 0.25 * np.sin(2 * np.pi * 220 * timeline)
        pcm = np.clip(source * 32768, -32768, 32767).astype("<i2").tobytes()

        converter = SpeechTempoConverter(1.10)
        chunk_bytes = rate // 10 * 2
        output_parts = [
            converter.convert(pcm[offset:offset + chunk_bytes])
            for offset in range(0, len(pcm), chunk_bytes)
        ]
        output_parts.append(converter.flush())
        output = b"".join(output_parts)

        effective_speed = len(pcm) / len(output)
        samples = np.frombuffer(output, dtype="<i2").astype(np.float32)
        frequencies = np.fft.rfftfreq(len(samples), 1 / rate)
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        peak_frequency = frequencies[np.argmax(spectrum)]

        self.assertTrue(1.09 <= effective_speed <= 1.12)
        self.assertAlmostEqual(220, peak_frequency, delta=2)

    def test_speed_limits(self):
        for speed in (0.99, 1.26):
            with self.assertRaises(ValueError):
                SpeechTempoConverter(speed)

    def test_speed_can_change_without_recreating_the_stream(self):
        converter = SpeechTempoConverter(1.08)
        original_tsm = converter._tsm
        converter.set_speed(1.10)
        self.assertIs(converter._tsm, original_tsm)
        self.assertEqual(converter.speed, 1.10)


def player_without_audio_device(profile_code: str) -> AudioPlayer:
    player = object.__new__(AudioPlayer)
    player.profile = playback_profile(profile_code)
    player.normal_speed = player.profile.normal_speed
    player.catchup_speed = player.profile.catchup_speed
    player.current_speed = player.normal_speed
    player.queue = queue.Queue()
    player.queued_bytes = 0
    player.pending_source_bytes = 0
    player.generation = 0
    player.queue_lock = threading.Lock()
    player.output_lock = threading.Lock()
    player.output_buffer = bytearray()
    player.playback_ready = False
    player.turn_audio_active = False
    player.turn_end_requested = False
    player.starving = False
    player.underflow_count = 0
    player.device_underflow_count = 0
    player.target_buffer_ms = player.profile.start_buffer_ms
    player.last_underflow_at = 0.0
    player.last_buffer_recovery_at = 0.0
    player.backlog_warning_emitted = False
    player.last_buffer_warning = ""
    return player


class AudioPlayerProfileTests(unittest.TestCase):
    def test_smooth_profile_keeps_normal_translation_backlog(self):
        player = player_without_audio_device("balanced-smooth")
        existing = b"\x01\0" * (24_000 * 9)
        player.queue.put_nowait(PlaybackItem(0, existing))
        player.queued_bytes = len(existing)

        extra = b"\x02\0" * 24_000
        with contextlib.redirect_stdout(io.StringIO()):
            player.play(extra)

        self.assertEqual(player.generation, 0)
        self.assertEqual(player.queued_bytes, len(existing) + len(extra))
        self.assertEqual(player.queue.qsize(), 2)

    def test_smooth_speed_protects_low_buffer_and_catches_up_when_high(self):
        player = player_without_audio_device("balanced-smooth")
        self.assertEqual(player._speed_for_backlog(400), 1.0)
        self.assertEqual(player._speed_for_backlog(1_500), 1.08)
        self.assertEqual(player._speed_for_backlog(3_000), 1.10)

    def test_underflow_increases_rebuffer_target(self):
        player = player_without_audio_device("balanced-smooth")
        player.playback_ready = True
        player._register_underflow()

        self.assertFalse(player.playback_ready)
        self.assertTrue(player.starving)
        self.assertEqual(player.underflow_count, 1)
        self.assertEqual(player.target_buffer_ms, 1_650)


if __name__ == "__main__":
    unittest.main()
