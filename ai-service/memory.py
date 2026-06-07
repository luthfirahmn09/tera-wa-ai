"""Short-term memory percakapan per-user di Redis.

Tujuannya CUMA satu: bantu klasifikasi intent memahami pesan lanjutan
(mis. "yang lebih murah" = lanjutan pencarian sebelumnya). History tidak
dipakai untuk menyusun balasan, jadi anti-halu (grounding/template) tetap utuh.

Key Redis:
  history:<user> -> LIST(JSON) {role: "user"|"bot", text: ...}
                    disimpan maksimal _MAX_TURNS terakhir, TTL _TTL detik.
"""
from __future__ import annotations

import json
from functools import lru_cache

import redis

from config import REDIS_URL

_MAX_TURNS = 6      # total pesan (user+bot) yang disimpan
_TTL = 1800         # 30 menit
_BOT_SNIPPET = 160  # potong balasan bot saat disimpan (hemat token)


@lru_cache(maxsize=1)
def _r() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def _key(user: str) -> str:
    return f"history:{user}"


def get_history(user: str) -> list[dict]:
    raw = _r().lrange(_key(user), 0, -1)
    return [json.loads(x) for x in raw]


def append(user: str, role: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    if role == "bot":
        text = text[:_BOT_SNIPPET]
    r = _r()
    key = _key(user)
    r.rpush(key, json.dumps({"role": role, "text": text}))
    r.ltrim(key, -_MAX_TURNS, -1)  # simpan N terakhir saja
    r.expire(key, _TTL)
