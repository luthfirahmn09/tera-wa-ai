"""Katalog produk — sumber kebenaran untuk lookup by id/nama/kategori.

Data disimpan di Redis (key `products:all`, hasil sinkron dari API). Kalau
Redis kosong (mis. sync belum jalan), fallback ke seed dummy products.json.
Dipakai oleh search (ingest), keranjang, dan checkout.
"""
from __future__ import annotations

import json
from functools import lru_cache

import redis

from config import PRODUCTS_FILE, REDIS_URL

_REDIS_KEY = "products:all"


@lru_cache(maxsize=1)
def _r() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


@lru_cache(maxsize=1)
def _seed() -> list[dict]:
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_products(products: list[dict]) -> None:
    """Simpan seluruh produk ke Redis (dipanggil oleh sync)."""
    _r().set(_REDIS_KEY, json.dumps(products))


def all_products() -> list[dict]:
    raw = _r().get(_REDIS_KEY)
    if raw:
        return json.loads(raw)
    return _seed()  # fallback kalau belum pernah sync


def _by_id() -> dict[str, dict]:
    return {p["id"]: p for p in all_products()}


def get_product(product_id: str) -> dict | None:
    return _by_id().get(product_id)


def find_by_name(keyword: str) -> list[dict]:
    """Cari produk yang nama/kategorinya mengandung keyword (substring)."""
    kw = keyword.lower().strip()
    if not kw:
        return []
    return [
        p
        for p in all_products()
        if kw in p["nama"].lower() or kw in p.get("kategori", "").lower()
    ]


def get_categories() -> list[str]:
    """Daftar kategori unik yang ada di katalog (untuk bantu klasifikasi intent)."""
    cats = {p.get("kategori", "").strip().lower() for p in all_products()}
    return sorted(c for c in cats if c)
