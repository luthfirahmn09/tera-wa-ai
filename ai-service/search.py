"""Pencarian produk: hybrid keyword + semantik.

Kenapa hybrid: dense-search (Qdrant) lemah untuk query 1 kata (mis. "kemeja"
sering tak menemukan produk bernama "Kemeja ..."). Maka:
  1. keyword: produk yang NAMA/deskripsinya mengandung kata kunci (presisi tinggi),
  2. semantik: Qdrant untuk maksud yang tidak match kata persis,
lalu digabung (keyword dulu) + filter stok/harga/kategori.
"""
from __future__ import annotations

import re
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

from catalog import all_products
from config import QDRANT_COLLECTION, QDRANT_URL, TOP_K
from embeddings import embed_query


@lru_cache(maxsize=1)
def _client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)


def _build_filter(kategori: str | None, max_harga: int | None) -> Filter:
    # selalu sembunyikan produk yang stoknya habis
    must = [FieldCondition(key="stok", range=Range(gte=1))]
    if kategori:
        must.append(
            FieldCondition(key="kategori", match=MatchValue(value=kategori.lower()))
        )
    if max_harga:
        must.append(FieldCondition(key="harga", range=Range(lte=max_harga)))
    return Filter(must=must)


def _semantic(query: str, kategori, max_harga, top_k: int) -> list[dict]:
    qvec = embed_query(query)
    hits = _client().search(
        collection_name=QDRANT_COLLECTION,
        query_vector=qvec,
        query_filter=_build_filter(kategori, max_harga),
        limit=top_k,
        with_payload=True,
    )
    out = []
    for h in hits:
        item = dict(h.payload)
        item["score"] = round(h.score, 4)
        out.append(item)
    return out


# kata umum non-produk: tidak boleh dipakai sebagai keyword (bikin kandidat ngawur)
_STOPWORDS = {
    "yang", "yg", "dan", "atau", "ada", "apa", "aja", "saja", "dong", "nih", "sih",
    "kak", "min", "cari", "cariin", "carikan", "mau", "pengen", "pingin", "tolong",
    "minta", "lihat", "liat", "punya", "dengan", "buat", "untuk", "harga", "harganya",
    "berapa", "produk", "product", "barang", "item", "kategori", "semua",
    "murah", "termurah", "murahnya", "paling", "mahal", "termahal", "mahalnya",
    "ribu", "juta", "rupiah", "dibawah", "kurang", "lebih", "dari", "ke", "ya",
    # sinonim/gaul harga (jangan jadi keyword)
    "cheap", "low", "murce", "murmer", "hemat", "expensive", "price", "budget",
}


def _tokens(query: str) -> list[str]:
    # kata >= 3 huruf, buang stopword & angka biar keyword cuma kata produk
    return [
        t
        for t in re.findall(r"[a-z]+", query.lower())
        if len(t) >= 3 and t not in _STOPWORDS
    ]


def has_keywords(query: str) -> bool:
    """True kalau query punya kata kunci produk yang bermakna (bukan cuma stopword).
    Dipakai untuk membedakan 'cari kaos' (search) vs 'ada produk apa aja' (browse).
    """
    return bool(_tokens(query or ""))


def list_all(
    kategori: str | None = None,
    max_harga: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """Daftar semua produk yang lolos filter (stok>0 + kategori/harga).
    Untuk 'lihat semua produk' / browse, bukan pencarian kata kunci."""
    items = [p for p in all_products() if _passes(p, kategori, max_harga)]
    items.sort(key=lambda p: (p.get("kategori", ""), p.get("nama", "")))
    return items[:limit]


def _passes(p: dict, kategori, max_harga) -> bool:
    if int(p.get("stok", 0)) < 1:
        return False
    if kategori and p.get("kategori", "").lower() != kategori.lower():
        return False
    if max_harga and int(p.get("harga", 0)) > max_harga:
        return False
    return True


def _keyword(query: str, kategori, max_harga) -> list[dict]:
    toks = _tokens(query)
    if not toks:
        return []
    by_name, by_desc = [], []
    for p in all_products():
        if not _passes(p, kategori, max_harga):
            continue
        name = p.get("nama", "").lower()
        desc = p.get("deskripsi", "").lower()
        colors = " ".join((p.get("variants") or {}).get("colors") or []).lower()
        if any(t in name for t in toks):
            by_name.append(p)
        elif any(t in desc or t in colors for t in toks):
            by_desc.append(p)
    return by_name + by_desc  # cocok nama lebih relevan dari cocok deskripsi


def search_products(
    query: str,
    kategori: str | None = None,
    max_harga: int | None = None,
    top_k: int = TOP_K,
) -> list[dict]:
    """Hybrid: keyword (cocok nama/deskripsi) didahulukan, lalu semantik."""
    keyword = _keyword(query, kategori, max_harga)
    semantic = _semantic(query, kategori, max_harga, top_k)

    merged: list[dict] = []
    seen: set[str] = set()
    for p in keyword + semantic:
        pid = p.get("id")
        if pid in seen:
            continue
        seen.add(pid)
        merged.append(p)
        if len(merged) >= top_k:
            break
    return merged


# ambang skor semantik agar kandidat sort tetap relevan.
# kalibrasi (MiniLM): item relevan skor >0.6, item tak relevan <0.4 -> 0.45 memisah bersih.
_SORT_SEM_THRESHOLD = 0.45


def search_ranked(
    query: str,
    kategori: str | None = None,
    max_harga: int | None = None,
    limit: int = 30,
) -> list[dict]:
    """Daftar produk RELEVAN terurut (keyword dulu, lalu semantik berskor tinggi).

    Dipakai untuk paginasi 'lanjut': beda dengan search_products yang langsung
    dipotong TOP_K, ini mengumpulkan lebih banyak hasil tapi tetap relevan
    (semantik di-saring ambang skor saat tanpa kategori).
    """
    out = list(_keyword(query, kategori, max_harga))
    seen = {p["id"] for p in out}
    for p in _semantic(query, kategori, max_harga, top_k=limit):
        if p["id"] in seen:
            continue
        # kategori sudah menjamin relevansi; tanpa kategori, andalkan ambang skor
        if kategori is None and p["score"] < _SORT_SEM_THRESHOLD:
            continue
        seen.add(p["id"])
        out.append(p)
    return out[:limit]


def search_by_price(
    query: str,
    kategori: str | None = None,
    max_harga: int | None = None,
    ascending: bool = True,
    top_k: int = TOP_K,
) -> list[dict]:
    """Untuk query 'termurah/termahal': urutkan kandidat RELEVAN berdasarkan harga.

    - Tanpa jenis (mis. 'yang termurah') -> seluruh katalog yang lolos filter.
    - Dengan jenis (mis. 'sepatu termurah') -> hanya produk relevan:
      cocok keyword + hit semantik berskor tinggi (>= ambang). Ini mencegah
      produk murah yang TIDAK relevan ikut terurut ke atas (halu).
    """
    toks = _tokens(query or "")
    if not toks:
        cands = [p for p in all_products() if _passes(p, kategori, max_harga)]
    else:
        cands = list(_keyword(query, kategori, max_harga))
        seen = {p["id"] for p in cands}
        for p in _semantic(query, kategori, max_harga, top_k=12):
            if p["score"] >= _SORT_SEM_THRESHOLD and p["id"] not in seen:
                seen.add(p["id"])
                cands.append(p)

    cands = sorted(cands, key=lambda p: int(p.get("harga", 0)), reverse=not ascending)
    return cands[:top_k]
