# TokoEko — WhatsApp AI Order Bot

A WhatsApp shopping assistant. Customers chat naturally (*"looking for a dress under 50k"*, *"any black shoes?"*), and the bot replies with matching products, cart management, and checkout — powered by an LLM and a vector search over the store's live product catalog.

- **WhatsApp:** [Baileys](https://github.com/WhiskeySockets/Baileys) (Node.js, WebSocket — no browser/Chromium needed)
- **AI / orchestration:** Python + [LangChain](https://www.langchain.com/)
- **LLM:** Groq — `llama-3.3-70b-versatile`, with automatic fallback models on rate limits
- **Voice notes:** transcribed via Groq Whisper, toggleable at runtime
- **Vector DB:** [Qdrant](https://qdrant.tech/) — hybrid keyword + semantic product search
- **Cache / data store:** Redis — per-user cart, chat memory, and synced product catalog
- **Data source:** the store's API, synced on a schedule

---

## How it works

Two services talk over HTTP: Baileys handles the WhatsApp connection, and a Python/FastAPI service handles the AI.

```
┌────────────┐    msg    ┌─────────────────┐   query   ┌──────────────┐
│  WhatsApp  │ ────────▶ │  wa-gateway     │ ────────▶ │ ai-service   │
│  (phone)   │ ◀──────── │  (Node/Baileys) │ ◀──────── │ (FastAPI +   │
└────────────┘   reply   └─────────────────┘   reply   │  LangChain)  │
                                                        └──────┬───────┘
                                                               │
                                              ┌────────────────┼────────────────┐
                                              ▼                ▼                ▼
                                       ┌───────────┐    ┌───────────┐   ┌───────────┐
                                       │   Groq    │    │  Qdrant   │   │   Redis   │
                                       │  (LLM)    │    │ (vector)  │   │  (cache)  │
                                       └───────────┘    └───────────┘   └───────────┘
```

**Per message:**

1. A customer sends a WhatsApp message.
2. `wa-gateway` forwards it to `ai-service` (`POST /chat`).
3. The LLM classifies intent (`search`, `add_to_cart`, `view_cart`, `remove_from_cart`, `checkout`, `help`, `greeting`, or `out_of_scope`), using recent chat history from Redis so follow-ups like *"just the 50k one"* stay in context.
4. The router acts on that intent:
   - **search** → embed the query → semantic search in Qdrant (with price/category filters) → the LLM composes a reply.
   - **cart actions** → read/write the per-user cart in Redis.
   - **checkout** → generate an order code and payment instructions.
5. The reply is sent back through `wa-gateway` to WhatsApp.

### Why a vector database?

Customer queries rarely match product names word-for-word — *"loose muslim wear for women"* should still find a product literally named *"Premium Syari Dress"*. Plain keyword search misses that; semantic search over embeddings finds it by meaning. Each product is embedded once and stored in Qdrant; price and stock filters are applied as payload filters, not left to the LLM to guess.

### Product sync

Products are pulled from the store's API into **Redis** (catalog) and embedded into **Qdrant** (search):

- **On startup** — synced once before the API starts serving traffic.
- **On a schedule** — an internal scheduler re-syncs every `SYNC_INTERVAL_MINUTES` (default 30; set to `0` to disable).
- **On demand** — `POST /admin/sync` (requires `ADMIN_TOKEN`), for an external cron if you'd rather not use the built-in scheduler.

Out-of-stock products are hidden from search results automatically.

### Guardrails

- Input/output length limits so replies stay readable on WhatsApp and token usage stays bounded.
- Messages outside the shopping context (general questions, coding help, etc.) are politely declined rather than answered, enforced both at intent classification and in the reply prompt.
- All processing is wrapped so a failure in the LLM, Qdrant, or Redis degrades to a friendly "having some trouble" message instead of a crash.
- If the primary Groq model is rate-limited, requests automatically retry against configured fallback models.

---

## Features

| Intent | Example message |
|---|---|
| Search products | `looking for a dress under 50k` |
| Browse everything | `what do you have` / `show me all` |
| See more results | `next` / `show more` |
| Ask product details | `what sizes?` / `what colors?` / `what's it made of?` |
| Add to cart | `save #1` / `add the cotton dress to cart` |
| View cart | `my cart` |
| Remove an item | `remove #1` / `remove the dress` |
| Clear cart | `empty my cart` |
| Checkout | `checkout` / `pay` |
| Help | `how do I order` |

Cart state is kept per WhatsApp number in Redis, and item references (`save #2`, `remove #1`) resolve against the last list the bot showed that user — whether that was a search result page or their cart.

---

## Getting started

### Requirements

- A [Groq](https://console.groq.com) API key
- Docker (recommended), or Python 3.11+ and Node.js 18+ for a manual setup

### Run with Docker Compose (recommended)

```bash
cp .env.example .env   # fill in GROQ_API_KEY
docker compose up --build
```

This starts Qdrant, Redis, `ai-service`, and `wa-gateway` together. On first boot, `ai-service` waits for Qdrant, syncs the product catalog, then starts serving on `:6001`.

Scan the WhatsApp QR code at **http://localhost:6002/qr** (open WhatsApp → *Linked Devices* → *Link a Device*). The session persists in a Docker volume, so you won't need to re-scan on restart.

Try the AI service directly (`from_` identifies the cart owner — keep it consistent across requests):

```bash
curl -sX POST http://localhost:6001/chat -H "Content-Type: application/json" \
  -d '{"from_":"628123","text":"looking for a dress under 50k"}'

curl -sX POST http://localhost:6001/chat -H "Content-Type: application/json" \
  -d '{"from_":"628123","text":"save #1"}'

curl -sX POST http://localhost:6001/chat -H "Content-Type: application/json" \
  -d '{"from_":"628123","text":"my cart"}'
```

Stop everything:

```bash
docker compose down       # stop
docker compose down -v    # stop + wipe volumes (Qdrant/Redis data, WA session, model cache)
```

### Manual setup

```bash
# Qdrant + Redis
docker run -p 6003:6333 qdrant/qdrant
docker run -p 6004:6379 redis

# AI service
cd ai-service
pip install -r requirements.txt
python ingest.py             # seed the vector DB
uvicorn main:app --reload --port 6001

# WhatsApp gateway
cd ../wa-gateway
npm install
npm start                    # then open http://localhost:6002/qr
```

---

## Configuration

All settings live in `.env` (see [.env.example](.env.example)):

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Groq API key for the LLM |
| `GROQ_MODEL` / `GROQ_FALLBACK_MODELS` | Primary model and ordered fallbacks |
| `VOICE_ENABLED` / `STT_MODELS` | Voice-note transcription toggle and models |
| `QDRANT_URL` / `REDIS_URL` | Backing store connections |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | Embedding model for semantic search |
| `PRODUCTS_API_URL` / `SYNC_INTERVAL_MINUTES` | Product source and sync cadence |
| `ADMIN_TOKEN` | Enables `/admin/*` endpoints (empty disables them) |
| `STORE_NAME`, `MAX_INPUT_CHARS`, `MAX_OUTPUT_CHARS` | Branding and guardrail limits |

---

## Project layout

```
wa-ai-order/
├── docker-compose.yml
├── ai-service/          # Python — FastAPI + LangChain + Groq
│   ├── main.py           # API entrypoint, /chat and /admin routes
│   ├── agent.py           # intent classification → routing → reply
│   ├── search.py          # hybrid keyword + semantic search
│   ├── cart.py            # per-user cart (Redis)
│   ├── memory.py          # per-user chat history (Redis)
│   ├── catalog.py         # product catalog (Redis, with seed fallback)
│   ├── api_client.py      # store API client
│   ├── sync.py            # API → Redis + Qdrant sync
│   ├── scheduler.py       # periodic sync
│   ├── embeddings.py      # fastembed wrapper (multilingual)
│   └── stt.py             # voice note transcription
└── wa-gateway/           # Node.js — Baileys WhatsApp connection + QR page
    └── index.js
```
