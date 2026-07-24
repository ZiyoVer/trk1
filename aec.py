"""Akustik exo bekor qilish (AEC) — karnayda Ctrl'siz erkin gapirish.

Muammo: ikki tomonlamada kiruvchi tarjima KARNAYda yangraydi, fizik mikrofon
uni havodan qayta eshitadi -> gapirish kanali o'sha tarjimani yana tarjima
qilib meetingga yuboradi (cheksiz halqa). Ilgari yechim mikrofonni yopish
edi (gate / push-to-talk) — foydalanuvchi erkin gapira olmasdi.

Bu modul Zoom/Meet ishlatadigan yo'lni qo'llaydi: biz karnayga NIMA
chiqarayotganimizni aniq bilamiz (o'zimiz ijro qilamiz). O'sha "reference"
signalni mikrofon signalidan adaptiv filtr bilan ayirib tashlaymiz:

  mikrofon = foydalanuvchi_nutqi + exo(karnay)   ->   AEC   ->  nutq

Arxitektura:
  AudioPlayer._output_callback (chiqish oqimi, jimlik ham)
      -> ReferenceRing.push()                (ijro nusxasi, real vaqt)
  AudioCapture -> Translator._enqueue_audio
      -> EchoCanceller.process(mic, reader)  (16 kHz mono, blokma-blok)

Filtr: PBFDAF (partitioned-block frequency-domain NLMS, overlap-save).
Blok 20 ms (320 sample @16k), 20 partitsiya = 400 ms dum — chiqish
kechikishi (~50-300 ms) + xona aks-sadosini qoplaydi. Sof numpy, drayver
yoki tashqi kutubxona KERAK EMAS.

Himoya qatlami: filtr hali moslashmagan (konvergensiya) paytda exo kuchli
qolsa, blok yumshoq susaytiriladi (halqa uzilishi kafolati); foydalanuvchi
gapirayotgan (double-talk) bloklar susaytirilmaydi va moslashuv muzlatiladi.
"""

from __future__ import annotations

import audioop
import threading
import time

import numpy as np


class ReferenceRing:
    """Karnayga chiqqan HAMMA audio (jimlik bilan) uzluksiz halqa buferi.

    AudioPlayer chiqish callback'idan push qilinadi (o'z tezligida, o'z
    kanal sonida). O'quvchilar (RingReader) undan 16 kHz mono uzluksiz
    oqim oladi. push() callback ichida ishlaydi — faqat append, tez.
    """

    def __init__(self, rate: int, channels: int, capacity_seconds: float = 4.0):
        self.rate = int(rate)
        self.channels = int(channels)
        self.frame_bytes = 2 * self.channels
        self._chunks: list[bytes] = []
        self._start_frame = 0  # _chunks[0] boshining global frame raqami
        self._written_frames = 0
        self._capacity_frames = int(self.rate * capacity_seconds)
        self._lock = threading.Lock()

    def push(self, pcm: bytes) -> None:
        if not pcm:
            return
        with self._lock:
            self._chunks.append(pcm)
            self._written_frames += len(pcm) // self.frame_bytes
            # Sig'imdan oshsa eskisini tashlaymiz.
            while (
                self._written_frames - self._start_frame > self._capacity_frames
                and self._chunks
            ):
                dropped = self._chunks.pop(0)
                self._start_frame += len(dropped) // self.frame_bytes

    def written_frames(self) -> int:
        with self._lock:
            return self._written_frames

    def read_native(self, start_frame: int, n_frames: int) -> tuple[bytes, int]:
        """[start_frame, start_frame+n) oralig'ini qaytaradi.

        Hali yozilmagan qism NOL bilan to'ldiriladi (miss soni ham qaytadi
        — moslashuvni o'sha blokda muzlatish uchun)."""
        with self._lock:
            chunks = list(self._chunks)
            base = self._start_frame
            written = self._written_frames
        out = bytearray()
        missing = 0
        pos = start_frame
        end = start_frame + n_frames
        if pos < base:  # juda orqada qolgan — eskirgan qism nol
            gap = min(base, end) - pos
            out += b"\x00" * (gap * self.frame_bytes)
            missing += gap
            pos = base
        if pos < min(end, written):
            # chunks ichidan [pos, min(end, written)) ni yig'amiz
            need_start = pos - base
            need_end = min(end, written) - base
            flat_off = 0
            for ch in chunks:
                ch_frames = len(ch) // self.frame_bytes
                lo = max(need_start - flat_off, 0)
                hi = min(need_end - flat_off, ch_frames)
                if hi > lo:
                    out += ch[lo * self.frame_bytes : hi * self.frame_bytes]
                flat_off += ch_frames
                if flat_off >= need_end:
                    break
            pos = min(end, written)
        if pos < end:  # hali yozilmagan (kelajak) — nol
            missing += end - pos
            out += b"\x00" * ((end - pos) * self.frame_bytes)
        return bytes(out), missing

    def reader(self, margin_seconds: float = 0.06) -> "RingReader":
        return RingReader(self, margin_seconds)


