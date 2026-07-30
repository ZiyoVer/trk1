#define MyAppName "Live Translator"
#define MyAppVersion "0.9.93"
#define MyAppPublisher "Live Translator"
#define MyAppExeName "Live Translator.exe"

[Setup]
AppId={{A1A35FA3-89DA-4D3C-A593-A2719144A515}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\..\installer\windows
; IKKI XIL O'RNATUVCHI, BITTA KOD:
;   oddiy  -> LiveTranslator-Setup-x.y.z.exe       (boshliq, hamkasblar)
;   /DOPS  -> LiveTranslator-OPS-Setup-x.y.z.exe   (monitorli sinov mashinasi)
; Farqi faqat `channel.txt` da — u yangilanish oqimini ajratadi, shunda
; sinov versiyasi barqaror kompyuterlarga ARALASHMAYDI.
#ifdef OPS
OutputBaseFilename=LiveTranslator-OPS-Setup-{#MyAppVersion}
#else
OutputBaseFilename=LiveTranslator-Setup-{#MyAppVersion}
#endif
Compression=lzma2
SolidCompression=yes
SetupIconFile=..\icon\AppIcon.ico
WizardStyle=modern
; HECH QANDAY TUGMA BOSILMAYDI: eski versiyadagi ilova o'rnatuvchini oddiy
; (jim bo'lmagan) rejimda ochadi — shunda ham foydalanuvchi "Next/Install"
; bosib o'tirmasin. Barcha sahifalar o'chirilgan: o'rnatuvchi ochiladi,
; o'rnatadi va o'zi yopilib dasturni qayta ochadi.
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
; Ilova admin talab qilmaydi (LocalAppData). Drayverni ILOVANING O'ZI
; birinchi ochilganda avtomatik o'rnatadi (bitta UAC). Installer [Run]
; orqali drayver o'rnatish ishonchsiz edi (elevated bo'lmagan sessiyada
; exit 5) — olib tashlandi.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible arm64
ArchitecturesInstallIn64BitMode=x64compatible arm64
UninstallDisplayIcon={app}\{#MyAppExeName}
; MUHIM: ilova tray'da ochiq turganda .exe qulflanib, yangilanish
; o'rnatilmasdi (foydalanuvchi eski nusxada qolib ketardi). Restart
; Manager orqali ishlab turgan dasturni MAJBURAN yopamiz.
CloseApplications=force
RestartApplications=no

[Files]
Source: "..\..\dist\product\Live Translator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef OPS
Source: "channel-ops.txt"; DestDir: "{app}"; DestName: "channel.txt"; Flags: ignoreversion
#endif

#ifndef OPS
[InstallDelete]
; Barqaror versiya OPS belgisini olib tashlaydi — OPS o'rnatilgan
; kompyuterga barqarorni o'rnatsak, u sinov oqimida qolib ketmasin.
Type: files; Name: "{app}\channel.txt"
#endif

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; Yorliq har doim yaratiladi — "qo'shaymi?" degan savol sahifasi olib tashlandi.
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
; `skipifsilent` OLIB TASHLANDI: ilova ichidagi avtomatik yangilanish
; o'rnatuvchini JIM rejimda ishga tushiradi va shundan keyin dastur
; O'ZI qayta ochilishi kerak (foydalanuvchi hech narsa bosmaydi).
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall

[Code]
{ TOZA YANGILASH: yangi versiyani o'rnatishdan OLDIN ishlab turgan dasturni
  yopamiz va eski versiyani butunlay o'chiramiz.

  Sabab (2026-07-27 jonli nosozlik): dastur ochiq turganda fayl qulflanib,
  o'rnatuvchi "DeleteFile failed; code 5" bilan yiqilardi va o'rnatish
  YARIM QOLIB, ilova umuman ishlamay qolardi (papkada faqat unins*.exe).
  Restart Manager (CloseApplications=force) boshqa sessiyadagi jarayonni
  yopa olmaydi — shuning uchun taskkill + jim uninstall qo'shildi.

  Foydalanuvchi ma'lumotlariga TEGILMAYDI: sozlamalar/loglar
  %LOCALAPPDATA%\Live Translator ichida, API kalit esa Windows Credential
  Manager'da — uninstaller faqat o'zi o'rnatgan fayllarni o'chiradi. }

function GetUninstallString(): String;
var
  Key: String;
  Value: String;
begin
  Result := '';
  { Har bir qadam TRY ichida: registr o'qish ham istisno chiqarishi mumkin
    (masalan siyosat bilan cheklangan mashinada). }
  try
    Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A1A35FA3-89DA-4D3C-A593-A2719144A515}_is1';
    Value := '';
    if not RegQueryStringValue(HKCU, Key, 'UninstallString', Value) then
      RegQueryStringValue(HKLM, Key, 'UninstallString', Value);
    Result := Value;
  except
    Result := '';
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  UninstallCmd: String;
  ResultCode: Integer;
begin
  { HECH QACHON YIQILMAYDI.

    Jonli nosozlik (2026-07-30): foydalanuvchida o'rnatishda «Unhandled
    exception in script» chiqdi. Bu bo'lim faqat QULAYLIK uchun — eski
    nusxani yopib, o'chirib tashlaydi. U ishlamasa ham o'rnatish davom
    etishi kerak edi, lekin istisno butun o'rnatishni to'xtatib qo'ydi.
    Endi har bir qadam `try..except` ichida va `Result` doim bo'sh —
    ya'ni o'rnatish har holatda davom etadi. }
  Result := '';

  { 1) Ishlab turgan dasturni (va dvigatel bolasini) majburan yopamiz.

       DIQQAT — bu yerda `/T` BO'LMASLIGI SHART. Ilgari `/T` bor edi va u
       jarayonni BUTUN DARAXTI bilan o'ldirardi. Yangilanishda o'rnatuvchi
       ilovaning bola jarayoni bo'lib ishga tushadi — ya'ni `/T` bilan
       o'rnatuvchi O'Z-O'ZINI o'ldirardi va yangilanish hech qachon
       tugamasdi.

       `/T` kerak ham emas: dvigatel va AEC bola jarayonlari ham AYNAN
       shu nomdagi exe (PyInstaller `sys.executable`), demak `/IM` ularni
       nomi bo'yicha baribir yopadi. }
  try
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM "Live Translator.exe" /F',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  except
    { taskkill topilmasa yoki bloklangan bo'lsa — e'tibor bermaymiz }
  end;

  { 2) Eski versiyani jim o'chiramiz (bo'lsa). Xato bo'lsa ham davom etamiz:
       o'chirilmasa ham o'rnatish odatdagidek ustiga yozadi. }
  try
    UninstallCmd := RemoveQuotes(GetUninstallString());
    if UninstallCmd <> '' then
    begin
      Exec(UninstallCmd, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
           '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Sleep(1500);
    end;
  except
    { eski o'chiruvchi yo'q/buzuq bo'lsa — o'rnatish ustiga yozadi }
  end;
end;
