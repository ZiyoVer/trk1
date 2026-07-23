#define MyAppName "Live Translator"
#define MyAppVersion "0.9.38"
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