class RingReader:
    """ReferenceRing'dan 16 kHz mono uzluksiz o'qish (stateful resample).

    Pozitsiya yozuv boshidan `margin` orqada boshlanadi va har take(n) da
    real-vaqt tezligida oldinga suriladi — mikrofon oqimi bilan taxminan
    barqaror siljishda qoladi (qolgan farqni adaptiv filtr dumi yutadi).
    """

    def __init__(self, ring: ReferenceRing, margin_seconds: float):
        self.ring = ring
        margin = int(ring.rate * margin_seconds)
        self._pos = max(0, ring.written_frames() - margin)
        self._state = None  # audioop.ratecv holati
        self._out16k = bytearray()
        self._out_taken = 0  # 16k da chiqarilgan sample (rounding driftsiz)

    def take(self, n16k: int) -> tuple[np.ndarray, int]:
        """n16k ta 16 kHz mono sample (float32 [-1,1]) + miss soni."""
        missing_total = 0
        # Kerakli native frame soni — kumulyativ (drift yig'ilmasin).
        while len(self._out16k) < n16k * 2:
            target16k = self._out_taken + max(
                n16k, len(self._out16k) // 2
            ) + 160  # ozgina zaxira
            need_native_end = (target16k * self.ring.rate + 15_999) // 16_000
            n_native = need_native_end - self._pos
            if n_native <= 0:
                n_native = max(1, self.ring.rate // 100)
            raw, missing = self.ring.read_native(self._pos, n_native)
            self._pos += n_native
            missing_total += missing
            mono = raw
            if self.ring.channels == 2:
                mono = audioop.tomono(raw, 2, 0.5, 0.5)
            conv, self._state = audioop.ratecv(
                mono, 2, 1, self.ring.rate, 16_000, self._state
            )
            self._out16k += conv
        chunk = bytes(self._out16k[: n16k * 2])
        del self._out16k[: n16k * 2]
        self._out_taken += n16k
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        return samples, missing_total


class EchoCanceller:
    """PBFDAF exo bekor qilgich (16 kHz mono) + konvergensiya himoyasi."""

    BLOCK = 320          # 20 ms @ 16 kHz
    PARTITIONS = 20      # 20 * 20 ms = 400 ms exo dumi
    MU_FAST = 0.5        # hali moslashmagan holatda qadam
    MU_SLOW = 0.15       # moslashgandan keyin (double-talk'da 0)
    REF_ACTIVE_POW = 1e-6   # ~ -60 dBFS: ijro "bor" hisoblanadigan quvvat
    SUPPRESS_GAIN = 0.12    # moslashmagan paytdagi yumshoq susaytirish

    def __init__(self):
        n = self.BLOCK * 2
        bins = n // 2 + 1
        self._H = np.zeros((self.PARTITIONS, bins), dtype=np.complex128)
        self._Xhist = np.zeros((self.PARTITIONS, bins), dtype=np.complex128)
        self._x_prev = np.zeros(self.BLOCK, dtype=np.float32)
        self._inbuf = bytearray()
        self._erle_smooth = 1.0  # mic_pow / e_pow (lineyka), 1.0 = 0 dB
        self._converged = False
        self._last_log = 0.0
        self.blocks = 0
        self.suppressed_blocks = 0

    # --- diagnostika ---
    def erle_db(self) -> float:
        return float(10.0 * np.log10(max(self._erle_smooth, 1e-9)))

    def _log_maybe(self, log) -> None:  # noqa: ANN001
        now = time.monotonic()
        if log is not None and now - self._last_log >= 5.0:
            self._last_log = now
            log(
                f"[AEC] ERLE {self.erle_db():.1f} dB | "
                f"moslashgan={'ha' if self._converged else 'yo`q'} | "
                f"bloklar={self.blocks} susaytirilgan={self.suppressed_blocks}"
            )

    def process(self, mic16k: bytes, reader: RingReader, log=None) -> bytes:  # noqa: ANN001
        """40 ms mikrofon bo'lagini exodan tozalab qaytaradi (oqimli)."""
        self._inbuf += mic16k
        out = bytearray()
        while len(self._inbuf) >= self.BLOCK * 2:
            mic = (
                np.frombuffer(bytes(self._inbuf[: self.BLOCK * 2]), dtype="<i2")
                .astype(np.float32)
                / 32768.0
            )
            del self._inbuf[: self.BLOCK * 2]
            ref, missing = reader.take(self.BLOCK)
            out += self._process_block(mic, ref, missing)
        self._log_maybe(log)
        return bytes(out)

    def _process_block(
        self, mic: np.ndarray, ref: np.ndarray, ref_missing: int
    ) -> bytes:
        self.blocks += 1
        n = self.BLOCK * 2
        x2 = np.concatenate((self._x_prev, ref))
        self._x_prev = ref
        X = np.fft.rfft(x2)
        self._Xhist = np.roll(self._Xhist, 1, axis=0)
        self._Xhist[0] = X

        Y = (self._Xhist * self._H).sum(axis=0)
        y = np.fft.irfft(Y, n)[self.BLOCK :]
        e = mic - y

        ref_pow = float(np.mean(x2 * x2))
        mic_pow = float(np.mean(mic * mic))
        y_pow = float(np.mean(y * y))
        e_pow = float(np.mean(e * e))
        playback_active = ref_pow > self.REF_ACTIVE_POW

        if playback_active and mic_pow > 1e-8:
            ratio = mic_pow / (e_pow + 1e-12)
            self._erle_smooth = 0.9 * self._erle_smooth + 0.1 * ratio
            self._converged = self._erle_smooth > 4.0  # ~6 dB

        # Double-talk: exo bahosi (y) dan mikrofon ancha kuchli bo'lsa —
        # foydalanuvchi gapiryapti. Moslashuvni muzlatamiz (filtr buzilmasin),
        # signalni SUSAYTIRMAYMIZ (nutq o'tsin).
        double_talk = self._converged and mic_pow > 4.0 * y_pow + 1e-8

        if playback_active and ref_missing == 0 and not double_talk:
            mu = self.MU_SLOW if self._converged else self.MU_FAST
            E2 = np.fft.rfft(np.concatenate((np.zeros(self.BLOCK), e)))
            norm = (np.abs(self._Xhist) ** 2).sum(axis=0) + 1e-6
            G = mu * np.conj(self._Xhist) * E2[None, :] / norm[None, :]
            # Gradient cheklovi (overlap-save to'g'riligi): har partitsiya
            # yangilanishining ikkinchi yarmi nolga tushiriladi.
            g = np.fft.irfft(G, n, axis=1)
            g[:, self.BLOCK :] = 0.0
            self._H += np.fft.rfft(g, n, axis=1)

        # Halqa uzilishi kafolati: ijro ketyapti, filtr hali moslashmagan
        # va blok asosan exo bo'lsa — yumshoq susaytirish. Foydalanuvchi
        # nutqi (double_talk) susaytirilmaydi.
        if playback_active and not self._converged and not double_talk:
            e = e * self.SUPPRESS_GAIN
            self.suppressed_blocks += 1

        e = np.clip(e * 32768.0, -32768, 32767).astype("<i2")
        return e.tobytes()
