# Live Translator — meeting voice translator

Zoom yoki Google Meet ovozini tanlangan input qurilmadan olib, kompaniyaning
EDCOM WebSocket gateway’i orqali real vaqtda tanlangan til va audio outputga uzatadi.
Audio va transcript diskka yozilmaydi.
Default tarjima ovozi: `Charon`.
EDCOM’dan keladigan 24 kHz mono tarjima ovozi output qurilmaning native sample
rate’iga moslanadi. MacBook Air Speakers va BlackHole 2ch odatda 48 kHz
ishlaydi. Callback asosidagi 20 ms playback va 240 ms jitter buffer tarmoq
chunklari orasidagi uzilish hamda qitirlashni oldini oladi.

## Installer orqali o‘rnatish

macOS development installer Desktop’da:

```text
LiveTranslator-Installer-macOS.pkg
```

U `/Applications/Live Translator.app` ichiga Python va barcha dependency’lar
bilan birga o‘rnatadi. Birinchi ochilishda kompaniyaning EDCOM API key’i
kiritadi; key Keychain’da saqlanadi. BlackHole topilmasa ilova rasmiy, checksum
bilan tekshirilgan installer’ni yuklab ochadi.

Windows uchun PyInstaller + Inno Setup build scripti va GitHub Actions build
workflow `build_product_windows.ps1` hamda `.github/workflows` ichida tayyor.

## 1. O‘rnatish

```bash
cd /Users/abc/meeting-translator-mvp
./setup.sh
```

API key `.env` ichida saqlanadi va `.gitignore` orqali gitdan chiqarilgan.

## 2. Qurilmalarni ko‘rish

```bash
./run.sh --list-devices
./run.sh --check
```

## 3. Zoom / Meet sozlamasi

Zoom yoki Google Meet:

- Speaker: `BlackHole 2ch`
- Microphone: odatiy fizik mikrofon

Translator default holatda:

- Input: `BlackHole 2ch`
- Output: BlackHole bo‘lmagan default speaker/headphone

## 4. Ishga tushirish

```bash
./run.sh
```

Yoki Desktop’dagi `English to Uzbek Translator.app` ilovasini ikki marta
bosing. Kichik shaffof boshqaruv oynasi ochiladi:

- `BOSHLASH` — EDCOM translatorni ulaydi;
- `TO‘XTATISH` — tarjimani xavfsiz to‘xtatadi;
- `TARJIMA REJIMI` — `Meetingni eshitish`, `Zoom’ga gapirish` yoki `Ikkalasi`; rejim
  o‘zgarsa input/output preset ham avtomatik almashadi;
- `MANBA TILI` va `TARJIMA TILI` — English, O‘zbekcha va Русский orasida
  mustaqil tanlanadi; har bir rejim o‘z til juftligini eslab qoladi;
- `Avtomatik` manba — bitta meetingda inglizcha va ruscha navbatma-navbat
  gapirilsa, EDCOM audiodagi tilni aniqlab tanlangan target tilga o‘giradi;
- `INPUT` — tarjima qilinadigan mikrofon yoki virtual meeting audiosi;
- `OUTPUT` — speaker/headphone yoki Zoom’ga beriladigan virtual mikrofon;
- original nutq va o‘zbekcha tarjima jonli subtitrda ko‘rinadi;
- oynani ekranning istalgan joyiga sudrab qo‘yish mumkin.

Muayyan output qurilma kerak bo‘lsa:

```bash
./run.sh --output-device "MacBook Pro Speakers"
./run.sh --output-device 3
```

Boshqa EDCOM ovozini sinash uchun:

```bash
./run.sh --voice Kore
```

`Ctrl+C` bilan to‘xtatiladi. Qisqa test:

```bash
./run.sh --max-seconds 30
```

BlackHole orqali avtomatik English audio bilan end-to-end test qilish uchun,
translator ishlayotgan paytda boshqa Terminal oynasida:

