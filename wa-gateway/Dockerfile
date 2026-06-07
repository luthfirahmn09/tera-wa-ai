# ---- builder: install deps (butuh git utk dep 'libsignal' dari GitHub) ----
FROM node:20-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json ./
RUN npm install --omit=optional

# ---- runtime: image bersih tanpa git/build tools ----
FROM node:20-slim
WORKDIR /app
COPY --from=builder /app/node_modules ./node_modules
COPY . .
CMD ["node", "index.js"]
