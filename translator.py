"""Real-time multilingual voice translator using Google Gemini Live Translate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import signal
import sys
import tempfile
import time
from contextlib import suppress

from dotenv import load_dotenv
from google import genai
from google.genai import types

import numpy as np
import sounddevice as sd

from audio import (
    AudioCapture,
    AudioPlayer,
    auto_input_device,
    auto_output_device,
    list_devices,
    preferred_physical_output,
)
from audio_routing import is_forbidden_route, is_virtual_device, virtual_device_family
from playback_profiles import DEFAULT_PLAYBACK_PROFILE, PLAYBACK_PROFILES

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # macOS/Linux
    msvcrt = None  # type: ignore[assignment]


PROVIDER = "google"
MODEL = "gemini-3.5-live-translate-preview"
DEFAULT_VOICE = "Charon"
ENGINE_LOCK_PATH = os.path.join(tempfile.gettempdir(), "live-translator-engine.lock")

LANGUAGE_NAMES = {
    "auto": "the automatically detected spoken language",
    "en": "English",
    "uz": "Uzbek",
    "ru": "Russian",
    "es": "Spanish",
}

LANGUAGE_CODES = {
    "en": "en-US",
    "uz": "uz-UZ",
    "ru": "ru-RU",
    "es": "es-ES",
}


def translation_instruction(args: argparse.Namespace) -> str:
    source = LANGUAGE_NAMES[args.source_language]
    target = LANGUAGE_NAMES[args.target_language]
    if args.source_language == "auto":
        source_rule = (
            "Detect whether each utterance is English, Russian, Spanish, or Uzbek. "
        )
    elif args.source_language == "uz":
        source_rule = (
            "The speaker is speaking Uzbek. Treat the speech as Uzbek; do not "
            "misclassify it as Turkish, Azerbaijani, Spanish, or Russian. "
        )
    else:
        source_rule = f"The speaker is speaking {source}. "
    return (
        "You are a simultaneous interpreter, not an assistant. "
        f"{source_rule}Translate every spoken utterance from {source} into natural {target}. "
        "Preserve meaning, names, numbers, and tone. Do not answer questions. "
        "Do not mention TRK, services, programs, or your capabilities. Never greet "
        "or add commentary. Translate incrementally in short streaming chunks. "
        "Continue seamlessly from the previous chunk and never repeat an earlier "
        "translation. Output only the translation of the speaker's words."
    )


def input_transcription_config(args: argparse.Namespace) -> types.AudioTranscriptionConfig:
    """Manba tili aniq tanlangan bo'lsa modelga til ishorasi beriladi.

    Ishorasiz o'zbek nutqi rus/tatar deb aniqlanardi ("bir ikki test" ->
    "Бер ике тест") va tarjima sifati tushardi. "Avtomatik" tanlansa
    ishora yubormaymiz — model o'zi aniqlaydi (aralash tilli meeting).
    """
    code = LANGUAGE_CODES.get(args.source_language)
    if args.source_language == "auto" or not code:
        return types.AudioTranscriptionConfig()
    return types.AudioTranscriptionConfig(
        language_hints=types.LanguageHints(language_codes=[code])
    )


def build_live_config(args: argparse.Namespace) -> types.LiveConnectConfig:
    """Build the documented continuous Live Translate configuration.

    speech_config va system_instruction jonli A/B bilan tekshirilgan
    (2026-07-20): uchala variantda ham audio to'liq chiqadi. Ilgari bu
    maydonlar umuman yuborilmasdi — ya'ni "Charon ovozi" va o'zbek uslub
    qoidalari faqat qog'ozda qolgan edi.
    """

    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=translation_instruction(args),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=args.voice or DEFAULT_VOICE
                )
            )
        ),
        input_audio_transcription=input_transcription_config(args),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        translation_config=types.TranslationConfig(
            target_language_code=args.target_language,
            echo_target_language=False,
        ),
        # ESLATMA: realtime_input_config (server-VAD) BILAN SINALDI va
        # OLIB TASHLANDI (v0.9.36). start_of_speech_sensitivity=LOW model
        # foydalanuvchi nutqiga ham javob bermay qo'ydi — tarjima butunlay
        # ishlamay qoldi (regressiya). Sukunat gallutsinatsiyasi ("that it
        # that it") uchun faqat client SilenceGate ishlatiladi (jimlikda
        # nol yuboradi) — u tarjimani buzmaydi.
    )


class SilenceGate:
    """Ovoz-aniqlagich (VAD): jimlik/shovqinda modelga CHINAKAM sukunat yuboradi.

    Muammo: model uzluksiz oqim kutadi. Jim paytda mikrofonning shovqin
    poli (nafas, xona shovqini) modelga "noaniq past ovoz" bo'lib boradi
    va model uni nutq deb "gap to'qib" chiqaradi (hallutsinatsiya).

    Yechim: har bo'lakning RMS energiyasini o'lchaymiz. Nutq bo'lsa —
    o'zini yuboramiz. Tasdiqlangan jimlikda — nol (raqamli sukunat)
    yuboramiz: oqim uzilmaydi, lekin model to'qishga narsa topmaydi.
    HANGOVER — nutq tugagach qisqa vaqt haqiqiy audioni davom ettiradi,
    so'z dumini kesmaslik uchun.
    """

    def __init__(self, threshold_rms: int, hangover_ms: int = 600,
                 clock=time.monotonic):  # noqa: ANN001
        self.threshold = max(1, threshold_rms)
        self.hangover_s = hangover_ms / 1000.0
        self._clock = clock
        self._last_voice = 0.0
        self._warmup_until = 0.0

    @staticmethod
    def _rms(pcm16: bytes) -> float:
        if not pcm16:
            return 0.0
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples * samples)))

    def process(self, pcm16: bytes) -> bytes:
        now = self._clock()
        if self._warmup_until == 0.0:
            # Boshlanishda 0.4s haqiqiy audio o'tsin (ulanish, birinchi so'z).
            self._warmup_until = now + 0.4
            self._last_voice = now
        rms = self._rms(pcm16)
        if rms >= self.threshold or now < self._warmup_until:
            self._last_voice = now
            return pcm16
        if now - self._last_voice < self.hangover_s:
            return pcm16  # so'z dumi
        return b"\x00" * len(pcm16)  # tasdiqlangan jimlik -> nol


class CaptureGate:
    """Yarim-duplex himoya: "Ikkalasi" rejimida eshitish kanali tarjima
    OVOZINI karnayda ijro qilayotgan paytda gapirish kanalining mikrofonini
    vaqtincha "kar" qiladi.

    Sabab: eshitish tarjimasi fizik karnayga chiqadi, gapirish kanali esa
    fizik mikrofonni yozadi — karnaydagi o'zbekcha tarjimani mikrofon
    eshitib, uni qaytadan meeting tiliga tarjima qilib yuborardi
    (tarjimaning-tarjimasi halqasi). TAIL_SECONDS — karnay so'nishi va
    yozuv kechikishini qoplaydigan qo'shimcha dum.
    """

    TAIL_SECONDS = 0.4
    # Xavfsizlik chegarasi: player "audio bor" holatida qotib qolsa mikrofon
    # abadiy o'chib qolmasin. 25s — uzun tabiiy nutq ijrosidan uzunroq
    # (duplex himoyasi buzilmaydi), lekin qotgan holatdan chiqaradi.
    MAX_BLOCK_SECONDS = 25.0

    def __init__(self, source_player_ref, clock=time.monotonic):  # noqa: ANN001
        self._source_player_ref = source_player_ref
        self._clock = clock
        self._blocked_until = 0.0
        self._blocking_since = 0.0

    def should_drop(self) -> bool:
        player = self._source_player_ref()
        now = self._clock()
        playing = player is not None and player.has_audio()
        if not playing and now >= self._blocked_until:
            self._blocking_since = 0.0
            return False
        if self._blocking_since == 0.0:
            self._blocking_since = now
        elif now - self._blocking_since > self.MAX_BLOCK_SECONDS:
            # Qotib qolgan holat — gate'ni majburan ochamiz.
            self._blocking_since = 0.0
            self._blocked_until = 0.0
            return False
        if playing:
            self._blocked_until = now + self.TAIL_SECONDS
        return True


class Translator:
    def __init__(self, args: argparse.Namespace, channel: str = ""):
        self.args = args
        self.channel = channel.strip().upper()
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        # Keep latency bounded: when the network falls behind, discard the
        # oldest PCM rather than playing a stale translation seconds later.
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=25)
        self.input_device = auto_input_device(args.input_device)
        self.output_device = auto_output_device(args.output_device)
        self.player = AudioPlayer(
            self.output_device,
            speech_speed=args.speech_speed,
            output_rate=args.output_sample_rate,
            playback_profile=args.playback_profile,
        )
        # "Nazorat" chiqishi: tarjima virtual kabelga ketganda foydalanuvchi
        # o'zi hech narsa eshitmaydi va ishlayotganini bilolmaydi. Ixtiyoriy
        # ikkinchi player o'sha ovozni naushnikka ham beradi.
        self.monitor_player: AudioPlayer | None = None
        monitor_query = getattr(args, "monitor_device", None)
        if monitor_query:
            monitor_device = auto_output_device(monitor_query)
            self.monitor_player = AudioPlayer(
                monitor_device,
                speech_speed=args.speech_speed,
                output_rate=None,
                playback_profile=args.playback_profile,
            )
            self.monitor_device = monitor_device
        self.capture = AudioCapture(self.input_device, self._from_audio_thread)
        self.started_at = 0.0
        self.input_bytes = 0
        self.output_bytes = 0
        self.source_language = args.source_language.upper()
        # Duplex'da tashqaridan o'rnatiladi (async_main): gapirish kanali
        # uchun eshitish kanalining player'iga bog'langan feedback-gate.
        self.capture_gate: CaptureGate | None = None
        self.gated_chunks = 0
        self._last_gate_log = 0.0
        # VAD: jimlikda modelga sukunat yuborib hallutsinatsiyani to'xtatadi.
        # --silence-threshold 0 bo'lsa o'chiriladi (agar kerak bo'lsa).
        self.silence_gate: SilenceGate | None = None
        if getattr(args, "silence_threshold", 0) > 0:
            self.silence_gate = SilenceGate(
                threshold_rms=args.silence_threshold,
                hangover_ms=getattr(args, "silence_ms", 600) or 600,
            )
        # DIQQAT: nazorat ovozi uchun o'z-o'zini gate qilish MUMKIN EMAS.
        # Bir kanalning o'zi ijro qilayotganda mikrofonini yopsa, gapirish
        # imkoni butunlay yo'qoladi (v0.7.4 regressiyasi: 1600+ chunk
        # tashlandi, tarjima umuman ishlamadi). Bu yerda halqa xavfi ham
        # yo'q: chiqish tili = target, echo_target_language=False bo'lgani
        # uchun model o'z tilidagi nutqqa javob bermaydi.

    def _log(self, message: str) -> None:
        prefix = f"[{self.channel}] " if self.channel else ""
        print(f"{prefix}{message}")

    def _from_audio_thread(self, data: bytes) -> None:
        self.loop.call_soon_threadsafe(self._enqueue_audio, data)

    def _enqueue_audio(self, data: bytes) -> None:
        if self.capture_gate is not None and self.capture_gate.should_drop():
            # O'z tarjimamiz karnayda yangrayapti — bu chunk'ni yuborsak,
            # model uni "yangi gap" deb qabul qilib qayta tarjima qiladi.
            self.gated_chunks += 1
            now = time.monotonic()
            if now - self._last_gate_log >= 5.0:
                self._last_gate_log = now
                self._log(
                    "Mikrofon vaqtincha jim: o‘z tarjimamiz ijro etilmoqda "
                    f"(feedback himoyasi, {self.gated_chunks} chunk)."
                )
            return
        if self.silence_gate is not None:
            data = self.silence_gate.process(data)
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
            f"Playback: {self.args.playback_profile} | "
            "Ctrl+C: stop"
        )

        self.player.start()
        if self.monitor_player is not None:
            self.monitor_player.start()
            self._log(f"Nazorat ovozi: {self.monitor_device.name}")
        self.capture.start()
        self.started_at = time.monotonic()
        device_watcher = asyncio.create_task(self._watch_output_device())
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
                    safe_error = str(error).replace(api_key, "<redacted>")
                    self._log(
                        f"[Ulanish uzildi] {type(error).__name__}: {safe_error}"
                    )
                    self._log(f"{delay:.0f}s dan keyin qayta ulanadi...")
                    self.player.clear()
                    if self.monitor_player is not None:
                        self.monitor_player.clear()
                    self._clear_audio_queue()
                    try:
                        await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    delay = min(delay * 2, 15.0)
        finally:
            device_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await device_watcher
            self.capture.stop()
            # To'xtashda qoldiq tarjimani oxirigacha ijro qilib o'tirmaymiz —
            # duplex'da ikki player'ning to'liq drain'i 6s dan oshib, GUI
            # majburan SIGKILL qilar edi ("Process crashed" ko'rinardi).
            self.player.clear()
            self._clear_audio_queue()
            self.player.stop()
            if self.monitor_player is not None:
                self.monitor_player.clear()
                self.monitor_player.stop()
            elapsed = max(time.monotonic() - self.started_at, 0.001)
            playback = self.player.metrics()
            self._log(
                f"To‘xtadi. {elapsed:.1f}s | "
                f"sent {self.input_bytes / 1024:.0f} KiB | "
                f"received {self.output_bytes / 1024:.0f} KiB | "
                f"buffer {playback['target_buffer_ms']}ms | "
                f"underflows {playback['underflows']}"
            )

    async def _session(self, api_key: str) -> None:
        self._log("Gemini 3.5 Live Translate’ga ulanmoqda...")
        self._clear_audio_queue()
        client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1alpha"},
        )
        async with client.aio.live.connect(
            model=self.args.model,
            config=build_live_config(self.args),
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
                if self.monitor_player is not None:
                    self.monitor_player.clear()
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
                        if self.monitor_player is not None:
                            self.monitor_player.play(data)
            if content.turn_complete:
                self.player.flush()
                if self.monitor_player is not None:
                    self.monitor_player.flush()

    async def _send_google_audio(self, session) -> None:  # noqa: ANN001
        # Live Translate expects one continuous PCM stream. Artificial turn
        # boundaries fragment words and make translated audio bursty.
        first_chunk = True
        while not self.stop_event.is_set():
            chunk = await self.audio_queue.get()
            self.input_bytes += len(chunk)
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
            )
            if first_chunk:
                self._log(
                    f"✓ Audio oqimi serverga ketmoqda ({len(chunk)} bytes/chunk)"
                )
                first_chunk = False

    def _clear_audio_queue(self) -> None:
        while True:
            with suppress(asyncio.QueueEmpty):
                self.audio_queue.get_nowait()
                continue
            return

    DEVICE_POLL_SECONDS = 2.0

    async def _swap_player(self, player: AudioPlayer, device) -> AudioPlayer:  # noqa: ANN001
        """Ijro qurilmasini sessiyani to'xtatmasdan almashtiradi."""
        replacement = AudioPlayer(
            device,
            speech_speed=self.args.speech_speed,
            output_rate=None,
            playback_profile=self.args.playback_profile,
        )
        replacement.start()
        await asyncio.to_thread(player.stop)
        return replacement

    async def _watch_output_device(self) -> None:
        """Naushnik ulansa/uzilsa tarjima ovozini yangi qurilmaga ko'chiradi.

        Faqat FIZIK chiqishlar kuzatiladi: "Gapirish" rejimida chiqish
        virtual kabel bo'ladi va unga tegilmasligi shart (aks holda tarjima
        Zoom'ga bormay qoladi).
        """
        follow_main = not is_virtual_device(self.output_device.name)
        if not follow_main and self.monitor_player is None:
            return
        listening_cable = (
            self.input_device.name if is_virtual_device(self.input_device.name) else ""
        )
        while not self.stop_event.is_set():
            await asyncio.sleep(self.DEVICE_POLL_SECONDS)
            try:
                await self._maybe_switch_output(listening_cable, follow_main)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Qurilma almashtirish — qulaylik, majburiyat emas: hech
                # qanday xato tarjimani to'xtatmasligi kerak.
                self._log(f"Qurilma kuzatuvchisi xatosi (e'tiborsiz): {error}")

    def _requested_output_name(self) -> str:
        """GUI yozib qo'ygan chiqish qurilmasi nomi (Windows yo'li).

        Windows'da PortAudio yangi qurilmani jarayon ichida ko'rmaydi —
        GUI (unda ochiq audio oqim yo'q) ro'yxatni yangilab, kerakli
        qurilma nomini shu faylga yozadi.
        """
        path = os.getenv("LIVE_TRANSLATOR_DEVICE_STATE", "").strip()
        if not path:
            return ""
        try:
            with open(path, encoding="utf-8") as handle:
                return str(json.load(handle).get("output", "")).strip()
        except Exception:
            return ""

    async def _reopen_audio(self, output_name: str) -> None:
        """Oqimlarni Gemini sessiyasini uzmasdan yangi qurilmada qayta ochadi.

        Windows'da yangi qurilma ko'rinishi uchun PortAudio'ni qayta
        yuklash shart, buning uchun esa avval BARCHA oqimlar yopilishi
        kerak (aks holda callback ochiq turib qulaydi).
        """
        input_name = self.input_device.name
        await asyncio.to_thread(self.capture.stop)
        await asyncio.to_thread(self.player.stop)
        if platform.system() != "Darwin":
            with suppress(Exception):
                await asyncio.to_thread(sd._terminate)  # type: ignore[attr-defined]
                await asyncio.to_thread(sd._initialize)  # type: ignore[attr-defined]
        # Qurilmalar qayta sanalgach index'lar suriladi — NOM bo'yicha
        # qaytadan topamiz.
        self.input_device = auto_input_device(input_name)
        self.output_device = auto_output_device(output_name)
        self.player = AudioPlayer(
            self.output_device,
            speech_speed=self.args.speech_speed,
            output_rate=self.args.output_sample_rate,
            playback_profile=self.args.playback_profile,
        )
        self.capture = AudioCapture(self.input_device, self._from_audio_thread)
        self.player.start()
        self.capture.start()

    async def _maybe_switch_output(self, listening_cable: str, follow_main: bool) -> None:
        if platform.system() != "Darwin":
            # Windows: GUI aniqlagan qurilmaga issiq almashish.
            requested = self._requested_output_name()
            if (
                follow_main
                and requested
                and requested != self.output_device.name
                and not is_virtual_device(requested)
            ):
                previous = self.output_device.name
                await self._reopen_audio(requested)
                self._log(f"Chiqish qurilmasi almashdi: {previous} → {requested}")
            return
        preferred = preferred_physical_output()
        if preferred is None:
            return
        if listening_cable and platform.system() == "Darwin":
            # "Tinglash" rejimida GUI tizim chiqishini kabelga qaratadi.
            # Naushnik ulanganda macOS uni o'ziga tortadi va meeting
            # ovozi kabelga tushmay qoladi — faqat SHU holatda kabelni
            # qaytaramiz.
            # MUHIM: o'zimiz hech qachon yangi marshrut O'RNATMAYMIZ —
            # avval kabel qaratilganini KO'RGAN bo'lsakgina tiklaymiz,
            # aks holda CLI'dan ishlatilganda tizim chiqishi kabelda
            # qolib ketardi (sinovda aynan shunday bo'ldi).
            with suppress(Exception):
                from system_audio import default_output, route_output_to

                current = (await asyncio.to_thread(default_output)).name
                if current == listening_cable:
                    self._cable_was_system_output = True
                elif getattr(self, "_cable_was_system_output", False):
                    await asyncio.to_thread(route_output_to, listening_cable)
                    self._log(
                        "Tizim chiqishi kabelga qaytarildi "
                        f"(«{current}» uni tortib olgan edi)."
                    )
        if follow_main and preferred.name != self.output_device.name:
            previous = self.output_device.name
            self.player = await self._swap_player(self.player, preferred)
            self.output_device = preferred
            self._log(f"Chiqish qurilmasi almashdi: {previous} → {preferred.name}")
        elif (
            self.monitor_player is not None
            and preferred.name != self.monitor_device.name
        ):
            previous = self.monitor_device.name
            self.monitor_player = await self._swap_player(
                self.monitor_player, preferred
            )
            self.monitor_device = preferred
            self._log(f"Nazorat ovozi almashdi: {previous} → {preferred.name}")

    async def _stop_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        self.stop_event.set()


