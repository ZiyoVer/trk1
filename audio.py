"""Small CoreAudio/PCM helpers used by the meeting translator."""

from __future__ import annotations

import audioop
import platform
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
import sounddevice as sd
from audiotsm import wsola
from audiotsm.io.array import ArrayReader, ArrayWriter

from audio_routing import is_virtual_device
from playback_profiles import PlaybackProfile, playback_profile as get_playback_profile


@dataclass(frozen=True)
class DeviceChoice:
    index: int
    name: str
    sample_rate: int
    channels: int


def _device_name(device: dict) -> str:
    return str(device.get("name", "Unknown device"))


def list_devices() -> str:
    rows = ["ID   IN OUT   RATE   DEVICE"]
    for index, device in enumerate(sd.query_devices()):
        rows.append(
            f"{index:>2}   {int(device['max_input_channels']):>2}  "
            f"{int(device['max_output_channels']):>2}  "
            f"{int(device['default_samplerate']):>6}   {_device_name(device)}"
        )
    return "\n".join(rows)


def _matches(query: str, name: str) -> bool:
    return query.casefold() in name.casefold()


def pcm_rms(pcm_16bit: bytes) -> int:
    """Return the RMS level of signed 16-bit PCM."""

    return audioop.rms(pcm_16bit, 2) if pcm_16bit else 0


def find_device(query: str, kind: str) -> DeviceChoice:
    if kind not in {"input", "output"}:
        raise ValueError("kind must be input or output")
    devices: Sequence[dict] = sd.query_devices()
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"

    if query.strip().isdigit():
        candidates = [int(query)]
    else:
        candidates = [
            index
            for index, device in enumerate(devices)
            if _matches(query, _device_name(device))
        ]

    usable = [
        index
        for index in candidates
        if 0 <= index < len(devices) and int(devices[index][channel_key]) > 0
    ]
    if not usable:
        raise RuntimeError(
            f"{kind.title()} device {query!r} topilmadi. "
            "`./run.sh --list-devices` bilan qurilmalarni ko‘ring."
        )

    index = usable[0]
    device = devices[index]
    return DeviceChoice(
        index=index,
        name=_device_name(device),
        sample_rate=int(device["default_samplerate"]),
        channels=min(2, int(device[channel_key])),
    )


def available_devices(kind: str) -> list[DeviceChoice]:
    """Return every usable PortAudio device for a UI device picker."""

    if kind not in {"input", "output"}:
        raise ValueError("kind must be input or output")
    devices: Sequence[dict] = sd.query_devices()
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    choices: list[DeviceChoice] = []
    for index, device in enumerate(devices):
        channels = int(device[channel_key])
        if channels <= 0:
            continue
        choices.append(
            DeviceChoice(
                index=index,
                name=_device_name(device),
                sample_rate=int(device["default_samplerate"]),
                channels=min(2, channels),
            )
        )
    return choices


def auto_input_device(query: str | None) -> DeviceChoice:
    if query:
        return find_device(query, "input")
    if platform.system() == "Windows":
        # ASOSIY VB-CABLE'ning yozib-olish tomonini topamiz. DIQQAT: Hi-Fi
        # Cable ham "Cable Output" nomiga ega — uni chetlab, aynan "Virtual
        # Cable" oilasini qidiramiz (aks holda listen Hi-Fi'ni tanlab
        # qolardi, real Windows testda aynan shunday bo'ldi).
        devices: Sequence[dict] = sd.query_devices()
        for index, device in enumerate(devices):
            name = _device_name(device)
            folded = name.casefold()
            if (
                int(device["max_input_channels"]) > 0
                and "virtual cable" in folded
                and "hi-fi" not in folded
            ):
                return find_device(str(index), "input")
        # Zaxira: har qanday "cable output" (Hi-Fi bo'lsa ham — hech
        # narsadan yaxshi).
        for index, device in enumerate(devices):
            name = _device_name(device)
            if int(device["max_input_channels"]) > 0 and "cable output" in name.casefold():
                return find_device(str(index), "input")
        raise RuntimeError(
            "VB-CABLE input topilmadi. Ilovadagi Audio Driver tugmasi bilan o‘rnating."
        )
    return find_device("BlackHole 2ch", "input")


