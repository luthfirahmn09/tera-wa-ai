"""Konfigurasi terpusat, dibaca dari environment variables / .env."""
import os
from functools import lru_cache

import redis
from dotenv import load_dotenv

load_dotenv()

# Groq (LLM)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Model speech-to-text (Whisper di Groq) untuk voice note; urut, fallback.
STT_MODELS = [
    m.strip()
    for m in os.getenv(
        "STT_MODELS", "whisper-large-v3-turbo,whisper-large-v3"
    ).split(",")
    if m.strip()
]

# Model cadangan saat model utama kena rate limit (urut, dicoba dari kiri).
# Default: 8b-instant (kuota besar & murah) lalu gemma2-9b-it.
GROQ_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GROQ_FALLBACK_MODELS", "llama-3.1-8b-instant,gemma2-9b-it"
    ).split(",")
    if m.strip()
]

# Key Redis tempat menyimpan API key Groq yang bisa diubah runtime.
GROQ_KEY_REDIS = "config:groq_api_key"

# Qdrant (vector DB)
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "products")

# Redis (cache / sumber data nanti)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Embedding model (fastembed/ONNX, multilingual - bagus untuk Bahasa Indonesia)
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
# model di atas menghasilkan vector berdimensi 384
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))

# lokasi cache model fastembed (di-set ke volume saat docker; None = default lokal)
EMBEDDING_CACHE_DIR = os.getenv("EMBEDDING_CACHE_DIR") or None

# Berapa banyak produk yang dikembalikan ke user
TOP_K = int(os.getenv("TOP_K", "5"))

# Paginasi hasil search ("lanjut"): jumlah per halaman & total kandidat ditarik
PAGE_SIZE = int(os.getenv("PAGE_SIZE", str(TOP_K)))
SEARCH_LIMIT = int(os.getenv("SEARCH_LIMIT", "30"))

# Branding
STORE_NAME = os.getenv("STORE_NAME", "TokoEko")

# Guard panjang pesan (anti-spam / anti-abuse)
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "500"))   # input user
MAX_OUTPUT_CHARS = int(os.getenv("MAX_OUTPUT_CHARS", "1500"))  # balasan bot

# Lokasi file dummy produk (seed fallback kalau API & Redis kosong)
PRODUCTS_FILE = os.getenv(
    "PRODUCTS_FILE",
    os.path.join(os.path.dirname(__file__), "data", "products.json"),
)

# Sumber data produk: API toko
PRODUCTS_API_URL = os.getenv(
    "PRODUCTS_API_URL", "https://shop.teknonusantara.net/api/products"
)
PRODUCTS_API_PER_PAGE = int(os.getenv("PRODUCTS_API_PER_PAGE", "50"))
PRODUCTS_API_TIMEOUT = int(os.getenv("PRODUCTS_API_TIMEOUT", "20"))
PRODUCTS_API_MAX_PAGES = int(os.getenv("PRODUCTS_API_MAX_PAGES", "100"))  # safety

# Sinkronisasi berkala (menit). 0 = matikan scheduler internal.
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "30"))

# Token sederhana untuk endpoint /admin/sync (kosong = endpoint dimatikan)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


@lru_cache(maxsize=1)
def _config_redis() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def get_groq_api_key() -> str:
    """API key Groq aktif: utamakan nilai di Redis, fallback ke .env.

    Dipanggil tiap butuh LLM, jadi ganti key di Redis langsung kepakai
    tanpa restart (lihat _llm() di agent.py yang cache per-nilai-key).
    """
    try:
        val = _config_redis().get(GROQ_KEY_REDIS)
        if val and val.strip():
            return val.strip()
    except Exception:
        # Redis down -> jangan bikin service mati, pakai .env saja.
        pass
    return GROQ_API_KEY


def set_groq_api_key(value: str) -> None:
    """Simpan/timpa API key Groq di Redis. value kosong = hapus (balik ke .env)."""
    r = _config_redis()
    value = (value or "").strip()
    if value:
        r.set(GROQ_KEY_REDIS, value)
    else:
        r.delete(GROQ_KEY_REDIS)


# Fitur voice note (bisa di-on/off runtime via Redis, default dari env)
VOICE_REDIS = "config:voice_enabled"
VOICE_ENABLED_DEFAULT = os.getenv("VOICE_ENABLED", "true").lower() in ("1", "true", "yes", "on")


def is_voice_enabled() -> bool:
    try:
        val = _config_redis().get(VOICE_REDIS)
        if val is not None:
            return val == "1"
    except Exception:
        pass
    return VOICE_ENABLED_DEFAULT


def set_voice_enabled(enabled: bool) -> None:
    try:
        _config_redis().set(VOICE_REDIS, "1" if enabled else "0")
    except Exception as exc:
        print(f"[config] gagal set voice flag: {exc}")
