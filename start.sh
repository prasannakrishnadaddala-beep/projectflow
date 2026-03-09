#!/bin/sh
# start.sh — ProjectFlow production startup script
# Runs BEFORE gunicorn. Links /data (persistent volume) into the app directory
# so that the database and uploads survive redeploys — without any code changes.

set -e

DATA_DIR="${DATA_DIR:-/data}"
APP_DIR="$(dirname "$(realpath "$0")")"

echo "▶ ProjectFlow startup"
echo "  App dir  : $APP_DIR"
echo "  Data dir : $DATA_DIR"

# Ensure persistent data directory exists
mkdir -p "$DATA_DIR/pf_uploads"
mkdir -p "$DATA_DIR/pf_static"

# ── Symlink DB ────────────────────────────────────────────────────────────────
# app.py expects: $APP_DIR/projectflow.db
# We store it at:  $DATA_DIR/projectflow.db  (on the mounted volume)
if [ ! -L "$APP_DIR/projectflow.db" ]; then
  # If a real DB exists from a previous run in the app dir, migrate it
  if [ -f "$APP_DIR/projectflow.db" ] && [ ! -f "$DATA_DIR/projectflow.db" ]; then
    echo "  Migrating existing DB to persistent volume..."
    cp "$APP_DIR/projectflow.db" "$DATA_DIR/projectflow.db"
    rm "$APP_DIR/projectflow.db"
  fi
  ln -sf "$DATA_DIR/projectflow.db" "$APP_DIR/projectflow.db"
  echo "  ✓ DB symlinked"
fi

# ── Symlink uploads ───────────────────────────────────────────────────────────
# app.py expects: $APP_DIR/pf_uploads/
if [ ! -L "$APP_DIR/pf_uploads" ]; then
  if [ -d "$APP_DIR/pf_uploads" ] && [ ! "$(ls -A "$DATA_DIR/pf_uploads" 2>/dev/null)" ]; then
    cp -r "$APP_DIR/pf_uploads/." "$DATA_DIR/pf_uploads/"
    rm -rf "$APP_DIR/pf_uploads"
  elif [ -d "$APP_DIR/pf_uploads" ]; then
    rm -rf "$APP_DIR/pf_uploads"
  fi
  ln -sf "$DATA_DIR/pf_uploads" "$APP_DIR/pf_uploads"
  echo "  ✓ Uploads symlinked"
fi

# ── Symlink secret key file ───────────────────────────────────────────────────
# app.py expects: $APP_DIR/.pf_secret  (session key — must survive redeploys!)
if [ ! -L "$APP_DIR/.pf_secret" ]; then
  if [ -f "$APP_DIR/.pf_secret" ] && [ ! -f "$DATA_DIR/.pf_secret" ]; then
    cp "$APP_DIR/.pf_secret" "$DATA_DIR/.pf_secret"
    rm "$APP_DIR/.pf_secret"
  fi
  ln -sf "$DATA_DIR/.pf_secret" "$APP_DIR/.pf_secret"
  echo "  ✓ Secret key symlinked"
fi

# ── Symlink static JS libs ────────────────────────────────────────────────────
# pf_static/ holds downloaded React/Recharts bundles. Cache them on the volume
# so they don't re-download on every deploy (saves boot time & avoids CDN issues).
if [ ! -L "$APP_DIR/pf_static" ]; then
  if [ -d "$APP_DIR/pf_static" ]; then
    cp -r "$APP_DIR/pf_static/." "$DATA_DIR/pf_static/"
    rm -rf "$APP_DIR/pf_static"
  fi
  ln -sf "$DATA_DIR/pf_static" "$APP_DIR/pf_static"
  echo "  ✓ Static JS symlinked"
fi

echo ""
echo "  Starting gunicorn on port ${PORT:-8080}..."
exec gunicorn app:app \
  --config gunicorn.conf.py \
  --bind "0.0.0.0:${PORT:-8080}"