# Windows'dagi "yo'naltirgich" qurilmalar: bular haqiqiy karnay emas, joriy
# TIZIM DEFAULTIGA ishora qiladi. Meeting ovozini ushlash uchun default
# chiqish odatda virtual kabelga qo'yiladi — natijada tarjima kirish
# kabeliga qaytib tushib, ilova o'z ovozini qayta tarjima qila boshlaydi
# (jonli Windows logida bitta gap cheksiz takrorlangan).
ALIAS_OUTPUT_MARKERS = (
    # Inglizcha Windows
    "sound mapper",
    "primary sound driver",
    "primary sound capture",
    # Ruscha Windows (masofaviy testda topildi 2026-07-22): tizim tili
    # o'zgarsa bu yo'naltirgichlar boshqa nomlanadi va filtrdan o'tib
    # ketardi — natijada cheksiz takror halqasi.
    "переназначение звуков",  # Sound Mapper
    "первичный звуковой драйвер",  # Primary Sound Driver
    "первичный драйвер записи",  # Primary Sound Capture Driver
    "устройство сопоставления",  # Sound Mapper (muqobil tarjima)
)


def is_alias_output(name: str) -> bool:
    folded = name.casefold()
    return any(marker in folded for marker in ALIAS_OUTPUT_MARKERS)


def is_physical_output(device: dict) -> bool:
    """Tarjima chiqishi uchun yaroqli FIZIK output qurilmami?

    Virtual kabellar yagona manba — audio_routing.VIRTUAL_MARKERS — bo'yicha
    chiqariladi (qo'lda yozilgan ro'yxat CABLE-A/B kabi endpointlarni
    o'tkazib yuborardi).
    """
    return int(device["max_output_channels"]) > 0 and not is_virtual_device(
        _device_name(device)
    )


BUILTIN_OUTPUT_MARKERS = (
    "macbook",
    "built-in",
    "internal",
    "realtek digital",
)


def preferred_physical_output() -> DeviceChoice | None:
    """Foydalanuvchi HOZIR eshitayotgan fizik chiqish qurilmasi.

    Yagona mezon — TIZIMNING joriy chiqish qurilmasi: naushnik ulanganda
    macOS uni o'zi tanlaydi, ya'ni har qanday marka (AirPods, JBL, Sony…)
    avtomatik ishlaydi. Tizim tanlovi virtual kabel yoki "Sound Mapper"
    bo'lsa — None qaytaramiz va joriy qurilma o'zgarmaydi.

    DIQQAT: "tashqi qurilma afzal" qoidasi ishlatilmaydi — u ulangan
    monitorni (masalan P2961) naushnik deb tanlab, ovozni hech kim
    eshitmaydigan joyga yuborardi (design/08-heuristic-evaluation.md).
    """
    try:
        devices: Sequence[dict] = sd.query_devices()
    except Exception:
        return None

    name = ""
    if platform.system() == "Darwin":
        try:
            from system_audio import default_output as _system_default_output

            name = _system_default_output().name
        except Exception:
            name = ""
    if not name:
        try:
            default_index = int(sd.default.device[1])
            if 0 <= default_index < len(devices):
                name = _device_name(devices[default_index])
        except Exception:
            return None
    if not name or is_virtual_device(name) or is_alias_output(name):
        return None
    for index, device in enumerate(devices):
        if is_physical_output(device) and _device_name(device) == name:
            return find_device(str(index), "output")
    return None


