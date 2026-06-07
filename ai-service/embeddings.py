"""Wrapper embedding model. Dipakai bersama oleh ingest.py dan search.py.

Pakai fastembed (ONNX runtime) - jauh lebih ringan dari PyTorch, dan satu
keluarga dengan Qdrant. Model di-load sekali (lazy) lalu di-cache.

Model default: paraphrase-multilingual-MiniLM-L12-v2 (multilingual, 384 dim).
Beda dengan e5, model ini TIDAK perlu prefix "passage:"/"query:".
"""
from __future__ import annotations

from functools import lru_cache

from fastembed import TextEmbedding

from config import EMBEDDING_CACHE_DIR, EMBEDDING_MODEL


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    return TextEmbedding(model_name=EMBEDDING_MODEL, cache_dir=EMBEDDING_CACHE_DIR)


def embed_documents(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _model().embed(texts)]


def embed_query(text: str) -> list[float]:
    return next(iter(_model().embed([text]))).tolist()
