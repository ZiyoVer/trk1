"""Desktop client for the optional Live Translator control plane."""

from __future__ import annotations

import json
import os
import platform
import socket
import ssl
import tempfile
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

try:
    import certifi
except ImportError:  # certifi ixtiyoriy — tizim CA'lari bilan ham ishlaymiz
    certifi = None


def ca_bundle_path() -> str:
    """MAVJUD CA to'plami yo'li yoki bo'sh satr.

    MUHIM: certifi.where() faqat yo'l qaytaradi, fayl borligini tekshirmaydi.
    PyInstaller cacert.pem'ni avtomatik qo'shmaydi (certifi hook'i yo'q), shu
    sabab bundle ichida bu yo'l mavjud bo'lmasligi mumkin. Mavjud bo'lmagan
    yo'lni SSL_CERT_FILE'ga yozish OpenSSL'ni butunlay sindiradi — Windows'da
    tizim sertifikatlari bilan ishlayotgan ulanish ham o'ladi.
    """
    for candidate in (
        os.path.join(getattr(sys, "_MEIPASS", ""), "certifi", "cacert.pem")
        if getattr(sys, "_MEIPASS", "")
        else "",
        certifi.where() if certifi is not None else "",
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def secure_ssl_context() -> ssl.SSLContext:
    """Bundle ichidagi Python macOS tizim CA do'konini ko'rmaydi — mavjud
    bo'lsa certifi to'plamidan foydalanamiz, aks holda tizim standarti."""
    bundle = _combined_bundle_path() or ca_bundle_path()
    if bundle:
        try:
            return ssl.create_default_context(cafile=bundle)
        except Exception:
            pass
    return ssl.create_default_context()


def _system_root_pem() -> str:
    """Windows sertifikat do'konidagi ISHONCHLI ildiz sertifikatlar (PEM).

    Korporativ tarmoqlarda TLS trafik tekshiriladi (proxy) va serverning
    sertifikatini KOMPANIYA ildiz sertifikati imzolaydi. U certifi
    to'plamida yo'q, lekin Windows do'koniga tarqatilgan bo'ladi.
    """
    if platform.system() != "Windows":
        return ""
    server_auth = "1.3.6.1.5.5.7.3.1"
    blocks: list[str] = []
    for store in ("ROOT", "CA"):
        try:
            certificates = ssl.enum_certificates(store)
        except Exception:
            continue
        for cert, encoding, trust in certificates:
            if encoding != "x509_asn":
                continue
            # trust: True (hamma maqsad uchun) yoki OID to'plami
            if trust is not True and (not trust or server_auth not in trust):
                continue
            try:
                blocks.append(ssl.DER_cert_to_PEM_cert(cert))
            except Exception:
                continue
    return "".join(blocks)


def _combined_bundle_path() -> str:
    """certifi + tizim ildizlari birlashgan CA fayli (yoki bo'sh satr).

    Faqat certifi ishlatilsa korporativ tarmoqda HAMMA TLS ulanish o'ladi
    (CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate) —
    Gemini'ga ulanish ham, drayver yuklab olish ham. Ikkala manbani
    birlashtiramiz: ochiq internet ham, kompaniya proxy'si ham ishlaydi.
    """
    system_pem = _system_root_pem()
    if not system_pem:
        return ""  # tizim do'koni yo'q/bo'sh — odatdagi certifi yo'li
    base = ca_bundle_path()
    try:
        certifi_pem = ""
        if base:
            with open(base, encoding="utf-8", errors="ignore") as handle:
                certifi_pem = handle.read()
        folder = os.path.join(
            os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
            "Live Translator",
        )
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, "ca-bundle.pem")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(certifi_pem)
            if certifi_pem and not certifi_pem.endswith("\n"):
                handle.write("\n")
            handle.write(system_pem)
        # Buzuq fayl OpenSSL'ni butunlay sindiradi — yozgach TEKSHIRAMIZ.
        ssl.create_default_context(cafile=target)
        return target
    except Exception:
        return ""


