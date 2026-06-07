# TokoEko — WA AI Order Bot

WhatsApp bot **TokoEko** untuk order **contoh pakaian**. User chat ke WA, tanya produk pakai bahasa natural (misal *"cari gamis harga 50rb"*), bot balas dengan beberapa list produk + link.

- **WhatsApp:** [Baileys](https://github.com/WhiskeySockets/Baileys) (Node.js, WebSocket — tanpa browser/Chromium)
- **AI / orchestration:** Python + [LangChain](https://www.langchain.com/)
- **LLM:** Groq — `llama-3.3-70b-versatile`
- **Vector DB:** [Qdrant](https://qdrant.tech/) (search produk: hybrid keyword + semantik)
- **Cache / data store:** Redis (keranjang per-user **+ katalog produk** hasil sync API)
- **Sumber data:** API toko (`shop.teknonusantara.net/api/products`), di-sync berkala.

> Data produk **sudah live dari API** (16 produk, multi-halaman). Seed `products.json`
> tetap ada sebagai fallback kalau API/Redis belum siap.

---

## Sinkronisasi produk dari API ("cron")

Produk ditarik dari API toko → disimpan ke **Redis** (katalog) **dan** di-embed ke
**Qdrant** (search). Alur ini ada di [sync.py](ai-service/sync.py) (`run_sync()`),
fetch semua halaman via [api_client.py](ai-service/api_client.py).

**Pakai cron?** Untuk Docker Compose dipilih **scheduler internal** (bukan cron OS),
biar nggak perlu container/crontab tambahan:

- **Saat start** → [entrypoint.sh](ai-service/entrypoint.sh) sync sekali (Qdrant siap sebelum API nerima trafik).
- **Berkala** → [scheduler.py](ai-service/scheduler.py) jalan tiap `SYNC_INTERVAL_MINUTES` (default 30). Set `0` untuk matikan.
- **Manual / cron eksternal** → `POST /admin/sync` (aktif kalau `ADMIN_TOKEN` di-set):
  ```bash
  curl -X POST http://localhost:6001/admin/sync -H "X-Admin-Token: <ADMIN_TOKEN>"
  ```
  Jadi kalau kamu lebih suka cron OS / scheduler eksternal, tinggal set `SYNC_INTERVAL_MINUTES=0`
  dan panggil endpoint ini dari cron.

Field yang dipakai dari API: `id, nama, kategori, harga, stok, deskripsi, link,
variants (sizes/colors), updated_at`. Varian (ukuran & warna) dipakai untuk
menjawab pertanyaan detail (`ada size apa aja?`), dicantumkan ke balasan search
biar tidak salah info warna, dan warna ikut di-embed agar query seperti
*"sepatu hitam"* tetap ketemu.
Produk **stok 0 disembunyikan** dari hasil search. Kategori mengikuti data asli
(mis. `pria, wanita, aksesoris, muslimah, sepatu, tas`) dan disuntik otomatis ke
klasifikasi intent.

---

## Pengaman (guard)

Beberapa lapis biar bot aman & nggak ngelantur:

- **Input dibatasi** `MAX_INPUT_CHARS` (default 500). Pesan kelewat panjang ditolak
  sopan **sebelum** dikirim ke LLM → hemat token & anti-spam.
- **Output dibatasi** `MAX_OUTPUT_CHARS` (default 1500). Balasan dipotong rapi
  supaya enak dibaca di WhatsApp.
- **Tetap di konteks toko.** Pesan di luar urusan belanja (pertanyaan umum, coding,
  politik, dll) dikenali sebagai `out_of_scope` → bot bilang dengan sopan kalau di
  sini khusus buat belanja di TokoEko, **tanpa** menjawab pertanyaannya. Dijaga 2
  lapis: klasifikasi intent **dan** instruksi di prompt balasan ("jangan jawab di
  luar urusan toko, jangan mengarang produk").
- **Fallback manusiawi.** Sapaan, out-of-scope, error, produk nggak ketemu, dll
  pakai bahasa santai + sedikit variasi frasa (deterministik per pesan) biar nggak
  terasa kaku/template.
- **Anti-error.** Semua pemrosesan dibungkus `try/except`; kalau LLM/Qdrant/Redis
  bermasalah, user dapat pesan "lagi ada gangguan" bukan crash.
- **Fallback model LLM.** Kalau model utama (`GROQ_MODEL`) kena rate limit (429),
  otomatis coba `GROQ_FALLBACK_MODELS` berurutan (default `llama-3.1-8b-instant`,
  lalu `gemma2-9b-it`) — chat tetap jalan tanpa intervensi. Baru error kalau
  SEMUA model kehabisan kuota.

Semua angka & nama toko bisa diatur lewat `.env` (`STORE_NAME`, `MAX_INPUT_CHARS`,
`MAX_OUTPUT_CHARS`).

---

## Fitur & perintah

| Maksud | Contoh ketikan user |
|---|---|
| Cari produk | `cari gamis harga 50rb` |
| Lihat semua produk | `ada produk apa aja` / `lihat semua` |
| Lihat lebih banyak hasil | `lanjut` / `ada lagi` / `yang lain` |
| Tanya detail produk | `ada size apa?` / `warna apa aja?` / `bahannya apa?` |
| Simpan ke keranjang | `simpan no 1` / `masukkan keranjang gamis katun` |
| Lihat keranjang | `keranjang saya` |
| Hapus 1 item | `hapus no 1` / `hapus gamis` |
| Kosongkan keranjang | `kosongkan keranjang` |
| Bayar / checkout | `bayar` / `checkout` |
| Cara order | `gimana cara order` / `bantuan` |

**Alur order:** cari produk → (langsung `bayar`) **atau** `simpan no <x>` ke keranjang dulu → `keranjang saya` → `bayar`.

- Keranjang disimpan **per nomor WhatsApp** di Redis (`cart:<nomor>`).
- Rujukan **nomor** (`simpan no 2`, `hapus no 1`) mengacu ke daftar terakhir yang
  ditampilkan bot (hasil pencarian / isi keranjang), disimpan di `last_list:<nomor>` (TTL 1 jam).
- **Paginasi**: kalau hasil pencarian > `PAGE_SIZE` (default 5), bot tampilkan 5 dulu
  + info *"masih ada N lagi"*. Ketik `lanjut` untuk batch berikutnya (state di
  `search:<nomor>`). Nomor selalu mengikuti batch yang sedang tampil.
- Checkout sekarang **dummy**: bikin kode order `INV-<timestamp>` + instruksi transfer, lalu keranjang dikosongkan.

---

## Kenapa butuh Vector DB (Qdrant)?

Query user itu bahasa natural & gak persis sama kata kunci di DB. Contoh:

- User: *"baju muslim wanita longgar"* → produk di DB namanya *"Gamis Syari Premium"*.
- Keyword search biasa **gak ketemu** (gak ada kata "longgar"/"muslim wanita").
- Vector search **ketemu** karena makna-nya mirip (semantic similarity).

Jadi flow-nya:
1. Tiap produk di-*embed* jadi vector → disimpan di Qdrant (beserta metadata: nama, harga, kategori, link).
2. Query user di-*embed* juga → cari produk paling mirip di Qdrant.
3. Filter harga (`<= 50.000`) lewat **payload filter** Qdrant, bukan di-AI.

> Alternatif kalau mau simpel dulu: skip Qdrant, pakai keyword filter biasa di JSON dummy.
> Tapi karena tujuannya search natural, **Qdrant disarankan dari awal**.

---

## Arsitektur / Flow

openWA itu Node.js, AI-nya Python — jadi **2 service** yang ngobrol via HTTP.

```
┌────────────┐    msg    ┌─────────────────┐   query   ┌──────────────┐
│  WhatsApp  │ ────────▶ │  openWA (Node)  │ ────────▶ │ AI Service   │
│   (HP)     │ ◀──────── │   listener      │ ◀──────── │ (Python/     │
└────────────┘   reply   └─────────────────┘   reply   │  FastAPI +   │
                                                        │  LangChain)  │
                                                        └──────┬───────┘
                                                               │
                                              ┌────────────────┼────────────────┐
                                              ▼                ▼                ▼
                                       ┌───────────┐    ┌───────────┐   ┌───────────┐
                                       │   Groq    │    │  Qdrant   │   │   Redis   │
                                       │  (LLM)    │    │ (vector)  │   │  (cache)  │
                                       └───────────┘    └───────────┘   └───────────┘
```

### Alur per pesan

1. **User** kirim chat (mis. *"cari gamis harga 50rb"* atau *"keranjang saya"*).
2. **openWA** terima pesan → POST ke AI service (`POST /chat` `{ from_, text }`).
3. **AI service:** LLM Groq klasifikasi **action** dari teks (dibantu **riwayat chat**
   per nomor dari Redis, supaya pesan lanjutan seperti *"yang 50rb aja"* tetap nyambung)
   → salah satu:
   `search · add_to_cart · view_cart · remove_from_cart · checkout · help · greeting`,
   lengkap dengan parameter (`kategori`, `max_harga`, `product_ref`). Lalu router:
   - **search** → embed query → semantic search Qdrant (+ filter harga) → LLM susun balasan.
   - **add/view/remove/checkout** → operasi keranjang di **Redis** (per nomor WA) → balasan template.
   - **help/greeting** → info cara order.
4. **AI service** balas JSON → openWA → **kirim ke WhatsApp**.

---

## Yang perlu di-setup

### 1. Groq API Key
- Daftar di https://console.groq.com → bikin API key.
- Simpan di `.env` → `GROQ_API_KEY=...`

### 2. Qdrant (vector DB)
Paling gampang pakai Docker:
```bash
docker run -p 6003:6333 -p 6334:6334 -v $(pwd)/qdrant_storage:/qdrant/storage qdrant/qdrant
```
Dashboard: http://localhost:6003/dashboard

### 3. Embedding model
Untuk ubah teks → vector. Pakai **[fastembed](https://github.com/qdrant/fastembed)**
(ONNX runtime — ringan, satu keluarga dengan Qdrant), model
`paraphrase-multilingual-MiniLM-L12-v2` (multilingual, **384 dim**, ~0.22 GB).
- Model di-download otomatis saat pertama jalan, lalu di-cache di volume
  `model_cache` (`EMBEDDING_CACHE_DIR=/root/.cache/fastembed`) → restart berikutnya cepat.
- Sengaja **bukan** PyTorch/sentence-transformers supaya image jauh lebih kecil.
> Groq belum nyediain embedding, jadi embedding pakai model terpisah.

### 4. Redis (nanti)
```bash
docker run -p 6004:6379 redis
```

### 5. WA Gateway — Baileys (Node.js)
```bash
cd wa-gateway
npm install
npm start   # lalu buka http://localhost:6002/qr untuk scan
```

---

## Struktur proyek (rencana)

```
wa-ai-order/
├── README.md
├── brainstrom.md
├── .env.example
├── docker-compose.yml          # satukan semua service
│
├── wa-gateway/                 # Node.js — koneksi WhatsApp (Baileys) + halaman QR
│   ├── index.js                # listener pesan → forward ke AI service
│   ├── package.json
│   └── Dockerfile              # node + chromium (puppeteer)
│
└── ai-service/                 # Python — AI + search
    ├── main.py                 # FastAPI: POST /chat
    ├── agent.py                # ekstrak intent (Groq) → search → balasan
    ├── search.py               # hybrid search (keyword + semantik) + filter
    ├── cart.py                 # keranjang per-user di Redis
    ├── memory.py               # riwayat chat per-user di Redis (paham follow-up)
    ├── catalog.py              # katalog produk (Redis, fallback seed)
    ├── api_client.py           # fetch produk dari API toko (semua halaman)
    ├── sync.py                 # API → Redis + Qdrant
    ├── scheduler.py            # sync berkala (cron internal)
    ├── embeddings.py           # wrapper fastembed (ONNX, multilingual)
    ├── ingest.py               # (lama) load dummy seed → Qdrant
    ├── config.py               # konfigurasi dari env
    ├── cli.py                  # tes via terminal tanpa WhatsApp
    ├── entrypoint.sh           # tunggu Qdrant → ingest → start API
    ├── data/products.json      # DUMMY data produk
    ├── requirements.txt
    └── Dockerfile
```

### Contoh dummy `products.json`
```json
[
  {
    "id": "p001",
    "nama": "Gamis Syari Premium",
    "kategori": "gamis",
    "harga": 49000,
    "deskripsi": "Gamis muslim wanita, bahan adem, longgar",
    "link": "https://toko.example.com/p/p001"
  },
  {
    "id": "p002",
    "nama": "Gamis Katun Polos",
    "kategori": "gamis",
    "harga": 55000,
    "deskripsi": "Gamis simpel harian, katun premium",
    "link": "https://toko.example.com/p/p002"
  }
]
```

---

## Roadmap

- [x] Setup `wa-gateway` (openWA) — listener + forward ke AI service
- [x] Setup `ai-service` (FastAPI + LangChain + Groq)
- [x] `ingest.py` — masukin dummy produk ke Qdrant
- [x] Intent/filter extraction (kategori + harga) via Groq
- [x] Semantic search + filter harga di Qdrant
- [x] Format balasan WA (list produk + link)
- [x] Keranjang per-user di Redis (simpan / lihat / hapus)
- [x] Checkout / bayar (dummy: kode order + instruksi transfer)
- [x] Guard input/output + lock konteks toko + branding TokoEko
- [x] Short-term memory (paham pesan lanjutan, hanya di klasifikasi intent)
- [x] Dockerize semua (`docker compose`)
- [x] Narik data dari API → Redis + Qdrant, sync berkala (scheduler) + `/admin/sync`
- [x] Hybrid search (keyword + semantik) + sembunyikan stok habis
- [x] Sort harga untuk query "termurah/termahal" (+ nyambung dgn memory)
- [ ] Konfirmasi bayar + simpan riwayat order

---

## Quickstart — Docker Compose (semua sekaligus)

Semua service (Qdrant, Redis, AI service, WA gateway) sudah disatukan di
`docker-compose.yml`.

```bash
# 1. siapkan .env (isi GROQ_API_KEY)
cp .env.example .env

# 2. build & jalankan semua
docker compose up --build
```

Yang terjadi otomatis saat start:
- Qdrant & Redis nyala
- `ai-service` nunggu Qdrant siap → sync produk dari API → start API di `:8000`
- `wa-gateway` (Baileys) nyala → sediakan **halaman QR**

Scan QR-nya — buka di browser:
> **http://localhost:6002/qr**

Halaman auto-refresh: tampilkan QR (buka WhatsApp → *Perangkat tertaut* → *Tautkan
perangkat*), dan otomatis berubah jadi "✅ Tersambung" begitu login. Sesi tersimpan di
volume `wa_session`, jadi nggak perlu scan ulang tiap restart.

Tes AI tanpa WhatsApp (`from_` jadi identitas keranjang — samakan tiap request):
```bash
curl -sX POST http://localhost:6001/chat -H "Content-Type: application/json" \
  -d '{"from_":"628123","text":"cari gamis harga 50rb"}'

curl -sX POST http://localhost:6001/chat -H "Content-Type: application/json" \
  -d '{"from_":"628123","text":"simpan no 1"}'

curl -sX POST http://localhost:6001/chat -H "Content-Type: application/json" \
  -d '{"from_":"628123","text":"keranjang saya"}'

curl -sX POST http://localhost:6001/chat -H "Content-Type: application/json" \
  -d '{"from_":"628123","text":"bayar"}'
```

Stop / reset:
```bash
docker compose down            # stop
docker compose down -v         # stop + hapus data (qdrant, redis, sesi WA, cache model)
```

> Catatan: build pertama agak lama (download Chromium di gateway + model embedding
> e5 di ai-service). Setelahnya cepat karena di-cache lewat volume.

---

## Quickstart — manual (tanpa Docker)

```bash
# 1. infra
docker run -p 6003:6333 qdrant/qdrant

# 2. AI service
cd ai-service
pip install -r requirements.txt
python ingest.py        # isi dummy produk ke Qdrant
python cli.py           # tes via terminal
uvicorn main:app --reload --port 8000

# 3. WA gateway
cd ../wa-gateway
npm install && npm start # scan QR
```
