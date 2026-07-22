# Live Translator — virtual audio drayverini avtomatik o'rnatish.
#
# O'rnatuvchi (Inno Setup) tomonidan admin huquqida chaqiriladi, shuning
# uchun VB-CABLE ham qo'shimcha UAC'siz jim o'rnatiladi. Foydalanuvchi
# hech narsa qilmaydi. Xato bo'lsa ham o'rnatishni to'xtatmaydi — ilova
# birinchi ochilishda qayta urinadi.

$ErrorActionPreference = "Stop"
$log = Join-Path $env:TEMP "lt_driver_install.log"
function Log($m) { Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $m) }

try {
    $existing = Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*CABLE*" }
    if ($existing) {
        Log "VB-CABLE allaqachon o'rnatilgan — o'tkazib yuborildi."
        exit 0
    }

    $url = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
    $zip = Join-Path $env:TEMP "lt_vbcable.zip"
    $dir = Join-Path $env:TEMP "lt_vbcable"

    Log "Yuklab olinmoqda: $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing

    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    Expand-Archive -Path $zip -DestinationPath $dir -Force

    $setup = if ([Environment]::Is64BitOperatingSystem) {
        Join-Path $dir "VBCABLE_Setup_x64.exe"
    } else {
        Join-Path $dir "VBCABLE_Setup.exe"
    }

    Log "Jim o'rnatilmoqda: $setup -i -h"
    $proc = Start-Process -FilePath $setup -ArgumentList "-i", "-h" -Wait -PassThru
    Log ("Setup exit code: " + $proc.ExitCode)

    Start-Sleep -Seconds 4
    $now = Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*CABLE*" }
    if ($now) {
        Log ("Muvaffaqiyat — qurilma: " + (($now.Name) -join ", "))
    } else {
        Log "Ogohlantirish: o'rnatishdan keyin CABLE topilmadi (reboot kerak bo'lishi mumkin)."
    }
} catch {
    Log ("XATO: " + $_.Exception.Message)
    # Chiqish kodini 0 qoldiramiz — drayver xatosi ilova o'rnatilishini
    # to'xtatmasin (ilova o'zi qayta urinadi).
}
exit 0
