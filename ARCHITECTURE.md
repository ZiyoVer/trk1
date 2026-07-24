# Live Translator — Arxitektura (v0.9.39, ishlagan holat)

Bu hujjat **v0.9.39** — foydalanuvchi tomonidan tasdiqlangan ("zor hammasi
ishladi") ishlaydigan holatni saqlaydi. Biror narsa buzilsa, shu holatga
qaytariladi.

## 1. Umumiy g'oya
Zoom/Google Meet ovozini real vaqtda tarjima qiladigan **ikki yo'nalishli**
(duplex) desktop ilova. Ikkita mustaqil kanal bir vaqtda ishlaydi:

- **INCOMING (eshitish):** meeting ovozi → **AUTO → o'zbekcha** → karnay/naushnik.
- **OUTGOING (gapirish):** sizning mikrofoningiz → **o'zbekcha → inglizcha**
  → virtual kabel → Meet mikrofoni → suhbatdosh.

## 2. Model va murojaat (Gemini)
- **Model:** `gemini-3.5-live-translate-preview` (Gemini 3.5 Live Translate).
- **Ovoz:** `Charon` (prebuilt voice).
- **Ulanish:** `client.aio.live.connect(model, config)` — WebSocket, uzluksiz
  oqim (`translator.py`). Har kanal ALOHIDA Live sessiya ochadi.
- **Audio yuborish:** mikrofon 16 kHz mono PCM → `session.send_realtime_input(
  audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))`. Sun'iy
  "turn" chegaralari YO'Q — uzluksiz oqim (so'zlar bo'linmasin).
- **Config** (`build_live_config`):
  - `response_modalities=["AUDIO"]`
  - `system_instruction` — tarjima uslubi/qoidalari
  - `speech_config` — Charon ovozi
  - `input_audio_transcription` — manba til ishorasi (AUTO bo'lsa ishorasiz)
  - `output_audio_transcription`
  - `translation_config(target_language_code, echo_target_language=False)`
  - ⚠️ **`realtime_input_config` (server-VAD) YO'Q** — u qo'shilганда model
    foydalanuvchi nutqiga ham javob bermay tarjimani BUZDI (v0.9.34→0.9.36
    revert). TEGMA.

## 3. Audio (sounddevice)
- Capture: 16 kHz mono → Gemini.
- Playback: 24 kHz → chiqish qurilmasi (`balanced-smooth` profil, buferlab
  silliq ijro).
- **Client VAD (`SilenceGate`, translator.py):** har bo'lak RMS < `--silence-
  threshold` (default **300**) bo'lsa modelga NOL (raqamli sukunat) yuboradi
  → jimlikda "gap to'qish" (hallutsinatsiya) kamayadi, tarjimani buzmaydi.

## 4. Feedback (echo/halqa) boshqaruvi — MUHIM
Karnayda incoming tarjima yangraydi, fizik mikrofon uni eshitib qayta
tarjima qilib yuborsa — cheksiz halqa. Yechim chiqish qurilmasiga qarab
AVTOMATIK tanlanadi (`product_app.py`, `_launch_translator`):

| Chiqish | Rejim | Bayon |
|--------|-------|-------|
| **Naushnik** (aniqlansa) | `--no-gate` | Mikrofon doim ochiq, ERKIN ikki tomonlama. Mikrofon karnayni eshitmaydi → halqa yo'q. |
| **Karnay (Windows)** | `--push-to-talk` | Mikrofon FAQAT **O'ng Ctrl** bosilganda ochiq (`GetAsyncKeyState`, VK 0xA3). Qo'yib yuborilgach **1.5s dum** (tarjima oxirigacha chiqsin). |
| **Karnay (macOS)** | `CaptureGate` | Incoming yangraganda mikrofon avtomatik jim (navbatlashib). |

- Naushnikni `_is_headphone_output()` (nom) + `findoutput` (holat) aniqlaydi.

## 5. Windows audio routing (`packaging/windows/audio_config.ps1`)
- **IPolicyConfig** (COM) orqali tizim DEFAULT qurilmalarini o'zgartiradi —
  shunda Zoom/Meet "Default" bilan hech narsa tanlamasdan ishlaydi:
  - `setrender hifi:2` → Meet KARNAYI Hi-Fi Cable'ga (ilova undan meeting
    ovozini oladi).
  - `setcapture vbcable:1` → Meet MIKROFONI "CABLE Output"ga (ilova tarjimani
    "CABLE Input"ga chiqaradi, u yerdan Meet oladi).
- **`findoutput`** (FindPhysicalPreferred): faqat **ACTIVE** (ulangan)
  qurilmalardan naushnik/garniturani afzal ko'radi, aks holda fizik karnay.
  Incoming tarjima shu qurilmaga chiqadi. Bo'sh quloqchin uyasi ACTIVE emas
  → ulangan naushnik ishonchli topiladi.
- Stop'da: `restore` — default'lar qaytariladi (naushnik bo'lsa unga, aks
  holda fizik karnayga).
- ⚠️ Gotcha: `IMMDevice.GetId` `[MarshalAs(LPWStr)]` bo'lishi SHART (aks holda
  heap corruption 0xC0000374). PowerShell HAR DOIM `-ExecutionPolicy Bypass
  -File`.

## 6. Virtual audio drayverlari (avto-o'rnatish)
- Ilova birinchi ochilganda asosiy kabel yo'q bo'lsa AVTOMATIK yuklab
  o'rnatadi (`_begin_first_run_driver_setup` → `install_driver`):
  - **VB-CABLE** (`VBCABLE_Driver_Pack45.zip`) — asosiy.
  - **VB-Audio Hi-Fi Cable** (`HiFiCableAsioBridgeSetup_v1007.zip`) — ikkinchi
    (duplex uchun).
- O'rnatish: `ShellExecuteW(None, "runas", setup, ...)` → **bitta UAC** so'raydi.
  Drayverlar imzolangan → Windows 10/11 qabul qiladi. Internet kerak (yuklab
  oladi). Ba'zan qurilma ko'rinishi uchun REBOOT kerak.

## 7. GUI va boshqalar
- **`product_app.py`** — PySide6. Tray belgisi, litsenziya/API-kalit,
  "Mening tilim" + "Tarjima tili" sozlamasi, ixcham oyna (640×530).
- Tray: belgini bosish oynani OCHMAYDI (faqat menyu) — Windows'da
  `ApplicationActivate` filtri o'rnatilmaydi.
- Xavfsizlik: API-kalit ichida, lekin `audit_artifact.py` build vaqtida
  installerda sirlar oqmasligini tekshiradi.

## 8. Nashr zanjiri
- Kod: `ZiyoVer/trk1` (private), branch `master`. `vX.Y.Z` tag push → CI
  (`build-installers.yml`) → Windows + macOS installer.
- Sayt: `ZiyoVer/trk1-site` (public) GitHub Pages, `index.html` Release
  asset'larga bog'langan.
- GitLab (kompaniya): `edcom/trk-tarjimon-app` paket registri + Docker/nginx
  sayt (dev/staging/main branch).

## 9. Versiya tarixi (muhim tuzatishlar)
- **0.9.33** — feedback-gate o'chirildi (`--no-gate`): gapirish ishladi.
- **0.9.36** — server-VAD REVERT (tarjimani buzgan edi).
- **0.9.37** — karnayda gate qaytarildi (halqa), naushnikda erkin.
- **0.9.38** — karnayda push-to-talk (O'ng Ctrl) — gate qotishi hal.
- **0.9.39** — naushnik avto-tanlash + push-to-talk release-dumi. ✅ ISHLADI.
