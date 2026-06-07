# Deploy (CI/CD)

CI/CD pakai **GitHub Actions** ([.github/workflows/ci-cd.yml](.github/workflows/ci-cd.yml)).

- **CI** (tiap push & pull request): cek syntax Python & Node, validasi `docker-compose`, build kedua image.
- **CD** (tiap push ke `main`/`master`): SSH ke server → `git pull` → `docker compose up -d --build` → bersihin image lama.

---

## 1. Secrets di GitHub

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Wajib | Contoh / keterangan |
|---|---|---|
| `SSH_HOST` | ✅ | IP / domain server, mis. `203.0.113.10` |
| `SSH_USER` | ✅ | user SSH, mis. `deploy` atau `root` |
| `SSH_KEY` | ✅ | **private key** SSH (isi file, bukan path) |
| `SSH_PORT` | opsional | default `22` |

> Path deploy sudah di-hardcode di workflow: **`/var/www/wa-ai-tera`** (bukan secret).
> Kalau pindah folder, ubah di [.github/workflows/ci-cd.yml](.github/workflows/ci-cd.yml).

Bikin keypair (kalau belum):
```bash
ssh-keygen -t ed25519 -C "github-deploy" -f deploy_key
# isi deploy_key.pub -> ~/.ssh/authorized_keys di server
# isi deploy_key (private) -> secret SSH_KEY
```

---

## 2. Prasyarat di server (sekali setup)

```bash
# 1. Docker + Docker Compose plugin terpasang
# 2. clone repo ke path deploy
git clone <url-repo> /var/www/wa-ai-tera
cd /var/www/wa-ai-tera

# 3. siapkan .env (TIDAK ikut git — berisi secret)
cp .env.example .env
nano .env   # isi GROQ_API_KEY, ADMIN_TOKEN, dll

# 4. jalan pertama kali
docker compose up -d --build
```

> `.env` di-gitignore, jadi **tidak** ke-pull oleh CD. Cukup dibuat sekali di server;
> deploy berikutnya nggak menimpanya.

---

## 3. Alur deploy

```
git push origin main
   └─ GitHub Actions: CI (lint + build) ─► CD (SSH ke server)
        └─ cd /var/www/wa-ai-tera && git pull && docker compose up -d --build
```

Pantau hasilnya di tab **Actions** repo GitHub.

---

## Catatan

- Server harus bisa `git pull` (pakai deploy key / token git tersendiri di server).
- Port yang dibuka di server: **6001** (API+UI), **6002** (QR). 6003/6004 (qdrant/redis)
  sebaiknya **jangan** diekspos ke publik — cukup internal. Atur firewall sesuai kebutuhan.
- Mau zero-downtime / push image ke registry (GHCR) ketimbang build di server? Bisa
  diubah belakangan.
