"""Client untuk API produk toko (shop.teknonusantara.net).

Response API:
{
  "success": true, "message": "OK",
  "data": [ {id, nama, kategori, harga, stok, deskripsi, link, updated_at}, ... ],
  "pagination": { page, per_page, total_pages, has_next, next_page, ... }
}

fetch_all_products() me-loop semua halaman (ikut has_next) lalu mengembalikan
list produk yang sudah dinormalisasi.
"""
from __future__ import annotations

import requests

from config import (
    PRODUCTS_API_MAX_PAGES,
    PRODUCTS_API_PER_PAGE,
    PRODUCTS_API_TIMEOUT,
    PRODUCTS_API_URL,
)

def _variants(raw: dict) -> dict:
    """Normalisasi variants -> {sizes: [str], colors: [str]}.

    API memberi colors sebagai list objek {hex, name}; kita ambil name saja
    untuk ditampilkan ke user. sizes bisa kosong (mis. tas).
    """
    v = raw.get("variants") or {}
    sizes = [str(s).strip() for s in (v.get("sizes") or []) if str(s).strip()]
    colors = []
    for c in v.get("colors") or []:
        if isinstance(c, dict):
            name = str(c.get("name", "")).strip()
        else:
            name = str(c).strip()
        if name:
            colors.append(name)
    return {"sizes": sizes, "colors": colors}


def _normalize(raw: dict) -> dict | None:
    """Ambil field yang dibutuhkan + paksa tipe. None jika data tak valid."""
    if not raw.get("id") or not raw.get("nama"):
        return None
    return {
        "id": str(raw["id"]),
        "nama": str(raw.get("nama", "")).strip(),
        "kategori": str(raw.get("kategori", "")).strip().lower(),
        "harga": int(raw.get("harga") or 0),
        "stok": int(raw.get("stok") or 0),
        "deskripsi": str(raw.get("deskripsi", "")).strip(),
        "link": str(raw.get("link", "")).strip(),
        "variants": _variants(raw),
        "updated_at": str(raw.get("updated_at", "")),
    }


def fetch_all_products() -> list[dict]:
    """Ambil seluruh produk dari API (semua halaman)."""
    products: list[dict] = []
    page = 1

    while page <= PRODUCTS_API_MAX_PAGES:
        resp = requests.get(
            PRODUCTS_API_URL,
            params={"per_page": PRODUCTS_API_PER_PAGE, "page": page},
            headers={"Accept": "application/json"},
            timeout=PRODUCTS_API_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()

        if not body.get("success"):
            raise RuntimeError(f"API gagal: {body.get('message')}")

        for raw in body.get("data", []):
            item = _normalize(raw)
            if item:
                products.append(item)

        pg = body.get("pagination") or {}
        if not pg.get("has_next"):
            break
        page = pg.get("next_page") or (page + 1)

    return products
