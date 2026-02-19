FROM python:3.9-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.9-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    WORKERS=3 \
    DATA_DIR=/app/data

RUN mkdir -p /app/data

WORKDIR /app
COPY src/ ./src/

WORKDIR /app/src

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" || exit 1

CMD gunicorn app:app \
    -w ${WORKERS} \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:${PORT} \
    --timeout 120 \
    --graceful-timeout 30 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --worker-connections 1000 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile -
