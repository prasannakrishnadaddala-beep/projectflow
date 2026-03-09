# ── ProjectFlow v4.0 — Production Dockerfile ──────────────────────────────────
# Targets: Railway / Render / Fly.io

FROM python:3.12-slim

LABEL org.opencontainers.image.title="ProjectFlow"
LABEL org.opencontainers.image.version="4.0"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
      sqlite3 curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App files
COPY app.py .
COPY gunicorn.conf.py .
COPY start.sh .
RUN chmod +x start.sh

# ── Download JS libraries at BUILD TIME ───────────────────────────────────────
# Railway containers block outbound HTTP at runtime.
# Baking the libs into the image means app.py finds them already present
# and skips its own download logic entirely.
RUN mkdir -p /app/pf_static && \
    curl -fsSL "https://unpkg.com/react@18/umd/react.production.min.js"         -o /app/pf_static/react.min.js      && \
    curl -fsSL "https://unpkg.com/react-dom@18/umd/react-dom.production.min.js" -o /app/pf_static/react-dom.min.js  && \
    curl -fsSL "https://unpkg.com/prop-types@15/prop-types.min.js"              -o /app/pf_static/prop-types.min.js && \
    curl -fsSL "https://unpkg.com/recharts@2/umd/Recharts.js"                   -o /app/pf_static/recharts.min.js   && \
    curl -fsSL "https://unpkg.com/htm@3/dist/htm.js"                            -o /app/pf_static/htm.min.js        && \
    echo "JS libs baked in: $(du -sh /app/pf_static | cut -f1)"

# Runtime data directories (volume at /data is linked by start.sh)
RUN mkdir -p /data/pf_uploads \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8080

# Health check — / always returns 200; /api/auth/me returns 401 (breaks Railway)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f "http://localhost:${PORT:-8080}/" || exit 1

CMD ["./start.sh"]
