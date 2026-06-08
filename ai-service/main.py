"""FastAPI service: terima pesan dari WA gateway, balas hasil AI."""
from __future__ import annotations

import base64
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import chatlog
import scheduler
from agent import handle_message
from config import (
    ADMIN_TOKEN,
    get_groq_api_key,
    is_voice_enabled,
    set_groq_api_key,
    set_voice_enabled,
)
from stt import transcribe
from sync import run_sync

app = FastAPI(title="WA AI Order - AI Service")


@app.on_event("startup")
def _on_startup() -> None:
    # initial sync sudah dijalankan entrypoint; di sini cukup nyalakan scheduler
    scheduler.start()

# izinkan halaman test (file:// atau origin lain) memanggil API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    from_: str | None = None  # nomor pengirim (opsional)
    text: str = ""
    audio_base64: str | None = None  # voice note (OGG) -> ditranskrip dulu
    quoted: str | None = None  # teks pesan yang di-reply user (kalau ada)


class GroqKeyRequest(BaseModel):
    api_key: str  # kosong = hapus override, balik ke nilai .env


def _require_admin(token: str) -> None:
    """Validasi token admin; 404 kalau fitur admin dimatikan, 401 kalau salah."""
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=404, detail="admin endpoint disabled")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")


def _mask(secret: str) -> str:
    """Tampilkan key tersamar, mis. 'gsk_...4f2a'."""
    if not secret:
        return ""
    return f"{secret[:4]}...{secret[-4:]}" if len(secret) > 8 else "****"


class ChatResponse(BaseModel):
    reply: str  # gabungan (kompat lama)
    messages: list[str]  # daftar bubble untuk dikirim terpisah
    transcript: str | None = None  # hasil transkrip kalau input voice note


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    user = req.from_ or "anon"
    transcript = None
    text = req.text

    # voice note -> transkrip dulu (kalau fitur voice aktif)
    if req.audio_base64:
        if not is_voice_enabled():
            msg = "Maaf, untuk sekarang aku cuma bisa baca teks ya 🙏 ketik aja pesananmu."
            return ChatResponse(reply=msg, messages=[msg], transcript=None)
        try:
            audio = base64.b64decode(req.audio_base64)
            text = transcribe(audio)
            transcript = text
        except Exception as exc:
            print(f"[chat] transkrip gagal: {exc}")
            msg = "Maaf, suaranya kurang kebaca 🙏 coba ketik aja ya."
            return ChatResponse(reply=msg, messages=[msg], transcript=None)

    messages = handle_message(text, user=user, quoted=req.quoted or "")
    reply = "\n\n".join(messages)
    try:
        logged = f"🎙️ {transcript}" if transcript else req.text
        chatlog.log(user, logged, reply)
    except Exception as exc:  # log tidak boleh ganggu balasan
        print(f"[chatlog] gagal simpan: {exc}")
    return ChatResponse(reply=reply, messages=messages, transcript=transcript)


@app.get("/admin/config/voice")
def admin_get_voice(x_admin_token: str = Header(default="")) -> dict:
    _require_admin(x_admin_token)
    return {"voice_enabled": is_voice_enabled()}


@app.post("/admin/config/voice")
def admin_set_voice(
    enabled: bool, x_admin_token: str = Header(default="")
) -> dict:
    """Aktif/non-aktifkan fitur voice note (runtime, tanpa restart)."""
    _require_admin(x_admin_token)
    set_voice_enabled(enabled)
    return {"voice_enabled": is_voice_enabled()}


@app.get("/admin/chats")
def admin_chats(limit: int = 200, x_admin_token: str = Header(default="")) -> dict:
    """Riwayat chat lintas user untuk dashboard monitor."""
    _require_admin(x_admin_token)
    return {"chats": chatlog.recent(limit)}


@app.post("/admin/sync")
def admin_sync(x_admin_token: str = Header(default="")) -> dict:
    """Trigger sinkron manual (mis. dari cron eksternal).

    Aktif hanya bila ADMIN_TOKEN di-set, dan header X-Admin-Token cocok.
    """
    _require_admin(x_admin_token)
    count = run_sync()
    return {"synced": count}


@app.get("/admin/config/groq-key")
def admin_get_groq_key(x_admin_token: str = Header(default="")) -> dict:
    """Lihat API key Groq yang sedang aktif (tersamar)."""
    _require_admin(x_admin_token)
    return {"groq_api_key": _mask(get_groq_api_key())}


@app.post("/admin/config/groq-key")
def admin_set_groq_key(
    req: GroqKeyRequest, x_admin_token: str = Header(default="")
) -> dict:
    """Set/timpa API key Groq di Redis (langsung kepakai tanpa restart).

    api_key kosong = hapus override, balik ke nilai .env.
    """
    _require_admin(x_admin_token)
    set_groq_api_key(req.api_key)
    return {"ok": True, "groq_api_key": _mask(get_groq_api_key())}


# halaman test chat di http://localhost:8000/ui/
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/ui", StaticFiles(directory=_STATIC_DIR, html=True), name="ui")
