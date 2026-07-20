# Live Translator — distributable product plan

## Problem

The current prototype assumes that Python, an API key, and a virtual audio
driver are already installed and configured. A normal Zoom, Meet, or YouTube
user cannot be expected to perform that setup manually.

## Target user

A non-technical macOS or Windows user who wants to hear a meeting or video in
another language and expects an install-and-run desktop application.

## Product solution

One desktop application with:

- a signed macOS installer and a Windows installer;
- first-run EDCOM API key setup stored in Keychain/Credential Manager;
- virtual audio driver detection and guided official driver installation;
- automatic device health checks before Start is enabled;
- source captions, translated captions, Charon audio, Start/Stop, and language
  selection independent from the listening/speaking product mode;
- one translator process maximum and bounded playback latency.
- explicit input/output device routing for both listening and translated virtual-mic modes;
- a self-hosted control plane for users, devices, live sessions, limits, and revocation.

## Must-have acceptance criteria

1. A clean machine can install the app without Python or Terminal.
2. The app never ships a developer API key.
3. The operator is prompted for the company EDCOM key on first launch.
4. If the virtual driver is missing, the app downloads only the official
   installer and verifies its checksum where a stable checksum is published.
5. macOS detects `BlackHole 2ch`; Windows detects `CABLE Output`.
6. Start stays unavailable until API key and valid input/output devices are selected.
7. The app restores a useful error state instead of silently freezing.
8. macOS and Windows installers are produced by repeatable build scripts/CI.
9. A licensed build activates a device before translation and stops within two
   missed heartbeats or immediately after an explicit revocation response.
10. Admin can create a license, limit devices, view live usage, revoke, and
    restore a user without rebuilding the client.
11. `Microphone → BlackHole` sends translated speech to Zoom/Meet when that
    virtual device is selected as the meeting microphone.
12. Listening and speaking modes each remember an independent source/target
    pair across English, Uzbek, and Russian; listening also offers automatic
    source detection for multilingual meetings.
13. Control-plane sessions store and expose mode, source language, target
    language, input device, and output device without resetting older data.
14. `Ikkalasi` mode runs incoming and outgoing EDCOM sessions in parallel,
    requires two logically independent virtual cables, and restores both the
    previous system input and system output after Stop or a crash.

## Constraints and decisions

- BlackHole source is GPL-3.0, but official compiled installers and branding
  are separately protected. The product must not rebundle the official binary
  without permission; it downloads the official package during setup.
- VB-CABLE distribution requires its own license. The product downloads the
  official end-user package instead of embedding it until a distribution
  agreement is signed.
- Driver installation requires administrator approval and often a reboot; no
  legitimate installer can silently bypass those OS security prompts.
- Public releases require Apple Developer ID signing/notarization and a Windows
  code-signing certificate. Local development builds are ad-hoc signed.
- Client-side checks raise the cost of casual copying but cannot provide hard
  revocation against a patched binary; production should keep the EDCOM
  credential behind a controlled relay.

## Success metrics

- clean-install-to-first-translation completion rate: at least 85%;
- median setup time excluding reboot: under 4 minutes;
- translation session crash-free rate: at least 99%;
- duplicate translator process rate: 0%;
- playback backlog: no more than 2 seconds.

## Release sequence

1. Cross-platform application shell and BYOK onboarding.
2. macOS `.pkg` development installer and clean-machine test.
3. Windows `.exe`/Inno Setup build in Windows CI.
4. Driver setup assistant and routing verification.
5. Signing, notarization, auto-update, privacy/consent screens, beta release.
6. Hosted control plane, admin MFA/audit logs, and a server-side Live relay.
