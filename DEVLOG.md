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
