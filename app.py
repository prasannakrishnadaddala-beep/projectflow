#!/usr/bin/env python3
"""
VEWIT v4.0
Multi-tenant workspaces | AI Assistant | Stage Dropdown | Direct Messages
"""
import os, sys, json, hashlib, secrets, random, urllib.request, urllib.error
import socket, threading, time, webbrowser, mimetypes, base64, smtplib
import re, struct, traceback, hmac, math, zlib, logging
from datetime import datetime, timedelta
from functools import wraps

# ── Structured logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout
)
log = logging.getLogger("vewit")
try:
    import bcrypt as _bcrypt
except ImportError:
    _bcrypt = None

# ── Vault encryption (Fernet = AES-128-CBC + HMAC-SHA256) ─────────────────────
try:
    from cryptography.fernet import Fernet as _Fernet, InvalidToken as _InvalidToken
    _FERNET_OK = True
except ImportError:
    _FERNET_OK = False
    print("  ⚠ 'cryptography' package not installed — vault rows stored unencrypted.\n"
          "    Fix: pip install cryptography")

_vault_fernet_instance = None

def _get_vault_fernet():
    """Return a cached Fernet instance, creating/loading the key on first call."""
    global _vault_fernet_instance
    if not _FERNET_OK:
        return None
    if _vault_fernet_instance is not None:
        return _vault_fernet_instance
    # 1) Prefer env var (set this in Railway / Render secrets)
    env_key = os.environ.get("VAULT_ENCRYPTION_KEY", "").strip()
    if env_key:
        try:
            _vault_fernet_instance = _Fernet(env_key.encode() if isinstance(env_key, str) else env_key)
            return _vault_fernet_instance
        except Exception as e:
            print(f"  ⚠ VAULT_ENCRYPTION_KEY env var is invalid: {e} — generating a new key")
    # 2) Fall back to a persisted key file
    key_path = os.path.join(DATA_DIR, ".vault_enc_key")
    if os.path.exists(key_path):
        try:
            with open(key_path, "rb") as _kf:
                k = _kf.read().strip()
            _vault_fernet_instance = _Fernet(k)
            return _vault_fernet_instance
        except Exception:
            pass
    # 3) Generate and persist a new key
    k = _Fernet.generate_key()
    try:
        with open(key_path, "wb") as _kf:
            _kf.write(k)
        print(f"  ✓ New vault encryption key generated and saved to {key_path}")
    except Exception as e:
        print(f"  ⚠ Could not persist vault key ({e}) — key lives in memory only (restarts will lose it!)")
    _vault_fernet_instance = _Fernet(k)
    return _vault_fernet_instance

def vault_encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string. Returns a Fernet token string, or the original
    if the cryptography library is unavailable (graceful degradation)."""
    f = _get_vault_fernet()
    if not f:
        return plaintext
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")

def vault_decrypt(token: str) -> str:
    """Decrypt a Fernet token. Falls back to returning the raw value for any
    legacy unencrypted rows (so old data keeps working after upgrade)."""
    if not token:
        return token
    f = _get_vault_fernet()
    if not f:
        return token
    try:
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception:
        # Could be a pre-encryption legacy value — return as-is
        return token
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, session, Response, send_file
from flask_cors import CORS

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = "/data" if os.path.isdir("/data") else BASE_DIR
JS_DIR     = os.path.join(BASE_DIR, "pf_static")
UPLOAD_DIR = os.path.join(DATA_DIR, "pf_uploads")
KEY_FILE   = os.path.join(DATA_DIR, ".pf_secret")

# ── PostgreSQL via pg8000 (pure Python — no libpq/system deps needed) ────────
import pg8000.native
import urllib.parse, re as _re

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("PGURL") or ""

def _parse_db_url(url):
    """Parse postgres://user:pass@host:port/dbname into pg8000 kwargs."""
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    url = url.replace("postgres://", "postgresql://", 1)
    p = urllib.parse.urlparse(url)
    import ssl as _ssl
    ssl_ctx = _ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = _ssl.CERT_NONE
    return dict(host=p.hostname, port=p.port or 5432, user=p.username,
                password=p.password, database=p.path.lstrip("/"),
                ssl_context=ssl_ctx)

def _sql_compat(sql, params=()):
    """Convert SQLite SQL + params to PostgreSQL named-param style for pg8000.
    Returns (pg_sql, params_dict) — pg8000 run() accepts **kwargs for params.
    """
    if "INSERT OR IGNORE INTO" in sql:
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO").rstrip()
        sql += " ON CONFLICT DO NOTHING"
    if "INSERT OR REPLACE INTO push_subscriptions" in sql:
        sql = sql.replace("INSERT OR REPLACE INTO push_subscriptions",
                          "INSERT INTO push_subscriptions").rstrip()
        sql += (" ON CONFLICT (endpoint) DO UPDATE SET "
                "p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth, "
                "created=EXCLUDED.created")
    params_dict = {}
    idx = [0]
    def _rep(m):
        key = f"p{idx[0]}"
        if idx[0] < len(params):
            params_dict[key] = params[idx[0]]
        idx[0] += 1
        return f":{key}"
    sql = _re.sub(r"\?", _rep, sql)
    return sql, params_dict

class _Row(dict):
    """dict subclass: supports row['col'] and row[int_index] like sqlite3.Row."""
    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._list = list(values)
    def __getitem__(self, key):
        if isinstance(key, int): return self._list[key]
        return super().__getitem__(key)
    def keys(self): return list(super().keys())

class _Cursor:
    """Thin wrapper so our code can call .execute()/.fetchone()/.fetchall()."""
    def __init__(self, conn):
        self._conn = conn
        self._rows = []
        self._cols = []
        self.rowcount = 0
    def execute(self, sql, params=()):
        pg_sql, params_dict = _sql_compat(sql, params)
        if params_dict:
            result = self._conn.run(pg_sql, **params_dict)
        else:
            result = self._conn.run(pg_sql)
        self._rows = result or []
        self._cols = [c["name"] for c in (self._conn.columns or [])]
        self.rowcount = self._conn.row_count or 0
        return self
    def fetchone(self):
        return _Row(self._cols, self._rows[0]) if self._rows else None
    def fetchall(self):
        return [_Row(self._cols, r) for r in self._rows]
    def __iter__(self):
        return iter(self.fetchall())

class _DB:
    """Context-manager wrapper matching 'with get_db() as db:' pattern."""
    def __init__(self, conn):
        self._conn = conn
    def execute(self, sql, params=()):
        return _Cursor(self._conn).execute(sql, params)
    def executescript(self, sql):
        """Run semicolon-separated DDL statements (used by init_db)."""
        stmts = [s.strip() for s in sql.split(";") if s.strip()]
        for stmt in stmts:
            try:
                self._conn.run(stmt)
            except Exception as e:
                msg = str(e).lower()
                safe = ["already exists", "duplicate", "column already",
                        "relation already", "index already"]
                if any(x in msg for x in safe):
                    continue
                print(f"  executescript error on: {stmt[:80]!r}: {e}")
                raise
    def commit(self):
        if not getattr(self._conn, 'autocommit', False):
            try: self._conn.run("COMMIT")
            except Exception: pass
    def close(self):
        try: self._conn.close()
        except Exception: pass
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        if not getattr(self._conn, 'autocommit', False):
            try:
                if exc_type: self._conn.run("ROLLBACK")
                else: self._conn.run("COMMIT")
            except Exception: pass
        self.close()
        return False

def get_secret_key():
    env_key = os.environ.get("SECRET_KEY","")
    if len(env_key) >= 32: return env_key
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE,"r") as f:
                k=f.read().strip()
                if len(k)==64: return k
        except: pass
    k=secrets.token_hex(32)
    try:
        with open(KEY_FILE,"w") as f: f.write(k)
    except: pass
    return k

app = Flask(__name__)
app.secret_key = get_secret_key()
APP_STARTED_AT = datetime.utcnow()
_is_https = os.environ.get("HTTPS","").lower() in ("1","true","on") or              os.environ.get("RAILWAY_ENVIRONMENT","") != "" or              os.environ.get("RENDER","") != ""
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=_is_https,PERMANENT_SESSION_LIFETIME=86400*30,
    SESSION_COOKIE_NAME="pf_session",
    MAX_CONTENT_LENGTH=150*1024*1024)
CORS(app, supports_credentials=True)

@app.after_request
def add_security_headers(response):
    """Add security headers to every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self' https://api.anthropic.com; "
        "frame-ancestors 'self';"
    )
    # Only set HSTS on HTTPS
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

CLRS=["#7c3aed","#2563eb","#059669","#d97706","#dc2626","#ec4899","#0891b2","#5a8cff"]

def get_db(autocommit=False):
    conn = pg8000.native.Connection(**_parse_db_url(DATABASE_URL))
    conn.autocommit = autocommit  # pg8000 supports autocommit property
    return _DB(conn)

# ── Simple pg8000 connection pool (3 reusable connections) ──────────────────
import queue as _queue, threading as _poollock
_PG_POOL = _queue.Queue(maxsize=5)
_PG_POOL_LOCK = _poollock.Lock()

def _get_pool_conn():
    """Get a connection from pool or create a new one."""
    from pg8000.native import Connection as _PGConn
    try:
        conn = _PG_POOL.get_nowait()
        # Test connection is still alive
        try:
            conn.run("SELECT 1")
            return conn
        except Exception:
            try: conn.close()
            except: pass
    except _queue.Empty:
        pass
    return _PGConn(**_parse_db_url(DATABASE_URL))

def _return_pool_conn(conn):
    """Return connection to pool, or close it if pool is full."""
    try:
        _PG_POOL.put_nowait(conn)
    except _queue.Full:
        try: conn.close()
        except: pass

def _raw_pg(sql, params=(), fetch=False):
    """Execute SQL via pooled pg8000 native connection, bypassing _DB wrapper.
    Returns rows if fetch=True, else None. Raises on error."""
    import re as _re
    pdict = {}
    idx = [0]
    def _rep(m):
        k = f"p{idx[0]}"
        pdict[k] = params[idx[0]] if idx[0] < len(params) else None
        idx[0] += 1
        return f":{k}"
    pg_sql = _re.sub(r"\?", _rep, sql)
    conn = _get_pool_conn()
    try:
        rows = conn.run(pg_sql, **pdict) if pdict else conn.run(pg_sql)
        cols = [c["name"] for c in (conn.columns or [])]
        result = [dict(zip(cols, r)) for r in (rows or [])] if fetch else None
        _return_pool_conn(conn)
        return result
    except Exception:
        # Don't return broken connections to pool
        try: conn.close()
        except: pass
        raise

def _run_ddl(sql):
    """Run a single DDL statement in its own fresh connection. Never raises."""
    try:
        conn = pg8000.native.Connection(**_parse_db_url(DATABASE_URL))
        try:
            # pg8000 native does NOT auto-wrap in transactions — DDL runs directly
            conn.run(sql)
            print(f"  [DDL OK] {sql[:80]!r}")
        except Exception as e:
            msg = str(e).lower()
            ok_msgs = ["already exists", "duplicate", "column already",
                       "relation already", "index already"]
            if any(x in msg for x in ok_msgs):
                print(f"  [DDL skip — already exists] {sql[:60]!r}")
            else:
                print(f"  [DDL WARN] {sql[:60]!r}: {type(e).__name__}: {e}")
        finally:
            try: conn.close()
            except: pass
    except Exception as e:
        print(f"  [DDL connect error] {e}")

def ensure_timelog_schema():
    """Ensure time_logs has ALL required columns. Safe to call repeatedly."""
    # Step 1: create minimal base table (id only — everything else added via ALTER)
    _run_ddl("""CREATE TABLE IF NOT EXISTS time_logs (
        id TEXT PRIMARY KEY)""")
    # Step 2: every column added individually — each gets its own fresh connection
    # This handles ANY state of the live DB regardless of when it was created
    for ddl in [
        "ALTER TABLE time_logs ADD COLUMN workspace_id TEXT DEFAULT ''",
        "ALTER TABLE time_logs ADD COLUMN user_id      TEXT DEFAULT ''",
        "ALTER TABLE time_logs ADD COLUMN team_id      TEXT DEFAULT ''",
        "ALTER TABLE time_logs ADD COLUMN date         TEXT DEFAULT ''",
        "ALTER TABLE time_logs ADD COLUMN task_name    TEXT DEFAULT ''",
        "ALTER TABLE time_logs ADD COLUMN project_id   TEXT DEFAULT ''",
        "ALTER TABLE time_logs ADD COLUMN task_id      TEXT DEFAULT ''",
        "ALTER TABLE time_logs ADD COLUMN hours        REAL DEFAULT 0",
        "ALTER TABLE time_logs ADD COLUMN minutes      INTEGER DEFAULT 0",
        "ALTER TABLE time_logs ADD COLUMN comments     TEXT DEFAULT ''",
        "ALTER TABLE time_logs ADD COLUMN created      TEXT DEFAULT ''",
        "ALTER TABLE workspaces ADD COLUMN required_hours_per_day REAL DEFAULT 8",
    ]:
        _run_ddl(ddl)


def get_user_role():
    """Fetch current user role from DB — role is not stored in session."""
    try:
        rows = _raw_pg("SELECT role FROM users WHERE id=?",
                       (session.get("user_id",""),), fetch=True)
        return rows[0]["role"] if rows else ""
    except Exception:
        return ""


def hash_pw(p):
    """Hash password with bcrypt (falls back to sha256 for legacy check)."""
    try:
        import bcrypt
        return bcrypt.hashpw(p.encode(), bcrypt.gensalt(rounds=12)).decode()
    except ImportError:
        return hashlib.sha256(p.encode()).hexdigest()

def verify_pw(plain, hashed):
    """Verify password — supports both bcrypt and legacy sha256 hashes."""
    try:
        import bcrypt
        if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
            return bcrypt.checkpw(plain.encode(), hashed.encode())
        return hashed == hashlib.sha256(plain.encode()).hexdigest()
    except ImportError:
        return hashed == hashlib.sha256(plain.encode()).hexdigest()

# ── OTP Store (in-memory, auto-expiring) ─────────────────────────────────────
import threading as _threading
_otp_store = {}   # {email: {"code": "123456", "expires": timestamp, "user_id": ..., "workspace_id": ...}}
_otp_lock = _threading.Lock()

def _otp_cleanup():
    """Remove expired OTPs every 2 minutes."""
    while True:
        import time as _time
        _time.sleep(120)
        now = _time.time()
        with _otp_lock:
            expired = [k for k, v in _otp_store.items() if v["expires"] < now]
            for k in expired:
                del _otp_store[k]

_threading.Thread(target=_otp_cleanup, daemon=True).start()

def generate_otp():
    """Generate a 6-digit OTP."""
    return str(secrets.randbelow(900000) + 100000)  # always 6 digits

def send_otp_email(to_email, otp_code, user_name):
    """Send OTP verification email."""
    subject = "VEWIT — Your Login Code"
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background:#f4f4f4; padding:20px;">
      <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
        <div style="background:#0a1a00;padding:24px 32px;text-align:center;">
          <h1 style="color:#5a8cff;margin:0;font-size:22px;letter-spacing:-0.5px;">VEWIT</h1>
        </div>
        <div style="padding:32px;">
          <h2 style="color:#111;margin:0 0 8px;">Hi {user_name},</h2>
          <p style="color:#555;margin:0 0 28px;">Use the code below to complete your sign-in. It expires in <b>10 minutes</b>.</p>
          <div style="text-align:center;margin:0 0 28px;">
            <div style="display:inline-block;background:#f0fff0;border:2px solid #5a8cff;border-radius:12px;padding:18px 36px;">
              <span style="font-size:38px;font-weight:800;letter-spacing:10px;color:#0a1a00;font-family:monospace;">{otp_code}</span>
            </div>
          </div>
          <p style="color:#888;font-size:13px;margin:0;">If you didn't request this code, you can safely ignore this email. Do not share this code with anyone.</p>
        </div>
        <div style="background:#f9f9f9;padding:14px 32px;text-align:center;border-top:1px solid #eee;">
          <p style="color:#aaa;font-size:11px;margin:0;">VEWIT · Team Project Management</p>
        </div>
      </div>
    </body>
    </html>
    """
    try:
        threading.Thread(target=send_email, args=(to_email, subject, body), daemon=True).start()
        return True
    except Exception as e:
        log.error("[OTP] Email send error: %s", e)
        return False
IST_OFFSET = timedelta(hours=5, minutes=30)

def now_ist():
    """Return current datetime in IST (UTC+5:30)."""
    return datetime.utcnow() + IST_OFFSET

def ts():
    """Return current IST time as ISO string with +05:30 offset."""
    return now_ist().strftime('%Y-%m-%dT%H:%M:%S') + '+05:30'


# ── Email Configuration & Function ────────────────────────────────────────────
EMAIL_ENABLED = os.environ.get('EMAIL_ENABLED', 'true').lower() == 'true'
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
FROM_EMAIL = os.environ.get('FROM_EMAIL', SMTP_USERNAME)
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
APP_URL = os.environ.get('APP_URL', 'http://localhost:5000')

