"""Live Translator — yordamchi server (loglar + yangilanish).

Nima uchun kerak:
  • LOGLAR: foydalanuvchi ilovani turli kompyuterlarga o'rnatadi (boshliq
    kompyuteri, hamkasblar). Ularda SSH yo'q, log faylni har safar qo'lda
    olib kelish noqulay. Endi ilovadagi bitta tugma logni shu serverga
    yuboradi va biz brauzerdan o'qiymiz.
  • YANGILANISH: ilova ochilganda shu serverdan oxirgi versiyani so'raydi.
    Yangisi bo'lsa foydalanuvchiga aytadi va o'zi yuklab o'rnatadi.

Xavfsizlik: yozish (log yuborish) va o'qish uchun ALOHIDA tokenlar.
  LT_UPLOAD_TOKEN — ilova ichida (log yuborish uchun)
  LT_ADMIN_TOKEN  — faqat bizda (loglarni ko'rish uchun)
Yangilanish manifesti (GET /update) ochiq — unda sir yo'q, faqat versiya
raqami va yuklab olish havolasi.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, Form, Header, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

DATA_DIR = Path(os.getenv("LT_DATA_DIR", "/data"))
UPLOAD_TOKEN = os.getenv("LT_UPLOAD_TOKEN", "")
ADMIN_TOKEN = os.getenv("LT_ADMIN_TOKEN", "")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024   # 8 MB — log fayl uchun yetarli
KEEP_LAST = 300                       # eski yuklamalar shundan ortsa tozalanadi

app = FastAPI(title="Live Translator Support", docs_url=None, redoc_url=None)


def _safe(text: str, limit: int = 60) -> str:
    """Fayl nomiga xavfsiz qism (foydalanuvchi bergan matndan)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "").strip())
    return cleaned[:limit].strip("-") or "nomsiz"


def _uploads_dir() -> Path:
    target = DATA_DIR / "uploads"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _prune() -> None:
    """Disk to'lib ketmasin: eng eski yuklamalarni o'chiramiz."""
    files = sorted(_uploads_dir().glob("*.log"), key=lambda p: p.stat().st_mtime)
    for old in files[:-KEEP_LAST]:
        with __import__("contextlib").suppress(OSError):
            old.unlink()
            old.with_suffix(".json").unlink(missing_ok=True)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "time": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/update")
def update_manifest(channel: str = Query(default="")) -> JSONResponse:
    """Ilova ochilganda shu manzilni so'raydi.

    IKKI OQIM (foydalanuvchi talabi: "monitor uchun alohida qil va
    aralashtirma"):
      • channel=ops → monitorli sinov kompyuteri. Yangi versiyani BIRINCHI
        bo'lib oladi (LT_OPS_VERSION / LT_OPS_URL).
      • oqim ko'rsatilmasa → barqaror oqim (boshliq, hamkasblar):
        LT_LATEST_VERSION / LT_LATEST_URL.
    OPS o'zgaruvchilari bo'sh bo'lsa barqaror oqimga qaytadi — ya'ni
    noto'g'ri sozlashda ham ilova yangilanishsiz qolmaydi.

    Qiymatlar Railway o'zgaruvchilaridan olinadi. DIQQAT: `railway variables
    --set` o'zi YETMAYDI — qiymat faqat xizmat qayta yuklanganda kuchga
    kiradi (`railway redeploy --yes`). 2026-07-28 da shu sababdan barqaror
    oqim eski versiyani ko'rsatib turgan edi."""
    ops = channel.strip().lower() == "ops"
    version = (os.getenv("LT_OPS_VERSION", "") if ops else "") or os.getenv("LT_LATEST_VERSION", "")
    url = (os.getenv("LT_OPS_URL", "") if ops else "") or os.getenv("LT_LATEST_URL", "")
    notes = (os.getenv("LT_OPS_NOTES", "") if ops else "") or os.getenv("LT_LATEST_NOTES", "")
    return JSONResponse(
        {
            "channel": "ops" if ops else "stable",
            "version": version,
            "url": url,
            "notes": notes,
            "mandatory": os.getenv("LT_UPDATE_MANDATORY", "0") == "1",
        }
    )


@app.post("/logs")
async def upload_log(
    file: UploadFile = File(...),
    version: str = Form(""),
    device: str = Form(""),
    note: str = Form(""),
    x_lt_token: str = Header(default=""),
) -> JSONResponse:
    """Ilovadagi «Log yuborish» tugmasi shu yerga yuboradi."""
    if UPLOAD_TOKEN and x_lt_token != UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="token")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="fayl juda katta")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}_{_safe(device)}_{_safe(version, 12)}"
    target = _uploads_dir() / f"{name}.log"
    target.write_bytes(payload)
    (_uploads_dir() / f"{name}.json").write_text(
        json.dumps(
            {
                "version": version,
                "device": device,
                "note": note,
                "bytes": len(payload),
                "received": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _prune()
    return JSONResponse({"ok": True, "id": name})


def _require_admin(token: str) -> None:
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="admin token")


@app.get("/", response_class=HTMLResponse)
def index(token: str = Query(default="")) -> HTMLResponse:
    """Yuklangan loglar ro'yxati (faqat admin token bilan)."""
    _require_admin(token)
    rows = []
    for meta_path in sorted(
        _uploads_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        name = meta_path.stem
        rows.append(
            "<tr>"
            f"<td>{meta.get('received', '')}</td>"
            f"<td>{meta.get('device', '')}</td>"
            f"<td>{meta.get('version', '')}</td>"
            f"<td>{meta.get('bytes', 0) // 1024} KB</td>"
            f"<td>{(meta.get('note') or '')[:80]}</td>"
            f"<td><a href='/logs/{name}?token={token}'>ochish</a></td>"
            "</tr>"
        )
    body = "".join(rows) or "<tr><td colspan=6>Hali log yuborilmagan</td></tr>"
    return HTMLResponse(
        "<html><head><meta charset='utf-8'><title>Live Translator loglari</title>"
        "<style>body{font:14px -apple-system,Segoe UI,sans-serif;margin:24px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;"
        "padding:6px 10px;text-align:left}th{background:#f5f5f7}</style></head>"
        "<body><h2>Live Translator — yuborilgan loglar</h2><table>"
        "<tr><th>Vaqt</th><th>Kompyuter</th><th>Versiya</th><th>Hajm</th>"
        "<th>Izoh</th><th></th></tr>" + body + "</table></body></html>"
    )


@app.get("/logs/{name}", response_class=PlainTextResponse)
def read_log(name: str, token: str = Query(default="")) -> PlainTextResponse:
    _require_admin(token)
    target = _uploads_dir() / f"{_safe(name, 120)}.log"
    if not target.exists():
        raise HTTPException(status_code=404, detail="topilmadi")
    return PlainTextResponse(target.read_text(encoding="utf-8", errors="replace"))
