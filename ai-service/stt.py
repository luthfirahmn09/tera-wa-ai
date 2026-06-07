"""Speech-to-text untuk voice note WhatsApp, pakai Groq Whisper.

WA voice note formatnya OGG/Opus — Groq Whisper menerima ogg langsung, jadi
tidak perlu konversi. Pakai API key Groq yang sama (runtime-swappable).
"""
from __future__ import annotations

from groq import Groq

from config import STT_MODELS, get_groq_api_key


def transcribe(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transkrip audio -> teks (Bahasa Indonesia). Coba model cadangan bila gagal."""
    client = Groq(api_key=get_groq_api_key())
    last_err: Exception | None = None
    for model in STT_MODELS:
        try:
            resp = client.audio.transcriptions.create(
                model=model,
                file=(filename, audio_bytes),
                language="id",
            )
            return (resp.text or "").strip()
        except Exception as err:  # noqa: BLE001
            last_err = err
            print(f"[stt] '{model}' gagal: {str(err)[:80]}…")
            continue
    raise last_err if last_err else RuntimeError("tidak ada model STT tersedia")
