#!/bin/sh
# start.sh — ProjectFlow production startup

DATA_DIR="${DATA_DIR:-/data}"
APP_DIR="$(dirname "$(realpath "$0")")"

echo "▶ ProjectFlow startup"
echo "  App dir  : $APP_DIR"
echo "  Data dir : $DATA_DIR"
echo "  Port     : ${PORT:-8080}"

# Ensure persistent data directories exist
mkdir -p "$DATA_DIR/pf_uploads"

# ── Check if existing DB is valid ─────────────────────────────────────────────
if [ -f "$DATA_DIR/projectflow.db" ]; then
  TABLE_COUNT=$(sqlite3 "$DATA_DIR/projectflow.db" \
    "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo "0")
  if [ "$TABLE_COUNT" = "0" ]; then
    echo "  ⚠ Empty/corrupt DB on volume — removing it"
    rm -f "$DATA_DIR/projectflow.db"
  else
    echo "  ✓ Existing DB healthy ($TABLE_COUNT tables)"
  fi
fi

# ── Symlink DB ────────────────────────────────────────────────────────────────
if [ ! -L "$APP_DIR/projectflow.db" ]; then
  if [ -f "$APP_DIR/projectflow.db" ] && [ ! -f "$DATA_DIR/projectflow.db" ]; then
    cp "$APP_DIR/projectflow.db" "$DATA_DIR/projectflow.db"
    rm -f "$APP_DIR/projectflow.db"
  fi
  ln -sf "$DATA_DIR/projectflow.db" "$APP_DIR/projectflow.db"
  echo "  ✓ DB -> $DATA_DIR/projectflow.db"
fi

# ── Symlink uploads ───────────────────────────────────────────────────────────
if [ ! -L "$APP_DIR/pf_uploads" ]; then
  if [ -d "$APP_DIR/pf_uploads" ]; then
    cp -r "$APP_DIR/pf_uploads/." "$DATA_DIR/pf_uploads/" 2>/dev/null || true
    rm -rf "$APP_DIR/pf_uploads"
  fi
  ln -sf "$DATA_DIR/pf_uploads" "$APP_DIR/pf_uploads"
  echo "  ✓ Uploads -> $DATA_DIR/pf_uploads"
fi

# ── Symlink session secret ────────────────────────────────────────────────────
if [ ! -L "$APP_DIR/.pf_secret" ]; then
  if [ -f "$APP_DIR/.pf_secret" ] && [ ! -f "$DATA_DIR/.pf_secret" ]; then
    cp "$APP_DIR/.pf_secret" "$DATA_DIR/.pf_secret"
    rm -f "$APP_DIR/.pf_secret"
  fi
  ln -sf "$DATA_DIR/.pf_secret" "$APP_DIR/.pf_secret"
  echo "  ✓ Secret -> $DATA_DIR/.pf_secret"
fi

# ── THE CRITICAL FIX ──────────────────────────────────────────────────────────
# app.py calls init_db() only inside "if __name__ == '__main__'" which means
# gunicorn (which imports app as a module) NEVER calls it — leaving an empty DB.
# We explicitly call it here before gunicorn starts.
echo "  Initializing database tables..."
cd "$APP_DIR" && python -c "
from app import init_db
init_db()
print('  ✓ Database tables ready')
"

echo ""
echo "  Launching gunicorn on port ${PORT:-8080}..."
exec gunicorn app:app \
  --config gunicorn.conf.py \
  --bind "0.0.0.0:${PORT:-8080}"
