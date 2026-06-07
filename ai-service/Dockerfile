FROM python:3.11-slim

WORKDIR /app

# install dependency python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# cache model embedding (fastembed) di-set via EMBEDDING_CACHE_DIR (lihat compose)

EXPOSE 6001

RUN chmod +x entrypoint.sh
CMD ["./entrypoint.sh"]
