# DEVLOG — Live Translator (TRK meeting-translator)

Bu hujjat 2026-07-22 → 2026-07-25 oralig'idagi katta debugging/rivojlantirish
sessiyasining TO'LIQ tarixi: har muammo, uning ILDIZ sababi, yechimi va
versiyasi. Yangi suhbat/odam shu faylni o'qib butun kontekstni tiklaydi.
(Arxitektura tafsiloti: `ARCHITECTURE.md`.)

---

## Loyiha bir qarashda

- **Nima:** Zoom/Google Meet ovozini ikki yo'nalishda jonli tarjima qiladigan
  desktop ilova (Windows birinchi, macOS ham bor).
- **Dvigatel:** Gemini `gemini-3.5-live-translate-preview`, ovoz **Charon**,
  har kanal alohida Live WebSocket sessiya.
- **Kanallar:** INCOMING (meeting → AUTO→UZ → karnay/naushnik) va OUTGOING
  (mikrofon → UZ→EN → virtual kabel → Meet mikrofoni).
- **Nashr:** kod `ZiyoVer/trk1` (private, master) → `v*` tag → GitHub CI →
  installerlar → `ZiyoVer/trk1-site` Release + sayt
  https://ziyover.github.io/trk1-site/ . GitLab (kompaniya):
  `git.edcom.uz/edcom/trk-tarjimon-app` — paket registri (0.9.39 gacha
  yuklangan) + Docker/nginx sayt (dev branch, index.html hali 0.9.31).

## Versiyalar xaritasi (muhimlari)

| Versiya | Nima | Holat |
|---|---|---|
| 0.9.16 | Birinchi muvaffaqiyatli build (0.9.12–15 CI jim yiqilardi) | tarixiy |
| 0.9.18 | IPolicyConfig heap-corruption tuzatildi (GetId LPWStr) — routing ishladi | tarixiy |
| 0.9.24 | `import subprocess` yo'q edi — routing ilovadan HECH ishlamagan; tuzatildi | tarixiy |
| 0.9.28–0.9.31 | Ikki tomonlama kabel tanlash, UI redesign (faqat ikki tomonlama, 2 til, compact 640×530), tray tozalash | tarixiy |
| 0.9.33 | Feedback-gate o'chirildi → gapirish birinchi marta ishladi ("zor ishladi") | tarixiy |
| 0.9.34→0.9.36 | Server-VAD qo'shildi → TARJIMA O'LDI → to'liq revert. **Gemini Live'da `realtime_input_config`ga TEGMA** | saboq |
| 0.9.37 | Karnayda halqa ("o'z ovozim qaytyapti") → gate qaytarildi; naushnikda erkin | tarixiy |
| 0.9.38 | Gate karnayda mikrofonni abadiy yopdi → **Push-to-talk** (Ctrl bosib gapirish) | tarixiy |
| 0.9.39 | PTT release-dumi 1.5s (tarjima oxirigacha chiqadi) + `findoutput` | tarixiy |
| **0.9.40** | **BARQAROR.** Kirill nom kodlash tuzatildi → naushnik ("Наушники (P2961)") to'g'ri tanlanadi: tarjima naushnikka, erkin gapirish; karnayda PTT. Saqlash nuqtasi: branch `stable-0.9.40`, tag `v0.9.40`. Saytda "Naushnikli eski versiya" havolasi | ✅ tasdiqlangan |
| 0.9.41 | **Microsoft AEC** (Voice Capture DSP, `winaec.py`): karnayda Ctrl'siz erkin gapirish; ishlamasa avto-PTT fallback | ⏳ foydalanuvchi testi kutilmoqda |
| 0.9.42 | Webcam/kirill mikrofon avto-tanlash (boshqa kompyuterlar) + Ctrl endi chap ham o'ng ham | ⏳ boshliq kompida test kutilmoqda |
| 0.9.43 | **Echo himoyasi teshigi tuzatildi** (statik audit): qurilma turi Windows FormFactor bo'yicha; AEC log diagnostikasi; BT garnitura ogohlantirishi; tushunarli xato xabarlari | ⏳ test kutilmoqda |

## Muammo → ildiz sabab → yechim (xronologik)

1. **Routing umuman ishlamasdi (tinglash ovozsiz, stop qaytarmaydi).**
   Ildiz 1: `IMMDevice.GetId(out string)` default BSTR marshaling → heap
   corruption 0xC0000374 → har set/restore jim crash. Yechim:
   `[MarshalAs(UnmanagedType.LPWStr)]`. Ildiz 2: `product_app.py`da
   `import subprocess` YO'Q edi, `except: return ""` yashirgan. Saboq:
   jim `except` bloklariga ishonma; ILOVA kontekstida `[ROUTING]` log.

2. **Ikki tomonlamada gapirish umuman tarjima qilmadi.**
   Log: `feedback himoyasi, 1→2498 chunk` uzluksiz. Ildiz: incoming tarjima
   karnayda deyarli uzluksiz yangraydi → CaptureGate mikrofonni abadiy yopadi.
   Yechim evolyutsiyasi: gate o'chirish (0.9.33) → karnayda halqa chiqdi
   (0.9.37 gate qaytdi) → gate yana abadiy yopdi → **push-to-talk** (0.9.38)
   → **Microsoft AEC** (0.9.41). Fizika: halqa HAVODA (karnay→xona→mikrofon),
   uni hech qanday virtual kabel/driver yechmaydi — faqat AEC yoki naushnik.

3. **"That it that it" gallutsinatsiya (sukutda).** Client `SilenceGate`
   (RMS<300 → nol yuborish) bor. Server-VAD bilan kuchaytirish TARJIMANI
   O'LDIRDI → revert. Qolgan yechim g'oyasi: chiqish post-filtri (takror
   frazalarni tashlash) — hali qilinmagan.

4. **Naushnik ulansa ham tarjima Realtek karnayga ketardi.**
   Ildiz: IKKI kodlash xatosi — `_win_audio` `text=True` (cp1251) va
   `audio_config.ps1`da UTF-8 BOM yo'q (PowerShell C# ichidagi "наушник"
   literallarini buzardi → IsHeadphone ishlamasdi). Yechim (0.9.40):
   BOM + `[Console]::OutputEncoding=UTF8` + Python `encoding="utf-8"`;
   incoming chiqish = `win_prev_render` (foydalanuvchi AYNAN eshitayotgan
   qurilma), `findoutput` zaxira; rejim `_is_loud_speaker()` — FAQAT ichki
   karnay (speaker+realtek/hd chip) PTT, qolgani (naushnik/monitor/tashqi)
   erkin `--no-gate`.

5. **Tray belgisini bossa oyna o'zi ochilardi.** Ildiz: macOS uchun yozilgan
   `ApplicationActivate` eventFilter Windows'da ham o'rnatilardi. Yechim
   (0.9.35): Windows'da filter yo'q; tray faqat menyu ko'rsatadi.