def auto_output_device(query: str | None) -> DeviceChoice:
    if query:
        return find_device(query, "output")

    devices: Sequence[dict] = sd.query_devices()
    default_output = int(sd.default.device[1])
    if 0 <= default_output < len(devices):
        device = devices[default_output]
        if is_physical_output(device) and not is_alias_output(_device_name(device)):
            return find_device(str(default_output), "output")

    priorities = (
        "headphone",
        "airpods",
        "headset",
        "speaker",
        "built-in",
        "monitor",
        "display",
        "realtek",
        "nvidia",
    )
    for keyword in priorities:
        for index, device in enumerate(devices):
            name = _device_name(device)
            if (
                is_physical_output(device)
                and not is_alias_output(name)
                and keyword in name.casefold()
            ):
                return find_device(str(index), "output")

    for index, device in enumerate(devices):
        if is_physical_output(devices[index]) and not is_alias_output(
            _device_name(devices[index])
        ):
            return find_device(str(index), "output")
    raise RuntimeError(
        "Bu kompyuterda tarjima ovozini chiqaradigan karnay/naushnik topilmadi. "
        "Naushnik yoki karnay ulang (yoki chiqish qurilmasini qo‘lda tanlang)."
    )


class PCMConverter:
    """Stateful signed-16-bit PCM channel and sample-rate converter."""

    def __init__(self, input_rate: int, output_rate: int, input_channels: int, output_channels: int):
        if input_channels not in {1, 2} or output_channels not in {1, 2}:
            raise ValueError("MVP supports one or two audio channels")
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.input_channels = input_channels
        self.output_channels = output_channels
        self._state = None

    def convert(self, data: bytes) -> bytes:
        if not data:
            return b""
        mono = data
        if self.input_channels == 2:
            mono = audioop.tomono(mono, 2, 0.5, 0.5)
        converted, self._state = audioop.ratecv(
            mono,
            2,
            1,
            self.input_rate,
            self.output_rate,
            self._state,
        )
        if self.output_channels == 2:
            converted = audioop.tostereo(converted, 2, 1.0, 1.0)
        return converted

    def clear(self) -> None:
        self._state = None


class SpeechTempoConverter:
    """Change 24 kHz mono speech tempo without changing its pitch."""

    FLUSH_PADDING_SAMPLES = 1_536  # 64 ms keeps the final spoken frame intact

    def __init__(self, speed: float):
        if not 1.0 <= speed <= 1.25:
            raise ValueError("speech speed must be between 1.0 and 1.25")
        self.speed = speed
        self._tsm = None if speed == 1.0 else wsola(1, speed=speed)

    def convert(self, pcm_24khz_mono: bytes, *, flush: bool = False) -> bytes:
        if self._tsm is None:
            return pcm_24khz_mono

        samples = np.frombuffer(pcm_24khz_mono, dtype="<i2")
        normalized = samples.astype(np.float32) / 32768.0
        reader = ArrayReader(normalized.reshape(1, -1))
        writer = ArrayWriter(1)
        self._tsm.run(reader, writer, flush=flush)
        output = writer.data[0]
        if output.size == 0:
            return b""
        output = np.clip(output * 32768.0, -32768, 32767).astype("<i2")
        return output.tobytes()

    def set_speed(self, speed: float) -> None:
        if not 1.0 <= speed <= 1.25:
            raise ValueError("speech speed must be between 1.0 and 1.25")
        if abs(speed - self.speed) < 0.001:
            return
        if self._tsm is None:
            self._tsm = wsola(1, speed=speed)
        else:
            self._tsm.set_speed(speed)
        self.speed = speed

    def flush(self) -> bytes:
        padding = b"\0" * (self.FLUSH_PADDING_SAMPLES * 2)
        return self.convert(padding, flush=True)

    def clear(self) -> None:
        if self._tsm is not None:
            self._tsm.clear()


@dataclass(frozen=True)
class PlaybackItem:
    generation: int
    data: bytes = b""
    flush: bool = False
    force_start: bool = False


