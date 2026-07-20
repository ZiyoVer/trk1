import asyncio
import unittest

from translator import (
    DEFAULT_VOICE,
    MODEL,
    PROVIDER,
    Translator,
    build_live_config,
    build_parser,
    duplex_channel_args,
)


class TranslatorConfigTests(unittest.TestCase):
    def test_charon_is_the_default_voice_for_every_target(self) -> None:
        parser = build_parser()
        for target in ("en", "uz", "ru", "es"):
            args = parser.parse_args(["--target-language", target])
            self.assertEqual(args.voice, DEFAULT_VOICE)
            self.assertEqual(args.voice, "Charon")

    def test_quality_playback_uses_slightly_faster_natural_speed(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.speech_speed, 1.08)
        self.assertEqual(args.playback_profile, "balanced-smooth")

    def test_company_provider_is_google_gemini(self) -> None:
        self.assertEqual(PROVIDER, "google")
        self.assertEqual(MODEL, "gemini-3.5-live-translate-preview")

    def test_live_config_targets_english_output(self) -> None:
        args = build_parser().parse_args(
            ["--source-language", "uz", "--target-language", "en"]
        )
        config = build_live_config(args)
        self.assertEqual(config.response_modalities, ["AUDIO"])
        self.assertEqual(config.translation_config.target_language_code, "en")
        self.assertFalse(config.translation_config.echo_target_language)
        self.assertIsNotNone(config.input_audio_transcription)
        self.assertIsNotNone(config.output_audio_transcription)

    def test_live_config_targets_uzbek_in_meeting_mode(self) -> None:
        args = build_parser().parse_args([])
        config = build_live_config(args)
        self.assertEqual(config.translation_config.target_language_code, "uz")
        self.assertEqual(args.model, MODEL)

    def test_google_audio_is_sent_as_16khz_pcm(self) -> None:
        sample = b"\x01\x00\x02\x00" * 800

        async def scenario() -> list[object]:
            translator = object.__new__(Translator)
            translator.stop_event = asyncio.Event()
            translator.audio_queue = asyncio.Queue()
            translator.input_bytes = 0
            translator.args = build_parser().parse_args([])
            translator._log = lambda _message: None
            await translator.audio_queue.put(sample)
            calls: list[object] = []

            class Session:
                async def send_realtime_input(self, *, audio) -> None:  # noqa: ANN001
                    calls.append(audio)
                    translator.stop_event.set()

            await translator._send_google_audio(Session())
            self.assertEqual(translator.input_bytes, len(sample))
            return calls

        calls = asyncio.run(scenario())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].data, sample)
        self.assertEqual(calls[0].mime_type, "audio/pcm;rate=16000")


    def test_duplex_keeps_two_independent_language_and_audio_routes(self) -> None:
        args = build_parser().parse_args(
            [
                "--duplex",
                "--incoming-source-language", "auto",
                "--incoming-target-language", "uz",
                "--incoming-input-device", "0",
                "--incoming-output-device", "2",
                "--outgoing-source-language", "uz",
                "--outgoing-target-language", "en",
                "--outgoing-input-device", "1",
                "--outgoing-output-device", "3",
            ]
        )
        incoming = duplex_channel_args(args, "incoming")
        outgoing = duplex_channel_args(args, "outgoing")
        self.assertEqual(
            (incoming.source_language, incoming.target_language, incoming.input_device, incoming.output_device),
            ("auto", "uz", "0", "2"),
        )
        self.assertEqual(
            (outgoing.source_language, outgoing.target_language, outgoing.input_device, outgoing.output_device),
            ("uz", "en", "1", "3"),
        )


if __name__ == "__main__":
    unittest.main()
