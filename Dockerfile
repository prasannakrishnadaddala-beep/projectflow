# ── ProjectFlow v4.0 — Production Dockerfile ──────────────────────────────────
# Targets: Railway / Render / Fly.io
# Python 3.12 slim keeps the image small (~120 MB final)

FROM python:3.12-slim

# Metadata
LABEL org.opencontainers.image.title="ProjectFlow"
LABEL org.opencontainers.image.version="4.0"

# System deps (sqlite3 CLI useful for debugging; curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
      sqlite3 curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — PaaS best practice
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install Python dependencies first (Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY gunicorn.conf.py .
COPY start.sh .

RUN chmod +x start.sh

# Directories the app will create at runtime — declare them so Docker knows
# (actual data goes to /data volume, linked by start.sh)
RUN mkdir -p /data/pf_uploads /data/pf_static \
    && chown -R appuser:appuser /app /data

USER appuser

# PaaS platforms inject $PORT — gunicorn reads it in start.sh
EXPOSE 8080

# Health check — PaaS uses this to know when the app is ready
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8080}/api/auth/me || exit 1

CMD ["./start.sh"]
