#!/bin/sh
set -e

echo "Menunggu Qdrant & Redis siap ..."
python - <<'PY'
import os, time, urllib.request
import redis as _redis

qbase = os.environ.get("QDRANT_URL", "http://qdrant:6333")
rurl = os.environ.get("REDIS_URL", "redis://redis:6379")

def wait(name, check):
    for _ in range(60):
        try:
            check(); print(f"{name} siap ✅"); return
        except Exception:
            time.sleep(1)
    print(f"⚠️  {name} tidak merespon, tetap lanjut...")

wait("Qdrant", lambda: urllib.request.urlopen(qbase + "/readyz", timeout=2))
wait("Redis", lambda: _redis.from_url(rurl).ping())
PY

# sinkron produk dari API toko -> Redis + Qdrant
echo "Sinkronisasi produk dari API..."
python sync.py || echo "⚠️  sync gagal (cek API/Redis), API tetap start - pakai data lama/seed"

echo "Start API di :6001"
exec uvicorn main:app --host 0.0.0.0 --port 6001
