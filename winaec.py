"""Microsoft AEC (Voice Capture DSP) — karnayda Ctrl'siz erkin gapirish.

Windows'ning O'ZIDA turgan, Skype/Lync yillar davomida ishlatgan exo-bekor
qilgich (mfwmaaec.dll, CLSID "AEC"). Source-mode'da DMO mikrofonni HAM,
karnay loopback'ini HAM o'zi ochadi va exo o'chirilgan toza 16 kHz mono
mikrofon signalini qaytaradi. Drayver yozish/imzolash KERAK EMAS.

Nega alohida JARAYON: DMO streaming yo'li audio sessiyasiz muhitda (masalan
SSH Session 0) AccessViolation berishi mumkin — Python uni ushlay olmaydi
(jarayon o'ladi). Worker alohida jarayonda ishlaydi: o'lsa dvigatel OMON
qoladi va avtomatik push-to-talk (Ctrl) rejimiga qaytadi.

Oqim:
  engine (translator.py)                 worker (shu exe --winaec-worker)
  WinAECCapture.start() --spawn--> run_worker(): DMO source mode
        ^                                 | stdout = xom PCM 16k mono
        +---- reader thread <-------------+ stderr = diagnostika
        deliver() -> Translator._from_audio_thread (AudioCapture bilan bir xil)

Sinov holati (2026-07-24, maqsad mashinada SSH orqali): xossalar, format,
AllocateStreamingResources, GetStreamCount(in=0,out=1 = source mode) HAMMASI
OK; streaming faqat interaktiv sessiyada tekshiriladi (Session 0 cheklovi).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
import uuid

S_OK = 0
S_FALSE = 1
E_NOINTERFACE = -2147467262  # 0x80004002
DMO_OUTPUT_INCOMPLETE = 0x01000000

CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
CLSID_CWMAudioAEC = "745057C7-F353-4F2D-A7EE-58434477730E"
IID_IMediaObject = "D8AD0F58-5494-4102-97C5-EC798E59BCF4"
IID_IPropertyStore = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
IID_IMediaBuffer = "59EFF8B9-938C-4A26-82F2-95CB84CDC837"
IID_IUnknown = "00000000-0000-0000-C000-000000000046"

# MFPKEY_WMAAECMA_* (wmcodecdsp) — maqsad mashinada zond bilan tasdiqlangan.
AEC_FMTID = "6F52C567-0360-4BD2-9617-CCBF1421C939"
PID_SYSTEM_MODE = 2      # 0 = SINGLE_CHANNEL_AEC
PID_SOURCE_MODE = 3      # TRUE = DMO qurilmalarni o'zi ochadi
PID_DEVICE_INDEXES = 4   # (spk_idx << 16) | mic_idx (ACTIVE tartibda)
PID_FEATURE_MODE = 5
PID_ECHO_LENGTH = 7      # ms
PID_AES = 10             # qo'shimcha exo bosish (0/1/2)

PKEY_FRIENDLY_NAME_FMTID = "A45C254E-DF1C-4EFD-8020-67D146A850E0"
PKEY_FRIENDLY_NAME_PID = 14

# PKEY_AudioEndpoint_FormFactor — Windows'ning O'ZI bergan qurilma TURI.
# Nomga qarab taxmin qilishdan ancha ishonchli: "Динамики (P2961)" (monitor
# karnayi) nomida "realtek" yo'q, lekin FormFactor=Speakers deb aniq aytadi.
PKEY_FORM_FACTOR_FMTID = "1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E"
PKEY_FORM_FACTOR_PID = 0

FF_REMOTE_NETWORK = 0
FF_SPEAKERS = 1
FF_LINE_LEVEL = 2
FF_HEADPHONES = 3
FF_MICROPHONE = 4
FF_HEADSET = 5
FF_HANDSET = 6
FF_DIGITAL_PASSTHROUGH = 7
FF_SPDIF = 8
FF_DIGITAL_DISPLAY = 9   # HDMI/DisplayPort — monitor/TV karnayi
FF_UNKNOWN = 10
# Ovoz QULOQQA chiqadigan (mikrofon eshitmaydigan) turlar — echo xavfi yo'q.
FF_EAR_SAFE = (FF_HEADPHONES, FF_HEADSET)


class GUID(ctypes.Structure):
    _fields_ = [("b", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        g = cls()
        g.b[:] = uuid.UUID(text).bytes_le
        return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]


class PROPVARIANT(ctypes.Structure):
    # 24 bayt (x64): vt(2) + 3 zaxira word + 16 bayt ma'lumot.
    _fields_ = [
        ("vt", ctypes.c_uint16),
        ("r1", ctypes.c_uint16),
        ("r2", ctypes.c_uint16),
        ("r3", ctypes.c_uint16),
        ("data", ctypes.c_uint64),
        ("data2", ctypes.c_uint64),
    ]


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", ctypes.c_uint16),
        ("nChannels", ctypes.c_uint16),
        ("nSamplesPerSec", ctypes.c_uint32),
        ("nAvgBytesPerSec", ctypes.c_uint32),
        ("nBlockAlign", ctypes.c_uint16),
        ("wBitsPerSample", ctypes.c_uint16),
        ("cbSize", ctypes.c_uint16),
    ]


class DMO_MEDIA_TYPE(ctypes.Structure):
    _fields_ = [
        ("majortype", GUID),
        ("subtype", GUID),
        ("bFixedSizeSamples", ctypes.c_int32),
        ("bTemporalCompression", ctypes.c_int32),
        ("lSampleSize", ctypes.c_uint32),
        ("formattype", GUID),
        ("pUnk", ctypes.c_void_p),
        ("cbFormat", ctypes.c_uint32),
        ("pbFormat", ctypes.c_void_p),
    ]


class DMO_OUTPUT_DATA_BUFFER(ctypes.Structure):
    _fields_ = [
        ("pBuffer", ctypes.c_void_p),
        ("dwStatus", ctypes.c_uint32),
        ("rtTimestamp", ctypes.c_longlong),
        ("rtTimelength", ctypes.c_longlong),
    ]


def _call(ptr: int, index: int, argtypes: tuple, *args):  # noqa: ANN001
    """COM vtable metodini raqami bo'yicha chaqirish (HRESULT qaytaradi)."""
    vtbl = ctypes.cast(
        ctypes.c_void_p(ptr), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(ptr, *args)


def _co_create(clsid: str, iid: str) -> int:
    ole32 = ctypes.windll.ole32
    ptr = ctypes.c_void_p(0)
    hr = ole32.CoCreateInstance(
        ctypes.byref(GUID.of(clsid)),
        None,
        0x17,  # CLSCTX_ALL
        ctypes.byref(GUID.of(iid)),
        ctypes.byref(ptr),
    )
    if hr != S_OK or not ptr.value:
        raise OSError(f"CoCreateInstance({clsid}) hr=0x{hr & 0xFFFFFFFF:08X}")
    return ptr.value


def _release(ptr: int) -> None:
    with_suppress = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    try:
        vtbl = ctypes.cast(
            ctypes.c_void_p(ptr), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        with_suppress(vtbl[2])(ptr)
    except Exception:
        pass


def _norm(name: str) -> str:
    return " ".join((name or "").casefold().split())


def active_endpoints(flow: int) -> list[tuple[str, int]]:
    """ACTIVE endpointlar (nom, FormFactor) — DMO DEVICE_INDEXES ko'radigan
    TARTIBDA (EnumAudioEndpoints). flow: 0=render(karnay), 1=capture(mikrofon).
    FormFactor o'qilmasa -1."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)  # MTA; allaqachon boshqa rejim bo'lsa ham davom
    enum = _co_create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    items: list[tuple[str, int]] = []
    try:
        col = ctypes.c_void_p(0)
        hr = _call(
            enum, 3,
            (ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)),
            flow, 1, ctypes.byref(col),
        )
        if hr != S_OK or not col.value:
            raise OSError(f"EnumAudioEndpoints hr=0x{hr & 0xFFFFFFFF:08X}")
        try:
            count = ctypes.c_uint32(0)
            _call(col.value, 3, (ctypes.POINTER(ctypes.c_uint32),), ctypes.byref(count))
            name_key = PROPERTYKEY(
                GUID.of(PKEY_FRIENDLY_NAME_FMTID), PKEY_FRIENDLY_NAME_PID
            )
            ff_key = PROPERTYKEY(
                GUID.of(PKEY_FORM_FACTOR_FMTID), PKEY_FORM_FACTOR_PID
            )
            for i in range(count.value):
                dev = ctypes.c_void_p(0)
                if _call(
                    col.value, 4,
                    (ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)),
                    i, ctypes.byref(dev),
                ) != S_OK or not dev.value:
                    items.append(("", -1))
                    continue
                try:
                    store = ctypes.c_void_p(0)
                    if _call(
                        dev.value, 4,
                        (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                        0, ctypes.byref(store),
                    ) != S_OK or not store.value:
                        items.append(("", -1))
                        continue
                    try:
                        name = ""
                        pv = PROPVARIANT()
                        if _call(
                            store.value, 5,
                            (ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)),
                            ctypes.byref(name_key), ctypes.byref(pv),
                        ) == S_OK and pv.vt == 31 and pv.data:  # VT_LPWSTR
                            name = (
                                ctypes.cast(
                                    ctypes.c_void_p(pv.data), ctypes.c_wchar_p
                                ).value
                                or ""
                            )
                        ctypes.windll.ole32.PropVariantClear(ctypes.byref(pv))

                        form_factor = -1
                        pv2 = PROPVARIANT()
                        if _call(
                            store.value, 5,
                            (ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)),
                            ctypes.byref(ff_key), ctypes.byref(pv2),
                        ) == S_OK and pv2.vt in (2, 3, 18, 19):  # I2/I4/UI2/UI4
                            form_factor = int(pv2.data & 0xFFFF)
                        ctypes.windll.ole32.PropVariantClear(ctypes.byref(pv2))

                        items.append((name, form_factor))
                    finally:
                        _release(store.value)
                finally:
                    _release(dev.value)
        finally:
            _release(col.value)
    finally:
        _release(enum)
    return items


def active_device_names(flow: int) -> list[str]:
    """ACTIVE endpoint NOMLARI (DMO indeks tartibida)."""
    return [name for name, _ff in active_endpoints(flow)]


def endpoint_form_factor(name: str, flow: int = 0) -> int:
    """Nomiga mos ACTIVE endpoint turi (FF_* konstantalari), topilmasa -1.

    Nomga qarab taxmin qilishdan ishonchli: Windows'ning o'zi qurilma
    naushnikmi/karnaymi/HDMI-monitormi ekanini aytadi."""
    try:
        endpoints = active_endpoints(flow)
    except Exception:
        return -1
    index = match_device_index(name, [n for n, _ff in endpoints])
    if index < 0:
        return -1
    return endpoints[index][1]


def output_is_ear_safe(name: str) -> bool | None:
    """Chiqish QULOQQA boradimi (naushnik/garnitura — mikrofon eshitmaydi)?

    True = naushnik/garnitura, False = karnay/HDMI/boshqa (echo xavfi bor),
    None = aniqlab bo'lmadi (chaqiruvchi nom bo'yicha taxminga tushadi)."""
    kind = endpoint_form_factor(name, 0)
    if kind < 0 or kind == FF_UNKNOWN:
        return None
    return kind in FF_EAR_SAFE


def default_endpoint_name(flow: int = 0) -> str:
    """Hozirgi ASOSIY (default) qurilma nomi — TEZ (ctypes, ~10 ms).

    Ilgari buni PowerShell (`getdefaults`) qilardi: har chaqiruv C#
    kompilyatsiya bilan ~2 SONIYA va Start bosilganda GUI qotardi.
    Bu yerda o'sha ma'lumot bevosita COM orqali olinadi."""
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        enum = _co_create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    except Exception:
        return ""
    try:
        dev = ctypes.c_void_p(0)
        # GetDefaultAudioEndpoint (vtable 4), role 0 = eConsole
        if _call(
            enum, 4,
            (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
            flow, 0, ctypes.byref(dev),
        ) != S_OK or not dev.value:
            return ""
        try:
            store = ctypes.c_void_p(0)
            if _call(
                dev.value, 4,
                (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                0, ctypes.byref(store),
            ) != S_OK or not store.value:
                return ""
            try:
                key = PROPERTYKEY(
                    GUID.of(PKEY_FRIENDLY_NAME_FMTID), PKEY_FRIENDLY_NAME_PID
                )
                pv = PROPVARIANT()
                name = ""
                if _call(
                    store.value, 5,
                    (ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)),
                    ctypes.byref(key), ctypes.byref(pv),
                ) == S_OK and pv.vt == 31 and pv.data:
                    name = (
                        ctypes.cast(ctypes.c_void_p(pv.data), ctypes.c_wchar_p).value
                        or ""
                    )
                ctypes.windll.ole32.PropVariantClear(ctypes.byref(pv))
                return name
            finally:
                _release(store.value)
        finally:
            _release(dev.value)
    except Exception:
        return ""
    finally:
        _release(enum)


def match_device_index(target: str, names: list[str]) -> int:
    """PortAudio nomi (qisqartirilgan bo'lishi mumkin) -> MMDevice indeksi."""
    t = _norm(target)
    if not t:
        return -1
    for i, n in enumerate(names):
        if _norm(n) == t:
            return i
    for i, n in enumerate(names):
        nn = _norm(n)
        if nn and (t in nn or nn in t):
            return i
    # PortAudio ~31 belgida kesadi — prefiks bo'yicha ham urinamiz.
    for i, n in enumerate(names):
        nn = _norm(n)
        if nn and (nn.startswith(t[:28]) or t.startswith(nn[:28])):
            return i
    return -1


class _MediaBufferCOM:
    """IMediaBuffer'ning sof-ctypes COM implementatsiyasi (DMO'ga beriladi)."""

    def __init__(self, size: int):
        self.raw = ctypes.create_string_buffer(size)
        self.size = size
        self.length = 0
        HRESULT = ctypes.c_long
        VP = ctypes.c_void_p
        self._iid_self = bytes(GUID.of(IID_IMediaBuffer).b)
        self._iid_unk = bytes(GUID.of(IID_IUnknown).b)

        @ctypes.WINFUNCTYPE(HRESULT, VP, VP, ctypes.POINTER(VP))
        def _qi(this, riid, ppv):  # noqa: ANN001
            try:
                got = bytes(ctypes.cast(riid, ctypes.POINTER(ctypes.c_ubyte * 16)).contents)
                if got in (self._iid_self, self._iid_unk):
                    ppv[0] = this
                    return S_OK
                ppv[0] = None
                return E_NOINTERFACE
            except Exception:
                return E_NOINTERFACE

        @ctypes.WINFUNCTYPE(ctypes.c_ulong, VP)
        def _addref(this):  # noqa: ANN001
            return 2

        @ctypes.WINFUNCTYPE(ctypes.c_ulong, VP)
        def _rel(this):  # noqa: ANN001
            return 1

        @ctypes.WINFUNCTYPE(HRESULT, VP, ctypes.c_ulong)
        def _setlen(this, n):  # noqa: ANN001
            self.length = min(int(n), self.size)
            return S_OK

        @ctypes.WINFUNCTYPE(HRESULT, VP, ctypes.POINTER(ctypes.c_ulong))
        def _getmax(this, p):  # noqa: ANN001
            if p:
                p[0] = self.size
            return S_OK

        @ctypes.WINFUNCTYPE(
            HRESULT, VP, ctypes.POINTER(VP), ctypes.POINTER(ctypes.c_ulong)
        )
        def _getbl(this, ppb, plen):  # noqa: ANN001
            if ppb:
                ppb[0] = ctypes.addressof(self.raw)
            if plen:
                plen[0] = self.length
            return S_OK

        # Callback obyektlarini yashatib turish SHART (GC dan himoya).
        self._funcs = (_qi, _addref, _rel, _setlen, _getmax, _getbl)
        self._vtbl = (ctypes.c_void_p * 6)(
            *(ctypes.cast(f, ctypes.c_void_p).value for f in self._funcs)
        )
        self._this = ctypes.c_void_p(ctypes.addressof(self._vtbl))
        self.com_ptr = ctypes.addressof(self._this)


def run_worker(mic_name: str, speaker_name: str) -> int:
    """DMO source-mode ishchi jarayoni: stdout'ga xom PCM 16k mono yozadi."""
    err = sys.stderr

    def log(msg: str) -> None:
        try:
            err.write(f"[winaec-worker] {msg}\n")
            err.flush()
        except Exception:
            pass

    try:
        out = sys.stdout.buffer
    except AttributeError:
        log("stdout binary emas")
        return 2

    ctypes.windll.ole32.CoInitializeEx(None, 0)
    renders = active_device_names(0)
    captures = active_device_names(1)
    spk = match_device_index(speaker_name, renders)
    mic = match_device_index(mic_name, captures)
    log(f"karnay={spk} ({speaker_name!r}) mikrofon={mic} ({mic_name!r})")
    log(f"RENDER={renders} CAPTURE={captures}")
    if spk < 0 or mic < 0:
        log("qurilma topilmadi")
        return 3

    dmo = _co_create(CLSID_CWMAudioAEC, IID_IMediaObject)
    try:
        # IPropertyStore
        ps = ctypes.c_void_p(0)
        hr = _call(
            dmo, 0,
            (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
            ctypes.byref(GUID.of(IID_IPropertyStore)), ctypes.byref(ps),
        )
        if hr != S_OK or not ps.value:
            log(f"IPropertyStore QI hr=0x{hr & 0xFFFFFFFF:08X}")
            return 4

        def set_prop(pid: int, vt: int, value: int) -> None:
            key = PROPERTYKEY(GUID.of(AEC_FMTID), pid)
            pv = PROPVARIANT(vt=vt)
            pv.data = value & 0xFFFFFFFFFFFFFFFF
            hr2 = _call(
                ps.value, 6,
                (ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)),
                ctypes.byref(key), ctypes.byref(pv),
            )
            log(f"prop pid={pid} vt={vt} val={value} hr=0x{hr2 & 0xFFFFFFFF:08X}")

        VT_I4, VT_BOOL = 3, 11
        TRUE16 = 0xFFFF
        set_prop(PID_SOURCE_MODE, VT_BOOL, TRUE16)
        set_prop(PID_SYSTEM_MODE, VT_I4, 0)
        set_prop(PID_DEVICE_INDEXES, VT_I4, ((spk & 0xFFFF) << 16) | (mic & 0xFFFF))
        set_prop(PID_FEATURE_MODE, VT_BOOL, TRUE16)
        set_prop(PID_ECHO_LENGTH, VT_I4, 512)
        set_prop(PID_AES, VT_I4, 1)
        _release(ps.value)

        # Chiqish formati: PCM 16 kHz mono 16-bit (Gemini kutgani bilan mos).
        wfx = WAVEFORMATEX(1, 1, 16_000, 32_000, 2, 16, 0)
        mt = DMO_MEDIA_TYPE(
            majortype=GUID.of("73647561-0000-0010-8000-00AA00389B71"),
            subtype=GUID.of("00000001-0000-0010-8000-00AA00389B71"),
            bFixedSizeSamples=1,
            bTemporalCompression=0,
            lSampleSize=2,
            formattype=GUID.of("05589F81-C356-11CE-BF01-00AA0055595A"),
            pUnk=None,
            cbFormat=ctypes.sizeof(wfx),
            pbFormat=ctypes.addressof(wfx),
        )
        hr = _call(
            dmo, 9,
            (ctypes.c_uint32, ctypes.POINTER(DMO_MEDIA_TYPE), ctypes.c_uint32),
            0, ctypes.byref(mt), 0,
        )
        log(f"SetOutputType hr=0x{hr & 0xFFFFFFFF:08X}")
        if hr != S_OK:
            return 5
        hr = _call(dmo, 18, ())
        log(f"AllocateStreamingResources hr=0x{hr & 0xFFFFFFFF:08X}")
        if hr != S_OK:
            return 6

        mb = _MediaBufferCOM(65536)
        odb = DMO_OUTPUT_DATA_BUFFER()
        status = ctypes.c_uint32(0)
        total = 0
        last_beat = time.monotonic()
        while True:
            mb.length = 0
            odb.pBuffer = mb.com_ptr
            odb.dwStatus = 0
            odb.rtTimestamp = 0
            odb.rtTimelength = 0
            hr = _call(
                dmo, 22,
                (
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_uint32),
                ),
                0, 1, ctypes.addressof(odb), ctypes.byref(status),
            )
            if hr == S_OK and mb.length:
                try:
                    out.write(mb.raw.raw[: mb.length])
                    out.flush()
                except (BrokenPipeError, OSError):
                    log("quvur yopildi — chiqamiz")
                    return 0
                total += mb.length
            elif hr == S_FALSE or (hr == S_OK and not mb.length):
                time.sleep(0.005)
            else:
                log(f"ProcessOutput hr=0x{hr & 0xFFFFFFFF:08X} — to'xtadik")
                return 7
            now = time.monotonic()
            if now - last_beat >= 5.0:
                last_beat = now
                log(f"oqim: jami {total} bayt")
            if odb.dwStatus & DMO_OUTPUT_INCOMPLETE:
                continue
    finally:
        with_ignore = None  # nomlar uchun
        del with_ignore
        try:
            _call(dmo, 19, ())  # FreeStreamingResources
        except Exception:
            pass
        _release(dmo)


class WinAECCapture:
    """Dvigatel tomoni: worker jarayonini boshqaradi, PCM'ni yetkazadi.

    AudioCapture bilan bir xil interfeys (start/stop). Worker birinchi
    ma'lumotni FIRST_DATA_TIMEOUT ichida bermasa yoki o'lib qolsa —
    xavfsiz yo'lga o'tadi: oddiy mikrofon capture + on_fallback()
    (push-to-talk gate o'rnatadi). Ikki tomonlama hech qachon "jim mikrofon
    + himoyasiz halqa" holatida qolmaydi.
    """

    # Birinchi audio shu vaqt ichida kelmasa — AEC ishlamadi deb hisoblaymiz.
    # Bu oyna davomida gapirish mikrofoni JIM (worker hali audio bermagan),
    # shuning uchun uzun bo'lmasligi kerak: 4s yetarli, aks holda
    # foydalanuvchining birinchi gapi yo'qoladi.
    FIRST_DATA_TIMEOUT = 9.0

    def __init__(self, mic_device, speaker_name, deliver, on_fallback, log=print):  # noqa: ANN001
        self.mic_device = mic_device
        self.speaker_name = speaker_name
        self.deliver = deliver
        self.on_fallback = on_fallback
        self.log = log
        self.proc: subprocess.Popen | None = None
        self._stopped = False
        self._got_data = False
        self._fb_capture = None
        self._fb_done = False
        self._threads: list[threading.Thread] = []

    def _spawn(self) -> subprocess.Popen:
        worker_args = [
            "--winaec-worker",
            "--winaec-mic", self.mic_device.name,
            "--winaec-speaker", self.speaker_name,
        ]
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--engine", *worker_args]
        else:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translator.py")
            cmd = [sys.executable, script, *worker_args]
        env = os.environ.copy()
        # Worker o'z stdout'iga XOM PCM yozadi; engine.log tee'si unga
        # tegmasin (aks holda engine logini ochib buzardi).
        env.pop("LIVE_TRANSLATOR_ENGINE_LOG", None)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=flags,
        )

    def start(self) -> None:
        self.log(
            "[AEC] ishga tushmoqda: exo-bekor qilish (mikrofon "
            f"{self.mic_device.name!r}, karnay {self.speaker_name!r})…"
        )
        try:
            self.proc = self._spawn()
        except Exception as error:
            self._fallback(f"worker ishga tushmadi: {error}")
            return
        t_read = threading.Thread(target=self._read_loop, daemon=True)
        t_err = threading.Thread(target=self._err_loop, daemon=True)
        t_watch = threading.Thread(target=self._watchdog, daemon=True)
        self._threads = [t_read, t_err, t_watch]
        for t in self._threads:
            t.start()

    def _read_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            while not self._stopped:
                chunk = proc.stdout.read(1280)
                if not chunk:
                    break
                if not self._got_data:
                    self._got_data = True
                    # Logdan AEC haqiqatan ishlaganini bilish uchun (masofadan
                    # diagnostika qilib bo'lmaydigan kompyuterlarda muhim).
                    self.log(
                        "[AEC] ISHLADI — exo-bekor qilish faol, tugma bosmasdan "
                        "erkin gapiring."
                    )
                self.deliver(chunk)
        except Exception:
            pass
        if not self._stopped:
            self._fallback("worker oqimi uzildi")

    def _err_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in iter(proc.stderr.readline, b""):
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    self.log(f"[AEC] {text}")
                if self._stopped:
                    break
        except Exception:
            pass

    def _watchdog(self) -> None:
        deadline = time.monotonic() + self.FIRST_DATA_TIMEOUT
        while not self._stopped and time.monotonic() < deadline:
            if self._got_data:
                return
            proc = self.proc
            if proc is not None and proc.poll() is not None:
                break
            time.sleep(0.2)
        if not self._stopped and not self._got_data:
            self._fallback("worker audio bermadi")

    def _fallback(self, reason: str) -> None:
        if self._fb_done or self._stopped:
            return
        self._fb_done = True
        self.log(
            f"[AEC] ishlamadi ({reason}) — xavfsiz rejim: Ctrl bosib gapiring."
        )
        self._kill_proc()
        try:
            from audio import AudioCapture

            self._fb_capture = AudioCapture(self.mic_device, self.deliver)
            self._fb_capture.start()
        except Exception as error:
            self.log(f"[AEC] zaxira mikrofon ham ochilmadi: {error}")
        try:
            self.on_fallback()
        except Exception:
            pass

    def _kill_proc(self) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass

    def stop(self) -> None:
        self._stopped = True
        self._kill_proc()
        fb = self._fb_capture
        if fb is not None:
            try:
                fb.stop()
            except Exception:
                pass
            self._fb_capture = None
