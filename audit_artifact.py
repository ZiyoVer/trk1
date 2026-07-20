"""Fail a release build if common secrets or private-key files are bundled."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


SECRET_PATTERNS = {
    "Google API key": re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
    "Google OAuth-style secret": re.compile(rb"AQ\.[0-9A-Za-z_-]{30,}"),
    "OpenAI-style key": re.compile(rb"sk-[0-9A-Za-z_-]{20,}"),
}
PRIVATE_KEY_HEADERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)
FORBIDDEN_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
}
FORBIDDEN_SUFFIXES = {".key", ".p12", ".pfx"}


def audit(root: Path) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.name.casefold() in FORBIDDEN_NAMES or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            findings.append(f"forbidden file: {relative}")
            continue
        try:
            data = path.read_bytes()
        except OSError as error:
            findings.append(f"unreadable file: {relative}: {error}")
            continue
        if data.lstrip().startswith(PRIVATE_KEY_HEADERS):
            findings.append(f"Private key: {relative}")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                findings.append(f"{label}: {relative}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    findings = audit(args.artifact)
    if findings:
        print("Release artifact xavfsizlik tekshiruvidan o‘tmadi:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("✓ Release artifact secret scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