class AudioCapture:
    """Capture BlackHole audio and deliver 16 kHz mono PCM chunks to asyncio."""

    def __init__(
        self,
        device: DeviceChoice,
        deliver: Callable[[bytes], None],
        block_ms: int = 40,
    ):
        self.device = device
        self.deliver = deliver
        self.converter = PCMConverter(device.sample_rate, 16_000, device.channels, 1)
        self.stream = sd.RawInputStream(
            device=device.index,
            samplerate=device.sample_rate,
            channels=device.channels,
            dtype="int16",
            blocksize=max(1, int(device.sample_rate * block_ms / 1000)),
            latency="low",
            callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        del frames, time_info
        if status:
            print(f"\n[Audio input warning] {status}")
        converted = self.converter.convert(bytes(indata))
        if converted:
            self.deliver(converted)

    def start(self) -> None:
        self.stream.start()

    def stop(self) -> None:
        self.stream.stop()
        self.stream.close()


class AudioPlayer:
    """Non-blocking translated-audio playback on a physical output device."""

    SOURCE_BYTES_PER_MS = 24_000 * 2 // 1000
    PLAYBACK_BLOCK_MS = 40
    PLAYBACK_BLOCK_BYTES = 24_000 * 2 * PLAYBACK_BLOCK_MS // 1000
    OUTPUT_CALLBACK_MS = 20

    def __init__(
        self,
        device: DeviceChoice,
        speech_speed: float = 1.0,
        output_rate: int | None = None,
        playback_profile: str = "balanced-smooth",
    ):
        self.device = device
        self.profile: PlaybackProfile = get_playback_profile(playback_profile)
        self.speech_speed = speech_speed
        self.normal_speed = speech_speed
        self.catchup_speed = min(
            1.25,
            max(self.normal_speed, self.profile.catchup_speed),
        )
        self.current_speed = speech_speed
        self.output_rate = output_rate or device.sample_rate
        self.tempo = SpeechTempoConverter(speech_speed)
        self.converter = PCMConverter(24_000, self.output_rate, 1, device.channels)
        self.queue: queue.Queue[PlaybackItem | None] = queue.Queue()
        self.queued_bytes = 0
        self.pending_source_bytes = 0
        self.generation = 0
        self.queue_lock = threading.Lock()
        self.output_lock = threading.Lock()
        self.output_buffer = bytearray()
        self.playback_ready = False
        self.turn_audio_active = False
        self.turn_end_requested = False
        self.starving = False
        self.underflow_count = 0
        self.device_underflow_count = 0
        self.last_buffer_warning = ""
        self.target_buffer_ms = self.profile.start_buffer_ms
        self.last_underflow_at = 0.0
        self.last_buffer_recovery_at = time.monotonic()
        self.backlog_warning_emitted = False
        output_blocksize = max(1, self.output_rate * self.OUTPUT_CALLBACK_MS // 1000)
        self.stream = sd.RawOutputStream(
            device=device.index,
            samplerate=self.output_rate,
            channels=device.channels,
            dtype="int16",
            blocksize=output_blocksize,
            latency="high",
            callback=self._output_callback,
        )
        self.output_latency = float(self.stream.latency)
        self.thread = threading.Thread(target=self._worker, daemon=True)

    def start(self) -> None:
        self.stream.start()
        self.thread.start()

    def has_audio(self) -> bool:
        """Hali ijro etilmagan (yoki ijro etilayotgan) tarjima audio bormi.

        Qulfsiz, taxminiy o'qish — duplex feedback-gate uchun ishlatiladi va
        PortAudio capture callback'ida chaqiriladi, shuning uchun kutish
        (lock) mumkin emas. GIL ostida int/len o'qish xavfsiz.
        """
        return (
            self.queued_bytes > 0
            or self.pending_source_bytes > 0
            or len(self.output_buffer) > 0
        )

    def play(self, pcm_24khz_mono: bytes) -> None:
        with self.queue_lock:
            maximum = self.profile.maximum_backlog_ms * self.SOURCE_BYTES_PER_MS
            if (
                self.profile.clear_when_backlog_full
                and self.queued_bytes + len(pcm_24khz_mono) > maximum
            ):
                self._clear_unlocked()
            elif (
                not self.backlog_warning_emitted
                and self.queued_bytes + len(pcm_24khz_mono) > maximum
            ):
                self.backlog_warning_emitted = True
                print(
                    "\n[Audio buffer warning] Tarjima audiosi 10 soniyadan "
                    "ko‘proq ortda qoldi; audio tashlab yuborilmadi."
                )
            self.queued_bytes += len(pcm_24khz_mono)
            self.queue.put_nowait(PlaybackItem(self.generation, pcm_24khz_mono))

    def flush(self, *, force_start: bool = False) -> None:
        with self.queue_lock:
            self.queue.put_nowait(
                PlaybackItem(
                    self.generation,
                    flush=True,
                    force_start=force_start,
                )
            )

    def clear(self) -> None:
        with self.queue_lock:
            self._clear_unlocked()

    def _clear_unlocked(self) -> None:
        self.generation += 1
        self.pending_source_bytes = 0
        self.backlog_warning_emitted = False
        self._discard_output()
        while True:
            try:
                item = self.queue.get_nowait()
                if item is not None:
                    self.queued_bytes = max(0, self.queued_bytes - len(item.data))
            except queue.Empty:
                return

    def _worker(self) -> None:
        active_generation = self.generation
        pending = bytearray()
        playback_active = False
        flush_requested = False
        force_start_requested = False
        while True:
            if active_generation != self.generation:
                pending.clear()
                self.tempo.clear()
                self.converter.clear()
                self._discard_output()
                active_generation = self.generation
                playback_active = False
                flush_requested = False
                force_start_requested = False

            if playback_active and len(pending) >= self.PLAYBACK_BLOCK_BYTES:
                source = bytes(pending[: self.PLAYBACK_BLOCK_BYTES])
                del pending[: self.PLAYBACK_BLOCK_BYTES]
                self._set_pending_source_bytes(len(pending))
                self._write_source(source)
                continue

            if flush_requested:
                self._write_source(
                    bytes(pending),
                    flush=True,
                    force_start=force_start_requested,
                )
                pending.clear()
                self._set_pending_source_bytes(0)
                playback_active = False
                flush_requested = False
                force_start_requested = False
                continue

            source_start_bytes = self.target_buffer_ms * self.SOURCE_BYTES_PER_MS
            if not playback_active and len(pending) >= source_start_bytes:
                playback_active = True
                continue

            item = self.queue.get()
            if item is None:
                return
            with self.queue_lock:
                self.queued_bytes = max(0, self.queued_bytes - len(item.data))
            if item.generation != active_generation:
                pending.clear()
                self.tempo.clear()
                self.converter.clear()
                self._discard_output()
                active_generation = item.generation
                playback_active = False
                flush_requested = False
                force_start_requested = False
            if item.data:
                pending.extend(item.data)
                self._set_pending_source_bytes(len(pending))
            if item.flush:
                playback_active = True
                flush_requested = True
                force_start_requested = item.force_start

    def _set_pending_source_bytes(self, value: int) -> None:
        with self.queue_lock:
            self.pending_source_bytes = value

    def backlog_ms(self) -> int:
        with self.queue_lock:
            source_bytes = self.queued_bytes + self.pending_source_bytes
        with self.output_lock:
            output_bytes = len(self.output_buffer)
        source_ms = source_bytes // self.SOURCE_BYTES_PER_MS
        output_bytes_per_ms = max(
            1, self.output_rate * self.device.channels * 2 // 1000
        )
        return int(source_ms + output_bytes // output_bytes_per_ms)

    def _speed_for_backlog(self, backlog_ms: int) -> float:
        if not self.profile.adaptive:
            return self.normal_speed
        if backlog_ms < self.profile.low_water_ms:
            return 1.0
        if backlog_ms > self.profile.high_water_ms:
            return self.catchup_speed
        return self.normal_speed

    def _update_tempo(self) -> None:
        speed = self._speed_for_backlog(self.backlog_ms())
        self.tempo.set_speed(speed)
        self.current_speed = speed

    def _recover_target_buffer(self) -> None:
        if not self.profile.adaptive:
            return
        now = time.monotonic()
        if self.target_buffer_ms <= self.profile.start_buffer_ms:
            return
        if now - self.last_underflow_at < self.profile.recovery_interval_seconds:
            return
        if now - self.last_buffer_recovery_at < 10.0:
            return
        self.target_buffer_ms = max(
            self.profile.start_buffer_ms,
            self.target_buffer_ms - 100,
        )
        self.last_buffer_recovery_at = now

    def _write_source(
        self,
        source: bytes,
        *,
        flush: bool = False,
        force_start: bool = False,
    ) -> None:
        self._recover_target_buffer()
        self._update_tempo()
        paced = self.tempo.convert(source)
        if flush:
            paced += self.tempo.flush()
        converted = self.converter.convert(paced)
        with self.output_lock:
            if converted:
                self.output_buffer.extend(converted)
                self.turn_audio_active = True
                if not flush:
                    self.turn_end_requested = False
            output_bytes_per_ms = max(
                1, self.output_rate * self.device.channels * 2 // 1000
            )
            required_ms = self.target_buffer_ms
            if flush and not force_start:
                required_ms = min(
                    required_ms,
                    self.profile.minimum_flush_start_ms,
                )
            required_bytes = required_ms * output_bytes_per_ms
            if not self.playback_ready and (
                len(self.output_buffer) >= required_bytes or force_start
            ):
                self.playback_ready = True
                self.starving = False
            if flush:
                self.turn_end_requested = True
        if flush:
            self.converter.clear()

    def _output_callback(self, outdata, frames, _time_info, status) -> None:  # noqa: ANN001
        needed = frames * self.device.channels * 2
        if status and status.output_underflow:
            self.device_underflow_count += 1
            self.last_buffer_warning = "CoreAudio callback underflow"

        with self.output_lock:
            if not self.playback_ready:
                outdata[:] = b"\0" * needed
                return

            available = min(needed, len(self.output_buffer))
            if available:
                outdata[:available] = self.output_buffer[:available]
                del self.output_buffer[:available]
            if available < needed:
                outdata[available:needed] = b"\0" * (needed - available)
                if self.turn_end_requested:
                    self.playback_ready = False
                    self.turn_audio_active = False
                    self.turn_end_requested = False
                    self.starving = False
                elif self.turn_audio_active and not self.starving:
                    self._register_underflow()

    def _register_underflow(self) -> None:
        self.underflow_count += 1
        self.starving = True
        self.playback_ready = False
        if self.profile.adaptive:
            self.last_underflow_at = time.monotonic()
            self.last_buffer_recovery_at = self.last_underflow_at
            self.target_buffer_ms = min(
                self.profile.maximum_target_buffer_ms,
                self.target_buffer_ms + self.profile.underflow_buffer_step_ms,
            )
        self.last_buffer_warning = (
            f"translation buffer reloading (target {self.target_buffer_ms} ms)"
        )

    def metrics(self) -> dict[str, int | float | str]:
        return {
            "profile": self.profile.code,
            "backlog_ms": self.backlog_ms(),
            "target_buffer_ms": self.target_buffer_ms,
            "speed": self.current_speed,
            "underflows": self.underflow_count,
            "device_underflows": self.device_underflow_count,
        }

    def _discard_output(self) -> None:
        with self.output_lock:
            self.output_buffer.clear()
            self.playback_ready = False
            self.turn_audio_active = False
            self.turn_end_requested = False
            self.starving = False

    def stop(self) -> None:
        # Finish a short buffered tail cleanly, but never wait indefinitely.
        self.flush(force_start=True)
        self.queue.put_nowait(None)
        self.thread.join(timeout=3)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with self.output_lock:
                if not self.output_buffer:
                    break
            time.sleep(0.01)
        self.stream.stop()
        self.stream.close()