def _send_via_resend(to_email, subject, body_html, from_email):
    """Send email via Resend HTTP API — works on Railway (no SMTP port blocking)."""
    if not RESEND_API_KEY:
        return False
    try:
        import json as _json
        payload = _json.dumps({
            "from": f"VEWIT <{from_email or 'noreply@vewit.in'}>",
            "to": [to_email],
            "subject": subject,
            "html": body_html
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = resp.read()
            log.info("[Resend] Sent to %s", to_email)
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error("[Resend] HTTP error %s", e.code)
        return False
    except Exception as e:
        log.error("[Resend] Error: %s: %s", type(e).__name__, e)
        return False

def send_email(to_email, subject, body_html, workspace_id=None):
    """Send email — tries Resend API first (works on Railway), falls back to SMTP."""
    from_addr = FROM_EMAIL or SMTP_USERNAME or 'noreply@vewit.in'

    # ── Try Resend API first (no port restrictions) ───────────────────────
    if RESEND_API_KEY:
        log.info("[Email] Using Resend API to %s", to_email)
        return _send_via_resend(to_email, subject, body_html, from_addr)

    # ── Fall back to workspace SMTP settings ──────────────────────────────
    smtp_config = None
    if workspace_id:
        try:
            with get_db() as db:
                ws = db.execute("""SELECT smtp_server, smtp_port, smtp_username, smtp_password,
                                   from_email, email_enabled FROM workspaces WHERE id=?""",
                                (workspace_id,)).fetchone()
                if ws and ws['email_enabled']:
                    smtp_config = {
                        'server': ws['smtp_server'],
                        'port': ws['smtp_port'] or 587,
                        'username': ws['smtp_username'],
                        'password': ws['smtp_password'],
                        'from_email': ws['from_email'] or ws['smtp_username']
                    }
        except Exception as e:
            log.error("[Email] Error loading config: %s", e)

    if not smtp_config or not smtp_config.get('username') or not smtp_config.get('password'):
        if not SMTP_USERNAME or not SMTP_PASSWORD:
            log.warning("[Email] Skipped (not configured): %s -> %s", subject, to_email)
            return False
        smtp_config = {
            'server': SMTP_SERVER,
            'port': SMTP_PORT,
            'username': SMTP_USERNAME,
            'password': SMTP_PASSWORD.replace(' ', '') if SMTP_PASSWORD else '',
            'from_email': FROM_EMAIL or SMTP_USERNAME
        }

    import traceback as _tb
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = smtp_config['from_email']
        msg['To'] = to_email
        msg.attach(MIMEText(body_html, 'html'))

        user = smtp_config['username']
        pwd  = (smtp_config['password'] or '').replace(' ', '')
        srv  = smtp_config['server']
        port = int(smtp_config['port'] or 587)
        print(f"[SMTP] >>> Connecting {srv}:{port} as {user} pwd_len={len(pwd)}")

        try:
            with smtplib.SMTP(srv, port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                print(f"[SMTP] >>> TLS OK, logging in...")
                server.login(user, pwd)
                print(f"[SMTP] >>> Login OK, sending to {to_email}...")
                server.send_message(msg)
        except Exception as inner_e:
            print(f"[SMTP] >>> STARTTLS failed ({type(inner_e).__name__}: {inner_e}), trying SSL:465...")
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            with smtplib.SMTP_SSL(srv, 465, timeout=30, context=ctx) as server:
                server.login(user, pwd)
                server.send_message(msg)

        log.info("[SMTP] Sent successfully to %s", to_email)
        return True
    except Exception as e:
        log.error("[SMTP] FINAL FAILURE to %s: %s: %s", to_email, type(e).__name__, e)
        _tb.print_exc()
        return False

def send_task_assigned_email(user_email, user_name, task_title, assigner_name, task_id, workspace_id):
    """Send email when a task is assigned"""
    subject = f"Task Assigned: {task_title}"
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #6366f1;">New Task Assignment</h2>
            <p>Hi {user_name},</p>
            <p><strong>{assigner_name}</strong> has assigned you to a new task:</p>
            <div style="background: #f3f4f6; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <h3 style="margin: 0 0 10px 0; color: #1f2937;">{task_title}</h3>
            </div>
            <p><a href="{APP_URL}" style="display: inline-block; background: #6366f1; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">View Task</a></p>
            <p style="color: #6b7280; font-size: 12px; margin-top: 30px;">VEWIT Notification System</p>
        </div>
    </body>
    </html>
    """
    send_email(user_email, subject, body, workspace_id)

def send_status_change_email(user_email, user_name, task_title, new_stage, changer_name, workspace_id):
    """Send email when task status changes"""
    subject = f"Task Status Updated: {task_title}"
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #10b981;">Task Status Changed</h2>
            <p>Hi {user_name},</p>
            <p><strong>{changer_name}</strong> has updated the status of your task:</p>
            <div style="background: #f3f4f6; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <h3 style="margin: 0 0 10px 0; color: #1f2937;">{task_title}</h3>
                <p style="margin: 0;"><strong>New Status:</strong> <span style="color: #10b981; font-weight: bold;">{new_stage}</span></p>
            </div>
            <p><a href="{APP_URL}" style="display: inline-block; background: #10b981; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">View Task</a></p>
            <p style="color: #6b7280; font-size: 12px; margin-top: 30px;">VEWIT Notification System</p>
        </div>
    </body>
    </html>
    """
    send_email(user_email, subject, body, workspace_id)

def send_comment_email(user_email, user_name, task_title, commenter_name, comment_text, workspace_id):
    """Send email when someone comments on a task"""
    subject = f"New Comment on: {task_title}"
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #f59e0b;">New Comment</h2>
            <p>Hi {user_name},</p>
            <p><strong>{commenter_name}</strong> commented on your task:</p>
            <div style="background: #f3f4f6; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <h3 style="margin: 0 0 10px 0; color: #1f2937;">{task_title}</h3>
                <div style="background: white; padding: 10px; border-left: 3px solid #f59e0b; margin-top: 10px;">
                    <p style="margin: 0;">{comment_text}</p>
                </div>
            </div>
            <p><a href="{APP_URL}" style="display: inline-block; background: #f59e0b; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">View Comment</a></p>
            <p style="color: #6b7280; font-size: 12px; margin-top: 30px;">VEWIT Notification System</p>
        </div>
    </body>
    </html>
    """
    send_email(user_email, subject, body, workspace_id)

# ── Web Push (VAPID) ──────────────────────────────────────────────────────────
VAPID_KEY_FILE = os.path.join(DATA_DIR, ".pf_vapid")

def get_vapid_keys():
    """Load or generate VAPID key pair (raw bytes stored as hex)."""
    if os.path.exists(VAPID_KEY_FILE):
        try:
            with open(VAPID_KEY_FILE, "r") as f:
                d = json.load(f)
                if d.get("private") and d.get("public"):
                    return d
        except: pass
    try:
        import struct
        priv_bytes = os.urandom(32)
        priv_hex = priv_bytes.hex()
        keys = {"private": priv_hex, "public": "", "generated": ts()}
        try:
            from cryptography.hazmat.primitives.asymmetric.ec import (
                generate_private_key, SECP256R1, EllipticCurvePublicKey)
            from cryptography.hazmat.primitives.serialization import (
                Encoding, PublicFormat, PrivateFormat, NoEncryption)
            import base64
            ec_key = generate_private_key(SECP256R1())
            pub_bytes = ec_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
            priv_bytes2 = ec_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
            keys = {
                "private": base64.urlsafe_b64encode(priv_bytes2).decode(),
                "public": base64.urlsafe_b64encode(pub_bytes).decode().rstrip("="),
                "generated": ts()
            }
        except ImportError:
            pass
        with open(VAPID_KEY_FILE, "w") as f:
            json.dump(keys, f)
        return keys
    except Exception as e:
        log.error("[VAPID] Key generation error: %s", e)
        return {"private": "", "public": ""}

def send_web_push(subscription_info, payload_dict):
    """Send a Web Push notification. Requires pywebpush."""
    try:
        from pywebpush import webpush, WebPushException
        vapid = get_vapid_keys()
        if not vapid.get("private") or not vapid.get("public"):
            return False
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload_dict),
            vapid_private_key=vapid["private"],
            vapid_claims={"sub": "mailto:admin@projectflow.app"}
        )
        return True
    except ImportError:
        return False  # pywebpush not installed — fall back to polling
    except Exception as e:
        log.error("[WebPush] Error: %s", e)
        return False

def push_notification_to_user(db_ignored, user_id, title, body, nav_url="/", tag=None):
    """Send Web Push to all subscriptions for a given user (uses connection pool for thread safety)."""
    try:
        subs = _raw_pg(
            "SELECT * FROM push_subscriptions WHERE user_id=?", (user_id,), fetch=True
        )
    except Exception as e:
        print(f"push_notification DB error: {e}")
        return
    payload = {"title": title, "body": body, "url": nav_url, "tag": tag or title}
    dead_ids = []
    for sub in (subs or []):
        sub_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}
        }
        ok = send_web_push(sub_info, payload)
        if not ok and sub["endpoint"]:
            dead_ids.append(sub["id"])
    if dead_ids:
        placeholders = ",".join("?" * len(dead_ids))
        _raw_pg(f"DELETE FROM push_subscriptions WHERE id IN ({placeholders})", tuple(dead_ids))

# ── DB Init & Migration ───────────────────────────────────────────────────────
def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with get_db(autocommit=True) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY, name TEXT, invite_code TEXT,
                owner_id TEXT, ai_api_key TEXT, created TEXT,
                smtp_server TEXT, smtp_port INTEGER, smtp_username TEXT,
                smtp_password TEXT, from_email TEXT, email_enabled INTEGER DEFAULT 1,
                otp_enabled INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, email TEXT,
                password TEXT, role TEXT, avatar TEXT, color TEXT, created TEXT,
                two_fa_enabled INTEGER DEFAULT 0, totp_secret TEXT DEFAULT '',
                totp_verified INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, description TEXT,
                owner TEXT, members TEXT DEFAULT '[]', start_date TEXT,
                target_date TEXT, progress INTEGER DEFAULT 0, color TEXT, created TEXT,
                team_id TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, workspace_id TEXT, title TEXT, description TEXT,
                project TEXT, assignee TEXT, priority TEXT, stage TEXT,
                created TEXT, due TEXT, pct INTEGER DEFAULT 0, comments TEXT DEFAULT '[]',
                team_id TEXT DEFAULT '', parent_id TEXT DEFAULT '',
                story_points INTEGER DEFAULT 0, sprint TEXT DEFAULT '',
                task_type TEXT DEFAULT 'task', labels TEXT DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS subtasks (
                id TEXT PRIMARY KEY, workspace_id TEXT, task_id TEXT,
                title TEXT, done INTEGER DEFAULT 0, assignee TEXT DEFAULT '',
                created TEXT);
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, size INTEGER,
                mime TEXT, task_id TEXT, project_id TEXT, uploaded_by TEXT, ts TEXT);
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, workspace_id TEXT, sender TEXT,
                project TEXT, content TEXT, ts TEXT);
            CREATE TABLE IF NOT EXISTS direct_messages (
                id TEXT PRIMARY KEY, workspace_id TEXT, sender TEXT,
                recipient TEXT, content TEXT, read INTEGER DEFAULT 0, ts TEXT);
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY, workspace_id TEXT, type TEXT, content TEXT,
                user_id TEXT, read INTEGER DEFAULT 0, ts TEXT);
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY, workspace_id TEXT, user_id TEXT,
                task_id TEXT, task_title TEXT, remind_at TEXT,
                minutes_before INTEGER DEFAULT 10, fired INTEGER DEFAULT 0,
                created TEXT);
            CREATE TABLE IF NOT EXISTS call_rooms (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
                initiator TEXT, participants TEXT DEFAULT '[]',
                status TEXT DEFAULT 'active', created TEXT);
            CREATE TABLE IF NOT EXISTS teams (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
                lead_id TEXT, member_ids TEXT DEFAULT '[]', created TEXT);
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY, workspace_id TEXT, title TEXT, description TEXT,
                type TEXT DEFAULT 'bug', priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'open', assignee TEXT, reporter TEXT,
                project TEXT, tags TEXT DEFAULT '[]', created TEXT, updated TEXT,
                team_id TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS ticket_comments (
                id TEXT PRIMARY KEY, workspace_id TEXT, ticket_id TEXT,
                user_id TEXT, content TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS call_signals (
                id TEXT PRIMARY KEY, workspace_id TEXT, room_id TEXT,
                from_user TEXT, to_user TEXT, type TEXT, data TEXT,
                consumed INTEGER DEFAULT 0, created TEXT);
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id TEXT PRIMARY KEY, user_id TEXT, workspace_id TEXT,
                endpoint TEXT UNIQUE, p256dh TEXT, auth TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS time_logs (
                id TEXT PRIMARY KEY, workspace_id TEXT, user_id TEXT,
                team_id TEXT DEFAULT '', date TEXT, task_name TEXT,
                project_id TEXT DEFAULT '', task_id TEXT DEFAULT '',
                hours REAL DEFAULT 0, minutes INTEGER DEFAULT 0,
                comments TEXT DEFAULT '', created TEXT);
            CREATE INDEX IF NOT EXISTS idx_tasks_ws        ON tasks(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_assignee  ON tasks(workspace_id, assignee);
            CREATE INDEX IF NOT EXISTS idx_tasks_project   ON tasks(workspace_id, project);
            CREATE INDEX IF NOT EXISTS idx_tasks_stage     ON tasks(workspace_id, stage);
            CREATE INDEX IF NOT EXISTS idx_notifs_user     ON notifications(workspace_id, user_id, read);
            CREATE INDEX IF NOT EXISTS idx_dm_recipient    ON direct_messages(workspace_id, recipient, read);
            CREATE INDEX IF NOT EXISTS idx_messages_proj   ON messages(workspace_id, project);
            CREATE INDEX IF NOT EXISTS idx_timelogs_user   ON time_logs(workspace_id, user_id);
            CREATE INDEX IF NOT EXISTS idx_timelogs_date   ON time_logs(workspace_id, date);
            CREATE INDEX IF NOT EXISTS idx_reminders_user  ON reminders(workspace_id, user_id, fired);
            CREATE INDEX IF NOT EXISTS idx_tickets_ws      ON tickets(workspace_id, status);
            CREATE TABLE IF NOT EXISTS task_events (
                id TEXT PRIMARY KEY, workspace_id TEXT, task_id TEXT,
                user_id TEXT, event_type TEXT, old_val TEXT DEFAULT '',
                new_val TEXT DEFAULT '', ts TEXT);
            CREATE INDEX IF NOT EXISTS idx_task_events ON task_events(task_id, ts);
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                admin_email TEXT DEFAULT '',
                action TEXT DEFAULT '',
                target TEXT DEFAULT '',
                detail TEXT DEFAULT '',
                created TEXT DEFAULT '');
            CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created);
        """)
        # ── Consolidated migrations (safe — each wrapped in try/except) ──────
        for stmt in [
            "ALTER TABLE projects ADD COLUMN team_id TEXT DEFAULT ''",
            "ALTER TABLE tickets ADD COLUMN team_id TEXT DEFAULT ''",
            "ALTER TABLE tasks ADD COLUMN team_id TEXT DEFAULT ''",
            "ALTER TABLE messages ADD COLUMN is_system INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN avatar_data TEXT",
            "ALTER TABLE users ADD COLUMN plain_password TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN two_fa_enabled INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN totp_secret TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN totp_verified INTEGER DEFAULT 0",
            "ALTER TABLE tasks ADD COLUMN parent_id TEXT DEFAULT ''",
            "ALTER TABLE tasks ADD COLUMN story_points INTEGER DEFAULT 0",
            "ALTER TABLE tasks ADD COLUMN sprint TEXT DEFAULT ''",
            "ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'task'",
            "ALTER TABLE tasks ADD COLUMN labels TEXT DEFAULT '[]'",
            "ALTER TABLE workspaces ADD COLUMN otp_enabled INTEGER DEFAULT 0",
            "ALTER TABLE workspaces ADD COLUMN dm_enabled INTEGER DEFAULT 1",
            "ALTER TABLE workspaces ADD COLUMN smtp_server TEXT",
            "ALTER TABLE workspaces ADD COLUMN smtp_port INTEGER DEFAULT 587",
            "ALTER TABLE workspaces ADD COLUMN smtp_username TEXT",
            "ALTER TABLE workspaces ADD COLUMN smtp_password TEXT",
            "ALTER TABLE workspaces ADD COLUMN from_email TEXT",
            "ALTER TABLE workspaces ADD COLUMN email_enabled INTEGER DEFAULT 1",
            "ALTER TABLE call_rooms ADD COLUMN invited_users TEXT DEFAULT '[]'",
            "ALTER TABLE notifications ADD COLUMN sender_id TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN last_active TEXT DEFAULT ''",
            "CREATE TABLE IF NOT EXISTS time_logs (id TEXT PRIMARY KEY, workspace_id TEXT, user_id TEXT, team_id TEXT DEFAULT '', date TEXT, task_name TEXT, project_id TEXT DEFAULT '', task_id TEXT DEFAULT '', hours REAL DEFAULT 0, minutes INTEGER DEFAULT 0, comments TEXT DEFAULT '', created TEXT)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_ws ON tasks(workspace_id)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(workspace_id, assignee)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_stage ON tasks(workspace_id, stage)",
            "CREATE INDEX IF NOT EXISTS idx_notifs_user ON notifications(workspace_id, user_id, read)",
            "CREATE INDEX IF NOT EXISTS idx_timelogs_user ON time_logs(workspace_id, user_id)",
            "CREATE INDEX IF NOT EXISTS idx_timelogs_date ON time_logs(workspace_id, date)",
            "CREATE TABLE IF NOT EXISTS task_events (id TEXT PRIMARY KEY, workspace_id TEXT, task_id TEXT, user_id TEXT, event_type TEXT, old_val TEXT DEFAULT \'\', new_val TEXT DEFAULT \'\', ts TEXT)",
            "CREATE INDEX IF NOT EXISTS idx_task_events ON task_events(task_id, ts)",
            "ALTER TABLE time_logs ADD COLUMN project_id TEXT DEFAULT ''",
            "ALTER TABLE time_logs ADD COLUMN task_id TEXT DEFAULT ''",
            "ALTER TABLE workspaces ADD COLUMN required_hours_per_day REAL DEFAULT 8",
            "CREATE TABLE IF NOT EXISTS vault_cards (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT DEFAULT '', tags TEXT DEFAULT '', rows TEXT DEFAULT '[]', cols TEXT DEFAULT '[]', lock_hash TEXT DEFAULT '', created TEXT, updated TEXT)",
            "CREATE INDEX IF NOT EXISTS idx_vault_cards_user ON vault_cards(user_id)",
            "ALTER TABLE vault_cards ADD COLUMN cols TEXT DEFAULT '[]'",
            "CREATE TABLE IF NOT EXISTS vault_audit_log (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, card_id TEXT NOT NULL, action TEXT NOT NULL, detail TEXT DEFAULT '', ip TEXT DEFAULT '', created TEXT)",
            "CREATE INDEX IF NOT EXISTS idx_vault_audit_user ON vault_audit_log(user_id, created)",
            "CREATE INDEX IF NOT EXISTS idx_vault_audit_card ON vault_audit_log(card_id)",
            "ALTER TABLE workspaces ADD COLUMN plan TEXT DEFAULT 'starter'",
            "ALTER TABLE workspaces ADD COLUMN suspended INTEGER DEFAULT 0",
            "ALTER TABLE audit_log ADD COLUMN ip TEXT DEFAULT ''",
            # Performance indexes missing from original schema
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
            "CREATE INDEX IF NOT EXISTS idx_users_workspace ON users(workspace_id)",
            "CREATE INDEX IF NOT EXISTS idx_tickets_assignee ON tickets(workspace_id, assignee)",
            "CREATE INDEX IF NOT EXISTS idx_dm_sender ON direct_messages(workspace_id, sender)",
            "CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)",
            "CREATE INDEX IF NOT EXISTS idx_audit_log_target ON audit_log(target)",
            # Soft-delete support
            "ALTER TABLE users ADD COLUMN deleted_at TEXT DEFAULT ''",
            "ALTER TABLE projects ADD COLUMN deleted_at TEXT DEFAULT ''",
            "ALTER TABLE tasks ADD COLUMN deleted_at TEXT DEFAULT ''",
        ]:
            try: db.execute(stmt)
            except: pass
        try:
            corrupted = db.execute("SELECT id, name, avatar FROM users WHERE avatar LIKE 'data:image%%' OR (length(avatar) > 10 AND avatar !~ '^[A-Z]{1,2}$')").fetchall()
            for row in corrupted:
                uid, name, av = row['id'], row['name'] or '', row['avatar'] or ''
                initials = ''.join(w[0] for w in name.split() if w)[:2].upper() or '?'
                if av.startswith('data:image'):
                    db.execute("UPDATE users SET avatar=?, avatar_data=? WHERE id=?", (initials, av, uid))
                else:
                    db.execute("UPDATE users SET avatar=? WHERE id=?", (initials, uid))
        except Exception as e:
            print(f"Avatar cleanup migration error: {e}")
        try: db.execute("""CREATE TABLE IF NOT EXISTS subtasks (
            id TEXT PRIMARY KEY, workspace_id TEXT, task_id TEXT,
            title TEXT, done INTEGER DEFAULT 0, assignee TEXT DEFAULT '', created TEXT)""")
        except: pass
        existing_ws = db.execute("SELECT id FROM workspaces LIMIT 1").fetchone()
        if not existing_ws:
            legacy_users = db.execute("SELECT id FROM users WHERE workspace_id IS NULL LIMIT 1").fetchone()
            ws_id = f"ws{int(datetime.now().timestamp()*1000)}"
            invite = secrets.token_hex(4).upper()
            db.execute("INSERT OR IGNORE INTO workspaces VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (ws_id,"Demo Workspace",invite,"u1",None,ts(),None,587,None,None,None,1))
            if legacy_users:
                for tbl in ["users","projects","tasks","files","messages","direct_messages","notifications"]:
                    try: db.execute(f"UPDATE {tbl} SET workspace_id=? WHERE workspace_id IS NULL",(ws_id,))
                    except: pass
            else:
                _seed_demo(db, ws_id)

def _seed_demo(db, ws_id):
    for u in [
        ("u1","Alice Chen",  "alice@dev.io",hash_pw("pass123"),"Admin",    "AC","#7c3aed"),
        ("u2","Bob Martinez","bob@dev.io",  hash_pw("pass123"),"Developer","BM","#2563eb"),
        ("u3","Carol Smith", "carol@dev.io",hash_pw("pass123"),"Tester",   "CS","#059669"),
        ("u4","David Kim",   "david@dev.io",hash_pw("pass123"),"Developer","DK","#d97706"),
        ("u5","Eva Wilson",  "eva@dev.io",  hash_pw("pass123"),"Viewer",   "EW","#dc2626"),
    ]:
        try: db.execute("INSERT INTO users(id,workspace_id,name,email,password,role,avatar,color,created,two_fa_enabled) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (u[0],ws_id,u[1],u[2],u[3],u[4],u[5],u[6],ts(),0))
        except: pass
    for p in [
        ("p1","E-Commerce Platform",   "Modern e-commerce with payment integration & inventory.",       "u1",'["u1","u2","u3","u4"]',"2025-01-15","2025-06-30",65,"#7c3aed"),
        ("p2","Mobile Banking App",    "Secure mobile banking with biometric auth & real-time transfers.","u2",'["u1","u2","u5"]',     "2025-02-01","2025-08-15",40,"#2563eb"),
        ("p3","AI Analytics Dashboard","Real-time analytics powered by ML for business intelligence.",   "u1",'["u1","u3","u4"]',     "2025-03-01","2025-09-30",20,"#059669"),
    ]:
        try: db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?)",(p[0],ws_id,*p[1:],ts()))
        except: pass
    for t in [
        ("T-001","Design system setup",        "Configure design tokens and component library.",       "p1","u2","high",  "completed",  "2025-02-15",100),
        ("T-002","User authentication API",    "JWT auth with refresh tokens.",                       "p1","u2","high",  "production", "2025-03-01",100),
        ("T-003","Product catalog UI",         "Product listing, filtering and search.",              "p1","u4","medium","development","2025-04-30", 60),
        ("T-004","Payment gateway integration","Stripe integration with webhooks.",                   "p1","u2","high",  "code_review","2025-05-15", 80),
        ("T-005","Cart & checkout flow",       "Shopping cart with multi-step checkout.",             "p1","u4","high",  "testing",    "2025-05-30", 70),
        ("T-006","Inventory management",       "Stock tracking and bulk import.",                     "p1","u2","medium","planning",   "2025-06-15", 10),
        ("T-007","Performance testing",        "Load testing and optimization.",                      "p1","u3","medium","backlog",    "2025-06-25",  0),
        ("T-008","Biometric auth flow",        "Face ID and fingerprint auth.",                       "p2","u2","high",  "development","2025-04-30", 55),
        ("T-009","Real-time transfers",        "WebSocket transfer notifications.",                   "p2","u2","high",  "planning",   "2025-05-30", 20),
        ("T-010","Security audit",             "Penetration testing and compliance.",                 "p2","u3","high",  "backlog",    "2025-07-15",  0),
        ("T-011","ML model integration",       "Connect ML models via REST API.",                     "p3","u4","high",  "development","2025-07-30", 25),
        ("T-012","Chart components",           "Interactive visualization components.",               "p3","u4","medium","code_review","2025-06-15", 85),
        ("T-013","Data pipeline setup",        "ETL pipeline for real-time data ingestion.",          "p3","u2","high",  "blocked",    "2025-06-01", 30),
    ]:
        try: db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(t[0],ws_id,t[1],t[2],t[3],t[4],t[5],t[6],ts(),t[7],t[8],"[]"))
        except: pass
    for m in [
        ("m1","u2","p1","Just pushed the auth API to staging!"),
        ("m2","u3","p1","Running test suite, will report results."),
        ("m3","u4","p1","@alice Can you review the product catalog PR?"),
        ("m4","u1","p1","Sure! Checking it after standup."),
    ]:
        try: db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",(m[0],ws_id,m[1],m[2],m[3],ts()))
        except: pass
    for n in [
        ("n1","task_assigned","You have been assigned to Cart & checkout flow","u4",0),
        ("n2","status_change","Task Payment gateway moved to Code Review","u2",0),
        ("n3","comment","Bob commented on Product catalog UI","u4",1),
    ]:
        try: db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",(n[0],ws_id,n[1],n[2],n[3],n[4],ts()))
        except: pass

def login_required(f):
    @wraps(f)
    def d(*a,**kw):
        if "user_id" not in session: return jsonify({"error":"Unauthorized"}),401
        return f(*a,**kw)
    return d

def wid(): return session.get("workspace_id","")

# ── Auth ──────────────────────────────────────────────────────────────────────

# ── Login rate limiter (brute-force protection) ───────────────────────────────
import time as _time_mod
_login_attempts = {}   # {key: [timestamp, ...]}
_LOGIN_MAX = 5         # max attempts
_LOGIN_WINDOW = 60     # seconds

def _check_rate_limit(key):
    """Return (allowed, seconds_until_reset). Cleans up old entries."""
    now = _time_mod.time()
    attempts = [t for t in _login_attempts.get(key, []) if now - t < _LOGIN_WINDOW]
    _login_attempts[key] = attempts
    if len(attempts) >= _LOGIN_MAX:
        wait = int(_LOGIN_WINDOW - (now - attempts[0]))
        return False, max(1, wait)
    return True, 0

def _record_attempt(key):
    _login_attempts.setdefault(key, []).append(_time_mod.time())

def _clear_attempts(key):
    _login_attempts.pop(key, None)

@app.route("/api/auth/login",methods=["POST"])
def login():
    d=request.json or {}
    email=d.get("email","").strip().lower()
    password=d.get("password","")
    # Rate-limit: block brute force after 5 wrong attempts per 60s
    rl_key = f"login:{request.remote_addr}:{email}"
    allowed, wait = _check_rate_limit(rl_key)
    if not allowed:
        return jsonify({"error": f"Too many attempts. Try again in {wait}s."}), 429
    with get_db() as db:
        u=db.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        if not u:
            _record_attempt(rl_key)
            return jsonify({"error":"Invalid email or password"}),401
        if not verify_pw(password, u["password"]):
            _record_attempt(rl_key)
            return jsonify({"error":"Invalid email or password"}),401
        # Upgrade legacy sha256 hash to bcrypt
        if not (u["password"].startswith("$2b$") or u["password"].startswith("$2a$")):
            try:
                new_hash = hash_pw(password)
                db.execute("UPDATE users SET password=? WHERE id=?",(new_hash, u["id"]))
            except Exception: pass
        # ── Google Authenticator (TOTP) — only 2FA method ────────────────────
        totp_active = u.get("totp_verified") and u.get("totp_secret")
        if totp_active:
            return jsonify({"totp_required": True, "user_id": u["id"], "name": u["name"]}), 200
        # ── No 2FA configured — direct login ─────────────────────────────────
        _clear_attempts(rl_key)  # reset limiter on success
        session.permanent=True
        session["user_id"]=u["id"]
        session["workspace_id"]=u["workspace_id"]
        session["role"]=u.get("role","")  # cache role in session
        try:
            db.execute("UPDATE users SET last_active=? WHERE id=?", (ts(), u["id"]))
        except Exception: pass
        _audit("user_login", u["id"], f"{u['name']} ({email}) logged in")
        result = dict(u)
        result.pop("totp_secret", None)
        result.pop("password", None)
        result.pop("avatar_data", None)
        return jsonify(result)

# ── Email OTP routes kept as stubs (for backward compat) but not used in login
@app.route("/api/auth/verify-otp",methods=["POST"])
def verify_otp():
    return jsonify({"error":"Email OTP is disabled. Use Google Authenticator."}),410

@app.route("/api/auth/resend-otp",methods=["POST"])
def resend_otp():
    return jsonify({"error":"Email OTP is disabled. Use Google Authenticator."}),410

@app.route("/api/auth/toggle-2fa",methods=["POST"])
@login_required
def toggle_user_2fa():
    """Toggle email 2FA flag (legacy — TOTP is the primary 2FA method now)."""
    d = request.json or {}
    target_id = d.get("user_id", session["user_id"])
    enabled = bool(d.get("enabled", False))
    with get_db() as db:
        caller = db.execute("SELECT role FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if target_id != session["user_id"] and (not caller or caller["role"] not in ("Admin","Manager")):
            return jsonify({"error": "Only admins can change 2FA for other users"}), 403
        db.execute("UPDATE users SET two_fa_enabled=? WHERE id=? AND workspace_id=?",
                   (1 if enabled else 0, target_id, wid()))
        u = db.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
        result = dict(u) if u else {}
        result.pop("password", None)
        result.pop("totp_secret", None)
        result.pop("avatar_data", None)
        return jsonify(result)

@app.route("/api/auth/2fa-status")
@login_required
def get_2fa_status():
    """Return 2FA status for all users (admin) or just current user."""
    with get_db() as db:
        caller = db.execute("SELECT role FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if caller and caller["role"] == "Admin":
            rows = db.execute(
                "SELECT id, name, email, role, color, two_fa_enabled, totp_secret, totp_verified FROM users WHERE workspace_id=? ORDER BY name",
                (wid(),)).fetchall()
        else:
            rows = db.execute(
                "SELECT id, name, email, role, color, two_fa_enabled, totp_secret, totp_verified FROM users WHERE id=?",
                (session["user_id"],)).fetchall()
        result = []
        for r in rows:
            d2 = dict(r)
            d2["totp_configured"] = bool(d2.get("totp_secret") and d2.get("totp_verified"))
            d2.pop("totp_secret", None)  # never expose secret over API
            result.append(d2)
        return jsonify(result)

# ── TOTP / Google Authenticator ───────────────────────────────────────────────
def _totp_generate_secret():
    """Generate a random base32 TOTP secret."""
    import base64
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode().rstrip('=')

def _totp_hotp(key_b32, counter):
    """HOTP: HMAC-based OTP (RFC 4226)."""
    import hmac, hashlib, struct
    # Pad base32 to multiple of 8
    pad = (8 - len(key_b32) % 8) % 8
    key = base64.b32decode(key_b32 + '=' * pad, casefold=True)
    msg = struct.pack('>Q', counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0f
    code = (struct.unpack('>I', h[offset:offset+4])[0] & 0x7fffffff) % 1000000
    return f"{code:06d}"

import base64 as _b64_mod, struct as _struct_mod

def _totp_verify(secret, token, window=1):
    """Verify TOTP token with ±window steps (30s each)."""
    import time as _t
    counter = int(_t.time()) // 30
    for delta in range(-window, window + 1):
        expected = _totp_hotp(secret, counter + delta)
        if expected == token.strip():
            return True
    return False

def _totp_qr_url(secret, email, issuer="VEWIT"):
    """Generate otpauth:// URL for QR code rendering."""
    import urllib.parse
    pad = (8 - len(secret) % 8) % 8
    secret_padded = secret + '=' * pad
    params = urllib.parse.urlencode({"secret": secret_padded, "issuer": issuer})
    return f"otpauth://totp/{urllib.parse.quote(issuer)}:{urllib.parse.quote(email)}?{params}"

# ── Pure-Python QR Code generator (no external deps) ─────────────────────────
def _qr_make_matrix(data: str):
    """Generate a QR code matrix for the given string using only stdlib.
    Returns a list-of-lists of booleans (True = dark module)."""
    # We use a minimal QR encoder: Version 3, Error Correction M, byte mode.
    # This handles URLs up to ~77 bytes which covers any otpauth:// URL.
    import struct, math

    # ── Reed-Solomon GF(256) over x^8+x^4+x^3+x^2+1 (QR primitive poly) ────
    GF_EXP = [0]*512; GF_LOG = [0]*256
    x = 1
    for i in range(255):
        GF_EXP[i] = x; GF_LOG[x] = i
        x = x << 1
        if x & 0x100: x ^= 0x11d
    for i in range(255,512): GF_EXP[i] = GF_EXP[i-255]

    def gf_mul(a,b):
        if a==0 or b==0: return 0
        return GF_EXP[GF_LOG[a]+GF_LOG[b]]

    def rs_poly_mul(p,q):
        r = [0]*(len(p)+len(q)-1)
        for i,a in enumerate(p):
            for j,b in enumerate(q):
                r[i+j] ^= gf_mul(a,b)
        return r

    def rs_generator(n):
        g = [1]
        for i in range(n):
            g = rs_poly_mul(g,[1, GF_EXP[i]])
        return g

    def rs_encode(msg_poly, n_ec):
        gen = rs_generator(n_ec)
        rem = list(msg_poly) + [0]*n_ec
        for i in range(len(msg_poly)):
            c = rem[i]
            if c:
                for j,b in enumerate(gen):
                    rem[i+j] ^= gf_mul(b,c)
        return rem[len(msg_poly):]

    # ── Select version (auto: try 2,3,4,5 with EC M) ─────────────────────────
    data_b = data.encode('iso-8859-1') if all(ord(c)<256 for c in data) else data.encode('utf-8')
    n = len(data_b)
    # Version capacities for EC M, byte mode (data codewords, ec codewords per block, blocks)
    VERS = [
        (1, 16, 10, 1),  (2, 28, 16, 1),  (3, 44, 26, 1),
        (4, 64, 18, 2),  (5, 86, 24, 2),  (6, 108,16, 4),
        (7, 124,18, 4),  (8, 154,22, 2),  (9, 182,22, 3),
        (10,216,26, 4),
    ]
    ver_info = None
    for v,cap,ec_per,blk in VERS:
        if n <= cap - 3:  # 2 byte mode indicator + length byte
            ver_info = (v, cap, ec_per, blk); break
    if not ver_info:
        raise ValueError(f"Data too long for QR ({n} bytes)")
    version, data_cap, ec_per_block, num_blocks = ver_info
    size = version*4 + 17

    # ── Build data bit stream ─────────────────────────────────────────────────
    bits = []
    def add_bits(val, count):
        for i in range(count-1,-1,-1): bits.append((val>>i)&1)

    add_bits(0b0100, 4)   # byte mode
    char_count_bits = 8 if version < 10 else 16
    add_bits(n, char_count_bits)
    for byte in data_b: add_bits(byte, 8)
    add_bits(0, 4)  # terminator
    while len(bits)%8: bits.append(0)

    # Convert to codewords and pad
    codewords = [int(''.join(str(b) for b in bits[i:i+8]),2) for i in range(0,len(bits),8)]
    PAD = [0xEC,0x11]
    total_data = data_cap
    while len(codewords) < total_data: codewords.append(PAD[len(codewords)%2])
    codewords = codewords[:total_data]

    # ── Reed-Solomon error correction ─────────────────────────────────────────
    block_size = total_data // num_blocks
    ec_blocks = []; data_blocks = []
    for b in range(num_blocks):
        blk = codewords[b*block_size:(b+1)*block_size]
        data_blocks.append(blk)
        ec_blocks.append(rs_encode(blk, ec_per_block))

    # Interleave
    final_cw = []
    max_d = max(len(b) for b in data_blocks)
    for i in range(max_d):
        for b in data_blocks:
            if i < len(b): final_cw.append(b[i])
    max_e = max(len(b) for b in ec_blocks)
    for i in range(max_e):
        for b in ec_blocks:
            if i < len(b): final_cw.append(b[i])

    # ── Build QR matrix ───────────────────────────────────────────────────────
    DARK=True; LIGHT=False
    mat  = [[LIGHT]*size for _ in range(size)]
    used = [[False]*size for _ in range(size)]

    def place(r,c,v):
        if 0<=r<size and 0<=c<size:
            mat[r][c]=v; used[r][c]=True

    def reserve(r,c):
        if 0<=r<size and 0<=c<size: used[r][c]=True

    def finder(tr,tc):
        for r in range(7):
            for c in range(7):
                v = (r in (0,6)) or (c in (0,6)) or (2<=r<=4 and 2<=c<=4)
                place(tr+r, tc+c, v)
        # Separator
        for i in range(8):
            place(tr+7,tc+i,LIGHT); place(tr+i,tc+7,LIGHT)
            reserve(tr+7,tc+i); reserve(tr+i,tc+7)

    finder(0,0); finder(0,size-7); finder(size-7,0)

    # Timing patterns
    for i in range(8, size-8):
        v = i%2==0
        place(6,i,v); place(i,6,v)

    # Dark module
    place(size-8,8,DARK)

    # Alignment patterns (version >= 2)
    ALIGN_POS = {2:[6,18],3:[6,22],4:[6,26],5:[6,30],6:[6,34],
                 7:[6,22,38],8:[6,24,42],9:[6,26,46],10:[6,28,50]}
    if version in ALIGN_POS:
        pos = ALIGN_POS[version]
        for r in pos:
            for c in pos:
                if used[r][c]: continue
                for dr in range(-2,3):
                    for dc in range(-2,3):
                        v = abs(dr)==2 or abs(dc)==2 or (dr==0 and dc==0)
                        place(r+dr,c+dc,v)

    # Reserve format info areas
    for i in range(9):
        reserve(8,i); reserve(i,8)
    for i in range(size-8,size):
        reserve(8,i); reserve(i,8)

    # ── Place data bits (zigzag) ──────────────────────────────────────────────
    all_bits = []
    for cw in final_cw:
        for i in range(7,-1,-1): all_bits.append((cw>>i)&1)
    # Remainder bits
    REM = [0,7,7,7,7,7,0,0,0,0,0,0,0,3,3,3,3,3,3,3,4,4,4,4,4,4,4,3,3,3,3,3,3]
    all_bits += [0]*REM[version-1]

    bi = 0
    col = size-1
    while col > 0:
        if col == 6: col -= 1
        up = True
        row_range = range(size-1,-1,-1) if up else range(size)
        rows = list(range(size-1,-1,-1))
        going_up = True
        for r in (range(size-1,-1,-1) if going_up else range(size)):
            for dc in range(2):
                c = col-dc
                if not used[r][c]:
                    if bi < len(all_bits):
                        mat[r][c] = bool(all_bits[bi]); bi+=1
        col -= 2

    # ── Apply mask 0 (checkerboard) ───────────────────────────────────────────
    for r in range(size):
        for c in range(size):
            if not used[r][c] and (r+c)%2==0:
                mat[r][c] = not mat[r][c]

    # ── Write format information (EC M, mask 0) ───────────────────────────────
    # Format = EC_M(01) + mask_0(000) = 0b01_000 = 8
    # BCH encoded: precomputed for EC=M, mask=0 → 0x7973 XOR 0x5412 = 0x2D61
    fmt = 0x2D61  # precomputed format string for EC=M, mask pattern 0
    fmt_bits = [(fmt>>i)&1 for i in range(14,-1,-1)]
    # Place format bits around finders
    pos1 = [(8,0),(8,1),(8,2),(8,3),(8,4),(8,5),(8,7),(8,8),(7,8),(5,8),(4,8),(3,8),(2,8),(1,8),(0,8)]
    pos2 = [(size-1,8),(size-2,8),(size-3,8),(size-4,8),(size-5,8),(size-6,8),(size-7,8),(size-8,8),
            (8,size-8),(8,size-7),(8,size-6),(8,size-5),(8,size-4),(8,size-3),(8,size-2),(8,size-1)]
    for i,(r,c) in enumerate(pos1[:15]):
        mat[r][c] = bool(fmt_bits[i])
    for i,(r,c) in enumerate(pos2[:15]):
        mat[r][c] = bool(fmt_bits[i])

    return mat

def _qr_to_svg(mat, cell=8, border=4):
    """Render QR matrix as SVG string."""
    n = len(mat)
    total = n*cell + 2*border
    rects = []
    for r in range(n):
        for c in range(n):
            if mat[r][c]:
                x = border + c*cell
                y = border + r*cell
                rects.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}"/>')
    inner = ''.join(rects)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{total}" '
            f'viewBox="0 0 {total} {total}">'
            f'<rect width="{total}" height="{total}" fill="white"/>'
            f'<g fill="black">{inner}</g>'
            f'</svg>')

def _qr_to_png_base64(mat, cell=8, border=4):
    """Render QR matrix as base64 PNG using only stdlib struct/zlib."""
    import zlib, struct as _st
    n = len(mat)
    img_w = img_h = n*cell + 2*border
    # Build raw RGBA pixels (white background, black modules)
    rows = []
    for r in range(img_h):
        row = [0]  # filter byte
        qr_r = (r - border) // cell
        for c in range(img_w):
            qr_c = (c - border) // cell
            if 0 <= qr_r < n and 0 <= qr_c < n and mat[qr_r][qr_c]:
                row += [0,0,0,255]    # black
            else:
                row += [255,255,255,255]  # white
        rows.append(bytes(row))
    raw = b''.join(rows)
    compressed = zlib.compress(raw, 9)

    def png_chunk(tag, data):
        c = _st.pack('>I', len(data)) + tag + data
        crc = zlib.crc32(tag+data) & 0xffffffff
        return c + _st.pack('>I', crc)

    png = (b'\x89PNG\r\n\x1a\n'
           + png_chunk(b'IHDR', _st.pack('>IIBBBBB', img_w, img_h, 8, 6, 0, 0, 0))
           + png_chunk(b'IDAT', compressed)
           + png_chunk(b'IEND', b''))
    return 'data:image/png;base64,' + base64.b64encode(png).decode()

def _totp_qr_base64(secret, email, issuer="VEWIT"):
    """Generate a real scannable QR code PNG — pure Python, zero dependencies."""
    otpauth_url = _totp_qr_url(secret, email, issuer)
    try:
        mat = _qr_make_matrix(otpauth_url)
        return _qr_to_png_base64(mat, cell=8, border=4)
    except Exception as e:
        print(f"[QR] Pure-Python QR failed: {e} — trying segno")
    # Try segno if installed
    try:
        import segno, io
        qr = segno.make_qr(otpauth_url, error='M')
        buf = io.BytesIO()
        qr.save(buf, kind='png', scale=6, border=2)
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    except ImportError: pass
    # Last resort: SVG (still scannable)
    try:
        mat2 = _qr_make_matrix(otpauth_url)
        svg = _qr_to_svg(mat2, cell=10, border=4)
        return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()
    except Exception as e2:
        print(f"[QR] SVG fallback also failed: {e2}")
    return None


@app.route("/api/auth/totp/setup", methods=["POST"])
@login_required
def totp_setup():
    """Begin TOTP setup: generate a secret and return otpauth URL + optional QR."""
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not u:
            return jsonify({"error": "User not found"}), 404
        if u.get("totp_verified"):
            return jsonify({"error": "TOTP already configured. Reset it first."}), 400
        secret = _totp_generate_secret()
        db.execute("UPDATE users SET totp_secret=?, totp_verified=0 WHERE id=?", (secret, u["id"]))
        otpauth = _totp_qr_url(secret, u["email"])
        # Try to generate server-side QR (optional — client will render its own too)
        qr = None
        try:
            import qrcode as _qrc, io as _io
            _qr = _qrc.QRCode(error_correction=_qrc.constants.ERROR_CORRECT_M, box_size=8, border=4)
            _qr.add_data(otpauth); _qr.make(fit=True)
            _img = _qr.make_image(fill_color="black", back_color="white")
            _buf = _io.BytesIO(); _img.save(_buf, 'PNG')
            qr = "data:image/png;base64," + base64.b64encode(_buf.getvalue()).decode()
        except Exception:
            pass  # Client-side QRCode.js will handle it
        return jsonify({
            "secret": secret,
            "otpauth": otpauth,
            "qr_image": qr,   # None if qrcode lib not installed — client renders instead
            "email": u["email"]
        })

@app.route("/api/auth/totp/verify-setup", methods=["POST"])
@login_required
def totp_verify_setup():
    """Confirm TOTP setup by verifying the first token from the authenticator app."""
    d = request.json or {}
    token = d.get("token", "").strip().replace(" ", "")
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not u or not u.get("totp_secret"):
            return jsonify({"error": "No TOTP setup in progress. Call /setup first."}), 400
        if not _totp_verify(u["totp_secret"], token):
            return jsonify({"error": "Invalid code. Check your authenticator app and try again."}), 401
        db.execute("UPDATE users SET totp_verified=1, two_fa_enabled=1 WHERE id=?", (u["id"],))
        return jsonify({"ok": True, "message": "Google Authenticator configured successfully!"})

@app.route("/api/auth/totp/verify", methods=["POST"])
def totp_verify_login():
    """Verify TOTP token during login flow."""
    d = request.json or {}
    user_id = d.get("user_id")
    token = d.get("token", "").strip().replace(" ", "")
    if not user_id or not token:
        return jsonify({"error": "user_id and token required"}), 400
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            return jsonify({"error": "User not found"}), 404
        if not u.get("totp_secret") or not u.get("totp_verified"):
            return jsonify({"error": "TOTP not configured for this user"}), 400
        if not _totp_verify(u["totp_secret"], token):
            return jsonify({"error": "Invalid authenticator code. Try again."}), 401
        session.permanent = True
        session["user_id"] = u["id"]
        session["workspace_id"] = u["workspace_id"]
        try:
            db.execute("UPDATE users SET last_active=? WHERE id=?", (ts(), u["id"]))
        except Exception: pass
        _audit("user_login_totp", u["id"], f"{u['name']} logged in via Google Authenticator")
        result = dict(u)
        result.pop("password", None)
        result.pop("totp_secret", None)
        result.pop("avatar_data", None)
        return jsonify(result)

@app.route("/api/auth/totp/reset", methods=["POST"])
@login_required
def totp_reset():
    """Admin resets TOTP for a user (or user resets their own)."""
    d = request.json or {}
    target_id = d.get("user_id", session["user_id"])
    with get_db() as db:
        caller = db.execute("SELECT role FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if target_id != session["user_id"] and (not caller or caller["role"] not in ("Admin", "Manager")):
            return jsonify({"error": "Only admins can reset TOTP for other users"}), 403
        db.execute("UPDATE users SET totp_secret='', totp_verified=0, two_fa_enabled=0 WHERE id=? AND workspace_id=?",
                   (target_id, wid()))
        return jsonify({"ok": True, "message": "TOTP reset. User can now set up a new authenticator."})

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    uid = session.get("user_id", "")
    _audit("user_logout", uid, "User signed out")
    session.clear()
    return jsonify({"ok": True})

@app.route("/signout")
@app.route("/sign-out")
def signout_redirect():
    """GET /signout — clear session and redirect to login page."""
    session.clear()
    return '<html><head><meta http-equiv="refresh" content="0;url=/?action=login"/></head><body>Signing out...</body></html>'


@app.route("/api/auth/register",methods=["POST"])
def register():
    d=request.json or {}
    mode=d.get("mode","create")  # 'create' or 'join'
    if not d.get("name") or not d.get("email") or not d.get("password"):
        return jsonify({"error":"All fields required"}),400
    uid=f"u{int(datetime.now().timestamp()*1000)}"
    av="".join(w[0] for w in d["name"].split())[:2].upper()
    c=random.choice(CLRS)
    ws_id=None
    if mode=="create":
        if not d.get("workspace_name"):
            return jsonify({"error":"Workspace name required"}),400
        ws_id=f"ws{int(datetime.now().timestamp()*1000)}"
        invite=secrets.token_hex(4).upper()
        with get_db() as db:
            db.execute("INSERT INTO workspaces VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (ws_id,d["workspace_name"],invite,uid,None,ts(),None,587,None,None,None,1))
    elif mode=="join":
        code=d.get("invite_code","").strip().upper()
        with get_db() as db:
            ws=db.execute("SELECT id FROM workspaces WHERE invite_code=?",(code,)).fetchone()
            if not ws: return jsonify({"error":"Invalid invite code"}),400
            ws_id=ws["id"]
    else:
        return jsonify({"error":"Invalid mode"}),400
    try:
        with get_db() as db:
            db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (uid,ws_id,d["name"],d["email"],hash_pw(d["password"]),
                        d.get("role","Developer"),av,c,ts(),None))
            session.permanent=True
            session["user_id"]=uid
            session["workspace_id"]=ws_id
            session["role"]=d.get("role","Developer")  # cache role
            _audit("user_register", uid, f"{d['name']} ({d['email']}) registered via {mode}")
            return jsonify({"id":uid,"workspace_id":ws_id,"name":d["name"],"email":d["email"],
                            "role":d.get("role","Developer"),"avatar":av,"color":c})
    except Exception as e:
        if "UNIQUE" in str(e): return jsonify({"error":"Email already registered"}),400
        return jsonify({"error":str(e)}),500

@app.route("/api/presence", methods=["POST"])
@login_required
def update_presence():
    with get_db() as db:
        db.execute("UPDATE users SET last_active=? WHERE id=? AND workspace_id=?",
                   (ts(), session["user_id"], wid()))
        return jsonify({"ok": True})

@app.route("/api/presence")
@login_required
def get_presence():
    """Returns list of user IDs active in last 2 minutes."""
    with get_db() as db:
        cutoff = (now_ist() - timedelta(minutes=3)).strftime('%Y-%m-%dT%H:%M:%S')
        rows = db.execute(
            "SELECT id FROM users WHERE workspace_id=? AND last_active>?",
            (wid(), cutoff)).fetchall()
        return jsonify([r["id"] for r in rows])

@app.route("/api/meet/notify", methods=["POST"])
@login_required
def meet_notify():
    """Send a Google Meet call notification to a specific user."""
    d = request.json or {}
    target_id = d.get("target_id")
    room_name = d.get("room_name", "")
    caller_name_override = d.get("caller_name", "")
    if not target_id:
        return jsonify({"error": "target_id required"}), 400
    with get_db() as db:
        caller = db.execute("SELECT name FROM users WHERE id=?", (session["user_id"],)).fetchone()
        cname = caller_name_override or (caller["name"] if caller else "Someone")
        nid = f"n{int(datetime.now().timestamp()*1000)}"
        msg = f"📹 {cname} is calling you — click to join the meeting"
        try:
            db.execute(
                "INSERT INTO notifications(id,workspace_id,type,content,user_id,read,ts,sender_id) VALUES (?,?,?,?,?,?,?,?)",
                (nid, wid(), "call", msg, target_id, 0, ts(), session["user_id"]))
        except:
            db.execute(
                "INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                (nid, wid(), "call", msg, target_id, 0, ts()))
        return jsonify({"ok": True, "caller": cname, "room": room_name})

@app.route("/api/auth/me")
def me():
    if "user_id" not in session: return jsonify({"error":"Not logged in"}),401
    with get_db() as db:
        u=db.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not u: session.clear(); return jsonify({"error":"Not found"}),404
        if u["workspace_id"]: session["workspace_id"]=u["workspace_id"]
        result = dict(u)
        for k in ("password","plain_password","totp_secret"):
            result.pop(k, None)
        return jsonify(result)

# ── Vault ─────────────────────────────────────────────────────────────────────
# All `rows` data is Fernet-encrypted (AES-128-CBC + HMAC-SHA256) before being
# stored in the database. vault_encrypt / vault_decrypt are defined near the top
# of this file. If the `cryptography` package is unavailable the functions are
# no-ops so the feature degrades gracefully (no silent data loss).

@app.route("/api/vault", methods=["GET"])
@login_required
def vault_list():
    with get_db() as db:
        records = db.execute(
            "SELECT * FROM vault_cards WHERE user_id=? ORDER BY created DESC",
            (session["user_id"],)
        ).fetchall()
    result = []
    for r in records:
        card = dict(r)
        # Decrypt rows — vault_decrypt falls back gracefully for legacy plain rows
        try:
            card["rows"] = vault_decrypt(card.get("rows") or "[]")
        except Exception:
            card["rows"] = "[]"
        result.append(card)
    return jsonify(result)

@app.route("/api/vault", methods=["POST"])
@login_required
def vault_create():
    d = request.json or {}
    now = ts()
    cid = "c" + str(int(time.time()*1000)) + secrets.token_hex(3)
    plain_rows = json.dumps(d.get("rows", []))
    encrypted_rows = vault_encrypt(plain_rows)
    with get_db() as db:
        db.execute(
            "INSERT INTO vault_cards (id,user_id,title,tags,rows,cols,lock_hash,created,updated) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, session["user_id"], d.get("title", ""), d.get("tags", ""),
             encrypted_rows, json.dumps(d.get("cols") or []),
             d.get("lock_hash", ""), now, now)
        )
    _vault_audit(session["user_id"], cid, "create", d.get("title", ""))
    return jsonify({"id": cid, "created": now})

@app.route("/api/vault/<cid>", methods=["PUT"])
@login_required
def vault_update(cid):
    d = request.json or {}
    now = ts()
    plain_rows = json.dumps(d.get("rows", []))
    encrypted_rows = vault_encrypt(plain_rows)
    with get_db() as db:
        db.execute(
            "UPDATE vault_cards SET title=?,tags=?,rows=?,cols=?,lock_hash=?,updated=? "
            "WHERE id=? AND user_id=?",
            (d.get("title", ""), d.get("tags", ""), encrypted_rows,
             json.dumps(d.get("cols") or []),
             d.get("lock_hash", ""), now, cid, session["user_id"])
        )
    return jsonify({"ok": True})

@app.route("/api/vault/<cid>", methods=["DELETE"])
@login_required
def vault_delete(cid):
    with get_db() as db:
        db.execute("DELETE FROM vault_cards WHERE id=? AND user_id=?", (cid, session["user_id"]))
        db.execute("DELETE FROM vault_audit_log WHERE card_id=? AND user_id=?", (cid, session["user_id"]))
    return jsonify({"ok": True})

# ── Vault Audit Log ────────────────────────────────────────────────────────────
def _vault_audit(user_id, card_id, action, detail="", ip=""):
    """Insert a vault audit log entry. Non-blocking — swallows errors."""
    try:
        aid = "va" + secrets.token_hex(6)
        now = ts()
        with get_db() as db:
            db.execute(
                "INSERT INTO vault_audit_log (id,user_id,card_id,action,detail,ip,created) "
                "VALUES (?,?,?,?,?,?,?)",
                (aid, user_id, card_id, action, detail[:200], ip[:60], now)
            )
    except Exception as e:
        log.warning("[vault_audit] non-fatal: %s", e)

@app.route("/api/vault/audit", methods=["GET"])
@login_required
def vault_audit_list():
    """Return the 50 most recent vault audit events for the current user."""
    with get_db() as db:
        rows = db.execute(
            "SELECT a.id, a.card_id, a.action, a.detail, a.ip, a.created, "
            "       v.title AS card_title "
            "FROM vault_audit_log a "
            "LEFT JOIN vault_cards v ON a.card_id = v.id "
            "WHERE a.user_id=? ORDER BY a.created DESC LIMIT 50",
            (session["user_id"],)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/vault/<cid>/audit", methods=["POST"])
@login_required
def vault_audit_event(cid):
    """Log a reveal or copy event triggered by the frontend."""
    d = request.json or {}
    action = (d.get("action") or "").strip()[:50]
    detail = (d.get("detail") or "").strip()[:200]
    if action not in ("reveal", "copy", "unlock"):
        return jsonify({"error": "Invalid action"}), 400
    # Verify the card belongs to this user before logging
    with get_db() as db:
        card = db.execute(
            "SELECT id FROM vault_cards WHERE id=? AND user_id=?",
            (cid, session["user_id"])
        ).fetchone()
    if not card:
        return jsonify({"error": "Not found"}), 404
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")[:60]
    _vault_audit(session["user_id"], cid, action, detail, ip)
    return jsonify({"ok": True})

# ── Workspace ─────────────────────────────────────────────────────────────────
@app.route("/api/workspace")
@login_required
def get_workspace():
    with get_db() as db:
        ws=db.execute("SELECT * FROM workspaces WHERE id=?",(wid(),)).fetchone()
        if not ws: return jsonify({"error":"Workspace not found"}),404
        return jsonify(dict(ws))

@app.route("/api/workspace",methods=["PUT"])
@login_required
def update_workspace():
    d=request.json or {}
    with get_db() as db:
        if "name" in d: db.execute("UPDATE workspaces SET name=? WHERE id=?",(d["name"],wid()))
        if "ai_api_key" in d: db.execute("UPDATE workspaces SET ai_api_key=? WHERE id=?",(d["ai_api_key"],wid()))
        if "smtp_server" in d: db.execute("UPDATE workspaces SET smtp_server=? WHERE id=?",(d["smtp_server"],wid()))
        if "smtp_port" in d: db.execute("UPDATE workspaces SET smtp_port=? WHERE id=?",(d["smtp_port"],wid()))
        if "smtp_username" in d: db.execute("UPDATE workspaces SET smtp_username=? WHERE id=?",(d["smtp_username"],wid()))
        if "smtp_password" in d: db.execute("UPDATE workspaces SET smtp_password=? WHERE id=?",(d["smtp_password"],wid()))
        if "from_email" in d: db.execute("UPDATE workspaces SET from_email=? WHERE id=?",(d["from_email"],wid()))
        if "email_enabled" in d: db.execute("UPDATE workspaces SET email_enabled=? WHERE id=?",(1 if d["email_enabled"] else 0,wid()))
        if "otp_enabled" in d: db.execute("UPDATE workspaces SET otp_enabled=? WHERE id=?",(1 if d["otp_enabled"] else 0,wid()))
        if "dm_enabled" in d: db.execute("UPDATE workspaces SET dm_enabled=? WHERE id=?",(1 if d["dm_enabled"] else 0,wid()))
        ws=db.execute("SELECT * FROM workspaces WHERE id=?",(wid(),)).fetchone()
        return jsonify(dict(ws))

@app.route("/api/workspace/new-invite",methods=["POST"])
@login_required
def new_invite():
    invite=secrets.token_hex(4).upper()
    with get_db() as db:
        db.execute("UPDATE workspaces SET invite_code=? WHERE id=?",(invite,wid()))
        return jsonify({"invite_code":invite})

@app.route("/api/workspace/test-email",methods=["POST"])
@login_required
def test_email():
    """Send a test email to verify SMTP configuration"""
    d=request.json or {}
    test_to=d.get("test_email")
    if not test_to:
        return jsonify({"error":"test_email required"}),400

    subject="VEWIT Email Test"
    body="""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #6366f1;">Email Configuration Test</h2>
            <p>Congratulations! Your email notifications are working correctly.</p>
            <div style="background: #f3f4f6; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <p style="margin: 0;">✅ SMTP connection successful</p>
                <p style="margin: 5px 0 0 0;">✅ Email delivery working</p>
            </div>
            <p>You will now receive notifications for:</p>
            <ul style="color: #4b5563;">
                <li>Task assignments</li>
                <li>Status changes</li>
                <li>New comments</li>
            </ul>
            <p style="color: #6b7280; font-size: 12px; margin-top: 30px;">VEWIT Notification System</p>
        </div>
    </body>
    </html>
    """

    success=send_email(test_to,subject,body,wid())
    if success:
        return jsonify({"success":True,"message":"Test email sent successfully!"})
    else:
        return jsonify({"success":False,"message":"Failed to send test email. Check SMTP settings and server logs."}),500

# ── Users ─────────────────────────────────────────────────────────────────────
@app.route("/api/users")
@login_required
def get_users():
    with get_db() as db:
        rows = db.execute(
            """SELECT id,workspace_id,name,email,role,avatar,color,created,
               two_fa_enabled,totp_verified,last_active
               FROM users WHERE workspace_id=? ORDER BY name""",
            (wid(),)).fetchall()
        caller = db.execute("SELECT role FROM users WHERE id=?", (session["user_id"],)).fetchone()
        caller_role = caller["role"] if caller else "Developer"
        can_see_passwords = caller_role in ("Admin", "Manager")
        users = []
        for r in rows:
            u = dict(r)
            u.pop('avatar_data', None)
            u.pop('password', None)
            u.pop('plain_password', None)  # never expose plaintext passwords over API
            u['totp_configured'] = bool(u.get('totp_verified') and u.get('totp_secret'))
            u.pop('totp_secret', None)
            users.append(u)
        return jsonify(users)

@app.route("/api/users",methods=["POST"])
@login_required
def add_user():
    d=request.json or {}
    if not d.get("name") or not d.get("email") or not d.get("password"):
        return jsonify({"error":"All fields required"}),400
    uid=f"u{int(datetime.now().timestamp()*1000)}"
    av="".join(w[0] for w in d["name"].split())[:2].upper()
    c=random.choice(CLRS)
    try:
        with get_db() as db:
            db.execute("INSERT INTO users (id,workspace_id,name,email,password,role,avatar,color,created,avatar_data) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (uid,wid(),d["name"],d["email"],hash_pw(d["password"]),
                        d.get("role","Developer"),av,c,ts(),None))
            return jsonify({"id":uid,"workspace_id":wid(),"name":d["name"],
                            "email":d["email"],"role":d.get("role","Developer"),"avatar":av,"color":c})
    except Exception as e:
        if "UNIQUE" in str(e): return jsonify({"error":"Email already in use"}),400
        return jsonify({"error":str(e)}),500

@app.route("/api/users/<uid>",methods=["PUT"])
@login_required
def update_user(uid):
    d=request.json or {}
    with get_db() as db:
        if "role" in d: db.execute("UPDATE users SET role=? WHERE id=? AND workspace_id=?",(d["role"],uid,wid()))
        if "name" in d:
            av="".join(w[0] for w in d["name"].split())[:2].upper()
            db.execute("UPDATE users SET name=?,avatar=? WHERE id=? AND workspace_id=?",(d["name"],av,uid,wid()))
        if "email" in d: db.execute("UPDATE users SET email=? WHERE id=? AND workspace_id=?",(d["email"],uid,wid()))
        if "password" in d: db.execute("UPDATE users SET password=? WHERE id=? AND workspace_id=?",(hash_pw(d["password"]),uid,wid()))
        if "avatar_data" in d: db.execute("UPDATE users SET avatar_data=? WHERE id=? AND workspace_id=?",(d["avatar_data"],uid,wid()))
        u=db.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        if u:
            caller=db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
            caller_role=caller["role"] if caller else "Developer"
            result=dict(u)
            result.pop("password",None)
            result.pop("plain_password",None)  # never expose plaintext passwords
            return jsonify(result)
        return jsonify({})

@app.route("/api/users/<uid>",methods=["DELETE"])
@login_required
def del_user(uid):
    with get_db() as db:
        u = db.execute("SELECT name, email FROM users WHERE id=? AND workspace_id=?",(uid,wid())).fetchone()
        db.execute("DELETE FROM users WHERE id=? AND workspace_id=?",(uid,wid()))
        name_str = f"{u['name']} ({u['email']})" if u else uid
        _audit("user_deleted", uid, f"{name_str} removed from workspace {wid()}")
        return jsonify({"ok":True})

# ── Projects ──────────────────────────────────────────────────────────────────
@app.route("/api/projects/all")
@login_required
def get_all_projects():
    """Return ALL workspace projects — used by Channels so everyone can see all project status."""
    with get_db() as db:
        rows=db.execute("SELECT * FROM projects WHERE workspace_id=? ORDER BY created DESC",(wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/projects/last-messages")
@login_required
def get_projects_last_messages():
    """Return the latest message timestamp per project — used to sort channels by activity."""
    with get_db() as db:
        rows=db.execute(
            "SELECT project, MAX(ts) as last_ts FROM messages WHERE workspace_id=? GROUP BY project",
            (wid(),)).fetchall()
        return jsonify({r["project"]: r["last_ts"] for r in rows})

@app.route("/api/projects")
@login_required
def get_projects():
    team_id = request.args.get("team_id","")
    with get_db() as db:
        if team_id:
            rows = db.execute(
                "SELECT * FROM projects WHERE workspace_id=? AND team_id=? ORDER BY created DESC",
                (wid(), team_id)).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM projects WHERE workspace_id=? ORDER BY created DESC", (wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/projects",methods=["POST"])
@login_required
def create_project():
    d=request.json or {}
    if not d.get("name"): return jsonify({"error":"Name required"}),400
    pid=f"p{int(datetime.now().timestamp()*1000)}"
    members=d.get("members",[session["user_id"]])
    if session["user_id"] not in members: members.insert(0,session["user_id"])
    with get_db() as db:
        db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid,wid(),d["name"],d.get("description",""),session["user_id"],
                    json.dumps(members),d.get("startDate",""),d.get("targetDate",""),0,
                    d.get("color","#5a8cff"),ts(),d.get("team_id","")))
        p=db.execute("SELECT * FROM projects WHERE id=? AND workspace_id=?",(pid,wid())).fetchone()
        creator=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
        cname=creator["name"] if creator else "Someone"
        for uid in members:
            if uid != session["user_id"]:
                nid=f"n{int(datetime.now().timestamp()*1000)}"
                db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                           (nid,wid(),"project_added",f"You were added to project '{d['name']}'",uid,0,ts()))
                threading.Thread(target=push_notification_to_user,
                    args=(db,uid,f"📁 Added to project: {d['name']}",
                          f"{cname} added you to '{d['name']}'","/"),daemon=True).start()
        return jsonify(dict(p))

@app.route("/api/projects/<pid>",methods=["PUT"])
@login_required
def update_project(pid):
    d=request.json or {}
    with get_db() as db:
        p=db.execute("SELECT * FROM projects WHERE id=? AND workspace_id=?",(pid,wid())).fetchone()
        if not p: return jsonify({"error":"Not found"}),404
        p_team = p["team_id"] if "team_id" in p.keys() else ""
        db.execute("""UPDATE projects SET name=?,description=?,start_date=?,target_date=?,color=?,members=?,team_id=?
                      WHERE id=? AND workspace_id=?""",
                   (d.get("name",p["name"]),d.get("description",p["description"]),
                    d.get("start_date",p["start_date"]),d.get("target_date",p["target_date"]),
                    d.get("color",p["color"]),
                    json.dumps(d.get("members",json.loads(p["members"]))),
                    d.get("team_id",p_team),pid,wid()))
        updated=db.execute("SELECT * FROM projects WHERE id=? AND workspace_id=?",(pid,wid())).fetchone()
        actor=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
        aname=actor["name"] if actor else "Someone"
        try: mems=json.loads(updated["members"] or "[]")
        except: mems=[]
        base_ts=int(datetime.now().timestamp()*1000)
        for i,uid in enumerate(mems):
            if uid==session["user_id"]: continue
            nid=f"n{base_ts+i}"
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"project_added",f"{aname} updated project '{updated['name']}'",uid,0,ts()))
            threading.Thread(target=push_notification_to_user,
                args=(db,uid,f"📁 Project updated: {updated['name']}",
                      f"{aname} made changes to '{updated['name']}'","/"),daemon=True).start()
        return jsonify(dict(updated))