def load_api_key() -> str:
    load_dotenv()
    key = (
        os.getenv("GOOGLE_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("EDCOM_API_KEY", "").strip()
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
    parser.add_argument(
        "--monitor-device",
        help="Tarjimani virtual kabeldan tashqari shu qurilmada ham eshittirish",
    )
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
        "--no-gate",
        action="store_true",
        help="Ikkalasi: gapirish mikrofoni feedback-gate'ini o'chiradi "
        "(naushnik ishlatilganda — tarjima karnayga chiqmaydi, echo yo'q)",
    )
    parser.add_argument(
        "--speech-speed",
        type=float,
        default=1.08,
        help="Translated speech playback speed without pitch shift (1.0-1.25)",
    )
    parser.add_argument(
        "--output-sample-rate",
        type=int,
        help="Override translated playback rate; default uses the output device native rate",
    )
    parser.add_argument(
        "--playback-profile",
        choices=tuple(PLAYBACK_PROFILES),
        default=DEFAULT_PLAYBACK_PROFILE,
        help="Translated audio buffering profile",
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
        default=300,
        help="RMS ostidagi audio JIMLIK deb sanaladi (VAD). 0 = o'chiq. "
        "Jim paytda modelga sukunat yuborilib gap-to'qish (hallutsinatsiya) "
        "to'xtatiladi.",
    )
    parser.add_argument(
        "--silence-ms",
        type=int,
        default=600,
        help="Nutq tugagach shuncha ms haqiqiy audio davom etadi (so'z dumi)",
    )
    parser.add_argument("--model", default=MODEL)
    return parser


def duplex_channel_args(args: argparse.Namespace, channel: str) -> argparse.Namespace:
    """Create the normal single-route namespace used by one duplex channel."""

    values = vars(args).copy()
    for field in ("input_device", "output_device", "source_language", "target_language"):
        values[field] = getattr(args, f"{channel}_{field}")
    # Duplex'da nazorat ovozi kerak emas: kiruvchi kanal allaqachon fizik
    # chiqishga o'ynaydi, ikkinchi nusxa faqat aks-sado yaratardi.
    values["monitor_device"] = None
    return argparse.Namespace(**values)


def validate_translation_args(args: argparse.Namespace) -> None:
    if args.source_language == args.target_language:
        raise ValueError("Manba va tarjima tili bir xil bo‘lishi mumkin emas")


async def check_connection(api_key: str, route: argparse.Namespace) -> None:
    client = genai.Client(
        api_key=api_key,
        http_options={"api_version": "v1alpha"},
    )
    async with client.aio.live.connect(
        model=route.model,
        config=build_live_config(route),
    ):
        pass
    print(f"✓ Google Gemini {route.model} ishlayapti. Target: {route.target_language}")


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
    if not args.duplex:
        single_in, single_out = resolved_devices[0]
        if is_forbidden_route(
            single_in.name, single_out.name, single_in.index, single_out.index
        ):
            raise ValueError(
                "Input va output bitta virtual kabelga ulanmaydi — "
                "tarjima o‘z-o‘ziga qaytib feedback loop yaratadi."
            )
    if args.check:
        for _channel, route in route_args:
            await check_connection(key, route)
        return 0

    engine_lock = acquire_engine_lock()
    try:
        translators = [Translator(route, channel) for channel, route in route_args]
        if args.duplex and len(translators) == 2:
            # route_args tartibi qat'iy: [0]=INCOMING (eshitish), [1]=OUTGOING
            # (gapirish). Eshitish tarjimasi karnayda yangrayotganda gapirish
            # mikrofoni gate bilan yopiladi — aks holda o'z tarjimamiz qayta
            # tarjima bo'lib meetingga ketardi.
            incoming_translator, outgoing_translator = translators
            if not getattr(args, "no_gate", False):
                outgoing_translator.capture_gate = CaptureGate(
                    lambda: incoming_translator.player
                )
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
    # Toza mashinada Google'ga wss ulanish ham CA'siz yiqiladi.
    from licensing import ensure_ca_bundle_env

    ensure_ca_bundle_env()
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
