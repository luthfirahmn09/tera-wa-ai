"""Log percakapan untuk dashboard monitor (owner lihat orang chat apa).

Disimpan di Redis list `chatlog` (terbaru di depan), dibatasi _MAX entri.
Beda dengan memory.py (konteks per-user utk LLM), ini log global lintas user
untuk ditampilkan di halaman /ui/monitor.html.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache

import redis

from config import REDIS_URL

_KEY = "chatlog"
_MAX = 500


@lru_cache(maxsize=1)
def _r() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def log(user: str, text: str, reply: str) -> None:
    entry = json.dumps(
        {"ts": time.time(), "user": user, "text": text, "reply": reply}
    )
    r = _r()
    r.lpush(_KEY, entry)
    r.ltrim(_KEY, 0, _MAX - 1)


def recent(limit: int = 200) -> list[dict]:
    raw = _r().lrange(_KEY, 0, max(0, limit - 1))
    return [json.loads(x) for x in raw]