def ensure_ca_bundle_env() -> None:
    """CA yo'lini butun jarayon (va child dvigatel) uchun e'lon qiladi —
    FAQAT fayl haqiqatan mavjud bo'lsa."""
    bundle = _combined_bundle_path() or ca_bundle_path()
    if not bundle:
        return
    os.environ.setdefault("SSL_CERT_FILE", bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)


class LicenseError(RuntimeError):
    pass


def validate_control_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LicenseError("Boshqaruv serveri URL’i noto‘g‘ri")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise LicenseError("Remote boshqaruv serveri HTTPS bo‘lishi shart")
    return url


class LicenseClient:
    def __init__(
        self,
        control_url: str,
        license_key: str,
        device_id: str,
        app_version: str,
        timeout: float = 5.0,
    ):
        self.control_url = validate_control_url(control_url)
        self.license_key = license_key.strip()
        self.device_id = device_id.strip()
        self.app_version = app_version
        self.timeout = timeout
        self.access_token = ""
        self.user_name = ""
        self.session_ids: list[str] = []

    @property
    def session_id(self) -> str:
        """Backward-compatible view used by older callers."""

        return self.session_ids[0] if self.session_ids else ""

    @property
    def enabled(self) -> bool:
        return bool(self.control_url)

    def _request(
        self, path: str, payload: dict[str, Any], *, authenticated: bool = False
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if authenticated:
            if not self.access_token:
                raise LicenseError("Litsenziya sessiyasi topilmadi")
            headers["Authorization"] = f"Bearer {self.access_token}"
        request = urllib.request.Request(
            f"{self.control_url}{path}",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=secure_ssl_context()
            ) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read()).get("error", str(error))
            except Exception:
                detail = str(error)
            raise LicenseError(str(detail)) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise LicenseError(f"Boshqaruv serveriga ulanib bo‘lmadi: {error}") from error
        except (ValueError, TypeError) as error:
            raise LicenseError("Boshqaruv serveridan noto‘g‘ri javob keldi") from error
        if not isinstance(result, dict):
            raise LicenseError("Boshqaruv serveridan noto‘g‘ri javob keldi")
        return result

    def activate(self) -> str:
        if not self.enabled:
            return "Developer mode"
        if not self.license_key:
            raise LicenseError("Litsenziya kalitini Settings ichida kiriting")
        result = self._request(
            "/api/v1/activate",
            {
                "license_key": self.license_key,
                "device_id": self.device_id,
                "device_label": socket.gethostname(),
                "platform": f"{platform.system()} {platform.machine()}",
                "app_version": self.app_version,
            },
        )
        self.access_token = str(result.get("access_token", ""))
        self.user_name = str(result.get("user", {}).get("name", ""))
        if not self.access_token:
            raise LicenseError("Server session token bermadi")
        return self.user_name or "Active user"

    def heartbeat(self) -> None:
        result = self._request(
            "/api/v1/heartbeat",
            {"app_version": self.app_version},
            authenticated=True,
        )
        refreshed = str(result.get("access_token", ""))
        if refreshed:
            self.access_token = refreshed
        if not result.get("active"):
            raise LicenseError("Litsenziya o‘chirilgan")

    def start_session(
        self,
        target_language: str,
        input_device: str,
        output_device: str,
        source_language: str = "auto",
        mode: str = "incoming",
    ) -> None:
        if not self.enabled:
            return
        result = self._request(
            "/api/v1/sessions/start",
            {
                "mode": mode,
                "source_language": source_language,
                "target_language": target_language,
                "input_device": input_device,
                "output_device": output_device,
            },
            authenticated=True,
        )
        session_id = str(result.get("session_id", ""))
        if session_id:
            self.session_ids.append(session_id)

    def end_session(self) -> None:
        if not self.enabled or not self.session_ids or not self.access_token:
            return
        session_ids, self.session_ids = self.session_ids, []
        first_error: Exception | None = None
        for session_id in session_ids:
            try:
                self._request(
                    "/api/v1/sessions/end",
                    {"session_id": session_id},
                    authenticated=True,
                )
            except Exception as error:
                first_error = first_error or error
        if first_error:
            raise first_error
