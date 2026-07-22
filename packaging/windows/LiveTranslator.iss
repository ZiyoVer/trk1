#define MyAppName "Live Translator"
#define MyAppVersion "0.5.0"
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
; Admin: virtual audio drayver (VB-CABLE) o'rnatish uchun shart. Bitta UAC
; butun jarayonni qamraydi — drayver bola jarayon elevation'ni meros
; qiladi, alohida so'rov chiqmaydi.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible arm64
ArchitecturesInstallIn64BitMode=x64compatible arm64
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\..\dist\product\Live Translator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
; Virtual audio kabelini AVTOMATIK o'rnatish (foydalanuvchi hech narsa
; qilmaydi). Idempotent: kabel allaqachon bo'lsa qayta o'rnatmaydi.
; Internet bo'lmasa jimgina o'tadi — ilova birinchi ochilishda qayta urinadi.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_drivers.ps1"""; \
  StatusMsg: "Virtual audio drayveri o'rnatilmoqda…"; \
  Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
