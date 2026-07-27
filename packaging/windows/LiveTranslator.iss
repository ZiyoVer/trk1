#define MyAppName "Live Translator"
#define MyAppVersion "0.9.55"
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
OutputBaseFilename=LiveTranslator-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
SetupIconFile=..\icon\AppIcon.ico
WizardStyle=modern
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

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

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
  { Per-user o'rnatish (PrivilegesRequired=lowest) -> odatda HKCU }
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A1A35FA3-89DA-4D3C-A593-A2719144A515}_is1';
  Value := '';
  if not RegQueryStringValue(HKCU, Key, 'UninstallString', Value) then
    RegQueryStringValue(HKLM, Key, 'UninstallString', Value);
  Result := Value;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  UninstallCmd: String;
  ResultCode: Integer;
begin
  Result := '';

  { 1) Ishlab turgan dasturni (va dvigatel bolasini) majburan yopamiz }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM "Live Translator.exe" /T /F',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1200);

  { 2) Eski versiyani jim o'chiramiz (bo'lsa). Xato bo'lsa ham davom etamiz:
       o'chirilmasa ham o'rnatish odatdagidek ustiga yozadi. }
  UninstallCmd := RemoveQuotes(GetUninstallString());
  if UninstallCmd <> '' then
  begin
    Exec(UninstallCmd, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  end;
end;
