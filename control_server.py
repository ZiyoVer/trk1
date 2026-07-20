"""Small self-hosted control plane for Live Translator licenses and usage.

The service intentionally uses only Python's standard library so it can be
deployed without adding server frameworks to the desktop installer.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


TOKEN_TTL_SECONDS = 180
MAX_BODY_BYTES = 32_768
SOURCE_LANGUAGES = frozenset({"auto", "en", "uz", "ru", "es"})
TARGET_LANGUAGES = frozenset({"en", "uz", "ru", "es"})
TRANSLATION_MODES = frozenset({"incoming", "outgoing"})


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class ControlError(RuntimeError):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


class TokenSigner:
    def __init__(self, secret: str):
        if len(secret) < 32:
            raise ValueError("CONTROL_SIGNING_SECRET kamida 32 belgi bo‘lishi kerak")
        self.secret = secret.encode("utf-8")

    def issue(self, user_id: str, device_id: str) -> str:
        payload = {
            "uid": user_id,
            "did": device_id,
            "exp": int(time.time()) + TOKEN_TTL_SECONDS,
            "nonce": secrets.token_hex(8),
        }
        encoded = _b64_encode(json.dumps(payload, separators=(",", ":")).encode())
        signature = _b64_encode(hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify(self, token: str) -> dict[str, Any]:
        try:
            encoded, supplied = token.split(".", 1)
            expected = _b64_encode(
                hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(supplied, expected):
                raise ValueError
            payload = json.loads(_b64_decode(encoded))
            if int(payload["exp"]) < int(time.time()):
                raise ControlError("Session token muddati tugagan", HTTPStatus.UNAUTHORIZED)
            if not payload.get("uid") or not payload.get("did"):
                raise ValueError
            return payload
        except ControlError:
            raise
        except Exception as error:
            raise ControlError("Session token noto‘g‘ri", HTTPStatus.UNAUTHORIZED) from error


class ControlStore:
    def __init__(self, database: Path, signing_secret: str):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.database.parent.chmod(0o700)
        self.license_secret = signing_secret.encode("utf-8")
        self._initialize()
        self.database.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    license_digest TEXT NOT NULL UNIQUE,
                    license_hint TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active', 'revoked')),
                    max_devices INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS devices_user_idx ON devices(user_id);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                    mode TEXT NOT NULL DEFAULT 'incoming',
                    source_language TEXT NOT NULL DEFAULT 'auto',
                    target_language TEXT NOT NULL,
                    input_device TEXT NOT NULL,
                    output_device TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    ended_at TEXT
                );
                CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
                """
            )
            session_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "mode" not in session_columns:
                db.execute(
                    "ALTER TABLE sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'incoming'"
                )
            if "source_language" not in session_columns:
                db.execute(
                    "ALTER TABLE sessions ADD COLUMN source_language TEXT NOT NULL DEFAULT 'auto'"
                )

    def _license_digest(self, license_key: str) -> str:
        return hmac.new(
            self.license_secret, license_key.strip().upper().encode(), hashlib.sha256
        ).hexdigest()

    def create_user(
        self, name: str, email: str, max_devices: int = 1, expires_at: str | None = None
    ) -> dict[str, Any]:
        name, email = name.strip(), email.strip().lower()
        if not name or "@" not in email:
            raise ControlError("Ism va to‘g‘ri email kiriting")
        if not 1 <= max_devices <= 20:
            raise ControlError("Device limiti 1–20 orasida bo‘lishi kerak")
        user_id = str(uuid.uuid4())
        raw = secrets.token_hex(12).upper()
        license_key = "LT-" + "-".join(raw[index : index + 6] for index in range(0, 24, 6))
        created_at = utc_now()
        try:
            with self._connect() as db:
                db.execute(
                    """INSERT INTO users
                       (id, name, email, license_digest, license_hint, max_devices,
                        created_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        name,
                        email,
                        self._license_digest(license_key),
                        license_key[-6:],
                        max_devices,
                        created_at,
                        expires_at or None,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ControlError("Bu email allaqachon mavjud") from error
        return {
            "id": user_id,
            "name": name,
            "email": email,
            "license_key": license_key,
            "status": "active",
            "max_devices": max_devices,
            "created_at": created_at,
        }

    def set_user_status(self, user_id: str, status: str) -> None:
        if status not in {"active", "revoked"}:
            raise ControlError("Status active yoki revoked bo‘lishi kerak")
        with self._connect() as db:
            cursor = db.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
            if not cursor.rowcount:
                raise ControlError("Foydalanuvchi topilmadi", HTTPStatus.NOT_FOUND)
            if status == "revoked":
                db.execute(
                    "UPDATE sessions SET ended_at = ? WHERE user_id = ? AND ended_at IS NULL",
                    (utc_now(), user_id),
                )

    def _active_user(self, db: sqlite3.Connection, user_id: str) -> sqlite3.Row:
        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise ControlError("Foydalanuvchi topilmadi", HTTPStatus.UNAUTHORIZED)
        if user["status"] != "active":
            raise ControlError("Litsenziya administrator tomonidan o‘chirilgan", HTTPStatus.FORBIDDEN)
        if user["expires_at"] and user["expires_at"] < utc_now():
            raise ControlError("Litsenziya muddati tugagan", HTTPStatus.FORBIDDEN)
        return user

    def activate(
        self,
        license_key: str,
        device_id: str,
        label: str,
        platform_name: str,
        app_version: str,
    ) -> dict[str, str]:
        if not license_key.strip() or not device_id.strip():
            raise ControlError("Litsenziya va device ID kerak")
        now = utc_now()
        with self._connect() as db:
            user = db.execute(
                "SELECT * FROM users WHERE license_digest = ?",
                (self._license_digest(license_key),),
            ).fetchone()
            if not user:
                raise ControlError("Litsenziya kaliti topilmadi", HTTPStatus.UNAUTHORIZED)
            self._active_user(db, user["id"])
            device = db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if device and device["user_id"] != user["id"]:
                raise ControlError("Bu device boshqa accountga bog‘langan", HTTPStatus.FORBIDDEN)
            if not device:
                count = db.execute(
                    "SELECT COUNT(*) FROM devices WHERE user_id = ?", (user["id"],)
                ).fetchone()[0]
                if count >= user["max_devices"]:
                    raise ControlError("Device limiti tugagan", HTTPStatus.FORBIDDEN)
                db.execute(
                    """INSERT INTO devices
                       (id, user_id, label, platform, app_version, first_seen_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (device_id, user["id"], label[:80], platform_name[:40], app_version[:20], now, now),
                )
            else:
                db.execute(
                    """UPDATE devices SET label = ?, platform = ?, app_version = ?,
                       last_seen_at = ? WHERE id = ?""",
                    (label[:80], platform_name[:40], app_version[:20], now, device_id),
                )
        return {"user_id": user["id"], "name": user["name"]}

    def heartbeat(self, user_id: str, device_id: str, app_version: str) -> None:
        now = utc_now()
        with self._connect() as db:
            self._active_user(db, user_id)
            device = db.execute(
                "SELECT user_id FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
            if not device or device["user_id"] != user_id:
                raise ControlError("Device ruxsati topilmadi", HTTPStatus.FORBIDDEN)
            db.execute(
                """UPDATE devices SET last_seen_at = ?,
                   app_version = CASE WHEN ? = '' THEN app_version ELSE ? END
                   WHERE id = ?""",
                (now, app_version[:20], app_version[:20], device_id),
            )
            db.execute(
                """UPDATE sessions SET last_seen_at = ?
                   WHERE device_id = ? AND ended_at IS NULL""",
                (now, device_id),
            )

    def start_session(
        self,
        user_id: str,
        device_id: str,
        target_language: str,
        input_device: str,
        output_device: str,
        source_language: str = "auto",
        mode: str = "incoming",
    ) -> str:
        source_language = source_language.strip().lower() or "auto"
        target_language = target_language.strip().lower()
        mode = mode.strip().lower() or "incoming"
        if source_language not in SOURCE_LANGUAGES:
            raise ControlError("Manba tili qo‘llanmaydi")
        if target_language not in TARGET_LANGUAGES:
            raise ControlError("Tarjima tili qo‘llanmaydi")
        if source_language == target_language:
            raise ControlError("Manba va tarjima tili bir xil bo‘lishi mumkin emas")
        if mode not in TRANSLATION_MODES:
            raise ControlError("Tarjima rejimi noto‘g‘ri")
        self.heartbeat(user_id, device_id, "")
        session_id, now = str(uuid.uuid4()), utc_now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO sessions
                   (id, user_id, device_id, mode, source_language,
                    target_language, input_device, output_device, started_at,
                    last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    user_id,
                    device_id,
                    mode,
                    source_language,
                    target_language[:12],
                    input_device[:120],
                    output_device[:120],
                    now,
                    now,
                ),
            )
        return session_id

    def end_session(self, user_id: str, session_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ? AND user_id = ?",
                (utc_now(), session_id, user_id),
            )

    def list_users(self) -> list[dict[str, Any]]:
        cutoff = datetime.fromtimestamp(time.time() - TOKEN_TTL_SECONDS, UTC).isoformat(
            timespec="seconds"
        )
        with self._connect() as db:
            rows = db.execute(
                """SELECT u.id, u.name, u.email, u.status, u.license_hint,
                          u.max_devices, u.created_at, u.expires_at,
                          COUNT(DISTINCT d.id) AS device_count,
                          MAX(d.last_seen_at) AS last_seen_at,
                          COUNT(DISTINCT CASE WHEN s.ended_at IS NULL
                            AND s.last_seen_at >= ? THEN s.id END) AS live_sessions
                   FROM users u
                   LEFT JOIN devices d ON d.user_id = u.id
                   LEFT JOIN sessions s ON s.user_id = u.id
                   GROUP BY u.id ORDER BY u.created_at DESC""",
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_live_sessions(self) -> list[dict[str, Any]]:
        cutoff = datetime.fromtimestamp(time.time() - TOKEN_TTL_SECONDS, UTC).isoformat(
            timespec="seconds"
        )
        with self._connect() as db:
            rows = db.execute(
                """SELECT s.id, s.mode, s.source_language, s.target_language,
                          s.input_device, s.output_device, s.started_at,
                          s.last_seen_at, u.name, u.email, d.label AS device_label
                   FROM sessions s
                   JOIN users u ON u.id = s.user_id
                   JOIN devices d ON d.id = s.device_id
                   WHERE s.ended_at IS NULL AND s.last_seen_at >= ?
                   ORDER BY s.started_at DESC""",
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, int]:
        cutoff = datetime.fromtimestamp(time.time() - TOKEN_TTL_SECONDS, UTC).isoformat(
            timespec="seconds"
        )
        with self._connect() as db:
            return {
                "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "active_users": db.execute(
                    "SELECT COUNT(*) FROM users WHERE status = 'active'"
                ).fetchone()[0],
                "devices": db.execute("SELECT COUNT(*) FROM devices").fetchone()[0],
                "online_devices": db.execute(
                    "SELECT COUNT(*) FROM devices WHERE last_seen_at >= ?", (cutoff,)
                ).fetchone()[0],
                "live_sessions": db.execute(
                    """SELECT COUNT(*) FROM sessions
                       WHERE ended_at IS NULL AND last_seen_at >= ?""",
                    (cutoff,),
                ).fetchone()[0],
            }


@dataclass(frozen=True)
class ControlConfig:
    admin_token: str
    signer: TokenSigner
    store: ControlStore

    @property
    def admin_cookie(self) -> str:
        return hmac.new(
            self.signer.secret, self.admin_token.encode(), hashlib.sha256
        ).hexdigest()


LOGIN_HTML = """<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Charon Control</title>
<style>html{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#07111f;color:#eef6ff;font:15px system-ui}.box{width:min(420px,calc(100% - 32px));padding:30px;border:1px solid #263b54;border-radius:18px;background:#0d1b2d;box-shadow:0 28px 80px #0008}b{letter-spacing:.12em}p{color:#91a6bd;line-height:1.5}input,button{width:100%;border:0;border-radius:10px;padding:13px;font:inherit}input{background:#14263c;color:white;border:1px solid #314963;margin:12px 0}button{background:#37d39a;color:#052016;font-weight:800;cursor:pointer}</style></head>
<body><form class="box" method="post" action="/admin/login"><b>CHARON CONTROL</b><p>Product boshqaruv paneliga kirish uchun admin tokenni kiriting.</p><input name="token" type="password" autocomplete="current-password" placeholder="Admin token" required><button>Kirish</button></form></body></html>"""


ADMIN_HTML = """<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Charon Control</title>
<style>
:root{color-scheme:dark;--ink:#07111f;--panel:#0d1b2d;--line:#243b55;--muted:#8ea5bd;--text:#edf7ff;--mint:#37d39a;--red:#ff6673;--amber:#ffc45e}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 75% -20%,#173755 0,transparent 38%),var(--ink);color:var(--text);font:14px ui-sans-serif,system-ui;min-height:100vh}.shell{max-width:1180px;margin:auto;padding:32px 24px 60px}header{display:flex;align-items:end;justify-content:space-between;margin-bottom:24px}h1{font-size:22px;letter-spacing:.12em;margin:0}header p{margin:7px 0 0;color:var(--muted)}.rail{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#0a1727;margin-bottom:18px}.metric{padding:18px 20px;border-right:1px solid var(--line)}.metric:last-child{border:0}.metric strong{display:block;font:700 28px ui-monospace,SFMono-Regular,monospace}.metric span{font-size:11px;letter-spacing:.1em;color:var(--muted)}.grid{display:grid;grid-template-columns:300px 1fr;gap:18px}.card{background:color-mix(in srgb,var(--panel) 94%,transparent);border:1px solid var(--line);border-radius:16px;padding:20px}h2{font-size:13px;letter-spacing:.08em;margin:0 0 16px;color:#bdd0e3}label{display:block;color:var(--muted);font-size:12px;margin:12px 0 6px}input,button{font:inherit}input{width:100%;padding:11px 12px;background:#10243a;color:white;border:1px solid #314b65;border-radius:9px;outline:none}input:focus{border-color:var(--mint);box-shadow:0 0 0 3px #37d39a22}button{border:0;border-radius:8px;padding:10px 13px;font-weight:750;cursor:pointer}.primary{width:100%;margin-top:16px;background:var(--mint);color:#042319}.ghost{background:#18304a;color:#d9e8f5}.danger{background:#3b1f2a;color:#ffabb1}.key{display:none;margin-top:14px;padding:12px;border:1px dashed var(--mint);border-radius:10px;background:#08251f;color:#9affda;overflow-wrap:anywhere}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th{text-align:left;font-size:10px;letter-spacing:.1em;color:var(--muted);padding:0 10px 12px}td{padding:13px 10px;border-top:1px solid #1d334a;white-space:nowrap}.person strong,.person small{display:block}.person small{color:var(--muted);margin-top:3px}.pill{padding:5px 8px;border-radius:999px;font-size:11px;background:#12382e;color:#6ff0bd}.pill.off{background:#3b1f2a;color:#ff9ba4}.empty{color:var(--muted);padding:38px;text-align:center}.sessions-card{margin-top:18px}.route{max-width:310px;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}.toast{position:fixed;right:24px;bottom:24px;padding:12px 16px;border-radius:10px;background:#eaf7ff;color:#07111f;box-shadow:0 18px 60px #0008;display:none}@media(max-width:780px){.rail{grid-template-columns:1fr 1fr}.metric:nth-child(2){border-right:0}.grid{grid-template-columns:1fr}header{align-items:start;flex-direction:column;gap:10px}}</style></head>
<body><main class="shell"><header><div><h1>CHARON CONTROL</h1><p>Users, qurilmalar va jonli tarjima sessiyalari.</p></div><button class="ghost" onclick="loadData()">Yangilash</button></header><section class="rail"><div class="metric"><strong id="active">—</strong><span>ACTIVE USERS</span></div><div class="metric"><strong id="online">—</strong><span>ONLINE DEVICES</span></div><div class="metric"><strong id="live">—</strong><span>LIVE SESSIONS</span></div><div class="metric"><strong id="devices">—</strong><span>ALL DEVICES</span></div></section><div class="grid"><section class="card"><h2>YANGI FOYDALANUVCHI</h2><form id="create"><label>Ism yoki kompaniya</label><input id="name" required placeholder="Acme Team"><label>Email</label><input id="email" type="email" required placeholder="owner@company.com"><label>Device limiti</label><input id="limit" type="number" value="1" min="1" max="20"><button class="primary">Litsenziya yaratish</button></form><div class="key" id="key"></div></section><section class="card table-wrap"><h2>FOYDALANUVCHILAR</h2><table><thead><tr><th>User</th><th>Status</th><th>Devices</th><th>Live</th><th>Oxirgi aloqa</th><th></th></tr></thead><tbody id="users"></tbody></table><div class="empty" id="empty">Hali foydalanuvchi yo‘q.</div></section></div><section class="card table-wrap sessions-card"><h2>JONLI SESSIYALAR</h2><table><thead><tr><th>User / device</th><th>Rejim</th><th>Til juftligi</th><th>Audio yo‘li</th><th>Boshlangan</th><th>Oxirgi aloqa</th></tr></thead><tbody id="sessions"></tbody></table><div class="empty" id="sessionsEmpty">Hozir jonli tarjima sessiyasi yo‘q.</div></section></main><div class="toast" id="toast"></div>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const request=async(url,opt={})=>{const r=await fetch(url,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{})}});const d=await r.json();if(!r.ok)throw new Error(d.error||'So‘rov bajarilmadi');return d};
function toast(s){const e=document.querySelector('#toast');e.textContent=s;e.style.display='block';setTimeout(()=>e.style.display='none',2600)}
const languageName={auto:'Avtomatik',en:'English',uz:'O‘zbekcha',ru:'Русский',es:'Español'};
const modeName={incoming:'Meetingni eshitish',outgoing:'Zoom’ga gapirish'};
async function loadData(){try{const [s,u,l]=await Promise.all([request('/api/admin/stats'),request('/api/admin/users'),request('/api/admin/sessions')]);active.textContent=s.active_users;online.textContent=s.online_devices;live.textContent=s.live_sessions;devices.textContent=s.devices;empty.style.display=u.users.length?'none':'block';users.innerHTML=u.users.map(x=>`<tr><td class="person"><strong>${esc(x.name)}</strong><small>${esc(x.email)} · …${esc(x.license_hint)}</small></td><td><span class="pill ${x.status==='active'?'':'off'}">${esc(x.status)}</span></td><td>${x.device_count}/${x.max_devices}</td><td>${x.live_sessions}</td><td>${esc(x.last_seen_at||'—')}</td><td><button class="${x.status==='active'?'danger':'ghost'}" onclick="statusUser('${x.id}','${x.status==='active'?'revoked':'active'}')">${x.status==='active'?'O‘chirish':'Yoqish'}</button></td></tr>`).join('');sessionsEmpty.style.display=l.sessions.length?'none':'block';sessions.innerHTML=l.sessions.map(x=>`<tr><td class="person"><strong>${esc(x.name)}</strong><small>${esc(x.device_label)}</small></td><td>${esc(modeName[x.mode]||x.mode)}</td><td><span class="pill">${esc(languageName[x.source_language]||x.source_language)} → ${esc(languageName[x.target_language]||x.target_language)}</span></td><td class="route" title="${esc(x.input_device)} → ${esc(x.output_device)}">${esc(x.input_device)} → ${esc(x.output_device)}</td><td>${esc(x.started_at)}</td><td>${esc(x.last_seen_at)}</td></tr>`).join('')}catch(e){toast(e.message)}}
async function statusUser(id,status){try{await request(`/api/admin/users/${id}/status`,{method:'POST',body:JSON.stringify({status})});toast(status==='active'?'User yoqildi':'User o‘chirildi');loadData()}catch(e){toast(e.message)}}
create.addEventListener('submit',async e=>{e.preventDefault();try{const d=await request('/api/admin/users',{method:'POST',body:JSON.stringify({name:name.value,email:email.value,max_devices:Number(limit.value)})});key.style.display='block';key.textContent='Faqat bir marta ko‘rsatiladi: '+d.license_key;create.reset();limit.value=1;toast('Litsenziya yaratildi');loadData()}catch(e){toast(e.message)}});loadData();setInterval(loadData,30000);
</script></body></html>"""


def handler_factory(config: ControlConfig):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CharonControl/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def _headers(self, status: int, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'",
            )
            self.end_headers()

        def _send(self, data: bytes, content_type: str, status: int = 200) -> None:
            self._headers(status, content_type, len(data))
            self.wfile.write(data)

        def _json(self, value: Any, status: int = 200) -> None:
            self._send(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
                "application/json; charset=utf-8",
                status,
            )

        def _read_body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ControlError("Content-Length noto‘g‘ri") from error
            if length > MAX_BODY_BYTES:
                raise ControlError("So‘rov juda katta", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return self.rfile.read(length)

        def _read_json(self) -> dict[str, Any]:
            try:
                value = json.loads(self._read_body() or b"{}")
                if not isinstance(value, dict):
                    raise ValueError
                return value
            except Exception as error:
                raise ControlError("JSON noto‘g‘ri") from error

        def _admin_ok(self) -> bool:
            supplied = self.headers.get("X-Admin-Token", "")
            if supplied and hmac.compare_digest(supplied, config.admin_token):
                return True
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            item = cookie.get("charon_admin")
            return bool(item and hmac.compare_digest(item.value, config.admin_cookie))

        def _require_admin(self) -> None:
            if not self._admin_ok():
                raise ControlError("Admin ruxsati kerak", HTTPStatus.UNAUTHORIZED)

        def _bearer(self) -> dict[str, Any]:
            value = self.headers.get("Authorization", "")
            if not value.startswith("Bearer "):
                raise ControlError("Session token kerak", HTTPStatus.UNAUTHORIZED)
            return config.signer.verify(value[7:])

        def do_GET(self) -> None:  # noqa: N802
            try:
                path = urllib.parse.urlparse(self.path).path
                if path == "/health":
                    self._json({"ok": True})
                elif path == "/":
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header("Location", "/admin")
                    self.end_headers()
                elif path == "/admin":
                    page = ADMIN_HTML if self._admin_ok() else LOGIN_HTML
                    self._send(page.encode(), "text/html; charset=utf-8")
                elif path == "/api/admin/stats":
                    self._require_admin()
                    self._json(config.store.stats())
                elif path == "/api/admin/users":
                    self._require_admin()
                    self._json({"users": config.store.list_users()})
                elif path == "/api/admin/sessions":
                    self._require_admin()
                    self._json({"sessions": config.store.list_live_sessions()})
                else:
                    raise ControlError("Endpoint topilmadi", HTTPStatus.NOT_FOUND)
            except ControlError as error:
                self._json({"error": str(error)}, error.status)
            except Exception:
                self._json({"error": "Server ichki xatosi"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:  # noqa: N802
            try:
                path = urllib.parse.urlparse(self.path).path
                if path == "/admin/login":
                    form = urllib.parse.parse_qs(self._read_body().decode())
                    token = form.get("token", [""])[0]
                    if not hmac.compare_digest(token, config.admin_token):
                        raise ControlError("Admin token noto‘g‘ri", HTTPStatus.UNAUTHORIZED)
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header("Location", "/admin")
                    self.send_header(
                        "Set-Cookie",
                        f"charon_admin={config.admin_cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800"
                        + (
                            "; Secure"
                            if self.headers.get("X-Forwarded-Proto", "").casefold() == "https"
                            else ""
                        ),
                    )
                    self.end_headers()
                    return
                if path == "/api/v1/activate":
                    data = self._read_json()
                    user = config.store.activate(
                        str(data.get("license_key", "")),
                        str(data.get("device_id", "")),
                        str(data.get("device_label", "Unknown device")),
                        str(data.get("platform", "unknown")),
                        str(data.get("app_version", "unknown")),
                    )
                    token = config.signer.issue(user["user_id"], str(data["device_id"]))
                    self._json({"access_token": token, "expires_in": TOKEN_TTL_SECONDS, "user": user})
                    return
                if path == "/api/v1/heartbeat":
                    claims = self._bearer()
                    data = self._read_json()
                    config.store.heartbeat(
                        claims["uid"], claims["did"], str(data.get("app_version", ""))
                    )
                    self._json(
                        {
                            "active": True,
                            "access_token": config.signer.issue(claims["uid"], claims["did"]),
                            "expires_in": TOKEN_TTL_SECONDS,
                        }
                    )
                    return
                if path == "/api/v1/sessions/start":
                    claims = self._bearer()
                    data = self._read_json()
                    session_id = config.store.start_session(
                        claims["uid"],
                        claims["did"],
                        str(data.get("target_language", "")),
                        str(data.get("input_device", "")),
                        str(data.get("output_device", "")),
                        str(data.get("source_language", "auto")),
                        str(data.get("mode", "incoming")),
                    )
                    self._json({"session_id": session_id})
                    return
                if path == "/api/v1/sessions/end":
                    claims = self._bearer()
                    data = self._read_json()
                    config.store.end_session(claims["uid"], str(data.get("session_id", "")))
                    self._json({"ok": True})
                    return
                if path == "/api/admin/users":
                    self._require_admin()
                    data = self._read_json()
                    created = config.store.create_user(
                        str(data.get("name", "")),
                        str(data.get("email", "")),
                        int(data.get("max_devices", 1)),
                        str(data.get("expires_at", "")) or None,
                    )
                    self._json(created, HTTPStatus.CREATED)
                    return
                if path.startswith("/api/admin/users/") and path.endswith("/status"):
                    self._require_admin()
                    user_id = path.removeprefix("/api/admin/users/").removesuffix("/status")
                    config.store.set_user_status(user_id, str(self._read_json().get("status", "")))
                    self._json({"ok": True})
                    return
                raise ControlError("Endpoint topilmadi", HTTPStatus.NOT_FOUND)
            except ControlError as error:
                self._json({"error": str(error)}, error.status)
            except (TypeError, ValueError):
                self._json({"error": "So‘rov qiymatlari noto‘g‘ri"}, HTTPStatus.BAD_REQUEST)
            except Exception:
                self._json({"error": "Server ichki xatosi"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    return Handler


def _load_or_create(path: Path, length: int = 32) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(length)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return value


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Live Translator admin/control server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--data-dir", type=Path, default=Path.home() / ".live-translator-control"
    )
    args = parser.parse_args()
    explicit_admin = os.getenv("CONTROL_ADMIN_TOKEN", "").strip()
    explicit_secret = os.getenv("CONTROL_SIGNING_SECRET", "").strip()
    if args.host not in {"127.0.0.1", "::1", "localhost"} and (
        not explicit_admin or not explicit_secret
    ):
        raise SystemExit(
            "Public bind uchun CONTROL_ADMIN_TOKEN va CONTROL_SIGNING_SECRET majburiy."
        )
    admin_token = explicit_admin or _load_or_create(args.data_dir / "admin-token")
    signing_secret = explicit_secret or _load_or_create(args.data_dir / "signing-secret", 48)
    store = ControlStore(args.data_dir / "control.sqlite3", signing_secret)
    config = ControlConfig(admin_token, TokenSigner(signing_secret), store)
    server = ThreadingHTTPServer((args.host, args.port), handler_factory(config))
    print(f"Charon Control: http://{args.host}:{args.port}/admin")
    if not explicit_admin and args.host in {"127.0.0.1", "::1", "localhost"}:
        print(f"Admin token: {admin_token}")
    else:
        print("Admin token environment orqali yuklandi; logga chiqarilmadi.")
    print("Remote productionda HTTPS reverse proxy ishlating.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