@app.route("/api/projects/<pid>",methods=["DELETE"])
@login_required
def del_project(pid):
    with get_db() as db:
        cu=db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        cu_role=cu["role"] if cu else "Viewer"
        if cu_role not in ("Admin","Manager"):
            return jsonify({"error":"Only Admin or Manager can delete projects."}),403
        db.execute("DELETE FROM projects WHERE id=? AND workspace_id=?",(pid,wid()))
        db.execute("DELETE FROM tasks WHERE project=? AND workspace_id=?",(pid,wid()))
        db.execute("DELETE FROM files WHERE project_id=? AND workspace_id=?",(pid,wid()))
        return jsonify({"ok":True})

@app.route("/api/projects/bulk-assign-team",methods=["POST"])
@login_required
def bulk_assign_team():
    """Assign a team_id to multiple projects at once."""
    d=request.json or {}
    team_id=d.get("team_id","")
    project_ids=d.get("project_ids",[])
    if not project_ids: return jsonify({"error":"project_ids required"}),400
    with get_db() as db:
        cu=db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not cu or cu["role"] not in ("Admin","Manager"):
            return jsonify({"error":"Only Admin or Manager can assign teams to projects."}),403
        for pid in project_ids:
            db.execute("UPDATE projects SET team_id=? WHERE id=? AND workspace_id=?",(team_id,pid,wid()))
        return jsonify({"ok":True,"updated":len(project_ids)})

