# Live Translator security model

## What actually protects the product

No desktop installer can be made impossible to reverse engineer. Code signing,
native compilation, and obfuscation raise the cost, but a customer controls the
machine on which the client runs. The enforceable boundary is the control
server: licenses, user status, device limits, session telemetry, and production
API credentials must remain server-side.

The current `0.3.x` client supports signed, short-lived license sessions and a
60-second heartbeat. An administrator can revoke a user; the official client
stops no later than the next heartbeat. This blocks normal use but a patched
BYOK client could bypass the local check. Before a paid public launch, proxy the
EDCOM connection through the product backend so revoked clients cannot
reach the paid service even after patching the executable.

## Release controls already implemented

- EDCOM and license values are stored in Keychain/Credential Manager.
- The admin server and its signing/admin secrets are not bundled in the client.
- Release builds run `audit_artifact.py` and fail on common API/private-key
  patterns or secret files.
- macOS has hooks for Developer ID Application signing, hardened runtime, and
  Developer ID Installer signing.
- Windows has a hook for Authenticode signing with `signtool`.
- Remote control URLs must use HTTPS; plain HTTP is accepted only on localhost.
- Control tokens are HMAC-signed, expire after three minutes, and are refreshed
  by heartbeat.

## Production release requirements

1. Deploy `control_server.py` behind HTTPS and a managed database/backups.
2. Set strong `CONTROL_ADMIN_TOKEN` and `CONTROL_SIGNING_SECRET` environment
   variables; never put either value in the installer or source repository.
3. Build clients with `LIVE_TRANSLATOR_CONTROL_URL=https://control.example.com`.
4. Sign and notarize macOS artifacts and Authenticode-sign Windows artifacts.
5. Move the EDCOM credential and websocket relay server-side before
   charging customers or claiming hard revocation.
6. Add rate limiting, audit logs, admin MFA, privacy consent, and retention
   limits before exposing the control server to the internet.
