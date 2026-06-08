"""Keranjang belanja per-user, disimpan di Redis.

Struktur key Redis:
  cart:<user>       -> HASH  { product_id: qty }
  last_list:<user>  -> STRING(JSON)  daftar product_id terakhir yang
                       ditampilkan ke user (hasil search / isi keranjang),
                       supaya user bisa rujuk pakai nomor ("simpan no 2").
  search:<user>     -> STRING(JSON)  {ids, offset} hasil pencarian terakhir
                       untuk paginasi ("lanjut" -> tampilkan batch berikutnya).
"""
from __future__ import annotations

import json
from functools import lru_cache

import redis

from catalog import get_product
from config import REDIS_URL

_LIST_TTL = 3600  # detik


@lru_cache(maxsize=1)
def _r() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def _cart_key(user: str) -> str:
    return f"cart:{user}"


def _list_key(user: str) -> str:
    return f"last_list:{user}"


def _search_key(user: str) -> str:
    return f"search:{user}"


# ---- keranjang ----

def add_to_cart(user: str, product_id: str, qty: int = 1) -> None:
    _r().hincrby(_cart_key(user), product_id, qty)


def remove_from_cart(user: str, product_id: str) -> None:
    _r().hdel(_cart_key(user), product_id)


def clear_cart(user: str) -> None:
    _r().delete(_cart_key(user))


def get_cart(user: str) -> list[dict]:
    """Return list {product, qty}, diurutkan stabil berdasarkan product_id."""
    raw = _r().hgetall(_cart_key(user))  # {product_id: qty}
    items = []
    for pid in sorted(raw.keys()):
        product = get_product(pid)
        if product:
            items.append({"product": product, "qty": int(raw[pid])})
    return items


# ---- daftar terakhir yang ditampilkan (untuk rujukan nomor) ----

def set_last_list(user: str, product_ids: list[str]) -> None:
    _r().set(_list_key(user), json.dumps(product_ids), ex=_LIST_TTL)


def get_last_list(user: str) -> list[str]:
    raw = _r().get(_list_key(user))
    return json.loads(raw) if raw else []


# ---- paginasi hasil search ("lanjut") ----

def set_search(user: str, product_ids: list[str]) -> None:
    """Simpan seluruh hasil pencarian (terurut) untuk dipaginasi."""
    _r().set(
        _search_key(user),
        json.dumps({"ids": product_ids, "offset": 0}),
        ex=_LIST_TTL,
    )


def reset_search(user: str) -> bool:
    """Balikin offset paginasi ke 0 (buat 'kirim ulang katalog'). False kalau
    belum ada pencarian tersimpan."""
    raw = _r().get(_search_key(user))
    if not raw:
        return False
    st = json.loads(raw)
    st["offset"] = 0
    _r().set(_search_key(user), json.dumps(st), ex=_LIST_TTL)
    return True


def next_search_page(user: str, page_size: int) -> tuple[list[str], int]:
    """Ambil batch berikutnya dari hasil pencarian tersimpan.

    Return (batch_ids, sisa_setelah_batch). ([], 0) kalau tidak ada / habis.
    """
    raw = _r().get(_search_key(user))
    if not raw:
        return [], 0
    st = json.loads(raw)
    ids, offset = st.get("ids", []), st.get("offset", 0)
    batch = ids[offset : offset + page_size]
    new_offset = offset + len(batch)
    st["offset"] = new_offset
    _r().set(_search_key(user), json.dumps(st), ex=_LIST_TTL)
    return batch, max(0, len(ids) - new_offset)
