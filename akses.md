# Akses TokoEko

Daftar semua akses sistem TokoEko. Skema port: **6001** ai-service · **6002** wa-gateway · **6003** qdrant · **6004** redis.

> Endpoint `/admin/*` butuh header `X-Admin-Token` (nilai dari `ADMIN_TOKEN` di `.env`, saat ini `Tekno123`). Kalau `ADMIN_TOKEN` dikosongkan, semua `/admin/*` mati (balas 404).

---

## 🌐 Halaman web (buka di browser)

| Akses | URL |
|---|---|
| Test chat (UI ala WhatsApp) | http://localhost:6001/ui/ |
| Monitor chat (inbox semua user) | http://localhost:6001/ui/monitor.html |
| Scan QR WhatsApp | http://localhost:6002/qr |
| Qdrant dashboard | http://localhost:6003/dashboard |
| API docs (Swagger) | http://localhost:6001/docs |

---

## 🔓 API publik (ai-service @ 6001)

| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/health` | cek status service |
| POST | `/chat` | kirim pesan, balas hasil AI |

Body `/chat`:
- Teks: `{ "from_": "<nomor>", "text": "<pesan>" }`
- Voice: `{ "from_": "<nomor>", "audio_base64": "<OGG base64>" }`

Response: `{ "reply": "<gabungan>", "messages": ["<bubble1>", ...], "transcript": "<hasil STT/null>" }`

```bash
curl -X POST http://localhost:6001/chat -H "Content-Type: application/json" \
  -d '{"from_":"628xx","text":"cari sepatu"}'
```

---

## 🔐 API admin (header `X-Admin-Token: Tekno123`)

| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/admin/chats?limit=200` | riwayat chat semua user (dipakai halaman monitor) |
| POST | `/admin/sync` | tarik ulang produk dari API toko → Redis + Qdrant |
| GET | `/admin/config/voice` | cek status fitur voice note |
| POST | `/admin/config/voice?enabled=true\|false` | aktif / non-aktifkan voice note (runtime) |
| GET | `/admin/config/groq-key` | lihat API key Groq aktif (tersamar) |
| POST | `/admin/config/groq-key` | ganti API key Groq runtime (body `{ "api_key": "..." }`) |

```bash
# sync produk manual
curl -X POST http://localhost:6001/admin/sync -H "X-Admin-Token: Tekno123"

# matikan / nyalakan voice note
curl -X POST "http://localhost:6001/admin/config/voice?enabled=false" -H "X-Admin-Token: Tekno123"
curl -X POST "http://localhost:6001/admin/config/voice?enabled=true"  -H "X-Admin-Token: Tekno123"

# ganti API key Groq tanpa restart (mis. kalau kuota harian habis)
curl -X POST http://localhost:6001/admin/config/groq-key -H "X-Admin-Token: Tekno123" \
  -H "Content-Type: application/json" -d '{"api_key":"gsk_xxx"}'

# lihat riwayat chat
curl "http://localhost:6001/admin/chats?limit=50" -H "X-Admin-Token: Tekno123"
```

---

## 🗄️ Infrastruktur

| Service | Akses |
|---|---|
| Qdrant (vector DB) | http://localhost:6003 · dashboard `/dashboard` |
| Redis (keranjang / katalog / log chat) | `localhost:6004` → `redis-cli -p 6004` |

Key Redis penting:
- `cart:<nomor>` — isi keranjang per user
- `last_list:<nomor>` — daftar produk terakhir ditampilkan (rujukan "simpan no x")
- `search:<nomor>` — state paginasi ("lanjut")
- `history:<nomor>` — riwayat chat (konteks LLM)
- `products:all` — katalog hasil sync API
- `chatlog` — log chat global (monitor)
- `config:groq_api_key`, `config:voice_enabled` — override runtime

---

## 🛠️ Operasional (Docker)

```bash
docker compose up -d --build       # build & jalankan semua service
docker compose ps                  # status + port
docker compose logs -f wa-gateway  # pantau WhatsApp gateway
docker compose logs -f ai-service  # pantau AI service
docker compose restart ai-service  # restart 1 service
docker compose down                # stop semua
docker compose down -v             # stop + hapus volume (data/sesi/cache)
```