6. **PTT'da Ctrl qo'yilganda tarjima yarim kesilardi.** Yechim (0.9.39):
   `RELEASE_HANGOVER_SECONDS=1.5` — qo'yib yuborilgach mikrofon 1.5s ochiq.

7. **Boshliq kompida (Win10, mikrofonsiz, webcam mic, BT naushnik) gapirish
   ishlamadi.** Ildiz: mikrofon qidiruvi lotincha edi — "Микрофон (USB2.0
   Camera)" topilmay BT hands-free tanlangan (HFP sifatni ham buzadi); va
   PTT faqat O'NG Ctrl edi (foydalanuvchi chapni bosgan). Yechim (0.9.42):
   kirill+webcam so'zlari, `avoid_words` (hands-free oxirgi chora),
   `PTT_DEFAULT_VK=0x11` (istalgan Ctrl).

8. **Karnayda Ctrl'siz gapirish (foydalanuvchi talabi "driver yozamiz").**
   Halol tahlil: yangi kabel-driver exoni yechmaydi; Realtek APO bu mashinada
   yo'q; **Windows'ning o'z AEC'i bor** — `mfwmaaec.dll`, CLSID
   `{745057C7-F353-4F2D-A7EE-58434477730E}`. Yechim (0.9.41): `winaec.py` —
   sof ctypes DMO source-mode worker (alohida jarayon, stdout=xom PCM 16k
   mono; MFPKEY fmtid `{6f52c567-0360-4bd2-9617-ccbf1421c939}`,
   SOURCE_MODE=TRUE, SYSTEM_MODE=0, DEVICE_INDEXES=(spk<<16)|mic,
   ECHO_LENGTH=512, AES=1). Worker o'lsa/6s audio bermasa → avto-PTT.
   `LT_SPEAKER_MODE=ptt` env → majburan eski rejim.

9. **Echo himoyasi FAQAT bitta kompyuterda ishlardi (statik audit, 0.9.43).**
   `_is_loud_speaker()` nomda ham "speaker/динамик", ham chip so'zi
   ("realtek"/"hd audio"…) bo'lishini talab qilardi. Shu sabab monitor karnayi
   ("Динамики (P2961)"), USB karnay, HDMI/TV, Realtek bo'lmagan noutbuklar
   "naushnik" deb hisoblanib `--no-gate` bilan HIMOYASIZ ishlardi → cheksiz
   echo halqasi qaytardi. Faqat TRK mashinasining "Speaker (Realtek(R) Audio)"
   nomi to'g'ri edi. Yechim: Windows endpoint **FormFactor**
   (`PKEY_AudioEndpoint_FormFactor`, winaec.py `output_is_ear_safe`) —
   Headphones(3)/Headset(5) → erkin; Speakers(1)/HDMI(9)/noaniq → himoya.
   Nom bo'yicha naushnik topilsa ham erkin (tasdiqlangan "Наушники (P2961)"
   holati o'zgarmaydi). **Qoida: noaniqlikda XAVFSIZ tomonni tanla.**

## Muhim texnik saboqlar (takrorlamaslik uchun)

- **Gemini Live:** `realtime_input_config`/server-VAD qo'shma — tarjima
  o'ladi. Ishlaydigan konfig: faqat system_instruction + speech_config +
  translation_config + transkripsiyalar. 1008 GoAway = sessiya muddati,
  normal; engine o'zi qayta ulanadi.
- **Rus/kirill Windows:** ps1↔Python nom almashinuvida UTF-8 hamma joyda
  (BOM + OutputEncoding + subprocess encoding). PowerShell'ni faqat
  `-ExecutionPolicy Bypass -File` bilan; uzun buyruqlar `-EncodedCommand`
  (UTF-16LE base64); juda uzuni SCP bilan fayl qilib yuboriladi.
- **SSH test mashinasi** (`user@100.98.115.105`, `~/.ssh/trk_server`):
  Session 0 audio/UAC sinolmaydi (AEC ProcessOutput=0x87CC000A — bu crash
  EMAS, sessiya cheklovi). Jim o'rnatish: avval `taskkill /IM "Live
  Translator.exe" /F`, keyin `/VERYSILENT` (aks holda exit 5).
  Boshliq kompiga SSH YO'Q — diagnostika: tray → "Loglarni ochish" →
  foydalanuvchi engine.log oxirini yuboradi.
- **PyInstaller/CI:** `--add-data` absolyut yo'l; `$LASTEXITCODE` qo'lda;
  APP_VERSION + MyAppVersion + installer nomi UCHALASI birga bump.
- **Qurilmalar doim NOM bilan uzatiladi** (index emas) — GUI va engine
  indekslari farq qiladi.

## Hozirgi holat (2026-07-25)

- TRK test mashinasida: **v0.9.40** o'rnatilgan (foydalanuvchi so'rovi bilan
  qaytarilgan; naushnik oqimi tasdiqlangan: "naushnikda eshitilyapti yaxshi").
- Sinov kutilmoqda: (a) v0.9.40'da naushnik bilan gapirish (Ctrl'siz, oynada
  UZ>/EN> chiqishi); (b) **v0.9.41 AEC** — karnayda Ctrl'siz; (c) v0.9.42 —
  boshliq kompida webcam-mikrofon bilan.
- GitLab: paketlar 0.9.39 gacha; repo `dev` push HTTP 500 bergan (katta
  fayl) — sayt yangilash chala, qo'lda yuklash papkasi: `~/Desktop/GitLab-trk-tarjimon/`.

## Qaytish yo'llari (biror narsa buzilsa)

```bash
# Barqaror holatga qaytish (kod):
git checkout stable-0.9.40        # yoki: git checkout v0.9.40
# Foydalanuvchiga barqaror installer:
https://github.com/ZiyoVer/trk1-site/releases/download/v0.9.40/LiveTranslator-Setup-0.9.40.exe
# AEC'ni o'chirib eski PTT rejimi (kod qaytarmasdan):
LT_SPEAKER_MODE=ptt muhit o'zgaruvchisi bilan ilovani ishga tushirish
```

## Keyingi mumkin qadamlar

