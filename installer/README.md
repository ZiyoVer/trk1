# Installer status

## macOS

Development installer:

`macos/LiveTranslator-0.5.0-macOS-arm64.pkg`

It installs `Live Translator.app` into `/Applications`. The application bundles
Python, Qt, sounddevice, WebSocket support, and the translation engine. It does not
bundle `.env` or a developer API key.

This local package is not Developer ID signed or notarized. Public distribution
requires Apple Developer ID Installer/Application certificates and notarization.

## Windows

`build_product_windows.ps1` creates the PyInstaller application and then builds
`installer/windows/LiveTranslator-Setup-0.5.0.exe` with Inno Setup 6. The
Windows artifact must be built and tested on Windows; the GitHub Actions workflow
contains that build job.

Public distribution requires Authenticode signing. VB-CABLE is downloaded from
the official vendor by the first-run setup assistant and is not embedded in the
application installer.

## First run

1. Enter the company EDCOM API key; it is saved in Keychain/Credential Manager.
2. If the virtual audio driver is missing, click `AUDIO DRIVER O‘RNATISH`.
3. Approve the official driver installer and reboot when requested.
4. Select the translation direction and click `BOSHLASH`.
