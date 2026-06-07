"""Sinkronisasi produk: API toko -> Redis (katalog) -> Qdrant (vector).

Dipakai:
  - sekali saat container start (lihat entrypoint.sh),
  - berkala oleh scheduler internal (lihat scheduler.py),
  - manual via endpoint POST /admin/sync.

Jalan manual:  python sync.py
"""
from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

import catalog
from api_client import fetch_all_products
from config import EMBEDDING_DIM, QDRANT_COLLECTION, QDRANT_URL
from embeddings import embed_documents


def _product_text(p: dict) -> str:
    """Teks yang di-embed untuk pencarian semantik (termasuk warna varian
    supaya query seperti 'kaos hitam' tetap ketemu)."""
    base = f"{p['nama']}. Kategori: {p.get('kategori', '')}. {p.get('deskripsi', '')}"
    colors = (p.get("variants") or {}).get("colors") or []
    if colors:
        base += " Warna: " + ", ".join(colors) + "."
    return base


def _index_to_qdrant(products: list[dict]) -> None:
    # hitung embedding DULU (bagian lambat) sebelum menyentuh collection,
    # supaya jeda 'collection kosong' saat recreate sekecil mungkin.
    vectors = embed_documents([_product_text(p) for p in products])

    client = QdrantClient(url=QDRANT_URL)
    client.recreate_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )
    points = [
        PointStruct(id=i, vector=vec, payload=p)
        for i, (p, vec) in enumerate(zip(products, vectors))
    ]
    client.upsert(collection_name=QDRANT_COLLECTION, points=points)


def run_sync() -> int:
    """Tarik produk dari API, simpan ke Redis & Qdrant. Return jumlah produk."""
    products = fetch_all_products()
    if not products:
        print("[sync] API tidak mengembalikan produk, dilewati.")
        return 0

    catalog.save_products(products)
    _index_to_qdrant(products)
    print(f"[sync] {len(products)} produk tersinkron (Redis + Qdrant). ✅")
    return len(products)


if __name__ == "__main__":
    run_sync()