# ── Tasks ─────────────────────────────────────────────────────────────────────
@app.route("/api/tasks")
@login_required
def get_tasks():
    team_id = request.args.get("team_id","")
    with get_db() as db:
        if team_id:
            team = db.execute("SELECT member_ids FROM teams WHERE id=? AND workspace_id=?",(team_id,wid())).fetchone()
            member_ids = json.loads(team["member_ids"] if team else "[]")
            team_projects = db.execute(
                "SELECT id FROM projects WHERE workspace_id=? AND team_id=?",(wid(),team_id)).fetchall()
            proj_ids = [p["id"] for p in team_projects]
            # Use SQL WHERE IN instead of Python-side filtering — much faster
            placeholders_p = ",".join("?" * len(proj_ids)) if proj_ids else "''"
            placeholders_m = ",".join("?" * len(member_ids)) if member_ids else "''"
            sql = f"""SELECT * FROM tasks WHERE workspace_id=? AND (
                team_id=? OR
                {f"project IN ({placeholders_p})" if proj_ids else "1=0"} OR
                {f"assignee IN ({placeholders_m})" if member_ids else "1=0"}
            ) ORDER BY created DESC LIMIT 500"""
            params = [wid(), team_id] + proj_ids + member_ids
            return jsonify([dict(r) for r in db.execute(sql, params).fetchall()])
        # Limit to 500 most recent — prevents huge payloads on large workspaces
        return jsonify([dict(r) for r in db.execute(
            "SELECT * FROM tasks WHERE workspace_id=? ORDER BY created DESC LIMIT 500",(wid(),)).fetchall()])

def next_task_id(db, ws):
    import time
    base = int(time.time() * 1000)
    row=db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=?",(ws,)).fetchone()
    count=row['cnt'] if row else 0
    return f"T-{count+1:03d}-{base % 10000}"

@app.route("/api/tasks",methods=["POST"])
@login_required
def create_task():
    d=request.json or {}
    if not d.get("title"): return jsonify({"error":"Title required"}),400
    with get_db() as db:
        tid=next_task_id(db,wid())
        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (tid,wid(),d["title"],d.get("description",""),d.get("project",""),
                    d.get("assignee",""),d.get("priority","medium"),d.get("stage","backlog"),
                    ts(),d.get("due",""),d.get("pct",0),json.dumps(d.get("comments",[])),
                    d.get("team_id","")))
        creator=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
        cname=creator["name"] if creator else "Someone"
        base_ts=int(datetime.now().timestamp()*1000)
        if d.get("assignee") and d["assignee"]!=session["user_id"]:
            nid=f"n{base_ts}"
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"task_assigned",f"{cname} assigned you to '{d['title']}'",d["assignee"],0,ts()))
            assignee_user=db.execute("SELECT name,email FROM users WHERE id=?",(d["assignee"],)).fetchone()
            if assignee_user and assignee_user["email"]:
                threading.Thread(target=send_task_assigned_email,
                    args=(assignee_user["email"],assignee_user["name"],d["title"],cname,tid,wid()),
                    daemon=True).start()
            threading.Thread(target=push_notification_to_user,
                args=(db, d["assignee"], f"✅ New task assigned: {d['title']}",
                      f"{cname} assigned you this task [{d.get('priority','medium')}]", "/"),
                daemon=True).start()
        if d.get("project"):
            proj=db.execute("SELECT name,members FROM projects WHERE id=? AND workspace_id=?",(d["project"],wid())).fetchone()
            if proj:
                try:
                    members=json.loads(proj["members"] or "[]")
                except: members=[]
                for i,uid in enumerate(members):
                    if uid==session["user_id"] or uid==d.get("assignee"): continue
                    nid2=f"n{base_ts+10+i}"
                    db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                               (nid2,wid(),"task_assigned",f"{cname} created task '{d['title']}' in {proj['name']}",uid,0,ts()))
                    threading.Thread(target=push_notification_to_user,
                        args=(db, uid, f"📋 New task in {proj['name']}",
                              f"{cname} created '{d['title']}'", "/"),
                        daemon=True).start()
        t=db.execute("SELECT * FROM tasks WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()
        if d.get("project"):
            assignee_name=""
            if d.get("assignee"):
                au=db.execute("SELECT name FROM users WHERE id=?",(d["assignee"],)).fetchone()
                if au: assignee_name=f" → assigned to {au['name']}"
            sysmid=f"m{base_ts+1}"
            msg=f"📋 **{cname}** created task **{d['title']}**{assignee_name} [{d.get('priority','medium').title()}]"
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                       (sysmid,wid(),"system",d["project"],msg,ts(),1))
        return jsonify(dict(t))


@app.route("/api/tasks/<tid>/events", methods=["GET"])
@login_required
def get_task_events(tid):
    """Get activity log for a task."""
    with get_db() as db:
        rows = db.execute(
            """SELECT te.*, u.name as user_name, u.avatar as user_avatar, u.color as user_color
               FROM task_events te LEFT JOIN users u ON te.user_id=u.id
               WHERE te.task_id=? AND te.workspace_id=? ORDER BY te.ts DESC LIMIT 50""",
            (tid, wid())).fetchall()
        return jsonify([dict(r) for r in rows])

def log_task_event(db, workspace_id, task_id, user_id, event_type, old_val="", new_val=""):
    """Insert a task activity event."""
    try:
        eid = f"te{int(datetime.now().timestamp()*1000)}{secrets.token_hex(2)}"
        db.execute("INSERT INTO task_events VALUES (?,?,?,?,?,?,?,?)",
                   (eid, workspace_id, task_id, user_id, event_type,
                    str(old_val), str(new_val), ts()))
    except Exception as e:
        log.warning("[task_event] %s", e)

