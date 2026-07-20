#!/bin/zsh
set -e

PROJECT="/Users/abc/meeting-translator-mvp"
APP="$PROJECT/build/English to Uzbek Translator.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
xcrun swiftc \
  -target arm64-apple-macosx13.0 \
  -O \
  -framework AppKit \
  "$PROJECT/TranslatorOverlay.swift" \
  -o "$APP/Contents/MacOS/MeetingTranslator"
cp "$PROJECT/Info.plist" "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP"

echo "$APP"
