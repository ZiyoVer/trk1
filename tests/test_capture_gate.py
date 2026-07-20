import unittest

from translator import CaptureGate


class _FakePlayer:
    def __init__(self) -> None:
        self.audio = False

    def has_audio(self) -> bool:
        return self.audio


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class CaptureGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.player = _FakePlayer()
        self.clock = _FakeClock()
        self.gate = CaptureGate(lambda: self.player, clock=self.clock)

    def test_drops_while_translation_audio_is_playing(self) -> None:
        self.player.audio = True
        self.assertTrue(self.gate.should_drop())

    def test_keeps_dropping_for_tail_after_playback_ends(self) -> None:
        self.player.audio = True
        self.gate.should_drop()
        self.player.audio = False
        # Karnay so'nishi va yozuv kechikishi davrida ham yopiq turadi.
        self.clock.now += CaptureGate.TAIL_SECONDS / 2
        self.assertTrue(self.gate.should_drop())

    def test_reopens_after_tail_expires(self) -> None:
        self.player.audio = True
        self.gate.should_drop()
        self.player.audio = False
        self.clock.now += CaptureGate.TAIL_SECONDS + 0.01
        self.assertFalse(self.gate.should_drop())

    def test_open_when_no_player(self) -> None:
        gate = CaptureGate(lambda: None, clock=self.clock)
        self.assertFalse(gate.should_drop())

    def test_open_when_idle(self) -> None:
        self.assertFalse(self.gate.should_drop())

    def test_stuck_player_cannot_deafen_the_microphone_forever(self) -> None:
        # v0.7.4 regressiyasi: player "audio bor" holatida qotib qolsa
        # mikrofon butun sessiya davomida yopiq qolardi.
        self.player.audio = True
        self.assertTrue(self.gate.should_drop())
        self.clock.now += CaptureGate.MAX_BLOCK_SECONDS + 0.1
        self.assertFalse(self.gate.should_drop(), "gate majburan ochilishi kerak")

    def test_normal_playback_still_gates_within_limit(self) -> None:
        self.player.audio = True
        self.gate.should_drop()
        self.clock.now += CaptureGate.MAX_BLOCK_SECONDS / 2
        self.assertTrue(self.gate.should_drop())


if __name__ == "__main__":
    unittest.main()
