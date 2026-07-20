$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv-product\Scripts\python.exe")) {
    py -3.11 -m venv .venv-product
}

& .venv-product\Scripts\python.exe -m pip install --upgrade pip
& .venv-product\Scripts\python.exe -m pip install -r requirements-product.txt

Remove-Item -Recurse -Force build\product, dist\product -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force build\product | Out-Null
$ControlUrl = if ($env:LIVE_TRANSLATOR_CONTROL_URL) { $env:LIVE_TRANSLATOR_CONTROL_URL } else { "" }
$ControlUrlLiteral = ConvertTo-Json $ControlUrl -Compress
$RuntimeHook = Join-Path $Root "build\control_url_runtime_hook.py"
Set-Content -Encoding UTF8 $RuntimeHook "import os`nos.environ.setdefault(`"LIVE_TRANSLATOR_CONTROL_URL`", $ControlUrlLiteral)`n"
& .venv-product\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name "Live Translator" `
    --icon "$Root\packaging\icon\AppIcon.ico" `
    --distpath dist\product `
    --workpath build\product `
    --specpath build\product `
    --collect-all google.genai `
    --collect-all sounddevice `
    --collect-submodules keyring.backends `
    --runtime-hook $RuntimeHook `
    product_app.py

$AppFolder = Join-Path $Root "dist\product\Live Translator"
& .venv-product\Scripts\python.exe audit_artifact.py $AppFolder

$SignTool = $null
if ($env:WINDOWS_SIGN_CERT_SHA1) {
    $SignTool = "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
    if (-not (Test-Path $SignTool)) {
        $SignTool = (Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" |
            Sort-Object FullName -Descending | Select-Object -First 1).FullName
    }
    if (-not $SignTool) { throw "Windows signtool topilmadi" }
    $AppExe = Join-Path $AppFolder "Live Translator.exe"
    & $SignTool sign /sha1 $env:WINDOWS_SIGN_CERT_SHA1 /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $AppExe
}

$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) {
    throw "Inno Setup 6 topilmadi: $Iscc"
}
& $Iscc packaging\windows\LiveTranslator.iss

$Installer = Join-Path $Root "installer\windows\LiveTranslator-Setup-0.5.0.exe"
if ($SignTool) {
    & $SignTool sign /sha1 $env:WINDOWS_SIGN_CERT_SHA1 /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $Installer
}
