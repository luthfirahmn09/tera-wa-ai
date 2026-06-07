"""Load dummy produk dari JSON -> embed -> simpan ke Qdrant.

Jalankan sekali (atau tiap data berubah):
    python ingest.py
"""
from __future__ import annotations

import json

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from config import EMBEDDING_DIM, PRODUCTS_FILE, QDRANT_COLLECTION, QDRANT_URL
from embeddings import embed_documents


def load_products() -> list[dict]:
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def product_text(p: dict) -> str:
    """Teks yang di-embed: gabungan field yang relevan untuk pencarian."""
    return f"{p['nama']}. Kategori: {p['kategori']}. {p['deskripsi']}"


def main() -> None:
    products = load_products()
    print(f"Loaded {len(products)} produk dari {PRODUCTS_FILE}")

    client = QdrantClient(url=QDRANT_URL)

    # (re)create collection
    client.recreate_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )
    print(f"Collection '{QDRANT_COLLECTION}' siap (dim={EMBEDDING_DIM}, cosine)")

    vectors = embed_documents([product_text(p) for p in products])

    points = [
        PointStruct(
            id=i,
            vector=vec,
            payload=p,  # simpan semua field produk sebagai payload
        )
        for i, (p, vec) in enumerate(zip(products, vectors))
    ]

    client.upsert(collection_name=QDRANT_COLLECTION, points=points)
    print(f"Upsert {len(points)} produk ke Qdrant. Selesai ✅")


if __name__ == "__main__":
    main()