@app.route("/api/tasks/<tid>",methods=["PUT"])
@login_required
def update_task(tid):
    d=request.json or {}
    with get_db() as db:
        cu=db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        cu_role=cu["role"] if cu else "Viewer"
        t=db.execute("SELECT * FROM tasks WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()
        if not t: return jsonify({"error":"Not found"}),404

        is_admin_manager = cu_role in ("Admin","Manager")
        is_teamlead = cu_role == "TeamLead"
        is_assignee = t["assignee"] == session["user_id"]
        proj = db.execute("SELECT owner FROM projects WHERE id=? AND workspace_id=?",(t["project"],wid())).fetchone() if t["project"] else None
        is_proj_owner = proj and proj["owner"] == session["user_id"]

        if not (is_admin_manager or is_teamlead or is_proj_owner):
            if is_assignee:
                allowed={"stage","pct","comments"}
                if any(k not in allowed for k in d.keys()):
                    return jsonify({"error":"You can only update stage, progress, and comments on tasks assigned to you."}),403
            else:
                return jsonify({"error":"You do not have permission to edit this task. Only the assignee, project owner, or managers can edit tasks."}),403

        old_stage=t["stage"]
        old_assignee=t["assignee"]
        def tf(key,default=''):
            return t[key] if key in t.keys() else default
        labels_val=d.get("labels",None)
        if labels_val is not None and isinstance(labels_val,list): labels_val=json.dumps(labels_val)
        elif labels_val is None: labels_val=tf("labels","[]")
        comments_val=d.get("comments",None)
        if comments_val is None: comments_val=json.loads(t["comments"] or "[]")
        db.execute("""UPDATE tasks SET title=?,description=?,project=?,assignee=?,
                      priority=?,stage=?,due=?,pct=?,comments=?,team_id=?,
                      story_points=?,task_type=?,labels=?,sprint=? WHERE id=? AND workspace_id=?""",
                   (d.get("title",t["title"]),d.get("description",t["description"]),
                    d.get("project",t["project"]),d.get("assignee",t["assignee"]),
                    d.get("priority",t["priority"]),d.get("stage",t["stage"]),
                    d.get("due",t["due"]),d.get("pct",t["pct"]),
                    json.dumps(comments_val),
                    d.get("team_id",tf("team_id","")),
                    d.get("story_points",tf("story_points",0)),
                    d.get("task_type",tf("task_type","task")),
                    labels_val,
                    d.get("sprint",tf("sprint","")),
                    tid,wid()))
        # Log activity events
        new_stage_val = d.get("stage", old_stage)
        new_assignee_val = d.get("assignee", old_assignee)
        if new_stage_val != old_stage:
            log_task_event(db, wid(), tid, session["user_id"], "stage_change", old_stage, new_stage_val)
        if new_assignee_val != old_assignee and new_assignee_val:
            assignee_name = (db.execute("SELECT name FROM users WHERE id=?", (new_assignee_val,)).fetchone() or {}).get("name","?")
            log_task_event(db, wid(), tid, session["user_id"], "assigned", old_assignee or "", assignee_name)
        if d.get("stage") and d["stage"]!=old_stage:
            base_ts2=int(datetime.now().timestamp()*1000)
            if t["assignee"] and t["assignee"]!=session["user_id"]:
                nid=f"n{base_ts2}"
                db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                           (nid,wid(),"status_change",f"Task '{t['title']}' moved to {d['stage']}",
                            t["assignee"],0,ts()))
                assignee_user=db.execute("SELECT name,email FROM users WHERE id=?",(t["assignee"],)).fetchone()
                changer_user=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
                changer_name=changer_user["name"] if changer_user else "Someone"
                if assignee_user and assignee_user["email"]:
                    threading.Thread(target=send_status_change_email,
                        args=(assignee_user["email"],assignee_user["name"],t["title"],d["stage"],changer_name,wid()),
                        daemon=True).start()
                threading.Thread(target=push_notification_to_user,
                    args=(db, t["assignee"], f"🔄 Task updated: {t['title']}",
                          f"{changer_name} moved it to {d['stage']}", "/"),
                    daemon=True).start()
            if t["project"]:
                proj=db.execute("SELECT members FROM projects WHERE id=? AND workspace_id=?",(t["project"],wid())).fetchone()
                if proj:
                    try: members=json.loads(proj["members"] or "[]")
                    except: members=[]
                    actor=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
                    aname=actor["name"] if actor else "Someone"
                    for i2,uid in enumerate(members):
                        if uid==session["user_id"] or uid==t["assignee"]: continue
                        nid2=f"n{base_ts2+20+i2}"
                        db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                                   (nid2,wid(),"status_change",f"{aname} moved '{t['title']}' → {d['stage']}",uid,0,ts()))
                        threading.Thread(target=push_notification_to_user,
                            args=(db, uid, f"🔄 {t['title']} → {d['stage']}",
                                  f"{aname} updated the task stage", "/"),
                            daemon=True).start()
                sysmid=f"m{base_ts2+2}"
                db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                           (sysmid,wid(),"system",t["project"],
                            f"⚡ **{aname}** moved **{t['title']}** → {d['stage'].title()}",ts(),1))
        new_comments=d.get("comments",[])
        old_comments=json.loads(t["comments"] or "[]")
        if len(new_comments)>len(old_comments) and t["project"]:
            latest=new_comments[-1]
            commenter=db.execute("SELECT name FROM users WHERE id=?",(latest.get("uid",""),)).fetchone()
            cname=commenter["name"] if commenter else "Someone"
            sysmid=f"m{int(datetime.now().timestamp()*1000)+3}"
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                       (sysmid,wid(),"system",t["project"],
                        f"💬 **{cname}** commented on **{t['title']}**: {latest.get('text','')}",ts(),1))
            if t["assignee"] and t["assignee"]!=session["user_id"]:
                nid2=f"n{int(datetime.now().timestamp()*1000)+4}"
                db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                           (nid2,wid(),"comment",f"{cname} commented on '{t['title']}': {latest.get('text','')}",
                            t["assignee"],0,ts()))
                assignee_user=db.execute("SELECT name,email FROM users WHERE id=?",(t["assignee"],)).fetchone()
                if assignee_user and assignee_user["email"]:
                    threading.Thread(target=send_comment_email,
                        args=(assignee_user["email"],assignee_user["name"],t["title"],cname,latest.get('text',''),wid()),
                        daemon=True).start()
                threading.Thread(target=push_notification_to_user,
                    args=(db, t["assignee"], f"💬 Comment on: {t['title']}",
                          f"{cname}: {latest.get('text','')[:80]}", "/"),
                    daemon=True).start()
        return jsonify(dict(db.execute("SELECT * FROM tasks WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()))


@app.route("/api/subtasks/search")
@login_required
def search_subtasks():
    q = request.args.get("q","").strip().lower()
    if not q or len(q) < 2:
        return jsonify([])
    with get_db() as db:
        rows = db.execute("""
            SELECT s.*, t.title as task_title, t.project
            FROM subtasks s
            JOIN tasks t ON s.task_id = t.id
            WHERE s.workspace_id = ?
            AND (LOWER(s.id) LIKE ? OR LOWER(s.title) LIKE ?)
            LIMIT 10
        """, (wid(), f"%{q}%", f"%{q}%")).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/tasks/<tid>/subtasks", methods=["GET"])
@login_required
def get_subtasks(tid):
    with get_db() as db:
        rows=db.execute("SELECT * FROM subtasks WHERE task_id=? AND workspace_id=? ORDER BY created",(tid,wid())).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/tasks/<tid>/subtasks", methods=["POST"])
@login_required
def create_subtask(tid):
    d=request.json or {}
    sid=f"st{int(datetime.now().timestamp()*1000)}{secrets.token_hex(3)}"
    with get_db() as db:
        db.execute("INSERT INTO subtasks VALUES (?,?,?,?,?,?,?)",
                   (sid,wid(),tid,d.get("title","Untitled"),0,d.get("assignee",""),ts()))
        return jsonify({"id":sid,"task_id":tid,"title":d.get("title",""),"done":0})

@app.route("/api/subtasks/<sid>", methods=["PUT"])
@login_required
def update_subtask(sid):
    d=request.json or {}
    with get_db() as db:
        st=db.execute("SELECT * FROM subtasks WHERE id=? AND workspace_id=?",(sid,wid())).fetchone()
        if not st: return jsonify({"error":"Not found"}),404
        done=d.get("done",st["done"])
        title=d.get("title",st["title"])
        assignee=d.get("assignee",st["assignee"])
        db.execute("UPDATE subtasks SET done=?,title=?,assignee=? WHERE id=?",(done,title,assignee,sid))
        return jsonify({"ok":True})

@app.route("/api/subtasks/<sid>", methods=["DELETE"])
@login_required
def delete_subtask(sid):
    with get_db() as db:
        db.execute("DELETE FROM subtasks WHERE id=? AND workspace_id=?",(sid,wid()))
        return jsonify({"ok":True})

@app.route("/api/tasks/<tid>",methods=["DELETE"])
@login_required
def del_task(tid):
    with get_db() as db:
        cu=db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        cu_role=cu["role"] if cu else "Viewer"
        if cu_role not in ("Admin","Manager","TeamLead"):
            return jsonify({"error":"Only Admin, Manager, or TeamLead can delete tasks."}),403
        db.execute("DELETE FROM tasks WHERE id=? AND workspace_id=?",(tid,wid()))
        return jsonify({"ok":True})

# ── Files ─────────────────────────────────────────────────────────────────────
@app.route("/api/files")
@login_required
def get_files():
    task_id=request.args.get("task_id"); project_id=request.args.get("project_id")
    with get_db() as db:
        if task_id:
            rows=db.execute("SELECT * FROM files WHERE task_id=? AND workspace_id=? ORDER BY ts DESC",(task_id,wid())).fetchall()
        elif project_id:
            rows=db.execute("SELECT * FROM files WHERE project_id=? AND workspace_id=? ORDER BY ts DESC",(project_id,wid())).fetchall()
        else: rows=[]
        return jsonify([dict(r) for r in rows])

@app.route("/api/files",methods=["POST"])
@login_required
def upload_file():
    f=request.files.get("file")
    if not f: return jsonify({"error":"No file"}),400
    fid=f"f{int(datetime.now().timestamp()*1000)}"
    data=f.read()
    if len(data)>150*1024*1024: return jsonify({"error":"File too large (max 150MB)"}),400
    path=os.path.join(UPLOAD_DIR,fid)
    with open(path,"wb") as fp: fp.write(data)
    task_id=request.form.get("task_id","")
    project_id=request.form.get("project_id","")
    with get_db() as db:
        db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?)",
                   (fid,wid(),f.filename,len(data),f.content_type,task_id,project_id,session["user_id"],ts()))
        row=db.execute("SELECT * FROM files WHERE id=? AND workspace_id=?",(fid,wid())).fetchone()
        return jsonify(dict(row))

@app.route("/api/files/<fid>")
@login_required
def download_file(fid):
    with get_db() as db:
        row=db.execute("SELECT * FROM files WHERE id=? AND workspace_id=?",(fid,wid())).fetchone()
        if not row: return jsonify({"error":"Not found"}),404
    path=os.path.join(UPLOAD_DIR,fid)
    if not os.path.exists(path): return jsonify({"error":"File missing"}),404
    return send_file(path,download_name=row["name"],as_attachment=True,mimetype=row["mime"])

@app.route("/api/files/<fid>",methods=["DELETE"])
@login_required
def del_file(fid):
    with get_db() as db:
        db.execute("DELETE FROM files WHERE id=? AND workspace_id=?",(fid,wid()))
    path=os.path.join(UPLOAD_DIR,fid)
    if os.path.exists(path): os.remove(path)
    return jsonify({"ok":True})

# ── Messages ──────────────────────────────────────────────────────────────────
@app.route("/api/messages")
@login_required
def get_messages():
    project=request.args.get("project","")
    with get_db() as db:
        rows=db.execute("SELECT * FROM messages WHERE project=? AND workspace_id=? ORDER BY ts",
                        (project,wid())).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/messages",methods=["POST"])
@login_required
def send_message():
    d=request.json or {}
    mid=f"m{int(datetime.now().timestamp()*1000)}"
    with get_db() as db:
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                   (mid,wid(),session["user_id"],d.get("project",""),d.get("content",""),ts(),0))
        sender=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
        sender_name=sender["name"] if sender else "Someone"
        project_row=db.execute("SELECT name FROM projects WHERE id=? AND workspace_id=?",(d.get("project",""),wid())).fetchone()
        proj_name=project_row["name"] if project_row else "a project"
        preview=d.get("content","")[:60]+("..." if len(d.get("content",""))>60 else "")
        members=db.execute("SELECT id FROM users WHERE workspace_id=? AND id!=?",(wid(),session["user_id"])).fetchall()
        base_ts=int(datetime.now().timestamp()*1000)
        for i,m in enumerate(members):
            nid=f"n{base_ts+i}"
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"message",f"#{proj_name} — {sender_name}: {preview}",m["id"],0,ts()))
        return jsonify(dict(db.execute("SELECT * FROM messages WHERE id=?",(mid,)).fetchone()))

# ── Direct Messages ───────────────────────────────────────────────────────────
@app.route("/api/dm/<other_id>")
@login_required
def get_dm(other_id):
    me=session["user_id"]
    with get_db() as db:
        rows=db.execute("""SELECT * FROM direct_messages
            WHERE workspace_id=? AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))
            ORDER BY ts""",(wid(),me,other_id,other_id,me)).fetchall()
        db.execute("UPDATE direct_messages SET read=1 WHERE workspace_id=? AND sender=? AND recipient=? AND read=0",
                   (wid(),other_id,me))
        return jsonify([dict(r) for r in rows])

@app.route("/api/dm",methods=["POST"])
@login_required
def send_dm():
    d=request.json or {}
    if not d.get("content","").strip(): return jsonify({"error":"Empty"}),400
    mid=f"dm{int(datetime.now().timestamp()*1000)}"
    with get_db() as db:
        db.execute("INSERT INTO direct_messages VALUES (?,?,?,?,?,?,?)",
                   (mid,wid(),session["user_id"],d["recipient"],d["content"],0,ts()))
        sender=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
        sender_name=sender["name"] if sender else "Someone"
        nid=f"n{int(datetime.now().timestamp()*1000)}"
        preview=d["content"][:60]+"..." if len(d["content"])>60 else d["content"]
        try:
            db.execute("INSERT INTO notifications(id,workspace_id,type,content,user_id,read,ts,sender_id) VALUES (?,?,?,?,?,?,?,?)",
                       (nid,wid(),"dm",f"{sender_name}: {preview}",d["recipient"],0,ts(),session["user_id"]))
        except:
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"dm",f"{sender_name}: {preview}",d["recipient"],0,ts()))
        return jsonify(dict(db.execute("SELECT * FROM direct_messages WHERE id=?",(mid,)).fetchone()))

@app.route("/api/dm/unread")
@login_required
def dm_unread():
    with get_db() as db:
        rows=db.execute("""SELECT sender,COUNT(*) as cnt FROM direct_messages
            WHERE workspace_id=? AND recipient=? AND read=0 GROUP BY sender""",
            (wid(),session["user_id"])).fetchall()
        return jsonify([dict(r) for r in rows])

# ── Reminders ─────────────────────────────────────────────────────────────────
@app.route("/api/reminders", methods=["GET"])
@login_required
def get_reminders():
    include_fired=request.args.get("include_fired","0")=="1"
    with get_db() as db:
        if include_fired:
            rows=db.execute("SELECT * FROM reminders WHERE workspace_id=? AND user_id=? ORDER BY remind_at DESC",
                            (wid(),session["user_id"])).fetchall()
        else:
            rows=db.execute("SELECT * FROM reminders WHERE workspace_id=? AND user_id=? AND fired=0 ORDER BY remind_at",
                            (wid(),session["user_id"])).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/reminders", methods=["POST"])
@login_required
def create_reminder():
    d=request.json or {}
    if not d.get("remind_at"): return jsonify({"error":"remind_at required"}),400
    rid=f"r{int(datetime.now().timestamp()*1000)}"
    with get_db() as db:
        db.execute("INSERT INTO reminders VALUES (?,?,?,?,?,?,?,?,?)",
                   (rid,wid(),session["user_id"],d.get("task_id",""),d.get("task_title","Reminder"),
                    d["remind_at"],d.get("minutes_before",10),0,ts()))
        row=db.execute("SELECT * FROM reminders WHERE id=?",(rid,)).fetchone()
        threading.Thread(target=push_notification_to_user,
            args=(db, session["user_id"], "⏰ Reminder set",
                  f"'{d.get('task_title','Reminder')}' — you'll be notified before the time.", "/"),
            daemon=True).start()
        return jsonify(dict(row))

@app.route("/api/reminders/<rid>", methods=["PUT"])
@login_required
def update_reminder(rid):
    d=request.json or {}
    with get_db() as db:
        existing=db.execute("SELECT * FROM reminders WHERE id=? AND user_id=?",(rid,session["user_id"])).fetchone()
        if not existing: return jsonify({"error":"Not found"}),404
        remind_at=d.get("remind_at",existing["remind_at"])
        minutes_before=d.get("minutes_before",existing["minutes_before"])
        task_title=d.get("task_title",existing["task_title"])
        db.execute("UPDATE reminders SET remind_at=?,minutes_before=?,task_title=?,fired=0 WHERE id=? AND user_id=?",
                   (remind_at,minutes_before,task_title,rid,session["user_id"]))
        row=db.execute("SELECT * FROM reminders WHERE id=?",(rid,)).fetchone()
        threading.Thread(target=push_notification_to_user,
            args=(db, session["user_id"], "⏰ Reminder updated",
                  f"'{task_title}' has been rescheduled.", "/"),
            daemon=True).start()
        return jsonify(dict(row))

@app.route("/api/reminders/<rid>", methods=["DELETE"])
@login_required
def delete_reminder(rid):
    with get_db() as db:
        db.execute("DELETE FROM reminders WHERE id=? AND user_id=?",(rid,session["user_id"]))
        return jsonify({"ok":True})