```bash
cd /Users/abc/meeting-translator-mvp
arch -arm64 .venv/bin/python test_feeder.py
```

Terminalda `EN ›` va `UZ ›` transcriptlari ko‘rinadi. Ularni yashirish:

```bash
./run.sh --no-transcript
```

## Ikki asosiy audio rejim

Meetingni o‘zbekcha eshitish:

- Mode: `Meetingni eshitish`
- Source: `Avtomatik`, `English` yoki `Русский`
- Target: `O‘zbekcha`
- App Input: `BlackHole 2ch`
- App Output: `MacBook Air Speakers` yoki headphone
- Zoom/Meet Speaker: `BlackHole 2ch`
- Zoom/Meet Microphone: odatiy mikrofon

O‘zbekcha gapirib, Zoom’ga inglizcha yuborish:

- Mode: `Zoom’ga gapirish`
- Source: `O‘zbekcha`
- Target: `English` (yoki `Русский`)
- App Input: `MacBook Air Microphone` yoki headset mikrofon
- App Output: `BlackHole 2ch`
- Zoom/Meet Microphone: `BlackHole 2ch`
- Zoom/Meet Speaker: `MacBook Air Speakers` yoki headphone

Bir vaqtning o‘zida incoming va outgoing ikki tomonlama tarjima:

- Mode: `Ikkalasi`
- Eshitish: `BlackHole 2ch → EDCOM → fizik speaker`
- Gapirish: `fizik mikrofon → EDCOM → BlackHole 16ch`
- Start bosilganda app ikki mustaqil Live sessionni parallel ulaydi;
- macOS system output’ni `BlackHole 2ch`ga, system microphone’ni
  `BlackHole 16ch`ga o‘tkazadi va Stop bosilganda ikkalasini qaytaradi;
- `BlackHole 16ch` topilmasa, uchinchi rejimning Start tugmasi bloklanadi va
  rasmiy, checksum bilan tekshirilgan installer tugmasi chiqadi.

Bitta virtual cable’ni ikki tomonga ishlatish feedback loop yaratgani uchun
app bunday routingni qabul qilmaydi.

## Admin panel va litsenziya

Development control server:

```bash
./run_control_server.sh
```

Terminal `http://127.0.0.1:8787/admin` manzilini va birinchi admin tokenni
ko‘rsatadi. Paneldan user yaratiladi, device limiti belgilanadi, bir martalik
license key olinadi, online device va jonli sessiyaning rejimi, source/target
tili hamda audio yo‘li ko‘riladi; user yoqiladi yoki o‘chiriladi.

Client Settings ichida control server URL va license key kiritiladi. Remote
kompyuterlar uchun control server HTTPS domen/VPS’da turishi kerak. Production
installerga URL bake qilish:

```bash
LIVE_TRANSLATOR_CONTROL_URL=https://control.example.com ./build_product_macos.sh
```

Client har 60 soniyada heartbeat yuboradi. Admin userni o‘chirsa, rasmiy client
keyingi heartbeatda tarjimani to‘xtatadi.

## Muhim

- Bir virtual qurilmani ayni paytda ham input, ham output tanlash bloklanadi;
  incoming rejimda fizik output ishlatish feedback loopni oldini oladi.
- 1.2 soniya jimlik aniqlansa audio oqimi yakunlanadi; eski gap qayta tarjima qilinmaydi.
- Playback navbati ikki soniya bilan cheklangan, shu sabab tarjima ortda yig‘ilib qolmaydi.
- Meeting audio kompaniyaning EDCOM gateway’iga yuboriladi; ishtirokchilarga xabar berish kerak.
- EDCOM protokoli yoki limitlari o‘zgarsa client konfiguratsiyasi yangilanishi mumkin.
- Lokal installer’ni reverse engineeringdan 100% yopib bo‘lmaydi. Release secret
  scan, short-lived license va signing hooklari bor; productionda EDCOM kaliti
  boshqariladigan server/proxy ortida saqlanishi kerak. Batafsil: `SECURITY.md`.
