#!/bin/sh
# start.sh — ProjectFlow production startup
# Persists DB, uploads and session secret to /data volume across redeploys.

DATA_DIR="${DATA_DIR:-/data}"
APP_DIR="$(dirname "$(realpath "$0")")"

echo "▶ ProjectFlow startup"
echo "  App dir  : $APP_DIR"
echo "  Data dir : $DATA_DIR"
echo "  Port     : ${PORT:-8080}"

# Ensure persistent data directories exist
mkdir -p "$DATA_DIR/pf_uploads"

# ── Persist the database ──────────────────────────────────────────────────────
if [ ! -L "$APP_DIR/projectflow.db" ]; then
  if [ -f "$APP_DIR/projectflow.db" ] && [ ! -f "$DATA_DIR/projectflow.db" ]; then
    echo "  Migrating DB to volume..."
    cp "$APP_DIR/projectflow.db" "$DATA_DIR/projectflow.db"
    rm -f "$APP_DIR/projectflow.db"
  fi
  ln -sf "$DATA_DIR/projectflow.db" "$APP_DIR/projectflow.db"
  echo "  ✓ DB -> $DATA_DIR/projectflow.db"
fi

# ── Persist uploaded files ────────────────────────────────────────────────────
if [ ! -L "$APP_DIR/pf_uploads" ]; then
  if [ -d "$APP_DIR/pf_uploads" ]; then
    cp -r "$APP_DIR/pf_uploads/." "$DATA_DIR/pf_uploads/" 2>/dev/null || true
    rm -rf "$APP_DIR/pf_uploads"
  fi
  ln -sf "$DATA_DIR/pf_uploads" "$APP_DIR/pf_uploads"
  echo "  ✓ Uploads -> $DATA_DIR/pf_uploads"
fi

# ── Persist the session secret key ───────────────────────────────────────────
# Without this, every redeploy invalidates all user sessions (forced logout)
if [ ! -L "$APP_DIR/.pf_secret" ]; then
  if [ -f "$APP_DIR/.pf_secret" ] && [ ! -f "$DATA_DIR/.pf_secret" ]; then
    cp "$APP_DIR/.pf_secret" "$DATA_DIR/.pf_secret"
    rm -f "$APP_DIR/.pf_secret"
  fi
  ln -sf "$DATA_DIR/.pf_secret" "$APP_DIR/.pf_secret"
  echo "  ✓ Secret key -> $DATA_DIR/.pf_secret"
fi

# NOTE: pf_static (JS libs) is NOT symlinked — it's baked into the Docker image
# at build time so we never need to download it at runtime.

echo ""
echo "  Launching gunicorn on port ${PORT:-8080}..."
exec gunicorn app:app \
  --config gunicorn.conf.py \
  --bind "0.0.0.0:${PORT:-8080}"