# ── Teams ─────────────────────────────────────────────────────────────────────
@app.route("/api/teams", methods=["GET"])
@login_required
def get_teams():
    with get_db() as db:
        rows=db.execute("SELECT * FROM teams WHERE workspace_id=? ORDER BY created DESC",(wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/teams", methods=["POST"])
@login_required
def create_team():
    d=request.json or {}
    if not d.get("name"): return jsonify({"error":"name required"}),400
    tid=f"tm{int(datetime.now().timestamp()*1000)}"
    with get_db() as db:
        db.execute("INSERT INTO teams VALUES (?,?,?,?,?,?)",
                   (tid,wid(),d["name"],d.get("lead_id",""),json.dumps(d.get("member_ids",[])),ts()))
        return jsonify(dict(db.execute("SELECT * FROM teams WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()))

@app.route("/api/teams/<tid>", methods=["PUT"])
@login_required
def update_team(tid):
    d=request.json or {}
    with get_db() as db:
        t=db.execute("SELECT * FROM teams WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()
        if not t: return jsonify({"error":"not found"}),404
        db.execute("UPDATE teams SET name=?,lead_id=?,member_ids=? WHERE id=?",
                   (d.get("name",t["name"]),d.get("lead_id",t["lead_id"]),
                    json.dumps(d.get("member_ids",json.loads(t["member_ids"] or "[]"))),tid))
        return jsonify(dict(db.execute("SELECT * FROM teams WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()))

@app.route("/api/teams/<tid>", methods=["DELETE"])
@login_required
def delete_team(tid):
    with get_db() as db:
        db.execute("DELETE FROM teams WHERE id=? AND workspace_id=?",(tid,wid()))
        return jsonify({"ok":True})

@app.route("/api/teams/<tid>/dashboard")
@login_required
def team_dashboard(tid):
    """Return rich stats for a single team: projects, tasks, member workloads."""
    with get_db() as db:
        team=db.execute("SELECT * FROM teams WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()
        if not team: return jsonify({"error":"Not found"}),404
        member_ids=json.loads(team["member_ids"] or "[]")
        all_tasks=db.execute("SELECT * FROM tasks WHERE workspace_id=?",(wid(),)).fetchall()
        team_tasks=[t for t in all_tasks if t["assignee"] in member_ids or (t["team_id"] if "team_id" in t.keys() else "")==tid]
        proj_ids=list({t["project"] for t in team_tasks if t["project"]})
        projects=[]
        for pid in proj_ids:
            p=db.execute("SELECT * FROM projects WHERE id=? AND workspace_id=?",(pid,wid())).fetchone()
            if p: projects.append(dict(p))
        member_stats=[]
        for uid in member_ids:
            u=db.execute("SELECT id,name,email,role,avatar,color FROM users WHERE id=?",(uid,)).fetchone()
            if not u: continue
            mtasks=[t for t in team_tasks if t["assignee"]==uid]
            member_stats.append({
                "id":uid,"name":u["name"],"role":u["role"],"avatar":u["avatar"],"color":u["color"],
                "total":len(mtasks),
                "completed":len([t for t in mtasks if t["stage"]=="completed"]),
                "in_progress":len([t for t in mtasks if t["stage"] in ("development","in-progress","code_review","testing","uat")]),
                "blocked":len([t for t in mtasks if t["stage"]=="blocked"]),
                "overdue":len([t for t in mtasks if t["due"] and t["due"]<now_ist().strftime("%Y-%m-%dT%H:%M:%S") and t["stage"]!="completed"]),
            })
        total=len(team_tasks)
        return jsonify({
            "team":dict(team),
            "projects":projects,
            "tasks":[dict(t) for t in team_tasks],
            "member_stats":member_stats,
            "summary":{
                "total_projects":len(projects),
                "total_tasks":total,
                "completed":len([t for t in team_tasks if t["stage"]=="completed"]),
                "in_progress":len([t for t in team_tasks if t["stage"] in ("development","in-progress","code_review","testing","uat")]),
                "blocked":len([t for t in team_tasks if t["stage"]=="blocked"]),
                "pending":len([t for t in team_tasks if t["stage"] in ("backlog","planning")]),
            }
        })

# ── Tickets ───────────────────────────────────────────────────────────────────
@app.route("/api/tickets", methods=["GET"])
@login_required
def get_tickets():
    status=request.args.get("status","")
    team_id=request.args.get("team_id","")
    with get_db() as db:
        if team_id:
            team=db.execute("SELECT member_ids FROM teams WHERE id=? AND workspace_id=?",(team_id,wid())).fetchone()
            member_ids=json.loads(team["member_ids"] if team else "[]")
            team_projs=db.execute("SELECT id FROM projects WHERE workspace_id=? AND team_id=?",(wid(),team_id)).fetchall()
            proj_ids=[p["id"] for p in team_projs]
            all_rows=db.execute("SELECT * FROM tickets WHERE workspace_id=? ORDER BY created DESC",(wid(),)).fetchall()
            mem_set=set(member_ids); proj_set=set(proj_ids)
            rows=[r for r in all_rows if
                (r["team_id"] if "team_id" in r.keys() else "")==team_id or
                (r["assignee"] and r["assignee"] in mem_set) or
                (r["project"] and r["project"] in proj_set)]
            if status: rows=[r for r in rows if r["status"]==status]
        elif status:
            rows=db.execute("SELECT * FROM tickets WHERE workspace_id=? AND status=? ORDER BY created DESC",(wid(),status)).fetchall()
        else:
            rows=db.execute("SELECT * FROM tickets WHERE workspace_id=? ORDER BY created DESC",(wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/tickets", methods=["POST"])
@login_required
def create_ticket():
    d=request.json or {}
    if not d.get("title"): return jsonify({"error":"title required"}),400
    tid=f"tkt{int(datetime.now().timestamp()*1000)}"
    now=ts()
    with get_db() as db:
        db.execute("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (tid,wid(),d["title"],d.get("description",""),d.get("type","bug"),
                    d.get("priority","medium"),d.get("status","open"),d.get("assignee",""),
                    session["user_id"],d.get("project",""),json.dumps(d.get("tags",[])),now,now,
                    d.get("team_id","")))
        if d.get("assignee") and d["assignee"]!=session["user_id"]:
            nid=f"n{int(datetime.now().timestamp()*1000)}"
            reporter=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
            rname=reporter["name"] if reporter else "Someone"
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"task_assigned",f"🎫 {rname} assigned ticket: {d['title']}",d["assignee"],0,now))
        return jsonify(dict(db.execute("SELECT * FROM tickets WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()))

@app.route("/api/tickets/<tid>", methods=["PUT"])
@login_required
def update_ticket(tid):
    d=request.json or {}
    with get_db() as db:
        cu=db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        cu_role=cu["role"] if cu else "Viewer"
        if cu_role=="Developer":
            allowed_fields = {"status"}
            if not set(d.keys()).issubset(allowed_fields):
                return jsonify({"error":"Developers can only update ticket status."}),403
        t=db.execute("SELECT * FROM tickets WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()
        if not t: return jsonify({"error":"not found"}),404
        now=ts()
        cur_team_id = t["team_id"] if "team_id" in t.keys() else ""
        db.execute("UPDATE tickets SET title=?,description=?,type=?,priority=?,status=?,assignee=?,project=?,tags=?,updated=?,team_id=? WHERE id=?",
                   (d.get("title",t["title"]),d.get("description",t["description"]),
                    d.get("type",t["type"]),d.get("priority",t["priority"]),
                    d.get("status",t["status"]),d.get("assignee",t["assignee"]),
                    d.get("project",t["project"]),json.dumps(d.get("tags",json.loads(t["tags"] or "[]"))),now,
                    d.get("team_id",cur_team_id),tid))
        return jsonify(dict(db.execute("SELECT * FROM tickets WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()))

@app.route("/api/tickets/<tid>", methods=["DELETE"])
@login_required
def delete_ticket(tid):
    with get_db() as db:
        cu=db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        cu_role=cu["role"] if cu else "Viewer"
        if cu_role not in ("Admin","Manager","TeamLead"):
            return jsonify({"error":"Only Admin, Manager, or TeamLead can delete tickets."}),403
        db.execute("DELETE FROM tickets WHERE id=? AND workspace_id=?",(tid,wid()))
        db.execute("DELETE FROM ticket_comments WHERE ticket_id=? AND workspace_id=?",(tid,wid()))
        return jsonify({"ok":True})

@app.route("/api/tickets/<tid>/comments", methods=["GET"])
@login_required
def get_ticket_comments(tid):
    with get_db() as db:
        rows=db.execute("SELECT * FROM ticket_comments WHERE ticket_id=? AND workspace_id=? ORDER BY created",(tid,wid())).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/tickets/<tid>/comments", methods=["POST"])
@login_required
def add_ticket_comment(tid):
    d=request.json or {}
    if not d.get("content"): return jsonify({"error":"content required"}),400
    cid=f"tc{int(datetime.now().timestamp()*1000)}"
    with get_db() as db:
        db.execute("INSERT INTO ticket_comments VALUES (?,?,?,?,?,?)",
                   (cid,wid(),tid,session["user_id"],d["content"],ts()))
        return jsonify(dict(db.execute("SELECT * FROM ticket_comments WHERE id=?",(cid,)).fetchone()))

# ── Calls (Huddle) ────────────────────────────────────────────────────────────

@app.route("/api/migrate-timelog", methods=["GET","POST"])
def migrate_timelog_public():
    """Public migration — hit this URL once after deploy to fix live DB schema."""
    results = []
    steps = [
        ("CREATE time_logs base", """CREATE TABLE IF NOT EXISTS time_logs (
            id TEXT PRIMARY KEY, workspace_id TEXT, user_id TEXT,
            hours REAL DEFAULT 0, minutes INTEGER DEFAULT 0,
            comments TEXT DEFAULT '', created TEXT)"""),
        ("ADD team_id",    "ALTER TABLE time_logs ADD COLUMN team_id    TEXT DEFAULT ''"),
        ("ADD date",       "ALTER TABLE time_logs ADD COLUMN date       TEXT DEFAULT ''"),
        ("ADD task_name",  "ALTER TABLE time_logs ADD COLUMN task_name  TEXT DEFAULT ''"),
        ("ADD project_id", "ALTER TABLE time_logs ADD COLUMN project_id TEXT DEFAULT ''"),
        ("ADD task_id",    "ALTER TABLE time_logs ADD COLUMN task_id    TEXT DEFAULT ''"),
        ("ADD req_hours",  "ALTER TABLE workspaces ADD COLUMN required_hours_per_day REAL DEFAULT 8"),
    ]
    for label, sql in steps:
        try:
            from pg8000.native import Connection as _C
            c = _C(**_parse_db_url(DATABASE_URL))
            try:
                c.run(sql)
                results.append({"step": label, "status": "ok"})
            except Exception as e:
                msg = str(e).lower()
                if any(x in msg for x in ["already exists","duplicate","column already","relation already"]):
                    results.append({"step": label, "status": "already_exists"})
                else:
                    results.append({"step": label, "status": "error", "msg": str(e)})
            finally:
                try: c.close()
                except: pass
        except Exception as e:
            results.append({"step": label, "status": "connect_error", "msg": str(e)})
    log.info("[migrate-timelog] %s", results)
    return jsonify({"ok": True, "results": results})


@app.route("/api/timelogs/setup", methods=["POST"])
@login_required
def timelogs_setup():
    ensure_timelog_schema()
    return jsonify({"ok": True})


@app.route("/api/timelogs", methods=["GET"])
@login_required
def get_timelogs():
    try:
        uid   = session["user_id"]
        role  = get_user_role()
        wid_  = wid()
        if role in ("Admin", "Manager"):
            rows = _raw_pg(
                "SELECT tl.*, u.name as user_name FROM time_logs tl "
                "LEFT JOIN users u ON tl.user_id=u.id "
                "WHERE tl.workspace_id=? ORDER BY tl.date DESC, tl.created DESC",
                (wid_,), fetch=True)
        else:
            rows = _raw_pg(
                "SELECT tl.*, u.name as user_name FROM time_logs tl "
                "LEFT JOIN users u ON tl.user_id=u.id "
                "WHERE tl.workspace_id=? AND tl.user_id=? "
                "ORDER BY tl.date DESC, tl.created DESC",
                (wid_, uid), fetch=True)
        return jsonify(rows or [])
    except Exception as e:
        log.error("[get_timelogs] %s", e)
        ensure_timelog_schema()
        return jsonify([])


@app.route("/api/timelogs", methods=["POST"])
@login_required
def create_timelog():
    d   = request.json or {}
    lid = f"tl{int(datetime.now().timestamp()*1000)}"
    values = (
        lid,
        wid(),
        session["user_id"],
        d.get("team_id", "") or "",
        d.get("date", now_ist().strftime("%Y-%m-%d")),
        d.get("task_name", "") or "",
        d.get("project_id", "") or "",
        d.get("task_id", "") or "",
        float(d.get("hours") or 0),
        int(d.get("minutes") or 0),
        d.get("comments", "") or "",
        ts()
    )
    sql = """INSERT INTO time_logs
             (id, workspace_id, user_id, team_id, date, task_name,
              project_id, task_id, hours, minutes, comments, created)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    try:
        _raw_pg(sql, values)
        return jsonify({"id": lid, "ok": True})
    except Exception as e:
        log.error("[timelog create] %s: %s", type(e).__name__, e)
        # Run schema fix then retry
        ensure_timelog_schema()
        try:
            _raw_pg(sql, values)
            log.info("[timelog] retry succeeded: %s", lid)
            return jsonify({"id": lid, "ok": True})
        except Exception as e2:
            log.error("[timelog retry failed] %s: %s", type(e2).__name__, e2)
            return jsonify({"error": str(e2)}), 500


@app.route("/api/timelogs/<log_id>", methods=["DELETE"])
@login_required
def delete_timelog(log_id):
    rows = _raw_pg("SELECT user_id FROM time_logs WHERE id=? AND workspace_id=?",
                   (log_id, wid()), fetch=True)
    if not rows:
        return jsonify({"error": "Not found"}), 404
    if rows[0]["user_id"] != session["user_id"] and get_user_role() not in ("Admin","Manager"):
        return jsonify({"error": "Forbidden"}), 403
    _raw_pg("DELETE FROM time_logs WHERE id=?", (log_id,))
    return jsonify({"ok": True})


@app.route("/api/timelogs/<log_id>", methods=["PUT"])
@login_required
def update_timelog(log_id):
    d = request.json or {}
    rows = _raw_pg("SELECT user_id FROM time_logs WHERE id=? AND workspace_id=?",
                   (log_id, wid()), fetch=True)
    if not rows:
        return jsonify({"error": "Not found"}), 404
    if rows[0]["user_id"] != session["user_id"] and get_user_role() not in ("Admin","Manager"):
        return jsonify({"error": "Forbidden"}), 403
    _raw_pg("UPDATE time_logs SET hours=?, minutes=?, comments=? WHERE id=?",
            (float(d.get("hours") or 0), int(d.get("minutes") or 0),
             d.get("comments", "") or "", log_id))
    return jsonify({"ok": True, "id": log_id})


@app.route("/api/timelogs/required-hours", methods=["GET", "POST"])
@login_required
def required_hours():
    if request.method == "GET":
        try:
            rows = _raw_pg("SELECT required_hours_per_day FROM workspaces WHERE id=?",
                           (wid(),), fetch=True)
            hrs = float(rows[0]["required_hours_per_day"]) if rows and rows[0].get("required_hours_per_day") is not None else 8.0
        except Exception:
            hrs = 8.0
        return jsonify({"hours": hrs})
    # POST — Admin or Manager only (role fetched from DB since not in session)
    if get_user_role() not in ("Admin", "Manager"):
        return jsonify({"error": "Forbidden"}), 403
    hrs = float((request.json or {}).get("hours", 8))
    _run_ddl("ALTER TABLE workspaces ADD COLUMN required_hours_per_day REAL DEFAULT 8")
    try:
        _raw_pg("UPDATE workspaces SET required_hours_per_day=? WHERE id=?", (hrs, wid()))
        return jsonify({"ok": True})
    except Exception as e:
        log.error("[required_hours] %s", e)
        return jsonify({"error": str(e)}), 500


# [Calling/WebRTC mechanism removed — use Google Meet or external tools]

@app.route("/api/reminders/due", methods=["GET"])
@login_required
def due_reminders():
    """Return reminders that should fire now (within last 2 min, not yet fired)"""
    now=ts()
    with get_db() as db:
        rows=db.execute("""SELECT * FROM reminders WHERE workspace_id=? AND user_id=?
            AND fired=0 AND remind_at <= ?""",(wid(),session["user_id"],now)).fetchall()
        ids=[r["id"] for r in rows]
        if ids:
            db.execute(f"UPDATE reminders SET fired=1 WHERE id IN ({','.join('?'*len(ids))})",ids)
        return jsonify([dict(r) for r in rows])

# ── Notifications ─────────────────────────────────────────────────────────────
@app.route("/api/notifications")
@login_required
def get_notifs():
    with get_db() as db:
        rows=db.execute("""SELECT * FROM notifications WHERE workspace_id=? AND user_id=?
            ORDER BY ts DESC LIMIT 50""",(wid(),session["user_id"])).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/notifications/read-all",methods=["PUT"])
@login_required
def notifs_read_all():
    with get_db() as db:
        db.execute("UPDATE notifications SET read=1 WHERE workspace_id=?",(wid(),))
        return jsonify({"ok":True})

@app.route("/api/notifications/all",methods=["DELETE"])
@login_required
def notifs_clear_all():
    with get_db() as db:
        db.execute("DELETE FROM notifications WHERE workspace_id=?",(wid(),))
        return jsonify({"ok":True})

@app.route("/api/notifications/<nid>", methods=["DELETE"])
@login_required
def delete_notif(nid):
    with get_db() as db:
        db.execute("DELETE FROM notifications WHERE id=? AND user_id=?",(nid,session["user_id"]))
        return jsonify({"ok":True})

@app.route("/api/notifications/<nid>/read",methods=["PUT"])
@login_required
def read_notif(nid):
    with get_db() as db:
        db.execute("UPDATE notifications SET read=1 WHERE id=? AND workspace_id=?",(nid,wid()))
        return jsonify({"ok":True})

# ── Web Push API ───────────────────────────────────────────────────────────────
@app.route("/api/push/vapid-key", methods=["GET"])
def get_vapid_public_key():
    """Return VAPID public key for frontend subscription."""
    vapid = get_vapid_keys()
    return jsonify({"publicKey": vapid.get("public", "")})

@app.route("/api/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    """Save a Web Push subscription for the current user."""
    d = request.json or {}
    endpoint = d.get("endpoint")
    keys = d.get("keys", {})
    if not endpoint:
        return jsonify({"error": "endpoint required"}), 400
    sub_id = f"ps{int(datetime.now().timestamp()*1000)}"
    with get_db() as db:
        db.execute("""INSERT OR REPLACE INTO push_subscriptions
            (id, user_id, workspace_id, endpoint, p256dh, auth, created)
            VALUES (
                COALESCE((SELECT id FROM push_subscriptions WHERE endpoint=?), ?),
                ?, ?, ?, ?, ?, ?
            )""", (endpoint, sub_id, session["user_id"], wid(),
                   endpoint, keys.get("p256dh",""), keys.get("auth",""), ts()))
    return jsonify({"ok": True})

@app.route("/api/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe():
    """Remove a Web Push subscription."""
    d = request.json or {}
    endpoint = d.get("endpoint")
    with get_db() as db:
        if endpoint:
            db.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND user_id=?",(endpoint, session["user_id"]))
        else:
            db.execute("DELETE FROM push_subscriptions WHERE user_id=?", (session["user_id"],))
    return jsonify({"ok": True})

# ── AI Assistant ──────────────────────────────────────────────────────────────
@app.route("/api/ai/chat",methods=["POST"])
@login_required
def ai_chat():
    d=request.json or {}
    user_msg=d.get("message","").strip()
    history=d.get("history",[])
    if not user_msg: return jsonify({"error":"Empty message"}),400

    with get_db() as db:
        ws=db.execute("SELECT * FROM workspaces WHERE id=?",(wid(),)).fetchone()
        api_key=(ws["ai_api_key"] if ws and ws["ai_api_key"] else "").strip()
        if not api_key:
            return jsonify({"error":"NO_KEY","message":"Please configure your Anthropic API key in Workspace Settings (⚙) to enable AI features."}),400

        projects=db.execute("SELECT id,name,description,target_date,color FROM projects WHERE workspace_id=?",(wid(),)).fetchall()
        tasks=db.execute("SELECT id,title,stage,priority,assignee,project,due,pct FROM tasks WHERE workspace_id=?",(wid(),)).fetchall()
        users=db.execute("SELECT id,name,role FROM users WHERE workspace_id=?",(wid(),)).fetchall()
        cu=db.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone()

    proj_ctx="\n".join([f"- {p['name']} (id:{p['id']}, due:{p['target_date']})" for p in projects])
    task_ctx="\n".join([f"- [{t['id']}] {t['title']} | stage:{t['stage']} | priority:{t['priority']} | pct:{t['pct']}%" for t in tasks])
    user_ctx="\n".join([f"- {u['name']} (id:{u['id']}, role:{u['role']})" for u in users])

    system=f"""You are an AI assistant for VEWIT — a project management tool used by the workspace "{ws['name'] if ws else 'Unknown'}".
Current user: {cu['name']} (role: {cu['role']})
Today: {datetime.now().strftime('%Y-%m-%d')}

PROJECTS:
{proj_ctx or 'No projects yet.'}

TASKS:
{task_ctx or 'No tasks yet.'}

TEAM MEMBERS:
{user_ctx}

You can answer questions, analyze status, and PERFORM ACTIONS by including JSON in your reply like:
<action>{{"type":"create_task","title":"Task name","project":"project_id","priority":"high","stage":"backlog","assignee":"user_id","due":"YYYY-MM-DD","description":"details"}}</action>
<action>{{"type":"update_task","task_id":"T-001","stage":"testing","pct":75}}</action>
<action>{{"type":"create_project","name":"Project Name","description":"desc","color":"#5a8cff","members":["user_id"]}}</action>
<action>{{"type":"eod_report"}}</action>

IMPORTANT: Always be helpful and concise. When performing actions, explain what you did. For EOD reports, summarize all task statuses by project."""

    msgs=[{"role":"user" if m["role"]=="user" else "assistant","content":m["content"]} for m in history[-10:]]
    msgs.append({"role":"user","content":user_msg})

    try:
        req_data=json.dumps({"model":"claude-sonnet-4-5","max_tokens":1500,"system":system,"messages":msgs}).encode()
        req=urllib.request.Request("https://api.anthropic.com/v1/messages",
            data=req_data,method="POST",
            headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"})
        with urllib.request.urlopen(req,timeout=30) as resp:
            result=json.loads(resp.read().decode())
            ai_text=result["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body=e.read().decode()
        if e.code==401: return jsonify({"error":"INVALID_KEY","message":"Invalid API key. Check your key in Workspace Settings."}),400
        return jsonify({"error":"API_ERROR","message":f"Anthropic API error: {body[:200]}"}),500
    except Exception as e:
        return jsonify({"error":"NETWORK_ERROR","message":f"Could not reach AI: {str(e)}"}),500

    import re
    actions_raw=re.findall(r'<action>(.*?)</action>',ai_text,re.DOTALL)
    action_results=[]
    clean_text=re.sub(r'<action>.*?</action>','',ai_text,flags=re.DOTALL).strip()

    for ar in actions_raw:
        try:
            act=json.loads(ar.strip())
            atype=act.get("type","")
            with get_db() as db:
                if atype=="create_task":
                    tid=next_task_id(db,wid())
                    db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                               (tid,wid(),act.get("title","New Task"),act.get("description",""),
                                act.get("project",""),act.get("assignee",""),
                                act.get("priority","medium"),act.get("stage","backlog"),
                                ts(),act.get("due",""),0,"[]"))
                    action_results.append({"type":"create_task","id":tid,"title":act.get("title")})
                elif atype=="update_task":
                    tid=act.get("task_id","")
                    t=db.execute("SELECT * FROM tasks WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()
                    if t:
                        db.execute("UPDATE tasks SET stage=?,pct=?,priority=?,assignee=? WHERE id=? AND workspace_id=?",
                                   (act.get("stage",t["stage"]),act.get("pct",t["pct"]),
                                    act.get("priority",t["priority"]),act.get("assignee",t["assignee"]),tid,wid()))
                        action_results.append({"type":"update_task","id":tid})
                elif atype=="create_project":
                    pid=f"p{int(datetime.now().timestamp()*1000)}"
                    mems=act.get("members",[session["user_id"]])
                    db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (pid,wid(),act.get("name","New Project"),act.get("description",""),
                                session["user_id"],json.dumps(mems),"",act.get("target_date",""),0,
                                act.get("color","#5a8cff"),ts()))
                    action_results.append({"type":"create_project","id":pid,"name":act.get("name")})
                elif atype=="eod_report":
                    rows=db.execute("SELECT t.*,p.name as pname FROM tasks t LEFT JOIN projects p ON t.project=p.id WHERE t.workspace_id=?",(wid(),)).fetchall()
                    by_stage={}
                    for r in rows:
                        s=r["stage"]
                        by_stage.setdefault(s,[]).append(r["title"])
                    report_lines=[]
                    for st,titles in by_stage.items():
                        report_lines.append(f"**{st.upper()}** ({len(titles)}): "+", ".join(titles[:3])+("..." if len(titles)>3 else ""))
                    action_results.append({"type":"eod_report","summary":"\n".join(report_lines)})
        except Exception as ex:
            action_results.append({"type":"error","message":str(ex)})

    return jsonify({"message":clean_text,"actions":action_results,"raw":ai_text})

@app.route("/api/ai/generate-docs",methods=["POST"])
@login_required
def ai_generate_docs():
    """Generate documentation from user description + workspace data."""
    d = request.json or {}
    doc_type = d.get("type", "documentation")
    project_id = d.get("project_id", "")
    user_description = d.get("context", "").strip()
    tech_stack = d.get("tech_stack", "").strip()
    audience = d.get("audience", "technical")

    with get_db() as db:
        ws = db.execute("SELECT * FROM workspaces WHERE id=?", (wid(),)).fetchone()
        api_key = (ws["ai_api_key"] if ws and ws["ai_api_key"] else "").strip()
        if not api_key:
            return jsonify({"error": "NO_KEY", "message": "Configure your Anthropic API key in Settings → AI Assistant."}), 400

        if project_id:
            projects = db.execute("SELECT * FROM projects WHERE id=? AND workspace_id=?", (project_id, wid())).fetchall()
            tasks = db.execute(
                "SELECT t.*, u.name as assignee_name FROM tasks t LEFT JOIN users u ON t.assignee=u.id "
                "WHERE t.project=? AND t.workspace_id=?", (project_id, wid())).fetchall()
        else:
            projects = db.execute("SELECT * FROM projects WHERE workspace_id=?", (wid(),)).fetchall()
            tasks = db.execute(
                "SELECT t.*, u.name as assignee_name FROM tasks t LEFT JOIN users u ON t.assignee=u.id "
                "WHERE t.workspace_id=?", (wid(),)).fetchall()

        users_db = db.execute("SELECT id,name,role,email FROM users WHERE workspace_id=?", (wid(),)).fetchall()
        teams = db.execute("SELECT * FROM teams WHERE workspace_id=?", (wid(),)).fetchall()
        tickets = db.execute("SELECT id,title,type,status,priority FROM tickets WHERE workspace_id=? LIMIT 20", (wid(),)).fetchall()

    # Build rich workspace context
    proj_ctx = "\n".join([
        f"### {p['name']}\n- ID: {p['id']}\n- Description: {p['description'] or 'N/A'}"
        f"\n- Target date: {p['target_date'] or 'N/A'}\n- Progress: {p['progress'] or 0}%"
        for p in projects]) or "No projects yet"

    by_stage = {}
    for t in tasks:
        by_stage.setdefault(t['stage'], []).append(t)
    task_ctx = "\n".join([
        f"**{stage.upper()}** ({len(ts)} tasks): " + ", ".join([f"{t['title']} [{t['priority']}]" for t in ts[:5]])
        + ("..." if len(ts)>5 else "")
        for stage, ts in by_stage.items()])[:3000] or "No tasks"

    user_ctx = "\n".join([f"- {u['name']} ({u['role']})" for u in users_db])
    team_ctx = "\n".join([f"- {t['name']}" for t in teams]) or "No sub-teams"
    ticket_ctx = "\n".join([f"- [{t['id']}] {t['title']} | {t['type']} | {t['status']} | {t['priority']}" for t in tickets]) or "No tickets"

    workspace_data = f"""
WORKSPACE NAME: {ws['name'] if ws else 'Unknown'}
TOTAL PROJECTS: {len(projects)}
TOTAL TASKS: {len(tasks)}
TEAM MEMBERS ({len(users_db)}):
{user_ctx}
SUB-TEAMS: {team_ctx}

PROJECTS:
{proj_ctx}

TASKS BY STAGE:
{task_ctx}

OPEN TICKETS (sample):
{ticket_ctx}
"""

    user_desc_block = f"""
USER DESCRIPTION:
{user_description if user_description else '(Not provided — use workspace data above)'}

TECH STACK: {tech_stack if tech_stack else 'Not specified'}
TARGET AUDIENCE: {audience}
"""

    audience_note = {
        "technical": "Write for software engineers and architects. Include technical details, code examples, data models, and API references where appropriate.",
        "business": "Write for business stakeholders. Avoid jargon. Focus on value, goals, timelines, risks, and business outcomes. Use plain language.",
        "both": "Write for both technical and business audiences. Use clear sections — business summary first, technical details later."
    }.get(audience, "")

    if doc_type == "architecture":
        prompt = f"""You are a senior software architect. Generate a comprehensive architecture documentation with Mermaid.js diagrams.
{user_desc_block}
{workspace_data}

{audience_note}

Generate the following:

## 1. System Architecture Diagram
```mermaid
flowchart TD
  (show the main system components, services, databases, and how they connect)
```

## 2. Data Flow Diagram  
```mermaid
sequenceDiagram
  (show key user interactions and data flows between components)
```

## 3. Component Overview
Describe each major component in 2-3 sentences.

## 4. Technology Stack
List all technologies with their role in the system.

## 5. Deployment Architecture
Describe how the system is deployed (cloud, containers, etc.).

Be specific based on the description provided. If tech stack is mentioned, use it in the diagrams."""

    elif doc_type == "technical":
        prompt = f"""You are a senior technical writer. Generate a detailed technical specification document.
{user_desc_block}
{workspace_data}

{audience_note}

Generate a complete Technical Specification including:

# Technical Specification

## 1. Overview & Purpose
## 2. System Architecture
## 3. Technology Stack
| Layer | Technology | Purpose |
(create a table)
## 4. Data Models
Describe key entities and their relationships with field-level details.
## 5. API Design
List key API endpoints with method, path, purpose, request/response schema.
## 6. Authentication & Security
## 7. Performance Requirements
## 8. Integration Points
## 9. Error Handling Strategy
## 10. Testing Strategy
## 11. Deployment & DevOps

Be comprehensive and specific. Use tables, code blocks, and diagrams where appropriate."""

    elif doc_type == "api":
        prompt = f"""You are an API documentation expert. Generate complete API reference documentation.
{user_desc_block}
{workspace_data}

Generate comprehensive API Documentation including:

# API Reference

## Authentication
How to authenticate — JWT, API keys, OAuth, etc.

## Base URL & Versioning

## Endpoints
For each major resource area (based on the project description), document:
### Resource Name
#### GET /resource
- **Description**: What it returns
- **Auth required**: Yes/No
- **Query params**: List with type and description
- **Response**: JSON schema with example
#### POST /resource
(repeat for all CRUD operations)

## Error Codes
| Code | Message | Description |
(table of all error codes)

## Rate Limiting
## Webhooks (if applicable)
## SDK Examples
Show code examples in Python and JavaScript.

Base the endpoints on the actual project description and workspace task data."""

    else:  # documentation
        prompt = f"""You are a professional technical writer. Generate comprehensive project documentation.
{user_desc_block}
{workspace_data}

{audience_note}

Generate a complete Project Documentation including:

# Project Documentation

## Executive Summary
2-3 paragraphs covering what the project is, its primary goals, and current status.

## Project Overview
- **Vision**: 
- **Problem Statement**:
- **Solution**:
- **Key Stakeholders**:

## Scope & Features
### In Scope
### Out of Scope
### Key Features (table with feature, status, priority)

## Team Structure
| Name | Role | Responsibilities |
(table from workspace member data)

## Current Status
Summary of progress with a status table per project.

## Task Breakdown
Organized by stage — Completed, In Progress, In Review, Blocked, Backlog.

## Timeline & Milestones
| Milestone | Target Date | Status |

## Risks & Blockers
| Risk | Impact | Mitigation |

## Technical Architecture
Brief description of the tech stack and architecture.

## Next Steps & Recommendations
Prioritized list of immediate actions.

---
*Generated by VEWIT AI · {datetime.now().strftime('%B %d, %Y')}*"""

    try:
        req_data = json.dumps({
            "model": "claude-sonnet-4-5",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=req_data, method="POST",
            headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            result_json = json.loads(resp.read().decode())
            content = result_json["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 401:
            return jsonify({"error": "INVALID_KEY", "message": "Invalid API key. Check your key in Settings."}), 400
        return jsonify({"error": "API_ERROR", "message": f"API error {e.code}: {body[:300]}"}), 500
    except Exception as e:
        return jsonify({"error": "NETWORK_ERROR", "message": str(e)}), 500

    return jsonify({
        "content": content,
        "type": doc_type,
        "projects": [p["name"] for p in projects],
        "task_count": len(tasks)
    })




@app.route("/api/export/csv")
@login_required
def export_csv():
    with get_db() as db:
        tasks=db.execute("SELECT * FROM tasks WHERE workspace_id=?",(wid(),)).fetchall()
    lines=["id,title,project,assignee,priority,stage,due,pct"]
    for t in tasks:
        lines.append(f'"{t["id"]}","{t["title"]}","{t["project"]}","{t["assignee"]}","{t["priority"]}","{t["stage"]}","{t["due"]}","{t["pct"]}"')
    return Response("\n".join(lines),mimetype="text/csv",
                    headers={"Content-Disposition":"attachment;filename=tasks.csv"})

@app.route("/api/import/csv", methods=["POST"])
@login_required
def import_csv():
    """Import tasks (and optionally projects) from CSV upload."""
    import csv, io
    f = request.files.get("file")
    if not f: return jsonify({"error":"No file uploaded"}), 400
    try:
        content = f.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
    except Exception as e:
        return jsonify({"error": f"Could not parse CSV: {e}"}), 400

    created_projects = 0
    created_tasks = 0
    errors = []
    with get_db() as db:
        for i, row in enumerate(reader):
            try:
                row = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
                proj_id = row.get("project_id", "").strip()
                proj_name = row.get("project", row.get("project_name", "")).strip()
                if proj_name and not proj_id:
                    existing = db.execute(
                        "SELECT id FROM projects WHERE workspace_id=? AND name=?", (wid(), proj_name)
                    ).fetchone()
                    if existing:
                        proj_id = existing["id"]
                    else:
                        proj_id = f"p{int(datetime.now().timestamp()*1000)+i}"
                        db.execute(
                            "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (proj_id, wid(), proj_name, "", session["user_id"],
                             json.dumps([session["user_id"]]), "", "", 0, "#5a8cff", ts())
                        )
                        created_projects += 1
                title = row.get("title", row.get("task", row.get("task_title", ""))).strip()
                if not title:
                    errors.append(f"Row {i+2}: missing title, skipped")
                    continue
                valid_stages = set(["backlog","planning","development","code_review","testing","uat","release","production","completed","blocked"])
                stage = row.get("stage", "backlog").strip()
                if stage not in valid_stages: stage = "backlog"
                valid_pris = {"critical","high","medium","low"}
                pri = row.get("priority", "medium").strip().lower()
                if pri not in valid_pris: pri = "medium"
                due = row.get("due", row.get("due_date", "")).strip()
                pct_raw = row.get("pct", row.get("progress", row.get("completion", "0"))).strip().replace("%","")
                try: pct = int(float(pct_raw))
                except: pct = 0
                assignee_id = row.get("assignee_id", row.get("assignee", "")).strip()
                if assignee_id and not assignee_id.startswith("u"):
                    u = db.execute("SELECT id FROM users WHERE workspace_id=? AND name=?", (wid(), assignee_id)).fetchone()
                    if u: assignee_id = u["id"]
                    else: assignee_id = ""
                tid = next_task_id(db, wid())
                db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (tid, wid(), title, row.get("description",""), proj_id,
                            assignee_id, pri, stage, ts(), due, pct, "[]"))
                created_tasks += 1
            except Exception as e:
                errors.append(f"Row {i+2}: {e}")

    return jsonify({
        "ok": True,
        "created_tasks": created_tasks,
        "created_projects": created_projects,
        "errors": errors
    })

# ── Serve ─────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    now = datetime.utcnow()
    uptime_seconds = int((now - APP_STARTED_AT).total_seconds())
    try:
        db_check_started = time.perf_counter()
        with get_db() as db:
            db.execute("SELECT 1")
        db_latency_ms = round((time.perf_counter() - db_check_started) * 1000, 2)
        return jsonify({
            "status":"ok",
            "service":"ProjectFlow",
            "version":"4.0",
            "timestamp":now.isoformat(timespec="seconds") + "Z",
            "uptime_seconds":uptime_seconds,
            "database":{"status":"ok","latency_ms":db_latency_ms}
        }), 200
    except Exception as e:
        return jsonify({
            "status":"error",
            "service":"ProjectFlow",
            "version":"4.0",
            "timestamp":now.isoformat(timespec="seconds") + "Z",
            "uptime_seconds":uptime_seconds,
            "database":{"status":"error","detail":str(e)}
        }), 500

@app.route("/api/auth/emergency-reset-2fa", methods=["POST"])
def emergency_reset_2fa():
    """Emergency endpoint to disable ALL 2FA workspace-wide.
    Requires the workspace invite code as proof of ownership.
    Use this if you're locked out."""
    d = request.json or {}
    invite_code = d.get("invite_code","").strip().upper()
    email = d.get("email","").strip().lower()
    if not invite_code or not email:
        return jsonify({"error":"invite_code and email required"}),400
    with get_db() as db:
        ws = db.execute("SELECT * FROM workspaces WHERE invite_code=?", (invite_code,)).fetchone()
        if not ws:
            return jsonify({"error":"Invalid invite code"}),403
        u = db.execute("SELECT id FROM users WHERE email=? AND workspace_id=?", (email, ws["id"])).fetchone()
        if not u:
            return jsonify({"error":"Email not found in this workspace"}),404
        # Reset all 2FA for the workspace
        db.execute("UPDATE workspaces SET otp_enabled=0 WHERE id=?", (ws["id"],))
        db.execute("UPDATE users SET two_fa_enabled=0, totp_secret='', totp_verified=0 WHERE workspace_id=?", (ws["id"],))
        return jsonify({"ok":True,"message":"All 2FA reset. You can now log in with email + password."})



@app.route("/static/<path:fn>")
def serve_static(fn):
    """Serve static files (frontend.js, landing.html, etc.) from app directory."""
    # Try every plausible location for the file
    locations = [
        os.path.join(BASE_DIR, fn),                # Same directory as app.py  ← most common
        os.path.join(BASE_DIR, "static", fn),      # static/ subdirectory
        os.path.join(JS_DIR, fn),                  # pf_static/ (downloaded libs)
        os.path.join(BASE_DIR, "pf_static", fn),   # explicit pf_static path
        os.path.join(BASE_DIR, "..", fn),           # Parent directory
        os.path.join("/app", fn),                  # Railway /app root
        os.path.join("/app", "static", fn),        # Railway /app/static
    ]

    path = None
    for loc in locations:
        if os.path.exists(loc) and os.path.isfile(loc):
            path = loc
            break

    if not path:
        # Special case: if frontend.js is missing, return a helpful JS error
        # instead of an HTML 404 (which causes the MIME-type rejection)
        if fn == "frontend.js":
            err_js = (
                "console.error('[VEWIT] frontend.js not found on server. "
                "Make sure frontend.js is deployed in the same directory as app.py.');"
                "document.body.innerHTML='<div style=\"color:#f87171;font-family:monospace;"
                "padding:40px;background:#0a0618;min-height:100vh\">"
                "<h2>⚠ frontend.js missing</h2>"
                "<p>Deploy frontend.js to the same folder as app.py on your server.</p>"
                "</div>';"
            )
            return Response(err_js, mimetype="application/javascript",
                            headers={"Cache-Control": "no-cache"})
        print(f"  ⚠ Static file not found: {fn}")
        print(f"     Searched: {locations}")
        return "", 404

    import mimetypes as _mt
    mime = _mt.guess_type(fn)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        data = fh.read()
    resp = Response(data, mimetype=mime)
    resp.headers["Cache-Control"] = "public, max-age=3600" if fn.endswith(".js") else "no-cache"
    return resp

@app.route("/js/<path:fn>")
def serve_js(fn):
    path=os.path.join(JS_DIR,fn)
    if os.path.exists(path) and os.path.getsize(path)>1000:
        mime,_=mimetypes.guess_type(fn)
        return Response(open(path,"rb").read(),mimetype=mime or "application/javascript",
                        headers={"Cache-Control":"public,max-age=86400"})
    CDN={
        "react.min.js":     "https://cdnjs.cloudflare.com/ajax/libs/react/18.2.0/umd/react.production.min.js",
        "react-dom.min.js": "https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.2.0/umd/react-dom.production.min.js",
        "prop-types.min.js":"https://cdnjs.cloudflare.com/ajax/libs/prop-types/15.8.1/prop-types.min.js",
        "recharts.min.js":  "https://cdnjs.cloudflare.com/ajax/libs/recharts/2.12.7/Recharts.js",
        "htm.min.js":       "https://unpkg.com/htm@3.1.1/dist/htm.js",
    }
    if fn in CDN:
        from flask import redirect
        return redirect(CDN[fn], code=302)
    return "Not Found", 404

@app.route("/sw.js")
def serve_sw():
    """Service Worker for background push notifications and offline caching."""
    sw_code = r"""
// VEWIT Service Worker v2
const CACHE = 'pf-v2';
const ICON = '/favicon.ico';

// Install & cache shell assets
self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(clients.claim());
});

// ── Push notification handler ────────────────────────────────────────────────
self.addEventListener('push', e => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch(err) {}
  const title  = data.title  || 'VEWIT';
  const body   = data.body   || '';
  const tag    = data.tag    || 'pf-notif';
  const url    = data.url    || '/';
  const icon   = data.icon   || ICON;
  const badge  = data.badge  || ICON;
  const opts = {
    body, tag, icon, badge,
    vibrate: [200, 100, 200],
    requireInteraction: data.requireInteraction || false,
    data: { url },
    actions: [
      { action: 'open',    title: 'Open'    },
      { action: 'dismiss', title: 'Dismiss' }
    ]
  };
  e.waitUntil(self.registration.showNotification(title, opts));
});

// ── Notification click handler ───────────────────────────────────────────────
self.addEventListener('notificationclick', e => {
  e.notification.close();
  if (e.action === 'dismiss') return;
  const tag = (e.notification.data && e.notification.data.tag) || null;
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
      for (const c of cs) {
        if (c.url.includes(self.location.origin) && 'focus' in c) {
          c.focus();
          c.postMessage({ type: 'PF_NOTIF_CLICK', tag });
          return;
        }
      }
      if (clients.openWindow) return clients.openWindow('/');
    })
  );
});

// ── Background sync — poll notifications every 30s when visible ─────────────
self.addEventListener('message', e => {
  if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
});

// Periodic background fetch (Chrome 80+ with periodicSync)
self.addEventListener('periodicsync', e => {
  if (e.tag === 'pf-poll') {
    e.waitUntil(pollNotifications());
  }
});

async function pollNotifications() {
  try {
    const r = await fetch('/api/notifications', { credentials: 'include' });
    if (!r.ok) return;
    const notifs = await r.json();
    const unread = notifs.filter(n => !n.read);
    if (unread.length > 0) {
      const badge = navigator.setAppBadge || null;
      if (badge) navigator.setAppBadge(unread.length).catch(()=>{});
    }
  } catch(e) {}
}
"""
    return Response(sw_code, mimetype="application/javascript",
                    headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

@app.route("/manifest.json")
def serve_manifest():
    """PWA manifest — full desktop installability."""
    manifest = {
        "name": "VEWIT",
        "short_name": "PFPro",
        "description": "AI-powered team project management — tasks, huddles, timeline, tickets & more.",
        "start_url": "/dashboard",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "background_color": "#ffffff",
        "theme_color": "#1d4ed8",
        "orientation": "landscape-primary",
        "categories": ["productivity", "business", "collaboration"],
        "lang": "en",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": "/favicon.ico", "sizes": "48x48", "type": "image/x-icon"}
        ],
        "shortcuts": [
            {"name": "Dashboard", "short_name": "Dashboard", "url": "/dashboard", "description": "Go to your dashboard"},
            {"name": "New Task", "short_name": "New Task", "url": "/tasks", "description": "Create a new task"},
            {"name": "Projects", "short_name": "Projects", "url": "/projects", "description": "View all projects"}
        ],
        "screenshots": [
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "form_factor": "wide", "label": "VEWIT Dashboard"}
        ]
    }
    return jsonify(manifest)

@app.route("/favicon.ico")
@app.route("/favicon.png")
def favicon():
    """Serve the VEWIT blue favicon — same as icon-192 PNG."""
    import base64
    from flask import Response
    # Exact same blue VEWIT icon as icon-192
    png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAYAAABS3GwHAAAEuklEQVR4nO3UQQ5TSRAEUS7ClTj/3IZZITWDRgJsV2b/eiHF3r8rw1++AAAAAAAAAAAAAAAAAAAAAAAAhPj67Z/vnDV9cxykx7DR9M1xkB7DRtM3x0F6DL/r75D+jQK4kPQYXh39jTGkb46D9BjePfwbQkjfHAfpMXxy/K0RpG+Og/QYPj3+xgjSN8dBegwT42+LIH1zHKTHMDX+pgjSN8dBegwCQJT0GBKkvzl9cxykxyAARNk2/oYI0jfHgQAEsBoBCGA1G8f/AwFAAALYjQAEsBoBCGA1AhDAagQggNWkRpCOIPnd6ZvjQAACWI0ABLCa5BBSEaS/OX1zHKTHIABESY9hOoL0twqgjPQYBIAo6TFMRpD+RgEUkh7DVATpbxNAKekxTESQ/iYBFJMew6cjSH+LAMpJj0EAS0kfodmnB9CiAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKPOTpL+tUQEUOUH6G9sUQJECmFcAJU6S/tYmBVBggvQ3tyiAAgWQUwBhk6S/vUEBhBWAAFYrAAGstYH0G6QVgABWK4Clw/8v6TcRwBKbSb+NAB7sTaTfSgAP8mbSbyeAy30C6TcUwIU+kfSbCuACN5B+YwGUuon0WwugyM2k314Ahl9B+hYCMPwK0rcRgPHHSd9IAIZfQfpmAjD8CtI3FIDxx0nfUgCGX0H6tgIw/ArStxbARcP/k997+7cJYGDMtwxkS+Sv3E8AFxz4b/jEP93TEICjerPvr7/ZlQE8jYnhe8OHBPAkEsP3nhcH8CTSw/euAoiQHrv3vTSA20mP21tfHMDtpActAgFESI94cwgCCJIerRAeFMBNpEcqhJ8RwCDpYYrgVwQwQHqMQvh/BPBB0uNrshUBfIj04BptRABvJj2yG2xCAG8iPaobbUAAQ4/Ie28ngDc8Iu+9nQDe8Ii893YCePEBeff96gNIPmJ6PE+w/XYCePEBeff9rggg8Yjp0TzJ5tsJ4MUH5N33uyaAqUdMD+XJNt7vqgAmHjE9kifbeLvrAvjUQ6bHscmm+10ZwLsfMT2IjbbcLxpAC+kxbDR9cxykx7DR9M1xkB7DRtM3x0F6DBtN3xwH6TFsNH1zHKTHsNH0zXGQHsNG0zfHQXoMG03fHAfpMWw0fXMcpMew0fTNcZAew0bTN8dBegwbTd8cB+kxbDR9cxykx7DR9M1xkB7DRtM3x0F6DBtN3xwH6TFsNH1zHKTHsNH0zXGQHsNG0zfHQXoMG03fHAfpMWw0fXMcpMew0fTNcZAew0bTN8dBegwbTd8cB+kxbDR9cxykx7DR9M1xkB7DRtM3x0F6DBtN3xwH6TFsNH1zHKTHsNH0zXGQHsNG0zfHQXoMG03fHAfpMWw0fXMcpMew0fTNAQAAAAAAAAAAAAAAAAAAAGAx/wJoKCsUOqYWXQAAAABJRU5ErkJggg=="
    png_data = base64.b64decode(png_b64)
    return Response(png_data, mimetype='image/png',
        headers={'Cache-Control':'public,max-age=3600','Content-Disposition':'inline; filename="favicon.png"'})

@app.route("/icon-192.png")
def icon_192():
    """Real PNG icon — 192x192 blue app icon."""
    import base64
    png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAYAAABS3GwHAAAEuklEQVR4nO3UQQ5TSRAEUS7ClTj/3IZZITWDRgJsV2b/eiHF3r8rw1++AAAAAAAAAAAAAAAAAAAAAAAAhPj67Z/vnDV9cxykx7DR9M1xkB7DRtM3x0F6DL/r75D+jQK4kPQYXh39jTGkb46D9BjePfwbQkjfHAfpMXxy/K0RpG+Og/QYPj3+xgjSN8dBegwT42+LIH1zHKTHMDX+pgjSN8dBegwCQJT0GBKkvzl9cxykxyAARNk2/oYI0jfHgQAEsBoBCGA1G8f/AwFAAALYjQAEsBoBCGA1AhDAagQggNWkRpCOIPnd6ZvjQAACWI0ABLCa5BBSEaS/OX1zHKTHIABESY9hOoL0twqgjPQYBIAo6TFMRpD+RgEUkh7DVATpbxNAKekxTESQ/iYBFJMew6cjSH+LAMpJj0EAS0kfodmnB9CiAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKFUAMwqgVAHMKIBSBTCjAEoVwIwCKPOTpL+tUQEUOUH6G9sUQJECmFcAJU6S/tYmBVBggvQ3tyiAAgWQUwBhk6S/vUEBhBWAAFYrAAGstYH0G6QVgABWK4Clw/8v6TcRwBKbSb+NAB7sTaTfSgAP8mbSbyeAy30C6TcUwIU+kfSbCuACN5B+YwGUuon0WwugyM2k314Ahl9B+hYCMPwK0rcRgPHHSd9IAIZfQfpmAjD8CtI3FIDxx0nfUgCGX0H6tgIw/ArStxbARcP/k997+7cJYGDMtwxkS+Sv3E8AFxz4b/jEP93TEICjerPvr7/ZlQE8jYnhe8OHBPAkEsP3nhcH8CTSw/euAoiQHrv3vTSA20mP21tfHMDtpActAgFESI94cwgCCJIerRAeFMBNpEcqhJ8RwCDpYYrgVwQwQHqMQvh/BPBB0uNrshUBfIj04BptRABvJj2yG2xCAG8iPaobbUAAQ4/Ie28ngDc8Iu+9nQDe8Ii893YCePEBeff96gNIPmJ6PE+w/XYCePEBeff9rggg8Yjp0TzJ5tsJ4MUH5N33uyaAqUdMD+XJNt7vqgAmHjE9kifbeLvrAvjUQ6bHscmm+10ZwLsfMT2IjbbcLxpAC+kxbDR9cxykx7DR9M1xkB7DRtM3x0F6DBtN3xwH6TFsNH1zHKTHsNH0zXGQHsNG0zfHQXoMG03fHAfpMWw0fXMcpMew0fTNcZAew0bTN8dBegwbTd8cB+kxbDR9cxykx7DR9M1xkB7DRtM3x0F6DBtN3xwH6TFsNH1zHKTHsNH0zXGQHsNG0zfHQXoMG03fHAfpMWw0fXMcpMew0fTNcZAew0bTN8dBegwbTd8cB+kxbDR9cxykx7DR9M1xkB7DRtM3x0F6DBtN3xwH6TFsNH1zHKTHsNH0zXGQHsNG0zfHQXoMG03fHAfpMWw0fXMcpMew0fTNAQAAAAAAAAAAAAAAAAAAAGAx/wJoKCsUOqYWXQAAAABJRU5ErkJggg=="
    png_data = base64.b64decode(png_b64)
    return Response(png_data, mimetype='image/png',
        headers={'Cache-Control':'public,max-age=86400'})

@app.route("/icon-512.png")
def icon_512():
    """Real PNG icon — 512x512 blue app icon."""
    import base64
    png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAAXNUlEQVR4nO3WW65cSW5AUU/EU/L4PRsbjUKhu6r0uI/I2EmetYD9LSmYh9R//RcAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAwz3//z//+n6Q91TsFGKJeVpLOVu8UYIh6WUk6W71TgCHqZSXpbPVOAYaol5Wks9U7BRiiXlaSzlbvFGCIellJOlu9U4Ah6mUl6Wz1TgGGqJeVzvcZ9d9V56t3CjBEvaz0vV6h/jfpe9U7BRiiXlb6XIX636zPVe8UYIh6WeljvYP6DfSx6p0CDFEvK/26d1S/iX5dvVOAIeplpR83Qf1G+nH1TgGGqJeV/tpE9Zvpr9U7BRiiXlb6ow3qN9Qf1TsFGKJeVtpx/P9Uv6X8BwD4oHpZPb2N6jd9evVOAYaol9VTe4L6jZ9avVOAIepl9cSepH7rJ1bvFGCIelk9rSeq3/xp1TsFGKJeVk/qyeq3f1L1TgGGqJfVU8J/Am5V7xRgiHpZPSH+rZ7FE6p3CjBEvay2xz/VM9levVOAIepltT3+qZ7J9uqdAgxRL6vN8XP1bDZX7xRgiHpZbY3fq2e0tXqnAEPUy2pr/F49o63VOwUYol5WG+Pj6lltrN4pwBD1stoWn1fPbFv1TgGGqJfVtvi8embbqncKMES9rDbF19Wz21S9U4Ah6mW1Kb6unt2m6p0CDFEvq03xdfXsNlXvFGCIelltie+rZ7ileqcAQ9TLakt8Xz3DLdU7BRiiXlYb4px6lhuqdwowRL2sNsQ59Sw3VO8UYIh6WW2Ic+pZbqjeKcAQ9bKaHufVM51evVOAIeplNT3Oq2c6vXqnAEPUy2p6nFfPdHr1TgGGqJfV9Divnun06p0CDFEvq+lxXj3T6dU7BRiiXlaT43Xq2U6u3inAEPWymhyvU892cvVOAYaol9XkeJ16tpOrdwowRL2sJsfr1LOdXL1TgCHqZTU5Xqee7eTqnQIMUS+ryfE69WwnV+8UYIh6WU2O16lnO7l6pwBD1MtqcrxOPdvJ1TsFGKJeVpPjderZTq7eKcAQ9bKaHK9Tz3Zy9U4BhqiX1eR4nXq2k6t3CjBEvawmx+vUs51cvVOAIeplNTlep57t5OqdAgxRL6vpcV490+nVOwUYol5W0+O8eqbTq3cKMES9rKbHefVMp1fvFGCIellNj/PqmU6v3inAEPWymh7n1TOdXr1TgCHqZbUhzqlnuaF6pwBD1MtqQ5xTz3JD9U4BhqiX1YY4p57lhuqdAgxRL6st8X31DLdU7xRgiHpZbYnvq2e4pXqnAEPUy2pLfF89wy3VOwUYol5Wm+Lr6tltqt4pwBD1stoUX1fPblP1TgGGqJfVtvi8embbqncKMES9rLbF59Uz21a9U4Ah6mW1MT6untXG6p0CDFEvq43xcfWsNlbvFGCIelltjd+rZ7S1eqcAQ9TLanP8XD2bzdU7BRiiXlab4+fq2Wyu3inAEPWy2h7/VM9ke/VOAYaol9UT4t/qWTyheqcAQ9TL6inh+N+q3inAEPWyelJPVr/9k6p3CjBEvaye1hPVb/606p0CDFEvqyf2JPVbP7F6pwBD1MvqqT1B/cZPrd4pwBD1snpym9Vv++TqnQIMUS+rp7dR/aZPr94pwBD1snp6G9Vv+vTqnQIMUS+rp7dR/aZPr94pwBD1snp6G9Vv+vTqnQIMUS+rp7dR/aZPr94pwBD1snp6G9Vv+vTqnQIMUS+rp7dR/aZPr94p8CX1hyPdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbqP6TaXb1bdzhXqI0u02qt9Uul19O1eohyjdbLP6baWb1bdzhXqI0q2eoH5j6Vb17VyhHqJ0oyep31q6UX07V6iHKL26J6rfXHp19e1coR6i9MqerH576ZXVt3OFeojSq8J/ArS3+nauUA9RekX8Wz0L6RXVt3OFeojS6fineibS6erbuUI9ROl0/FM9E+l09e1coR6idDJ+rp6NdLL6dq5QD1E6Fb9Xz0g6VX07V6iHKJ2K36tnJJ2qvp0r1EOUTsTH1bOSTlTfzhXqIUrfjc+rZyZ9t/p2rlAPUfpufF49M+m71bdzhXqI0nfi6+rZSd+pvp0r1EOUvhNfV89O+k717VyhHqL01fi+eobSV6tv5wr1EKWvxvfVM5S+Wn07V6iHKH01vq+eofTV6tu5Qj1E6StxTj1L6SvVt3OFeojSV+KcepbSV6pv5wr1EKWvxDn1LKWvVN/OFeohSp+N8+qZSp+tvp0r1EOUPhvn1TOVPlt9O1eohyh9Ns6rZyp9tvp2rlAPUfpsnFfPVPps9e1coR6i9Nk4r56p9Nnq27lCPUTpM/E69Wylz1TfzhXqIUofjderZyx9tPp2rlAPUfpI3FPPWvpI9e1coR6i9Kvo1LOXflV9O1eohyj9KN5H/VuQflR9O1eohyj9Pd5P/ZuQ/l59O1eohyj9Ge+v/o1If1bfzhXqIUrMU/9mpPp2rlAPUc+N+erfkJ5bfTtXqIeoZ8Ye9W9Jz6y+nSvUQ9SzYq/6t6VnVd/OFeoh6hnxHPVvTc+ovp0r1EPU/nie+jen/dW3c4V6iNob1L9B7a2+nSvUQ9S+4O/q36T2Vd/OFeohalfwM/VvU7uqb+cK9RC1I/io+reqHdW3c4V6iJodfFX929Xs6tu5Qj1EzQxOqX/Lmll9O1eoh6h5wWn1b1rzqm/nCvUQNSd4tfo3rjnVt3OFeoh6/+C2+jev96++nSvUQ9R7B5X6t6/3rr6dK9RD1HsG76L+FvSe1bdzhXqIeq/gXdXfht6r+nauUA9R7xFMUX8reo/q27lCPUT1wTT1N6O++nauUA9RXTBd/Q2pq76dK9RD1P1gm/qb0v3q27lCPUTdDbaqvy3drb6dK9RD1J3gKepvTXeqb+cK9RD12uCp6m9Pr62+nSvUQ9Trgqerv0G9rvp2rlAPUecD/qr+JnW++nauUA9R5wJ+rf5Gda76dq5QD1HfD/ic+pvV96tv5wr1EPW9gK+pv119r/p2rlAPUV8LOKP+lvW16tu5Qj1EfS7gNepvW5+rvp0r1EPUxwNeq/7G9fHq27lCPUT9PuCu+pvX76tv5wr1EPXzgFa9A/Tz6tu5Qj1E/TPgvdQ7Qf+svp0r1EPUXwPeU70b9Nfq27lCPUT9ETBDvSv0R/XtXKEe4tMDZqp3x9Orb+cK9RCfHDBbvUOeXH07V6iH+MR4f/W86j+fz6l3yhOrb+cK9RCfFO+vnKHfz3z1jnlS9e1coR7iU+L93Zyt39Fe9a55SvXtXKEeYtHND5r3V/8e/a52mrKjplbfzhXqIb66V6n+XM6pf5vv/PvmnHfdUdOrb+cK9RC3LEULeZb6Nzr9987nvcN+2lR9O1eoh2gJclP9O/UNUKt/p6eqb+cK9RAtPW6of6e+Cd5N/Tv9bvXtXKEeoiXHq9W/Vd8H76z+rX61+nauUA/RYuNV6t/qOwQfVf9WP1t9O1eoh2iZcVr9O33H4CPq3+lnqm/nCvUQLS9OqX+nE4KPqH+nH6m+nSvUQ7SwOKH+nU4KPqL+nf6u+nauUA/RouI76t/o5OB36t/or6pv5wr1EC0nvqL+fW4KfqX+ff6s+nauUA/RQuKz6t/nxuBX6t/nj6pv5wr1EC0iPqr+bT4h+Jn6t/n36tu5Qj1Ey4ffqX+XTwx+pP5d/mf17VyhHqKFw8/Uv0n5Lvmn+jf5Z/XtXKEeoiXDj9S/Sfk++bn6N/mv6tu5Qj1E+E/171G+VT6m/j3Wt3MFC4V3UC8T+W75vPJ3WN/OFSwSavVBk2+Xryl/g/XtXMECoVIfMfmO+b7qt1ffzhUsDW6rj5Z805xV/Obq27mCZcFN9aGS75rzit9bfTtXsCS4oT5O8o3zWrd/Z/XtXMFy4JXqY6QunuX276u+nStYCLxCfXz0PvEcN39X9e1cwSLgtPrg6P3iGW7+purbuYIlwCn1kdH7x243f0v17VzBh8931UdF82KvW7+h+nau4IPnO+pDormx063fT307V/Ch8xX18dCe2OfG76a+nSv4wPmM+lhob+xx4/dS384VfNh8RH0c9JyY78bvpL6dK/ig+Z36IOh5MduN30h9O1fwIfMz9RGQmOvVv436dq7gA+bv6qUv/T3mefVvor6dK/hw+U/1opd+FrO8+vdQ384VfLT8S73cpY/GDK/+HdS3cwUf67PVy1z6ary3V8+/vp0r+Eifq17g0nfjfb169vXtXMEH+jz10pZOx/t59czr27mCD/M56iUtvTrex6tnXd/OFXyQ+9VLWbodvVfPuL6dK/gQd6sXsVRF69XzrW/nCj7CnerlK71LNF491/p2ruDj26VettK7xl2vnmd9O1fw0e1RL1jp3eOeV8+yvp0r+ODmq5eqNC1e79UzrG/nCj60ueolKk2P13n17OrbuYIPbKZ6cUpb4jVePbf6dq7g45qpXprSlniNV8+tvp0r+LhmqpemtCVe49Vzq2/nCj6umeqlKW2J13j13OrbuYIPbJ56YUrb4qwbM6tv5wo+rnnqZSlti7NuzKy+nSv4uOapl6W0Lc66MbP6dq7g45qnXpbStjjrxszq27mCD2yWelFKW+OMW/Oqb+cKPq5Z6iUpbY0zbs2rvp0r+MBmqZektDW+7+a86tu5gg9sjnpBStvje27Oqr6dK/i45qiXo7Q9vufmrOrbuYIPbIZ6MUpPia+5Paf6dq7g45qhXorSU+Jrbs+pvp0r+MDeX70QpafF5xQzqm/nCj6u91cvQ+lp8TnFjOrbuYIP7L3Vi1B6anxMNZ/6dq7gA3tf9QKUnh6/Vs6mvp0r+LjeV738pKfHr5WzqW/nCj6w91TPRdIf8WP1XOrbuUI9RB/YP9XzkPTX+Kt6Hv+qvp0r1EP0gf1VPQdJP44/1HP4s/p2rlAP0Qf2V/UMJP043ms/1bdzhXqIPrB/q99f0q97uvr9/7P6dq5QD9EH9of63SV9rKeq3/3v1bdzhXqIPrD3+7Ak/bqnqd/7R9W3c4V6iE/+yOr3lfS9tqvf91fVt3OFeohP/cDqd5V0pq3qd/1d9e1coR7iEz+w+j0lnW2b+j0/Un074UvqD0fS2eqdAgxRLytJZ6t3CjBEvawkna3eKcAQ9bKSdLZ6pwBD1MtK0tnqnQIMUS8rSWerdwowRL2sJJ2t3inAEPWyknS2eqcAQ9TLStLZ6p0CDFEvK0lnq3cKMES9rCSdrd4pwBD1spJ0tnqnAEPUy0rS2eqdAgxRLytJZ6t3CjBEvawkna3eKcAQ9bKSdLZ6pwBD1MtK0tnqnQIMUS8rSWerdwowRL2sJJ2t3inAEPWyknS2eqcAQ9TLStLZ6p0CDFEvK0lnq3cKMES9rCSdrd4pwBD1spJ0tnqnAEPUy0rS2eqdAgxRLytJZ6t3CjBEvawkna3eKcAQ9bKSdLZ6pwBD1MtK0tnqnQIMUS8rSWerdwowRL2sJJ2t3inAEPWyknS2eqcAQ9TLStLZ6p0CDFEvK0lnq3cKMES9rCSdrd4pwBD1spJ0tnqnAEPUy0rS2eqdAgxRLytJZ6t3CjBEvawkna3eKcAQ9bKSdLZ6pwBD1MtK0tnqnQIMUS8rSWerdwowRL2sJJ2t3inAEPWyknS2eqcAQ9TLStLZ6p0CDFEvK0lnq3cKMES9rCSdrd4pwBD1spJ0tnqnAEPUy0rS2eqdAgxRLytJZ6t3CjBEvawkna3eKcAQ9bKSdLZ6pwBD1MtK0tnqnQIMUS8rSWerdwowRL2sJJ2t3inAEPWyknS2eqcAQ9TLStLZ6p0CDFEvK0lnq3cKMES9rCSdrd4pwBD1spJ0tnqnAEPUy0rS2eqdAgxRLytJZ6t3CjBEvawkna3eKcAQ9bKSdLZ6pwBD1MtK0tnqnQIMUS8rSWerdwowRL2sJJ2t3inAEPWyknS2eqcAQ9TLStLZ6p0CDFEvK0lnq3cKMES9rCSdrd4pwBD1spJ0tnqnAEPUy0rS2eqdAgxRLytJZ6t3CjBEvawkna3eKcAQ9bKSdLZ6pwBD1MtK0tnqnQIMUS8rSWerdwowRL2sJJ2t3inAEPWyknS2eqcAQ9TLStLZ6p0CDFEvK0lnq3cKMES9rCSdrd4pwBD1spJ0tnqnAEPUy0rS2eqdAgxRLytJZ6t3CjBEvawkna3eKcAQ9bKSdLZ6pwBD1MtK0tnqnQIMUS8rSWerdwowRL2sJJ2t3inAEPWyknS2eqcAQ9TLStLZ6p0CAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAHzP/wNQKhtAofrgWwAAAABJRU5ErkJggg=="
    png_data = base64.b64decode(png_b64)
    return Response(png_data, mimetype='image/png',
        headers={'Cache-Control':'public,max-age=86400'})

# ── Main Application Routes ────────────────────────────────────────────────────
@app.route("/")
def index():
    """Serve the landing page for non-authenticated users."""
    if "user_id" in session:
        return serve_app()
    action = request.args.get("action", "")
    if action in ("login", "register"):
        return HTML
    return LANDING_HTML

@app.route("/app")
@app.route("/dashboard")
@app.route("/projects")
@app.route("/tasks")
@app.route("/messages")
@app.route("/settings")
@app.route("/profile")
@app.route("/analytics")
@app.route("/tickets")
@app.route("/timeline")
def serve_app():
    """Serve the main application template for all app routes."""
    return HTML

@app.route("/password-generator")
def password_generator_page():
    """Serve the standalone password generator tool."""
    return PASSWORD_GENERATOR_HTML

@app.route("/privacy")
def privacy_page():
    """Serve the Privacy Policy page."""
    return _load_template('privacy.html')

@app.route("/terms")
def terms_page():
    """Serve the Terms of Service page."""
    return _load_template('terms.html')

@app.route("/security")
def security_info_page():
    """Serve the Security page."""
    return _load_template('security.html')

# ── Admin Token Store & Guard (must be defined before any route that calls it) ──
_ADMIN_TOKENS = {}       # token -> expiry (datetime)
_ADMIN_FAIL_LOG = {}     # ip -> [fail_timestamp, ...] — brute-force lockout

def _admin_check_lockout(ip):
    """Return True if this IP is locked out (5+ failures in last 15 min)."""
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=15)
    hits = [t for t in _ADMIN_FAIL_LOG.get(ip, []) if t > cutoff]
    _ADMIN_FAIL_LOG[ip] = hits
    return len(hits) >= 5

def _admin_record_failure(ip):
    """Record a failed login attempt for this IP."""
    _ADMIN_FAIL_LOG.setdefault(ip, []).append(datetime.utcnow())

def _admin_clear_failures(ip):
    """Clear failure log on successful login."""
    _ADMIN_FAIL_LOG.pop(ip, None)

def _require_admin():
    """Return True if request carries a valid admin token."""
    token = request.headers.get("X-Admin-Token", "")
    exp   = _ADMIN_TOKENS.get(token)
    return bool(exp and datetime.utcnow() < exp)

def _audit(action, target="", detail=""):
    """Write an entry to audit_log. Fire-and-forget — never raises."""
    try:
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@vewit.in")
        entry_id    = secrets.token_hex(8)
        with get_db() as db:
            db.execute(
                "INSERT INTO audit_log (id, admin_email, action, target, detail, created) "
                "VALUES (?,?,?,?,?,?)",
                (entry_id, admin_email, action, target, detail,
                 ts())
            )
            db.commit()
    except Exception as _ae:
        log.error("[audit] write error: %s", _ae)

@app.route("/api/admin/security-stats")
def admin_api_security_stats():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        with get_db() as db:
            try:
                # Cast to int to handle both boolean TRUE and integer 1 stored in pg
                enabled  = db.execute(
                    "SELECT COUNT(*) FROM users WHERE totp_verified = 1"
                ).fetchone()[0]
                disabled = db.execute(
                    "SELECT COUNT(*) FROM users WHERE totp_verified IS NULL OR totp_verified = 0"
                ).fetchone()[0]
                no_totp  = db.execute("""
                    SELECT u.id, u.name, u.email, u.role, w.name AS workspace_name
                    FROM users u
                    LEFT JOIN workspaces w ON w.id = u.workspace_id
                    WHERE u.totp_verified IS NULL OR u.totp_verified = 0
                    ORDER BY u.created DESC LIMIT 100
                """).fetchall()
            except Exception:
                enabled, disabled, no_totp = 0, 0, []
        return jsonify({
            "totp_enabled":  enabled,
            "totp_disabled": disabled,
            "no_totp": [dict(r) for r in no_totp],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/plans-stats")
def admin_api_plans_stats():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        with get_db() as db:
            rows = db.execute("""
                SELECT w.id, w.name, w.plan, COUNT(u.id) AS member_count
                FROM workspaces w
                LEFT JOIN users u ON u.workspace_id = w.id
                GROUP BY w.id, w.name, w.plan
                ORDER BY w.created DESC
            """).fetchall()
            starter    = sum(1 for r in rows if (r["plan"] or "starter") == "starter")
            team       = sum(1 for r in rows if r["plan"] == "team")
            enterprise = sum(1 for r in rows if r["plan"] == "enterprise")
        return jsonify({
            "starter_count":    starter,
            "team_count":       team,
            "enterprise_count": enterprise,
            "workspaces":       [dict(r) for r in rows],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/users/<uid>/reset-password", methods=["POST"])
def admin_api_user_reset_password(uid):
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    pw = data.get("password", "")
    if len(pw) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    try:
        with get_db() as db:
            db.execute("UPDATE users SET password=? WHERE id=?", (hash_pw(pw), uid))
            db.commit()
        _audit("reset_user_password", uid, "Password reset by admin")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/users/<uid>/reset-totp", methods=["POST"])
def admin_api_user_reset_totp(uid):
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        with get_db() as db:
            db.execute(
                "UPDATE users SET totp_secret='', totp_verified=0, two_fa_enabled=0 WHERE id=?",
                (uid,)
            )
            db.commit()
        _audit("reset_user_totp", uid, "2FA cleared by admin")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/users/<uid>/change-role", methods=["POST"])
def admin_api_user_change_role(uid):
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    role = data.get("role", "")
    valid_roles = ("Admin","Manager","TeamLead","Developer","Tester","Viewer")
    if role not in valid_roles:
        return jsonify({"error": "Invalid role"}), 400
    try:
        with get_db() as db:
            db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
            db.commit()
        _audit("change_user_role", uid, f"Role changed to {role}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Admin Panel ────────────────────────────────────────────────────────────────

@app.route("/adminpanel")
@app.route("/adminpanel/<path:workspace>")
def admin_panel_page(workspace=None):
    """Serve the admin panel HTML."""
    return ADMIN_HTML

@app.route("/api/admin/login", methods=["POST"])
def admin_api_login():
    """Super-admin login — returns a short-lived bearer token."""
    data = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "")

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@vewit.in").strip().lower()
    admin_pass  = os.environ.get("ADMIN_PASSWORD", "")

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")[:60]

    if _admin_check_lockout(client_ip):
        return jsonify({"error": "Too many failed attempts. Try again in 15 minutes."}), 429

    if not admin_pass:
        return jsonify({"error": "Admin password not configured. Set ADMIN_PASSWORD env var."}), 503

    if email != admin_email or password != admin_pass:
        _admin_record_failure(client_ip)
        remaining = 5 - len(_ADMIN_FAIL_LOG.get(client_ip, []))
        return jsonify({"error": f"Invalid credentials. {max(remaining,0)} attempt(s) remaining before lockout."}), 401

    _admin_clear_failures(client_ip)
    token = secrets.token_hex(32)
    _ADMIN_TOKENS[token] = datetime.utcnow() + timedelta(hours=8)
    _audit("admin_login", "system", f"Admin logged in: {email}")
    return jsonify({"token": token})

@app.route("/api/admin/session")
def admin_api_session():
    """Validate an existing admin token — called on page load to restore session."""
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@vewit.in")
    return jsonify({"ok": True, "email": admin_email})

@app.route("/api/admin/logout", methods=["POST"])
def admin_api_logout():
    """Invalidate the admin token."""
    token = request.headers.get("X-Admin-Token", "")
    if token and token in _ADMIN_TOKENS:
        _audit("admin_logout", "system", "Admin signed out")
        _ADMIN_TOKENS.pop(token, None)
    return jsonify({"ok": True})

@app.route("/api/admin/dashboard")
def admin_api_dashboard():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        with get_db() as db:
            total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total_ws    = db.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
            # active = logged in within last 7 days (if last_active column exists)
            try:
                cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
                active = db.execute(
                    "SELECT COUNT(*) FROM users WHERE last_active > ?", (cutoff,)
                ).fetchone()[0]
            except Exception:
                active = total_users
            # revenue placeholder — extend when billing is wired
            revenue = 0
        return jsonify({
            "total_users": total_users,
            "total_workspaces": total_ws,
            "active_users": active,
            "revenue": revenue,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/workspaces")
def admin_api_workspaces():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        with get_db() as db:
            rows = db.execute("""
                SELECT w.id, w.name, w.invite_code, w.plan, w.created,
                       COUNT(u.id) AS member_count
                FROM workspaces w
                LEFT JOIN users u ON u.workspace_id = w.id
                GROUP BY w.id, w.name, w.invite_code, w.plan, w.created
                ORDER BY w.created DESC
            """).fetchall()
        return jsonify({"workspaces": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/workspaces/<ws_id>")
def admin_api_workspace_detail(ws_id):
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        with get_db() as db:
            ws = db.execute("SELECT * FROM workspaces WHERE id=?", (ws_id,)).fetchone()
            if not ws:
                return jsonify({"error": "Workspace not found"}), 404
            members = db.execute(
                "SELECT id, name, email, role, created FROM users WHERE workspace_id=? ORDER BY created",
                (ws_id,)
            ).fetchall()
        return jsonify({"workspace": dict(ws), "members": [dict(m) for m in members]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/users")
def admin_api_users():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        with get_db() as db:
            rows = db.execute("""
                SELECT u.id, u.name, u.email, u.role, u.created,
                       w.name AS workspace_name
                FROM users u
                LEFT JOIN workspaces w ON w.id = u.workspace_id
                ORDER BY u.created DESC
            """).fetchall()
        return jsonify({"users": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/users/<uid>/delete", methods=["POST"])
def admin_api_delete_user(uid):
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        with get_db() as db:
            db.execute("DELETE FROM users WHERE id=?", (uid,))
            db.commit()
        _audit("delete_user", uid, "User deleted by admin")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/audit")
def admin_api_audit():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        with get_db() as db:
            rows = db.execute(
                "SELECT * FROM audit_log ORDER BY created DESC LIMIT 200"
            ).fetchall()
        return jsonify({"logs": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"logs": [], "warning": str(e)}), 200

@app.route("/api/admin/workspace/set-plan", methods=["POST"])
def admin_api_set_plan():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    ws_id = data.get("workspace_id")
    plan  = data.get("plan", "starter")
    if plan not in ("starter", "team", "enterprise"):
        return jsonify({"error": "Invalid plan"}), 400
    try:
        with get_db() as db:
            db.execute("UPDATE workspaces SET plan=? WHERE id=?", (plan, ws_id))
            db.commit()
        _audit("set_plan", ws_id, f"Plan changed to {plan}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/workspace/suspend", methods=["POST"])
def admin_api_suspend_workspace():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    ws_id = data.get("workspace_id")
    try:
        with get_db() as db:
            db.execute("UPDATE workspaces SET suspended=1 WHERE id=?", (ws_id,))
            db.commit()
        _audit("suspend_workspace", ws_id, "Workspace suspended")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/workspace/reset-invite", methods=["POST"])
def admin_api_reset_invite():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    ws_id = data.get("workspace_id")
    new_code = secrets.token_urlsafe(8).upper()[:8]
    try:
        with get_db() as db:
            db.execute("UPDATE workspaces SET invite_code=? WHERE id=?", (new_code, ws_id))
            db.commit()
        _audit("reset_invite_code", ws_id, f"New code: {new_code}")
        return jsonify({"ok": True, "invite_code": new_code})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/workspace/reset-all-passwords", methods=["POST"])
def admin_api_reset_all_passwords():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    ws_id = data.get("workspace_id")
    pw    = data.get("password", "")
    if len(pw) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    try:
        with get_db() as db:
            db.execute(
                "UPDATE users SET password=? WHERE workspace_id=?",
                (hash_pw(pw), ws_id)
            )
            cur = db.execute("SELECT COUNT(*) FROM users WHERE workspace_id=?", (ws_id,))
            count = cur.fetchone()[0]
            db.commit()
        _audit("reset_all_passwords", ws_id, f"Bulk password reset for {count} users")
        return jsonify({"ok": True, "count": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/workspace/reset-all-totp", methods=["POST"])
def admin_api_reset_all_totp():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    ws_id = data.get("workspace_id")
    try:
        with get_db() as db:
            db.execute(
                "UPDATE users SET totp_secret='', totp_verified=0, two_fa_enabled=0 WHERE workspace_id=?",
                (ws_id,)
            )
            cur = db.execute("SELECT COUNT(*) FROM users WHERE workspace_id=?", (ws_id,))
            count = cur.fetchone()[0]
            db.commit()
        _audit("reset_all_totp", ws_id, f"Bulk 2FA reset for {count} users")
        return jsonify({"ok": True, "count": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/workspace/toggle-2fa", methods=["POST"])
def admin_api_toggle_2fa():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    ws_id   = data.get("workspace_id")
    enabled = bool(data.get("enabled", False))
    try:
        with get_db() as db:
            db.execute(
                "UPDATE workspaces SET otp_enabled=? WHERE id=?",
                (1 if enabled else 0, ws_id)
            )
            db.commit()
        _audit("toggle_2fa", ws_id, f"2FA requirement {'enabled' if enabled else 'disabled'}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/workspace/add-user", methods=["POST"])
def admin_api_add_user():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data  = request.get_json(silent=True) or {}
    ws_id = data.get("workspace_id")
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    pw    = data.get("password", "")
    role  = data.get("role", "Developer")
    if not name or not email or len(pw) < 8:
        return jsonify({"error": "Name, email and password (min 8 chars) are required"}), 400
    uid = secrets.token_hex(8)
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO users (id, name, email, password, role, workspace_id, created) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid, name, email, hash_pw(pw), role, ws_id, ts())
            )
            db.commit()
        _audit("add_user", ws_id, f"User {name} ({email}) created with role {role}")
        return jsonify({"ok": True, "id": uid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/<path:path>")
def catch_all(path):
    """Catch-all route for SPA client-side routing."""
    # If it looks like a file request, return 404
    if "." in path.split("/")[-1]:
        return "", 404
    # Otherwise serve the app (for client-side routing)
    return HTML

import os as _os

# ── Load HTML templates from separate files ────────────────────────────────────
_BASE = _os.path.dirname(_os.path.abspath(__file__))

def _load_template(filename, fallback=''):
    """Load an HTML template file, return fallback string if not found."""
    path = _os.path.join(_BASE, filename)
    try:
        with open(path, 'r', encoding='utf-8') as _f:
            return _f.read()
    except FileNotFoundError:
        print(f"  ⚠ Template not found: {filename}")
        return fallback

HTML                    = _load_template('template.html')
LANDING_HTML            = _load_template('landing.html')
PASSWORD_GENERATOR_HTML = _load_template('password-generator.html')
ADMIN_HTML              = _load_template('adminpanel.html')


# ── Utilities ─────────────────────────────────────────────────────────────────
# Module-level init — runs when gunicorn imports app, ensures DB is ready
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(JS_DIR, exist_ok=True)
    init_db()
    ensure_timelog_schema()   # always run — adds any missing time_log columns
except Exception as _ie:
    import traceback
    print(f"  ⚠ Init error: {_ie}")
    traceback.print_exc()
def find_free_port(preferred=5000):
    for port in range(preferred, preferred+10):
        try:
            s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            s.bind(("",port)); s.close(); return port
        except: pass
    return preferred

def download_js():
    os.makedirs(JS_DIR,exist_ok=True)
    libs=[
        ("react.min.js", "https://unpkg.com/react@18/umd/react.production.min.js"), ("react-dom.min.js", "https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"), ("prop-types.min.js","https://unpkg.com/prop-types@15/prop-types.min.js"), ("recharts.min.js", "https://unpkg.com/recharts@2/umd/Recharts.js"), ("htm.min.js", "https://unpkg.com/htm@3/dist/htm.js"), ]
    all_ok=True
    for fn,url in libs:
        path=os.path.join(JS_DIR,fn)
        if os.path.exists(path) and os.path.getsize(path)>1000: continue
        print(f"  Downloading {fn}...",end="",flush=True)
        try:
            with urllib.request.urlopen(url,timeout=15) as r:
                with open(path,"wb") as f: f.write(r.read())
            print(" ✓")
        except Exception as e:
            print(f" ✗ ({e})"); all_ok=False
    return all_ok

def open_browser(port):
    time.sleep(1.4)
    webbrowser.open(f"http://localhost:{port}")

if __name__=="__main__":
    print("\n⚡ VEWIT v4.0 — Multi-Tenant | AI | Workspaces")
    print("="*54)
    print("  Initializing database...")
    init_db()
    print("  Ensuring timelog schema...")
    ensure_timelog_schema()
    print("  Checking JS libraries...")
    if not download_js():
        print("  ⚠ Some libraries failed. Check your internet connection.")
    port=find_free_port(5000)
    print(f"\n  ✓ Running at  http://localhost:{port}")
    print(f"  ✓ Database:   {DATABASE_URL[:40]}...")
    print(f"  ✓ Uploads:    {UPLOAD_DIR}")
    print(f"\n  Demo: alice@dev.io / pass123 (Admin)")
    print(f"  New company? Click 'Create Account' → 'New Workspace'")
    print(f"  Invite others? Share your code from Settings ⚙\n")
    threading.Thread(target=open_browser,args=(port,),daemon=True).start()
    app.run(host="0.0.0.0",port=port,debug=False,use_reloader=False)