1. v0.9.41 AEC jonli testi (karnay, Ctrl'siz) — natijaga qarab default qilish
   yoki fallback'ni qoldirish.
2. "That it" post-filtri (takror frazalarni chiqishda tashlash).
3. Gemini session_resumption (uzoq meetingda 1008 uzilishlarini silliqlash).
4. GitLab saytini 0.9.4x ga yangilash (500 muammosi: LFS yoki kichik push).
5. Ishlayotganda naushnik ulanganda avto-almashish (hozir Stop→Start kerak).

## Yangi kompyuterga o'rnatishda MAJBURIY qadam (2026-07-27)

Ilova tarjimani virtual kabelga yozadi va tizim mikrofonini o'sha kabelga
qo'yadi. Lekin **Meet/Zoom ko'pincha o'zi tanlagan mikrofonda qolib ketadi**
(masalan webcam) — u holda suhbatdosh NA tarjimani, na xom ovozni eshitadi.

**Yechim:** Meet/Zoom sozlamalarida mikrofon =
`CABLE Output (VB-Audio Virtual Cable)` (yoki «Same as System»).

Ilova v0.9.52 dan boshlab kerakli nomni ekranda o'zi ko'rsatadi
(`meet_mic_hint`, manba: `audio_config.ps1 setcapture` qaytargan aniq nom).
Jonli tasdiq (Windows 11, boshliq kompyuteri): mikrofon CABLE Output ga
o'zgartirilgach eshitish ham, gapirish ham ishladi.

Tashxis tartibi (suhbatdosh eshitmasa):
1. Ilova ekranidagi «Meet/Zoom mikrofoni: ...» yozuvini o'qing va Meet'da
   AYNAN o'shani tanlang.
2. Windows → Звук → Запись → CABLE Output daraja ko'rsatkichi gapirganda
   harakatlanadimi (harakatlansa — kabel ishlayapti, muammo Meet tomonda).
3. Drayver yangi o'rnatilgan bo'lsa — kompyuterni qayta yoqing.

## v0.9.69–0.9.71 — ovoz qaytmasligi va yangilanish (2026-07-28)

### 1. «To'xtatsam kompyuterning ovozi umuman chiqmay qoladi»

Ildiz sabab (0.9.65 da kiritilgan xato): `_restore_routing_worker` fon
oqimda `_routing_lock` ni oladi, keyin xato bilan `_win_restore_routing_`
`async()` — ya'ni O'ZINI chaqirardi. Yangi oqim o'sha qulfni kutib abadiy
qotardi va qurilmalar UMUMAN tiklanmasdi. Logdagi belgisi: `startduplex`
bor, `stopduplex` YO'Q.

Tuzatish: haqiqiy `_win_restore_routing()` chaqiriladi + natija
TEKSHIRILADI (default chiqish hali ham virtual kabel bo'lsa 3 martagacha
qayta urinadi). `win_prev_*` faqat muvaffaqiyatdan keyin tozalanadi.

### 2. «Update qayta-qayta qilsam ham bo'lmayapti»

Ildiz sabab (ikkita, ikkalasi ham tuzatildi):

1. O'rnatuvchi ilovaning BOLA jarayoni bo'lib ochilardi, o'rnatuvchi ichidagi
   `taskkill /IM "Live Translator.exe" /T /F` esa `/T` tufayli butun
   daraxtni — o'rnatuvchining O'ZINI ham — o'ldirardi. `/T` olib tashlandi
   (kerak ham emas: dvigatel/AEC bolalari ham shu nomdagi exe, `/IM` ularni
   nomi bo'yicha yopadi). Bu tuzatish YANGI O'RNATUVCHI ichida ketadi —
   demak eski 0.9.68 dagi ilova ham endi o'zini yangilay oladi.
2. Sehrgar sahifalari o'chirildi (`DisableWelcomePage` … `DisableFinished`
   `Page`) — o'rnatuvchi jim bo'lmagan rejimda ochilsa ham hech qanday tugma
   bosilmaydi. Ish stoli yorlig'i endi doim yaratiladi.

Yangi ilova esa o'rnatuvchini butunlay AJRATIB (DETACHED .cmd, 3 s kutish)
ishga tushiradi va o'zi toza yopiladi — fayllar qulfdan bo'shaydi.

### 3. Monitor uchun alohida oqim — «aralashtirma»

Bitta koddan ikkita o'rnatuvchi:

| Fayl | Kim uchun | Oqim |
| --- | --- | --- |
| `LiveTranslator-Setup-x.y.z.exe` | boshliq, hamkasblar | barqaror |
| `LiveTranslator-OPS-Setup-x.y.z.exe` | monitorli sinov mashinasi | `ops` |

Farqi faqat `{app}\channel.txt` (OPS o'rnatuvchi yozadi, barqaror
`[InstallDelete]` bilan o'chiradi). Ilova `/update?channel=ops` so'raydi,
server `LT_OPS_VERSION`/`LT_OPS_URL` dan javob beradi; ular bo'sh bo'lsa
barqarorga qaytadi. Tray menyusi va logda `0.9.71 · Windows OPS` ko'rinadi.

Yangi sinov versiyasini FAQAT monitorga chiqarish:
```
railway variables --set LT_OPS_VERSION=x.y.z --set LT_OPS_URL=<OPS exe>
```
Barqarorga ko'chirish (ishonch hosil qilgandan keyin):
```
railway variables --set LT_LATEST_VERSION=x.y.z --set LT_LATEST_URL=<oddiy exe>
railway redeploy --yes
```
**Qayta yuklash SHART.** `railway variables --set` qiymatni yozadi, lekin
ishlab turgan konteyner uni ko'rmaydi — `/update` eski versiyani qaytaraverdi
(2026-07-28 da aynan shunday bo'ldi). Buyruqlar `support_server/` katalogidan
ishlaydi (loyiha o'sha yerga bog'langan).

Ikki oqim bir-biriga TA'SIR QILMAYDI: OPS mashinasi `LT_OPS_*` ni, qolganlar
`LT_LATEST_*` ni o'qiydi. Boshliqlarga yangi versiya chiqarish OPS ni
buzmaydi. Ikki ehtiyot shart: (1) `LT_OPS_*` ni bo'sh qoldirma — bo'sh bo'lsa
OPS barqaror oqimga tushib qoladi; (2) OPS mashinasiga oddiy o'rnatuvchini
qo'lda o'rnatma — u `channel.txt` ni o'chiradi.

## v0.9.72 — «Meeting o'zbekcha» (2026-07-28, Zoom ko'rsatuvidan keyin)

### Nima bo'lgan (log dalillari, `conference` OPS 0.9.71, 14:15–14:22)

Dvigatel benuqson ishlagan: bironta xato, uzilish, qayta ulanish yo'q,
`[AEC] ISHLADI`. Muammo boshqa yerda edi:

1. **Chiquvchi tarjima tayyor bo'lgan** (`UZ › Hammaga assalomu alaykum →
   RU › Всем здравствуйте`, chiqish `CABLE Input`). Hamkasblar ruschani
   eshitmagan bo'lsa — Zoom mikrofonni `CABLE Output` dan olmagan. Zoom
   qurilmani meetingga kirganda "yopishtiradi" va keyin Windows default
   o'zgarsa ergashmaydi; Meet (brauzer) ergashadi — **Meet bilan Zoom farqi
   aynan shu**.
2. **Kiruvchi yo'nalishda 28 ta ingliz satri, o'zbek manba BITTA HAM YO'Q.**
   Kimdir o'zbekcha gapirganda model jim qolgan (AUTO → UZ: tarjima
   qiladigan narsa yo'q). Ayni paytda meeting ovozi kabelga burilgani uchun
   asl ovoz ham eshitilmagan → **xona jimjit**.
3. **Mikrofon narigi tomonni eshitib, dastur uni meetingga qaytargan**:
   `14:20:49 [OUTGOING] UZ › Alloh, how are you? → RU › Боже, как ты?`,
   o'sha soniyada `14:20:50 [INCOMING] EN › Hello, hello. How are you?`.
   3 marta. Chiquvchi satr kiruvchidan OLDIN kelgan → ovoz xonada, havoda
   yangragan (boshqa qurilma meetingda ochiq bo'lgan). AEC faqat o'z
   orientir qurilmasini bekor qiladi.

Hamkasblar mashinalari (`x-safarov`, `j-zarmasov`) — **alohida masala**:
ularning hech bir logida `engine.log` yo'q, ya'ni tarjima u yerda hech qachon
ishga tushmagan (`j-zarmasov` da API kalit umuman kiritilmagan).

### Yechim: «Meeting o'zbekcha» belgisi

Oynada belgi + tray'da shu band. Yoqilganda `_current_mode()` **"outgoing"**
qaytaradi — ya'ni ilova allaqachon mavjud va sinalgan "gapirish" yo'lidan
ketadi:

- kiruvchi kanal umuman ochilmaydi;
- tizim CHIQISHIGA tegilmaydi (`_win_apply_routing("", kabel)` — birinchi
  argument bo'sh, `audio_config.ps1` `startduplex` uni `if ($p[0])` bilan
  o'tkazib yuboradi), demak meeting ovozi karnaydan **jonli** eshitiladi;
- faqat mikrofon chiquvchi kabelga o'tkaziladi (Zoom mikrofoni).

Ikki tomonlama yo'lga **bir belgi ham tegilmadi** (AST bilan tekshirildi:
yangi shox `if args.duplex and len(translators) == 2` ga `elif` bo'lib
ulanadi).

### AEC ham kerak bo'ldi (aks holda 3-band takrorlanardi)

Bu rejimda meeting ovozi karnaydan chiqadi va fizik mikrofon uni eshitadi.
`CaptureGate` bu yerda foydasiz (kuzatadigan ijro yo'q), `--winaec` ulanishi
esa avval FAQAT duplex shoxida bor edi. Endi bitta kanalli holat uchun ham
ulanadi; orientir qurilma GUI'dan **mavjud** `--winaec-speaker` argumenti
bilan keladi (`_aec_reference_output()`: ESHITAMAN tanlovi → naushnik →
tizim default'i). Naushnikda AEC qo'shilmaydi — u yerda aks-sado fizik
jihatdan yo'q.

### Sinov qoidalari (kodsiz, baribir kerak)

1. Zoom → Settings → Audio → Microphone = `CABLE Output (VB-Audio Virtual
   Cable)`. Bir marta, Zoom eslab qoladi.
2. Xonada FAQAT bitta kompyuter meetingda tursin.

### v0.9.72 — Zoom qismi (qo'shimcha)

**1. Zoom ogohlantirishi.** Start bosilganda Zoom ish stoli ilovasi ochiq
bo'lsa, bir martalik bildirishnoma chiqadi: «Zoom → Settings → Audio →
Microphone = «CABLE Output …»». Doimiy ko'k banner QAYTARILMADI
(foydalanuvchi uni ataylab olib tashlatgan) — faqat Zoom ochiq bo'lgandagi
bildirishnoma, u oyna kichraytirilgan bo'lsa ham ko'rinadi.
`tasklist` FON oqimida chaqiriladi (`_apply_routing_worker` ichida) —
GUI oqimida ~300 ms qotishi mumkin edi.

**2. Qisqa javoblar yo'qolardi (480 ms).** `AudioPlayer` ijroni bufer
to'lgach boshlaydi; gap tugaganda chegara `minimum_flush_start_ms` = 480 ms
ga tushadi, lekin `force_start` HECH QAYERDA ishlatilmasdi. 44100 Hz stereo
chiqishda 480 ms = 84 480 bayt — «Ha», «Rahmat», raqam kabi javob shuncha
chiqarmaydi, ya'ni ijro umuman boshlanmasdi va bo'lak buferda yotib keyingi
gapga yopishib chiqardi. Endi `turn_complete` da `flush(force_start=True)`.
Ta'sir doirasi tekshirildi: 600 ms va undan uzun gaplarda xatti-harakat
o'zgarmaydi (ular allaqachon ijro etilardi) — faqat 480 ms dan qisqa
javoblar tuzaladi.

**Ataylab TEGILMADI — `SilenceGate`.** Simulyatsiya bilan tasdiqlandi:
doimiy darajali audio RMS 600 va 1200 da 0.96 s dan keyin butunlay
jimlashtiriladi (RMS 2500+ da yo'q, chunki `FLOOR_MAX`=800 →
chegara 2000). Pauzali haqiqiy nutqda 300 blokdan 0 tasi jimlashtirildi,
ya'ni oddiy suhbatga tegmaydi — xavf faqat uzluksiz, siqilgan oqimda
(video/musiqa). Tuzatish "that it that it" gallyutsinatsiya himoyasini
qayta ochib yuborishi mumkin, shuning uchun alohida, dalil bilan
qilinadi.

## ISHLAYDIGAN SOZLAMA — TASDIQLANGAN (2026-07-28 kechqurun)

Foydalanuvchi: «mikrofonlarni same qilganimdan keyin hech qanday muammosiz
ishlayapti». Bu **Zoom sozlamasi**, ilova versiyasi emas — 0.9.71 logi ilova
rus tarjimasini kabelga to'g'ri yozganini allaqachon ko'rsatgan edi, Zoom
uni olmagan.

**Zoom → Звук (Audio) — IKKALASI HAM «Как в системе» (Same as System):**

| Zoom sozlamasi | Qiymat |
| --- | --- |
| Динамик (Speaker) | **Как в системе (…)** |
| Микрофон (Microphone) | **Как в системе (…)** ← avval «Микрофон (Logitech BRIO)» edi |

Nega ishlaydi: ilova Start bosilganda Windows default qurilmalarini
kabellarga o'tkazadi, Stop bosilganda fizik qurilmalarga qaytaradi. Zoom
«Как в системе» da bo'lsa har ikkalasiga ERGASHADI — meeting o'rtasida ham.
Buning isboti 0.9.71 logining o'zida bor edi: Zoom KARNAYI allaqachon «Как в
системе» bo'lgani uchun kiruvchi tarjima ishlagan (28 ta ingliz satri),
mikrofon esa BRIO'ga qattiq bog'langani uchun chiquvchi tarjima Zoomga
bormagan.

Qo'shimcha foyda: Stop bosilganda mikrofon o'zi BRIO'ga qaytadi, ya'ni
dastur ishlamayotgan paytda Zoom oddiy holatda ishlayveradi — meeting
xonasidagi bilmagan odam ham xato qilmaydi.

**BU SOZLAMAGA TEGILMASIN.** Yangi kompyuterda birinchi qadam — Zoomda
ikkala qurilmani ham «Как в системе» qilish.

Ixtiyoriy (tarjima ovozi g'alati eshitilsa): Zoomdagi «Автоматически
регулировать громкость микрофона» ni o'chirish yoki «Звук оригинала для
музыкантов» ni yoqish — kabelda shovqin ham, aks-sado ham yo'q, Zoom
filtrlari u yerda faqat zarar qiladi.

## v0.9.73 — Navbat ko'rsatkichi + jonli rejim almashtirish (Bosqich 1)

Foydalanuvchi: «navbatni qo'yishimiz kerak … latency shundoq ham yomon,
orada ikkalamiz ham gapdan to'xtayapmiz» va «rejimni almashtirsam ham
7-10 sekund ketadimi».

### 1. Navbat ko'rsatkichi

Ildiz sabab: gapirib bo'lgach tarjima YETIB BORGANINI bilish imkoni yo'q
edi. Odam kutadi, «javob bermadi» deb yana gapiradi va aynan o'sha payt
javob keladi — to'qnashuv. Dvigatel holatni **biladi**
(`AudioPlayer.has_audio()`), lekin oynaga aytmasdi.

Endi `Translator._watch_state()` har 200 ms tekshiradi va FAQAT o'zgarganda
bitta qator yozadi: `[STATE] delivering|idle`. Oyna uni
`_handle_line` da ushlab, katta ko'rsatkichga aylantiradi:

| Holat | Ko'rsatkich |
| --- | --- |
| Chiquvchi ijro ketyapti | 📤 Tarjimangiz yetkazilmoqda… |
| Chiquvchi tugadi | ✅ Yetkazildi — javobni kuting |
| Kiruvchi ijro ketyapti | 🎧 Suhbatdosh gapirmoqda — kuting |
| Ikkalasi jim | 🎤 Gapirishingiz mumkin |

Tray tooltip'ida ham shu matn — oyna kichraytirilgan holatda ham ko'rinadi.

### 2. Rejimni jonli almashtirish (7-10 s → ~1 s)

Ilgari «Meeting o'zbekcha» ni almashtirish uchun Stop→Start kerak edi
(ulanishning o'zi ~6 s). Endi **mavjud** `devices.json` kanali orqali
(`product_app.py` yozadi, `translator.py` o'qiydi) `incoming_paused`
buyrug'i yuboriladi:

- dvigatel kiruvchi kanal sessiyasini yopadi (`pause_event` `_session`
  dagi `asyncio.wait` to'plamiga qo'shildi — audio darhol to'xtaydi, ya'ni
  pul ham ketmaydi);
- oyna tizim CHIQISHINI fizik qurilmaga qaytaradi (fon oqimida —
  PowerShell GUI'ni qotirmasin);
- **chiquvchi kanalga tegilmaydi** — sizning gapingiz tarjimasi uzilmaydi.

Buyruq fayli endi BIRLASHTIRIB yoziladi (`_write_engine_command`) — aks
holda qurilma almashtirish buyrug'i pauzani o'chirib yuborardi.

Yagona cheklov: dastur «Meeting o'zbekcha» YOQIQ holda ishga tushirilgan
bo'lsa kiruvchi kanal umuman ochilmagan bo'ladi — uni yo'ldan qo'shib
bo'lmaydi, Stop→Start kerak (ekranda aytiladi).

### Tekshirildi

- Dvigatel: haqiqiy fayl bilan — `[STATE] idle → delivering → idle`,
  `paused`/`resumed` o'qildi, `output` kaliti buzilmadi.
- Oyna: to'rt bosqichli suhbat aylanasi to'g'ri ko'rsatkich berdi;
  `devices.json` ikkala kalitni ham saqladi.
- 70 test o'tdi.

## v0.9.74 — «Sifatli tarjima» rejimi + interfeys tozalandi

### 1. Sifatli tarjima (Bosqich 3)

Ildiz sabab (kod xatosi EMAS): `gemini-3.5-live-translate-preview` —
SINXRON tarjimon, gap tugashini kutmay har ~1 soniyalik bo'lakni alohida
tarjima qiladi. Logdagi dalil: `UZ › Men o'zim ishlayotgan → RU › Я
работаю`, `UZ › bo'lsa, zoomda → RU › в Zoom,`. Bo'laklar orasidagi
grammatik bog'lanish yo'qoladi. O'zbekcha uchun ayniqsa yomon: **fe'l gap
oxirida** keladi, ya'ni gap tugamaguncha tarjima qilinadigan ma'no yo'q.

Yangi rejim (belgi ortida, o'chiq holda keladi):

1. Live sessiya SAQLANADI, lekin undan faqat `input_transcription`
   olinadi; modelning bo'lak-bo'lak AUDIOSI ijro etilmaydi.
2. `SentenceBuffer` gapni yig'adi va tugaganini aniqlaydi: tinish belgisi,
   **0.8 s pauza** (asosiy qoida — o'zbek transkriptida tinish belgisi
   ko'pincha yo'q) yoki xavfsizlik chegarasi (12 s / 220 belgi).
3. To'liq gap matn modeliga boradi: «tabiiy, jonli tarjima; so'zma-so'z
   emas» + **oxirgi 3 gap konteksti** (olmosh/mavzu bog'lanishi uchun —
   sinxron modelda bu umuman yo'q).
4. Tarjima TTS bilan ovozga aylanadi (Charon ovozi saqlanadi) va mavjud
   `AudioPlayer` ga beriladi — ijro/qurilma/aks-sado mantig'i o'zgarmaydi.

**Model nomlari qattiq yozilmagan** — `_pick_models()` API'dan ro'yxat
olib mos keladiganini tanlaydi (`--quality-model` / `--tts-model` bilan
majburlash mumkin). Model eskirganda rejim jimgina o'lib qolmasin uchun.

**XATOGA CHIDAMLI:** biror bosqich yiqilsa (model topilmadi, TTS bo'sh
qaytdi) rejim o'chadi, logga sabab yoziladi va dvigatel odatdagi sinxron
tarjimaga QAYTADI — foydalanuvchi jimlikda qolmaydi. Sinovda tasdiqlandi.

### 2. Interfeys tozalandi (foydalanuvchi talabi)

- **Ogohlantirish yozuvlari ekrandan olib tashlandi** (`route_hint`,
  `meet_mic_hint` endi layoutga qo'shilmaydi). Ob'ektlar saqlandi —
  kodning ~40 joyida matn yoziladi, ularni o'chirish keraksiz xavf edi.
- «Tarjimangiz yetkazilmoqda…» yozuvi olindi; holat ichkarida saqlanadi,
  tugagach «Yetkazildi — javobni kuting» ko'rinadi.
- Ranglar tinchlantirildi: neon yashil tugma → bosiq zumrad, tarjima matni
  → deyarli oq, yorliqlar → bosiq kulrang. Pastki panel yuqorigisi bilan
  bir xil (ko'k quti olib tashlandi). Belgilar nozik ramkali.
- Ortiqcha «Tillar» yorlig'i olindi (pastda «Mening tilim / Tarjima tili»
  bor edi).
- **Drayver qatori boshida yashirin** — skan tugagunча oynaning tepasida
  sababsiz bo'shliq turardi.
- Balandlik 596 → 552.

Interfeys o'zgarishlari QT offscreen render bilan **rasmga olib
ko'rilgan** va bosqichma-bosqich tuzatilgan (matnlar bir-birining ustiga
tushib qolgani ham shunda topildi).

## v0.9.75 — Boshqa loyihadan olingan beshta saboq

Foydalanuvchi UzLive (YouTube dublyaj, Navoiy/CosyVoice TTS) loyihasining
o'zgarishlar tarixini berdi. Ular boshqa yo'ldan borib BIZ duch kelgan
muammolarning ko'piga yechim topgan. Beshtasi ko'chirildi.

**MIKROFON/QURILMA QATLAMIGA TEGILMADI** (foydalanuvchi sharti). Isbot —
`git diff --stat`: faqat `translator.py` va test fayli o'zgardi;
`audio_config.ps1`, `winaec.py`, `audio_routing.py` va `product_app.py`
dagi 29 ta routing chaqiruvi — bittasi ham qo'zg'atilmadi.

### 1. Konveyer (eng katta yutuq)

Ilgari bitta oqim: tarjima → ovoz → ijro → keyin endi keyingi gap. Ya'ni
1-gap YANGRAYOTGANDA 2-gap ustida ish ketmasdi. Endi ikki bosqich —
`_translate_worker` va `_speak_worker` — navbat orqali ulangan. Tartib
buzilmaydi (tarjima oqimi bitta, navbat FIFO).

O'lchov (taqlidiy 0.4 s tarjima + 0.6 s ovoz, 3 gap):

| | Vaqt |
| --- | --- |
| Ketma-ket (eski) | 3.0 s |
| Konveyer (yangi) | **2.2 s** |

Gaplar orasi 1.0 s dan 0.6 s ga tushdi — ya'ni suhbat ravonroq.

### 2. Doimiy tezlatish olib tashlandi (1.08 → 1.0)

Ularda ataylab qaytarilgan: «oddiy nutq yana 1.0 da». Bizda har bir tarjima
DOIM 1.08 tezlikda cho'zilardi — shoshqaloq va sun'iy eshitilardi.

Muhimi: **kod o'zgarmadi, bitta raqam o'zgardi.** «Orqada qolsa tezlatish»
mantig'i `AudioPlayer._speed_for_backlog` da allaqachon bor:
`backlog < 850 ms → 1.0`, `> 2600 ms → 1.10`, oraliqda `normal_speed`.
Faqat `normal_speed` (ya'ni `--speech-speed`) 1.08 turgan edi.

### 3. Qo'shni takror gaplar tashlanadi

Ularda takror gap tarjima va TTS'dan OLDIN o'chiriladi. Bizda ham shu
nosozlik bo'lgan («brother brother brother»). Endi tinish belgisi va
katta-kichik harf hisobga olinmagan holda AYNAN bir xil qo'shni gap
tashlanadi. Oddiy so'z takrorlariga va ma'noli matnga tegilmaydi.

### 4. Duration guard

Ovoz uzunligi matnga nisbatan g'ayritabiiy (>2.2x yoki <0.4x) bo'lsa BIR
marta qayta yaratiladi va kutilganiga yaqinrog'i olinadi. Normal uzunlik
birinchi urinishdayoq qabul qilinadi — qo'shimcha kechikish yo'q. Bu
generativ modelning «srыv» (o'zidan gap to'qib yuborishi) holatidan
himoya: bunday narsa meetingga ketmaydi.

### 5. Uzun gap faqat tinish belgisidan bo'linadi

Bitta so'rov 28 so'z / 190 belgi bilan cheklandi (ularning real
xatolardan chiqqan raqami). Chegaradan oshsa gap nuqta/savol/undov, keyin
vergul-nuqtali vergul bo'yicha bo'linadi — o'rtasidan KESILMAYDI.
Qismlar orasiga 80 ms pauza qo'yiladi, sun'iy ulanish eshitilmasin.

### Olinmagani

- **Kesh va bo'lakni oldindan tayyorlash** — ularda video, 2 daqiqa
  oldinga yugurish mumkin. Jonli suhbatda imkonsiz.
- **Asl ovozni 3% da ostida qoldirish** — ularda mikrofon yo'q. Bizda o'sha
  past ovoz mikrofonga tushib, boshqalarning gapi qayta tarjima bo'lardi
  (2026-07-28 Zoom nosozligining aynan o'zi).

## v0.9.76 — «Sifatli» rejim: tarjima emas, QAYTA QURISH

Foydalanuvchi: «inglizcha gapni qaytadan o'zbekchaga tuzib beradigan
qilsak, chiroyliroq va tabiiy bo'ladi — aynan o'sha tugma bosilganda».

Oldingi ko'rsatma umumiy edi («tabiiy tarjima qil») va model manba gapning
TUZILISHIGA yopishib qolardi — natija to'g'ri, lekin «tarjimadek»
eshitilardi. Endi usul aniq aytilgan:

1. avval gapning MA'NOSINI tushun;
2. o'sha ma'noni o'sha tilda gapiradigan odam qanday aytsa, shunday qur;
3. so'zma-so'z ag'darish g'aliz chiqsa — gapni **noldan qayta tuz**.

Qo'shilgan qoidalar: og'zaki uslub (rasmiy-kitobiy emas), ish joyida
haqiqatan ishlatiladigan so'zlar (odatiy o'zlashmalar ham) noyob kitobiy
muqobil o'rniga; passiv va uzun ot zanjirlari oddiy aktiv gapga
aylantiriladi; «uh, you know» kabi to'ldiruvchilar tashlanadi; ismlar,
raqamlar, sanalar aynan saqlanadi.

**O'zbekcha uchun alohida blok** (target=uz bo'lganda qo'shiladi): fe'l gap
OXIRIDA keladi — ingliz/rus tartibini ko'chirmaslik; kalka va rasmiy-idora
qurilmalaridan qochish; raqam va sanalarni o'zbekcha o'qish. Asosiy muammo
aynan shu tilda edi.

`temperature` 0.3 → 0.45: 0.3 da model manba tuzilishiga yopishib qolardi;
qayta qurish uchun biroz erkinlik kerak.

Faqat «Sifatli tarjima» belgisi yoqilganda ishlaydi — odatdagi rejimga
tegilmadi. Mikrofon/qurilma qatlamiga ham tegilmadi (faqat matn ko'rsatmasi).

## v0.9.77 — REGRESSIYA TUZATILDI: ogohlantirish alohida oyna bo'lib chiqardi

Foydalanuvchi: «nega alohida oynachada bitta ekrancha chiqib qolyapti —
exo bekor qilish, tugma bosmasdan gapiring deb».

Sabab — mening 0.9.74 dagi xatoyim. Ogohlantirish yozuvlari layoutdan olib
tashlangan edi (`layout.addWidget` chaqiruvi o'chirildi), lekin kodning ~40
joyida hamon `route_hint.setVisible(True)` chaqiriladi. Qt qoidasi: **otasi
ham, layouti ham yo'q widget ko'rsatilsa — u alohida TOP-LEVEL OYNA bo'lib
chiqadi.** Shuning uchun Start bosilganda ekranda kichkina mustaqil oyna
paydo bo'lardi.

Tuzatish: yozuvlar YASHIRIN ota widgetga (`_hidden_hints`) bog'landi. Qt'da
otasi yashirin bo'lsa bola `setVisible(True)` da ham ko'rinmaydi — ya'ni
eski chaqiruvlarni birma-bir izlab o'chirish shart emas (bu 40 joyda
tegish demak edi, keraksiz xavf).

Tekshirildi: `setVisible(True)` dan keyin `isWindow()=False`,
`isVisible()=False`, yangi top-level oyna soni **0**.

SABOQ: widget'ni layoutdan olib tashlaganda uni ko'rsatadigan chaqiruvlar
qolsa, u yo'qolmaydi — mustaqil oynaga aylanadi. To'g'ri yo'l — yashirin
otaga bog'lash.

## v0.9.78 — Oyna qotishi tuzatildi + tabiiylik kuchaytirildi

### 1. QOTISH — ildiz sabab navbat ko'rsatkichida edi (mening qo'shganim)

Foydalanuvchi: «ko'p tarjimadan keyin dastur oynasi qotyapti, To'xtatish
tugmasi ishlamayapti».

Ildiz sabab — vaqt oynalari mos kelmagan:

| | Qiymat |
| --- | --- |
| `AudioPlayer.has_audio()` qaraydigan oyna | **0.15 s** |
| `_watch_state` so'rov oralig'i | **0.2 s** |

So'rov oralig'i oynadan KATTA. Shuning uchun ijro davomida holat deyarli
har so'rovda `delivering ↔ idle` deb sakrardi — sekundiga ~5 marta. Har
o'zgarishda oyna `setStyleSheet()` (Qt uslubni qaytadan tahlil qilib,
widget'ni re-polish qiladi) va `tray.setToolTip()` (Windows shell
chaqiruvi) bajarardi. Uzoq meetingda minglab chaqiruv — GUI oqimi
tiqilib, «To'xtatish» ham bosilmay qolardi.

Yechim: **navbat ko'rsatkichi butunlay olib tashlandi** (foydalanuvchi ham
shuni so'radi). Dvigateldagi `_watch_state` ham o'chirildi — endi bunday
satrlar umuman yozilmaydi. Pauza xabarlari `[REJIM]` prefiksi bilan
qoladi (ular kamdan-kam va faqat logga).

SABOQ: davriy so'rov oralig'i kuzatilayotgan hodisaning oynasidan KICHIK
bo'lishi shart, aks holda holat sun'iy ravishda sakraydi. Bu yerda esa
har sakrash GUI'da qimmat ish qo'zg'atardi.

### 2. Tabiiylik: qoida emas, MISOL

Foydalanuvchi: «gemini o'sha gapni original gapga yopishib olmasdan,
o'zbekcha tarjimasini tabiiy qilib qaytadan yasashi kerak».

0.9.76 da usul aytilgan edi (ma'noni tushun → qayta qur). Endi ustiga
**misollar** qo'shildi — model mavhum ko'rsatmadan ko'ra aniq misolga
ancha ishonchli ergashadi. Beshta BAD/GOOD juftligi, har biri bitta
haqiqiy nuqsonni ko'rsatadi:

- «Biz hisobot … topshirilganligiga ishonch hosil qilishimiz kerak»
  → «Hisobotni bugun kech bo'lmasdan topshirishimiz kerak»
- «e'tiboringizni … faktiga qaratmoqchiman» → «aytib o'tmoqchiman»
- «… mumkinligi ehtimoli mavjud» → «… mumkin»
- «joriy holati bo'yicha yangilanish taqdim eta olasizmi» → «qay ahvolda —
  aytib bera olasizmi»
- «… bilan bog'liq holda … taklif qilaman» → «Vaqt tig'iz, shuning uchun …»

Qo'shimcha: kalka qurilmalar nomma-nom taqiqlandi («ishonch hosil qilish»,
«taqdim etish», «joriy holat», «amalga oshirish» va h.k.) va aniq mezon
berildi — **yaxshi o'zbekcha odatda so'zma-so'zidan QISQAROQ**; uzunroq
chiqsa, demak hali so'zma-so'z tarjima qilinyapti.

Misollar FAQAT target=uz bo'lganda qo'shiladi (ruscha ko'rsatma 1134
belgi, o'zbekcha 2908).

## v0.9.79 — JONLI NOSOZLIK: sifatli rejim suhbatdoshni jimlikda qoldirdi

Foydalanuvchi: «mikrofon yana ishlamay qoldi, o'zbekcha gapimning
tarjimasini ikkinchi odam eshitmayapti — kecha eshitilayotgan edi».
Keyin tasdiqladi: «sifatli tarjimani o'chirganimdan keyin ishlab ketdi».

### Ildiz sabab (ikkita, ikkalasi ham meniki)

Log (`conference` 0.9.78, 11:20):

```
[SIFAT] matn modeli: 'gemini-omni-flash-preview'
[SIFAT] ishlamadi (400: This model only supports Interactions API)
        — odatdagi tarjimaga qaytdik
```

1. **Avtomatik model tanlash noto'g'ri modelni oldi.** 56 ta modeldan
   nom bo'yicha saralab `gemini-omni-flash-preview` tanlangan — u
   `generate_content` ni umuman qo'llab-quvvatlamaydi.

2. **Ashaddiy xato: rejim o'zini isbotlamasdan turib "egallab" oldi.**
   Yoqilishi bilan Gemini'ning TAYYOR audiosi tashlanardi, o'zi esa
   birinchi gapdagina yiqilardi. Oradagi ~10 soniyada foydalanuvchi
   gapirdi (`11:20:04 UZ › yaxshi ishlar edim…`), tarjima MATNI chiqdi,
   lekin **ovoz hech qayerga bormadi** — suhbatdosh jimlikni eshitdi.

### Tuzatish

- **Sifatli rejim endi FAQAT KIRUVCHI kanalda.** Chiquvchi yo'l —
  suhbatdosh eshitadigan, biznes uchun eng muhim yo'l — sinalgan Live
  zanjirida qoladi. Tajribaviy zanjirni u yerga qo'yish xato edi.
- **Rejim o'zini ISBOTLAMAGUNCHA jonli audio tashlanmaydi.** Ishga
  tushishda `_verify_quality` kichik sinov chaqiruvini qiladi; faqat
  matn ham, ovoz ham ishlagach `_quality_ready` yoqiladi. Ya'ni yomon
  holatda ham foydalanuvchi **hech qachon jimlikda qolmaydi**.
- **Bitta model o'rniga NOMZODLAR ro'yxati** sinab ko'riladi; ishlamagani
  o'tkazib yuboriladi. Ma'lum yaramaydigan turkumlar (`omni`, `live`,
  `tts`, `embed`, `image`, `vision`, `audio`) oldindan chiqariladi.
- **Bo'sh tarjima endi jimgina tashlanmaydi** — rejim o'chadi va jonli
  tarjimaga qaytiladi (aks holda gap yo'qolardi).
- Tekshirilmagan holatda kelgan gaplar navbatga qo'yilmaydi (jonli audio
  baribir yangrayapti — ikki marta eshitilmasin).

Sinovda tasdiqlandi: yaroqsiz model o'tkazib yuborilib, ishlaydigani
tanlanadi; hech qaysi model ishlamasa rejim yoqilmaydi va jonli audio
o'z yo'lida qolaveradi; chiquvchi kanalda rejim umuman yoqilmaydi.

**SABOQ:** tajribaviy zanjir ishlab turgan zanjirni O'ZINI ISBOTLAMASDAN
almashtirmasligi kerak. To'g'ri tartib — avval sinov chaqiruvi, keyin
almashtirish.

## v0.9.80 — O'z-o'zidan chiqadigan bildirishnomalar olib tashlandi

Foydalanuvchi: «bildirishnomalar, ogohlantirishlarni oddiy Windows 10/11
versiyalaridan ham olib tashla».

Olib tashlandi (foydalanuvchi hech narsa bosmagan holda o'z-o'zidan
chiqadiganlari):

- «Zoom mikrofonini tekshiring» — Start bosilganda chiqardi. Zoom
  sozlamasi bir marta to'g'rilangach keraksiz.
- «Tarjima davom etmoqda — menyu panelidan boshqaring» — oyna yopilganda.
- «Ishga tushdi — yuqoridagi belgidan boshqaring» — dastur ochilganda.
- «Oyna yashirildi — menyu panelidagi belgidan qaytariladi» —
  kichraytirilganda.
- «Fizik karnay topilmadi…» ogohlantirishi.

QOLDIRILDI — faqat foydalanuvchi BOSGAN tugmaga javob beradiganlari
(«Loglar saqlandi», «Sifatli tarjima keyingi ishga tushirishda»,
«Rejimni almashtirish uchun avval to'xtating», «Ovoz endi: …»). Ularsiz
bosilgan tugma hech narsa qilmagandek ko'rinardi.

### Barqaror oqim ham yangilandi

Shu versiyagacha hamkasblar 0.9.71 da qolib turgan edi (sinov ataylab
ajratilgan edi). Foydalanuvchi «oddiy Windows versiyasida tabiiy tarjima
yo'q-ku» deganidan keyin barqaror oqim ham 0.9.80 ga ko'chirildi — ya'ni
boshliq va hamkasblar ham quyidagilarni oladi: sifatli tarjima rejimi,
tabiiy tezlik, qotish tuzatilishi, tozalangan interfeys.

### Antivirus haqida (savolga javob)

Hamkasblarning kompyuterlarida tugmalar ishlamasligining logdan ko'ringan
sababi antivirus emas edi: `j-zarmasov` da API kalit umuman kiritilmagan,
`x-safarov` da qurilma tanlanmagan. Lekin haqiqiy tegishli xavf bor:
**ilova imzolanmagan**, shuning uchun SmartScreen va antiviruslar shubha
qiladi. To'liq yechim — kod imzolash sertifikati (yiliga ~$200–400).
Ro'yxatga qo'shildi.

## v0.9.81 — Soxta chiqish qurilmasi «gapirish» ni o'ldirardi

Foydalanuvchi: «Windows versiya endi gapirmay qoydi, tarjima qilyapti
lekin eshitilmay qoydi». Uy kompyuteri (`user`, Windows 10, 0.9.80).

### Ildiz sabab (logdan)

```
[ROUTING] dialogdan tanlandi: 'Переназначение звуковых устр. - Output'
[AEC] [winaec-worker] karnay=-1 (...) qurilma topilmadi
[AEC] ishlamadi (worker oqimi uzildi) — xavfsiz rejim: Ctrl bosib gapiring
```

«Переназначение звуковых устройств - Output» — **haqiqiy qurilma emas**,
Windows MME yo'naltirgichi (Sound Mapper). Foydalanuvchi uni «ESHITAMAN»
ro'yxatidan tanlagan, chunki ro'yxat uni **chiqarib tashlamagan** edi.

Zanjir:
1. Aks-sado bekor qilish (AEC) orientir sifatida o'sha soxta qurilmani
   oldi → `karnay=-1` → worker o'ldi → dvigatel **«Ctrl bosib gapiring»**
   zaxira rejimiga tushdi. Foydalanuvchi Ctrl bosmagani uchun gapi umuman
   chiqmadi — «gapirmay qoydi» aynan shu.
2. Tarjima ovozi ham o'sha yo'naltirgichga ketdi; u esa TIZIM chiqishiga
   qarab ishlaydi, tizim chiqishi o'sha payt virtual kabelga
   o'zgartiriladi → ovoz kabelga tushib, eshitilmadi.

Ajablanarlisi: `is_alias_output()` funksiyasi va uning ro'yxati
(`переназначение звуков`, `sound mapper`, `первичный звуковой драйвер`)
**allaqachon bor edi**, lekin faqat bitta macOS yo'lida ishlatilardi —
chiqish tanlash ro'yxatida emas.

### Tuzatish

- Soxta qurilmalar «ESHITAMAN» ro'yxatidan **chiqarildi**.
- Ilgari saqlangan soxta tanlov **bekor qilinadi** (aks holda
  yangilanishdan keyin ham nosozlik qolaverardi).

### Ikkinchi muammo: PowerShell 30 soniyada uzildi

```
[ROUTING] startduplex ... timed out after 30 seconds
```

Yo'naltirish o'sha mashinada UMUMAN qo'llanmagan. Sekin kompyuterda yoki
antivirus PowerShell'ni tekshirayotganda C# kompilyatsiya uzoq ketadi.
Kutish vaqti **30 → 90 soniya**. Chaqiruv fon oqimida — GUI qotmaydi.
(Foydalanuvchining «antiviruslar bloklayotgandir» degan taxmini shu
nuqtada haqiqatga eng yaqin.)
