#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
APP_VERSION="${APP_VERSION:-0.5.0}"
PKG_NAME="LiveTranslator-${APP_VERSION}-macOS-arm64.pkg"
CONTROL_URL="${LIVE_TRANSLATOR_CONTROL_URL:-}"
APP_SIGN_IDENTITY="${MACOS_APP_SIGN_IDENTITY:--}"
INSTALLER_SIGN_IDENTITY="${MACOS_INSTALLER_SIGN_IDENTITY:-}"

rm -rf build/product dist/product installer/macos
mkdir -p installer/macos

arch -arm64 .venv/bin/python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --target-arch arm64 \
  --name "Live Translator" \
  --icon packaging/icon/AppIcon.icns \
  --osx-bundle-identifier "local.live-translator" \
  --distpath dist/product \
  --workpath build/product \
  --specpath build/product \
  --collect-all google.genai \
  --collect-all sounddevice \
  --collect-submodules keyring.backends \
  product_app.py

# Required for the virtual audio input. Without this key macOS can leave the
# packaged child engine waiting for audio permission while the API itself is OK.
plutil -replace NSMicrophoneUsageDescription \
  -string "Live Translator needs microphone access to read the selected virtual audio device for real-time translation." \
  "dist/product/Live Translator.app/Contents/Info.plist" 2>/dev/null || \
plutil -insert NSMicrophoneUsageDescription \
  -string "Live Translator needs microphone access to read the selected virtual audio device for real-time translation." \
  "dist/product/Live Translator.app/Contents/Info.plist"
plutil -replace CFBundleShortVersionString -string "$APP_VERSION" \
  "dist/product/Live Translator.app/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$APP_VERSION" \
  "dist/product/Live Translator.app/Contents/Info.plist" 2>/dev/null || \
plutil -insert CFBundleVersion -string "$APP_VERSION" \
  "dist/product/Live Translator.app/Contents/Info.plist"
plutil -replace LSMinimumSystemVersion -string "12.0" \
  "dist/product/Live Translator.app/Contents/Info.plist" 2>/dev/null || \
plutil -insert LSMinimumSystemVersion -string "12.0" \
  "dist/product/Live Translator.app/Contents/Info.plist"
if [[ -n "$CONTROL_URL" ]]; then
  plutil -replace LiveTranslatorControlURL -string "$CONTROL_URL" \
    "dist/product/Live Translator.app/Contents/Info.plist" 2>/dev/null || \
  plutil -insert LiveTranslatorControlURL -string "$CONTROL_URL" \
    "dist/product/Live Translator.app/Contents/Info.plist"
fi

if [[ "$APP_SIGN_IDENTITY" == "-" ]]; then
  codesign --force --deep --sign - "dist/product/Live Translator.app"
else
  codesign --force --deep --options runtime --timestamp \
    --sign "$APP_SIGN_IDENTITY" "dist/product/Live Translator.app"
fi

arch -arm64 .venv/bin/python audit_artifact.py \
  "dist/product/Live Translator.app"

UNSIGNED_PKG="installer/macos/$PKG_NAME"
if [[ -n "$INSTALLER_SIGN_IDENTITY" ]]; then
  UNSIGNED_PKG="installer/macos/LiveTranslator-${APP_VERSION}-unsigned.pkg"
fi
pkgbuild \
  --component "dist/product/Live Translator.app" \
  --install-location /Applications \
  --identifier "local.live-translator" \
  --version "$APP_VERSION" \
  "$UNSIGNED_PKG"

if [[ -n "$INSTALLER_SIGN_IDENTITY" ]]; then
  productsign --sign "$INSTALLER_SIGN_IDENTITY" --timestamp \
    "$UNSIGNED_PKG" "installer/macos/$PKG_NAME"
  rm "$UNSIGNED_PKG"
fi

echo "$ROOT/installer/macos/$PKG_NAME"
