"""Real-time multilingual voice translator using the EDCOM voice gateway."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import sys
import tempfile
import time
from contextlib import suppress
from urllib.parse import quote

from dotenv import load_dotenv
from google import genai
from google.genai import types
import websockets

from audio import (
    AudioCapture,
    AudioPlayer,
    auto_input_device,
    auto_output_device,
    list_devices,
)
from audio_routing import virtual_device_family

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # macOS/Linux
    msvcrt = None  # type: ignore[assignment]


MODEL = "gemini-3.5-live-translate-preview"
DEFAULT_VOICE = "Charon"
EDCOM_WS_URL = "wss://rag-api.edcom.uz/chat/voice/ws"
ENGINE_LOCK_PATH = os.path.join(tempfile.gettempdir(), "live-translator-engine.lock")

LANGUAGE_NAMES = {
    "auto": "the automatically detected spoken language",
    "en": "English",
    "uz": "Uzbek",
    "ru": "Russian",
    "es": "Spanish",
}


class Translator:
    def __init__(self, args: argparse.Namespace, channel: str = ""):
        self.args = args
        self.channel = channel.strip().upper()
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=20)
        self.input_device = auto_input_device(args.input_device)
        self.output_device = auto_output_device(args.output_device)
        self.player = AudioPlayer(
            self.output_device,
            speech_speed=args.speech_speed,
            output_rate=args.output_sample_rate,
        )
        self.capture = AudioCapture(self.input_device, self._from_audio_thread)
        self.started_at = 0.0
        self.input_bytes = 0
        self.output_bytes = 0
        self.source_language = args.source_language.upper()

    def _log(self, message: str) -> None:
        prefix = f"[{self.channel}] " if self.channel else ""
        print(f"{prefix}{message}")

    def _from_audio_thread(self, data: bytes) -> None:
        self.loop.call_soon_threadsafe(self._enqueue_audio, data)

    def _enqueue_audio(self, data: bytes) -> None:
        if self.audio_queue.full():
            with suppress(asyncio.QueueEmpty):
                self.audio_queue.get_nowait()
        self.audio_queue.put_nowait(data)

    async def run(self, api_key: str) -> None:
        self._log(f"Input : [{self.input_device.index}] {self.input_device.name} "
                  f"({self.input_device.sample_rate} Hz, {self.input_device.channels}ch)")
        self._log(f"Output: [{self.output_device.index}] {self.output_device.name} "
                  f"({self.player.output_rate} Hz, {self.output_device.channels}ch)")
        self._log(
            f"Mode  : {self.args.source_language.upper()} → "
            f"{self.args.target_language.upper()} | "
            f"Voice: {self.args.voice} | Speed: {self.args.speech_speed:.2f}x | "
            "Ctrl+C: stop"
        )

        self.player.start()
        self.capture.start()
        self.started_at = time.monotonic()
        delay = 1.0
        try:
            while not self.stop_event.is_set():
                try:
                    await self._session(api_key)
                    delay = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if self.stop_event.is_set():
                        break
                    self._log(f"[Ulanish uzildi] {type(error).__name__}: {error}")
                    self._log(f"{delay:.0f}s dan keyin qayta ulanadi...")
                    self.player.clear()
                    self._clear_audio_queue()
                    try:
                        await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    delay = min(delay * 2, 15.0)
        finally:
            self.capture.stop()
            self.player.stop()
            elapsed = max(time.monotonic() - self.started_at, 0.001)
            self._log(
                f"To‘xtadi. {elapsed:.1f}s | "
                f"sent {self.input_bytes / 1024:.0f} KiB | "
                f"received {self.output_bytes / 1024:.0f} KiB"
            )

    def _translation_instruction(self) -> str:
        source = LANGUAGE_NAMES[self.args.source_language]
        target = LANGUAGE_NAMES[self.args.target_language]
        detection = (
            "Detect whether each utterance is English, Russian, Spanish, or Uzbek. "
            if self.args.source_language == "auto"
            else ""
        )
        return (
            "You are a low-latency simultaneous interpreter. "
            f"{detection}Translate every spoken utterance from {source} into natural {target}. "
            "Preserve meaning, names, numbers, and tone. Output only the translation. "
            "Never answer the speaker, never mention TRK, and never add commentary."
        )

    def _setup_message(self) -> dict:
        return {
            "type": "setup",
            "conversation_id": None,
            "initial_query": None,
            "config": {
                "generation_config": {
                    "response_modalities": ["AUDIO"],
                    "temperature": 0.2,
                    "speech_config": {
                        "voice_config": {
                            "prebuilt_voice_config": {
                                "voice_name": self.args.voice,
                            }
                        }
                    },
                },
                "realtime_input_config": {
                    "automatic_activity_detection": {
                        "disabled": False,
                        "silence_duration_ms": 500,
                        "prefix_padding_ms": 500,
                    }
                },
                "model": self.args.model,
                "input_audio_transcription": {},
                "output_audio_transcription": {},
                "system_instruction": {
                    "parts": [{"text": self._translation_instruction()}]
                },
            },
        }

    async def _session(self, api_key: str) -> None:
        self._log("Gemini 3.5 Live Translate’ga ulanmoqda...")
        self._clear_audio_queue()
        client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1alpha"},
        )
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            translation_config=types.TranslationConfig(
                target_language_code=self.args.target_language,
                echo_target_language=False,
            ),
        )
        async with client.aio.live.connect(
            model=self.args.model, config=config
        ) as session:
            self._log(
                f"✓ Ulandi. {self.args.source_language.upper()} nutqini kutyapman..."
            )
            sender = asyncio.create_task(self._send_google_audio(session))
            receiver = asyncio.create_task(self._receive_google_audio(session))
            stop_watcher = asyncio.create_task(self.stop_event.wait())
            timer = (
                asyncio.create_task(self._stop_after(self.args.max_seconds))
                if self.args.max_seconds
                else None
            )
            try:
                done, _ = await asyncio.wait(
                    {sender, receiver, stop_watcher},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if sender in done:
                    await sender
                if receiver in done:
                    await receiver
            finally:
                for task in (sender, receiver, stop_watcher):
                    task.cancel()
                for task in (sender, receiver, stop_watcher):
                    with suppress(asyncio.CancelledError):
                        await task
                if timer:
                    timer.cancel()

    async def _receive_google_audio(self, session) -> None:  # noqa: ANN001
        async for response in session.receive():
            content = response.server_content
            if not content:
                continue
            if content.interrupted:
                self.player.clear()
            if not self.args.no_transcript and content.input_transcription:
                transcription = content.input_transcription
                text = (transcription.text or "").strip()
                if transcription.language_code:
                    self.source_language = transcription.language_code.upper()
                if text:
                    self._log(f"{self.source_language} › {text}")
            if not self.args.no_transcript and content.output_transcription:
                text = (content.output_transcription.text or "").strip()
                if text:
                    self._log(f"{self.args.target_language.upper()} › {text}")
            if content.model_turn:
                for part in content.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        data = part.inline_data.data
                        self.output_bytes += len(data)
                        self.player.play(data)
            if content.turn_complete:
                self.player.flush()

    async def _send_google_audio(self, session) -> None:  # noqa: ANN001
        # Live Translate expects one continuous PCM stream. Ending the stream
        # every few seconds fragments words and makes returned audio bursty.
        first_chunk = True
        while not self.stop_event.is_set():
            chunk = await self.audio_queue.get()
            self.input_bytes += len(chunk)
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
            )
            if first_chunk:
                self._log(f"✓ Audio oqimi serverga ketmoqda ({len(chunk)} bytes/chunk)")
                first_chunk = False

    @staticmethod
    async def _wait_until_ready(session) -> None:  # noqa: ANN001
        while True:
            raw = await asyncio.wait_for(session.recv(), timeout=12)
            if isinstance(raw, bytes):
                continue
            message = json.loads(raw)
            message_type = message.get("type")
            if message_type == "ready":
                return
            if message_type == "error":
                raise RuntimeError(message.get("message") or "EDCOM ulanish xatosi")

    async def _receive_edcom_audio(self, session) -> None:  # noqa: ANN001
        async for raw in session:
            if isinstance(raw, bytes):
                self.output_bytes += len(raw)
                self.player.play(raw)
                continue
            message = json.loads(raw)
            message_type = message.get("type")
            if message_type == "audio":
                data = base64.b64decode(message.get("data", ""))
                if data:
                    self.output_bytes += len(data)
                    self.player.play(data)
            elif message_type == "transcript" and not self.args.no_transcript:
                text = str(message.get("text", "")).strip()
                role = message.get("role")
                if text and role == "user":
                    self._log(f"{self.source_language} › {text}")
                elif text and role == "model":
                    self._log(f"{self.args.target_language.upper()} › {text}")
            elif message_type == "turn_complete":
                self.player.flush()
            elif message_type == "error":
                raise RuntimeError(message.get("message") or "EDCOM server xatosi")

    async def _send_edcom_audio(self, session) -> None:  # noqa: ANN001
        # Server-side automatic activity detection needs the silence frames too.
        while not self.stop_event.is_set():
            chunk = await self.audio_queue.get()
            self.input_bytes += len(chunk)
            await session.send(chunk)

    def _clear_audio_queue(self) -> None:
        while True:
            with suppress(asyncio.QueueEmpty):
                self.audio_queue.get_nowait()
                continue
            return

    async def _stop_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        self.stop_event.set()


def load_api_key() -> str:
    load_dotenv()
    key = (
        os.getenv("GOOGLE_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    )
    if not key:
        raise RuntimeError("GOOGLE_API_KEY topilmadi. Sozlamalardagi API key’ni tekshiring.")
    return key


def acquire_engine_lock():  # noqa: ANN201
    lock_file = open(ENGINE_LOCK_PATH, "a+b")
    try:
        if os.name == "nt":
            assert msvcrt is not None
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            assert fcntl is not None
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as error:
        lock_file.close()
        raise RuntimeError(
            "Translator allaqachon ishlayapti. Avval mavjud nusxani to‘xtating."
        ) from error
    return lock_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Real-time multilingual meeting translator")
    parser.add_argument(
        "--duplex",
        action="store_true",
        help="Run incoming and outgoing translation sessions simultaneously",
    )
    parser.add_argument("--input-device", help="BlackHole input device name or ID")
    parser.add_argument("--output-device", help="Physical speaker/headphone name or ID")
    for channel in ("incoming", "outgoing"):
        parser.add_argument(f"--{channel}-input-device")
        parser.add_argument(f"--{channel}-output-device")
        parser.add_argument(
            f"--{channel}-source-language",
            choices=("auto", "en", "uz", "ru", "es"),
        )
        parser.add_argument(
            f"--{channel}-target-language",
            choices=("en", "uz", "ru", "es"),
        )
    parser.add_argument("--list-devices", action="store_true", help="Show audio devices and exit")
    parser.add_argument("--check", action="store_true", help="Check devices/API connection and exit")
    parser.add_argument("--no-transcript", action="store_true", help="Hide terminal captions")
    parser.add_argument("--max-seconds", type=float, help="Stop automatically after N seconds")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="Prebuilt voice name")
    parser.add_argument(
        "--speech-speed",
        type=float,
        default=1.00,
        help="Translated speech playback speed without pitch shift (1.0-1.25)",
    )
    parser.add_argument(
        "--output-sample-rate",
        type=int,
        help="Override translated playback rate; default uses the output device native rate",
    )
    parser.add_argument(
        "--source-language",
        default="auto",
        choices=("auto", "en", "uz", "ru", "es"),
        help="Expected source language; auto detects supported speech languages",
    )
    parser.add_argument(
        "--target-language",
        default="uz",
        choices=("en", "uz", "ru", "es"),
        help="Translation output language",
    )
    parser.add_argument(
        "--silence-threshold",
        type=int,
        default=50,
        help="PCM RMS level treated as speech/audio",
    )
    parser.add_argument(
        "--silence-ms",
        type=int,
        default=1200,
        help="Legacy client-side silence setting",
    )
    parser.add_argument("--model", default=MODEL)
    return parser


def duplex_channel_args(args: argparse.Namespace, channel: str) -> argparse.Namespace:
    """Create the normal single-route namespace used by one duplex channel."""

    values = vars(args).copy()
    for field in ("input_device", "output_device", "source_language", "target_language"):
        values[field] = getattr(args, f"{channel}_{field}")
    return argparse.Namespace(**values)


def validate_translation_args(args: argparse.Namespace) -> None:
    if args.source_language == args.target_language:
        raise ValueError("Manba va tarjima tili bir xil bo‘lishi mumkin emas")


async def check_connection(api_key: str, route: argparse.Namespace) -> None:
    client = genai.Client(
        api_key=api_key,
        http_options={"api_version": "v1alpha"},
    )
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        translation_config=types.TranslationConfig(
            target_language_code=route.target_language,
            echo_target_language=False,
        ),
    )
    async with client.aio.live.connect(model=route.model, config=config):
        pass
    print(
        f"✓ Google Gemini {route.model} ishlayapti. "
        f"Target: {route.target_language}"
    )


async def async_main(args: argparse.Namespace) -> int:
    if args.list_devices:
        print(list_devices())
        return 0
    if args.max_seconds is not None and args.max_seconds <= 0:
        raise ValueError("--max-seconds musbat bo‘lishi kerak")
    if not 1.0 <= args.speech_speed <= 1.25:
        raise ValueError("--speech-speed 1.0 va 1.25 orasida bo‘lishi kerak")
    if args.output_sample_rate is not None and args.output_sample_rate not in {24_000, 48_000}:
        raise ValueError("--output-sample-rate 24000 yoki 48000 bo‘lishi kerak")
    route_args: list[tuple[str, argparse.Namespace]]
    if args.duplex:
        required = (
            "incoming_input_device",
            "incoming_output_device",
            "incoming_source_language",
            "incoming_target_language",
            "outgoing_input_device",
            "outgoing_output_device",
            "outgoing_source_language",
            "outgoing_target_language",
        )
        missing = [name.replace("_", "-") for name in required if getattr(args, name) is None]
        if missing:
            raise ValueError(f"Duplex parametrlar yetishmayapti: {', '.join(missing)}")
        route_args = [
            ("INCOMING", duplex_channel_args(args, "incoming")),
            ("OUTGOING", duplex_channel_args(args, "outgoing")),
        ]
        if (
            route_args[0][1].input_device
            == route_args[1][1].output_device
        ):
            raise ValueError(
                "Duplex rejimida incoming input va outgoing output "
                "alohida virtual qurilmalar bo‘lishi kerak"
            )
    else:
        route_args = [("", args)]
    for _channel, route in route_args:
        validate_translation_args(route)

    key = load_api_key()
    resolved_devices = []
    for channel, route in route_args:
        input_device = auto_input_device(route.input_device)
        output_device = auto_output_device(route.output_device)
        resolved_devices.append((input_device, output_device))
        prefix = f"[{channel}] " if channel else ""
        print(f"{prefix}✓ Audio: {input_device.name} → {output_device.name}")
    if args.duplex and (
        resolved_devices[0][0].index == resolved_devices[1][1].index
        or virtual_device_family(resolved_devices[0][0].name)
        == virtual_device_family(resolved_devices[1][1].name)
    ):
        raise ValueError(
            "Duplex rejimida incoming input va outgoing output "
            "alohida virtual audio kabellar bo‘lishi kerak"
        )
    if args.check:
        for _channel, route in route_args:
            await check_connection(key, route)
        return 0

    engine_lock = acquire_engine_lock()
    try:
        translators = [Translator(route, channel) for channel, route in route_args]
        loop = asyncio.get_running_loop()

        def stop_all() -> None:
            for active in translators:
                active.stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_all)
        tasks = [asyncio.create_task(active.run(key)) for active in translators]
        try:
            await asyncio.gather(*tasks)
        finally:
            stop_all()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        engine_lock.close()
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"Xato: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
