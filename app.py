#!/usr/bin/env python3
"""
VEWIT v4.0
Multi-tenant workspaces | AI Assistant | Stage Dropdown | Direct Messages
"""
import os, sys, json, hashlib, secrets, random, urllib.request, urllib.error
import socket, threading, time, webbrowser, mimetypes, base64, smtplib
from datetime import datetime, timedelta
from functools import wraps
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
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False,PERMANENT_SESSION_LIFETIME=86400*7,
    MAX_CONTENT_LENGTH=150*1024*1024)
CORS(app, supports_credentials=True)

@app.after_request
def add_headers(response):
    """Add performance and security headers to every response."""
    # Auth endpoints must NEVER be cached — a stale /api/auth/me response
    # allows a logged-out browser to appear authenticated. No exceptions.
    if request.path.startswith('/api/auth/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    elif request.path.startswith('/api/'):
        if request.method == 'GET':
            response.headers['Cache-Control'] = 'private, max-age=5'
        else:
            response.headers['Cache-Control'] = 'no-store'
    # Security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

CLRS=["#7c3aed","#2563eb","#059669","#d97706","#dc2626","#ec4899","#0891b2","#aaff00"]

def get_db(autocommit=False):
    """Get DB connection with retry logic for transient connection errors."""
    import time as _t
    last_err = None
    for attempt in range(3):
        try:
            conn = pg8000.native.Connection(**_parse_db_url(DATABASE_URL))
            conn.autocommit = autocommit
            return _DB(conn)
        except Exception as e:
            last_err = e
            if attempt < 2:
                _t.sleep(0.2 * (attempt + 1))  # 200ms, 400ms backoff
    raise RuntimeError(f"DB connection failed after 3 attempts: {last_err}")
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

def _background_scheduler():
    """Run periodic background jobs: recurring tasks, digest emails."""
    import time as _t
    _last_digest = 0
    while True:
        _t.sleep(3600)  # check every hour
        try: _spawn_recurring_tasks()
        except: pass
        now = _t.time()
        if now - _last_digest >= 86400:  # daily digest
            try: _run_digest()
            except: pass
            _last_digest = now

_threading.Thread(target=_background_scheduler, daemon=True).start()

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
          <h1 style="color:#aaff00;margin:0;font-size:22px;letter-spacing:-0.5px;">VEWIT</h1>
        </div>
        <div style="padding:32px;">
          <h2 style="color:#111;margin:0 0 8px;">Hi {user_name},</h2>
          <p style="color:#555;margin:0 0 28px;">Use the code below to complete your sign-in. It expires in <b>10 minutes</b>.</p>
          <div style="text-align:center;margin:0 0 28px;">
            <div style="display:inline-block;background:#f0fff0;border:2px solid #aaff00;border-radius:12px;padding:18px 36px;">
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
        send_email(to_email, subject, body)
        return True
    except Exception as e:
        print(f"[OTP] Email send error: {e}")
        return False
def ts(): return datetime.utcnow().isoformat() + 'Z'

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
            print(f"[Resend] ✓ Sent to {to_email}: {result[:80]}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[Resend] ✗ HTTP {e.code}: {body}")
        return False
    except Exception as e:
        print(f"[Resend] ✗ Error: {type(e).__name__}: {e}")
        return False

def send_email(to_email, subject, body_html, workspace_id=None):
    """Send email — tries Resend API first (works on Railway), falls back to SMTP."""
    from_addr = FROM_EMAIL or SMTP_USERNAME or 'noreply@vewit.in'

    # ── Try Resend API first (no port restrictions) ───────────────────────
    if RESEND_API_KEY:
        print(f"[Email] Using Resend API to send to {to_email}")
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
            print(f"[Email] Error loading config: {e}")

    if not smtp_config or not smtp_config.get('username') or not smtp_config.get('password'):
        if not SMTP_USERNAME or not SMTP_PASSWORD:
            print(f"[Email] Skipped (not configured): {subject} -> {to_email}")
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

        print(f"[SMTP] >>> SUCCESS: sent to {to_email}")
        return True
    except Exception as e:
        print(f"[SMTP] >>> FINAL FAILURE to {to_email}: {type(e).__name__}: {e}")
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
        print(f"[VAPID] Key generation error: {e}")
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
        print(f"[WebPush] Error: {e}")
        return False

def push_notification_to_user(db_ignored, user_id, title, body, nav_url="/", tag=None):
    """Send Web Push to all subscriptions for a given user (opens its own DB conn for thread safety)."""
    try:
        db = get_db()
    except Exception as e:
        print(f"push_notification DB error: {e}")
        return
    with db:
        subs = db.execute(
            "SELECT * FROM push_subscriptions WHERE user_id=?", (user_id,)
        ).fetchall()
    payload = {"title": title, "body": body, "url": nav_url, "tag": tag or title}
    dead_ids = []
    for sub in subs:
        sub_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}
        }
        ok = send_web_push(sub_info, payload)
        if not ok and sub["endpoint"]:
            dead_ids.append(sub["id"])
    if dead_ids:
        db.execute(f"DELETE FROM push_subscriptions WHERE id IN ({','.join('?'*len(dead_ids))})", dead_ids)

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
                password TEXT, role TEXT, avatar TEXT, color TEXT, created TEXT);
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
        """)
        try: db.execute('''CREATE TABLE IF NOT EXISTS teams (
            id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
            lead_id TEXT, member_ids TEXT DEFAULT '[]', created TEXT)''')
        except: pass
        try: db.executescript('''
            CREATE TABLE IF NOT EXISTS teams (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
                lead_id TEXT, member_ids TEXT DEFAULT '[]', created TEXT);
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY, workspace_id TEXT, title TEXT, description TEXT,
                type TEXT DEFAULT 'bug', priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'open', assignee TEXT, reporter TEXT,
                project TEXT, tags TEXT DEFAULT '[]', created TEXT, updated TEXT);
            CREATE TABLE IF NOT EXISTS ticket_comments (
                id TEXT PRIMARY KEY, workspace_id TEXT, ticket_id TEXT,
                user_id TEXT, content TEXT, created TEXT);
        ''')
        except: pass
        try: db.execute("ALTER TABLE projects ADD COLUMN team_id TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE tickets ADD COLUMN team_id TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE tasks ADD COLUMN team_id TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE messages ADD COLUMN is_system INTEGER DEFAULT 0")
        except: pass
        try: db.execute("ALTER TABLE users ADD COLUMN avatar_data TEXT")
        except: pass
        try: db.execute("ALTER TABLE users ADD COLUMN plain_password TEXT DEFAULT ''")
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
        try: db.execute("ALTER TABLE workspaces ADD COLUMN otp_enabled INTEGER DEFAULT 0")
        except: pass
        try: db.execute("ALTER TABLE workspaces ADD COLUMN dm_enabled INTEGER DEFAULT 1")
        except: pass
        try: db.execute("ALTER TABLE call_rooms ADD COLUMN invited_users TEXT DEFAULT '[]'")
        except: pass
        try: db.execute("ALTER TABLE notifications ADD COLUMN sender_id TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE users ADD COLUMN last_active TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE tasks ADD COLUMN parent_id TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE tasks ADD COLUMN story_points INTEGER DEFAULT 0")
        except: pass
        try: db.execute("ALTER TABLE tasks ADD COLUMN sprint TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'task'")
        except: pass
        try: db.execute("ALTER TABLE tasks ADD COLUMN labels TEXT DEFAULT '[]'")
        except: pass
        # ── New feature migrations ─────────────────────────────────────────────
        try: db.execute("ALTER TABLE tasks ADD COLUMN recurring TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE tasks ADD COLUMN recur_parent TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE tasks ADD COLUMN depends_on TEXT DEFAULT '[]'")
        except: pass
        try: db.execute("ALTER TABLE tasks ADD COLUMN time_logged INTEGER DEFAULT 0")
        except: pass
        try: db.execute("ALTER TABLE projects ADD COLUMN budget REAL DEFAULT 0")
        except: pass
        try: db.execute("ALTER TABLE projects ADD COLUMN budget_spent REAL DEFAULT 0")
        except: pass
        try: db.execute("ALTER TABLE workspaces ADD COLUMN white_label_name TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE workspaces ADD COLUMN white_label_logo TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE workspaces ADD COLUMN referral_code TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE workspaces ADD COLUMN digest_enabled INTEGER DEFAULT 0")
        except: pass
        try: db.execute("ALTER TABLE workspaces ADD COLUMN digest_frequency TEXT DEFAULT 'daily'")
        except: pass
        try: db.execute("ALTER TABLE users ADD COLUMN is_guest INTEGER DEFAULT 0")
        except: pass
        try: db.execute("ALTER TABLE users ADD COLUMN guest_projects TEXT DEFAULT '[]'")
        except: pass
        try:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS time_logs (
                id TEXT PRIMARY KEY, workspace_id TEXT, task_id TEXT,
                user_id TEXT, description TEXT, minutes INTEGER DEFAULT 0,
                logged_date TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS docs (
                id TEXT PRIMARY KEY, workspace_id TEXT, project_id TEXT,
                title TEXT, content TEXT, author TEXT,
                created TEXT, updated TEXT, is_public INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY, workspace_id TEXT, title TEXT,
                description TEXT, owner TEXT, status TEXT DEFAULT 'active',
                progress INTEGER DEFAULT 0, due TEXT, created TEXT,
                team_id TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS goal_krs (
                id TEXT PRIMARY KEY, goal_id TEXT, workspace_id TEXT,
                title TEXT, target REAL DEFAULT 100, current REAL DEFAULT 0,
                unit TEXT DEFAULT '%', created TEXT);
            CREATE TABLE IF NOT EXISTS custom_fields (
                id TEXT PRIMARY KEY, workspace_id TEXT, entity_type TEXT DEFAULT 'task',
                name TEXT, field_type TEXT DEFAULT 'text',
                options TEXT DEFAULT '[]', created TEXT);
            CREATE TABLE IF NOT EXISTS custom_field_values (
                id TEXT PRIMARY KEY, workspace_id TEXT, field_id TEXT,
                entity_id TEXT, value TEXT, updated TEXT);
            CREATE TABLE IF NOT EXISTS task_templates (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
                description TEXT, priority TEXT DEFAULT 'medium',
                stage TEXT DEFAULT 'backlog', labels TEXT DEFAULT '[]',
                subtasks TEXT DEFAULT '[]', created TEXT);
            CREATE TABLE IF NOT EXISTS webhooks_config (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
                url TEXT, events TEXT DEFAULT '[]',
                secret TEXT, active INTEGER DEFAULT 1, created TEXT);
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY, workspace_id TEXT, user_id TEXT,
                name TEXT, key_hash TEXT, key_prefix TEXT,
                scopes TEXT DEFAULT '[]', last_used TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY, workspace_id TEXT, user_id TEXT,
                action TEXT, entity_type TEXT, entity_id TEXT,
                details TEXT, ip TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS sprints (
                id TEXT PRIMARY KEY, workspace_id TEXT, project_id TEXT,
                name TEXT, goal TEXT, status TEXT DEFAULT 'planning',
                start_date TEXT, end_date TEXT, velocity INTEGER DEFAULT 0, created TEXT);
            CREATE TABLE IF NOT EXISTS referrals (
                id TEXT PRIMARY KEY, referrer_ws TEXT, referred_ws TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS announcements (
                id TEXT PRIMARY KEY, workspace_id TEXT, title TEXT, content TEXT,
                author TEXT, pinned INTEGER DEFAULT 0, created TEXT, expires TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS announcement_reads (
                id TEXT PRIMARY KEY, announcement_id TEXT, user_id TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS message_reactions (
                id TEXT PRIMARY KEY, workspace_id TEXT, message_id TEXT, message_type TEXT DEFAULT 'channel',
                user_id TEXT, emoji TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS message_threads (
                id TEXT PRIMARY KEY, workspace_id TEXT, parent_id TEXT, sender TEXT,
                content TEXT, ts TEXT);
            CREATE TABLE IF NOT EXISTS intake_forms (
                id TEXT PRIMARY KEY, workspace_id TEXT, title TEXT, description TEXT,
                project_id TEXT, fields TEXT DEFAULT '[]', active INTEGER DEFAULT 1,
                created_by TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS intake_submissions (
                id TEXT PRIMARY KEY, form_id TEXT, workspace_id TEXT,
                data TEXT DEFAULT '{}', ticket_id TEXT, submitter_email TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS totp_secrets (
                id TEXT PRIMARY KEY, user_id TEXT, secret TEXT,
                enabled INTEGER DEFAULT 0, backup_codes TEXT DEFAULT '[]', created TEXT);
            CREATE TABLE IF NOT EXISTS standup_reports (
                id TEXT PRIMARY KEY, workspace_id TEXT, user_id TEXT,
                report_date TEXT, content TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS code_reviews (
                id TEXT PRIMARY KEY, workspace_id TEXT, task_id TEXT, ticket_id TEXT,
                diff_text TEXT, review_result TEXT, author TEXT, created TEXT);
            """)
        except: pass
        try: db.execute("""CREATE TABLE IF NOT EXISTS subtasks (
            id TEXT PRIMARY KEY, workspace_id TEXT, task_id TEXT,
            title TEXT, done INTEGER DEFAULT 0, assignee TEXT DEFAULT '', created TEXT)""")
        except: pass
        try:
            db.execute("ALTER TABLE workspaces ADD COLUMN smtp_server TEXT")
            db.execute("ALTER TABLE workspaces ADD COLUMN smtp_port INTEGER DEFAULT 587")
            db.execute("ALTER TABLE workspaces ADD COLUMN smtp_username TEXT")
            db.execute("ALTER TABLE workspaces ADD COLUMN smtp_password TEXT")
            db.execute("ALTER TABLE workspaces ADD COLUMN from_email TEXT")
            db.execute("ALTER TABLE workspaces ADD COLUMN email_enabled INTEGER DEFAULT 1")
        except: pass
        # ── Performance indexes — critical for unlimited scale ─────────────
        try:
            db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_workspace_created ON tasks(workspace_id, created DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_workspace_stage ON tasks(workspace_id, stage);
            CREATE INDEX IF NOT EXISTS idx_tasks_workspace_assignee ON tasks(workspace_id, assignee);
            CREATE INDEX IF NOT EXISTS idx_tasks_workspace_project ON tasks(workspace_id, project);
            CREATE INDEX IF NOT EXISTS idx_tasks_team ON tasks(workspace_id, team_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_sprint ON tasks(workspace_id, sprint);
            CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(workspace_id, due);
            CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_projects_team ON projects(workspace_id, team_id);
            CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(workspace_id, owner);
            CREATE INDEX IF NOT EXISTS idx_tickets_workspace ON tickets(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(workspace_id, status);
            CREATE INDEX IF NOT EXISTS idx_tickets_assignee ON tickets(workspace_id, assignee);
            CREATE INDEX IF NOT EXISTS idx_tickets_team ON tickets(workspace_id, team_id);
            CREATE INDEX IF NOT EXISTS idx_messages_project ON messages(workspace_id, project, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_dm_recipient ON direct_messages(workspace_id, recipient, read);
            CREATE INDEX IF NOT EXISTS idx_dm_sender ON direct_messages(workspace_id, sender);
            CREATE INDEX IF NOT EXISTS idx_notifs_user ON notifications(workspace_id, user_id, read);
            CREATE INDEX IF NOT EXISTS idx_notifs_created ON notifications(workspace_id, user_id, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_subtasks_task ON subtasks(workspace_id, task_id);
            CREATE INDEX IF NOT EXISTS idx_files_task ON files(workspace_id, task_id);
            CREATE INDEX IF NOT EXISTS idx_files_project ON files(workspace_id, project_id);
            CREATE INDEX IF NOT EXISTS idx_time_logs_task ON time_logs(workspace_id, task_id);
            CREATE INDEX IF NOT EXISTS idx_time_logs_user ON time_logs(workspace_id, user_id, logged_date);
            CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(workspace_id, user_id, fired);
            CREATE INDEX IF NOT EXISTS idx_teams_workspace ON teams(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_docs_workspace ON docs(workspace_id, project_id);
            CREATE INDEX IF NOT EXISTS idx_audit_workspace ON audit_logs(workspace_id, created DESC);
            CREATE INDEX IF NOT EXISTS idx_reactions_msg ON message_reactions(workspace_id, message_id);
            CREATE INDEX IF NOT EXISTS idx_threads_parent ON message_threads(workspace_id, parent_id);
            CREATE INDEX IF NOT EXISTS idx_sprints_project ON sprints(workspace_id, project_id);
            CREATE INDEX IF NOT EXISTS idx_goals_workspace ON goals(workspace_id, status);
            CREATE INDEX IF NOT EXISTS idx_users_workspace ON users(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_tasks_due_stage ON tasks(workspace_id, due, stage);
            CREATE INDEX IF NOT EXISTS idx_tasks_assignee_stage ON tasks(workspace_id, assignee, stage);
            CREATE INDEX IF NOT EXISTS idx_projects_members ON projects(workspace_id, members);
            CREATE INDEX IF NOT EXISTS idx_msg_thread_parent ON message_threads(workspace_id, parent_id, ts);
            CREATE INDEX IF NOT EXISTS idx_reactions_user ON message_reactions(workspace_id, user_id, message_id);
            CREATE INDEX IF NOT EXISTS idx_ann_workspace ON announcements(workspace_id, pinned, created DESC);
            CREATE INDEX IF NOT EXISTS idx_intake_forms ON intake_forms(workspace_id, active);
            """)
        except Exception as e:
            print(f"Index creation: {e}")
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
        try: db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?)",(u[0],ws_id,*u[1:],ts(),None))
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

# Lightweight in-memory workspace settings cache (TTL=60s per workspace)
import time as _time_mod
_ws_cache = {}
_ws_cache_ttl = {}
def get_ws_cached(ws_id):
    """Return cached workspace settings, refreshing every 60s."""
    now = _time_mod.time()
    if ws_id in _ws_cache and now - _ws_cache_ttl.get(ws_id,0) < 60:
        return _ws_cache[ws_id]
    try:
        with get_db() as db:
            ws = db.execute("SELECT * FROM workspaces WHERE id=?",(ws_id,)).fetchone()
            if ws:
                _ws_cache[ws_id] = dict(ws)
                _ws_cache_ttl[ws_id] = now
                return _ws_cache[ws_id]
    except: pass
    return None

def invalidate_ws_cache(ws_id):
    """Call after updating workspace settings."""
    _ws_cache.pop(ws_id, None)
    _ws_cache_ttl.pop(ws_id, None)

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route("/api/auth/login",methods=["POST"])
def login():
    d=request.json or {}
    email=d.get("email","").strip().lower()
    password=d.get("password","")
    with get_db() as db:
        u=db.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        if not u: return jsonify({"error":"Invalid email or password"}),401
        if not verify_pw(password, u["password"]):
            return jsonify({"error":"Invalid email or password"}),401
        if not (u["password"].startswith("$2b$") or u["password"].startswith("$2a$")):
            try:
                new_hash = hash_pw(password)
                db.execute("UPDATE users SET password=? WHERE id=?",(new_hash, u["id"]))
            except Exception: pass
        ws = db.execute("SELECT * FROM workspaces WHERE id=?",(u["workspace_id"],)).fetchone()
        otp_enabled = ws and ws.get("otp_enabled", 0)
        if otp_enabled:
            smtp_ok = ws.get("smtp_username") and ws.get("smtp_password")
            if smtp_ok:
                import time as _time
                code = generate_otp()
                with _otp_lock:
                    _otp_store[email] = {
                        "code": code,
                        "expires": _time.time() + 600,  # 10 minutes
                        "user_id": u["id"],
                        "workspace_id": u["workspace_id"],
                        "name": u["name"]
                    }
                sent = send_otp_email(email, code, u["name"])
                if sent:
                    return jsonify({"otp_required": True, "email": email, "name": u["name"]}), 200
        # Check TOTP 2FA before creating session
        totp_rec = db.execute("SELECT secret,enabled FROM totp_secrets WHERE user_id=? AND enabled=1",
                               (u["id"],)).fetchone()
        if totp_rec:
            # Store pending auth in a short-lived session key, not full login
            session["_totp_pending_uid"] = u["id"]
            session["_totp_pending_ws"]  = u["workspace_id"]
            return jsonify({"totp_required": True, "email": email}), 200

        session.permanent=True
        session["user_id"]=u["id"]
        session["workspace_id"]=u["workspace_id"]
        session.pop("_logged_out", None)
        try:
            db.execute("UPDATE users SET last_active=? WHERE id=?",
                       (datetime.utcnow().isoformat(), u["id"]))
        except Exception: pass
        return jsonify(dict(u))


# ── TOTP verification during login ───────────────────────────────────────────
@app.route("/api/auth/totp-login", methods=["POST"])
def totp_login():
    """Verify TOTP code for pending 2FA login."""
    d = request.json or {}
    code = d.get("code","").strip()
    uid  = session.get("_totp_pending_uid")
    ws_id= session.get("_totp_pending_ws")
    if not uid:
        return jsonify({"error":"No pending 2FA login. Please log in again."}),400
    with get_db() as db:
        rec = db.execute("SELECT secret,backup_codes FROM totp_secrets WHERE user_id=? AND enabled=1",(uid,)).fetchone()
        if not rec:
            return jsonify({"error":"2FA record not found"}),400
        if _totp_verify(rec["secret"], code):
            session.pop("_totp_pending_uid", None)
            session.pop("_totp_pending_ws",  None)
            session.pop("_logged_out", None)
            session.permanent = True
            session["user_id"] = uid
            session["workspace_id"] = ws_id
            db.execute("UPDATE users SET last_active=? WHERE id=?",(datetime.utcnow().isoformat(),uid))
            u = db.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
            return jsonify(dict(u))
        # Check backup codes
        backup = json.loads(rec["backup_codes"] or "[]")
        if code.upper() in backup:
            backup.remove(code.upper())
            db.execute("UPDATE totp_secrets SET backup_codes=? WHERE user_id=?",(json.dumps(backup),uid))
            session.pop("_totp_pending_uid", None)
            session.pop("_totp_pending_ws",  None)
            session.pop("_logged_out", None)
            session.permanent = True
            session["user_id"] = uid
            session["workspace_id"] = ws_id
            u = db.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
            return jsonify(dict(u))
        return jsonify({"error":"Invalid code. Check your authenticator app."}),401

@app.route("/api/auth/verify-otp",methods=["POST"])
def verify_otp():
    d=request.json or {}
    email=d.get("email","").strip().lower()
    code=d.get("code","").strip()
    import time as _time
    with _otp_lock:
        entry = _otp_store.get(email)
        if not entry:
            return jsonify({"error":"OTP expired or not found. Please log in again."}),400
        if _time.time() > entry["expires"]:
            del _otp_store[email]
            return jsonify({"error":"OTP has expired. Please log in again."}),400
        if entry["code"] != code:
            return jsonify({"error":"Invalid OTP code. Please try again."}),401
        del _otp_store[email]
    with get_db() as db:
        u=db.execute("SELECT * FROM users WHERE id=?",(entry["user_id"],)).fetchone()
        if not u: return jsonify({"error":"User not found"}),404
        session.permanent=True
        session["user_id"]=u["id"]
        session["workspace_id"]=u["workspace_id"]
        try:
            db.execute("UPDATE users SET last_active=? WHERE id=?",
                       (datetime.utcnow().isoformat(), u["id"]))
        except Exception: pass
        return jsonify(dict(u))

@app.route("/api/auth/resend-otp",methods=["POST"])
def resend_otp():
    d=request.json or {}
    email=d.get("email","").strip().lower()
    import time as _time
    with _otp_lock:
        entry = _otp_store.get(email)
        if not entry:
            return jsonify({"error":"Session expired. Please log in again."}),400
        last_sent = entry.get("last_sent", 0)
        if _time.time() - last_sent < 60:
            wait = int(60 - (_time.time() - last_sent))
            return jsonify({"error":f"Please wait {wait}s before resending."}),429
        code = generate_otp()
        entry["code"] = code
        entry["expires"] = _time.time() + 600
        entry["last_sent"] = _time.time()
    sent = send_otp_email(email, code, entry["name"])
    if sent:
        return jsonify({"ok": True, "message": "New OTP sent to your email."})
    return jsonify({"error":"Failed to send email. Check SMTP settings."}),500

@app.route("/api/auth/logout",methods=["POST"])
def logout():
    session.clear()
    session["_logged_out"] = True  # prevents /api/auth/me from auto-logging back in
    response = jsonify({"ok": True})
    # Expire the session cookie immediately in the browser
    response.set_cookie("session", "", expires=0, httponly=True, samesite="Lax")
    return response

@app.route("/signout")
@app.route("/sign-out")
def signout_redirect():
    """GET /signout — clear session, expire cookie, redirect to login."""
    session.clear()
    resp = app.make_response(
        '<html><head><meta http-equiv="refresh" content="0;url=/?action=login"/></head><body>Signing out...</body></html>'
    )
    cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    resp.set_cookie(cookie_name, "", expires=0, httponly=True, samesite="Lax", path="/")
    return resp


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
            return jsonify({"id":uid,"workspace_id":ws_id,"name":d["name"],"email":d["email"],
                            "role":d.get("role","Developer"),"avatar":av,"color":c})
    except Exception as e:
        if "UNIQUE" in str(e): return jsonify({"error":"Email already registered"}),400
        return jsonify({"error":str(e)}),500

@app.route("/api/presence", methods=["POST"])
@login_required
def update_presence():
    with get_db() as db:
        # Store without Z suffix so string comparisons work consistently
        db.execute("UPDATE users SET last_active=? WHERE id=? AND workspace_id=?",
                   (datetime.utcnow().isoformat(), session["user_id"], wid()))
        return jsonify({"ok": True})

@app.route("/api/presence")
@login_required
def get_presence():
    """Returns list of user IDs active in last 2 minutes."""
    with get_db() as db:
        # Use 3-minute window; strip Z suffix so string comparison is consistent
        cutoff = (datetime.utcnow() - timedelta(minutes=3)).isoformat()
        rows = db.execute(
            "SELECT id FROM users WHERE workspace_id=? AND (REPLACE(last_active,'Z','')>? OR last_active>?)",
            (wid(), cutoff, cutoff)).fetchall()
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
    # Session is fully cleared + cookie expired on logout.
    # Absence of user_id is the only check needed.
    if "user_id" not in session: return jsonify({"error":"Not logged in"}),401
    with get_db() as db:
        u=db.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not u: session.clear(); return jsonify({"error":"Not found"}),404
        if u["workspace_id"]: session["workspace_id"]=u["workspace_id"]
        return jsonify(dict(u))

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
        invalidate_ws_cache(wid())
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
        caller = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        can_see_pw = (caller["role"] if caller else "") in ("Admin","Manager")
        # Never return password hash or avatar_data blob; conditionally return plain_password
        rows = db.execute(
            "SELECT id,workspace_id,name,email,role,avatar,color,created,"
            "COALESCE(last_active,'') as last_active,"
            "COALESCE(is_guest,0) as is_guest "
            + (", COALESCE(plain_password,'') as plain_password " if can_see_pw else "")
            + "FROM users WHERE workspace_id=? ORDER BY name",
            (wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

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
            db.execute("INSERT INTO users (id,workspace_id,name,email,password,role,avatar,color,created,avatar_data,plain_password) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (uid,wid(),d["name"],d["email"],hash_pw(d["password"]),
                        d.get("role","Developer"),av,c,ts(),None,d["password"]))
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
        if "password" in d: db.execute("UPDATE users SET password=?,plain_password=? WHERE id=? AND workspace_id=?",(hash_pw(d["password"]),d["password"],uid,wid()))
        if "avatar_data" in d: db.execute("UPDATE users SET avatar_data=? WHERE id=? AND workspace_id=?",(d["avatar_data"],uid,wid()))
        u=db.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        if u:
            caller=db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
            caller_role=caller["role"] if caller else "Developer"
            result=dict(u)
            result.pop("password",None)
            if caller_role not in ("Admin","Manager"):
                result.pop("plain_password",None)
            return jsonify(result)
        return jsonify({})

@app.route("/api/users/<uid>",methods=["DELETE"])
@login_required
def del_user(uid):
    with get_db() as db:
        db.execute("DELETE FROM users WHERE id=? AND workspace_id=?",(uid,wid()))
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
    page    = max(1, int(request.args.get("page","1") or 1))
    limit   = min(200, max(10, int(request.args.get("limit","200") or 200)))
    offset  = (page-1)*limit
    with get_db() as db:
        cols = "id,workspace_id,name,description,owner,members,start_date,target_date,progress,color,created,team_id,COALESCE(budget,0) as budget,COALESCE(budget_spent,0) as budget_spent"
        if team_id:
            rows = db.execute(
                "SELECT "+cols+" FROM projects WHERE workspace_id=? AND team_id=? ORDER BY created DESC LIMIT ? OFFSET ?",
                (wid(), team_id, limit, offset)).fetchall()
            total = db.execute("SELECT COUNT(*) as cnt FROM projects WHERE workspace_id=? AND team_id=?",
                               (wid(),team_id)).fetchone()["cnt"]
        else:
            rows = db.execute(
                "SELECT "+cols+" FROM projects WHERE workspace_id=? ORDER BY created DESC LIMIT ? OFFSET ?",
                (wid(), limit, offset)).fetchall()
            total = db.execute("SELECT COUNT(*) as cnt FROM projects WHERE workspace_id=?",(wid(),)).fetchone()["cnt"]
        return jsonify({"items":[dict(r) for r in rows],"total":total,"page":page,"limit":limit})

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
                    d.get("color","#aaff00"),ts(),d.get("team_id","")))
        p=db.execute("SELECT * FROM projects WHERE id=?",(pid,)).fetchone()
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
        updated=db.execute("SELECT * FROM projects WHERE id=?",(pid,)).fetchone()
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
    team_id  = request.args.get("team_id","")
    stage    = request.args.get("stage","")
    assignee = request.args.get("assignee","")
    project  = request.args.get("project","")
    priority = request.args.get("priority","")
    page     = max(1, int(request.args.get("page","1") or 1))
    limit    = min(500, max(10, int(request.args.get("limit","500") or 500)))
    offset   = (page-1)*limit
    with get_db() as db:
        where  = ["t.workspace_id=?"]
        params = [wid()]
        if team_id:
            team = db.execute("SELECT member_ids FROM teams WHERE id=? AND workspace_id=?",(team_id,wid())).fetchone()
            member_ids = json.loads(team["member_ids"] if team else "[]")
            proj_ids   = [p["id"] for p in db.execute(
                "SELECT id FROM projects WHERE workspace_id=? AND team_id=?",(wid(),team_id)).fetchall()]
            if proj_ids and member_ids:
                ph_p = ",".join("?"*len(proj_ids))
                ph_m = ",".join("?"*len(member_ids))
                where.append("(t.team_id=? OR t.project IN("+ph_p+") OR t.assignee IN("+ph_m+"))")
                params += [team_id] + proj_ids + member_ids
            elif proj_ids:
                ph_p = ",".join("?"*len(proj_ids))
                where.append("(t.team_id=? OR t.project IN("+ph_p+"))")
                params += [team_id] + proj_ids
            elif member_ids:
                ph_m = ",".join("?"*len(member_ids))
                where.append("(t.team_id=? OR t.assignee IN("+ph_m+"))")
                params += [team_id] + member_ids
            else:
                where.append("t.team_id=?")
                params.append(team_id)
        if stage:    where.append("t.stage=?");    params.append(stage)
        if assignee: where.append("t.assignee=?"); params.append(assignee)
        if project:  where.append("t.project=?");  params.append(project)
        if priority: where.append("t.priority=?"); params.append(priority)
        where_sql = " AND ".join(where)
        cols = ("t.id,t.workspace_id,t.title,t.description,t.project,t.assignee,"
                "t.priority,t.stage,t.created,t.due,t.pct,t.comments,t.team_id,"
                "t.parent_id,t.story_points,t.sprint,t.task_type,t.labels,"
                "t.recurring,t.recur_parent,t.depends_on,"
                "COALESCE(t.time_logged,0) as time_logged")
        rows = db.execute(
            "SELECT "+cols+" FROM tasks t WHERE "+where_sql+" ORDER BY t.created DESC LIMIT ? OFFSET ?",
            params+[limit,offset]).fetchall()
        total_row = db.execute("SELECT COUNT(*) as cnt FROM tasks t WHERE "+where_sql, params).fetchone()
        total_count = total_row["cnt"] if total_row else 0
        return jsonify({"items":[dict(r) for r in rows],"total":total_count,"page":page,"limit":limit,"pages":max(1,(total_count+limit-1)//limit)})

def next_task_id(db, ws):
    """Race-condition-free ID using high-res timestamp + random suffix."""
    import time, random
    ts_ms = int(time.time() * 1000)
    rand3 = random.randint(100,999)
    return f"T-{ts_ms % 10000000:07d}-{rand3}"

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
        t=db.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone()
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
        old_stage=t["stage"]
        def tf(key,default=''):
            return t[key] if key in t.keys() else default
        labels_val=d.get("labels",None)
        if labels_val is not None and isinstance(labels_val,list): labels_val=json.dumps(labels_val)
        elif labels_val is None: labels_val=tf("labels","[]")
        comments_val=d.get("comments",None)
        if comments_val is None: comments_val=json.loads(t["comments"] or "[]")
        db.execute("""UPDATE tasks SET title=?,description=?,project=?,assignee=?,
                      priority=?,stage=?,due=?,pct=?,comments=?,team_id=?,
                      story_points=?,task_type=?,labels=?,sprint=?,
                      recurring=?,depends_on=? WHERE id=? AND workspace_id=?""",
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
                    d.get("recurring",tf("recurring","")),
                    json.dumps(d.get("depends_on",json.loads(t.get("depends_on") or "[]"))),
                    tid,wid()))
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
        return jsonify(dict(db.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone()))


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
        row=db.execute("SELECT * FROM files WHERE id=?",(fid,)).fetchone()
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
    project = request.args.get("project","")
    limit   = min(200, int(request.args.get("limit","100") or 100))
    before  = request.args.get("before","")
    if not project: return jsonify([])
    with get_db() as db:
        if before:
            rows=db.execute(
                "SELECT m.id,m.workspace_id,m.sender,m.project,m.content,m.ts,"
                "COALESCE(m.is_system,0) as is_system,COALESCE(m.pinned,0) as pinned,"
                "u.name as sender_name,u.avatar as sender_avatar,u.color as sender_color "
                "FROM messages m LEFT JOIN users u ON m.sender=u.id "
                "WHERE m.project=? AND m.workspace_id=? AND m.ts<? "
                "ORDER BY m.ts DESC LIMIT ?",(project,wid(),before,limit)).fetchall()
        else:
            rows=db.execute(
                "SELECT m.id,m.workspace_id,m.sender,m.project,m.content,m.ts,"
                "COALESCE(m.is_system,0) as is_system,COALESCE(m.pinned,0) as pinned,"
                "u.name as sender_name,u.avatar as sender_avatar,u.color as sender_color "
                "FROM messages m LEFT JOIN users u ON m.sender=u.id "
                "WHERE m.project=? AND m.workspace_id=? "
                "ORDER BY m.ts DESC LIMIT ?",(project,wid(),limit)).fetchall()
        return jsonify(list(reversed([dict(r) for r in rows])))

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
        # Only notify project members, not entire workspace
        try:
            proj_members_row = db.execute("SELECT members FROM projects WHERE id=? AND workspace_id=?",(d.get("project",""),wid())).fetchone()
            proj_member_ids = json.loads(proj_members_row["members"] if proj_members_row and proj_members_row["members"] else "[]")
        except: proj_member_ids = []
        base_ts=int(datetime.now().timestamp()*1000)
        for i,uid in enumerate(proj_member_ids):
            if uid == session["user_id"]: continue
            nid=f"n{base_ts+i}"
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"message",f"#{proj_name} — {sender_name}: {preview}",uid,0,ts()))
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
        return jsonify(dict(db.execute("SELECT * FROM teams WHERE id=?",(tid,)).fetchone()))

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
        return jsonify(dict(db.execute("SELECT * FROM teams WHERE id=?",(tid,)).fetchone()))

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
                "overdue":len([t for t in mtasks if t["due"] and t["due"]<datetime.utcnow().isoformat() and t["stage"]!="completed"]),
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
    status   = request.args.get("status","")
    team_id  = request.args.get("team_id","")
    assignee = request.args.get("assignee","")
    page     = max(1, int(request.args.get("page","1") or 1))
    limit    = min(200, max(10, int(request.args.get("limit","100") or 100)))
    offset   = (page-1)*limit
    with get_db() as db:
        where  = ["t.workspace_id=?"]
        params = [wid()]
        if team_id:
            team = db.execute("SELECT member_ids FROM teams WHERE id=? AND workspace_id=?",(team_id,wid())).fetchone()
            member_ids = json.loads(team["member_ids"] if team else "[]")
            proj_ids   = [p["id"] for p in db.execute(
                "SELECT id FROM projects WHERE workspace_id=? AND team_id=?",(wid(),team_id)).fetchall()]
            if proj_ids and member_ids:
                ph_p=",".join("?"*len(proj_ids)); ph_m=",".join("?"*len(member_ids))
                where.append("(t.team_id=? OR t.project IN("+ph_p+") OR t.assignee IN("+ph_m+"))")
                params += [team_id]+proj_ids+member_ids
            elif proj_ids:
                where.append("(t.team_id=? OR t.project IN("+",".join("?"*len(proj_ids))+"))")
                params += [team_id]+proj_ids
            else:
                where.append("t.team_id=?"); params.append(team_id)
        if status:   where.append("t.status=?");   params.append(status)
        if assignee: where.append("t.assignee=?"); params.append(assignee)
        where_sql = " AND ".join(where)
        rows  = db.execute(
            "SELECT t.*,u.name as reporter_name FROM tickets t LEFT JOIN users u ON t.reporter=u.id "
            "WHERE "+where_sql+" ORDER BY t.created DESC LIMIT ? OFFSET ?",
            params+[limit,offset]).fetchall()
        total = db.execute("SELECT COUNT(*) as cnt FROM tickets t WHERE "+where_sql,params).fetchone()["cnt"]
        return jsonify({"items":[dict(r) for r in rows],"total":total,"page":page,"limit":limit})

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
        return jsonify(dict(db.execute("SELECT * FROM tickets WHERE id=?",(tid,)).fetchone()))

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
        return jsonify(dict(db.execute("SELECT * FROM tickets WHERE id=?",(tid,)).fetchone()))

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
@app.route("/api/calls", methods=["GET"])
@login_required
def get_active_calls():
    with get_db() as db:
        rooms=db.execute("SELECT * FROM call_rooms WHERE workspace_id=? AND status='active' ORDER BY created DESC",(wid(),)).fetchall()
        result=[]
        uid=session["user_id"]
        for r in rooms:
            rd=dict(r)
            try:
                created=datetime.fromisoformat(rd['created'].replace('Z',''))
                if (datetime.utcnow()-created).total_seconds()>28800:
                    db.execute("UPDATE call_rooms SET status='ended' WHERE id=?",(rd['id'],))
                    continue
            except: pass
            # STRICT: only show room if user was explicitly invited or is already a participant
            try:
                invited=json.loads(rd.get("invited_users","[]") or "[]")
                parts=json.loads(rd.get("participants","[]") or "[]")
            except: invited=[]; parts=[]
            if uid in invited or uid in parts or rd.get("initiator")==uid:
                result.append(rd)
        return jsonify(result)

@app.route("/api/calls", methods=["POST"])
@login_required
def create_call():
    d=request.json or {}
    room_id=f"call{int(datetime.now().timestamp()*1000)}"
    with get_db() as db:
        caller=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
        cname=caller["name"] if caller else "Someone"
        room_name=d.get("name",f"{cname}'s Instant Meet")
        db.execute("INSERT INTO call_rooms VALUES (?,?,?,?,?,?,?)",
                   (room_id,wid(),room_name,session["user_id"],json.dumps([session["user_id"]]),"active",ts()))
        users=db.execute("SELECT id FROM users WHERE workspace_id=? AND id!=?",(wid(),session["user_id"])).fetchall()
        invited=[u["id"] for u in users]
        db.execute("UPDATE call_rooms SET invited_users=? WHERE id=?",(json.dumps(invited),room_id))
        for uid in invited:
            nid=f"n{int(datetime.now().timestamp()*1000)}{uid}"
            try:
                db.execute("INSERT INTO notifications(id,workspace_id,type,content,user_id,read,ts,sender_id) VALUES (?,?,?,?,?,?,?,?)",
                           (nid,wid(),"call",f"📞 {cname} started an Instant Meet — Join now! ({room_name})",uid,0,ts(),session["user_id"]))
            except:
                db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                           (nid,wid(),"call",f"📞 {cname} started an Instant Meet — Join now! ({room_name})",uid,0,ts()))
        return jsonify({"room_id":room_id,"name":room_name})

@app.route("/api/calls/<room_id>/join", methods=["POST"])
@login_required
def join_call(room_id):
    with get_db() as db:
        room=db.execute("SELECT * FROM call_rooms WHERE id=? AND workspace_id=?",(room_id,wid())).fetchone()
        if not room: return jsonify({"error":"Room not found"}),404
        if room["status"]!="active": return jsonify({"error":"Call has ended"}),410
        parts=json.loads(room["participants"])
        if session["user_id"] not in parts:
            parts.append(session["user_id"])
            db.execute("UPDATE call_rooms SET participants=? WHERE id=?",(json.dumps(parts),room_id))
        return jsonify({"participants":parts,"name":room["name"]})

@app.route("/api/calls/<room_id>/leave", methods=["POST"])
@login_required
def leave_call(room_id):
    with get_db() as db:
        room=db.execute("SELECT * FROM call_rooms WHERE id=? AND workspace_id=?",(room_id,wid())).fetchone()
        if not room: return jsonify({"ok":True})
        parts=[p for p in json.loads(room["participants"]) if p!=session["user_id"]]
        if not parts: db.execute("UPDATE call_rooms SET status='ended' WHERE id=?",(room_id,))
        else: db.execute("UPDATE call_rooms SET participants=? WHERE id=?",(json.dumps(parts),room_id))
        return jsonify({"ok":True})

@app.route("/api/calls/<room_id>/invite/<target_id>", methods=["POST"])
@login_required
def invite_to_call(room_id, target_id):
    with get_db() as db:
        room=db.execute("SELECT * FROM call_rooms WHERE id=? AND workspace_id=?",(room_id,wid())).fetchone()
        if not room: return jsonify({"error":"Room not found"}),404
        caller=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
        cname=caller["name"] if caller else "Someone"
        nid=f"n{int(datetime.now().timestamp()*1000)}"
        db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                   (nid,wid(),"call",f"📞 {cname} invited you to: {room['name']} — Join now!",target_id,0,ts()))
        # Add target to invited_users list
        try:
            inv=json.loads(room.get("invited_users","[]") or "[]")
            if target_id not in inv:
                inv.append(target_id)
                db.execute("UPDATE call_rooms SET invited_users=? WHERE id=?",(json.dumps(inv),room_id))
        except: pass
        return jsonify({"ok":True})

@app.route("/api/calls/<room_id>/signal", methods=["POST"])
@login_required
def send_signal(room_id):
    d=request.json or {}
    sid=f"sig{int(datetime.now().timestamp()*1000)}{secrets.token_hex(3)}"
    with get_db() as db:
        db.execute("INSERT INTO call_signals VALUES (?,?,?,?,?,?,?,?,?)",
                   (sid,wid(),room_id,session["user_id"],d.get("to_user",""),
                    d.get("type",""),json.dumps(d.get("data",{})),0,ts()))
        old=db.execute("SELECT id FROM call_signals WHERE room_id=? AND consumed=1 ORDER BY created DESC LIMIT -1 OFFSET 200",(room_id,)).fetchall()
        if old: db.execute(f"DELETE FROM call_signals WHERE id IN ({','.join('?'*len(old))})",[r['id'] for r in old])
        return jsonify({"ok":True,"id":sid})

@app.route("/api/calls/<room_id>/signals", methods=["GET"])
@login_required
def get_signals(room_id):
    with get_db() as db:
        rows=db.execute("""SELECT * FROM call_signals WHERE workspace_id=? AND room_id=? AND to_user=? AND consumed=0
            ORDER BY created LIMIT 50""",(wid(),room_id,session["user_id"])).fetchall()
        ids=[r["id"] for r in rows]
        if ids: db.execute(f"UPDATE call_signals SET consumed=1 WHERE id IN ({','.join('?'*len(ids))})",ids)
        return jsonify([dict(r) for r in rows])

@app.route("/api/calls/<room_id>/ping", methods=["POST"])
@login_required
def ping_call(room_id):
    with get_db() as db:
        room=db.execute("SELECT * FROM call_rooms WHERE id=? AND workspace_id=?",(room_id,wid())).fetchone()
        if not room: return jsonify({"error":"ended"}),404
        if room["status"]!="active": return jsonify({"error":"ended"}),410
        return jsonify({"participants":json.loads(room["participants"]),"status":room["status"],"name":room["name"]})

@app.route("/api/reminders/due", methods=["GET"])
@login_required
def due_reminders():
    """Return reminders that should fire now (within last 2 min, not yet fired)"""
    now=datetime.utcnow().isoformat()+"Z"
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

@app.route("/api/notifications/read-all",methods=["PUT"])
@login_required
def read_all_notifs():
    with get_db() as db:
        db.execute("UPDATE notifications SET read=1 WHERE workspace_id=? AND user_id=?",(wid(),session["user_id"]))
        return jsonify({"ok":True})

@app.route("/api/notifications/all",methods=["DELETE"])
@login_required
def clear_all_notifs():
    with get_db() as db:
        db.execute("DELETE FROM notifications WHERE workspace_id=? AND user_id=?",(wid(),session["user_id"]))
        return jsonify({"ok":True})

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
<action>{{"type":"create_project","name":"Project Name","description":"desc","color":"#aaff00","members":["user_id"]}}</action>
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
                                act.get("color","#aaff00"),ts()))
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


# ── Audit log helper ──────────────────────────────────────────────────────────
def audit(db, action, entity_type='', entity_id='', details=''):
    try:
        aid = f"al{int(__import__('time').time()*1000)}"
        ip = request.remote_addr or ''
        db.execute("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?,?,?)",
                   (aid, wid(), session.get('user_id',''), action, entity_type, entity_id, details, ip, ts()))
    except: pass

# ── Time Tracking ─────────────────────────────────────────────────────────────
@app.route("/api/time-logs", methods=["GET"])
@login_required
def get_time_logs():
    task_id = request.args.get("task_id","")
    with get_db() as db:
        if task_id:
            rows = db.execute("SELECT tl.*,u.name as user_name FROM time_logs tl LEFT JOIN users u ON tl.user_id=u.id WHERE tl.workspace_id=? AND tl.task_id=? ORDER BY tl.created DESC",(wid(),task_id)).fetchall()
        else:
            rows = db.execute("SELECT tl.*,u.name as user_name FROM time_logs tl LEFT JOIN users u ON tl.user_id=u.id WHERE tl.workspace_id=? ORDER BY tl.created DESC LIMIT 200",(wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/time-logs", methods=["POST"])
@login_required
def create_time_log():
    d = request.json or {}
    if not d.get("task_id"): return jsonify({"error":"task_id required"}),400
    if not d.get("minutes",0): return jsonify({"error":"minutes required"}),400
    with get_db() as db:
        lid = f"tl{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO time_logs VALUES (?,?,?,?,?,?,?,?)",
                   (lid,wid(),d["task_id"],session["user_id"],d.get("description",""),
                    int(d["minutes"]),d.get("logged_date",ts()[:10]),ts()))
        db.execute("UPDATE tasks SET time_logged=COALESCE(time_logged,0)+? WHERE id=? AND workspace_id=?",
                   (int(d["minutes"]),d["task_id"],wid()))
        audit(db,"time_log","task",d["task_id"],f"{d['minutes']}min logged")
        return jsonify({"ok":True,"id":lid})

@app.route("/api/time-logs/<lid>", methods=["DELETE"])
@login_required
def delete_time_log(lid):
    with get_db() as db:
        row = db.execute("SELECT * FROM time_logs WHERE id=? AND workspace_id=?",(lid,wid())).fetchone()
        if not row: return jsonify({"error":"Not found"}),404
        db.execute("UPDATE tasks SET time_logged=MAX(0,COALESCE(time_logged,0)-?) WHERE id=? AND workspace_id=?",
                   (row["minutes"],row["task_id"],wid()))
        db.execute("DELETE FROM time_logs WHERE id=?",(lid,))
        return jsonify({"ok":True})

# ── Task Dependencies ─────────────────────────────────────────────────────────
@app.route("/api/tasks/<tid>/dependencies", methods=["GET"])
@login_required
def get_task_deps(tid):
    with get_db() as db:
        task = db.execute("SELECT depends_on FROM tasks WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()
        if not task: return jsonify([])
        dep_ids = json.loads(task["depends_on"] or "[]")
        deps = []
        for did in dep_ids:
            t = db.execute("SELECT id,title,stage,priority FROM tasks WHERE id=?",(did,)).fetchone()
            if t: deps.append(dict(t))
        return jsonify(deps)

@app.route("/api/tasks/<tid>/dependencies", methods=["POST"])
@login_required
def add_task_dep(tid):
    d = request.json or {}
    dep_id = d.get("dep_id","")
    if not dep_id: return jsonify({"error":"dep_id required"}),400
    if dep_id == tid: return jsonify({"error":"Cannot depend on self"}),400
    with get_db() as db:
        task = db.execute("SELECT depends_on FROM tasks WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()
        if not task: return jsonify({"error":"Task not found"}),404
        deps = json.loads(task["depends_on"] or "[]")
        if dep_id not in deps: deps.append(dep_id)
        db.execute("UPDATE tasks SET depends_on=? WHERE id=? AND workspace_id=?",(json.dumps(deps),tid,wid()))
        return jsonify({"ok":True,"depends_on":deps})

@app.route("/api/tasks/<tid>/dependencies/<dep_id>", methods=["DELETE"])
@login_required
def remove_task_dep(tid,dep_id):
    with get_db() as db:
        task = db.execute("SELECT depends_on FROM tasks WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()
        if not task: return jsonify({"error":"Not found"}),404
        deps = [x for x in json.loads(task["depends_on"] or "[]") if x != dep_id]
        db.execute("UPDATE tasks SET depends_on=? WHERE id=? AND workspace_id=?",(json.dumps(deps),tid,wid()))
        return jsonify({"ok":True})

# ── Recurring Tasks ───────────────────────────────────────────────────────────
@app.route("/api/tasks/<tid>/recurring", methods=["PUT"])
@login_required
def set_recurring(tid):
    d = request.json or {}
    pattern = d.get("pattern","") # daily|weekly|monthly|""
    with get_db() as db:
        db.execute("UPDATE tasks SET recurring=? WHERE id=? AND workspace_id=?",(pattern,tid,wid()))
        return jsonify({"ok":True})

def _spawn_recurring_tasks():
    """Run periodically to create recurring task instances."""
    try:
        with get_db() as db:
            now_date = datetime.utcnow().date()
            tasks = db.execute("SELECT * FROM tasks WHERE recurring!='' AND recurring IS NOT NULL").fetchall()
            for t in tasks:
                pattern = t["recurring"]
                if not pattern: continue
                last_due = t["due"] or t["created"][:10]
                try: last_dt = datetime.strptime(last_due[:10],"%Y-%m-%d").date()
                except: continue
                if pattern=="daily": next_dt = last_dt + timedelta(days=1)
                elif pattern=="weekly": next_dt = last_dt + timedelta(weeks=1)
                elif pattern=="monthly":
                    m = last_dt.month+1 if last_dt.month<12 else 1
                    y = last_dt.year if last_dt.month<12 else last_dt.year+1
                    next_dt = last_dt.replace(year=y,month=m)
                else: continue
                if next_dt <= now_date:
                    existing = db.execute("SELECT id FROM tasks WHERE recur_parent=? AND due=?",(t["id"],str(next_dt))).fetchone()
                    if not existing:
                        new_id = next_task_id(db, t["workspace_id"])
                        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                   (new_id,t["workspace_id"],t["title"],t["description"],t["project"],
                                    t["assignee"],t["priority"],"backlog",ts(),str(next_dt),0,"[]",
                                    t.get("team_id",""),t["id"],0,"","task","[]"))
                        db.execute("UPDATE tasks SET due=?,recurring=? WHERE id=? AND workspace_id=?",
                                   (str(next_dt),pattern,t["id"],t["workspace_id"]))
    except Exception as e:
        print(f"Recurring tasks error: {e}")


# ── AI Doc Generation ─────────────────────────────────────────────────────────
@app.route("/api/docs/generate", methods=["POST"])
@login_required
def generate_doc():
    d = request.json or {}
    prompt = d.get("prompt","").strip()
    doc_type = d.get("doc_type","general")  # general|architecture|api|readme|runbook
    project_id = d.get("project_id","")
    if not prompt: return jsonify({"error":"Prompt required"}),400

    with get_db() as db:
        ws = db.execute("SELECT ai_api_key,name FROM workspaces WHERE id=?",(wid(),)).fetchone()
        api_key = (ws["ai_api_key"] if ws and ws["ai_api_key"] else "").strip()
        if not api_key:
            return jsonify({"error":"NO_KEY","message":"Configure your Anthropic API key in Settings to use AI doc generation."}),400

        projects = db.execute("SELECT id,name,description FROM projects WHERE workspace_id=?",(wid(),)).fetchall()
        proj_ctx = "\n".join([f"- {p['name']}: {p['description']}" for p in projects]) or "No projects"
        proj_name = ""
        if project_id:
            p = db.execute("SELECT name,description FROM projects WHERE id=?",(project_id,)).fetchone()
            if p: proj_name = p["name"]

    TYPE_INSTRUCTIONS = {
        "general": "Write a comprehensive, well-structured technical document. Use clear headings, bullet points where appropriate, and code blocks for any code examples.",
        "architecture": """Write an architectural documentation document. Include:
1. Overview section
2. System components and their responsibilities
3. A Mermaid.js architecture diagram (use ```mermaid code blocks with graph TD or flowchart LR syntax)
4. Data flow description
5. Technology stack
6. Deployment considerations
Make the Mermaid diagram detailed and accurate to the described system.""",
        "api": """Write API documentation. Include:
1. Overview
2. Authentication
3. Base URL
4. Endpoints table with Method, Path, Description
5. Request/Response examples in JSON code blocks
6. Error codes""",
        "readme": """Write a professional README.md. Include:
1. Project title and badge line
2. Short description
3. Features list
4. Installation steps (with code blocks)
5. Usage examples
6. Configuration
7. Contributing section""",
        "runbook": """Write an operational runbook. Include:
1. Service overview
2. Prerequisites
3. Common operations (step by step)
4. Troubleshooting guide
5. Escalation contacts placeholder
6. Monitoring and alerts"""
    }

    system = f"""You are a senior technical writer for the workspace "{ws['name'] if ws else 'VEWIT'}".
Generate professional technical documentation based on the user's request.
{TYPE_INSTRUCTIONS.get(doc_type, TYPE_INSTRUCTIONS['general'])}
Projects in this workspace:
{proj_ctx}
{"Focus on the project: " + proj_name if proj_name else ""}
Output ONLY the document content — no preamble, no meta-commentary.
For Mermaid diagrams, always wrap in ```mermaid code fences."""

    try:
        req_data = json.dumps({
            "model": "claude-sonnet-4-5",
            "max_tokens": 3000,
            "system": system,
            "messages": [{"role":"user","content": prompt}]
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=req_data, method="POST",
            headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode())
            generated = result["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 401: return jsonify({"error":"INVALID_KEY","message":"Invalid API key."}),400
        return jsonify({"error":"API_ERROR","message":f"AI error: {body[:200]}"}),500
    except Exception as e:
        return jsonify({"error":"NETWORK_ERROR","message":str(e)}),500

    # Auto-save the generated doc
    title_line = generated.split('\n')[0].lstrip('#').strip() or prompt[:60]
    did = f"doc{int(__import__('time').time()*1000)}"
    now = ts()
    with get_db() as db:
        db.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?)",
                   (did, wid(), project_id, title_line, generated,
                    session["user_id"], now, now, 0))
    return jsonify({"ok":True,"id":did,"title":title_line,"content":generated})

# ── Docs / Wiki ───────────────────────────────────────────────────────────────
@app.route("/api/docs", methods=["GET"])
@login_required
def get_docs():
    project_id = request.args.get("project_id","")
    with get_db() as db:
        if project_id:
            rows = db.execute("SELECT d.*,u.name as author_name FROM docs d LEFT JOIN users u ON d.author=u.id WHERE d.workspace_id=? AND d.project_id=? ORDER BY d.updated DESC",(wid(),project_id)).fetchall()
        else:
            rows = db.execute("SELECT d.*,u.name as author_name FROM docs d LEFT JOIN users u ON d.author=u.id WHERE d.workspace_id=? ORDER BY d.updated DESC",(wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/docs", methods=["POST"])
@login_required
def create_doc():
    d = request.json or {}
    if not d.get("title"): return jsonify({"error":"Title required"}),400
    with get_db() as db:
        did = f"doc{int(__import__('time').time()*1000)}"
        now = ts()
        db.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?)",
                   (did,wid(),d.get("project_id",""),d["title"],d.get("content",""),
                    session["user_id"],now,now,0))
        audit(db,"create","doc",did,d["title"])
        return jsonify({"ok":True,"id":did})

@app.route("/api/docs/<did>", methods=["PUT"])
@login_required
def update_doc(did):
    d = request.json or {}
    with get_db() as db:
        doc = db.execute("SELECT * FROM docs WHERE id=? AND workspace_id=?",(did,wid())).fetchone()
        if not doc: return jsonify({"error":"Not found"}),404
        db.execute("UPDATE docs SET title=?,content=?,project_id=?,is_public=?,updated=? WHERE id=?",
                   (d.get("title",doc["title"]),d.get("content",doc["content"]),
                    d.get("project_id",doc["project_id"]),int(d.get("is_public",doc["is_public"])),ts(),did))
        return jsonify({"ok":True})

@app.route("/api/docs/<did>", methods=["DELETE"])
@login_required
def delete_doc(did):
    with get_db() as db:
        db.execute("DELETE FROM docs WHERE id=? AND workspace_id=?",(did,wid()))
        return jsonify({"ok":True})

# ── Goals / OKRs ──────────────────────────────────────────────────────────────
@app.route("/api/goals", methods=["GET"])
@login_required
def get_goals():
    with get_db() as db:
        goals = db.execute("SELECT g.*,u.name as owner_name FROM goals g LEFT JOIN users u ON g.owner=u.id WHERE g.workspace_id=? ORDER BY g.created DESC",(wid(),)).fetchall()
        result = []
        for g in goals:
            krs = db.execute("SELECT * FROM goal_krs WHERE goal_id=?",(g["id"],)).fetchall()
            gd = dict(g); gd["krs"] = [dict(k) for k in krs]
            result.append(gd)
        return jsonify(result)

@app.route("/api/goals", methods=["POST"])
@login_required
def create_goal():
    d = request.json or {}
    if not d.get("title"): return jsonify({"error":"Title required"}),400
    with get_db() as db:
        gid = f"g{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO goals VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (gid,wid(),d["title"],d.get("description",""),
                    d.get("owner",session["user_id"]),"active",0,d.get("due",""),ts(),d.get("team_id","")))
        for kr in d.get("krs",[]):
            kid = f"kr{int(__import__('time').time()*1000)}{secrets.token_hex(2)}"
            db.execute("INSERT INTO goal_krs VALUES (?,?,?,?,?,?,?,?)",
                       (kid,gid,wid(),kr.get("title",""),kr.get("target",100),0,kr.get("unit","%"),ts()))
        return jsonify({"ok":True,"id":gid})

@app.route("/api/goals/<gid>", methods=["PUT"])
@login_required
def update_goal(gid):
    d = request.json or {}
    with get_db() as db:
        g = db.execute("SELECT * FROM goals WHERE id=? AND workspace_id=?",(gid,wid())).fetchone()
        if not g: return jsonify({"error":"Not found"}),404
        # Auto-calc progress from KRs
        krs = db.execute("SELECT * FROM goal_krs WHERE goal_id=?",(gid,)).fetchall()
        if krs:
            pct = sum(min(100,int((k["current"]/k["target"])*100)) if k["target"]>0 else 0 for k in krs) // len(krs)
        else: pct = d.get("progress",g["progress"])
        db.execute("UPDATE goals SET title=?,description=?,status=?,progress=?,due=?,owner=? WHERE id=?",
                   (d.get("title",g["title"]),d.get("description",g["description"]),
                    d.get("status",g["status"]),pct,d.get("due",g["due"]),
                    d.get("owner",g["owner"]),gid))
        # Update KRs
        for kr in d.get("krs",[]):
            if kr.get("id"):
                db.execute("UPDATE goal_krs SET current=?,title=?,target=?,unit=? WHERE id=?",
                           (kr.get("current",0),kr.get("title",""),kr.get("target",100),kr.get("unit","%"),kr["id"]))
        return jsonify({"ok":True})

@app.route("/api/goals/<gid>", methods=["DELETE"])
@login_required
def delete_goal(gid):
    with get_db() as db:
        db.execute("DELETE FROM goal_krs WHERE goal_id=?",(gid,))
        db.execute("DELETE FROM goals WHERE id=? AND workspace_id=?",(gid,wid()))
        return jsonify({"ok":True})

# ── Custom Fields ─────────────────────────────────────────────────────────────
@app.route("/api/custom-fields", methods=["GET"])
@login_required
def get_custom_fields():
    entity = request.args.get("entity","task")
    with get_db() as db:
        rows = db.execute("SELECT * FROM custom_fields WHERE workspace_id=? AND entity_type=? ORDER BY created",(wid(),entity)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/custom-fields", methods=["POST"])
@login_required
def create_custom_field():
    d = request.json or {}
    if not d.get("name"): return jsonify({"error":"Name required"}),400
    with get_db() as db:
        fid = f"cf{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO custom_fields VALUES (?,?,?,?,?,?,?)",
                   (fid,wid(),d.get("entity_type","task"),d["name"],
                    d.get("field_type","text"),json.dumps(d.get("options",[])),ts()))
        return jsonify({"ok":True,"id":fid})

@app.route("/api/custom-fields/<fid>", methods=["DELETE"])
@login_required
def delete_custom_field(fid):
    with get_db() as db:
        db.execute("DELETE FROM custom_field_values WHERE field_id=?",(fid,))
        db.execute("DELETE FROM custom_fields WHERE id=? AND workspace_id=?",(fid,wid()))
        return jsonify({"ok":True})

@app.route("/api/custom-field-values/<entity_id>", methods=["GET"])
@login_required
def get_cfv(entity_id):
    with get_db() as db:
        rows = db.execute("SELECT cfv.*,cf.name,cf.field_type FROM custom_field_values cfv JOIN custom_fields cf ON cfv.field_id=cf.id WHERE cfv.workspace_id=? AND cfv.entity_id=?",(wid(),entity_id)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/custom-field-values", methods=["POST"])
@login_required
def set_cfv():
    d = request.json or {}
    with get_db() as db:
        existing = db.execute("SELECT id FROM custom_field_values WHERE workspace_id=? AND field_id=? AND entity_id=?",(wid(),d["field_id"],d["entity_id"])).fetchone()
        if existing:
            db.execute("UPDATE custom_field_values SET value=?,updated=? WHERE id=?",(d.get("value",""),ts(),existing["id"]))
        else:
            vid = f"cfv{int(__import__('time').time()*1000)}"
            db.execute("INSERT INTO custom_field_values VALUES (?,?,?,?,?,?)",(vid,wid(),d["field_id"],d["entity_id"],d.get("value",""),ts()))
        return jsonify({"ok":True})

# ── Task Templates ────────────────────────────────────────────────────────────
@app.route("/api/task-templates", methods=["GET"])
@login_required
def get_task_templates():
    with get_db() as db:
        rows = db.execute("SELECT * FROM task_templates WHERE workspace_id=? ORDER BY created DESC",(wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/task-templates", methods=["POST"])
@login_required
def create_task_template():
    d = request.json or {}
    if not d.get("name"): return jsonify({"error":"Name required"}),400
    with get_db() as db:
        tid = f"tt{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO task_templates VALUES (?,?,?,?,?,?,?,?,?)",
                   (tid,wid(),d["name"],d.get("description",""),d.get("priority","medium"),
                    d.get("stage","backlog"),json.dumps(d.get("labels",[])),
                    json.dumps(d.get("subtasks",[])),ts()))
        return jsonify({"ok":True,"id":tid})

@app.route("/api/task-templates/<tid>", methods=["DELETE"])
@login_required
def delete_task_template(tid):
    with get_db() as db:
        db.execute("DELETE FROM task_templates WHERE id=? AND workspace_id=?",(tid,wid()))
        return jsonify({"ok":True})

# ── Sprints ───────────────────────────────────────────────────────────────────
@app.route("/api/sprints", methods=["GET"])
@login_required
def get_sprints():
    project_id = request.args.get("project_id","")
    with get_db() as db:
        if project_id:
            rows = db.execute("SELECT * FROM sprints WHERE workspace_id=? AND project_id=? ORDER BY created DESC",(wid(),project_id)).fetchall()
        else:
            rows = db.execute("SELECT * FROM sprints WHERE workspace_id=? ORDER BY created DESC",(wid(),)).fetchall()
        result = []
        for s in rows:
            sd = dict(s)
            tasks_in_sprint = db.execute("SELECT id,title,stage,story_points FROM tasks WHERE workspace_id=? AND sprint=?",(wid(),s["id"])).fetchall()
            sd["tasks"] = [dict(t) for t in tasks_in_sprint]
            sd["total_points"] = sum(t["story_points"] or 0 for t in tasks_in_sprint)
            sd["done_points"] = sum(t["story_points"] or 0 for t in tasks_in_sprint if t["stage"]=="completed")
            result.append(sd)
        return jsonify(result)

@app.route("/api/sprints", methods=["POST"])
@login_required
def create_sprint():
    d = request.json or {}
    if not d.get("name"): return jsonify({"error":"Name required"}),400
    with get_db() as db:
        sid = f"sp{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO sprints VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (sid,wid(),d.get("project_id",""),d["name"],d.get("goal",""),
                    "planning",d.get("start_date",""),d.get("end_date",""),0,ts()))
        return jsonify({"ok":True,"id":sid})

@app.route("/api/sprints/<sid>", methods=["PUT"])
@login_required
def update_sprint(sid):
    d = request.json or {}
    with get_db() as db:
        s = db.execute("SELECT * FROM sprints WHERE id=? AND workspace_id=?",(sid,wid())).fetchone()
        if not s: return jsonify({"error":"Not found"}),404
        db.execute("UPDATE sprints SET name=?,goal=?,status=?,start_date=?,end_date=? WHERE id=?",
                   (d.get("name",s["name"]),d.get("goal",s["goal"]),d.get("status",s["status"]),
                    d.get("start_date",s["start_date"]),d.get("end_date",s["end_date"]),sid))
        if d.get("task_ids"):
            db.execute("UPDATE tasks SET sprint='' WHERE workspace_id=? AND sprint=?",(wid(),sid))
            for task_id in d["task_ids"]:
                db.execute("UPDATE tasks SET sprint=? WHERE id=? AND workspace_id=?",(sid,task_id,wid()))
        return jsonify({"ok":True})

@app.route("/api/sprints/<sid>", methods=["DELETE"])
@login_required
def delete_sprint(sid):
    with get_db() as db:
        db.execute("UPDATE tasks SET sprint='' WHERE workspace_id=? AND sprint=?",(wid(),sid))
        db.execute("DELETE FROM sprints WHERE id=? AND workspace_id=?",(sid,wid()))
        return jsonify({"ok":True})

# ── Webhooks ──────────────────────────────────────────────────────────────────
def fire_webhooks(db, event, payload):
    try:
        hooks = db.execute("SELECT * FROM webhooks_config WHERE workspace_id=? AND active=1",(wid(),)).fetchall()
        for h in hooks:
            events = json.loads(h["events"] or "[]")
            if event not in events and "*" not in events: continue
            body = json.dumps({"event":event,"workspace_id":wid(),"data":payload}).encode()
            req = urllib.request.Request(h["url"],data=body,method="POST",
                headers={"Content-Type":"application/json","X-VEWIT-Event":event,"X-VEWIT-Secret":h["secret"] or ""})
            try:
                with urllib.request.urlopen(req,timeout=5) as r: pass
            except: pass
    except: pass

@app.route("/api/webhooks", methods=["GET"])
@login_required
def get_webhooks():
    with get_db() as db:
        rows = db.execute("SELECT id,name,url,events,active,created FROM webhooks_config WHERE workspace_id=?",(wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/webhooks", methods=["POST"])
@login_required
def create_webhook():
    d = request.json or {}
    if not d.get("url"): return jsonify({"error":"URL required"}),400
    with get_db() as db:
        wh_id = f"wh{int(__import__('time').time()*1000)}"
        secret = secrets.token_hex(16)
        db.execute("INSERT INTO webhooks_config VALUES (?,?,?,?,?,?,?,?)",
                   (wh_id,wid(),d.get("name","Webhook"),d["url"],
                    json.dumps(d.get("events",["*"])),secret,1,ts()))
        return jsonify({"ok":True,"id":wh_id,"secret":secret})

@app.route("/api/webhooks/<wh_id>", methods=["PUT"])
@login_required
def update_webhook(wh_id):
    d = request.json or {}
    with get_db() as db:
        h = db.execute("SELECT * FROM webhooks_config WHERE id=? AND workspace_id=?",(wh_id,wid())).fetchone()
        if not h: return jsonify({"error":"Not found"}),404
        db.execute("UPDATE webhooks_config SET name=?,url=?,events=?,active=? WHERE id=?",
                   (d.get("name",h["name"]),d.get("url",h["url"]),
                    json.dumps(d.get("events",json.loads(h["events"] or "[]"))),
                    int(d.get("active",h["active"])),wh_id))
        return jsonify({"ok":True})

@app.route("/api/webhooks/<wh_id>", methods=["DELETE"])
@login_required
def delete_webhook(wh_id):
    with get_db() as db:
        db.execute("DELETE FROM webhooks_config WHERE id=? AND workspace_id=?",(wh_id,wid()))
        return jsonify({"ok":True})

# ── API Keys ──────────────────────────────────────────────────────────────────
@app.route("/api/api-keys", methods=["GET"])
@login_required
def get_api_keys():
    with get_db() as db:
        rows = db.execute("SELECT id,name,key_prefix,scopes,last_used,created FROM api_keys WHERE workspace_id=? AND user_id=?",(wid(),session["user_id"])).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/api-keys", methods=["POST"])
@login_required
def create_api_key():
    d = request.json or {}
    raw_key = f"vwt_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    prefix = raw_key[:12]
    with get_db() as db:
        kid = f"ak{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO api_keys VALUES (?,?,?,?,?,?,?,?,?)",
                   (kid,wid(),session["user_id"],d.get("name","My API Key"),
                    key_hash,prefix,json.dumps(d.get("scopes",["read"])),None,ts()))
        return jsonify({"ok":True,"key":raw_key,"id":kid,"prefix":prefix})

@app.route("/api/api-keys/<kid>", methods=["DELETE"])
@login_required
def delete_api_key(kid):
    with get_db() as db:
        db.execute("DELETE FROM api_keys WHERE id=? AND workspace_id=? AND user_id=?",(kid,wid(),session["user_id"]))
        return jsonify({"ok":True})

# ── Audit Logs ────────────────────────────────────────────────────────────────
@app.route("/api/audit-logs", methods=["GET"])
@login_required
def get_audit_logs():
    with get_db() as db:
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not cu or cu["role"] not in ("Admin","Manager"): return jsonify({"error":"Forbidden"}),403
        rows = db.execute("SELECT al.*,u.name as user_name FROM audit_logs al LEFT JOIN users u ON al.user_id=u.id WHERE al.workspace_id=? ORDER BY al.created DESC LIMIT 100",(wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

# ── Guest access ──────────────────────────────────────────────────────────────
@app.route("/api/guests", methods=["POST"])
@login_required
def invite_guest():
    d = request.json or {}
    email = d.get("email","").strip().lower()
    if not email: return jsonify({"error":"Email required"}),400
    project_ids = d.get("project_ids",[])
    with get_db() as db:
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not cu or cu["role"] not in ("Admin","Manager"): return jsonify({"error":"Forbidden"}),403
        existing = db.execute("SELECT id FROM users WHERE email=? AND workspace_id=?",(email,wid())).fetchone()
        if existing: return jsonify({"error":"User already exists"}),400
        uid = f"g{int(__import__('time').time()*1000)}"
        raw_pw = secrets.token_hex(8)
        db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?)",
                   (uid,wid(),d.get("name",email.split("@")[0]),email,
                    hash_pw(raw_pw),"Viewer","G","#64748b",ts()))
        db.execute("UPDATE users SET is_guest=1,guest_projects=? WHERE id=?",(json.dumps(project_ids),uid))
        ws = db.execute("SELECT name FROM workspaces WHERE id=?",(wid(),)).fetchone()
        body = f"<p>You've been invited as a guest to <b>{ws['name'] if ws else 'VEWIT'}</b>.</p><p>Email: {email}<br>Password: {raw_pw}</p><p>Login at your VEWIT workspace.</p>"
        threading.Thread(target=send_email,args=(email,"Guest Invitation — VEWIT",body,wid()),daemon=True).start()
        return jsonify({"ok":True,"id":uid,"temp_password":raw_pw})

# ── Referral System ───────────────────────────────────────────────────────────
@app.route("/api/referral", methods=["GET"])
@login_required
def get_referral():
    with get_db() as db:
        ws = db.execute("SELECT referral_code FROM workspaces WHERE id=?",(wid(),)).fetchone()
        code = ws["referral_code"] if ws and ws["referral_code"] else ""
        if not code:
            code = secrets.token_hex(6).upper()
            db.execute("UPDATE workspaces SET referral_code=? WHERE id=?",(code,wid()))
        count = db.execute("SELECT COUNT(*) as cnt FROM referrals WHERE referrer_ws=?",(wid(),)).fetchone()
        return jsonify({"code":code,"referrals":count["cnt"] if count else 0})

@app.route("/api/referral/use", methods=["POST"])
def use_referral():
    d = request.json or {}
    code = d.get("code","").strip().upper()
    ws_id = d.get("workspace_id","")
    if not code or not ws_id: return jsonify({"error":"Missing params"}),400
    with get_db() as db:
        referrer = db.execute("SELECT id FROM workspaces WHERE referral_code=?",(code,)).fetchone()
        if not referrer: return jsonify({"error":"Invalid code"}),404
        if referrer["id"] == ws_id: return jsonify({"error":"Cannot self-refer"}),400
        existing = db.execute("SELECT id FROM referrals WHERE referred_ws=?",(ws_id,)).fetchone()
        if existing: return jsonify({"ok":True,"already":True})
        rid = f"ref{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO referrals VALUES (?,?,?,?)",(rid,referrer["id"],ws_id,ts()))
        return jsonify({"ok":True})

# ── Email Digest ──────────────────────────────────────────────────────────────
def _send_digest_for_workspace(ws):
    try:
        with get_db() as db:
            tasks = db.execute("SELECT * FROM tasks WHERE workspace_id=?",(ws["id"],)).fetchall()
            users = db.execute("SELECT * FROM users WHERE workspace_id=?",(ws["id"],)).fetchall()
            by_stage = {}
            for t in tasks:
                by_stage.setdefault(t["stage"],[]).append(t)
            rows_html = ""
            for stage, items in by_stage.items():
                rows_html += f"<tr><td style='padding:8px;border-bottom:1px solid #e2e8f0'><b>{stage.title()}</b></td><td style='padding:8px;border-bottom:1px solid #e2e8f0'>{len(items)}</td></tr>"
            overdue = [t for t in tasks if t.get("due") and t["due"]<datetime.utcnow().strftime("%Y-%m-%d") and t.get("stage")!="completed"]
            overdue_html = "".join(f"<li>{t['title']} (due {t['due']})</li>" for t in overdue[:5])
            body = f"""<h2>VEWIT Daily Digest — {ws['name']}</h2>
            <h3>Task Summary</h3><table border='0' cellpadding='0' cellspacing='0'>{rows_html}</table>
            {"<h3>⚠️ Overdue Tasks</h3><ul>"+overdue_html+"</ul>" if overdue else ""}
            <p><small>Unsubscribe in Workspace Settings → Digest.</small></p>"""
            for u in users:
                if u.get("email"):
                    send_email(u["email"],f"Daily Digest — {ws['name']}",body,ws["id"])
    except Exception as e:
        print(f"Digest error for {ws.get('id')}: {e}")

def _run_digest():
    try:
        with get_db() as db:
            workspaces = db.execute("SELECT * FROM workspaces WHERE digest_enabled=1").fetchall()
            for ws in workspaces:
                threading.Thread(target=_send_digest_for_workspace,args=(dict(ws),),daemon=True).start()
    except Exception as e:
        print(f"Digest runner error: {e}")


# ── Announcements ────────────────────────────────────────────────────────────────────────────
@app.route("/api/announcements", methods=["GET"])
@login_required
def get_announcements():
    with get_db() as db:
        rows = db.execute(
            "SELECT a.*,u.name as author_name FROM announcements a LEFT JOIN users u ON a.author=u.id "
            "WHERE a.workspace_id=? ORDER BY a.pinned DESC, a.created DESC",(wid(),)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            read = db.execute("SELECT id FROM announcement_reads WHERE announcement_id=? AND user_id=?",
                              (r["id"],session["user_id"])).fetchone()
            d["read"] = bool(read)
            result.append(d)
        return jsonify(result)

@app.route("/api/announcements", methods=["POST"])
@login_required
def create_announcement():
    d = request.json or {}
    with get_db() as db:
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not cu or cu["role"] not in ("Admin","Manager"): return jsonify({"error":"Forbidden"}),403
        aid = f"ann{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO announcements VALUES (?,?,?,?,?,?,?,?)",
                   (aid,wid(),d.get("title",""),d.get("content",""),
                    session["user_id"],int(d.get("pinned",0)),ts(),d.get("expires","")))
        users = db.execute("SELECT id FROM users WHERE workspace_id=?",(wid(),)).fetchall()
        for u in users:
            if u["id"] == session["user_id"]: continue
            nid = f"n{int(__import__('time').time()*1000)}{secrets.token_hex(2)}"
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"announcement",f"Announcement: {d.get('title','')}",u["id"],0,ts()))
        return jsonify({"ok":True,"id":aid})

@app.route("/api/announcements/<aid>/read", methods=["POST"])
@login_required
def mark_announcement_read(aid):
    with get_db() as db:
        existing = db.execute("SELECT id FROM announcement_reads WHERE announcement_id=? AND user_id=?",
                              (aid,session["user_id"])).fetchone()
        if not existing:
            rid = f"ar{int(__import__('time').time()*1000)}"
            db.execute("INSERT INTO announcement_reads VALUES (?,?,?,?)",(rid,aid,session["user_id"],ts()))
        return jsonify({"ok":True})

@app.route("/api/announcements/<aid>", methods=["DELETE"])
@login_required
def delete_announcement(aid):
    with get_db() as db:
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not cu or cu["role"] not in ("Admin","Manager"): return jsonify({"error":"Forbidden"}),403
        db.execute("DELETE FROM announcement_reads WHERE announcement_id=?",(aid,))
        db.execute("DELETE FROM announcements WHERE id=? AND workspace_id=?",(aid,wid()))
        return jsonify({"ok":True})

# ── Message Reactions ────────────────────────────────────────────────────────────────────────────
@app.route("/api/reactions/<msg_type>/<msg_id>", methods=["GET"])
@login_required
def get_reactions(msg_type, msg_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT emoji, user_id FROM message_reactions WHERE workspace_id=? AND message_id=? AND message_type=?",
            (wid(),msg_id,msg_type)).fetchall()
        grouped = {}
        for r in rows:
            if r["emoji"] not in grouped: grouped[r["emoji"]] = []
            grouped[r["emoji"]].append(r["user_id"])
        return jsonify(grouped)

@app.route("/api/reactions/<msg_type>/<msg_id>", methods=["POST"])
@login_required
def toggle_reaction(msg_type, msg_id):
    d = request.json or {}
    emoji = d.get("emoji","like")
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM message_reactions WHERE workspace_id=? AND message_id=? AND message_type=? AND user_id=? AND emoji=?",
            (wid(),msg_id,msg_type,session["user_id"],emoji)).fetchone()
        if existing:
            db.execute("DELETE FROM message_reactions WHERE id=?",(existing["id"],))
            return jsonify({"ok":True,"action":"removed"})
        rid = f"r{int(__import__('time').time()*1000)}{secrets.token_hex(2)}"
        db.execute("INSERT INTO message_reactions VALUES (?,?,?,?,?,?,?)",
                   (rid,wid(),msg_id,msg_type,session["user_id"],emoji,ts()))
        return jsonify({"ok":True,"action":"added"})

# ── Message Threads ────────────────────────────────────────────────────────────────────────────
@app.route("/api/messages/<mid>/thread", methods=["GET"])
@login_required
def get_thread(mid):
    with get_db() as db:
        rows = db.execute(
            "SELECT t.*,u.name as sender_name FROM message_threads t LEFT JOIN users u ON t.sender=u.id "
            "WHERE t.workspace_id=? AND t.parent_id=? ORDER BY t.ts",(wid(),mid)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/messages/<mid>/thread", methods=["POST"])
@login_required
def post_thread_reply(mid):
    d = request.json or {}
    content = d.get("content","").strip()
    if not content: return jsonify({"error":"Empty"}),400
    with get_db() as db:
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if cu and cu["role"] == "Viewer": return jsonify({"error":"Viewers cannot post"}),403
        tid = f"th{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO message_threads VALUES (?,?,?,?,?,?)",
                   (tid,wid(),mid,session["user_id"],content,ts()))
        return jsonify({"ok":True,"id":tid})

# ── AI Daily Standup ────────────────────────────────────────────────────────────────────────────
@app.route("/api/ai/standup", methods=["POST"])
@login_required
def ai_standup():
    d = request.json or {}
    target_user_id = d.get("user_id", session["user_id"])
    with get_db() as db:
        ws = db.execute("SELECT * FROM workspaces WHERE id=?",(wid(),)).fetchone()
        api_key = (ws["ai_api_key"] if ws and ws["ai_api_key"] else "").strip()
        if not api_key: return jsonify({"error":"NO_KEY"}),400
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        cu_role = cu["role"] if cu else "Viewer"
        if cu_role in ("Developer","Tester") and target_user_id != session["user_id"]:
            return jsonify({"error":"Forbidden"}),403
        target_user = db.execute("SELECT name FROM users WHERE id=? AND workspace_id=?",(target_user_id,wid())).fetchone()
        if not target_user: return jsonify({"error":"User not found"}),404
        today_str = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
        yesterday = (__import__("datetime").datetime.utcnow() - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
        tasks = db.execute(
            "SELECT title,stage,priority,due,pct FROM tasks WHERE workspace_id=? AND assignee=? ORDER BY created DESC LIMIT 20",
            (wid(),target_user_id)).fetchall()
        time_logs = db.execute(
            "SELECT tl.description,tl.minutes,t.title as task_title FROM time_logs tl LEFT JOIN tasks t ON tl.task_id=t.id "
            "WHERE tl.workspace_id=? AND tl.user_id=? AND tl.logged_date>=? ORDER BY tl.created DESC LIMIT 10",
            (wid(),target_user_id,yesterday)).fetchall()
        task_ctx = "\n".join([f"- [{t['stage']}] {t['title']} ({t['pct']}% done)" for t in tasks])
        time_ctx = "\n".join([f"- {l['task_title']}: {l['minutes']}min" for l in time_logs]) or "No time logged recently"
        prompt = f"Generate a daily standup for {target_user['name']}. Tasks: {task_ctx or 'None'}. Recent time logs: {time_ctx}. Today: {today_str}. Format: 3 sections: What I did yesterday, What I am doing today, Blockers. Be concise, bullet-pointed."
    try:
        req_data = json.dumps({"model":"claude-sonnet-4-5","max_tokens":500,"messages":[{"role":"user","content":prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",data=req_data,method="POST",
            headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"})
        with urllib.request.urlopen(req,timeout=30) as resp:
            result = json.loads(resp.read().decode())
            report = result["content"][0]["text"]
    except Exception as e:
        return jsonify({"error":str(e)}),500
    with get_db() as db:
        sid = f"sr{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO standup_reports VALUES (?,?,?,?,?,?)",
                   (sid,wid(),target_user_id,today_str,report,ts()))
    return jsonify({"ok":True,"report":report,"user":target_user["name"]})

# ── AI Code Review ────────────────────────────────────────────────────────────────────────────
@app.route("/api/ai/code-review", methods=["POST"])
@login_required
def ai_code_review():
    d = request.json or {}
    diff = d.get("diff","").strip()
    context = d.get("context","")
    if not diff: return jsonify({"error":"No diff provided"}),400
    with get_db() as db:
        ws = db.execute("SELECT ai_api_key FROM workspaces WHERE id=?",(wid(),)).fetchone()
        api_key = (ws["ai_api_key"] if ws and ws["ai_api_key"] else "").strip()
        if not api_key: return jsonify({"error":"NO_KEY"}),400
    system = f"You are a senior code reviewer. Review this diff and give structured feedback with: Summary, Issues Found (Critical/Major/Minor), Suggestions, Security Notes, and a Verdict (Approve/Request Changes/Reject). Context: {context or 'None'}"
    try:
        req_data = json.dumps({"model":"claude-sonnet-4-5","max_tokens":1500,"system":system,
            "messages":[{"role":"user","content":f"Review this diff:\n```\n{diff[:6000]}\n```"}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",data=req_data,method="POST",
            headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"})
        with urllib.request.urlopen(req,timeout=45) as resp:
            result = json.loads(resp.read().decode())
            review = result["content"][0]["text"]
    except Exception as e:
        return jsonify({"error":str(e)}),500
    with get_db() as db:
        rid = f"cr{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO code_reviews VALUES (?,?,?,?,?,?,?,?)",
                   (rid,wid(),d.get("task_id",""),d.get("ticket_id",""),diff[:6000],review,session["user_id"],ts()))
    return jsonify({"ok":True,"review":review,"id":rid})

# ── AI Risk ────────────────────────────────────────────────────────────────────────────
@app.route("/api/ai/risk", methods=["GET"])
@login_required
def ai_risk():
    with get_db() as db:
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not cu or cu["role"] not in ("Admin","Manager","TeamLead"):
            return jsonify({"error":"Forbidden"}),403
        ws = db.execute("SELECT ai_api_key FROM workspaces WHERE id=?",(wid(),)).fetchone()
        api_key = (ws["ai_api_key"] if ws and ws["ai_api_key"] else "").strip()
        if not api_key: return jsonify({"error":"NO_KEY"}),400
        projects = db.execute("SELECT * FROM projects WHERE workspace_id=?",(wid(),)).fetchall()
        tasks = db.execute("SELECT * FROM tasks WHERE workspace_id=?",(wid(),)).fetchall()
        today = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
    summaries = []
    for p in projects:
        ptasks = [t for t in tasks if t["project"] == p["id"]]
        overdue = len([t for t in ptasks if t.get("due","") and t["due"] < today and t["stage"] != "completed"])
        blocked = len([t for t in ptasks if t["stage"] == "blocked"])
        pct_done = round(sum(t["pct"] or 0 for t in ptasks) / max(len(ptasks),1))
        summaries.append(f"Project '{p['name']}': {len(ptasks)} tasks, {overdue} overdue, {blocked} blocked, {pct_done}% avg done, deadline: {p.get('target_date','unknown')}")
    prompt = "Analyze these projects for risk. For each: PROJECT name, RISK level (LOW/MEDIUM/HIGH/CRITICAL), REASON (one sentence), ACTIONS (2-3 bullets). Today: " + today + ". Projects:\n" + "\n".join(summaries)
    try:
        req_data = json.dumps({"model":"claude-sonnet-4-5","max_tokens":1500,"messages":[{"role":"user","content":prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",data=req_data,method="POST",
            headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"})
        with urllib.request.urlopen(req,timeout=45) as resp:
            result = json.loads(resp.read().decode())
            analysis = result["content"][0]["text"]
    except Exception as e:
        return jsonify({"error":str(e)}),500
    return jsonify({"ok":True,"analysis":analysis})

# ── Intake Forms ────────────────────────────────────────────────────────────────────────────
@app.route("/api/forms", methods=["GET"])
@login_required
def get_forms():
    with get_db() as db:
        rows = db.execute("SELECT * FROM intake_forms WHERE workspace_id=? ORDER BY created DESC",(wid(),)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/forms", methods=["POST"])
@login_required
def create_form():
    d = request.json or {}
    with get_db() as db:
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not cu or cu["role"] not in ("Admin","Manager","TeamLead"): return jsonify({"error":"Forbidden"}),403
        fid = f"frm{int(__import__('time').time()*1000)}"
        db.execute("INSERT INTO intake_forms VALUES (?,?,?,?,?,?,?,?,?)",
                   (fid,wid(),d.get("title",""),d.get("description",""),
                    d.get("project_id",""),json.dumps(d.get("fields",[])),1,session["user_id"],ts()))
        return jsonify({"ok":True,"id":fid,"url":f"/form/{fid}"})

@app.route("/api/forms/<fid>", methods=["PUT"])
@login_required
def update_form(fid):
    d = request.json or {}
    with get_db() as db:
        f = db.execute("SELECT * FROM intake_forms WHERE id=? AND workspace_id=?",(fid,wid())).fetchone()
        if not f: return jsonify({"error":"Not found"}),404
        db.execute("UPDATE intake_forms SET title=?,description=?,fields=?,project_id=?,active=? WHERE id=?",
                   (d.get("title",f["title"]),d.get("description",f["description"]),
                    json.dumps(d.get("fields",json.loads(f["fields"] or "[]"))),
                    d.get("project_id",f["project_id"]),int(d.get("active",f["active"])),fid))
        return jsonify({"ok":True})

@app.route("/api/forms/<fid>", methods=["DELETE"])
@login_required
def delete_form(fid):
    with get_db() as db:
        db.execute("DELETE FROM intake_forms WHERE id=? AND workspace_id=?",(fid,wid()))
        return jsonify({"ok":True})

@app.route("/form/<fid>")
def public_form(fid):
    with get_db() as db:
        f = db.execute("SELECT * FROM intake_forms WHERE id=? AND active=1",(fid,)).fetchone()
        if not f: return "Form not found or inactive",404
        ws = db.execute("SELECT name FROM workspaces WHERE id=?",(f["workspace_id"],)).fetchone()
        ws_name = ws["name"] if ws else "VEWIT"
        fields_html = ""
        for field in json.loads(f["fields"] or "[]"):
            ft = field.get("type","text"); fn = field.get("name",""); flbl = field.get("label",fn)
            req = "required" if field.get("required") else ""
            star = "*" if req else ""
            if ft == "textarea":
                fields_html += f'<div class="fg"><label>{flbl}{star}</label><textarea name="{fn}" {req} rows="4"></textarea></div>'
            elif ft == "select":
                opts = "".join(f'<option value="{o}">{o}</option>' for o in field.get("options",[]))
                fields_html += f'<div class="fg"><label>{flbl}</label><select name="{fn}" {req}><option value="">Select...</option>{opts}</select></div>'
            else:
                fields_html += f'<div class="fg"><label>{flbl}{star}</label><input type="{ft}" name="{fn}" {req}/></div>'
    css = """*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,sans-serif;background:#f8fafc;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}.card{background:#fff;border-radius:16px;padding:36px;width:100%;max-width:560px;box-shadow:0 4px 24px rgba(0,0,0,.08)}h1{font-size:22px;font-weight:700;color:#0f172a;margin-bottom:6px}p{font-size:14px;color:#64748b;margin-bottom:24px;line-height:1.6}.fg{margin-bottom:16px}label{font-size:13px;font-weight:600;color:#374151;display:block;margin-bottom:5px}input,textarea,select{width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;color:#1e293b;outline:none;font-family:inherit}input:focus,textarea:focus,select:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.1)}button{width:100%;padding:12px;background:#1d4ed8;color:#fff;border:none;border-radius:9px;font-size:15px;font-weight:600;cursor:pointer;margin-top:8px}button:hover{background:#1e40af}.badge{display:inline-block;padding:2px 10px;background:#eff6ff;color:#1d4ed8;border-radius:99px;font-size:12px;font-weight:600;margin-bottom:16px}"""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{f["title"]}</title><style>{css}</style></head><body><div class="card"><div class="badge">{ws_name}</div><h1>{f["title"]}</h1><p>{f["description"] or "Fill out the form below."}</p><div id="fw"><form id="if">{fields_html}<div class="fg"><label>Your email (optional)</label><input type="email" name="_email" placeholder="your@email.com"/></div><button type="submit">Submit</button></form></div></div><script>document.getElementById("if").onsubmit=async(e)=>{{e.preventDefault();const data={{}};new FormData(e.target).forEach((v,k)=>data[k]=v);const r=await fetch("/api/forms/{fid}/submit",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(data)}});if(r.ok)document.getElementById("fw").innerHTML='<div style="text-align:center;padding:32px"><h2 style="color:#15803d">Submitted!</h2><p>Your response has been received.</p></div>'}};</script></body></html>"""

@app.route("/api/forms/<fid>/submit", methods=["POST"])
def submit_form(fid):
    data = request.json or {}
    with get_db() as db:
        f = db.execute("SELECT * FROM intake_forms WHERE id=? AND active=1",(fid,)).fetchone()
        if not f: return jsonify({"error":"Form not found"}),404
        sid = f"sub{int(__import__('time').time()*1000)}"
        submitter_email = data.pop("_email","")
        ticket_title = f"[Form] {f['title']} submission"
        ticket_body = "\n".join([f"**{k}**: {v}" for k,v in data.items()])
        tid = f"TK-{sid[-8:]}"
        db.execute("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (tid,f["workspace_id"],ticket_title,ticket_body,
                    "task","medium","open","",submitter_email,f["project_id"],"[]",ts(),ts(),f["workspace_id"]))
        db.execute("INSERT INTO intake_submissions VALUES (?,?,?,?,?,?,?)",
                   (sid,fid,f["workspace_id"],json.dumps(data),tid,submitter_email,ts()))
        return jsonify({"ok":True})

@app.route("/api/forms/<fid>/submissions", methods=["GET"])
@login_required
def get_form_submissions(fid):
    with get_db() as db:
        rows = db.execute("SELECT * FROM intake_submissions WHERE form_id=? AND workspace_id=? ORDER BY created DESC",(fid,wid())).fetchall()
        return jsonify([dict(r) for r in rows])

# ── TOTP 2FA — Pure Python (no pyotp needed) ──────────────────────────────────────────
import hmac as _hmac, hashlib as _hashlib, struct as _struct, base64 as _base64

def _totp_generate(secret_b32, window=0):
    """Pure-Python TOTP — RFC 6238 / RFC 4226. No external deps."""
    import time as _t
    try:
        key = _base64.b32decode(secret_b32.upper() + '=' * ((8 - len(secret_b32) % 8) % 8))
    except Exception:
        return None
    t = int(_t.time()) // 30 + window
    msg = _struct.pack('>Q', t)
    h = _hmac.new(key, msg, _hashlib.sha1).digest()
    offset = h[-1] & 0x0f
    code = _struct.unpack('>I', h[offset:offset+4])[0] & 0x7fffffff
    return str(code % 1000000).zfill(6)

def _totp_verify(secret_b32, code):
    """Verify with ±1 window tolerance."""
    code = str(code).strip()
    for w in [-1, 0, 1]:
        if _totp_generate(secret_b32, w) == code:
            return True
    return False

def _b32_secret():
    raw = secrets.token_bytes(20)
    return _base64.b32encode(raw).decode().rstrip('=')

@app.route("/api/totp/setup", methods=["POST"])
@login_required
def totp_setup():
    with get_db() as db:
        existing = db.execute("SELECT * FROM totp_secrets WHERE user_id=?",(session["user_id"],)).fetchone()
        if existing and existing["enabled"]: return jsonify({"error":"2FA already enabled"}),400
        secret = _b32_secret()
        user = db.execute("SELECT email,name FROM users WHERE id=?",(session["user_id"],)).fetchone()
        email = user["email"] if user else "user"
        uri = f"otpauth://totp/VEWIT:{email}?secret={secret}&issuer=VEWIT&algorithm=SHA1&digits=6&period=30"
        backup_codes = [secrets.token_hex(4).upper() for _ in range(8)]
        tid = f"totp{int(__import__('time').time()*1000)}"
        if existing:
            db.execute("UPDATE totp_secrets SET secret=?,backup_codes=?,enabled=0 WHERE user_id=?",
                      (secret,json.dumps(backup_codes),session["user_id"]))
        else:
            db.execute("INSERT INTO totp_secrets VALUES (?,?,?,?,?,?)",
                      (tid,session["user_id"],secret,0,json.dumps(backup_codes),ts()))
        return jsonify({"ok":True,"secret":secret,"uri":uri,"backup_codes":backup_codes})

@app.route("/api/totp/verify", methods=["POST"])
@login_required
def totp_verify():
    d = request.json or {}
    code = d.get("code","").strip()
    with get_db() as db:
        rec = db.execute("SELECT * FROM totp_secrets WHERE user_id=?",(session["user_id"],)).fetchone()
        if not rec: return jsonify({"error":"2FA not set up"}),400
        if _totp_verify(rec["secret"], code):
            db.execute("UPDATE totp_secrets SET enabled=1 WHERE user_id=?",(session["user_id"],))
            return jsonify({"ok":True,"message":"2FA enabled successfully"})
        backup = json.loads(rec["backup_codes"] or "[]")
        if code.upper() in backup:
            backup.remove(code.upper())
            db.execute("UPDATE totp_secrets SET backup_codes=? WHERE user_id=?",(json.dumps(backup),session["user_id"]))
            return jsonify({"ok":True,"message":"Backup code used","remaining":len(backup)})
        return jsonify({"error":"Invalid code — please try again"}),400

@app.route("/api/totp/disable", methods=["POST"])
@login_required
def totp_disable():
    with get_db() as db:
        db.execute("UPDATE totp_secrets SET enabled=0 WHERE user_id=?",(session["user_id"],))
        return jsonify({"ok":True})

@app.route("/api/totp/status", methods=["GET"])
@login_required
def totp_status():
    with get_db() as db:
        rec = db.execute("SELECT enabled FROM totp_secrets WHERE user_id=?",(session["user_id"],)).fetchone()
        return jsonify({"enabled":bool(rec and rec["enabled"])})

# ── Time Report ────────────────────────────────────────────────────────────────────────────
@app.route("/api/reports/time", methods=["GET"])
@login_required
def time_report():
    period = request.args.get("period","week")
    user_filter = request.args.get("user_id","")
    with get_db() as db:
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if cu and cu["role"] in ("Developer","Tester"): user_filter = session["user_id"]
        now = __import__("datetime").datetime.utcnow()
        if period == "week": start = (now - __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%d")
        elif period == "month": start = now.replace(day=1).strftime("%Y-%m-%d")
        else: start = (now - __import__("datetime").timedelta(days=90)).strftime("%Y-%m-%d")
        q = "SELECT tl.*,u.name as user_name,t.title as task_title,p.name as project_name FROM time_logs tl LEFT JOIN users u ON tl.user_id=u.id LEFT JOIN tasks t ON tl.task_id=t.id LEFT JOIN projects p ON t.project=p.id WHERE tl.workspace_id=? AND tl.logged_date>=?"
        params = [wid(), start]
        if user_filter: q += " AND tl.user_id=?"; params.append(user_filter)
        rows = db.execute(q + " ORDER BY tl.logged_date DESC", params).fetchall()
        return jsonify([dict(r) for r in rows])



# ── Mentions ──────────────────────────────────────────────────────────────────
@app.route("/api/mentions", methods=["GET"])
@login_required
def get_mentions():
    with get_db() as db:
        rows = db.execute(
            "SELECT n.*,u.name as sender_name FROM notifications n LEFT JOIN users u ON n.sender_id=u.id "
            "WHERE n.workspace_id=? AND n.user_id=? AND n.type='mention' ORDER BY n.ts DESC LIMIT 50",
            (wid(),session["user_id"])).fetchall()
        return jsonify([dict(r) for r in rows])


# ── Pinned Messages ───────────────────────────────────────────────────────────
@app.route("/api/messages/<mid>/pin", methods=["POST"])
@login_required
def pin_message(mid):
    with get_db() as db:
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not cu or cu["role"] not in ("Admin","Manager","TeamLead"):
            return jsonify({"error":"Forbidden"}),403
        try: db.execute("ALTER TABLE messages ADD COLUMN pinned INTEGER DEFAULT 0")
        except: pass
        db.execute("UPDATE messages SET pinned=1 WHERE id=? AND workspace_id=?",(mid,wid()))
        return jsonify({"ok":True})

@app.route("/api/messages/<mid>/unpin", methods=["POST"])
@login_required
def unpin_message(mid):
    with get_db() as db:
        db.execute("UPDATE messages SET pinned=0 WHERE id=? AND workspace_id=?",(mid,wid()))
        return jsonify({"ok":True})

@app.route("/api/projects/<pid>/pinned-messages", methods=["GET"])
@login_required
def get_pinned_messages(pid):
    with get_db() as db:
        try: db.execute("ALTER TABLE messages ADD COLUMN pinned INTEGER DEFAULT 0")
        except: pass
        rows = db.execute(
            "SELECT m.*,u.name as sender_name FROM messages m LEFT JOIN users u ON m.sender=u.id "
            "WHERE m.workspace_id=? AND m.project=? AND m.pinned=1 ORDER BY m.ts DESC",
            (wid(),pid)).fetchall()
        return jsonify([dict(r) for r in rows])



# ── Dashboard summary — fast counts, no full data loads ──────────────────────
@app.route("/api/dashboard/summary")
@login_required
def dashboard_summary():
    """Returns counts and lightweight summary — frontend uses this for Dashboard view."""
    team_id = request.args.get("team_id","")
    with get_db() as db:
        ws_id = wid()
        uid   = session["user_id"]
        today = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")

        if team_id:
            # Get team project IDs and member IDs
            team = db.execute("SELECT member_ids FROM teams WHERE id=? AND workspace_id=?",(team_id,ws_id)).fetchone()
            member_ids = json.loads(team["member_ids"] if team else "[]")
            proj_rows  = db.execute("SELECT id FROM projects WHERE workspace_id=? AND team_id=?",(ws_id,team_id)).fetchall()
            proj_ids   = [p["id"] for p in proj_rows]
            task_count = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND team_id=?",(ws_id,team_id)).fetchone()["cnt"]
            active_count = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND team_id=? AND stage NOT IN ('completed','backlog')",(ws_id,team_id)).fetchone()["cnt"]
            done_count = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND team_id=? AND stage='completed'",(ws_id,team_id)).fetchone()["cnt"]
            blocked_count = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND team_id=? AND stage='blocked'",(ws_id,team_id)).fetchone()["cnt"]
            overdue_count = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND team_id=? AND due<? AND stage!='completed'",(ws_id,team_id,today)).fetchone()["cnt"]
        else:
            task_count    = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=?",(ws_id,)).fetchone()["cnt"]
            active_count  = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND stage NOT IN ('completed','backlog')",(ws_id,)).fetchone()["cnt"]
            done_count    = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND stage='completed'",(ws_id,)).fetchone()["cnt"]
            blocked_count = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND stage='blocked'",(ws_id,)).fetchone()["cnt"]
            overdue_count = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND due<? AND stage!='completed'",(ws_id,today)).fetchone()["cnt"]

        proj_count   = db.execute("SELECT COUNT(*) as cnt FROM projects WHERE workspace_id=?",(ws_id,)).fetchone()["cnt"]
        member_count = db.execute("SELECT COUNT(*) as cnt FROM users WHERE workspace_id=?",(ws_id,)).fetchone()["cnt"]
        open_tickets = db.execute("SELECT COUNT(*) as cnt FROM tickets WHERE workspace_id=? AND status='open'",(ws_id,)).fetchone()["cnt"]
        my_tasks     = db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND assignee=? AND stage!='completed'",(ws_id,uid)).fetchone()["cnt"]
        my_tickets   = db.execute("SELECT COUNT(*) as cnt FROM tickets WHERE workspace_id=? AND assignee=? AND status NOT IN ('closed','resolved')",(ws_id,uid)).fetchone()["cnt"]
        unread_notifs = db.execute("SELECT COUNT(*) as cnt FROM notifications WHERE workspace_id=? AND user_id=? AND read=0",(ws_id,uid)).fetchone()["cnt"]

        # Recent activity - last 5 completed tasks
        recent = db.execute(
            "SELECT t.id,t.title,t.stage,t.priority,u.name as assignee_name "
            "FROM tasks t LEFT JOIN users u ON t.assignee=u.id "
            "WHERE t.workspace_id=? ORDER BY t.created DESC LIMIT 8",(ws_id,)).fetchall()

        # Priority breakdown
        priority_counts = {p:db.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE workspace_id=? AND priority=? AND stage!='completed'",(ws_id,p)).fetchone()["cnt"]
            for p in ["critical","high","medium","low"]}

        return jsonify({
            "tasks":       {"total":task_count,"active":active_count,"done":done_count,"blocked":blocked_count,"overdue":overdue_count},
            "projects":    proj_count,
            "members":     member_count,
            "tickets":     {"open":open_tickets},
            "my":          {"tasks":my_tasks,"tickets":my_tickets},
            "unread":      unread_notifs,
            "priority":    priority_counts,
            "recent":      [dict(r) for r in recent],
        })

# ── Lightweight poll endpoint — only fetch what changed ──────────────────────
@app.route("/api/poll")
@login_required
def poll():
    """Lightweight polling — returns only unread notification count + DM count.
    Frontend polls this every 15s instead of re-fetching all data."""
    with get_db() as db:
        uid = session["user_id"]
        ws  = wid()
        unread_notifs = db.execute(
            "SELECT COUNT(*) as cnt FROM notifications WHERE workspace_id=? AND user_id=? AND read=0",(ws,uid)).fetchone()["cnt"]
        unread_dm = db.execute(
            "SELECT sender, COUNT(*) as cnt FROM direct_messages WHERE workspace_id=? AND recipient=? AND read=0 GROUP BY sender",(ws,uid)).fetchall()
        return jsonify({
            "notif_count": unread_notifs,
            "dm_unread":   [{"sender":r["sender"],"cnt":r["cnt"]} for r in unread_dm],
            "ts":          __import__("time").time()
        })


# ── Budget Tracking ───────────────────────────────────────────────────────────
@app.route("/api/projects/<pid>/budget", methods=["GET"])
@login_required
def get_project_budget(pid):
    with get_db() as db:
        p = db.execute("SELECT budget,budget_spent FROM projects WHERE id=? AND workspace_id=?",(pid,wid())).fetchone()
        if not p: return jsonify({"error":"Not found"}),404
        logs = db.execute("SELECT * FROM time_logs WHERE workspace_id=? AND task_id IN (SELECT id FROM tasks WHERE project=? AND workspace_id=?)",(wid(),pid,wid())).fetchall()
        return jsonify({"budget":p["budget"] or 0,"budget_spent":p["budget_spent"] or 0,"time_logged_minutes":sum(l["minutes"] for l in logs)})

@app.route("/api/projects/<pid>/budget", methods=["PUT"])
@login_required
def update_project_budget(pid):
    d = request.json or {}
    with get_db() as db:
        db.execute("UPDATE projects SET budget=?,budget_spent=? WHERE id=? AND workspace_id=?",
                   (d.get("budget",0),d.get("budget_spent",0),pid,wid()))
        return jsonify({"ok":True})

# ── Public status page ────────────────────────────────────────────────────────
@app.route("/status/<invite_code>")
def public_status(invite_code):
    with get_db() as db:
        ws = db.execute("SELECT * FROM workspaces WHERE invite_code=?",(invite_code,)).fetchone()
        if not ws: return "Workspace not found",404
        projects = db.execute("SELECT id,name,color,progress FROM projects WHERE workspace_id=?",(ws["id"],)).fetchall()
        tasks = db.execute("SELECT stage,COUNT(*) as cnt FROM tasks WHERE workspace_id=? GROUP BY stage",(ws["id"],)).fetchall()
        stage_counts = {t["stage"]:t["cnt"] for t in tasks}
        proj_html = "".join(f"<div style='padding:12px;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:8px'><div style='display:flex;justify-content:space-between'><b>{p['name']}</b><span>{p['progress']}%</span></div><div style='height:6px;background:#e2e8f0;border-radius:3px;margin-top:6px'><div style='height:6px;background:{p['color'] or '#3b82f6'};border-radius:3px;width:{p['progress']}%'></div></div></div>" for p in projects)
        stage_html = "".join(f"<span style='padding:4px 10px;background:#f1f5f9;border-radius:99px;font-size:13px;margin-right:6px'>{s}: <b>{c}</b></span>" for s,c in stage_counts.items())
        return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>{ws['name']} — Status</title>
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#1e293b}}</style></head>
        <body><h1>🟢 {ws['name']} — Project Status</h1><p style="color:#64748b">Live status page · Updated in real time</p>
        <h2>Tasks by Stage</h2><div style="margin-bottom:20px">{stage_html}</div>
        <h2>Projects</h2>{proj_html or '<p>No projects yet.</p>'}
        </body></html>"""

# ── White-label settings ──────────────────────────────────────────────────────
@app.route("/api/workspace/white-label", methods=["PUT"])
@login_required
def update_white_label():
    d = request.json or {}
    with get_db() as db:
        cu = db.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if not cu or cu["role"] not in ("Admin",): return jsonify({"error":"Forbidden"}),403
        db.execute("UPDATE workspaces SET white_label_name=?,white_label_logo=? WHERE id=?",
                   (d.get("name",""),d.get("logo",""),wid()))
        return jsonify({"ok":True})


# ── Export ────────────────────────────────────────────────────────────────────
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
                             json.dumps([session["user_id"]]), "", "", 0, "#aaff00", ts())
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
    try:
        with get_db() as db: db.execute("SELECT 1")
        return jsonify({"status":"ok"}), 200
    except Exception as e:
        return jsonify({"status":"error","detail":str(e)}), 500

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


@app.route("/about")
def about_page():
    """Public About page — indexed by Google, no login required."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>About VEWIT — AI-Powered Team Collaboration Platform</title>
<link rel="icon" type="image/png" href="/icon-192.png"/>
<link rel="shortcut icon" href="/favicon.ico"/>
<meta name="description" content="VEWIT is an AI-powered team collaboration platform for project management, task tracking, direct messaging, support tickets, timeline tracking and developer productivity analytics."/>
<meta name="keywords" content="VEWIT, team collaboration, project management, task management, AI assistant, direct messages, developer productivity, support tickets"/>
<meta name="robots" content="index, follow"/>
<link rel="canonical" href="https://www.vewit.in/about"/>
<meta property="og:title" content="About VEWIT — AI-Powered Team Collaboration"/>
<meta property="og:description" content="VEWIT is an AI-powered team collaboration platform. Manage projects, tasks, direct messages, tickets and analytics all in one place."/>
<meta property="og:url" content="https://www.vewit.in/about"/>
<meta property="og:type" content="website"/>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization",
"name":"VEWIT","url":"https://www.vewit.in","description":"AI-powered team collaboration platform for project management, task tracking, direct messaging, support tickets, timeline tracking and developer productivity analytics.",
"foundingDate":"2024","applicationCategory":"BusinessApplication",
"sameAs":["https://www.vewit.in"]}
</script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1e293b;background:#fff;line-height:1.6}
.nav{background:#0f172a;padding:16px 40px;display:flex;align-items:center;justify-content:space-between}
.nav-logo{color:#fff;font-size:20px;font-weight:800;letter-spacing:-0.5px;text-decoration:none}
.nav-logo span{color:#2563eb}
.nav-cta{background:#2563eb;color:#fff;padding:9px 22px;border-radius:8px;text-decoration:none;font-size:13px;font-weight:700}
.hero{background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);color:#fff;padding:80px 40px;text-align:center}
.hero h1{font-size:clamp(28px,5vw,52px);font-weight:800;letter-spacing:-1px;margin-bottom:20px;line-height:1.15}
.hero h1 span{color:#60a5fa}
.hero p{font-size:18px;color:#94a3b8;max-width:640px;margin:0 auto 36px}
.hero-cta{display:inline-block;background:#2563eb;color:#fff;padding:14px 36px;border-radius:10px;text-decoration:none;font-size:15px;font-weight:700;margin-right:12px}
.hero-sec{display:inline-block;border:1px solid rgba(255,255,255,.25);color:#cbd5e1;padding:13px 28px;border-radius:10px;text-decoration:none;font-size:15px;font-weight:600}
.section{padding:72px 40px;max-width:1100px;margin:0 auto}
.section-label{font-size:12px;font-weight:700;color:#2563eb;text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px}
.section h2{font-size:clamp(24px,3.5vw,38px);font-weight:800;letter-spacing:-0.5px;margin-bottom:16px;color:#0f172a}
.section p{color:#475569;font-size:16px;max-width:680px}
.features{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px;margin-top:48px}
.feat{background:#f8fafc;border:1px solid #e2e8f0;border-radius:16px;padding:28px;transition:box-shadow .2s}
.feat:hover{box-shadow:0 8px 32px rgba(37,99,235,.1)}
.feat-icon{width:48px;height:48px;background:#eff6ff;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:22px;margin-bottom:16px}
.feat h3{font-size:16px;font-weight:700;color:#0f172a;margin-bottom:8px}
.feat p{font-size:14px;color:#64748b;line-height:1.55}
.roles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin-top:36px}
.role{background:#f1f5f9;border-radius:12px;padding:20px;text-align:center}
.role-icon{font-size:28px;margin-bottom:8px}
.role h4{font-size:14px;font-weight:700;color:#1e293b;margin-bottom:4px}
.role p{font-size:12px;color:#64748b}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:24px;background:#0f172a;border-radius:20px;padding:48px;margin-top:48px}
.stat{text-align:center;color:#fff}
.stat-num{font-size:42px;font-weight:800;color:#60a5fa;display:block;letter-spacing:-1px}
.stat-label{font-size:13px;color:#94a3b8;margin-top:4px}
.divider{height:1px;background:#e2e8f0;margin:0 40px}
footer{background:#0f172a;color:#94a3b8;padding:40px;text-align:center;font-size:13px}
footer a{color:#60a5fa;text-decoration:none}
footer .footer-links{display:flex;justify-content:center;gap:32px;margin-bottom:16px;flex-wrap:wrap}
</style>
</head>
<body>

<nav class="nav">
  <a href="/" class="nav-logo">VEWIT</a>
  <a href="/?action=login" class="nav-cta">Sign In →</a>
</nav>

<section class="hero">
  <div style="display:inline-flex;align-items:center;gap:7px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);padding:5px 14px;border-radius:100px;margin-bottom:20px">
    <span style="width:6px;height:6px;border-radius:50%;background:#22c55e;display:inline-block;box-shadow:0 0 6px #22c55e"></span>
    <span style="font-size:11px;font-weight:700;color:rgba(255,255,255,.8);letter-spacing:.06em;text-transform:uppercase">Free to start · No credit card</span>
  </div>
  <h1>AI-Powered <span>Project Management</span><br/>&amp; Team Collaboration Platform</h1>
  <p>VEWIT replaces Jira, Slack and Linear with one unified platform — project management, task tracking, direct messaging, support tickets, timeline planning and AI-powered analytics for your entire team.</p>
  <a href="/?action=register" class="hero-cta">Get Started Free</a>
  <a href="/?action=login" class="hero-sec">Sign In</a>
  <div style="display:flex;gap:24px;justify-content:center;margin-top:28px;flex-wrap:wrap">
    <div style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,.5)"><span style="color:#22c55e;font-weight:700">✓</span> Free workspace</div>
    <div style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,.5)"><span style="color:#22c55e;font-weight:700">✓</span> AI assistant included</div>
    <div style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,.5)"><span style="color:#22c55e;font-weight:700">✓</span> 12+ modules built-in</div>
    <div style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,.5)"><span style="color:#22c55e;font-weight:700">✓</span> No credit card needed</div>
  </div>
</section>

<div class="section">
  <div class="section-label">What is VEWIT</div>
  <h2>Everything your team needs, in one place</h2>
  <p>VEWIT is a multi-workspace team collaboration platform powered by AI. Teams use VEWIT to plan projects, track tasks, communicate directly, manage support tickets, monitor timelines and measure developer productivity — without switching between multiple tools.</p>

  <div class="features">
    <div class="feat">
      <div class="feat-icon">📋</div>
      <h3>Project Management</h3>
      <p>Create and manage multiple projects with team assignments, progress tracking, target dates and priority management. Filter projects by team and track completion rates in real time.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">✅</div>
      <h3>Smart Task Board</h3>
      <p>Kanban-style task board with custom stages, story points, sprint planning, subtasks, file attachments, comments and due date reminders. Assign tasks to specific team members with role-based access.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">💬</div>
      <h3>Direct Messages</h3>
      <p>Private one-to-one messaging between team members with real-time online presence indicators, message history and unread notification badges. See who is active at a glance.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">#️⃣</div>
      <h3>Project Channels</h3>
      <p>Dedicated message channels per project for focused team communication. Share updates, files and decisions in context without cluttering direct messages.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">🎫</div>
      <h3>Support Tickets</h3>
      <p>Full ticketing system with bug reports, feature requests, priority levels, assignee tracking, status workflows and team-based filtering. Close issues faster with full context.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">📅</div>
      <h3>Timeline Tracker</h3>
      <p>Visual Gantt-style timeline to plan project schedules, track milestones and identify overlapping workloads across your entire team. Navigate weeks and months effortlessly.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">📊</div>
      <h3>Developer Productivity</h3>
      <p>Analytics dashboard measuring individual and team output — completed tasks, velocity, blocked work, sprint performance and project contribution breakdowns.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">🤖</div>
      <h3>AI Assistant</h3>
      <p>Built-in AI assistant powered by Anthropic Claude. Ask questions about your projects and tasks, get summaries, generate task descriptions and get intelligent suggestions.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">⏰</div>
      <h3>Smart Reminders</h3>
      <p>Set reminders on tasks and deadlines. Get desktop push notifications at the right time so nothing falls through the cracks — even when the app is in the background.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">🔔</div>
      <h3>Push Notifications</h3>
      <p>Real-time notifications for task assignments, status changes, comments, direct messages and reminders. Desktop and in-app notifications route you directly to the relevant item.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">👥</div>
      <h3>Team Management</h3>
      <p>Create sub-teams within your workspace, assign team leads, add members and filter all views by team. Admins control roles, permissions and workspace settings.</p>
    </div>
    <div class="feat">
      <div class="feat-icon">🏢</div>
      <h3>Multi-Workspace</h3>
      <p>Separate workspaces for different companies or departments, each with their own members, projects, settings and branding. Invite members with a unique workspace code.</p>
    </div>
  </div>
</div>

<div class="divider"></div>

<div class="section">
  <div class="section-label">User Roles</div>
  <h2>Role-based access for every team</h2>
  <p>VEWIT supports five user roles with fine-grained permissions, so every team member sees exactly what they need.</p>
  <div class="roles">
    <div class="role"><div class="role-icon">👑</div><h4>Admin</h4><p>Full workspace control, settings, billing and all data</p></div>
    <div class="role"><div class="role-icon">🗂️</div><h4>Manager</h4><p>Create projects, manage tasks and view all team data</p></div>
    <div class="role"><div class="role-icon">🏷️</div><h4>Team Lead</h4><p>Lead sub-teams, assign tasks and manage team members</p></div>
    <div class="role"><div class="role-icon">💻</div><h4>Developer</h4><p>Work on assigned tasks, log progress and communicate</p></div>
    <div class="role"><div class="role-icon">🔍</div><h4>Tester</h4><p>Create and manage tickets, test and verify work items</p></div>
    <div class="role"><div class="role-icon">👁️</div><h4>Viewer</h4><p>Read-only access to projects and team progress</p></div>
  </div>
</div>

<div class="divider"></div>

<div class="section" style="padding-bottom:0">
  <div class="section-label">Why VEWIT</div>
  <h2>One platform, zero context switching</h2>
  <p>Most teams use 5–7 separate tools for project management, communication, ticketing and analytics. VEWIT replaces all of them with a single integrated platform that keeps your entire team in sync.</p>
  <div class="stats">
    <div class="stat"><span class="stat-num">12+</span><span class="stat-label">Integrated modules</span></div>
    <div class="stat"><span class="stat-num">6</span><span class="stat-label">User role levels</span></div>
    <div class="stat"><span class="stat-num">∞</span><span class="stat-label">Projects & tasks</span></div>
    <div class="stat"><span class="stat-num">AI</span><span class="stat-label">Powered by Claude</span></div>
  </div>
</div>

<div class="section">
  <div class="section-label">Get Started</div>
  <h2>Ready to bring your team together?</h2>
  <p>Create a free workspace in seconds. Invite your team, set up your first project and start shipping faster — no credit card required.</p>
  <div style="margin-top:28px;display:flex;gap:16px;flex-wrap:wrap">
    <a href="/?action=register" class="hero-cta" style="display:inline-block">Create Free Account</a>
    <a href="/?action=login" class="hero-sec" style="display:inline-block;background:#f1f5f9;border-color:#e2e8f0;color:#475569">Sign In to VEWIT</a>
  </div>
</div>



<footer>
  <div class="footer-links">
    <a href="/">Home</a>
    <a href="/about">About</a>
    <a href="/?action=login">Sign In</a>
    <a href="/?action=register">Sign Up</a>
  </div>
  <p>© 2024 VEWIT — AI-Powered Team Collaboration Platform &nbsp;·&nbsp; <a href="https://www.vewit.in">www.vewit.in</a></p>
</footer>

</body>
</html>"""


@app.route("/api/contact", methods=["POST"])
def contact_form():
    """Handle public contact form — sends email to ceo@vewit.in."""
    d = request.json or {}
    name    = d.get("name","").strip()
    email   = d.get("email","").strip()
    company = d.get("company","").strip()
    topic   = d.get("topic","").strip()
    message = d.get("message","").strip()

    if not name or not email or not message or not topic:
        return jsonify({"error": "Please fill in all required fields."}), 400

    topic_labels = {
        "demo": "Request a Demo",
        "support": "Technical Support",
        "feature": "Feature Request",
        "enterprise": "Enterprise / Pricing",
        "partnership": "Partnership",
        "other": "General Inquiry"
    }
    topic_label = topic_labels.get(topic, topic)

    subject = f"VEWIT Contact Form: {topic_label} — from {name}"
    body_html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
      <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
        <div style="background:#0f172a;padding:24px 32px;display:flex;align-items:center;gap:12px;">
          <div style="width:36px;height:36px;background:#2563eb;border-radius:8px;display:flex;align-items:center;justify-content:center;">
            <span style="color:white;font-weight:800;font-size:14px">V</span>
          </div>
          <h1 style="color:#fff;margin:0;font-size:18px;font-weight:700;">VEWIT — New Contact Form Submission</h1>
        </div>
        <div style="padding:28px 32px;">
          <div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:14px 18px;margin-bottom:24px;">
            <p style="margin:0;font-size:13px;color:#0369a1;font-weight:600;">📩 Topic: {topic_label}</p>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:20px;">
            <tr style="border-bottom:1px solid #f1f5f9;"><td style="padding:10px 0;color:#64748b;font-weight:600;width:130px;">Name</td><td style="padding:10px 0;color:#1e293b;">{name}</td></tr>
            <tr style="border-bottom:1px solid #f1f5f9;"><td style="padding:10px 0;color:#64748b;font-weight:600;">Email</td><td style="padding:10px 0;"><a href="mailto:{email}" style="color:#2563eb;">{email}</a></td></tr>
            {"<tr style='border-bottom:1px solid #f1f5f9;'><td style='padding:10px 0;color:#64748b;font-weight:600;'>Company</td><td style='padding:10px 0;color:#1e293b;'>" + company + "</td></tr>" if company else ""}
          </table>
          <div style="background:#f8fafc;border-radius:8px;padding:18px;border:1px solid #e2e8f0;">
            <p style="margin:0 0 8px;font-size:12px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:.05em;">Message</p>
            <p style="margin:0;font-size:14px;color:#1e293b;line-height:1.7;white-space:pre-wrap;">{message}</p>
          </div>
        </div>
        <div style="background:#f8fafc;padding:16px 32px;border-top:1px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center;">
          <p style="margin:0;font-size:12px;color:#94a3b8;">Reply directly to <a href="mailto:{email}" style="color:#2563eb;">{email}</a></p>
          <p style="margin:0;font-size:11px;color:#cbd5e1;">VEWIT Contact Form</p>
        </div>
      </div>
    </body>
    </html>
    """

    # Log before sending
    print(f"[Contact] Attempting send: {name} <{email}> | SMTP={SMTP_SERVER}:{SMTP_PORT} | user={SMTP_USERNAME} | pwd_len={len((SMTP_PASSWORD or '').replace(' ',''))}")

    # Send to CEO
    success = send_email("ceo@vewit.in", subject, body_html)
    print(f"[Contact] send_email to CEO returned: {success}")

    # Also send auto-reply to the person who submitted
    auto_reply_html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
      <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
        <div style="background:#0f172a;padding:24px 32px;text-align:center;">
          <h1 style="color:#fff;margin:0;font-size:20px;font-weight:800;">VEWIT</h1>
          <p style="color:#94a3b8;margin:6px 0 0;font-size:13px;">We received your message</p>
        </div>
        <div style="padding:32px;">
          <h2 style="color:#1e293b;margin:0 0 12px;font-size:16px;">Hi {name}, thanks for reaching out! 👋</h2>
          <p style="color:#475569;font-size:14px;line-height:1.7;margin:0 0 20px;">We've received your message about <strong>{topic_label}</strong> and will get back to you within <strong>24 hours</strong>.</p>
          <div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;padding:16px 20px;margin-bottom:20px;">
            <p style="margin:0;font-size:13px;color:#0369a1;"><strong>Your message summary:</strong><br/><span style="color:#1e293b;">{message[:200]}{"..." if len(message)>200 else ""}</span></p>
          </div>
          <p style="color:#64748b;font-size:13px;margin:0;">In the meantime, you can <a href="https://www.vewit.in" style="color:#2563eb;">explore VEWIT</a> or reply to this email if you have anything to add.</p>
        </div>
        <div style="background:#f8fafc;padding:16px 32px;border-top:1px solid #e2e8f0;text-align:center;">
          <p style="margin:0;font-size:12px;color:#94a3b8;">© 2026 VEWIT · <a href="https://www.vewit.in" style="color:#2563eb;">www.vewit.in</a></p>
        </div>
      </div>
    </body>
    </html>
    """
    send_email(email, f"We received your message — VEWIT", auto_reply_html)

    if success:
        return jsonify({"ok": True, "message": f"Message sent successfully! We'll reply to {email} within 24 hours."})
    else:
        return jsonify({"ok": False, "error": "Email could not be sent. Please email ceo@vewit.in directly."}), 500

@app.route("/sitemap.xml")
def sitemap():
    from datetime import datetime as _dt
    today = _dt.utcnow().strftime("%Y-%m-%d")
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.vewit.in/</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>1.0</priority></url>
  <url><loc>https://www.vewit.in/about</loc><lastmod>{today}</lastmod><changefreq>monthly</changefreq><priority>0.9</priority></url>
</urlset>'''
    return xml, 200, {"Content-Type": "application/xml"}

@app.route("/robots.txt")
def robots():
    txt = """User-agent: *
Allow: /
Allow: /about
Disallow: /api/
Disallow: /dashboard
Disallow: /projects
Disallow: /tasks
Disallow: /messages
Disallow: /dm
Disallow: /tickets
Disallow: /timeline
Disallow: /reminders
Disallow: /settings
Disallow: /team
Disallow: /productivity
Sitemap: https://www.vewit.in/sitemap.xml"""
    return txt, 200, {"Content-Type": "text/plain"}

@app.route("/dashboard")
@app.route("/projects")
@app.route("/tasks")
@app.route("/messages")
@app.route("/dm")
@app.route("/tickets")
@app.route("/timeline")
@app.route("/reminders")
@app.route("/settings")
@app.route("/team")
@app.route("/productivity")
@app.route("/calendar")
@app.route("/kanban")
@app.route("/docs")
@app.route("/goals")
@app.route("/sprints")
@app.route("/integrations")
@app.route("/audit")
@app.route("/announcements")
@app.route("/standup")
@app.route("/codereview")
@app.route("/risk")
@app.route("/timereport")
@app.route("/forms")
def app_page(**kwargs):
    """Serve the SPA for all clean URLs — JS picks up the path and sets the view."""
    return HTML

@app.route("/",defaults={"p":""})
@app.route("/<path:p>")
def root(p):
    action=request.args.get("action","")
    if action in ("login","register") or p!="":
        return HTML
    return LANDING_HTML

LANDING_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>VEWIT — AI-Powered Team Collaboration &amp; Project Management Platform</title>
<meta name="description" content="VEWIT — AI-powered team collaboration. Kanban boards, sprints, AI standup generator, code review bot, risk predictor, intake forms, 2FA and more. Free to start."/>
<meta name="keywords" content="VEWIT, AI project management, team collaboration software, kanban board, sprint planning, AI standup, code review bot, risk predictor, intake forms, 2FA security, time tracking"/>
<meta name="author" content="VEWIT"/>
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large, max-video-preview:-1"/>
<meta name="theme-color" content="#0a0f1e"/>
<link rel="canonical" href="https://www.vewit.in/"/>
<meta property="og:type" content="website"/>
<meta property="og:url" content="https://www.vewit.in/"/>
<meta property="og:title" content="VEWIT — AI-Powered Team Collaboration Platform"/>
<meta property="og:description" content="Kanban, sprints, AI standup, code review bot, risk predictor, 2FA — all in one. Free to start."/>
<meta property="og:site_name" content="VEWIT"/>
<meta property="og:image" content="https://www.vewit.in/icon-512.png"/>
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:title" content="VEWIT — AI-Powered Team Collaboration"/>
<meta name="twitter:description" content="Kanban, sprints, AI standup generator, code review bot, risk predictor — free to start."/>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"SoftwareApplication","name":"VEWIT","url":"https://www.vewit.in","description":"AI-powered team collaboration platform with kanban boards, sprint planning, AI standup, code review, risk predictor, 2FA and developer productivity analytics.","applicationCategory":"BusinessApplication","operatingSystem":"Web","offers":{"@type":"Offer","price":"0","priceCurrency":"INR"},"featureList":["AI Standup Generator","AI Code Review","AI Risk Predictor","Kanban Board","Sprint Planning","2FA Security","Time Tracking","Intake Forms","Docs Wiki"]}
</script>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<style>
:root{
  --bg:#ffffff;--sf:#f8fafc;--sf2:#f1f5f9;--sf3:#e2e8f0;
  --tx:#0a0f1e;--tx2:#1e293b;--tx3:#475569;--tx4:#94a3b8;
  --ac:#2563eb;--ac2:#1d4ed8;--pu:#7c3aed;--pi:#db2777;
  --gn:#16a34a;--cy:#0891b2;--am:#d97706;--rd:#dc2626;
  --g1:linear-gradient(135deg,#2563eb,#7c3aed);
  --g2:linear-gradient(135deg,#7c3aed,#db2777);
  --g3:linear-gradient(135deg,#2563eb,#0891b2);
  --r8:8px;--r12:12px;--r16:16px;--r20:20px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:var(--bg);color:var(--tx);font-family:'Inter',system-ui,sans-serif;font-size:16px;line-height:1.65;overflow-x:hidden;}
a{color:inherit;text-decoration:none;}

/* ── NOISE TEXTURE OVERLAY ─────────────────────────── */
body::before{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");pointer-events:none;z-index:0;opacity:.4;}

/* ── NAV ──────────────────────────────────────────── */
nav{position:fixed;top:0;left:0;right:0;z-index:300;height:60px;display:flex;align-items:center;
  background:rgba(255,255,255,.85);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
  border-bottom:1px solid rgba(0,0,0,.06);}
.nav-in{max-width:1200px;margin:0 auto;padding:0 32px;width:100%;display:flex;align-items:center;justify-content:space-between;gap:20px;}
.logo{display:flex;align-items:center;gap:9px;font-weight:800;font-size:.97rem;color:var(--tx);letter-spacing:-.03em;}
.logo-mark{width:32px;height:32px;border-radius:9px;background:var(--g1);display:flex;align-items:center;justify-content:center;box-shadow:0 4px 14px rgba(37,99,235,.35);}
.nav-links{display:flex;align-items:center;gap:2px;list-style:none;}
.nav-links a{font-size:.84rem;font-weight:500;color:var(--tx3);padding:6px 13px;border-radius:var(--r8);transition:all .15s;}
.nav-links a:hover{color:var(--tx);background:var(--sf2);}
.nav-cta{display:flex;gap:8px;align-items:center;}
.btn{display:inline-flex;align-items:center;gap:6px;border:none;cursor:pointer;font-family:'Inter',sans-serif;font-weight:600;transition:all .17s;white-space:nowrap;}
.btn-ghost{background:transparent;color:var(--tx3);padding:8px 18px;border-radius:var(--r8);font-size:.84rem;border:1.5px solid rgba(0,0,0,.12);}
.btn-ghost:hover{color:var(--tx);border-color:rgba(0,0,0,.2);background:var(--sf);}
.btn-grd{background:var(--g1);color:#fff;padding:9px 22px;border-radius:var(--r8);font-size:.84rem;box-shadow:0 4px 16px rgba(37,99,235,.3);}
.btn-grd:hover{opacity:.92;box-shadow:0 6px 24px rgba(37,99,235,.4);transform:translateY(-1px);}
.btn-lg{padding:14px 32px!important;font-size:.96rem!important;border-radius:12px!important;}
.btn-white{background:#fff;color:#1d4ed8;padding:13px 28px;border-radius:12px;font-size:.96rem;box-shadow:0 4px 20px rgba(0,0,0,.15);}
.btn-white:hover{background:#f0f9ff;transform:translateY(-1px);}
.btn-outline-white{background:rgba(255,255,255,.1);color:#fff;padding:13px 28px;border-radius:12px;font-size:.96rem;border:1.5px solid rgba(255,255,255,.3);}
.btn-outline-white:hover{background:rgba(255,255,255,.18);border-color:rgba(255,255,255,.5);}

/* ── HERO ─────────────────────────────────────────── */
.hero{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:100px 32px 80px;position:relative;overflow:hidden;
  background:linear-gradient(160deg,#0a0f1e 0%,#0f1b3d 40%,#1a0a2e 70%,#0a0f1e 100%);}
/* Animated orbs */
.hero::before{content:'';position:absolute;width:600px;height:600px;border-radius:50%;
  background:radial-gradient(circle,rgba(37,99,235,.25) 0%,transparent 70%);
  top:-100px;right:-100px;animation:orb1 8s ease-in-out infinite alternate;pointer-events:none;}
.hero::after{content:'';position:absolute;width:500px;height:500px;border-radius:50%;
  background:radial-gradient(circle,rgba(124,58,237,.2) 0%,transparent 70%);
  bottom:-50px;left:-80px;animation:orb2 10s ease-in-out infinite alternate;pointer-events:none;}
@keyframes orb1{0%{transform:translate(0,0) scale(1);}100%{transform:translate(-60px,40px) scale(1.15);}}
@keyframes orb2{0%{transform:translate(0,0) scale(1);}100%{transform:translate(50px,-40px) scale(1.1);}}
/* Grid pattern */
.hero-grid{position:absolute;inset:0;background-image:linear-gradient(rgba(37,99,235,.07) 1px,transparent 1px),linear-gradient(90deg,rgba(37,99,235,.07) 1px,transparent 1px);background-size:60px 60px;pointer-events:none;}
.hero-in{position:relative;z-index:2;max-width:1200px;margin:0 auto;width:100%;display:grid;grid-template-columns:1fr 1fr;gap:72px;align-items:center;}
.hero-badge{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);padding:5px 14px 5px 8px;border-radius:100px;font-size:.72rem;font-weight:700;color:rgba(255,255,255,.9);margin-bottom:24px;letter-spacing:.04em;text-transform:uppercase;backdrop-filter:blur(8px);}
.badge-dot{width:22px;height:22px;border-radius:50%;background:var(--g1);display:flex;align-items:center;justify-content:center;}
.hero h1{font-size:clamp(2.2rem,4vw,3.5rem);font-weight:900;color:#fff;margin-bottom:20px;line-height:1.06;letter-spacing:-.04em;}
.hero h1 .grad{background:linear-gradient(135deg,#60a5fa,#a78bfa,#f472b6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.hero-sub{font-size:1.02rem;color:rgba(255,255,255,.6);max-width:480px;margin-bottom:36px;line-height:1.75;}
.hero-actions{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px;}
.hero-trust{display:flex;align-items:center;gap:16px;flex-wrap:wrap;}
.trust-it{display:flex;align-items:center;gap:5px;font-size:.78rem;color:rgba(255,255,255,.45);}
.trust-it svg{opacity:.6;}

/* App window mockup */
.hero-right{position:relative;}
.app-win{border-radius:16px;overflow:hidden;border:1px solid rgba(255,255,255,.1);box-shadow:0 40px 100px rgba(0,0,0,.5),0 0 0 1px rgba(255,255,255,.05);background:#0d1117;}
.win-bar{height:40px;background:#161b22;border-bottom:1px solid rgba(255,255,255,.06);display:flex;align-items:center;padding:0 14px;gap:7px;}
.wd{width:11px;height:11px;border-radius:50%;}
.win-url{flex:1;margin:0 10px;height:22px;background:rgba(255,255,255,.06);border-radius:6px;display:flex;align-items:center;padding:0 10px;gap:6px;}
.win-url-txt{font-size:.62rem;color:rgba(255,255,255,.35);font-family:'JetBrains Mono',monospace;}
.win-body{display:flex;height:360px;}
.win-sb{width:168px;flex-shrink:0;background:#0d1117;padding:10px 8px;border-right:1px solid rgba(255,255,255,.06);}
.ws-head{display:flex;align-items:center;gap:8px;padding:7px 8px 12px;border-bottom:1px solid rgba(255,255,255,.06);margin-bottom:8px;}
.ws-av{width:28px;height:28px;border-radius:7px;background:var(--g1);display:flex;align-items:center;justify-content:center;font-size:.58rem;font-weight:800;color:#fff;flex-shrink:0;}
.ws-nm{font-size:.7rem;font-weight:700;color:#e2e8f0;letter-spacing:-.01em;}
.ws-sub{font-size:.58rem;color:#475569;}
.nav-sec{font-size:.56rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#334155;padding:8px 8px 3px;}
.nav-it{display:flex;align-items:center;gap:7px;padding:6px 8px;border-radius:6px;font-size:.68rem;color:#64748b;margin-bottom:1px;}
.nav-it.act{background:rgba(37,99,235,.2);color:#93c5fd;}
.nav-it svg{width:12px;height:12px;flex-shrink:0;}
.win-main{flex:1;background:#0a0f1e;overflow:hidden;display:flex;flex-direction:column;}
.win-hdr{height:38px;background:#0d1117;border-bottom:1px solid rgba(255,255,255,.06);display:flex;align-items:center;padding:0 14px;justify-content:space-between;flex-shrink:0;}
.win-title{font-size:.75rem;font-weight:700;color:#e2e8f0;}
.win-badges{display:flex;gap:5px;}
.win-badge{font-size:.58rem;font-weight:700;padding:2px 7px;border-radius:99px;}
.kan{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;padding:10px;overflow:hidden;}
.kcol{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:8px;}
.kcol-h{font-size:.56rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:7px;display:flex;justify-content:space-between;align-items:center;}
.kcard{background:#161b22;border:1px solid rgba(255,255,255,.07);border-radius:6px;padding:7px;margin-bottom:5px;border-left-width:2px;border-left-style:solid;}
.kcard-t{font-size:.62rem;font-weight:500;color:#e2e8f0;margin-bottom:5px;line-height:1.4;}
.ktag{display:inline-block;font-size:.52rem;padding:1.5px 5px;border-radius:3px;font-weight:600;margin-right:3px;}
/* floating cards */
.float-card{position:absolute;background:#fff;border-radius:12px;padding:12px 14px;box-shadow:0 12px 40px rgba(0,0,0,.25);border:1px solid rgba(0,0,0,.07);}
.fc-head{display:flex;align-items:center;gap:7px;margin-bottom:5px;}
.fc-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;}
.fc-title{font-size:.7rem;font-weight:700;color:#0a0f1e;}
.fc-body{font-size:.68rem;color:#475569;line-height:1.5;}

/* ── TICKER ───────────────────────────────────────── */
.ticker-wrap{overflow:hidden;padding:14px 0;background:linear-gradient(135deg,#0a0f1e,#1a0a2e);border-top:1px solid rgba(255,255,255,.06);border-bottom:1px solid rgba(255,255,255,.06);}
.ticker{display:flex;animation:tick 45s linear infinite;width:max-content;}
@keyframes tick{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.t-it{display:flex;align-items:center;gap:8px;padding:0 28px;font-size:.78rem;color:rgba(255,255,255,.4);white-space:nowrap;flex-shrink:0;}
.t-hi{font-weight:700;background:linear-gradient(135deg,#60a5fa,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.t-sep{color:rgba(255,255,255,.15);}

/* ── STATS ────────────────────────────────────────── */
.stats{padding:72px 0;background:#fff;}
.stats-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;}
.stat-card{text-align:center;padding:28px 16px;border-radius:var(--r16);border:1.5px solid var(--sf3);background:#fff;transition:all .2s;position:relative;overflow:hidden;}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--g1);opacity:0;transition:opacity .2s;}
.stat-card:hover{transform:translateY(-4px);box-shadow:0 12px 36px rgba(37,99,235,.1);border-color:rgba(37,99,235,.2);}
.stat-card:hover::before{opacity:1;}
.stat-n{font-size:2.4rem;font-weight:900;background:var(--g1);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;line-height:1;margin-bottom:6px;}
.stat-l{font-size:.8rem;color:var(--tx3);font-weight:500;}

/* ── SECTIONS ─────────────────────────────────────── */
section{padding:96px 0;}
.wrap{max-width:1200px;margin:0 auto;padding:0 32px;}
.sec-tag{display:inline-flex;align-items:center;gap:6px;font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#fff;margin-bottom:12px;background:var(--g1);padding:4px 14px;border-radius:100px;box-shadow:0 2px 12px rgba(37,99,235,.3);}
.sec-title{font-size:clamp(1.8rem,3vw,2.5rem);font-weight:900;max-width:580px;margin-bottom:12px;color:var(--tx);letter-spacing:-.03em;line-height:1.1;}
.sec-sub{color:var(--tx3);font-size:.97rem;max-width:520px;margin-bottom:52px;line-height:1.8;}
.centered{text-align:center;}.centered .sec-title,.centered .sec-sub{margin-left:auto;margin-right:auto;}

/* ── AI SECTION ───────────────────────────────────── */
.ai-section{background:linear-gradient(160deg,#0a0f1e 0%,#0f1b3d 50%,#1a0a2e 100%);position:relative;overflow:hidden;}
.ai-section::before{content:'';position:absolute;width:800px;height:800px;border-radius:50%;background:radial-gradient(circle,rgba(124,58,237,.12) 0%,transparent 70%);top:-200px;right:-200px;pointer-events:none;}
.ai-section::after{content:'';position:absolute;width:600px;height:600px;border-radius:50%;background:radial-gradient(circle,rgba(37,99,235,.1) 0%,transparent 70%);bottom:-100px;left:-100px;pointer-events:none;}
.ai-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;position:relative;z-index:2;}
.ai-card{border-radius:var(--r16);padding:28px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.04);backdrop-filter:blur(12px);transition:all .25s;position:relative;overflow:hidden;}
.ai-card::before{content:'';position:absolute;inset:0;background:linear-gradient(135deg,rgba(255,255,255,.04),transparent);opacity:0;transition:opacity .25s;}
.ai-card:hover{border-color:rgba(255,255,255,.2);transform:translateY(-4px);box-shadow:0 20px 60px rgba(0,0,0,.4);}
.ai-card:hover::before{opacity:1;}
.ai-card.span2{grid-column:span 2;}
.ai-icon{width:48px;height:48px;border-radius:12px;display:flex;align-items:center;justify-content:center;margin-bottom:16px;font-size:22px;}
.ai-ic-blue{background:linear-gradient(135deg,rgba(37,99,235,.3),rgba(37,99,235,.1));border:1px solid rgba(37,99,235,.3);}
.ai-ic-purple{background:linear-gradient(135deg,rgba(124,58,237,.3),rgba(124,58,237,.1));border:1px solid rgba(124,58,237,.3);}
.ai-ic-red{background:linear-gradient(135deg,rgba(220,38,38,.25),rgba(220,38,38,.08));border:1px solid rgba(220,38,38,.25);}
.ai-ic-green{background:linear-gradient(135deg,rgba(22,163,74,.25),rgba(22,163,74,.08));border:1px solid rgba(22,163,74,.25);}
.ai-card h3{font-size:1.05rem;font-weight:700;color:#fff;margin-bottom:10px;}
.ai-card p{font-size:.88rem;color:rgba(255,255,255,.55);line-height:1.75;}
.ai-list{list-style:none;margin-top:14px;display:flex;flex-direction:column;gap:8px;}
.ai-list li{display:flex;align-items:flex-start;gap:8px;font-size:.84rem;color:rgba(255,255,255,.6);}
.ai-list li::before{content:'';width:5px;height:5px;border-radius:50%;background:linear-gradient(135deg,#60a5fa,#a78bfa);flex-shrink:0;margin-top:8px;}
.role-chip{font-size:.62rem;font-weight:700;padding:2px 8px;border-radius:100px;background:rgba(96,165,250,.15);color:#93c5fd;border:1px solid rgba(96,165,250,.25);margin-left:5px;}
.code-prev{margin-top:18px;background:rgba(0,0,0,.4);border-radius:10px;padding:16px;font-family:'JetBrains Mono',monospace;font-size:.72rem;line-height:1.8;border:1px solid rgba(255,255,255,.06);}
.cp-c{color:#475569;}
.cp-k{color:#93c5fd;}
.cp-v{color:#86efac;}
.cp-s{color:#fca5a5;}

/* ── FEATURE BENTO ────────────────────────────────── */
.bento{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;}
.ben{background:#fff;border:1.5px solid var(--sf3);border-radius:var(--r16);padding:28px;transition:all .2s;cursor:default;position:relative;overflow:hidden;}
.ben::after{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--g1);opacity:0;transition:opacity .2s;}
.ben:hover{transform:translateY(-4px);box-shadow:0 16px 50px rgba(37,99,235,.08);border-color:rgba(37,99,235,.18);}
.ben:hover::after{opacity:1;}
.ben.wide{grid-column:span 2;}
.ben-ico{width:46px;height:46px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px;margin-bottom:16px;transition:transform .2s;}
.ben:hover .ben-ico{transform:scale(1.1);}
.ben-ico-1{background:linear-gradient(135deg,rgba(37,99,235,.12),rgba(37,99,235,.04));border:1px solid rgba(37,99,235,.15);}
.ben-ico-2{background:linear-gradient(135deg,rgba(124,58,237,.12),rgba(124,58,237,.04));border:1px solid rgba(124,58,237,.15);}
.ben-ico-3{background:linear-gradient(135deg,rgba(22,163,74,.1),rgba(22,163,74,.03));border:1px solid rgba(22,163,74,.15);}
.ben-ico-4{background:linear-gradient(135deg,rgba(14,165,233,.1),rgba(14,165,233,.03));border:1px solid rgba(14,165,233,.15);}
.ben-ico-5{background:linear-gradient(135deg,rgba(245,158,11,.1),rgba(245,158,11,.03));border:1px solid rgba(245,158,11,.15);}
.ben-ico-6{background:linear-gradient(135deg,rgba(219,39,119,.1),rgba(219,39,119,.03));border:1px solid rgba(219,39,119,.15);}
.ben-ico-7{background:linear-gradient(135deg,rgba(6,182,212,.1),rgba(6,182,212,.03));border:1px solid rgba(6,182,212,.15);}
.ben-ico-8{background:linear-gradient(135deg,rgba(16,185,129,.1),rgba(16,185,129,.03));border:1px solid rgba(16,185,129,.15);}
.ben h3{font-size:.97rem;font-weight:700;margin-bottom:8px;color:var(--tx);}
.ben p{font-size:.87rem;color:var(--tx3);line-height:1.7;}
.ben-list{list-style:none;margin-top:12px;display:flex;flex-direction:column;gap:6px;}
.ben-list li{display:flex;align-items:flex-start;gap:7px;font-size:.84rem;color:var(--tx2);}
.ben-list li::before{content:'';width:4px;height:4px;border-radius:50%;background:var(--g1);flex-shrink:0;margin-top:8px;}

/* ── SECURITY SECTION ─────────────────────────────── */
.sec-section{background:linear-gradient(160deg,#0a0f1e 0%,#0c1a3b 50%,#150a2e 100%);position:relative;overflow:hidden;}
.sec-section::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(96,165,250,.4),transparent);}
.sec-section::after{content:'';position:absolute;bottom:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(167,139,250,.4),transparent);}
.sec-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.sec-card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:var(--r16);padding:26px;display:flex;gap:16px;align-items:flex-start;transition:all .2s;backdrop-filter:blur(8px);}
.sec-card:hover{background:rgba(255,255,255,.07);border-color:rgba(255,255,255,.18);transform:translateY(-2px);}
.sec-ico{width:44px;height:44px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0;}
.sec-ico-blue{background:linear-gradient(135deg,rgba(37,99,235,.35),rgba(37,99,235,.12));border:1px solid rgba(37,99,235,.3);}
.sec-ico-purple{background:linear-gradient(135deg,rgba(124,58,237,.35),rgba(124,58,237,.12));border:1px solid rgba(124,58,237,.3);}
.sec-ico-green{background:linear-gradient(135deg,rgba(22,163,74,.3),rgba(22,163,74,.1));border:1px solid rgba(22,163,74,.25);}
.sec-ico-amber{background:linear-gradient(135deg,rgba(217,119,6,.3),rgba(217,119,6,.1));border:1px solid rgba(217,119,6,.25);}
.sec-card h4{font-size:.93rem;font-weight:700;margin-bottom:6px;color:#fff;}
.sec-card p{font-size:.84rem;color:rgba(255,255,255,.5);line-height:1.65;}

/* ── ROLE MATRIX ──────────────────────────────────── */
.role-section{background:var(--sf);}
.role-table-wrap{overflow-x:auto;border-radius:var(--r16);border:1.5px solid var(--sf3);overflow:hidden;}
.role-table{width:100%;border-collapse:collapse;font-size:.84rem;}
.role-table th{background:#fff;padding:13px 16px;text-align:center;font-weight:700;font-size:.73rem;color:var(--tx2);border-bottom:1.5px solid var(--sf3);white-space:nowrap;}
.role-table th.feat-col{text-align:left;min-width:200px;}
.role-table td{padding:11px 16px;border-bottom:1px solid var(--sf3);background:#fff;text-align:center;vertical-align:middle;}
.role-table td.feat-col{text-align:left;font-weight:500;color:var(--tx);background:#fafbfc;border-right:1.5px solid var(--sf3);}
.role-table .cat-row td{background:linear-gradient(135deg,#eff6ff,#f5f3ff);font-size:.7rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#4338ca;padding:8px 16px;}
.role-table tr:last-child td{border-bottom:none;}
.pchip{display:inline-block;font-size:.68rem;font-weight:600;padding:2px 9px;border-radius:99px;}
.pc-full{background:linear-gradient(135deg,rgba(22,163,74,.12),rgba(16,185,129,.08));color:#15803d;border:1px solid rgba(22,163,74,.2);}
.pc-own{background:rgba(217,119,6,.1);color:#b45309;border:1px solid rgba(217,119,6,.2);}
.pc-view{background:rgba(37,99,235,.08);color:#1d4ed8;border:1px solid rgba(37,99,235,.15);}
.pc-no{color:#cbd5e1;font-size:.9rem;}

/* ── HOW IT WORKS ─────────────────────────────────── */
.steps-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:24px;margin-top:52px;position:relative;}
.steps-grid::before{content:'';position:absolute;top:23px;left:10%;right:10%;height:1px;background:linear-gradient(90deg,transparent,rgba(37,99,235,.3),rgba(124,58,237,.3),rgba(37,99,235,.3),transparent);z-index:0;}
.step-card{text-align:center;position:relative;z-index:1;}
.step-num{width:48px;height:48px;border-radius:50%;background:var(--g1);display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-weight:800;font-size:.9rem;color:#fff;box-shadow:0 4px 20px rgba(37,99,235,.35);}
.step-card h3{font-size:.97rem;font-weight:700;margin-bottom:8px;color:var(--tx);}
.step-card p{font-size:.87rem;color:var(--tx3);line-height:1.65;}

/* ── INTEGRATIONS ─────────────────────────────────── */
.int-section{background:linear-gradient(135deg,#f0f9ff,#f5f3ff,#fce7f3);}
.int-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px;}
.int-card{background:#fff;border:1.5px solid var(--sf3);border-radius:var(--r16);padding:20px 16px;text-align:center;transition:all .2s;position:relative;overflow:hidden;}
.int-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--g1);transform:scaleX(0);transition:transform .2s;}
.int-card:hover{transform:translateY(-3px);box-shadow:0 10px 32px rgba(37,99,235,.1);border-color:rgba(37,99,235,.2);}
.int-card:hover::before{transform:scaleX(1);}
.int-ico{font-size:1.9rem;margin-bottom:9px;}
.int-n{font-size:.86rem;font-weight:700;color:var(--tx);margin-bottom:3px;}
.int-d{font-size:.75rem;color:var(--tx3);line-height:1.45;}

/* ── TESTIMONIALS ─────────────────────────────────── */
.testimonial-section{background:#fff;}
.quote-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}
.quote-card{background:#fff;border:1.5px solid var(--sf3);border-radius:var(--r20);padding:28px;transition:all .2s;position:relative;overflow:hidden;}
.quote-card::before{content:'';position:absolute;top:0;left:0;width:100%;height:3px;opacity:0;transition:opacity .2s;}
.quote-card:nth-child(1)::before{background:linear-gradient(90deg,#2563eb,#7c3aed);}
.quote-card:nth-child(2)::before{background:linear-gradient(90deg,#7c3aed,#db2777);}
.quote-card:nth-child(3)::before{background:linear-gradient(90deg,#16a34a,#0891b2);}
.quote-card:hover{border-color:rgba(37,99,235,.2);box-shadow:0 12px 40px rgba(37,99,235,.08);transform:translateY(-3px);}
.quote-card:hover::before{opacity:1;}
.q-stars{font-size:.95rem;letter-spacing:2px;margin-bottom:14px;}
.q-text{font-size:.91rem;color:var(--tx2);line-height:1.75;margin-bottom:20px;font-style:italic;}
.q-author{display:flex;align-items:center;gap:11px;}
.q-av{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.8rem;color:#fff;flex-shrink:0;}
.q-name{font-size:.86rem;font-weight:700;color:var(--tx);}
.q-role{font-size:.76rem;color:var(--tx3);}

/* ── FAQ ──────────────────────────────────────────── */
.faq-section{background:var(--sf);}
.faq-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px;}
.faq-item{background:#fff;border:1.5px solid var(--sf3);border-radius:var(--r16);padding:22px 24px;transition:all .2s;}
.faq-item:hover{border-color:rgba(37,99,235,.2);box-shadow:0 6px 24px rgba(37,99,235,.07);}
.faq-item h4{font-size:.93rem;font-weight:700;color:var(--tx);margin-bottom:8px;}
.faq-item p{font-size:.86rem;color:var(--tx3);line-height:1.7;}

/* ── CTA ──────────────────────────────────────────── */
.cta-section{padding:100px 0;background:linear-gradient(160deg,#0a0f1e 0%,#0f1b3d 40%,#1a0a2e 70%,#0a0f1e 100%);position:relative;overflow:hidden;}
.cta-section::before{content:'';position:absolute;width:700px;height:700px;border-radius:50%;background:radial-gradient(circle,rgba(37,99,235,.15) 0%,transparent 70%);top:-200px;left:-100px;pointer-events:none;}
.cta-section::after{content:'';position:absolute;width:600px;height:600px;border-radius:50%;background:radial-gradient(circle,rgba(124,58,237,.12) 0%,transparent 70%);bottom:-150px;right:-100px;pointer-events:none;}
.cta-in{text-align:center;position:relative;z-index:2;}
.cta-tag{display:inline-block;background:rgba(96,165,250,.15);color:#93c5fd;font-size:.72rem;font-weight:700;padding:4px 14px;border-radius:100px;letter-spacing:.07em;text-transform:uppercase;margin-bottom:22px;border:1px solid rgba(96,165,250,.2);}
.cta-in h2{font-size:clamp(2rem,4vw,3.2rem);font-weight:900;color:#fff;margin-bottom:14px;letter-spacing:-.04em;line-height:1.08;}
.cta-in h2 .grad{background:linear-gradient(135deg,#60a5fa,#a78bfa,#f472b6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.cta-in p{color:rgba(255,255,255,.55);font-size:1rem;max-width:440px;margin:0 auto 36px;line-height:1.75;}
.cta-actions{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-bottom:28px;}
.cta-trust{display:flex;justify-content:center;gap:20px;flex-wrap:wrap;}
.ct-it{display:flex;align-items:center;gap:6px;font-size:.78rem;color:rgba(255,255,255,.45);}

/* ── FOOTER ───────────────────────────────────────── */
footer{padding:60px 0 36px;background:#0a0f1e;border-top:1px solid rgba(255,255,255,.06);}
.footer-grid{display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr;gap:40px;margin-bottom:48px;}
.footer-brand p{font-size:.83rem;color:rgba(255,255,255,.35);margin-top:10px;line-height:1.75;max-width:240px;}
.footer-col h4{font-size:.72rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:rgba(255,255,255,.4);margin-bottom:14px;}
.footer-col ul{list-style:none;display:flex;flex-direction:column;gap:10px;}
.footer-col a{color:rgba(255,255,255,.35);font-size:.84rem;transition:color .15s;}
.footer-col a:hover{color:rgba(255,255,255,.8);}
.footer-bottom{display:flex;align-items:center;justify-content:space-between;padding-top:28px;border-top:1px solid rgba(255,255,255,.06);flex-wrap:wrap;gap:12px;}
.footer-copy{font-size:.79rem;color:rgba(255,255,255,.25);}
.footer-badges{display:flex;gap:8px;}
.fb{font-size:.7rem;padding:3px 10px;border-radius:100px;border:1px solid rgba(255,255,255,.1);color:rgba(255,255,255,.3);}

/* ── ANIMATIONS ───────────────────────────────────── */
@keyframes fadeUp{from{opacity:0;transform:translateY(22px)}to{opacity:1;transform:translateY(0)}}
.a1{animation:fadeUp .65s .08s both;}
.a2{animation:fadeUp .65s .18s both;}
.a3{animation:fadeUp .65s .3s both;}
.a4{animation:fadeUp .65s .44s both;}
.a5{animation:fadeUp .65s .6s both;}

/* ── RESPONSIVE ───────────────────────────────────── */
@media(max-width:1024px){
  .hero-in{grid-template-columns:1fr;gap:48px;}
  .hero-right{display:none;}
  .ai-grid,.bento{grid-template-columns:1fr 1fr;}
  .ai-card.span2,.ben.wide{grid-column:span 1;}
  .steps-grid{grid-template-columns:1fr 1fr;}
  .sec-grid{grid-template-columns:1fr;}
  .quote-grid{grid-template-columns:1fr 1fr;}
  .footer-grid{grid-template-columns:1fr 1fr;}
  .int-grid{grid-template-columns:repeat(2,1fr);}
  .stats-grid{grid-template-columns:repeat(3,1fr);}
  .faq-grid{grid-template-columns:1fr;}
}
@media(max-width:640px){
  .nav-links,.nav-cta .btn-ghost{display:none;}
  .ai-grid,.bento,.quote-grid{grid-template-columns:1fr;}
  .steps-grid{grid-template-columns:1fr;}
  .footer-grid{grid-template-columns:1fr;}
  .stats-grid{grid-template-columns:1fr 1fr;}
  .int-grid{grid-template-columns:1fr 1fr;}
  .hero{padding:90px 20px 50px;}
  .steps-grid::before{display:none;}
}
</style>
</head>
<body>
<!-- NAV -->
<nav id="nav">
  <div class="nav-in">
    <a href="/" class="logo">
      <div class="logo-mark"><svg width="16" height="16" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white"/><circle cx="51" cy="43" r="6" fill="white"/><circle cx="13" cy="43" r="6" fill="white"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/></svg></div>
      VEWIT
    </a>
    <ul class="nav-links">
      <li><a href="#features">Features</a></li>
      <li><a href="#ai">AI Tools</a></li>
      <li><a href="#security">Security</a></li>
      <li><a href="#roles">Roles</a></li>
      <li><a href="#how">How it works</a></li>
    </ul>
    <div class="nav-cta">
      <a href="/?action=login" class="btn btn-ghost">Sign In</a>
      <a href="/?action=register" class="btn btn-grd">Get Started Free</a>
    </div>
  </div>
</nav>

<!-- HERO -->
<section class="hero">
  <div class="hero-grid"></div>
  <div class="hero-in">
    <div>
      <div class="hero-badge a1">
        <div class="badge-dot"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg></div>
        v5.0 — AI Standup · Code Review · Risk · 2FA
      </div>
      <h1 class="a2">Ship <span class="grad">faster.</span><br/>Stay in sync.</h1>
      <p class="hero-sub a3">Kanban boards, sprints, AI standup generator, code review bot, risk predictor, intake forms, 2FA — one platform built for engineering teams that move fast.</p>
      <div class="hero-actions a4">
        <a href="/?action=register" class="btn btn-grd btn-lg">Start Free — No Card Needed →</a>
        <a href="#features" class="btn btn-outline-white btn-lg">See All Features</a>
      </div>
      <div class="hero-trust a4">
        <div class="trust-it"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>Free forever</div>
        <div class="trust-it"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>TOTP 2FA built-in</div>
        <div class="trust-it"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>Your own AI key</div>
        <div class="trust-it"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>Up in 2 min</div>
      </div>
    </div>
    <div class="hero-right a5">
      <div style="text-align:center;margin-bottom:10px;"><span style="font-size:10px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:rgba(255,255,255,.25);background:rgba(255,255,255,.06);padding:3px 12px;border-radius:99px;border:1px solid rgba(255,255,255,.08);">Live Preview</span></div>
      <div class="app-win">
        <div class="win-bar">
          <div class="wd" style="background:#ff5f57"></div>
          <div class="wd" style="background:#febc2e"></div>
          <div class="wd" style="background:#28c840"></div>
          <div class="win-url"><span class="win-url-txt">vewit.in/tasks</span></div>
        </div>
        <div class="win-body">
          <div class="win-sb">
            <div class="ws-head">
              <div class="ws-av">VW</div>
              <div><div class="ws-nm">VEWIT Corp</div><div class="ws-sub">8 online</div></div>
            </div>
            <div class="nav-it act"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>Kanban</div>
            <div class="nav-it"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>Channels</div>
            <div class="nav-sec">AI Tools</div>
            <div class="nav-it"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>AI Standup</div>
            <div class="nav-it"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>Code Review</div>
            <div class="nav-it"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>Risk Predictor</div>
          </div>
          <div class="win-main">
            <div class="win-hdr">
              <div class="win-title">Kanban Board</div>
              <div class="win-badges">
                <span class="win-badge" style="background:rgba(37,99,235,.15);color:#93c5fd;">Sprint 4</span>
                <span class="win-badge" style="background:rgba(34,197,94,.12);color:#4ade80;">42 pts</span>
              </div>
            </div>
            <div class="kan">
              <div class="kcol"><div class="kcol-h" style="color:#64748b">Backlog<span style="background:#1e293b;color:#475569;font-size:.5rem;padding:1px 5px;border-radius:3px;">8</span></div>
                <div class="kcard" style="border-left-color:#8b5cf6"><div class="kcard-t">Auth flow redesign</div><div><span class="ktag" style="background:rgba(139,92,246,.2);color:#a78bfa">5pt</span><span class="ktag" style="background:rgba(37,99,235,.15);color:#93c5fd">Design</span></div></div>
                <div class="kcard" style="border-left-color:#8b5cf6"><div class="kcard-t">API rate limiting</div><div><span class="ktag" style="background:rgba(139,92,246,.2);color:#a78bfa">3pt</span></div></div>
              </div>
              <div class="kcol"><div class="kcol-h" style="color:#38bdf8">In Progress<span style="background:#0c4a6e;color:#38bdf8;font-size:.5rem;padding:1px 5px;border-radius:3px;">5</span></div>
                <div class="kcard" style="border-left-color:#38bdf8"><div class="kcard-t">Payment gateway</div><div><span class="ktag" style="background:rgba(239,68,68,.2);color:#f87171">High</span><span class="ktag" style="background:rgba(14,165,233,.15);color:#38bdf8">8pt</span></div></div>
                <div class="kcard" style="border-left-color:#38bdf8"><div class="kcard-t">Dashboard charts</div><div><span class="ktag" style="background:rgba(245,158,11,.2);color:#fbbf24">Med</span></div></div>
              </div>
              <div class="kcol"><div class="kcol-h" style="color:#fbbf24">Review<span style="background:#451a03;color:#fbbf24;font-size:.5rem;padding:1px 5px;border-radius:3px;">3</span></div>
                <div class="kcard" style="border-left-color:#fbbf24"><div class="kcard-t">Mobile responsive</div><div><span class="ktag" style="background:rgba(245,158,11,.2);color:#fbbf24">3pt</span></div></div>
              </div>
              <div class="kcol"><div class="kcol-h" style="color:#4ade80">Done<span style="background:#052e16;color:#4ade80;font-size:.5rem;padding:1px 5px;border-radius:3px;">12</span></div>
                <div class="kcard" style="border-left-color:#4ade80"><div class="kcard-t">User onboarding</div><div><span class="ktag" style="background:rgba(34,197,94,.15);color:#4ade80">Done</span></div></div>
                <div class="kcard" style="border-left-color:#4ade80"><div class="kcard-t">Email notifications</div><div><span class="ktag" style="background:rgba(34,197,94,.15);color:#4ade80">Done</span></div></div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <!-- Floating cards -->
      <div class="float-card" style="bottom:-16px;right:-20px;max-width:210px;">
        <div class="fc-head"><div class="fc-dot" style="background:#22c55e"></div><div class="fc-title">AI Standup Generated</div></div>
        <div class="fc-body">✅ Merged auth PR<br/>🔨 Starting payment gateway<br/>🚧 Waiting on design review</div>
      </div>
      <div class="float-card" style="top:-14px;left:-20px;max-width:200px;">
        <div class="fc-head"><div class="fc-dot" style="background:#f59e0b"></div><div class="fc-title" style="color:#b45309">⚠️ Risk Alert</div></div>
        <div class="fc-body">Payment sprint — HIGH risk<br/>3 tasks overdue</div>
      </div>
    </div>
  </div>
</section>

<!-- TICKER -->
<div class="ticker-wrap">
  <div class="ticker">
    <div class="t-it"><span class="t-hi">Kanban Board</span> Drag-drop task management <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">AI Standup</span> Auto-generate daily reports <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Code Review Bot</span> AI reviews your PR diffs <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Risk Predictor</span> Flag at-risk projects early <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Intake Forms</span> Public forms → auto tickets <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">TOTP 2FA</span> Authenticator app security <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Message Reactions</span> Emoji reactions on messages <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Time Reports</span> Billable hours tracking <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Sprint Planning</span> Velocity &amp; story points <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Announcements</span> Workspace-wide broadcasts <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Goals &amp; OKRs</span> Track key results <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Docs &amp; Wiki</span> AI-generated documentation <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Kanban Board</span> Drag-drop task management <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">AI Standup</span> Auto-generate daily reports <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Code Review Bot</span> AI reviews your PR diffs <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Risk Predictor</span> Flag at-risk projects early <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Intake Forms</span> Public forms → auto tickets <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">TOTP 2FA</span> Authenticator app security <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Message Reactions</span> Emoji reactions on messages <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Time Reports</span> Billable hours tracking <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Sprint Planning</span> Velocity &amp; story points <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Announcements</span> Workspace-wide broadcasts <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Goals &amp; OKRs</span> Track key results <span class="t-sep">·</span></div>
    <div class="t-it"><span class="t-hi">Docs &amp; Wiki</span> AI-generated documentation <span class="t-sep">·</span></div>
  </div>
</div>

<!-- STATS -->
<div class="stats">
  <div class="wrap">
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-n">35+</div><div class="stat-l">Platform features</div></div>
      <div class="stat-card"><div class="stat-n">6</div><div class="stat-l">Role levels + RBAC</div></div>
      <div class="stat-card"><div class="stat-n">4</div><div class="stat-l">AI-powered tools</div></div>
      <div class="stat-card"><div class="stat-n">Zero</div><div class="stat-l">Dependencies required</div></div>
      <div class="stat-card"><div class="stat-n">Free</div><div class="stat-l">No credit card needed</div></div>
    </div>
  </div>
</div>

<!-- AI FEATURES -->
<section id="ai" class="ai-section">
  <div class="wrap">
    <div class="centered">
      <div class="sec-tag">🤖 AI-Powered Tools</div>
      <h2 class="sec-title" style="color:#fff">Your team's AI co-pilot</h2>
      <p class="sec-sub" style="color:rgba(255,255,255,.55)">Four powerful AI tools built into your workflow — use your own Anthropic API key, zero vendor lock-in.</p>
    </div>
    <div class="ai-grid">
      <div class="ai-card span2">
        <div class="ai-icon ai-ic-blue">🤖</div>
        <h3>AI Daily Standup Generator</h3>
        <p>Automatically generates professional standup reports from task activity, time logs, and progress — no manual writing.</p>
        <ul class="ai-list">
          <li>Pulls from task changes, time logs, and comments from the last 24h</li>
          <li>Managers generate standups for whole team; devs see their own<span class="role-chip">Role-gated</span></li>
          <li>Auto-saves to standup history for retrospectives</li>
          <li>Copy to clipboard and share in one click</li>
        </ul>
        <div class="code-prev">
          <span class="cp-c">// Generated standup for Prasanna — today</span><br/>
          <span class="cp-k">✅ Yesterday:</span> <span class="cp-v">Merged MuleSoft auth API to staging</span><br/>
          <span class="cp-k">🔨 Today:</span> <span class="cp-v">Starting payment gateway integration (T-024)</span><br/>
          <span class="cp-k">🚧 Blockers:</span> <span class="cp-s">Waiting on design review for checkout UI</span>
        </div>
      </div>
      <div class="ai-card">
        <div class="ai-icon ai-ic-purple">🔍</div>
        <h3>AI Code Review Bot</h3>
        <p>Paste any PR diff and get instant structured code review with bug detection, security analysis, and a clear verdict.</p>
        <ul class="ai-list">
          <li>🔴 Critical · 🟠 Major · 🟡 Minor classification</li>
          <li>Security vulnerability detection</li>
          <li>Approve / Request Changes / Reject verdict</li>
          <li>Reviews saved to task or ticket history</li>
        </ul>
      </div>
      <div class="ai-card">
        <div class="ai-icon ai-ic-red">⚠️</div>
        <h3>AI Risk Predictor</h3>
        <p>Scans all projects for overdue tasks, blockers, and deadline proximity — flags each with risk level and actions.</p>
        <ul class="ai-list">
          <li>LOW / MEDIUM / HIGH / CRITICAL risk scoring</li>
          <li>Overdue, blocked, completion rate analysis</li>
          <li>Actionable recommendations per project</li>
          <li>Admin, Manager, TeamLead only<span class="role-chip">Role-gated</span></li>
        </ul>
      </div>
      <div class="ai-card">
        <div class="ai-icon ai-ic-green">📄</div>
        <h3>AI Docs &amp; Wiki Generator</h3>
        <p>Generate technical documentation in seconds — architecture diagrams, API references, READMEs, and runbooks.</p>
        <ul class="ai-list">
          <li>5 doc types: General, Architecture, API, README, Runbook</li>
          <li>Auto-renders Mermaid architecture diagrams</li>
          <li>Auto-saves to your docs library</li>
        </ul>
      </div>
    </div>
  </div>
</section>

<!-- CORE FEATURES -->
<section id="features">
  <div class="wrap">
    <div class="centered">
      <div class="sec-tag">🚀 Platform Features</div>
      <h2 class="sec-title">Everything your team needs</h2>
      <p class="sec-sub">35+ features across project management, communication, security and analytics — in a single workspace.</p>
    </div>
    <div class="bento">
      <div class="ben wide">
        <div class="ben-ico ben-ico-1">🗂</div>
        <h3>Kanban Board &amp; Sprint Planning</h3>
        <p>Visual drag-and-drop task board with 7 stages, story points, sprint assignment, task dependencies, recurring tasks, and time tracking.</p>
        <ul class="ben-list">
          <li>7 pipeline stages: Backlog → Planning → In Progress → Review → Testing → Done → Blocked</li>
          <li>Story points, sprint velocity tracking, task dependencies</li>
          <li>Recurring tasks (daily, weekly, monthly) auto-spawned by scheduler</li>
          <li>Per-task time logging with timer and manual modes</li>
        </ul>
      </div>
      <div class="ben">
        <div class="ben-ico ben-ico-2">📢</div>
        <h3>Announcements</h3>
        <p>Workspace-wide broadcasts with pin support, read receipts, and push notifications.</p>
        <ul class="ben-list"><li>Pinned banner until dismissed</li><li>Read receipt tracking</li><li>Push to all workspace members</li></ul>
      </div>
      <div class="ben">
        <div class="ben-ico ben-ico-3">📝</div>
        <h3>Forms &amp; Intake</h3>
        <p>Public intake forms that auto-create support tickets on submission — no login needed for clients.</p>
        <ul class="ben-list"><li>5 field types: text, email, textarea, select, number</li><li>Auto-creates ticket with form data</li><li>View all submissions</li></ul>
      </div>
      <div class="ben">
        <div class="ben-ico ben-ico-4">💬</div>
        <h3>Channels &amp; DMs</h3>
        <p>Project channels with emoji reactions, threaded replies, file uploads, and private DMs — all real-time.</p>
        <ul class="ben-list"><li>Emoji reactions on all messages</li><li>Threaded replies — Slack-style</li><li>Markdown rich text</li></ul>
      </div>
      <div class="ben">
        <div class="ben-ico ben-ico-5">⏱️</div>
        <h3>Time Report</h3>
        <p>Complete time tracking with per-member, per-project breakdowns. Export to CSV for billing.</p>
        <ul class="ben-list"><li>Timer mode + manual log entry</li><li>Weekly, monthly, quarterly views</li><li>One-click CSV export</li></ul>
      </div>
      <div class="ben wide">
        <div class="ben-ico ben-ico-6">📊</div>
        <h3>Timeline &amp; Dev Productivity</h3>
        <p>Gantt-style timeline with health badges — plus a full developer productivity leaderboard with 0–100 score per engineer.</p>
        <ul class="ben-list">
          <li>Health badges: On Track · At Risk · Needs Attention · Overdue</li>
          <li>Productivity score from velocity, completion rate, and time logged</li>
          <li>Drill into any developer's full task history</li>
        </ul>
      </div>
      <div class="ben">
        <div class="ben-ico ben-ico-7">🏃</div>
        <h3>Sprint Management</h3>
        <p>Create and manage sprints with story points, velocity charts, and team capacity planning.</p>
        <ul class="ben-list"><li>Sprint states: Planning → Active → Completed</li><li>Story point burndown</li><li>Assign tasks from any view</li></ul>
      </div>
      <div class="ben">
        <div class="ben-ico ben-ico-8">🎯</div>
        <h3>Goals &amp; OKRs</h3>
        <p>Set company and team goals with key result tracking. Link results to sprints for auto-progress updates.</p>
        <ul class="ben-list"><li>Quarterly goal views</li><li>KR progress tracking</li><li>Dashboard health badges</li></ul>
      </div>
    </div>
  </div>
</section>

<!-- SECURITY -->
<section id="security" class="sec-section">
  <div class="wrap">
    <div class="centered">
      <div class="sec-tag" style="background:rgba(96,165,250,.15);color:#93c5fd;border:1px solid rgba(96,165,250,.25)">🔐 Security</div>
      <h2 class="sec-title" style="color:#fff">Enterprise-grade security,<br/>zero complexity</h2>
      <p class="sec-sub" style="color:rgba(255,255,255,.5)">Multi-layered security for teams that take data protection seriously — without the enterprise price tag.</p>
    </div>
    <div class="sec-grid">
      <div class="sec-card">
        <div class="sec-ico sec-ico-blue">🔑</div>
        <div><h4>TOTP Two-Factor Authentication</h4><p>Enable authenticator-based 2FA (Google Authenticator, Authy, 1Password). QR code setup, backup codes, instant activation. Prompted on every login when enabled. Admins can enforce workspace-wide.</p></div>
      </div>
      <div class="sec-card">
        <div class="sec-ico sec-ico-purple">🛡️</div>
        <div><h4>Role-Based Access Control</h4><p>6 granular roles — Admin, Manager, TeamLead, Developer, Tester, Viewer — each with fine-grained permissions across every feature. 26 permission toggles configurable from Settings.</p></div>
      </div>
      <div class="sec-card">
        <div class="sec-ico sec-ico-green">📧</div>
        <div><h4>OTP Email Verification</h4><p>Optional OTP-based login via email. Works with any SMTP provider — Gmail, SendGrid, Resend. Adds a second layer before accessing the dashboard.</p></div>
      </div>
      <div class="sec-card">
        <div class="sec-ico sec-ico-amber">🔗</div>
        <div><h4>API Key Management</h4><p>Generate scoped API keys for CI/CD pipelines and integrations. Keys are hashed at rest, shown only once on creation, and revocable at any time.</p></div>
      </div>
      <div class="sec-card">
        <div class="sec-ico sec-ico-purple">👁️</div>
        <div><h4>Guest Access</h4><p>Invite external collaborators as guests with access restricted to specific projects only — no workspace-wide access, no login required for intake forms.</p></div>
      </div>
      <div class="sec-card">
        <div class="sec-ico sec-ico-blue">🔐</div>
        <div><h4>Secure Session Management</h4><p>Server-side sessions with 7-day lifetime, HttpOnly cookies, bcrypt-hashed passwords, CORS protection, and automatic session invalidation on logout.</p></div>
      </div>
    </div>
  </div>
</section>

<!-- ROLE MATRIX -->
<section id="roles" class="role-section">
  <div class="wrap">
    <div class="centered">
      <div class="sec-tag">👥 Role Permissions</div>
      <h2 class="sec-title">Right access for every member</h2>
      <p class="sec-sub">Six pre-configured roles with 26 permission toggles — all customisable from Workspace Settings.</p>
    </div>
    <div class="role-table-wrap">
      <table class="role-table">
        <thead>
          <tr>
            <th class="feat-col">Feature</th>
            <th>👑 Admin</th><th>🗂 Manager</th><th>🧑‍💼 TeamLead</th><th>💻 Developer</th><th>🔍 Tester</th><th>👁 Viewer</th>
          </tr>
        </thead>
        <tbody>
          <tr class="cat-row"><td colspan="7">Project &amp; Task Management</td></tr>
          <tr><td class="feat-col">Create / Edit Projects</td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td class="pc-no">—</td><td class="pc-no">—</td><td class="pc-no">—</td></tr>
          <tr><td class="feat-col">Kanban Board &amp; Tasks</td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-own">Own tasks</span></td><td><span class="pchip pc-view">View</span></td><td><span class="pchip pc-view">View</span></td></tr>
          <tr><td class="feat-col">Sprint Planning</td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-own">Team sprints</span></td><td><span class="pchip pc-view">View</span></td><td><span class="pchip pc-view">View</span></td><td class="pc-no">—</td></tr>
          <tr><td class="feat-col">Time Tracking</td><td><span class="pchip pc-full">All members</span></td><td><span class="pchip pc-full">All members</span></td><td><span class="pchip pc-own">Team only</span></td><td><span class="pchip pc-own">Own only</span></td><td><span class="pchip pc-own">Own only</span></td><td class="pc-no">—</td></tr>
          <tr class="cat-row"><td colspan="7">Communication</td></tr>
          <tr><td class="feat-col">Post Announcements</td><td><span class="pchip pc-full">Post + pin</span></td><td><span class="pchip pc-full">Post + pin</span></td><td class="pc-no">—</td><td class="pc-no">—</td><td class="pc-no">—</td><td class="pc-no">—</td></tr>
          <tr><td class="feat-col">Channels + Reactions</td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-view">View</span></td></tr>
          <tr class="cat-row"><td colspan="7">AI Tools</td></tr>
          <tr><td class="feat-col">AI Standup Generator</td><td><span class="pchip pc-full">All members</span></td><td><span class="pchip pc-full">All members</span></td><td><span class="pchip pc-own">Team only</span></td><td><span class="pchip pc-own">Own only</span></td><td><span class="pchip pc-own">Own only</span></td><td class="pc-no">—</td></tr>
          <tr><td class="feat-col">AI Code Review</td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-full">Full</span></td><td><span class="pchip pc-view">View results</span></td><td class="pc-no">—</td></tr>
          <tr><td class="feat-col">AI Risk Predictor</td><td><span class="pchip pc-full">All projects</span></td><td><span class="pchip pc-full">All projects</span></td><td><span class="pchip pc-own">Team projects</span></td><td class="pc-no">—</td><td class="pc-no">—</td><td class="pc-no">—</td></tr>
          <tr class="cat-row"><td colspan="7">Security</td></tr>
          <tr><td class="feat-col">TOTP 2FA Setup</td><td><span class="pchip pc-full">Self + enforce</span></td><td><span class="pchip pc-own">Self only</span></td><td><span class="pchip pc-own">Self only</span></td><td><span class="pchip pc-own">Self only</span></td><td><span class="pchip pc-own">Self only</span></td><td><span class="pchip pc-own">Self only</span></td></tr>
          <tr><td class="feat-col">Workspace Settings</td><td><span class="pchip pc-full">Full</span></td><td class="pc-no">—</td><td class="pc-no">—</td><td class="pc-no">—</td><td class="pc-no">—</td><td class="pc-no">—</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

<!-- HOW IT WORKS -->
<section id="how">
  <div class="wrap centered">
    <div class="sec-tag">⚡ Getting Started</div>
    <h2 class="sec-title">Up and running in minutes</h2>
    <p class="sec-sub">No complex onboarding, no credit card, no setup fees. Productive the same day.</p>
    <div class="steps-grid">
      <div class="step-card"><div class="step-num">1</div><h3>Create workspace</h3><p>Register in under 60 seconds. Workspace is ready instantly — name it, set invite code, go.</p></div>
      <div class="step-card"><div class="step-num">2</div><h3>Invite your team</h3><p>Share your invite code or send email invites. Assign roles — Admin, Manager, Developer, Tester, or Viewer.</p></div>
      <div class="step-card"><div class="step-num">3</div><h3>Add your AI key</h3><p>Paste your Anthropic API key in Settings to unlock all four AI tools.</p></div>
      <div class="step-card"><div class="step-num">4</div><h3>Ship faster</h3><p>Plan sprints, track on Kanban, run AI standups, review code — all from one place.</p></div>
    </div>
  </div>
</section>

<!-- INTEGRATIONS -->
<section id="integrations" class="int-section">
  <div class="wrap centered">
    <div class="sec-tag">🔗 Integrations</div>
    <h2 class="sec-title">Connects to your stack</h2>
    <p class="sec-sub">Webhooks, API keys, public intake forms, and email via any SMTP — build your own automation layer.</p>
    <div class="int-grid">
      <div class="int-card"><div class="int-ico">📬</div><div class="int-n">SMTP Email</div><div class="int-d">Gmail, SendGrid, Resend, any provider</div></div>
      <div class="int-card"><div class="int-ico">🔗</div><div class="int-n">Webhooks</div><div class="int-d">HTTP POST on task, ticket, project events</div></div>
      <div class="int-card"><div class="int-ico">🔑</div><div class="int-n">REST API Keys</div><div class="int-d">Scoped tokens for external access</div></div>
      <div class="int-card"><div class="int-ico">📝</div><div class="int-n">Intake Forms</div><div class="int-d">Public URL → auto-ticket on submission</div></div>
      <div class="int-card"><div class="int-ico">🤖</div><div class="int-n">Anthropic Claude</div><div class="int-d">Bring your own key — full AI suite</div></div>
      <div class="int-card"><div class="int-ico">📱</div><div class="int-n">Push Notifications</div><div class="int-d">Web push — desktop and mobile</div></div>
      <div class="int-card"><div class="int-ico">📞</div><div class="int-n">Instant Meet</div><div class="int-d">WebRTC video calls — no third-party</div></div>
      <div class="int-card"><div class="int-ico">🌐</div><div class="int-n">Public Status Page</div><div class="int-d">Share project status via public URL</div></div>
    </div>
  </div>
</section>

<!-- TESTIMONIALS -->
<section class="testimonial-section">
  <div class="wrap centered">
    <div class="sec-tag">⭐ Trusted by Teams</div>
    <h2 class="sec-title">Built for real engineering teams</h2>
    <p class="sec-sub">From early-stage startups to established engineering orgs.</p>
    <div class="quote-grid">
      <div class="quote-card">
        <div class="q-stars">★★★★★</div>
        <p class="q-text">"The AI standup generator alone saves our team 30 minutes every morning. It pulls from actual task data, not just what people remember to type."</p>
        <div class="q-author"><div class="q-av" style="background:linear-gradient(135deg,#2563eb,#7c3aed)">AK</div><div><div class="q-name">Arjun Kumar</div><div class="q-role">Engineering Manager, FinTech startup</div></div></div>
      </div>
      <div class="quote-card">
        <div class="q-stars">★★★★★</div>
        <p class="q-text">"The code review bot caught a SQL injection vulnerability that passed our normal review. It's like having a senior engineer on call 24/7."</p>
        <div class="q-author"><div class="q-av" style="background:linear-gradient(135deg,#7c3aed,#db2777)">SR</div><div><div class="q-name">Sneha Reddy</div><div class="q-role">Lead Developer, SaaS company</div></div></div>
      </div>
      <div class="quote-card">
        <div class="q-stars">★★★★★</div>
        <p class="q-text">"Intake forms to tickets completely replaced our email support. Clients submit, tickets are created, assigned, and tracked — all automatically."</p>
        <div class="q-author"><div class="q-av" style="background:linear-gradient(135deg,#16a34a,#0891b2)">MP</div><div><div class="q-name">Meera Pillai</div><div class="q-role">Product Manager, Agency</div></div></div>
      </div>
    </div>
  </div>
</section>

<!-- FAQ -->
<section id="faq" class="faq-section">
  <div class="wrap centered">
    <div class="sec-tag">❓ FAQ</div>
    <h2 class="sec-title">Common questions</h2>
    <p class="sec-sub">Everything you need to know before getting started.</p>
    <div class="faq-grid" style="text-align:left">
      <div class="faq-item"><h4>Is VEWIT really free?</h4><p>Yes — free to start with no credit card. All core features are immediately available with no paywalls.</p></div>
      <div class="faq-item"><h4>How does the AI work?</h4><p>AI features use the Anthropic Claude API with your own key. No markup — you pay Anthropic directly at their rates.</p></div>
      <div class="faq-item"><h4>How is VEWIT different from Jira + Slack?</h4><p>One platform replaces both — channels, DMs, tickets, kanban, sprints, timeline, AI tools, and forms. No integration tax.</p></div>
      <div class="faq-item"><h4>Is my data secure?</h4><p>bcrypt passwords, HttpOnly cookies, TOTP 2FA, OTP email verification, API keys hashed at rest, automatic session invalidation on logout.</p></div>
      <div class="faq-item"><h4>Can external clients submit tickets?</h4><p>Yes — Intake Forms create a public URL. Clients submit without logging in; a ticket is automatically created with all form data.</p></div>
      <div class="faq-item"><h4>Can I control what each member sees?</h4><p>Yes — 6 role levels with 26 individual permission toggles. Guest access restricts users to specific projects only.</p></div>
    </div>
  </div>
</section>

<!-- CTA -->
<section class="cta-section">
  <div class="wrap cta-in">
    <div class="cta-tag">🚀 Ready to ship faster?</div>
    <h2>One platform.<br/>Every tool your team <span class="grad">needs.</span></h2>
    <p>Kanban boards, AI standup, code review, 2FA security — set up in 2 minutes, no credit card, no vendor lock-in.</p>
    <div class="cta-actions">
      <a href="/?action=register" class="btn btn-white btn-lg">Start Free — No Card Needed →</a>
      <a href="#features" class="btn btn-outline-white btn-lg">See All Features</a>
    </div>
    <div class="cta-trust">
      <div class="ct-it"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>Free forever</div>
      <div class="ct-it"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>TOTP 2FA built-in</div>
      <div class="ct-it"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>Bring your own AI key</div>
      <div class="ct-it"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>PostgreSQL backed</div>
    </div>
  </div>
</section>

<!-- FOOTER -->
<footer>
  <div class="wrap">
    <div class="footer-grid">
      <div class="footer-brand">
        <a href="/" class="logo" style="margin-bottom:12px;display:inline-flex;">
          <div class="logo-mark" style="width:28px;height:28px;border-radius:7px;"><svg width="14" height="14" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white"/><circle cx="51" cy="43" r="6" fill="white"/><circle cx="13" cy="43" r="6" fill="white"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/></svg></div>
          <span style="font-size:.96rem;color:#fff;">VEWIT</span>
        </a>
        <p>AI-powered team collaboration for modern engineering teams. Kanban, sprints, AI tools, 2FA — all in one place.</p>
      </div>
      <div class="footer-col"><h4>Product</h4><ul><li><a href="#features">Features</a></li><li><a href="#ai">AI Tools</a></li><li><a href="#security">Security</a></li><li><a href="#roles">Role Matrix</a></li></ul></div>
      <div class="footer-col"><h4>Platform</h4><ul><li><a href="/?action=register">Get Started</a></li><li><a href="/?action=login">Sign In</a></li><li><a href="#faq">FAQ</a></li><li><a href="#how">How it works</a></li></ul></div>
      <div class="footer-col"><h4>Features</h4><ul><li><a href="#features">Kanban Board</a></li><li><a href="#ai">AI Standup</a></li><li><a href="#ai">Code Review Bot</a></li><li><a href="#security">TOTP 2FA</a></li></ul></div>
    </div>
    <div class="footer-bottom">
      <div class="footer-copy">© 2025 VEWIT. All rights reserved. · <a href="https://www.vewit.in" style="color:rgba(96,165,250,.6)">vewit.in</a></div>
      <div class="footer-badges"><span class="fb">Free to start</span><span class="fb">TOTP 2FA</span><span class="fb">AI-powered</span><span class="fb">PostgreSQL</span></div>
    </div>
  </div>
</footer>

<script>
document.querySelectorAll('a[href^="#"]').forEach(a=>{
  a.addEventListener('click',e=>{const t=document.querySelector(a.getAttribute('href'));if(t){e.preventDefault();t.scrollIntoView({behavior:'smooth',block:'start'});}});
});
window.addEventListener('scroll',()=>{
  const n=document.getElementById('nav');
  n.style.background=window.scrollY>50?'rgba(255,255,255,.97)':'rgba(255,255,255,.85)';
  n.style.boxShadow=window.scrollY>50?'0 2px 20px rgba(0,0,0,.08)':'none';
});
const obs=new IntersectionObserver(entries=>{
  entries.forEach(e=>{if(e.isIntersecting){e.target.style.opacity='1';e.target.style.transform='translateY(0)';}});
},{threshold:0.08});
document.querySelectorAll('.ai-card,.ben,.sec-card,.int-card,.quote-card,.faq-item,.step-card,.stat-card').forEach(el=>{
  el.style.opacity='0';el.style.transform='translateY(20px)';
  el.style.transition='opacity .5s ease, transform .5s ease';
  obs.observe(el);
});
</script>
</body>
</html>

"""

HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>VEWIT — Sign In</title>
<link rel="icon" type="image/png" href="/icon-192.png"/>
<link rel="shortcut icon" href="/favicon.ico"/>
<meta name="description" content="Sign in to VEWIT — AI-powered team collaboration platform."/>
<meta name="robots" content="noindex"/>
<link rel="canonical" href="https://www.vewit.in/"/>
<link rel="manifest" href="/manifest.json"/>
<meta name="theme-color" content="#1d4ed8"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<meta name="apple-mobile-web-app-title" content="VEWIT"/>
<meta name="mobile-web-app-capable" content="yes"/>
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png"/>
<link rel="shortcut icon" type="image/png" href="/favicon.ico"/>
<link rel="apple-touch-icon" href="/icon-192.png"/>
<script>
// Favicon is now served as PNG via /favicon.ico — no JS override needed
</script>

<!-- ═══════════════════════════════════════════════════════
     SERVICE WORKER + WEB PUSH BOOTSTRAP
     Registers SW immediately so push works even when app
     is minimised or the tab is in the background.
     ═══════════════════════════════════════════════════════ -->
<script>
(function(){
'use strict';

window._pfSWReady = false;
window._pfPushSub = null;

if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js', {scope:'/'})
    .then(function(reg){
      window._pfSWReady = true;
      window._pfSWReg   = reg;

      navigator.serviceWorker.addEventListener('message', function(e){
        if(e.data && e.data.type === 'PF_NAVIGATE'){
          window.location.hash = e.data.url || '/';
          window.focus();
        }
        if(e.data && e.data.type === 'PF_NOTIF_CLICK'){
          window.focus();
          var tag=e.data.tag;
          if(tag&&window._pfNotifHandlers&&window._pfNotifHandlers[tag]){
            try{window._pfNotifHandlers[tag]();}catch(err){}
            delete window._pfNotifHandlers[tag];
          }
        }
      });

      _pfSetupPush(reg);
    })
    .catch(function(e){ console.warn('[PF] SW registration failed:', e); });
}

function _pfUrlB64(base64String){
  var padding='='.repeat((4-base64String.length%4)%4);
  var base64=(base64String+padding).replace(/-/g,'+').replace(/_/g,'/');
  var rawData=window.atob(base64);
  var outputArray=new Uint8Array(rawData.length);
  for(var i=0;i<rawData.length;++i) outputArray[i]=rawData.charCodeAt(i);
  return outputArray;
}

async function _pfSetupPush(reg){
  if(!('PushManager' in window)) return;

  var vapidKey='';
  try{
    var r=await fetch('/api/push/vapid-key',{credentials:'include'});
    var d=await r.json();
    vapidKey=d.publicKey||'';
  }catch(e){ return; }

  if(!vapidKey){
    return;
  }

  var perm = Notification.permission;
  if(perm==='default'){
    perm = await Notification.requestPermission();
  }
  if(perm!=='granted') return;

  var existingSub = await reg.pushManager.getSubscription();
  if(existingSub){
    window._pfPushSub = existingSub;
    _pfSendSubToServer(existingSub);
    return;
  }

  try{
    var sub = await reg.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: _pfUrlB64(vapidKey)
    });
    window._pfPushSub = sub;
    _pfSendSubToServer(sub);
  }catch(e){
    console.warn('[PF] Push subscribe failed:', e);
  }
}

function _pfSendSubToServer(sub){
  var subJson = sub.toJSON();
  fetch('/api/push/subscribe',{
    method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      endpoint: subJson.endpoint, keys: subJson.keys
    })
  }).catch(function(){});
}

window._pfLastPollTrigger = null;
document.addEventListener('visibilitychange', function(){
  if(document.visibilityState === 'visible'){
    if(typeof window._pfOnVisible === 'function'){
      window._pfOnVisible();
    }
  }
});

window._pfPushUnsubscribe = async function(){
  if(window._pfPushSub){
    try{
      await window._pfPushSub.unsubscribe();
      await fetch('/api/push/unsubscribe',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint:window._pfPushSub.endpoint})});
      window._pfPushSub = null;
    }catch(e){}
  }
};

})();
</script>

<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet"/>
<script>
(function(){
  var libs=[
    'https://cdnjs.cloudflare.com/ajax/libs/react/18.2.0/umd/react.production.min.js', 'https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.2.0/umd/react-dom.production.min.js', 'https://cdnjs.cloudflare.com/ajax/libs/prop-types/15.8.1/prop-types.min.js', 'https://cdnjs.cloudflare.com/ajax/libs/recharts/2.12.7/Recharts.js', 'https://unpkg.com/htm@3.1.1/dist/htm.js', ];
  function loadNext(i){
    if(i>=libs.length)return;
    var s=document.createElement('script');
    s.src=libs[i];
    s.crossOrigin='anonymous';
    s.onload=function(){loadNext(i+1);};
    s.onerror=function(){loadNext(i+1);};
    document.head.appendChild(s);
  }
  loadNext(0);
})();
</script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;width:100%;overflow:auto}
body{font-family:'Plus Jakarta Sans',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--tx);font-size:13px;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}

/* === DARK THEME (default) — precise HubSpot CRM workspace colours === */
:root{
  --bg:#eef2f7;
  --sf:#ffffff;
  --sf2:#f1f5f9;
  --sf3:#e2e8f0;
  --bd:rgba(15,23,42,0.12);
  --bd2:rgba(15,23,42,0.08);
  --tx:#0a0f1e;
  --tx2:#1e293b;
  --tx3:#475569;
  --sb:#0f172a;
  --sb2:#1e293b;
  --sb3:#334155;
  --sbt:#94a3b8;
  --ac:#1d4ed8;
  --ac2:#1e40af;
  --ac3:rgba(29,78,216,0.10);
  --ac4:rgba(29,78,216,0.06);
  --ac-tx:#ffffff;
  --rd:#b91c1c;
  --rd2:#dc2626;
  --gn:#15803d;
  --gn2:#16a34a;
  --am:#b45309;
  --cy:#0e7490;
  --pu:#6d28d9;
  --or:#c2410c;
  --pk:#be185d;
  --sh:0 1px 3px rgba(0,0,0,0.10),0 2px 8px rgba(0,0,0,0.07);
  --sh2:0 4px 16px rgba(0,0,0,0.12),0 8px 32px rgba(0,0,0,0.08);
  --sh3:0 0 0 1px var(--bd);
}

/* === LIGHT THEME — via .lm on body. Cards: white on #ebebeb canvas === */
.lm{
  --bg:#eef2f7;
  --sf:#ffffff;
  --sf2:#f1f5f9;
  --sf3:#e2e8f0;
  --bd:rgba(15,23,42,0.12);
  --bd2:rgba(15,23,42,0.08);
  --tx:#0a0f1e;
  --tx2:#1e293b;
  --tx3:#475569;
  --sb:#0f172a;
  --sb2:#1e293b;
  --sb3:#334155;
  --sbt:#94a3b8;
  --ac:#1d4ed8;
  --ac2:#1e40af;
  --ac3:rgba(29,78,216,0.10);
  --ac4:rgba(29,78,216,0.06);
  --ac-tx:#ffffff;
  --rd:#b91c1c;
  --rd2:#dc2626;
  --gn:#15803d;
  --gn2:#16a34a;
  --am:#b45309;
  --cy:#0e7490;
  --pu:#6d28d9;
  --or:#c2410c;
  --pk:#be185d;
  --sh:0 1px 3px rgba(0,0,0,0.10),0 2px 8px rgba(0,0,0,0.07);
  --sh2:0 4px 16px rgba(0,0,0,.10),0 8px 32px rgba(0,0,0,.07);
  --sh3:0 0 0 1px var(--bd);
}
/* === DARK THEME — .dm class === */
.dm{
  --bg:#0d1117;
  --sf:#161b22;
  --sf2:#21262d;
  --sf3:#2d333b;
  --bd:rgba(255,255,255,0.08);
  --bd2:rgba(255,255,255,0.05);
  --tx:#e6edf3;
  --tx2:#8b949e;
  --tx3:#484f58;
  --sb:#0d1117;
  --sb2:#161b22;
  --sb3:#21262d;
  --sbt:#6e7681;
  --ac:#3b82f6;
  --ac2:#2563eb;
  --ac3:rgba(59,130,246,0.15);
  --ac4:rgba(59,130,246,0.08);
  --ac-tx:#ffffff;
  --rd:#f85149;
  --rd2:#ff7b72;
  --gn:#3fb950;
  --gn2:#56d364;
  --am:#d29922;
  --cy:#39c5cf;
  --pu:#bc8cff;
  --or:#ffa657;
  --pk:#ff7eb3;
  --sh:0 1px 3px rgba(0,0,0,0.4),0 2px 8px rgba(0,0,0,0.3);
  --sh2:0 4px 16px rgba(0,0,0,0.5),0 8px 32px rgba(0,0,0,0.4);
  --sh3:0 0 0 1px rgba(255,255,255,0.08);
}

::-webkit-scrollbar{width:3px;height:3px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--bd);border-radius:8px}
::-webkit-scrollbar-thumb:hover{background:var(--tx3)}

input[type=date]{color-scheme:dark}
.lm input[type=date]{color-scheme:light}
input[type=date]::-webkit-calendar-picker-indicator{cursor:pointer;opacity:.45;filter:invert(1)}
.lm input[type=date]::-webkit-calendar-picker-indicator{filter:none;opacity:.5}

.card{background:var(--sf);border-radius:18px;padding:18px;border:1px solid var(--bd2);transition:border-color .15s}
.card:hover{border-color:var(--bd)}

.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:100px;border:none;cursor:pointer;font-size:12px;font-weight:600;transition:all .14s;white-space:nowrap;line-height:1;font-family:inherit;letter-spacing:.01em}
.bp{background:var(--ac);color:var(--ac-tx)!important}
.bp:hover{background:var(--ac2);transform:translateY(-1px);box-shadow:0 3px 14px rgba(170,255,0,.3)}
.bp:active{transform:translateY(0)}
.bp:disabled{opacity:.4;cursor:not-allowed;transform:none}
.bg{background:transparent;color:var(--tx2)!important;border:1px solid var(--bd)}
.bg:hover{background:var(--sf2);color:var(--tx)!important;border-color:var(--tx3)}
.brd{background:rgba(185,28,28,0.10);color:var(--rd)!important;border:1px solid rgba(255,68,68,.2)}
.brd:hover{background:rgba(255,68,68,.14)}
.bam{background:rgba(180,83,9,0.10);color:var(--am)!important;border:1px solid rgba(245,158,11,.25)}
.bam:hover{background:rgba(245,158,11,.16)}
.bdk{background:var(--sb);color:#fff!important;border:1px solid var(--bd)}
.bdk:hover{background:var(--sb2);transform:translateY(-1px)}
.bwh{background:#ffffff;color:#111111!important;border:none}
.bwh:hover{background:#e8e8e8;transform:translateY(-1px)}

.inp{background:var(--sf2);border:1px solid var(--bd);border-radius:10px;padding:9px 13px;color:var(--tx);font-size:13px;width:100%;outline:none;transition:border-color .14s,box-shadow .14s;font-family:inherit;line-height:1.4}
.inp:focus{border-color:var(--ac);box-shadow:0 0 0 3px rgba(170,255,0,.12)}
.inp::placeholder{color:var(--tx3)}
textarea.inp{resize:vertical;min-height:66px;line-height:1.5}
.sel{background:var(--sf2);border:1px solid var(--bd);border-radius:10px;padding:9px 30px 9px 13px;color:var(--tx);font-size:13px;width:100%;outline:none;cursor:pointer;font-family:inherit;-webkit-appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%23666' stroke-width='2.5'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center;transition:border-color .14s}
.sel:focus{border-color:var(--ac);outline:none;box-shadow:0 0 0 3px rgba(170,255,0,.12)}

.badge{display:inline-flex;align-items:center;padding:2px 7px;border-radius:100px;font-size:10px;font-weight:700;letter-spacing:.2px;text-transform:uppercase;line-height:1.5}
.nb{display:flex;align-items:center;gap:9px;padding:8px 11px;border-radius:10px;cursor:pointer;color:var(--tx2);font-size:12px;font-weight:500;transition:all .12s;border:none;background:transparent;width:100%;text-align:left;position:relative}
.nb:hover{background:var(--sf2);color:var(--tx)}
.nb.act{background:var(--ac);color:var(--ac-tx)!important;font-weight:600}
.nb.act svg{stroke:var(--ac-tx)!important}

.ov{position:fixed;inset:0;background:rgba(0,0,0,.7);display:flex;align-items:center;justify-content:center;z-index:2000;padding:16px;backdrop-filter:blur(14px)}
.mo{background:var(--sf);border-radius:22px;padding:26px;width:100%;max-width:640px;max-height:94vh;overflow-y:auto;box-shadow:var(--sh2);border:1px solid var(--bd2)}
.mo-xl{max-width:920px}

.tkc{background:var(--sf);border-radius:16px;padding:14px;cursor:pointer;transition:all .16s;border:1px solid var(--bd2)}
.tkc:hover{transform:translateY(-2px);box-shadow:var(--sh2);border-color:var(--bd)}

.prog{height:3px;background:var(--bd);border-radius:100px;overflow:hidden}
.progf{height:100%;border-radius:100px;transition:width .5s ease}

.tb{padding:5px 13px;border-radius:100px;cursor:pointer;font-size:11px;font-weight:600;border:1px solid var(--bd);background:transparent;color:var(--tx2);transition:all .12s;font-family:inherit;letter-spacing:.01em;white-space:nowrap}
.tb.act{background:var(--tx);color:var(--bg)!important;border-color:transparent}
.lm .tb.act{background:#111111;color:#ffffff!important;border-color:#111111}
.tb:hover:not(.act){background:var(--sf2);color:var(--tx);border-color:var(--tx3)}

.av{border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0;letter-spacing:-.3px}

.lbl{color:var(--tx3);font-size:10px;margin-bottom:4px;display:block;text-transform:uppercase;letter-spacing:.8px;font-weight:700}
.chip{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:100px;font-size:11px;font-weight:600;background:var(--sf2);border:1px solid var(--bd);color:var(--tx2);cursor:pointer;transition:all .12s}
.chip:hover{border-color:var(--ac);color:var(--ac);background:var(--ac3)}
.chip.on{background:var(--ac3);border-color:var(--ac);color:var(--ac)}

.drop-zone{border:1.5px dashed var(--bd);border-radius:12px;padding:20px;text-align:center;cursor:pointer;transition:all .16s;color:var(--tx3);font-size:13px}
.drop-zone:hover,.drop-zone.over{border-color:var(--ac);color:var(--ac);background:var(--ac4)}

@keyframes fi{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.fi{animation:fi .18s ease forwards}
@keyframes sp{to{transform:rotate(360deg)}}
.spin{display:inline-block;width:14px;height:14px;border:2px solid var(--bd);border-top-color:var(--ac);border-radius:50%;animation:sp .5s linear infinite;vertical-align:middle}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.pulse{animation:pulse 1.4s ease-in-out infinite}
@keyframes slideUp{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:translateY(0)}}

  .ai-btn{position:fixed;bottom:80px;right:20px;z-index:1800;width:46px;height:46px;border-radius:50%;background:var(--ac);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:19px;box-shadow:0 4px 18px var(--ac3);transition:all .18s}
.ai-btn:hover{transform:scale(1.1);box-shadow:0 6px 26px var(--ac3)}
.ai-panel{position:fixed;bottom:136px;right:20px;z-index:1800;width:370px;height:520px;background:var(--sf);border-radius:20px;display:flex;flex-direction:column;box-shadow:var(--sh2);overflow:hidden;border:1px solid var(--bd);animation:slideUp .18s ease}
.ai-msg-user{align-self:flex-end;background:var(--ac);color:var(--ac-tx);border-radius:16px 16px 4px 16px;padding:9px 13px;font-size:12px;max-width:80%;line-height:1.5;font-weight:600}
.ai-msg-ai{align-self:flex-start;background:var(--sf2);color:var(--tx);border-radius:16px 16px 16px 4px;padding:9px 13px;font-size:12px;max-width:90%;line-height:1.55;white-space:pre-wrap;border:1px solid var(--bd2)}
.ai-action{background:var(--ac3);border:1px solid rgba(170,255,0,.2);border-radius:8px;padding:7px 10px;font-size:10px;color:var(--ac);font-family:monospace;margin-top:4px}

.snb{width:38px;height:38px;border-radius:10px;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;background:transparent;color:var(--sbt);transition:all .12s;flex-shrink:0}
.snb:hover{background:rgba(37,99,235,0.12);color:#93c5fd}
.snb.act{background:var(--ac)}
.snb.act svg{stroke:var(--ac-tx)!important}

.pri-hi{background:rgba(255,68,68,.1);color:var(--rd);border:1px solid rgba(255,68,68,.2)}
.pri-md{background:rgba(167,139,250,.1);color:var(--pu);border:1px solid rgba(167,139,250,.2)}
.pri-lo{background:rgba(34,211,238,.1);color:var(--cy);border:1px solid rgba(34,211,238,.2)}
.pri-gn{background:rgba(62,207,110,.1);color:var(--gn);border:1px solid rgba(62,207,110,.2)}

.stat-num{font-family:'Space Grotesk',sans-serif;font-weight:700;line-height:1;letter-spacing:-1.5px}
.int-dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex-shrink:0}

.sched-pill{display:flex;align-items:center;gap:8px;padding:4px 12px 4px 4px;border-radius:100px;background:var(--sf2);border:1px solid var(--bd);cursor:pointer;transition:all .13s;flex-shrink:0}
.sched-pill:hover{border-color:var(--tx3)}
.sched-pill.active{background:var(--ac);border-color:var(--ac)}
.sched-pill.active span{color:var(--ac-tx)!important}

.status-pill{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:100px;font-size:10px;font-weight:600;border:1px solid var(--bd);background:var(--sf2);color:var(--tx2);cursor:pointer;transition:all .12s}
.status-pill:hover{border-color:var(--tx3);color:var(--tx)}

.section-title{font-family:'Space Grotesk',sans-serif;font-size:17px;font-weight:700;color:var(--tx);letter-spacing:-.4px}
.section-count{font-size:11px;font-weight:600;color:var(--tx3);padding:2px 7px;border-radius:100px;background:var(--sf2);border:1px solid var(--bd)}

.hs-card{background:var(--sf);border:1px solid var(--bd2);border-radius:18px;padding:16px;transition:all .16s;position:relative;overflow:hidden}
.hs-card:hover{border-color:var(--bd);transform:translateY(-1px);box-shadow:var(--sh)}
.hs-card-accent{position:absolute;top:0;left:0;width:100%;height:3px;border-radius:18px 18px 0 0}

/* ═══════════════════════════════════════════════════════════════════
   IN-APP TOAST / BANNER NOTIFICATIONS
   Stacks from top-right, auto-dismisses, click to navigate
   ═══════════════════════════════════════════════════════════════════ */
.toast-stack{position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:9px;pointer-events:none;max-width:360px;width:360px}
.toast{pointer-events:all;display:flex;align-items:flex-start;gap:11px;padding:13px 14px;border-radius:14px;border:1px solid var(--bd);background:var(--sf);box-shadow:0 4px 24px rgba(0,0,0,.55),0 1px 4px rgba(0,0,0,.3);cursor:pointer;transition:all .2s;position:relative;overflow:hidden}
.lm .toast{box-shadow:0 4px 24px rgba(0,0,0,.18),0 1px 4px rgba(0,0,0,.1)}
.toast:hover{transform:translateX(-3px);box-shadow:0 6px 28px rgba(0,0,0,.65)}
.toast-bar{position:absolute;bottom:0;left:0;height:2px;border-radius:0 0 14px 14px;transition:width linear}
.toast-icon{width:34px;height:34px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}
.toast-body{flex:1;min-width:0}
.toast-title{font-size:12px;font-weight:700;color:var(--tx);line-height:1.3;margin-bottom:2px}
.toast-msg{font-size:11px;color:var(--tx2);line-height:1.4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.toast-time{font-size:9px;color:var(--tx3);margin-top:3px;font-family:monospace}
.toast-close{width:20px;height:20px;border-radius:6px;border:none;background:transparent;color:var(--tx3);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0;transition:all .12s;padding:0}
.toast-close:hover{background:var(--sf2);color:var(--tx)}
@keyframes floatUp{0%{opacity:1;transform:translateY(0) scale(1)}70%{opacity:.8;transform:translateY(-60px) scale(1.2)}100%{opacity:0;transform:translateY(-110px) scale(.8)}}
@keyframes toastIn{from{opacity:0;transform:translateX(100%)}to{opacity:1;transform:translateX(0)}}

/* ── Utility classes for repeated inline styles ── */
.tx3-11{font-size:11px;color:var(--tx3)}
.tx3-10{font-size:10px;color:var(--tx3)}
.mono-10{font-size:10px;color:var(--tx3);font-family:monospace}
.id-badge{display:inline-flex;align-items:center;font-size:10px;font-family:'JetBrains Mono',monospace,sans-serif;font-weight:700;padding:2px 7px;border-radius:5px;letter-spacing:.02em;white-space:nowrap;flex-shrink:0}
.id-task{background:rgba(29,78,216,0.10);color:#1d4ed8;border:1px solid rgba(29,78,216,0.2)}
.id-ticket{background:rgba(194,65,12,0.10);color:#c2410c;border:1px solid rgba(194,65,12,0.2)}
.id-subtask{background:rgba(71,85,105,0.10);color:#475569;border:1px solid rgba(71,85,105,0.2)}
.id-epic{background:rgba(109,40,217,0.10);color:#6d28d9;border:1px solid rgba(109,40,217,0.2)}
.f1-mw0{flex:1;min-width:0}
.jc-sb{display:flex;justify-content:space-between}
.fc-g8{display:flex;flex-direction:column;gap:8px}
.fc-g4{display:flex;flex-direction:column;gap:4px}
.fc-g3{display:flex;flex-direction:column;gap:3px}

@keyframes toastOut{from{opacity:1;transform:translateX(0)}to{opacity:0;transform:translateX(110%)}}
.toast{animation:toastIn .25s cubic-bezier(.34,1.56,.64,1) forwards}
.toast.leaving{animation:toastOut .2s ease forwards}
@keyframes pageEnter{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.page-enter{animation:pageEnter .22s ease forwards}
@keyframes teamSwitch{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:translateX(0)}}
.team-switch-enter{animation:teamSwitch .25s ease forwards}
@keyframes loadBar{0%{width:0%;margin-left:0}60%{width:80%}100%{width:100%;margin-left:120%}}
</style></head><body>

<div id="root"></div>
  <div id="LE" style="display:none;color:#dc2626;font-size:12px;position:fixed;bottom:20px;left:50%;transform:translateX(-50%);max-width:360px;padding:12px 16px;background:rgba(220,38,38,.06);border:1px solid rgba(220,38,38,.2);border-radius:10px;text-align:center;z-index:9999;"></div>
<script>
window.onerror=function(m,s,l,c,e){var el=document.getElementById('LE');if(el){el.style.display='block';el.innerHTML='<b>Load Error</b><br>'+(e?e.message:m);}};
</script>
<script>
(function(){
'use strict';
function waitForLibs(cb, attempts){
  attempts = attempts||0;
  if(typeof React!=='undefined' && typeof ReactDOM!=='undefined' && typeof htm!=='undefined' && typeof Recharts!=='undefined'){
    cb(); return;
  }
  if(attempts > 150){ // 15 seconds timeout
    var el=document.getElementById('LE');
    if(el){el.style.display='block';el.innerHTML='<b>Failed to load libraries.</b> Check your internet connection and refresh.';}
    return;
  }
  setTimeout(function(){waitForLibs(cb, attempts+1);}, 100);
}
window._pfStartApp = function(){
const html=htm.bind(React.createElement);
const {useState,useEffect,useRef,useCallback,useMemo}=React;
const RC=Recharts;

const api={
  get:u=>fetch(u,{credentials:'include'}).then(r=>r.json()).catch(()=>({})), post:(u,b)=>fetch(u,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json()).catch(()=>({})), put:(u,b)=>fetch(u,{method:'PUT',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json()).catch(()=>({})), del:u=>fetch(u,{method:'DELETE',credentials:'include'}).then(r=>r.json()).catch(()=>({})), upload:(u,fd)=>fetch(u,{method:'POST',credentials:'include',body:fd}).then(r=>r.json()).catch(()=>({})),
};

const STAGES={
  backlog:    {label:'Backlog', color:'#94a3b8',bg:'rgba(148,163,184,.13)'}, planning:   {label:'Planning', color:'var(--cy)',bg:'rgba(96,165,250,.13)'}, development:{label:'Dev', color:'#9b8ef4',bg:'rgba(167,139,250,.13)'}, code_review:{label:'Review', color:'#22d3ee',bg:'rgba(34,211,238,.13)'}, testing:    {label:'Testing', color:'var(--pu)',bg:'rgba(251,191,36,.13)'}, uat:        {label:'UAT', color:'#f472b6',bg:'rgba(244,114,182,.13)'}, release:    {label:'Release', color:'#fb923c',bg:'rgba(251,146,60,.13)'}, production: {label:'Production',color:'#34d399',bg:'rgba(52,211,153,.13)'}, completed:  {label:'Completed', color:'#4ade80',bg:'rgba(74,222,128,.13)'}, blocked:    {label:'Blocked', color:'var(--rd2)',bg:'rgba(248,113,113,.13)'},
};
const KCOLS=['backlog','planning','development','code_review','testing','uat','release','production','completed','blocked'];
const PRIS={critical:{label:'Critical',color:'var(--rd)',sym:'🔴'},high:{label:'High',color:'var(--rd2)',sym:'↑'},medium:{label:'Medium',color:'var(--pu)',sym:'→'},low:{label:'Low',color:'var(--cy)',sym:'↓'}};
const ROLES=['Admin','Manager','TeamLead','Developer','Tester','Viewer'];
const JOIN_ROLES=['Developer','Tester','Viewer']; // roles available when joining via invite code
const PAL=['#7c3aed','#2563eb','#059669','#d97706','#dc2626','#ec4899','#0891b2','#aaff00'];
const fmtD=d=>{if(!d)return'—';try{return new Date(d).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});}catch(e){return d;}};
const ago=iso=>{const m=Math.floor((Date.now()-new Date(iso))/60000);if(m<1)return'just now';if(m<60)return m+'m ago';if(m<1440)return Math.floor(m/60)+'h ago';return Math.floor(m/1440)+'d ago';};
const safe=a=>(Array.isArray(a)?a:[]);

function Av({u,size=32}){
  const imgSrc=(u&&u.avatar_data&&u.avatar_data.startsWith('data:image'))?u.avatar_data:
               (u&&u.avatar&&u.avatar.length>10&&u.avatar.startsWith('data:image'))?u.avatar:null;
  if(imgSrc){
    return html`<img src=${imgSrc} class="av" style=${{width:size,height:size,objectFit:'cover',borderRadius:'50%',border:'2px solid rgba(0,0,0,.06)'}}/>`;
  }
  const initials=(u&&u.avatar&&u.avatar.length<=4)?u.avatar:(u&&u.name?u.name.split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase():'?');
  return html`<div class="av" style=${{width:size,height:size,background:(u&&u.color)||'#2563eb',color:'#fff',fontSize:Math.max(9,Math.floor(size*.33))}}>
    ${initials}
  </div>`;
}
function SP({s}){
  const d=STAGES[s]||{label:s,color:'#94a3b8',bg:'rgba(148,163,184,.13)'};
  return html`<span class="badge" style=${{color:d.color,background:d.bg}}>${d.label}</span>`;
}
function PB({p}){
  const d=PRIS[p]||{label:p,color:'#94a3b8',sym:'·'};
  const isC=p==='critical';
  return html`<span class="badge" style=${{color:d.color,background:d.color+'22',boxShadow:isC?'0 0 6px '+d.color+'55':'none',animation:isC?'pulse 1.5s infinite':'none'}}>${d.sym} ${d.label}</span>`;
}
function Prog({pct,color}){
  return html`<div class="prog"><div class="progf" style=${{width:Math.min(100,Math.max(0,pct||0))+'%',background:color||'var(--ac)'}}></div></div>`;
}

/* ─── Shared hooks & utilities ─────────────────────────────────────────────── */

// usePagedApi — generic paginated data fetcher
function usePagedApi(url, deps=[]){
  const [items,setItems]=useState([]);
  const [total,setTotal]=useState(0);
  const [loading,setLoading]=useState(false);
  const [page,setPage]=useState(1);
  const load=useCallback(async(p=1)=>{
    if(!url)return;
    setLoading(true);
    try{
      const sep=url.includes('?')?'&':'?';
      const r=await api.get(url+sep+'page='+p);
      if(r?.items){setItems(p===1?r.items:[...items,...r.items]);setTotal(r.total||0);}
      else if(Array.isArray(r)){setItems(r);setTotal(r.length);}
    }catch(e){}
    setLoading(false);
  },[url]);
  useEffect(()=>{setPage(1);load(1);},[url,...deps]);
  const loadMore=()=>{const next=page+1;setPage(next);load(next);};
  const hasMore=items.length<total;
  return {items,total,loading,load:()=>load(1),loadMore,hasMore};
}

// useDebouncedValue — debounce a frequently changing value
function useDebouncedValue(value, delay=300){
  const [dv,setDv]=useState(value);
  useEffect(()=>{const t=setTimeout(()=>setDv(value),delay);return()=>clearTimeout(t);},[value,delay]);
  return dv;
}

// safeJSON — safely parse JSON with a default
function safeJSON(str, def=[]){
  try{return JSON.parse(str||JSON.stringify(def));}catch{return def;}
}

// fmtMins — format minutes as "2h 15m"
function fmtMins(m){
  if(!m||m===0)return '0m';
  const h=Math.floor(m/60);const min=m%60;
  return h>0?(min>0?h+'h '+min+'m':h+'h'):min+'m';
}

// fmtDate — human-friendly relative date
function fmtDate(dateStr){
  if(!dateStr)return '';
  const d=new Date(dateStr);const now=new Date();
  const diff=Math.floor((now-d)/86400000);
  if(diff===0)return 'Today';if(diff===1)return 'Yesterday';
  if(diff<7)return diff+'d ago';
  return d.toLocaleDateString('en-US',{month:'short',day:'numeric'});
}

// Priority colors — single source of truth used across all components
const PRIO_COLOR={critical:'#ef4444',high:'#f97316',medium:'#eab308',low:'#22c55e'};
const PRIO_BG   ={critical:'rgba(239,68,68,.1)',high:'rgba(249,115,22,.1)',medium:'rgba(234,179,8,.1)',low:'rgba(34,197,94,.1)'};
const STAGE_COLOR={backlog:'#64748b',planning:'#8b5cf6',inprogress:'#0ea5e9',review:'#f59e0b',testing:'#06b6d4',completed:'#22c55e',blocked:'#ef4444'};
const STAGE_BG   ={backlog:'rgba(100,116,139,.1)',planning:'rgba(139,92,246,.1)',inprogress:'rgba(14,165,233,.1)',review:'rgba(245,158,11,.1)',testing:'rgba(6,182,212,.1)',completed:'rgba(34,197,94,.1)',blocked:'rgba(239,68,68,.1)'};

// PriorityBadge — reusable priority pill
function PriorityBadge({priority,size=10}){
  if(!priority)return null;
  return html`<span style=${{fontSize:size,padding:'1px 6px',borderRadius:3,fontWeight:700,
    background:PRIO_BG[priority]||'rgba(100,116,139,.1)',
    color:PRIO_COLOR[priority]||'#64748b'}}>${priority}</span>`;
}

// StageBadge — reusable stage pill
function StageBadge({stage,size=10}){
  if(!stage)return null;
  const lbl={backlog:'Backlog',planning:'Planning',inprogress:'In Progress',review:'Review',testing:'Testing',completed:'Done',blocked:'Blocked'};
  return html`<span style=${{fontSize:size,padding:'1px 6px',borderRadius:3,fontWeight:700,
    background:STAGE_BG[stage]||'rgba(100,116,139,.1)',
    color:STAGE_COLOR[stage]||'#64748b'}}>${lbl[stage]||stage}</span>`;
}

// EmptyState — reusable empty state component
function EmptyState({icon='📭',title,sub,action,actionLabel}){
  return html`<div style=${{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',padding:'48px 24px',color:'var(--tx3)',textAlign:'center',gap:10}}>
    <div style=${{fontSize:40}}>${icon}</div>
    <div style=${{fontSize:15,fontWeight:600,color:'var(--tx2)'}}>${title}</div>
    ${sub?html`<div style=${{fontSize:13,maxWidth:280,lineHeight:1.6}}>${sub}</div>`:null}
    ${action?html`<button class="btn bp" style=${{fontSize:13,marginTop:6}} onClick=${action}>${actionLabel||'Get started'}</button>`:null}
  </div>`;
}

// LoadingSpinner — reusable loader
function LoadingSpinner({size=20,message=''}){
  return html`<div style=${{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:10,padding:32,color:'var(--tx3)'}}>
    <div style=${{width:size,height:size,border:'2px solid var(--bd)',borderTop:'2px solid var(--ac)',borderRadius:'50%',animation:'sp .7s linear infinite'}}></div>
    ${message?html`<div style=${{fontSize:12}}>${message}</div>`:null}
  </div>`;
}

// TaskCard — reusable task card for Kanban + list views
function TaskCard({task,users,projects,onClick,compact=false}){
  const u=safe(users||[]).find(x=>x.id===task.assignee);
  const p=safe(projects||[]).find(x=>x.id===task.project);
  return html`<div onClick=${onClick}
    style=${{background:'var(--bg)',border:'1px solid var(--bd)',borderRadius:8,
      padding:compact?'7px 10px':'10px 12px',cursor:'pointer',transition:'all .12s',
      borderLeft:'2px solid '+(STAGE_COLOR[task.stage]||'#64748b')}}
    onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.boxShadow='0 2px 12px rgba(37,99,235,.08)';}}
    onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.boxShadow='none';}}>
    ${p&&!compact?html`<div style=${{fontSize:10,color:p.color||'var(--ac)',fontWeight:600,marginBottom:3}}>${p.name}</div>`:null}
    <div style=${{fontSize:compact?12:13,fontWeight:600,color:'var(--tx)',lineHeight:1.35,marginBottom:5}}>${task.title}</div>
    <div style=${{display:'flex',alignItems:'center',gap:5,flexWrap:'wrap'}}>
      <${StageBadge} stage=${task.stage}/>
      <${PriorityBadge} priority=${task.priority}/>
      ${task.due?html`<span style=${{fontSize:10,color:task.due<new Date().toISOString().slice(0,10)&&task.stage!=='completed'?'#ef4444':'var(--tx3)'}}>📅 ${task.due.slice(5)}</span>`:null}
      ${u?html`<span style=${{marginLeft:'auto',width:20,height:20,borderRadius:'50%',background:'var(--ac)',color:'#fff',fontSize:9,fontWeight:700,display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}} title=${u.name}>${u.name.slice(0,2).toUpperCase()}</span>`:null}
    </div>
    ${task.pct>0&&!compact?html`<div style=${{height:2,background:'var(--sf2)',borderRadius:1,marginTop:6}}>
      <div style=${{height:2,width:task.pct+'%',background:task.pct===100?'#22c55e':'var(--ac)',borderRadius:1,transition:'width .3s'}}></div>
    </div>`:null}
  </div>`;
}


class ErrorBoundary extends React.Component{
  constructor(p){super(p);this.state={err:null,info:null};}
  static getDerivedStateFromError(e){return{err:e};}
  componentDidCatch(e,info){this.setState({info});}
  render(){
    if(this.state.err)return html`
      <div style=${{padding:40,textAlign:'center',color:'var(--rd)',maxWidth:520,margin:'0 auto'}}>
        <div style=${{fontSize:32,marginBottom:12}}>⚠️</div>
        <div style=${{fontSize:15,fontWeight:700,color:'var(--tx)',marginBottom:8}}>Something went wrong</div>
        <div style=${{fontSize:12,color:'var(--rd)',fontFamily:'monospace',background:'rgba(248,113,113,.08)',padding:'10px 14px',borderRadius:8,marginBottom:16,textAlign:'left',wordBreak:'break-word',maxHeight:120,overflowY:'auto'}}>
          ${this.state.err.message}
        </div>
        <button class="btn bp" onClick=${()=>this.setState({err:null,info:null})}>Retry</button>
      </div>`;
    return this.props.children;
  }
}

/* ─── AuthScreen — Professional Tech Design ───────────────────────────────── */
function AuthScreen({onLogin}){
  const _initTab=(()=>{try{const p=new URLSearchParams(window.location.search);return p.get('action')==='register'?'register':'login';}catch{return 'login';}})();
  const _setTab=(t)=>{
    setTabRaw(t);
    setEmail('');setPw('');setErr('');setName('');setWsName('');setInviteCode('');
    try{history.replaceState(null,'','/?action='+t);}catch{}
  };
  const [tab,setTabRaw]=useState(_initTab);const setTab=_setTab;
  const [regMode,setRegMode]=useState('create');
  const [wsName,setWsName]=useState('');
  const [inviteCode,setInviteCode]=useState('');
  const [name,setName]=useState('');
  const [email,setEmail]=useState('');
  const [pw,setPw]=useState('');
  const [role,setRole]=useState('Developer');
  const [showPw,setShowPw]=useState(false);
  const [err,setErr]=useState('');
  const [busy,setBusy]=useState(false);
  const [otpStep,setOtpStep]=useState(false);
  const [otpEmail,setOtpEmail]=useState('');
  const [otpCode,setOtpCode]=useState('');
  const [totpLoginStep,setTotpLoginStep]=useState(false);
  const [totpLoginCode,setTotpLoginCode]=useState('');
  const totpLoginRefs=[useRef(),useRef(),useRef(),useRef(),useRef(),useRef()];
  const [otpResendCd,setOtpResendCd]=useState(0);
  const otpRefs=[useRef(),useRef(),useRef(),useRef(),useRef(),useRef()];
  const cvRef=useRef(null);

  useEffect(()=>{
    if(otpResendCd<=0)return;
    const t=setTimeout(()=>setOtpResendCd(c=>c-1),1000);
    return()=>clearTimeout(t);
  },[otpResendCd]);
  useEffect(()=>{
    if(otpStep&&otpRefs[0].current)otpRefs[0].current.focus();
  },[otpStep]);

  useEffect(()=>{
    const cv=cvRef.current;if(!cv)return;
    const ctx=cv.getContext('2d');
    let id,frame=0;
    const resize=()=>{cv.width=cv.offsetWidth||700;cv.height=cv.offsetHeight||900;};
    resize();
    const ro=new ResizeObserver(()=>{resize();});ro.observe(cv);

    const waveConfigs=[
      {spd:.00042,amp:.048,freq:2.2,ph:0, fill:'rgba(147,197,253,',stroke:'rgba(96,165,250,', base:.50}, {spd:.00031,amp:.040,freq:1.8,ph:2.0, fill:'rgba(96,165,250,', stroke:'rgba(59,130,246,', base:.56}, {spd:.00051,amp:.032,freq:2.7,ph:4.1, fill:'rgba(59,130,246,', stroke:'rgba(37,99,235,', base:.61}, {spd:.00024,amp:.026,freq:1.5,ph:1.2, fill:'rgba(37,99,235,', stroke:'rgba(29,78,216,', base:.66}, {spd:.00058,amp:.020,freq:3.1,ph:3.4, fill:'rgba(29,78,216,', stroke:'rgba(30,64,175,', base:.70}, ];

    const pts=Array.from({length:28},()=>({
      x:Math.random(),y:Math.random()*.5, vx:(Math.random()-.5)*.00012,vy:(Math.random()-.5)*.00010, r:.6+Math.random()*1.2,ph:Math.random()*Math.PI*2,sp:.006+Math.random()*.008, }));

    const bubbles=Array.from({length:16},()=>({
      x:Math.random(),y:.45+Math.random()*.45, r:1.5+Math.random()*4,vy:-.00025-Math.random()*.0003, ph:Math.random()*Math.PI*2,sp:.007+Math.random()*.005,a:.06+Math.random()*.10, }));

    const sparks=Array.from({length:24},()=>({
      x:Math.random(),yb:.46+Math.random()*.18, ph:Math.random()*Math.PI*2,sp:.018+Math.random()*.014,sz:.6+Math.random()*1.2, }));

    const draw=()=>{
      const W=cv.width,H=cv.height;
      frame++;const t=frame*.016;

      const bg=ctx.createLinearGradient(0,0,0,H);
      bg.addColorStop(0,'#ffffff');
      bg.addColorStop(.30,'#f0f9ff');
      bg.addColorStop(.52,'#dbeafe');
      bg.addColorStop(.75,'#bfdbfe');
      bg.addColorStop(1,'#93c5fd');
      ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);

      const sg=ctx.createRadialGradient(W*.78,H*.08,0,W*.78,H*.08,W*.5);
      sg.addColorStop(0,'rgba(254,240,138,0.45)');
      sg.addColorStop(.3,'rgba(253,224,71,0.15)');
      sg.addColorStop(1,'rgba(0,0,0,0)');
      ctx.fillStyle=sg;ctx.fillRect(0,0,W,H);

      const ta=ctx.createRadialGradient(W*.5,0,0,W*.5,0,H*.55);
      ta.addColorStop(0,'rgba(219,234,254,0.4)');
      ta.addColorStop(1,'rgba(0,0,0,0)');
      ctx.fillStyle=ta;ctx.fillRect(0,0,W,H);

      pts.forEach(p=>{
        p.x+=p.vx;p.y+=p.vy;p.ph+=p.sp;
        if(p.x<0)p.x=1;if(p.x>1)p.x=0;if(p.y<0)p.y=.5;if(p.y>.5)p.y=0;
        const a=(.08+Math.sin(p.ph)*.06)*(1-p.y/.5);
        ctx.beginPath();ctx.arc(p.x*W,p.y*H,p.r,0,Math.PI*2);
        ctx.fillStyle='rgba(59,130,246,'+a+')';ctx.fill();
      });

      ctx.lineWidth=.4;
      for(let i=0;i<pts.length;i++)for(let j=i+1;j<pts.length;j++){
        const dx=(pts[i].x-pts[j].x)*W,dy=(pts[i].y-pts[j].y)*H;
        const d=Math.sqrt(dx*dx+dy*dy);
        if(d<W*.14){
          ctx.strokeStyle='rgba(147,197,253,'+(0.06*(1-d/(W*.14)))+')';
          ctx.beginPath();ctx.moveTo(pts[i].x*W,pts[i].y*H);ctx.lineTo(pts[j].x*W,pts[j].y*H);ctx.stroke();
        }
      }

      const hy=H*.48;
      const hl=ctx.createLinearGradient(0,hy,W,hy);
      hl.addColorStop(0,'rgba(255,255,255,0)');hl.addColorStop(.3,'rgba(255,255,255,0.4)');
      hl.addColorStop(.5,'rgba(219,234,254,0.6)');hl.addColorStop(.7,'rgba(255,255,255,0.4)');
      hl.addColorStop(1,'rgba(255,255,255,0)');
      ctx.fillStyle=hl;ctx.fillRect(0,hy-1,W,3);

      waveConfigs.forEach((w,wi)=>{
        const ph=t*w.spd*1000+w.ph;
        ctx.beginPath();ctx.moveTo(0,H);
        for(let x=0;x<=W;x+=2){
          const xn=x/W;
          const y=H*(w.base+Math.sin(xn*Math.PI*w.freq+ph)*w.amp
            +Math.sin(xn*Math.PI*w.freq*1.7+ph*.65)*(w.amp*.3)
            +Math.sin(xn*Math.PI*w.freq*.9+ph*1.4)*(w.amp*.18));
          x===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
        }
        ctx.lineTo(W,H);ctx.closePath();
        ctx.fillStyle=w.fill+[.14,.12,.11,.10,.09][wi]+')';ctx.fill();
        ctx.beginPath();
        for(let x=0;x<=W;x+=2){
          const xn=x/W;
          const y=H*(w.base+Math.sin(xn*Math.PI*w.freq+ph)*w.amp
            +Math.sin(xn*Math.PI*w.freq*1.7+ph*.65)*(w.amp*.3)
            +Math.sin(xn*Math.PI*w.freq*.9+ph*1.4)*(w.amp*.18));
          x===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
        }
        ctx.strokeStyle=w.stroke+[.07,.06,.06,.05,.05][wi]+')';ctx.lineWidth=1.1;ctx.stroke();
        if(wi===0){
          for(let fx=W*.04;fx<W;fx+=W*.09+Math.sin(fx*0.01)*W*.02){
            const xn=fx/W;
            const fy=H*(w.base+Math.sin(xn*Math.PI*w.freq+ph)*w.amp
              +Math.sin(xn*Math.PI*w.freq*1.7+ph*.65)*(w.amp*.3));
            const fa=.10+Math.sin(t*.9+fx*.008)*.05;
            const fg=ctx.createRadialGradient(fx,fy,0,fx,fy,20+Math.sin(t+fx*.01)*5);
            fg.addColorStop(0,'rgba(255,255,255,'+fa+')');fg.addColorStop(1,'rgba(255,255,255,0)');
            ctx.fillStyle=fg;ctx.beginPath();ctx.ellipse(fx,fy,22,5,0,0,Math.PI*2);ctx.fill();
          }
        }
      });

      for(let rx=W*.02;rx<W;rx+=W*.065){
        const ry=H*(.52+Math.sin(t*.35+rx*.008)*.05);
        const ra=.03+Math.sin(t*.7+rx*.015)*.02;
        const rg=ctx.createLinearGradient(rx,ry,rx+W*.05,ry);
        rg.addColorStop(0,'rgba(255,255,255,0)');rg.addColorStop(.5,'rgba(255,255,255,'+ra+')');rg.addColorStop(1,'rgba(255,255,255,0)');
        ctx.fillStyle=rg;ctx.fillRect(rx,ry,W*.05,2);
      }

      bubbles.forEach(b=>{
        b.y+=b.vy;b.ph+=b.sp;
        if(b.y<.42)b.y=.5+Math.random()*.3;
        const bx=b.x*W+Math.sin(b.ph)*6;
        const by=b.y*H;
        const ba=b.a*(0.4+Math.sin(b.ph)*.6);
        ctx.beginPath();ctx.arc(bx,by,b.r,0,Math.PI*2);
        ctx.strokeStyle='rgba(147,197,253,'+ba+')';ctx.lineWidth=.7;ctx.stroke();
        ctx.beginPath();ctx.arc(bx-b.r*.3,by-b.r*.35,b.r*.28,0,Math.PI*2);
        ctx.fillStyle='rgba(255,255,255,'+(ba*.55)+')';ctx.fill();
      });

      sparks.forEach(s=>{
        s.ph+=s.sp;
        const sx=s.x*W+Math.sin(s.ph*.4)*10;
        const sy=H*(s.yb+Math.sin(s.ph*.3)*.015);
        const sa=(Math.sin(s.ph)+1)/2;
        if(sa>.35){
          const a=sa*.22;
          ctx.save();ctx.translate(sx,sy);
          ctx.fillStyle='rgba(255,255,255,'+a+')';
          ctx.beginPath();
          ctx.moveTo(0,-s.sz*2.2);ctx.lineTo(s.sz*.35,-s.sz*.35);ctx.lineTo(s.sz*2.2,0);
          ctx.lineTo(s.sz*.35,s.sz*.35);ctx.lineTo(0,s.sz*2.2);ctx.lineTo(-s.sz*.35,s.sz*.35);
          ctx.lineTo(-s.sz*2.2,0);ctx.lineTo(-s.sz*.35,-s.sz*.35);
          ctx.closePath();ctx.fill();ctx.restore();
        }
      });

      id=requestAnimationFrame(draw);
    };
    draw();
    return()=>{ro.disconnect();cancelAnimationFrame(id);};
  },[]);

  const go=async()=>{
    setErr('');setBusy(true);
    if(tab==='login'){
      const r=await api.post('/api/auth/login',{email,password:pw});
      if(r.error)setErr(r.error);
      else if(r.totp_required){setTotpLoginStep(true);setErr('');}
      else if(r.otp_required){setOtpEmail(r.email);setOtpStep(true);setOtpResendCd(60);}
      else onLogin(r);
    } else {
      if(!name||!email||!pw){setErr('All fields required.');setBusy(false);return;}
      if(regMode==='create'&&!wsName){setErr('Workspace name required.');setBusy(false);return;}
      if(regMode==='join'&&!inviteCode){setErr('Invite code required.');setBusy(false);return;}
      const r=await api.post('/api/auth/register',{mode:regMode,workspace_name:wsName,invite_code:inviteCode,name,email,password:pw,role});
      if(r.error)setErr(r.error);else onLogin(r);
    }
    setBusy(false);
  };
  const submitOtp=async()=>{
    if(otpCode.length!==6){setErr('Enter the 6-digit code.');return;}
    setErr('');setBusy(true);
    const r=await api.post('/api/auth/verify-otp',{email:otpEmail,code:otpCode});
    if(r.error){setErr(r.error);setOtpCode('');}else onLogin(r);
    setBusy(false);
  };

  const submitTotpLogin=async()=>{
    const code=totpLoginCode.replace(/\D/g,'');
    if(code.length!==6){setErr('Enter the 6-digit code from your authenticator app.');return;}
    setErr('');setBusy(true);
    const r=await api.post('/api/auth/totp-login',{code});
    setBusy(false);
    if(r.error){setErr(r.error);setTotpLoginCode('');totpLoginRefs[0].current?.focus();}
    else{setTotpLoginStep(false);onLogin(r);}
  };
  const handleTotpLoginDigit=(i,val)=>{
    const digits=totpLoginCode.split('');digits[i]=val.replace(/\D/g,'').slice(-1);
    const nc=digits.join('');setTotpLoginCode(nc);
    if(val&&i<5)totpLoginRefs[i+1].current?.focus();
    if(nc.length===6&&digits.every(d=>d))setTimeout(submitTotpLogin,80);
  };
  const handleTotpLoginKey=(i,e)=>{
    if(e.key==='Backspace'&&!totpLoginCode[i]&&i>0)totpLoginRefs[i-1].current?.focus();
    if(e.key==='Enter'&&totpLoginCode.length===6)submitTotpLogin();
  };
  const resendOtp=async()=>{
    if(otpResendCd>0)return;setErr('');
    const r=await api.post('/api/auth/resend-otp',{email:otpEmail});
    if(r.error)setErr(r.error);else{setOtpResendCd(60);setOtpCode('');}
  };
  const handleOtpInput=(i,val)=>{
    const d=otpCode.split('');d[i]=val.slice(-1);const nc=d.join('');setOtpCode(nc);
    if(val&&i<5&&otpRefs[i+1].current)otpRefs[i+1].current.focus();
    if(nc.length===6&&d.every(x=>x))setTimeout(submitOtp,80);
  };
  const handleOtpKey=(i,e)=>{
    if(e.key==='Backspace'&&!otpCode[i]&&i>0)otpRefs[i-1].current.focus();
    if(e.key==='Enter')submitOtp();
  };
  const handleOtpPaste=(e)=>{
    const p=e.clipboardData.getData('text').replace(/\D/g,'').slice(0,6);
    if(p.length===6){setOtpCode(p);setTimeout(submitOtp,80);}
  };

  const inp={
    width:'100%',padding:'12px 15px',borderRadius:10,fontSize:14,outline:'none', background:'#f8fafc',border:'1.5px solid #e2e8f0',color:'#0f172a', fontFamily:'inherit',transition:'border-color .18s,box-shadow .18s',boxSizing:'border-box', };
  const lbl={display:'block',fontSize:11,fontWeight:700,letterSpacing:.07, textTransform:'uppercase',color:'#94a3b8',marginBottom:6};

  const leftPanel=html`
    <div style=${{
      width:'48%',flexShrink:0,minHeight:'100vh', position:'relative',overflow:'hidden', }}>
      <canvas ref=${cvRef} style=${{
        position:'absolute',top:0,left:0, width:'100%',height:'100%',display:'block', }}></canvas>

      <div style=${{position:'absolute',top:24,left:24,zIndex:10,display:'flex',alignItems:'center',gap:9}}>
        <div style=${{width:32,height:32,borderRadius:9,background:'white',display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 2px 12px rgba(59,130,246,0.2)'}}>
          <svg width="17" height="17" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="#1d4ed8"/><circle cx="32" cy="11" r="6" fill="#1d4ed8"/><circle cx="51" cy="43" r="6" fill="#1d4ed8"/><circle cx="13" cy="43" r="6" fill="#1d4ed8"/><line x1="32" y1="17" x2="32" y2="23" stroke="#1d4ed8" stroke-width="3.5" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="#1d4ed8" stroke-width="3.5" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="#1d4ed8" stroke-width="3.5" stroke-linecap="round"/></svg>
        </div>
        <span style=${{fontFamily:"'Syne',sans-serif",fontWeight:800,fontSize:15,color:'#1e3a5f',letterSpacing:-.3}}>VEWIT</span>
      </div>

            <div style=${{
        position:'absolute',top:'50%',left:'50%', transform:'translate(-50%,-50%)', textAlign:'center',zIndex:10,pointerEvents:'none', width:'80%', }}>
        <div style=${{display:'inline-flex',alignItems:'center',gap:7,background:'rgba(255,255,255,0.7)',border:'1px solid rgba(147,197,253,0.6)',padding:'5px 14px',borderRadius:100,marginBottom:18,backdropFilter:'blur(8px)'}}>
          <div style=${{width:5,height:5,borderRadius:'50%',background:'#3b82f6'}}></div>
          <span style=${{fontSize:11,color:'#1d4ed8',fontWeight:700,letterSpacing:.05}}>TEAM COLLABORATION · AI-POWERED</span>
        </div>
        <h2 style=${{fontFamily:"'Syne',sans-serif",fontSize:'clamp(1.5rem,2.5vw,2rem)',fontWeight:800,color:'#1e3a5f',lineHeight:1.2,marginBottom:10,letterSpacing:-.03}}>
          Where teams<br/>ship together
        </h2>
        <p style=${{fontSize:13,color:'rgba(30,58,95,0.65)',lineHeight:1.7}}>
          Tasks · AI assistant · Huddles<br/>Timeline · Tickets
        </p>
      </div>

            <div style=${{
        position:'absolute',bottom:28,left:0,right:0, display:'flex',justifyContent:'center',gap:8,flexWrap:'wrap', padding:'0 20px',zIndex:10, }}>
        ${['📋 Tasks','🤖 AI','📅 Timeline','📞 Huddles','🎫 Tickets'].map(f=>html`
          <div key=${f} style=${{
            background:'rgba(255,255,255,0.72)', border:'1px solid rgba(147,197,253,0.5)', backdropFilter:'blur(8px)', padding:'5px 12px',borderRadius:100, fontSize:11,fontWeight:600,color:'#1d4ed8', }}>${f}</div>
        `)}
      </div>
    </div>`;

  const rightPanel=(child)=>html`
    <div style=${{
      flex:1,minHeight:'100vh',background:'#ffffff', display:'flex',alignItems:'center',justifyContent:'center', padding:'40px 36px',overflowY:'auto', borderLeft:'1px solid #f1f5f9', }}>
      <div style=${{width:'100%',maxWidth:400}}>
        ${child}
      </div>
    </div>`;

  if(totpLoginStep) return html`
    <div style=${{minHeight:'100vh',display:'flex',alignItems:'center',justifyContent:'center',background:'var(--bg)',padding:20}}>
      <div style=${{width:'min(420px,100%)',background:'var(--sf)',borderRadius:16,padding:'32px 28px',border:'1px solid var(--bd)',boxShadow:'0 8px 40px rgba(0,0,0,.08)'}}>
        <div style=${{textAlign:'center',marginBottom:24}}>
          <div style=${{fontSize:36,marginBottom:8}}>🔐</div>
          <div style=${{fontSize:18,fontWeight:700,color:'var(--tx)',marginBottom:6}}>Two-Factor Authentication</div>
          <div style=${{fontSize:13,color:'var(--tx3)',lineHeight:1.6}}>Enter the 6-digit code from your authenticator app (Google Authenticator, Authy, 1Password)</div>
        </div>
        ${err?html`<div style=${{padding:'10px 14px',background:'rgba(185,28,28,.07)',border:'1px solid rgba(185,28,28,.2)',borderRadius:8,color:'#b91c1c',fontSize:13,marginBottom:16,textAlign:'center'}}>${err}</div>`:null}
        <div style=${{display:'flex',gap:8,justifyContent:'center',marginBottom:20}}>
          ${[0,1,2,3,4,5].map(i=>html`
            <input key=${i} ref=${totpLoginRefs[i]} type="text" inputMode="numeric"
              maxLength="1" value=${totpLoginCode[i]||''}
              onInput=${e=>handleTotpLoginDigit(i,e.target.value)}
              onKeyDown=${e=>handleTotpLoginKey(i,e)}
              style=${{width:44,height:52,textAlign:'center',fontSize:24,fontWeight:700,fontFamily:'monospace',
                border:'2px solid '+(totpLoginCode[i]?'var(--ac)':'var(--bd)'),
                borderRadius:10,background:'var(--sf2)',color:'var(--tx)',outline:'none',transition:'border-color .15s'}}/>`)}
        </div>
        <button class="btn bp" style=${{width:'100%',padding:'11px',fontSize:15,marginBottom:10,borderRadius:10}} onClick=${submitTotpLogin} disabled=${busy||totpLoginCode.replace(/\D/g,'').length<6}>
          ${busy?html`<span class="spin"></span>`:null} ${busy?'Verifying…':'Verify →'}
        </button>
        <button style=${{width:'100%',background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:13,padding:8}} onClick=${()=>{setTotpLoginStep(false);setTotpLoginCode('');setErr('');}}>
          ← Back to login
        </button>
      </div>
    </div>`;

  if(otpStep) return html`
    <div style=${{width:'100vw',minHeight:'100vh',display:'flex',overflow:'hidden'}}>
      ${leftPanel}
      ${rightPanel(html`
        <div style=${{marginBottom:28}}>
          <div style=${{width:52,height:52,borderRadius:14,background:'#eff6ff',border:'1.5px solid #bfdbfe',display:'flex',alignItems:'center',justifyContent:'center',marginBottom:16,fontSize:24}}>🔐</div>
          <h2 style=${{fontFamily:"'Syne',sans-serif",fontSize:20,fontWeight:800,color:'#0f172a',marginBottom:6}}>Verify your identity</h2>
          <p style=${{fontSize:13.5,color:'#64748b',marginBottom:3}}>6-digit code sent to</p>
          <p style=${{fontSize:14,fontWeight:700,color:'#2563eb'}}>${otpEmail}</p>
        </div>
        <div style=${{display:'flex',gap:8,marginBottom:20}} onPaste=${handleOtpPaste}>
          ${[0,1,2,3,4,5].map(i=>html`
            <input key=${i} ref=${otpRefs[i]}
              style=${{flex:1,height:54,borderRadius:10,textAlign:'center',fontSize:20,fontWeight:700,fontFamily:'monospace',outline:'none',boxSizing:'border-box',transition:'all .15s', background:otpCode[i]?'#eff6ff':'#f8fafc', border:'1.5px solid '+(otpCode[i]?'#3b82f6':'#e2e8f0'), color:'#0f172a',boxShadow:otpCode[i]?'0 0 0 3px rgba(59,130,246,0.1)':'none'}}
              maxLength=1 value=${otpCode[i]||''}
              onInput=${e=>handleOtpInput(i,e.target.value)}
              onKeyDown=${e=>handleOtpKey(i,e)}
              onFocus=${e=>e.target.select()}
            />`)}
        </div>
        ${err?html`<div style=${{color:'#dc2626',fontSize:13,padding:'10px 14px',background:'#fef2f2',borderRadius:9,border:'1px solid #fecaca',marginBottom:14}}>${err}</div>`:null}
        <button onClick=${submitOtp} disabled=${busy||otpCode.length!==6}
          style=${{width:'100%',height:46,borderRadius:10,border:'none',fontFamily:'inherit', background:otpCode.length===6?'#2563eb':'#e2e8f0', color:otpCode.length===6?'#fff':'#94a3b8', fontSize:14,fontWeight:700,cursor:otpCode.length===6?'pointer':'default', transition:'all .18s',marginBottom:14, boxShadow:otpCode.length===6?'0 4px 14px rgba(37,99,235,0.3)':'none'}}>
          ${busy?'Verifying...':'Verify & Sign In →'}
        </button>
        <div style=${{display:'flex',justifyContent:'center',gap:8,marginBottom:8}}>
          <span style=${{fontSize:12.5,color:'#94a3b8'}}>Didn't receive it?</span>
          <button onClick=${resendOtp} disabled=${otpResendCd>0}
            style=${{background:'none',border:'none',cursor:otpResendCd>0?'default':'pointer',color:otpResendCd>0?'#cbd5e1':'#2563eb',fontSize:12.5,fontWeight:600,padding:0}}>
            ${otpResendCd>0?`Resend in ${otpResendCd}s`:'Resend code'}
          </button>
        </div>
        <div style=${{textAlign:'center'}}>
          <button onClick=${()=>{setOtpStep(false);setOtpCode('');setErr('');}}
            style=${{background:'none',border:'none',cursor:'pointer',color:'#94a3b8',fontSize:12,fontFamily:'inherit'}}>
            ← Back to login
          </button>
        </div>
      `)}
    </div>`;

  return html`
    <div style=${{width:'100vw',minHeight:'100vh',display:'flex',overflow:'hidden'}}>
      ${leftPanel}
      ${rightPanel(html`
                <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:28}}>
          <div style=${{width:28,height:28,borderRadius:7,background:'#2563eb',display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 2px 8px rgba(37,99,235,0.3)'}}>
            <svg width="15" height="15" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white"/><circle cx="51" cy="43" r="6" fill="white"/><circle cx="13" cy="43" r="6" fill="white"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/></svg>
          </div>
          <span style=${{fontFamily:"'Syne',sans-serif",fontWeight:800,fontSize:14.5,color:'#0f172a',letterSpacing:-.3}}>VEWIT</span>
        </div>

                <h1 style=${{fontFamily:"'Syne',sans-serif",fontSize:'clamp(1.5rem,2.2vw,1.85rem)',fontWeight:800,color:'#0f172a',marginBottom:6,letterSpacing:-.03,lineHeight:1.15}}>
          ${tab==='login'?'Welcome back':'Create account'}
        </h1>
        <p style=${{fontSize:13.5,color:'#64748b',marginBottom:24,lineHeight:1.6}}>
          ${tab==='login'?'Sign in to your VEWIT workspace':'Set up your workspace and start shipping'}
        </p>

                <div style=${{display:'flex',background:'#f1f5f9',borderRadius:11,padding:3,marginBottom:22}}>
          ${['login','register'].map(tp=>html`
            <button key=${tp} onClick=${()=>{setTab(tp);setErr('');}}
              style=${{flex:1,height:35,fontSize:13,fontWeight:600,border:'none',cursor:'pointer', borderRadius:9,fontFamily:'inherit',transition:'all .16s', background:tab===tp?'#ffffff':'transparent', color:tab===tp?'#0f172a':'#94a3b8', boxShadow:tab===tp?'0 1px 4px rgba(0,0,0,0.08)':'none'}}>
              ${tp==='login'?'Sign In':'Create Account'}
            </button>`)}
        </div>

        ${tab==='register'?html`
          <div style=${{display:'flex',background:'#f1f5f9',borderRadius:9,padding:3,marginBottom:16}}>
            ${[['create','🏢 New Workspace'],['join','🔗 Join Workspace']].map(([m,lbl])=>html`
              <button key=${m} onClick=${()=>setRegMode(m)}
                style=${{flex:1,height:29,fontSize:11,fontWeight:600,border:'none',cursor:'pointer', borderRadius:7,fontFamily:'inherit',transition:'all .16s', background:regMode===m?'#ffffff':'transparent', color:regMode===m?'#374151':'#94a3b8', boxShadow:regMode===m?'0 1px 3px rgba(0,0,0,0.07)':'none'}}>
                ${lbl}
              </button>`)}
          </div>
          ${regMode==='create'?html`
            <div style=${{marginBottom:14}}><label style=${lbl}>Workspace Name</label>
              <input style=${inp} placeholder="e.g. Acme Corp" value=${wsName} onInput=${e=>setWsName(e.target.value)}/></div>`:null}
          ${regMode==='join'?html`
            <div style=${{marginBottom:14,padding:'12px 14px',background:'#eff6ff',borderRadius:10,border:'1px solid #bfdbfe'}}>
              <label style=${lbl}>Invite Code</label>
              <input style=${{...inp,fontFamily:'monospace',letterSpacing:4,fontSize:16,textAlign:'center',background:'#fff'}}
                placeholder="XXXXXXXX" value=${inviteCode}
                onInput=${e=>setInviteCode(e.target.value.toUpperCase())}/>
            </div>`:null}`:null}

        <div style=${{display:'flex',flexDirection:'column',gap:13}}>
          ${tab==='register'?html`
            <div><label style=${lbl}>Full Name</label>
              <input style=${inp} placeholder="Alice Chen" value=${name} onInput=${e=>setName(e.target.value)}/></div>`:null}

          <div><label style=${lbl}>Email Address</label>
            <input style=${inp} type="email" placeholder="you@company.com" value=${email} autoComplete="username"
              onInput=${e=>setEmail(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&go()}/></div>

          <div><label style=${lbl}>Password</label>
            <div style=${{position:'relative'}}>
              <input style=${{...inp,paddingRight:42}} type=${showPw?'text':'password'}
                placeholder="••••••••••" value=${pw} autoComplete="current-password"
                onInput=${e=>setPw(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&go()}/>
              <button onClick=${()=>setShowPw(!showPw)}
                style=${{position:'absolute',right:13,top:'50%',transform:'translateY(-50%)',background:'none',border:'none',cursor:'pointer',color:'#94a3b8',fontSize:14,padding:0,lineHeight:1}}>
                ${showPw?'🙈':'👁'}
              </button>
            </div>
          </div>

          ${tab==='register'?html`
            <div><label style=${lbl}>Role</label>
              <select style=${{...inp,cursor:'pointer'}} value=${role} onChange=${e=>setRole(e.target.value)}>
                ${(regMode==='join'?JOIN_ROLES:ROLES).map(r=>html`<option key=${r}>${r}</option>`)}
              </select></div>`:null}

          ${err?html`
            <div style=${{display:'flex',alignItems:'center',gap:8,padding:'10px 13px',background:'#fef2f2',borderRadius:9,border:'1px solid #fecaca'}}>
              <span style=${{fontSize:13}}>⚠️</span>
              <span style=${{fontSize:13,color:'#dc2626'}}>${err}</span>
            </div>`:null}

          <button onClick=${go} disabled=${busy}
            style=${{height:46,borderRadius:10,border:'none',cursor:busy?'default':'pointer', fontFamily:'inherit', background:busy?'#bfdbfe':'#2563eb', color:busy?'#93c5fd':'#ffffff', fontSize:14,fontWeight:700,letterSpacing:.01, transition:'all .18s',marginTop:2, boxShadow:busy?'none':'0 4px 14px rgba(37,99,235,0.3),inset 0 1px 0 rgba(255,255,255,0.15)'}}>
            ${busy?'Please wait...':(tab==='login'?'Sign In →':regMode==='create'?'Create Workspace & Account →':'Join Workspace →')}
          </button>
        </div>

        <p style=${{fontSize:12.5,color:'#94a3b8',marginTop:18,textAlign:'center'}}>
          ${tab==='login'
            ?html`New to VEWIT? <button onClick=${()=>{setTab('register');setErr('');try{history.replaceState(null,'','/?action=register');}catch{}}} style=${{background:'none',border:'none',color:'#2563eb',cursor:'pointer',fontSize:12.5,fontWeight:600,padding:'0 0 0 2px',fontFamily:'inherit'}}>Create an account</button>`
            :html`Already have an account? <button onClick=${()=>{setTab('login');setErr('');try{history.replaceState(null,'','/?action=login');}catch{}}} style=${{background:'none',border:'none',color:'#2563eb',cursor:'pointer',fontSize:12.5,fontWeight:600,padding:'0 0 0 2px',fontFamily:'inherit'}}>Sign in</button>`}
        </p>
      `)}
    </div>`;
}/* ─── Sidebar ─────────────────────────────────────────────────────────────── */
function Sidebar({cu,view,setView,onLogout,unread,dmUnread,col,setCol,wsName,dark,setDark,teams,users,projects,tasks,teamCtx,setTeamCtx,activeTeam,wsDmEnabled=true,onlineUsers=new Set()}){
  const inCall=false; // Google Meet handles calls externally
  const fmtTime=s=>{const m=Math.floor(s/60);const sec=s%60;return m+':'+(sec<10?'0':'')+sec;};
  const isAdminManager=cu&&(cu.role==='Admin'||cu.role==='Manager');
  const baseView=(view||'dashboard').split(':')[0];

  const NAV_ICONS={
    dashboard:    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>', projects:     '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>', tasks:        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>', messages:     '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>', tickets:      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M2 9a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v1.5a1.5 1.5 0 0 0 0 3V15a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-1.5a1.5 1.5 0 0 0 0-3V9z"/><line x1="9" y1="7" x2="9" y2="17" strokeDasharray="2 2"/></svg>', timeline:     '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="14" x2="10" y2="14"/><line x1="8" y1="18" x2="14" y2="18"/></svg>', productivity: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/><line x1="2" y1="20" x2="22" y2="20"/></svg>', reminders:    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>', team:         '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>', dm:           '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
    docs:         '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    timereport:   '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    productivity: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  };
  // Grouped sidebar sections
  const NAV_GROUPS=[
    {key:'main', label:null, items:[
      {id:'dashboard',label:'Dashboard'},
    ]},
    {key:'work', label:'Work', items:[
      {id:'projects',label:'Projects'},
      {id:'tasks',label:'Kanban Board'},
      {id:'timeline',label:'Timeline'},
      {id:'reminders',label:'Reminders'},
    ]},
    {key:'comms', label:'Communication', items:[
      {id:'messages',label:'Channels'},
      ...(wsDmEnabled||isAdminManager?[{id:'dm',label:'Direct Messages'}]:[]),
      {id:'tickets',label:'Tickets'},
    ]},
    {key:'ai', label:'AI', items:[
      {id:'docs',label:'Documentation & Diagrams'},
    ]},
    ...(isAdminManager?[{key:'admin',label:'Administration',items:[
      {id:'team',label:'Team Management'},
      {id:'timereport',label:'Time Report'},
      {id:'productivity',label:'Dev Productivity'},
    ]}]:[]),
  ];
  // Collapsed group state — persisted
  const [collapsedGroups,setCollapsedGroups]=useState(()=>{
    try{return JSON.parse(localStorage.getItem('vw_nav_collapsed')||'{}');}catch{return {};}
  });
  const toggleGroup=(key)=>{
    setCollapsedGroups(prev=>{
      const n={...prev,[key]:!prev[key]};
      try{localStorage.setItem('vw_nav_collapsed',JSON.stringify(n));}catch{}
      return n;
    });
  };

  const themeIcon=dark
    ?'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>'
    :'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

  const W=col?64:200; // collapsed=64px, expanded=200px

  return html`
    <aside style=${{
      width:W,minWidth:W,maxWidth:W, background:'#0f172a', display:'flex',flexDirection:'column', height:'100vh',flexShrink:0,overflow:'visible', borderRight:'1px solid rgba(37,99,235,0.15)', transition:'width .2s ease,min-width .2s ease,max-width .2s ease', position:'relative'
    }}>

            <div style=${{
        padding:col?'14px 0':'12px 14px', display:'flex',alignItems:'center', gap:8,flexShrink:0, borderBottom:'1px solid rgba(37,99,235,0.15)', justifyContent:col?'center':'flex-start', minHeight:52
      }}>
        <div style=${{width:28,height:28,borderRadius:8,background:'#2563eb',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0,boxShadow:'0 2px 8px rgba(37,99,235,0.4)'}}>
          <svg width="14" height="14" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white" opacity=".9"/><circle cx="51" cy="43" r="6" fill="white" opacity=".9"/><circle cx="13" cy="43" r="6" fill="white" opacity=".9"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" strokeWidth="3.5" strokeLinecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" strokeWidth="3.5" strokeLinecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" strokeWidth="3.5" strokeLinecap="round"/></svg>
        </div>
        ${!col?html`<div style=${{flex:1,minWidth:0}}>
          <div style=${{fontSize:12,fontWeight:700,color:'#ffffff',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${wsName||'VEWIT'}</div>
          ${activeTeam?html`<div style=${{fontSize:10,color:'var(--ac)',fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'flex',alignItems:'center',gap:4}}>
            ${!isAdminManager?html`<span style=${{color:'rgba(255,255,255,.3)',fontWeight:400}}>My Team</span>`:null}
            ${activeTeam.name}
          </div>`
          :html`<div style=${{fontSize:10,color:'rgba(203,213,225,0.6)'}}>Workspace</div>`}
        </div>`:null}
      </div>

            <nav style=${{flex:1,overflowY:'auto',padding:'6px 6px 4px',display:'flex',flexDirection:'column',gap:0}}>
        ${NAV_GROUPS.map(grp=>html`
          <div key=${grp.key} style=${{marginBottom:2}}>
            ${!col&&grp.label?html`
              <button onClick=${()=>toggleGroup(grp.key)}
                style=${{display:'flex',alignItems:'center',justifyContent:'space-between',width:'100%',padding:'5px 8px',background:'none',border:'none',cursor:'pointer',color:'rgba(100,116,139,0.7)',fontSize:10,fontWeight:700,letterSpacing:'.07em',textTransform:'uppercase'}}>
                ${grp.label}
                <span style=${{fontSize:9,opacity:.6,transition:'transform .15s',transform:collapsedGroups[grp.key]?'rotate(-90deg)':'rotate(0)'}}>▾</span>
              </button>`:null}
            ${(!collapsedGroups[grp.key]||col)?grp.items.map(it=>html`
              <button key=${it.id}
                title=${col?it.label:''}
                onClick=${()=>setView(it.id)}
                style=${{
                  display:'flex',alignItems:'center',gap:col?0:9,width:'100%',
                  padding:col?'9px 0':'7px 8px',
                  borderRadius:8,border:'none',cursor:'pointer',
                  background:baseView===it.id?'rgba(37,99,235,0.18)':'transparent',
                  color:baseView===it.id?'#93c5fd':'rgba(203,213,225,0.7)',
                  fontSize:12,fontWeight:baseView===it.id?700:400,
                  transition:'all .1s',textAlign:'left',
                  borderLeft:baseView===it.id&&!col?'2px solid #3b82f6':'2px solid transparent',
                  justifyContent:col?'center':'flex-start',position:'relative',
                  marginBottom:1
                }}
                onMouseEnter=${e=>{if(baseView!==it.id){e.currentTarget.style.background='rgba(37,99,235,0.12)';e.currentTarget.style.color='#93c5fd';}}}
                onMouseLeave=${e=>{if(baseView!==it.id){e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(203,213,225,0.7)';}}}>\n                <span style=${{flexShrink:0,width:col?'auto':16,display:'flex',alignItems:'center',justifyContent:'center',opacity:.8}} dangerouslySetInnerHTML=${{__html:NAV_ICONS[it.id]||''}}></span>
                ${!col?html`<span style=${{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',fontSize:12,flex:1}}>${it.label}</span>`:null}
                ${it.id==='dm'&&dmUnread.reduce((a,x)=>a+(x.cnt||0),0)>0?html`<span style=${{minWidth:16,height:16,borderRadius:8,background:'#06b6d4',color:'#fff',fontSize:9,fontWeight:700,display:'flex',alignItems:'center',justifyContent:'center',padding:'0 4px'}}>${dmUnread.reduce((a,x)=>a+(x.cnt||0),0)}</span>`:null}
              </button>`):null}
          </div>`)}
      </nav>

            <div style=${{padding:'8px 6px',borderTop:'1px solid rgba(37,99,235,0.15)',display:'flex',flexDirection:'column',gap:2,flexShrink:0}}>

        <button title=${dark?'Light Mode':'Dark Mode'} onClick=${()=>{setDark(d=>{const n=!d;try{localStorage.setItem('pf_dark',n?'1':'0');}catch{}return n;})}}
          style=${{display:'flex',alignItems:'center',gap:col?0:9,width:'100%',padding:col?'9px 0':'8px 10px',borderRadius:9,border:'none',cursor:'pointer',background:'transparent',color:'rgba(203,213,225,0.65)',transition:'all .12s',justifyContent:col?'center':'flex-start'}}
          onMouseEnter=${e=>{e.currentTarget.style.background='rgba(37,99,235,0.15)';e.currentTarget.style.color='#93c5fd';}}
          onMouseLeave=${e=>{e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(203,213,225,0.65)';}}>
          <span style=${{fontSize:15,flexShrink:0,width:col?'auto':18,display:'flex',alignItems:'center',justifyContent:'center'}} dangerouslySetInnerHTML=${{__html:themeIcon}}></span>
          ${!col?html`<span style=${{fontSize:12}}>${dark?'Light Mode':'Dark Mode'}</span>`:null}
        </button>
        ${(cu&&(cu.role==='Admin'||cu.role==='Manager'||cu.role==='TeamLead'))?html`
          <button title=${col?'Settings':''} onClick=${()=>setView('settings')}
            style=${{display:'flex',alignItems:'center',gap:col?0:9,width:'100%',padding:col?'9px 0':'8px 10px',borderRadius:9,border:'none',cursor:'pointer', background:baseView==='settings'?'rgba(37,99,235,0.18)':'transparent', color:baseView==='settings'?'var(--ac)':'rgba(255,255,255,.35)', transition:'all .12s',justifyContent:col?'center':'flex-start'}}
            onMouseEnter=${e=>{if(baseView!=='settings'){e.currentTarget.style.background='rgba(37,99,235,0.15)';e.currentTarget.style.color='#93c5fd';}}}
            onMouseLeave=${e=>{if(baseView!=='settings'){e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(255,255,255,.35)';}}}>
            <span style=${{fontSize:15,flexShrink:0,width:col?'auto':18,textAlign:'center'}}>⚙️</span>
            ${!col?html`<span style=${{fontSize:12}}>Settings</span>`:null}
          </button>`:null}
        <button title=${col?'Sign out':''} onClick=${onLogout}
          style=${{display:'flex',alignItems:'center',gap:col?0:9,width:'100%',padding:col?'9px 0':'8px 10px',borderRadius:9,border:'none',cursor:'pointer',background:'transparent',color:'rgba(203,213,225,0.55)',transition:'all .12s',justifyContent:col?'center':'flex-start'}}
          onMouseEnter=${e=>{e.currentTarget.style.background='rgba(239,68,68,.1)';e.currentTarget.style.color='#f87171';}}
          onMouseLeave=${e=>{e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(203,213,225,0.55)';}}>
          <span style=${{fontSize:15,flexShrink:0,width:col?'auto':18,textAlign:'center'}}>↪</span>
          ${!col?html`<span style=${{fontSize:12}}>Sign out</span>`:null}
        </button>
      </div>
      <button title=${col?'Expand sidebar':'Collapse sidebar'} onClick=${()=>setCol(c=>!c)}
        style=${{
          position:'absolute', left:col?64:200, top:'50%', transform:'translateY(-50%)', zIndex:200, width:14, height:40, background:'#0f0f0f', border:'1px solid rgba(255,255,255,.1)', borderLeft:'none', borderRadius:'0 6px 6px 0', cursor:'pointer', display:'flex', alignItems:'center', justifyContent:'center', color:'rgba(255,255,255,.35)', transition:'left .2s ease, background .12s, color .12s', padding:0, }}
        onMouseEnter=${e=>{e.currentTarget.style.background='#1a1a1a';e.currentTarget.style.color='rgba(255,255,255,.8)';}}
        onMouseLeave=${e=>{e.currentTarget.style.background='#0f172a';e.currentTarget.style.color='rgba(148,163,184,0.5)';}}>
        <svg width="8" height="12" viewBox="0 0 8 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          ${col
            ?html`<polyline points="2 2 6 6 2 10"/>`
            :html`<polyline points="6 2 2 6 6 10"/>`}
        </svg>
      </button>
    </aside>`;
}

/* ─── Header ──────────────────────────────────────────────────────────────── */
function Header({title,sub,dark,setDark,extra,cu,setCu,upcomingReminders,onViewReminders,notifs,onNotifClick,onMarkAllRead,onClearAll,activeTeam,teams,setTeamCtx}){
  const [showNP,setShowNP]=useState(false);
  const [showProfile,setShowProfile]=useState(false);
  const [uploadMsg,setUploadMsg]=useState('');
  const now=new Date();
  const todayStr=now.toLocaleDateString('en-US',{day:'numeric',month:'short'});
  const upcoming=safe(upcomingReminders).slice(0,4);
  const fmtT=dt=>{const d=new Date(dt);return d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0');};
  const unread=safe(notifs).filter(n=>!n.read).length;
  const NI={task_assigned:'✅',status_change:'🔄',comment:'💬',deadline:'⏰',dm:'📨',project_added:'📁',reminder:'🔔',call:'📞'};
  const NC={task_assigned:'var(--ac)',status_change:'var(--cy)',comment:'var(--pu)',deadline:'var(--am)',dm:'var(--cy)',project_added:'var(--gn)',reminder:'var(--am)',call:'#22c55e'};
  const npRef=useRef(null);
  const prRef=useRef(null);
  const prImgRef=useRef(null);
  useEffect(()=>{
    if(!showNP)return;
    const h=e=>{if(npRef.current&&!npRef.current.contains(e.target))setShowNP(false);};
    document.addEventListener('mousedown',h);return()=>document.removeEventListener('mousedown',h);
  },[showNP]);
  useEffect(()=>{
    if(!showProfile)return;
    const h=e=>{if(prRef.current&&!prRef.current.contains(e.target))setShowProfile(false);};
    document.addEventListener('mousedown',h);return()=>document.removeEventListener('mousedown',h);
  },[showProfile]);
  return html`
    <div style=${{flexShrink:0,background:'var(--bg)',borderBottom:'1px solid var(--bd2)',position:'relative',zIndex:100}}>
      <div style=${{padding:'0 18px',height:54,display:'flex',alignItems:'center',gap:10}}>
                <div style=${{display:'flex',alignItems:'center',gap:8,flexShrink:0,padding:'5px 14px 5px 10px',background:'#1e3a5f',borderRadius:100,cursor:'pointer',border:'1px solid rgba(37,99,235,0.25)',transition:'all .14s'}} onClick=${onViewReminders}>
          <svg width="13" height="13" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="7" fill="#60a5fa"/><circle cx="32" cy="13" r="4" fill="#60a5fa" opacity="0.9"/><circle cx="48" cy="43" r="4" fill="#60a5fa" opacity="0.9"/><circle cx="16" cy="43" r="4" fill="#60a5fa" opacity="0.9"/><line x1="32" y1="17" x2="32" y2="25" stroke="#60a5fa" strokeWidth="2.5" strokeLinecap="round"/><line x1="44" y1="40" x2="38" y2="36" stroke="#aaff00" strokeWidth="2.5" strokeLinecap="round"/><line x1="20" y1="40" x2="26" y2="36" stroke="#aaff00" strokeWidth="2.5" strokeLinecap="round"/></svg>
          <span style=${{fontSize:11,fontWeight:700,color:'#bfdbfe',letterSpacing:'.3px'}}>Your Reminders</span>
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,.35)" strokeWidth="2" strokeLinecap="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          <span style=${{fontSize:11,color:'#93c5fd',fontWeight:700}}>${todayStr}</span>
        </div>
                        <div style=${{flex:1,overflowX:'auto',scrollbarWidth:'none',msOverflowStyle:'none'}}>
          <div style=${{height:40,background:'#0f172a',borderRadius:100,display:'flex',alignItems:'center',padding:'0 14px',gap:0,position:'relative',minWidth:0,overflow:'hidden',border:'1px solid rgba(37,99,235,0.15)'}}>
            ${upcoming.length===0?html`
              <div style=${{display:'flex',alignItems:'center',gap:10,width:'100%',justifyContent:'center'}}>
                <span style=${{fontSize:11,color:'rgba(148,163,184,0.8)',fontStyle:'italic',letterSpacing:'.2px'}}>No reminders today</span>
                <button onClick=${onViewReminders} style=${{fontSize:10,padding:'3px 12px',height:22,borderRadius:100,background:'#1d4ed8',color:'#ffffff',border:'none',cursor:'pointer',fontWeight:700,letterSpacing:'.2px'}}>+ Add</button>
              </div>
            `:html`
              <div style=${{display:'flex',alignItems:'center',gap:0,width:'100%',overflowX:'auto',scrollbarWidth:'none',position:'relative'}}>
                <div style=${{position:'absolute',top:'50%',left:0,right:40,height:1,background:'linear-gradient(90deg,rgba(37,99,235,0.08) 0%,rgba(96,165,250,0.4) 55%,rgba(37,99,235,0.08) 100%)',transform:'translateY(-50%)',borderRadius:2,zIndex:0}}></div>
                ${upcoming.map((r,i)=>{
                  const isNow=Math.abs(new Date(r.remind_at)-new Date())<1800000;
                  const abbr=(r.task_title||'').split(' ').slice(0,2).join(' ');
                  const tStr=fmtT(r.remind_at);
                  return html`
                    <div key=${r.id} style=${{display:'flex',flexDirection:'column',alignItems:'center',marginRight:i<upcoming.length-1?28:0,flexShrink:0,position:'relative',zIndex:1,cursor:'pointer'}} onClick=${onViewReminders} title=${r.task_title}>
                      <div style=${{position:'relative'}}>
                        ${cu&&cu.avatar_data&&cu.avatar_data.startsWith('data:image')?
                          html`<img src=${cu.avatar_data} style=${{width:isNow?28:22,height:isNow?28:22,borderRadius:'50%',objectFit:'cover',border:isNow?'2px solid #22c55e':'2px solid rgba(170,255,0,.4)',boxShadow:isNow?'0 0 0 3px rgba(34,197,94,.2)':'none',transition:'all .18s'}}/>`:
                          html`<div style=${{width:isNow?28:22,height:isNow?28:22,borderRadius:'50%',background:isNow?'linear-gradient(135deg,#22c55e,#16a34a)':'linear-gradient(135deg,#3b82f6,#2563eb)',border:isNow?'2px solid #22c55e':'2px solid rgba(96,165,250,0.5)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:isNow?10:8,fontWeight:700,color:isNow?'#fff':'#fff',boxShadow:isNow?'0 0 0 3px rgba(34,197,94,.2)':'0 0 8px rgba(59,130,246,.3)',transition:'all .18s'}}>
                            ${(r.task_title||'?').charAt(0).toUpperCase()}
                          </div>`}
                        ${isNow?html`<div style=${{position:'absolute',bottom:-1,right:-1,width:7,height:7,borderRadius:'50%',background:'#22c55e',border:'1.5px solid #111',boxShadow:'0 0 4px #22c55e'}}></div>`:null}
                      </div>
                      <div style=${{display:'flex',flexDirection:'column',alignItems:'center',marginTop:1}}>
                        <span style=${{fontSize:8,fontWeight:700,color:isNow?'#22c55e':'var(--ac)',fontFamily:'monospace',lineHeight:1}}>${tStr}</span>
                        <span style=${{fontSize:7,color:'rgba(255,255,255,.35)',maxWidth:48,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',lineHeight:1.2}}>${abbr}</span>
                      </div>
                    </div>`;
                })}
                <button onClick=${onViewReminders} style=${{marginLeft:'auto',flexShrink:0,width:20,height:20,borderRadius:'50%',background:'var(--ac4)',border:'1px solid var(--ac3)',cursor:'pointer',color:'var(--ac)',fontSize:12,display:'flex',alignItems:'center',justifyContent:'center',fontWeight:700,lineHeight:1}} title="Manage reminders">+</button>
              </div>
            `}
          </div>
        </div>
        <div style=${{display:'flex',alignItems:'center',gap:8,flexShrink:0}}>
                    <div style=${{position:'relative'}} ref=${npRef}>
            <button style=${{width:34,height:34,borderRadius:'50%',border:'none',background:showNP?'var(--sf2)':'var(--sf)',boxShadow:showNP?'none':'var(--sh)',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',position:'relative',color:'var(--tx2)',transition:'all .15s'}}
              onClick=${()=>setShowNP(v=>!v)}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
              ${unread>0?html`<div style=${{position:'absolute',top:-3,right:-3,width:15,height:15,borderRadius:'50%',background:'#ef4444',border:'2px solid var(--sf)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:8,fontWeight:700,color:'#fff'}}>${unread>9?'9+':unread}</div>`:null}
            </button>
            ${showNP?html`
              <div style=${{position:'fixed',top:58,right:14,width:350,maxHeight:460,background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:16,boxShadow:'0 12px 48px rgba(0,0,0,0.22)',zIndex:9500,overflow:'hidden',display:'flex',flexDirection:'column'}}>
                <div style=${{padding:'10px 13px 8px',borderBottom:'1px solid var(--bd)',display:'flex',justifyContent:'space-between',alignItems:'center',flexShrink:0}}>
                  <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em'}}>Notifications ${unread>0?html`<span style=${{color:'var(--ac)',fontSize:11}}>(${unread})</span>`:null}</span>
                  <div style=${{display:'flex',gap:5}}>
                    ${unread>0?html`<button class="btn bg" style=${{fontSize:10,padding:'2px 7px',height:20}} onClick=${onMarkAllRead}>✓ Mark all read</button>`:null}
                    <button class="btn brd" style=${{fontSize:10,padding:'2px 7px',height:20}} onClick=${()=>{onClearAll&&onClearAll();setShowNP(false);}}>Clear all</button>
                  </div>
                </div>
                <div style=${{overflowY:'auto',flex:1}}>
                  ${safe(notifs).length===0?html`<div style=${{textAlign:'center',padding:'20px 0',color:'var(--tx3)',fontSize:12}}>🔔 All caught up!</div>`:null}
                  ${safe(notifs).slice(0,25).map(n=>html`
                    <div key=${n.id} onClick=${()=>{onNotifClick&&onNotifClick(n);setShowNP(false);}}
                      style=${{display:'flex',gap:9,padding:'9px 13px',borderBottom:'1px solid var(--bd)',cursor:'pointer',background:n.read?'transparent':'rgba(29,78,216,.04)',borderLeft:n.read?'none':'2px solid rgba(29,78,216,.3)'}}>
                      <div style=${{width:26,height:26,borderRadius:7,background:(NC[n.type]||'var(--ac)')+'22',display:'flex',alignItems:'center',justifyContent:'center',fontSize:12,flexShrink:0}}>${NI[n.type]||'🔔'}</div>
                      <div style=${{flex:1,minWidth:0}}>
                        <p style=${{fontSize:12,color:'var(--tx)',fontWeight:n.read?400:600,lineHeight:1.35,marginBottom:2,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${n.content}</p>
                        <div style=${{display:'flex',gap:5,alignItems:'center'}}>
                          <span class="mono-10">${ago(n.ts)}</span>
                          ${n.type==='dm'?html`<span style=${{fontSize:9,fontWeight:700,color:'var(--cy)',background:'rgba(14,116,144,0.1)',borderRadius:4,padding:'1px 5px',letterSpacing:'.03em'}}>DM • click to reply</span>`:null}
                          ${n.type==='task_assigned'||n.type==='status_change'||n.type==='comment'?html`<span style=${{fontSize:9,fontWeight:600,color:'var(--ac)',background:'var(--ac3)',borderRadius:4,padding:'1px 5px'}}>→ Tasks</span>`:null}
                        </div>
                      </div>
                      ${!n.read?html`<div style=${{width:5,height:5,borderRadius:'50%',background:'var(--ac)',flexShrink:0,marginTop:5}}></div>`:null}
                    </div>`)}
                </div>
              </div>`:null}
          </div>
          ${cu?html`<div style=${{position:'relative'}} ref=${prRef}>
            <div style=${{display:'flex',alignItems:'center',gap:6,padding:'3px 9px 3px 3px',background:'var(--sf2)',borderRadius:20,border:'1px solid var(--bd)',cursor:'pointer',transition:'all .15s'}}
              onClick=${()=>setShowProfile(v=>!v)}
              onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.background='var(--sf)';}}
              onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.background='var(--sf2)';}}>
              <${Av} u=${cu} size=${24}/>
              <div style=${{lineHeight:1.2}}>
                <div style=${{fontSize:11,fontWeight:700,color:'var(--tx)'}}>${cu&&cu.name?cu.name.split(' ')[0]:''}</div>

              </div>
            </div>
            ${showProfile?html`
              <div style=${{position:'fixed',top:60,right:16,width:290,background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:18,boxShadow:'0 8px 40px rgba(0,0,0,.15)',zIndex:9500,overflow:'hidden'}}>
                <div style=${{padding:'20px 16px',background:'linear-gradient(135deg,rgba(170,255,0,.12),rgba(184,224,32,.04))',borderBottom:'1px solid var(--bd)',display:'flex',flexDirection:'column',alignItems:'center',gap:10}}>
                  <div style=${{position:'relative',cursor:'pointer'}} title="Click to change photo"
                    onClick=${e=>{e.stopPropagation();prImgRef.current&&prImgRef.current.click();}}>
                    ${(cu.avatar_data&&cu.avatar_data.startsWith('data:image'))?
                      html`<img src=${cu.avatar_data} style=${{width:68,height:68,borderRadius:'50%',objectFit:'cover',border:'3px solid var(--ac)',display:'block'}}/>`:
                      html`<div style=${{width:68,height:68,borderRadius:'50%',background:cu.color||'#aaff00',display:'flex',alignItems:'center',justifyContent:'center',fontSize:24,fontWeight:700,color:'#fff',border:'3px solid var(--ac)'}}>${cu.avatar||'?'}</div>`}
                    <div style=${{position:'absolute',bottom:2,right:2,width:22,height:22,borderRadius:'50%',background:'var(--ac)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:12,border:'2px solid var(--sf)',color:'#fff',pointerEvents:'none'}}>📷</div>
                  </div>
                  <input ref=${prImgRef} type="file" accept="image/*" style=${{display:'none'}} onChange=${async e=>{
                    const f=e.target.files[0];if(!f)return;
                    if(f.size>2*1024*1024){setUploadMsg('Image too large (max 2MB)');return;}
                    setUploadMsg('Uploading...');
                    const reader=new FileReader();
                    reader.onload=async ev=>{
                      const dataUrl=ev.target.result;
                      const res=await api.put('/api/users/'+cu.id,{avatar_data:dataUrl});
                      if(res&&res.id){
                        setCu&&setCu(prev=>({...prev,avatar_data:dataUrl}));
                        setUploadMsg('✓ Photo updated!');
                        setTimeout(()=>setUploadMsg(''),2500);
                      } else {
                        setUploadMsg('Upload failed. Try a smaller image.');
                      }
                    };
                    reader.readAsDataURL(f);
                  }}/>
                  <div style=${{textAlign:'center',width:'100%'}}>
                    <div style=${{fontSize:15,fontWeight:700,color:'var(--tx)',marginBottom:2}}>${cu.name}</div>
                    <div style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace',marginBottom:4,wordBreak:'break-all'}}>${cu.email}</div>
                    <span style=${{display:'inline-block',padding:'3px 10px',borderRadius:20,fontSize:10,fontWeight:700,fontFamily:'monospace',background:'rgba(170,255,0,.15)',color:'var(--ac2)',textTransform:'uppercase'}}>${cu&&cu.role||''}</span>
                    ${uploadMsg?html`<div style=${{marginTop:8,fontSize:11,color:uploadMsg.startsWith('✓')?'var(--gn)':'var(--rd)',fontFamily:'monospace'}}>${uploadMsg}</div>`:null}
                  </div>
                </div>
                <div style=${{padding:'10px 12px'}}>
                  <p style=${{fontSize:10,color:'var(--tx3)',textAlign:'center',marginBottom:8,fontFamily:'monospace'}}>Click avatar to change profile photo</p>
                  <button class="btn bg" style=${{width:'100%',justifyContent:'center',fontSize:12}} onClick=${()=>setShowProfile(false)}>Close</button>
                </div>
              </div>`:null}
          </div>`:null}
        </div>
      </div>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'0 20px',height:42,borderTop:'1px solid var(--bd2)'}}>
        <div style=${{display:'flex',alignItems:'baseline',gap:10,minWidth:0}}>
          <h1 style=${{fontSize:15,fontWeight:700,color:'var(--tx)',letterSpacing:'-.2px',fontFamily:"'Space Grotesk',sans-serif",whiteSpace:'nowrap',flexShrink:0}}>${title}</h1>
          ${sub?html`<span style=${{color:'var(--tx2)',fontSize:11,fontWeight:500,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${sub}</span>`:null}
        </div>
        <div style=${{display:'flex',alignItems:'center',gap:7,flexShrink:0}}>${extra||null}</div>
      </div>
    </div>`;
}

/* ─── MemberPicker ────────────────────────────────────────────────────────── */
function MemberPicker({allUsers,selected,onChange}){
  return html`<div style=${{display:'flex',flexWrap:'wrap',gap:7,marginTop:4}}>
    ${safe(allUsers).map(u=>html`
      <button key=${u.id} class=${'chip'+(selected.includes(u.id)?' on':'')}
        onClick=${()=>onChange(selected.includes(u.id)?selected.filter(x=>x!==u.id):[...selected,u.id])}>
        <${Av} u=${u} size=${18}/><span>${u.name}</span>
        ${selected.includes(u.id)?html`<span style=${{color:'var(--ac2)',fontSize:11}}>✓</span>`:null}
      </button>`)}
  </div>`;
}

/* ─── FileAttachments ─────────────────────────────────────────────────────── */
function FileAttachments({taskId,projectId,readOnly}){
  const [files,setFiles]=useState([]);const [busy,setBusy]=useState(false);const [drag,setDrag]=useState(false);const ref=useRef(null);
  const load=useCallback(async()=>{
    const url=taskId?'/api/files?task_id='+taskId:projectId?'/api/files?project_id='+projectId:'';
    if(!url)return;const d=await api.get(url);setFiles(Array.isArray(d)?d:[]);
  },[taskId,projectId]);
  useEffect(()=>{load();},[load]);
  const upload=async fl=>{
    if(!fl||!fl.length)return;setBusy(true);
    for(let i=0;i<fl.length;i++){const fd=new FormData();fd.append('file',fl[i]);if(taskId)fd.append('task_id',taskId);if(projectId)fd.append('project_id',projectId);await api.upload('/api/files',fd);}
    await load();setBusy(false);
  };
  const del=async id=>{if(!window.confirm('Delete this file?'))return;await api.del('/api/files/'+id);setFiles(f=>f.filter(x=>x.id!==id));};
  const icon=m=>{if(!m)return'📄';if(m.startsWith('image/'))return'🖼';if(m.includes('pdf'))return'📕';if(m.includes('word'))return'📝';if(m.includes('sheet'))return'📊';if(m.includes('zip'))return'🗜';return'📄';};
  const sz=b=>b<1024?b+'B':b<1048576?+(b/1024).toFixed(1)+'KB':+(b/1048576).toFixed(1)+'MB';
  return html`<div style=${{display:'flex',flexDirection:'column',gap:10}}>
    ${!readOnly?html`<div class=${'drop-zone'+(drag?' over':'')} onClick=${()=>ref.current&&ref.current.click()}
      onDragOver=${e=>{e.preventDefault();setDrag(true);}} onDragLeave=${()=>setDrag(false)}
      onDrop=${e=>{e.preventDefault();setDrag(false);upload(e.dataTransfer.files);}}>
      ${busy?html`<span class="spin"></span><span style=${{marginLeft:8}}>Uploading...</span>`:
        html`<div style=${{fontSize:22,marginBottom:6}}>📎</div><div style=${{fontWeight:500}}>Click or drag to attach files</div><div style=${{fontSize:11,marginTop:3}}>Max 150 MB</div>`}
      <input ref=${ref} type="file" multiple style=${{display:'none'}} onChange=${e=>upload(e.target.files)}/></div>`:null}
    ${files.map(f=>html`
      <div key=${f.id} style=${{display:'flex',alignItems:'center',gap:10,padding:'9px 12px',background:'var(--sf2)',borderRadius:9,border:'1px solid var(--bd)'}}>
        <span style=${{fontSize:18}}>${icon(f.mime)}</span>
        <div style=${{flex:1,minWidth:0}}>
          <a href=${'/api/files/'+f.id} style=${{fontSize:13,color:'var(--ac2)',fontWeight:500,textDecoration:'none',display:'block',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${f.name}</a>
          <span class="mono-10">${sz(f.size)} · ${ago(f.ts)}</span>
        </div>
        ${!readOnly?html`<button class="btn brd" style=${{padding:'4px 9px',fontSize:11}} onClick=${()=>del(f.id)}>✕</button>`:null}
      </div>`)}
  </div>`;
}

/* ─── TaskModal ───────────────────────────────────────────────────────────── */
const TYPE_COLORS={task:'#1d4ed8',story:'#15803d',bug:'#b91c1c',epic:'#6d28d9',spike:'#b45309'};
const TYPE_BG={task:'rgba(29,78,216,0.10)',story:'rgba(21,128,61,0.10)',bug:'rgba(185,28,28,0.10)',epic:'rgba(109,40,217,0.10)',spike:'rgba(180,83,9,0.10)'};
const TYPE_BORDER={task:'rgba(29,78,216,0.2)',story:'rgba(21,128,61,0.2)',bug:'rgba(185,28,28,0.2)',epic:'rgba(109,40,217,0.2)',spike:'rgba(180,83,9,0.2)'};

function TaskModal({task,onClose,onSave,onDel,projects,users,cu,defaultPid,onSetReminder,teams,activeTeam}){
    const [title,setTitle]=useState((task&&task.title)||'');
  const [desc,setDesc]=useState((task&&task.description)||'');
  const [pid,setPid]=useState((task&&task.project)||defaultPid||(projects[0]&&projects[0].id)||'');
  const [teamId,setTeamId]=useState((task&&task.team_id)||((!task&&activeTeam)?activeTeam.id:'')||'');
  const [ass,setAss]=useState((task&&task.assignee)||'');
  const [pri,setPri]=useState((task&&task.priority)||'medium');
  const [stage,setStage]=useState((task&&task.stage)||'backlog');
  const [due,setDue]=useState((task&&task.due)||'');
  const [pct,setPct]=useState((task&&task.pct)||0);
  const [sprint,setSprint]=useState((task&&task.sprint)||'');
  const [cmts,setCmts]=useState(()=>{const r=task&&task.comments;if(!r)return[];if(Array.isArray(r))return r;try{return JSON.parse(r)||[];}catch{return [];}});
  const [nc,setNc]=useState('');
  const [tab,setTab]=useState('details');
  const [saving,setSaving]=useState(false);
  const [err,setErr]=useState('');
  const isEdit=!!(task&&task.id);
  const selectedTeam=safe(teams).find(t=>t.id===teamId);
  const teamMemberIds=selectedTeam?JSON.parse(selectedTeam.member_ids||'[]'):null;
  const assigneeOptions=teamMemberIds
    ? safe(users).filter(u=>teamMemberIds.includes(u.id))
    : safe(users);
  const FULL_EDIT_ROLES=['Admin','Manager','TeamLead'];
  const isAdminManagerTeamLead=cu&&FULL_EDIT_ROLES.includes(cu.role);
  const isAssignee=cu&&task&&task.assignee===cu.id;
  const canEditTask=isAdminManagerTeamLead||(!isEdit); // new tasks always editable
  const canUpdateStage=isAdminManagerTeamLead||isAssignee;
  const canDeleteTask=cu&&FULL_EDIT_ROLES.includes(cu.role);
  const [rmEnabled,setRmEnabled]=useState(false);
  // Subtasks
  const [subtasks,setSubtasks]=useState([]);
  const [newSubtask,setNewSubtask]=useState('');
  const [loadingSubtasks,setLoadingSubtasks]=useState(false);
  // Jira fields
  const [storyPoints,setStoryPoints]=useState((task&&task.story_points)||0);
  const [recurring,setRecurring]=useState((task&&task.recurring)||'');
  const [taskType,setTaskType]=useState((task&&task.task_type)||'task');
  const [taskLabels,setTaskLabels]=useState(()=>{const r=task&&task.labels;if(!r)return[];if(Array.isArray(r))return r;try{return JSON.parse(r)||[];}catch{return [];}});
  const [newLabel,setNewLabel]=useState('');
  const TASK_TYPES=['task','story','bug','epic','spike'];

  useEffect(()=>{
    if(isEdit&&tab==='subtasks'){
      setLoadingSubtasks(true);
      api.get('/api/tasks/'+task.id+'/subtasks').then(d=>{
        if(Array.isArray(d))setSubtasks(d);
        setLoadingSubtasks(false);
      }).catch(()=>setLoadingSubtasks(false));
    }
  },[isEdit,tab]);

  const addSubtask=async()=>{
    if(!newSubtask.trim()||!isEdit)return;
    const st=await api.post('/api/tasks/'+task.id+'/subtasks',{title:newSubtask.trim()});
    if(st&&st.id){setSubtasks(prev=>[...prev,st]);setNewSubtask('');}
  };
  const toggleSubtask=async(st)=>{
    await api.put('/api/subtasks/'+st.id,{done:st.done?0:1});
    setSubtasks(prev=>prev.map(s=>s.id===st.id?{...s,done:s.done?0:1}:s));
  };
  const delSubtask=async(sid)=>{
    await api.del('/api/subtasks/'+sid);
    setSubtasks(prev=>prev.filter(s=>s.id!==sid));
  };
  const [rmDate,setRmDate]=useState(()=>{
    const d=new Date();d.setDate(d.getDate()+(d.getHours()>=20?1:0));
    return d.toISOString().split('T')[0];
  });
  const [rmTime,setRmTime]=useState('16:00');
  const [rmMins,setRmMins]=useState(10);

  const addCmt=async()=>{
    if(!nc.trim())return;
    const newCmt={id:Date.now()+'',uid:cu&&cu.id,name:cu&&cu.name,text:nc.trim(),ts:new Date().toISOString()};
    const updated=[...cmts,newCmt];
    setCmts(updated);setNc('');
    if(task&&task.id){
      const payload={comments:updated};
      if(canEditTask){
        Object.assign(payload,{title:title.trim()||task.title,description:desc,project:pid,assignee:ass,priority:pri,stage,due,pct});
      } else {
        payload.stage=stage;payload.pct=pct;
      }
      await api.put('/api/tasks/'+task.id,payload);
    }
  };
  const save=async(opts={})=>{
    if(!title.trim()&&(!isEdit||canEditTask)){setErr('Title required.');return null;}
    setSaving(true);setErr('');
    let payload;
    if(isEdit&&canUpdateStage&&!canEditTask){
      payload={stage,pct};
    } else {
      payload={title:title.trim(),description:desc,project:pid,assignee:ass,priority:pri,stage,due,pct,comments:cmts,team_id:teamId,story_points:storyPoints,task_type:taskType,labels:taskLabels,sprint,recurring};
    }
    if(task&&task.id)payload.id=task.id;
    const result=await onSave(payload);
    setSaving(false);
    if(result&&result.error){setErr(result.error);return null;}
    if(!isEdit&&rmEnabled&&rmDate&&rmTime){
      const dt=new Date(rmDate+'T'+rmTime);
      const taskId=(result&&result.id)||'';
      await api.post('/api/reminders',{task_id:taskId,task_title:title.trim(),remind_at:dt.toISOString(),minutes_before:rmMins});
    }
    if(opts.keepOpen)return result;
    onClose();
    return result;
  };

  return html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&onClose()}>
      <div class="mo fi">
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:16}}>
          <div>
            <div style=${{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
              <!-- Type badge pill -->
              <span style=${{
                fontSize:10,fontWeight:800,padding:'3px 9px',borderRadius:5,textTransform:'uppercase',
                background:TYPE_BG[taskType||'task'],
                color:TYPE_COLORS[taskType||'task'],
                border:'1px solid '+TYPE_BORDER[taskType||'task'],
                display:'flex',alignItems:'center',gap:4
              }}>
                <span style=${{width:7,height:7,borderRadius:1,display:'inline-block',background:TYPE_COLORS[taskType||'task']}}></span>
                ${taskType||'task'}
              </span>
              <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)',margin:0}}>
                ${isEdit
                  ? (tab==='subtasks'
                      ? 'Subtasks'
                      : canEditTask
                        ? 'Edit '+(taskType&&taskType!=='task'?taskType.charAt(0).toUpperCase()+taskType.slice(1):'Task')
                        : canUpdateStage?'Update Stage':'View Task')
                  : 'New '+(taskType&&taskType!=='task'?taskType.charAt(0).toUpperCase()+taskType.slice(1):'Task')}
              </h2>
              ${tab==='subtasks'?html`<span style=${{fontSize:11,color:'var(--tx3)',fontWeight:400}}>${title}</span>`:null}
            </div>
            ${isEdit?html`<span class="id-badge id-task">${task.id}</span>`:null}
            ${isEdit&&!canEditTask&&canUpdateStage?html`<div style=${{fontSize:11,color:'var(--am)',marginTop:3}}>You can update stage & progress as the assignee.</div>`:null}
            ${isEdit&&!canEditTask&&!canUpdateStage?html`<div style=${{fontSize:11,color:'var(--tx3)',marginTop:3}}>Read-only — you are not assigned to this task.</div>`:null}
          </div>
          <div style=${{display:'flex',gap:7}}>
            ${isEdit&&onDel&&canDeleteTask?html`<button class="btn brd" style=${{fontSize:12,padding:'6px 11px'}}
              onClick=${async()=>{if(window.confirm('Delete this task?')){await onDel(task.id);onClose();}}}>🗑</button>`:null}
            <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${onClose}>✕</button>
          </div>
        </div>
        ${isEdit?html`
          <div style=${{display:'flex',gap:2,background:'var(--sf2)',borderRadius:9,padding:3,marginBottom:14,width:'fit-content',flexWrap:'wrap'}}>
            ${['details','subtasks','comments','files','time','deps'].map(t=>html`
              <button key=${t} class=${'tb'+(tab===t?' act':'')} onClick=${()=>setTab(t)} style=${{fontSize:11}}>
                ${t==='details'?'Details':t==='subtasks'?html`Subtasks${subtasks.length>0?html` <span style=${{background:'var(--ac)',color:'#fff',borderRadius:8,padding:'0 5px',fontSize:9}}>${subtasks.filter(s=>s.done).length}/${subtasks.length}</span>`:''}`:t==='comments'?'Comments'+(cmts.length?' ('+cmts.length+')':''):t==='time'?'⏱ Time':t==='deps'?'🔗 Deps':'Files'}
              </button>`)}
          </div>`:null}

        ${tab==='details'?html`
          <div style=${{display:'grid',gap:12}}>
            ${!canEditTask&&!canUpdateStage?html`
              <div style=${{background:'var(--sf2)',borderRadius:10,padding:'12px 14px',border:'1px solid var(--bd)',display:'grid',gap:8}}>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Title</span><span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${title}</span></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Stage</span><${SP} s=${stage}/></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Priority</span><${PB} p=${pri}/></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Due</span><span style=${{fontSize:12,color:'var(--tx2)',fontFamily:'monospace'}}>${fmtD(due)}</span></div>
                <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center'}}><span class="tx3-11">Progress</span><span style=${{fontSize:12,color:'var(--ac)',fontWeight:700,fontFamily:'monospace'}}>${pct}%</span></div>
              </div>
            `:canUpdateStage&&!canEditTask?html`
              <div style=${{background:'var(--sf2)',borderRadius:10,padding:'12px 14px',border:'1px solid var(--bd)',display:'grid',gap:8,marginBottom:4}}>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Title</span><span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${title}</span></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Priority</span><${PB} p=${pri}/></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span class="tx3-11">Due</span><span style=${{fontSize:12,color:'var(--tx2)',fontFamily:'monospace'}}>${fmtD(due)}</span></div>
              </div>
              <div><label class="lbl">Stage</label>
                <select class="sel" value=${stage} onChange=${e=>{
                  const ns=e.target.value;setStage(ns);
                  const ap=STAGE_PCT[ns];if(ap!==null&&ap!==undefined)setPct(ap);
                }}>
                  ${Object.entries(STAGES).map(([k,v])=>html`<option key=${k} value=${k}>${v.label}</option>`)}
                </select>
              </div>
              <div><label class="lbl">Completion: ${pct}%</label>
                <div style=${{display:'flex',alignItems:'center',gap:12}}>
                  <input type="range" min="0" max="100" value=${pct} style=${{flex:1,accentColor:'var(--ac)',cursor:'pointer'}} onChange=${e=>setPct(parseInt(e.target.value))}/>
                  <span style=${{fontSize:13,color:'var(--ac)',fontWeight:700,fontFamily:'monospace',width:34,textAlign:'right'}}>${pct}%</span>
                </div>
              </div>
            `:html`
              <div><label class="lbl">Title *</label>
                <input class="inp" placeholder="Task title..." value=${title} onInput=${e=>setTitle(e.target.value)}/></div>
              <div><label class="lbl">Description</label>
                <textarea class="inp" rows="3" placeholder="Describe the task..." onInput=${e=>setDesc(e.target.value)}>${desc}</textarea></div>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div><label class="lbl">Project</label>
                  <select class="sel" value=${pid} onChange=${e=>setPid(e.target.value)}>
                    ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
                  </select></div>
                <div><label class="lbl">Team <span style=${{fontWeight:400,color:'var(--tx3)',fontSize:10}}>(optional)</span></label>
                  <select class="sel" value=${teamId} onChange=${e=>{setTeamId(e.target.value);setAss('');}}>
                    <option value="">— No team —</option>
                    ${safe(teams).map(t=>html`<option key=${t.id} value=${t.id}>${t.name}</option>`)}
                  </select></div>
              </div>
              <div><label class="lbl">Assignee${teamId?html` <span style=${{fontWeight:400,color:'var(--ac)',fontSize:10}}>(from ${selectedTeam&&selectedTeam.name})</span>`:''}</label>
                  <select class="sel" value=${ass} onChange=${e=>setAss(e.target.value)}>
                    <option value="">Unassigned</option>
                    ${assigneeOptions.map(u=>html`<option key=${u.id} value=${u.id}>${u.name} (${u.role})</option>`)}
                  </select></div>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:11}}>
                <div><label class="lbl">Priority</label>
                  <select class="sel" value=${pri} onChange=${e=>setPri(e.target.value)}>
                    ${Object.entries(PRIS).map(([k,v])=>html`<option key=${k} value=${k}>${v.sym} ${v.label}</option>`)}
                  </select></div>
                <div><label class="lbl">Stage</label>
                  <select class="sel" value=${stage} onChange=${e=>{
                    const ns=e.target.value;setStage(ns);
                    const ap=STAGE_PCT[ns];if(ap!==null&&ap!==undefined)setPct(ap);
                    if(!due&&ns!=='backlog'&&ns!=='blocked'){const days=STAGE_DAYS[ns];if(days>0)setDue(addDays(days));}
                  }}>
                    ${Object.entries(STAGES).map(([k,v])=>html`<option key=${k} value=${k}>${v.label}</option>`)}
                  </select></div>
                <div><label class="lbl">Due Date</label>
                  <input class="inp" type="date" value=${due} min="" onChange=${e=>setDue(e.target.value)} onFocus=${e=>{if(!e.target.value)e.target.value=new Date().toISOString().split('T')[0];}}/></div>
              <div><label class="lbl">Sprint</label><input class="inp" placeholder="e.g. Sprint 3" value=${sprint} onInput=${e=>setSprint(e.target.value)} disabled=${!canEditTask}/></div>
              </div>
              <div><label class="lbl">Completion: ${pct}%</label>
                <div style=${{display:'flex',alignItems:'center',gap:12}}>
                  <input type="range" min="0" max="100" value=${pct} style=${{flex:1,accentColor:'var(--ac)',cursor:'pointer'}} onChange=${e=>setPct(parseInt(e.target.value))}/>
                  <span style=${{fontSize:13,color:'var(--ac)',fontWeight:700,fontFamily:'monospace',width:34,textAlign:'right'}}>${pct}%</span>
                </div>
              </div>
            `}
            ${err?html`<div style=${{color:'var(--rd)',fontSize:12,padding:'7px 11px',background:'rgba(248,113,113,.07)',borderRadius:7}}>${err}</div>`:null}
            ${!isEdit?html`
              <div style=${{borderTop:'1px solid var(--bd)',paddingTop:12}}>
                <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:rmEnabled?12:0}}>
                  <div style=${{display:'flex',alignItems:'center',gap:8,cursor:'pointer'}} onClick=${()=>setRmEnabled(v=>!v)}>
                    <div style=${{width:36,height:20,borderRadius:10,background:rmEnabled?'var(--ac)':'var(--bd)',position:'relative',transition:'background .2s',flexShrink:0}}>
                      <div style=${{position:'absolute',top:2,left:rmEnabled?18:2,width:16,height:16,borderRadius:'50%',background:'#fff',transition:'left .2s',boxShadow:'0 1px 4px rgba(0,0,0,.2)'}}></div>
                    </div>
                    <span style=${{fontSize:12,fontWeight:600,color:'var(--tx)'}}>⏰ Set a reminder</span>
                    ${!rmEnabled?html`<span class="tx3-11">— get notified before this task is due</span>`:null}
                  </div>
                </div>
                ${rmEnabled?html`
                  <div style=${{background:'rgba(170,255,0,.06)',borderRadius:10,border:'1px solid rgba(99,102,241,.18)',padding:'12px 14px',display:'flex',flexDirection:'column',gap:10}}>
                    <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
                      <div>
                        <label class="lbl" style=${{fontSize:10,marginBottom:3}}>Reminder Date</label>
                        <input class="inp" type="date" value=${rmDate} onChange=${e=>setRmDate(e.target.value)} min=${new Date().toISOString().split('T')[0]} onFocus=${e=>{if(!e.target.value)e.target.value=new Date().toISOString().split('T')[0];}} style=${{fontSize:12}}/>
                      </div>
                      <div>
                        <label class="lbl" style=${{fontSize:10,marginBottom:3}}>Reminder Time</label>
                        <input class="inp" type="time" value=${rmTime} onChange=${e=>setRmTime(e.target.value)} style=${{fontSize:12}}/>
                      </div>
                    </div>
                    <div>
                      <label class="lbl" style=${{fontSize:10,marginBottom:4}}>Notify me before</label>
                      <div style=${{display:'flex',gap:6,flexWrap:'wrap'}}>
                        ${[5,10,15,30,60].map(m=>html`<button key=${m} class=${'chip'+(rmMins===m?' on':'')} onClick=${()=>setRmMins(m)} style=${{fontSize:11,padding:'3px 11px'}}>${m<60?m+' min':'1 hr'}</button>`)}
                      </div>
                    </div>
                    <div style=${{fontSize:11,color:'var(--tx3)',display:'flex',alignItems:'center',gap:5}}>
                      <span>🔔</span>
                      <span>You'll be notified${rmMins>0?' '+rmMins+' min before':' at'} ${rmTime||'the set time'} on ${rmDate||'the selected date'} with sound.</span>
                    </div>
                  </div>
                `:null}
              </div>
            `:null}
            <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:6,borderTop:isEdit?'1px solid var(--bd)':'none'}}>
              <button class="btn bg" onClick=${onClose}>${isEdit&&!canEditTask&&!canUpdateStage?'Close':'Cancel'}</button>
              ${onSetReminder&&isEdit?html`<button class="btn bam" style=${{fontSize:12}} onClick=${async()=>{const r=await save({keepOpen:true});if(r!==null){onClose();onSetReminder({id:(task&&task.id)||r.id,title:title,due});}}}>⏰ Set Reminder</button>`:null}
              ${(!isEdit||canEditTask||canUpdateStage)?html`<button class="btn bp" onClick=${save} disabled=${saving}>${saving?html`<span class="spin"></span>`:(isEdit?'Save Changes':'Create Task')}</button>`:null}
              ${!isEdit?html`<button class="btn bg" style=${{fontSize:12}} onClick=${()=>setShowTemplates(true)}>📋 Templates</button>`:null}
                          </div>
          </div>`:null}

        ${tab==='comments'?html`
          <div style=${{display:'flex',flexDirection:'column',gap:10}}>
            ${cmts.length>0?html`<div style=${{display:'flex',flexDirection:'column',gap:8,maxHeight:240,overflowY:'auto'}}>
              ${cmts.map((c,i)=>{
                const au=safe(users).find(u=>u.id===c.uid);
                return html`<div key=${i} style=${{display:'flex',gap:9,padding:'9px 12px',background:'var(--sf2)',borderRadius:9,border:'1px solid var(--bd)'}}>
                  <${Av} u=${au} size=${24}/>
                  <div style=${{flex:1}}>
                    <div style=${{display:'flex',gap:7,alignItems:'center',marginBottom:3}}>
                      <span style=${{fontSize:12,fontWeight:600,color:'var(--tx)'}}>${(au&&au.name)||'?'}</span>
                      <span class="mono-10">${ago(c.ts)}</span>
                    </div>
                    <p style=${{fontSize:13,color:'var(--tx2)',lineHeight:1.5}}>${c.text}</p>
                  </div>
                </div>`;})}
            </div>`:null}
            <div style=${{display:'flex',gap:8}}>
              <input class="inp" style=${{flex:1}} placeholder="Add a comment..." value=${nc}
                onInput=${e=>setNc(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&addCmt()}/>
              <button class="btn bp" onClick=${addCmt}>Post</button>
            </div>
            <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:6,borderTop:'1px solid var(--bd)'}}>
              <button class="btn bg" onClick=${onClose}>Close</button>
              ${onSetReminder&&isEdit?html`<button class="btn bg" style=${{color:'var(--am)'}} onClick=${async()=>{const r=await save({keepOpen:true});if(r!==null){onClose();onSetReminder({id:(task&&task.id),title:title,due});}}}>⏰ Remind</button>`:null}
              <button class="btn bp" onClick=${save} disabled=${saving}>${saving?html`<span class="spin"></span>`:'Save'}</button>
            </div>
          </div>`:null}

        ${tab==='subtasks'?html`
          <div style=${{display:'flex',flexDirection:'column',gap:10}}>
            <!-- Story Points + Task Type row -->
            <div style=${{display:'flex',gap:10,flexWrap:'wrap'}}>
              <div style=${{flex:1,minWidth:140}}>
                <label class="lbl">Task Type</label>
                <select class="sel" value=${taskType} onChange=${e=>setTaskType(e.target.value)} disabled=${!canEditTask}>
                  ${TASK_TYPES.map(t=>html`<option key=${t} value=${t}>${t.charAt(0).toUpperCase()+t.slice(1)}</option>`)}
                </select>
              </div>
              <div style=${{flex:1,minWidth:140}}>
                <label class="lbl">Story Points</label>
                <select class="sel" value=${storyPoints} onChange=${e=>setStoryPoints(parseInt(e.target.value))} disabled=${!canEditTask}>
                  ${[0,1,2,3,5,8,13,21].map(p=>html`<option key=${p} value=${p}>${p===0?'—':p+' pt'+(p>1?'s':'')}</option>`)}
                </select>
              </div>
              <div style=${{flex:1,minWidth:140}}>
                <label class="lbl">Recurring</label>
                <select class="sel" value=${recurring} onChange=${async e=>{setRecurring(e.target.value);if(isEdit)await api.put(`/api/tasks/${task.id}/recurring`,{pattern:e.target.value});}} disabled=${!canEditTask}>
                  <option value="">Not recurring</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </div>
            </div>
            <!-- Labels -->
            <div>
              <label class="lbl">Labels</label>
              <div style=${{display:'flex',gap:6,flexWrap:'wrap',marginBottom:6}}>
                ${taskLabels.map((lbl,i)=>html`
                  <span key=${i} style=${{display:'inline-flex',alignItems:'center',gap:4,padding:'3px 9px',background:'var(--ac3)',color:'var(--ac)',borderRadius:100,fontSize:11,fontWeight:600}}>
                    ${lbl}
                    ${canEditTask?html`<button onClick=${()=>setTaskLabels(prev=>prev.filter((_,j)=>j!==i))} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--ac)',fontSize:12,lineHeight:1,padding:0}}>×</button>`:null}
                  </span>`)}
              </div>
              ${canEditTask?html`
                <div style=${{display:'flex',gap:6}}>
                  <input class="inp" style=${{flex:1,fontSize:12}} placeholder="Add label..." value=${newLabel}
                    onInput=${e=>setNewLabel(e.target.value)}
                    onKeyDown=${e=>{if(e.key==='Enter'&&newLabel.trim()){setTaskLabels(p=>[...p,newLabel.trim()]);setNewLabel('');}}}/>
                  <button class="btn bg" style=${{fontSize:12,padding:'5px 12px'}} onClick=${()=>{if(newLabel.trim()){setTaskLabels(p=>[...p,newLabel.trim()]);setNewLabel('');}}} >+</button>
                </div>`:null}
            </div>
            <!-- Subtasks list -->
            <div>
              <label class="lbl">Subtasks ${subtasks.length>0?html`<span style=${{color:'var(--tx3)',fontWeight:400}}>(${subtasks.filter(s=>s.done).length}/${subtasks.length} done)</span>`:null}</label>
              ${loadingSubtasks?html`<div class="spin" style=${{margin:'10px auto'}}></div>`:null}
              ${subtasks.length>0?html`
                <div style=${{display:'flex',flexDirection:'column',gap:4,marginBottom:8}}>
                  ${subtasks.map(st=>html`
                    <div key=${st.id} style=${{display:'flex',alignItems:'center',gap:8,padding:'7px 10px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)',transition:'all .15s'}}>
                      <input type="checkbox" checked=${!!st.done} onChange=${()=>toggleSubtask(st)}
                        style=${{width:15,height:15,accentColor:'var(--ac)',cursor:'pointer',flexShrink:0}}/>
                      <span class="id-badge id-subtask" style=${{fontSize:9}}>${st.id.slice(0,8)}</span>
                      <span style=${{flex:1,fontSize:13,color:'var(--tx)',textDecoration:st.done?'line-through':'none',opacity:st.done?.55:1}}>${st.title}</span>
                      ${st.done?html`<span style=${{fontSize:10,color:'var(--gn)',fontWeight:600}}>Done</span>`:null}
                      <button onClick=${()=>delSubtask(st.id)} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--rd2)',fontSize:14,lineHeight:1,padding:'0 2px',opacity:.6}}
                        onMouseEnter=${e=>e.currentTarget.style.opacity=1}
                        onMouseLeave=${e=>e.currentTarget.style.opacity=.6}>×</button>
                    </div>`)}
                </div>`:null}
              <!-- Add subtask input -->
              <div style=${{display:'flex',gap:6}}>
                <input class="inp" style=${{flex:1,fontSize:12}} placeholder="Add a subtask..." value=${newSubtask}
                  onInput=${e=>setNewSubtask(e.target.value)}
                  onKeyDown=${e=>{if(e.key==='Enter')addSubtask();}}/>
                <button class="btn bg" style=${{fontSize:12,padding:'5px 14px'}} onClick=${addSubtask} disabled=${!newSubtask.trim()}>+ Add</button>
              </div>
              ${subtasks.length>0?html`
                <div style=${{display:'flex',gap:8,marginTop:8}}>
                  <${Prog} pct=${Math.round(subtasks.filter(s=>s.done).length*100/subtasks.length)} color="var(--ac)"/>
                </div>`:null}
            </div>
          </div>`:null}
        ${tab==='files'&&isEdit?html`<${FileAttachments} taskId=${task.id} readOnly=${cu&&cu.role==='Viewer'}/>`:null}
        ${tab==='time'&&isEdit?html`<${TimeTracker} taskId=${task.id} cu=${cu}/>`:null}
        ${tab==='deps'&&isEdit?html`<${TaskDepsPanel} taskId=${task.id} allTasks=${[]}/>`:null}
      </div>
    </div>`;
}

/* ─── ProjectDetail ───────────────────────────────────────────────────────── */
function ProjectDetail({project,allTasks,allUsers,cu,onClose,onReload,onSetReminder,teams,activeTeam}){
  const [tab,setTab]=useState('tasks');const [edit,setEdit]=useState(false);
  const [name,setName]=useState(project.name||'');const [desc,setDesc]=useState(project.description||'');
  const [tDate,setTDate]=useState(project.target_date||'');const [color,setColor]=useState(project.color||'#aaff00');
  const [members,setMembers]=useState(safe(project.members));const [saving,setSaving]=useState(false);
  const [showNew,setShowNew]=useState(false);const [editTask,setEditTask]=useState(null);
  const [projTeamId,setProjTeamId]=useState((project.team_id)||'');

  const handleTeamChange=useCallback((tid)=>{
    setProjTeamId(tid);
    if(!tid)return;
    const team=safe(teams).find(t=>t.id===tid);
    if(!team)return;
    const teamMids=JSON.parse(team.member_ids||'[]');
    setMembers(prev=>{
      const merged=[...prev];
      teamMids.forEach(mid=>{if(!merged.includes(mid))merged.push(mid);});
      return merged;
    });
  },[teams]);

  const projTasks=useMemo(()=>safe(allTasks).filter(t=>t.project===project.id),[allTasks,project.id]);
  const projUsers=useMemo(()=>safe(members).map(id=>safe(allUsers).find(u=>u.id===id)).filter(Boolean),[members,allUsers]);
  const done=projTasks.filter(t=>t.stage==='completed').length;
  const pc=projTasks.length?Math.round(projTasks.reduce((a,t)=>a+(t.pct||0),0)/projTasks.length):(project.progress||0);
  const stageGroups=KCOLS.map(s=>({s,tasks:projTasks.filter(t=>t.stage===s)})).filter(g=>g.tasks.length>0);

  const saveEdit=async()=>{
    setSaving(true);
    await api.put('/api/projects/'+project.id,{name,description:desc,target_date:tDate,color,members,team_id:projTeamId});
    await onReload();setSaving(false);setEdit(false);
  };
  const delProject=async()=>{if(!window.confirm('Delete project and all its tasks? Cannot be undone.'))return;await api.del('/api/projects/'+project.id);await onReload();onClose();};
  const saveTask=async p=>{
    let r;
    if(p.id&&allTasks.find(t=>t.id===p.id))r=await api.put('/api/tasks/'+p.id,p);
    else r=await api.post('/api/tasks',{...p,project:project.id});
    await onReload();
    return r;
  };
  const delTask=async id=>{await api.del('/api/tasks/'+id);await onReload();};

  return html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&onClose()}>
      <div class="mo mo-xl fi" style=${{height:'90vh',display:'flex',flexDirection:'column',padding:0,overflow:'hidden'}}>

        <div style=${{padding:'20px 24px 0',flexShrink:0}}>
          <div style=${{display:'flex',alignItems:'flex-start',justifyContent:'space-between',marginBottom:14}}>
            <div style=${{display:'flex',alignItems:'center',gap:11}}>
              <div style=${{width:11,height:11,borderRadius:3,background:edit?color:project.color,flexShrink:0,marginTop:4}}></div>
              ${edit?html`<input class="inp" style=${{fontSize:17,fontWeight:700,padding:'4px 8px'}} value=${name} onInput=${e=>setName(e.target.value)}/>`:
                      html`<h2 style=${{fontSize:18,fontWeight:700,color:'var(--tx)'}}>${project.name}</h2>`}
            </div>
            <div style=${{display:'flex',gap:7,flexShrink:0}}>
              ${cu&&cu.role!=='Viewer'&&!edit?html`<button class="btn bg" style=${{fontSize:12,padding:'7px 12px'}} onClick=${()=>setEdit(true)}>✏ Edit</button>`:null}
              ${edit?html`<button class="btn bg" onClick=${()=>setEdit(false)}>Cancel</button><button class="btn bp" onClick=${saveEdit} disabled=${saving}>${saving?html`<span class="spin"></span>`:'Save'}</button>`:null}
              ${cu&&(cu.role==='Admin'||cu.role==='Manager')&&!edit?html`<button class="btn brd" style=${{fontSize:12,padding:'7px 12px'}} onClick=${delProject}>🗑</button>`:null}
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${onClose}>✕</button>
            </div>
          </div>
          ${edit?html`
            <div style=${{display:'flex',flexDirection:'column',gap:11,marginBottom:12}}>
              <textarea class="inp" rows="2" value=${desc} onInput=${e=>setDesc(e.target.value)}>${desc}</textarea>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div><label class="lbl">Target Date</label><input class="inp" type="date" value=${tDate} onChange=${e=>setTDate(e.target.value)} onFocus=${e=>{if(!e.target.value){e.target.value=new Date().toISOString().split('T')[0];}}}/></div>
                <div><label class="lbl">Color</label>
                  <div style=${{display:'flex',gap:7,flexWrap:'wrap',marginTop:4}}>
                    ${PAL.map(c=>html`<button key=${c} onClick=${()=>setColor(c)} style=${{width:26,height:26,borderRadius:6,background:c,border:'3px solid '+(color===c?'#fff':'transparent'),cursor:'pointer',transform:color===c?'scale(1.15)':'none'}}></button>`)}
                  </div>
                </div>
              </div>
                            <div>
                <label class="lbl">Assign to Team <span style=${{fontWeight:400,color:'var(--tx3)',fontSize:10}}>(auto-adds team members)</span></label>
                <select class="sel" value=${projTeamId} onChange=${e=>handleTeamChange(e.target.value)}>
                  <option value="">— No team —</option>
                  ${safe(teams).map(t=>html`<option key=${t.id} value=${t.id}>${t.name} (${JSON.parse(t.member_ids||'[]').length} members)</option>`)}
                </select>
              </div>
              <div><label class="lbl">Members</label><${MemberPicker} allUsers=${allUsers} selected=${members} onChange=${setMembers}/></div>
            </div>
            <div style=${{height:1,background:'var(--bd)',marginBottom:12}}></div>`:html`
            <p style=${{color:'var(--tx2)',fontSize:13,marginBottom:11,lineHeight:1.55}}>${project.description||'No description.'}</p>
            <div style=${{display:'flex',alignItems:'center',gap:18,marginBottom:10}}>
              <div style=${{flex:1}}><${Prog} pct=${pc} color=${project.color}/></div>
              <span style=${{fontSize:11,color:'var(--tx2)',fontFamily:'monospace',fontWeight:700}}>${pc}%</span>
              <span style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace'}}>Due ${fmtD(project.target_date)}</span>
            </div>
            <div style=${{display:'flex',alignItems:'center',gap:14,marginBottom:12}}>
              <span style=${{fontSize:12,color:'var(--tx2)'}}><b style=${{color:'var(--tx)'}}>${projTasks.length}</b> tasks · <b style=${{color:'var(--gn)'}}>${done}</b> done · <b style=${{color:'var(--am)'}}>${projTasks.length-done}</b> open</span>
              <div style=${{display:'flex',alignItems:'center',gap:8}}>
                ${(()=>{const pt=safe(teams).find(t=>t.id===(project.team_id||projTeamId));return pt?html`<span style=${{fontSize:10,color:'var(--ac)',background:'rgba(170,255,0,.1)',border:'1px solid rgba(170,255,0,.25)',padding:'2px 8px',borderRadius:5,fontWeight:600}}>👥 ${pt.name}</span>`:null;})()}
                <div style=${{display:'flex'}}>
                  ${projUsers.slice(0,7).map((m,i)=>html`<div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-8:0,border:'2px solid var(--sf)',borderRadius:'50%',zIndex:7-i}}><${Av} u=${m} size=${24}/></div>`)}
                </div>
              </div>
            </div>`}
          <div style=${{display:'flex',gap:2,background:'var(--sf2)',borderRadius:10,padding:3,width:'fit-content',marginBottom:12}}>
            ${[['tasks','☑ Tasks'],['files','📎 Files'],['members','👥 Members']].map(([id,lbl])=>html`
              <button key=${id} class=${'tb'+(tab===id?' act':'')} onClick=${()=>setTab(id)}>${lbl}</button>`)}
          </div>
          <div style=${{height:1,background:'var(--bd)'}}></div>
        </div>

        <div style=${{flex:1,overflowY:'auto',padding:'16px 24px'}}>
          ${tab==='tasks'?html`
            <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:14}}>
              <span style=${{fontSize:13,color:'var(--tx2)'}}>${projTasks.length} task${projTasks.length!==1?'s':''}</span>
              ${cu&&cu.role!=='Viewer'?html`<button class="btn bp" style=${{fontSize:12,padding:'7px 13px'}} onClick=${()=>setShowNew(true)}>+ Add Task</button>`:null}
            </div>
            ${projTasks.length===0?html`<div style=${{textAlign:'center',padding:'48px 0',color:'var(--tx3)',fontSize:13}}><div style=${{fontSize:28,marginBottom:10}}>📋</div>No tasks yet. Click "+ Add Task" to get started.</div>`:null}
            ${stageGroups.map(g=>{
              const si=STAGES[g.s]||{label:g.s,color:'#94a3b8'};
              return html`<div key=${g.s} style=${{marginBottom:18}}>
                <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:8}}>
                  <div style=${{width:8,height:8,borderRadius:2,background:si.color}}></div>
                  <span style=${{fontSize:11,fontWeight:700,color:'var(--tx2)',textTransform:'uppercase',letterSpacing:.5,fontFamily:'monospace'}}>${si.label}</span>
                  <span style=${{fontSize:10,color:'var(--tx3)',background:'var(--bd)',padding:'1px 6px',borderRadius:4,fontFamily:'monospace'}}>${g.tasks.length}</span>
                </div>
                ${g.tasks.map(tk=>{
                  const au=safe(allUsers).find(u=>u.id===tk.assignee);
                  return html`<div key=${tk.id} class="tkc" style=${{marginBottom:7,display:'flex',gap:10,alignItems:'center'}} onClick=${()=>setEditTask(tk)}>
                    <div style=${{flex:1,minWidth:0}}>
                      <div style=${{display:'flex',gap:7,alignItems:'center',marginBottom:4}}><span class="id-badge id-task">${tk.id}</span><${PB} p=${tk.priority}/></div>
                      <div style=${{fontSize:13,fontWeight:500,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${tk.title}</div>
                      ${tk.pct>0?html`<div style=${{marginTop:5}}><${Prog} pct=${tk.pct} color=${si.color}/></div>`:null}
                    </div>
                    <div style=${{display:'flex',flexDirection:'column',alignItems:'flex-end',gap:5,flexShrink:0}}>
                      ${au?html`<${Av} u=${au} size=${24}/>`:html`<div style=${{width:24,height:24,borderRadius:'50%',background:'var(--bd)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:10,color:'var(--tx3)'}}>?</div>`}
                      ${tk.due?html`<span class="mono-10">${fmtD(tk.due)}</span>`:null}
                    </div>
                  </div>`;
                })}
              </div>`;
            })}`:null}
          ${tab==='files'?html`<${FileAttachments} projectId=${project.id} readOnly=${cu&&cu.role==='Viewer'}/>`:null}
          ${tab==='members'?html`
            <div style=${{display:'flex',flexDirection:'column',gap:8}}>
              <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:6}}>
                <span style=${{color:'var(--tx2)',fontSize:13}}>${projUsers.length} members</span>
                ${cu&&cu.role!=='Viewer'?html`<button class="btn bg" style=${{fontSize:12,padding:'7px 12px'}} onClick=${()=>{setEdit(true);setTab('tasks');}}>Edit Members</button>`:null}
              </div>
              ${projUsers.map(m=>html`<div key=${m.id} style=${{display:'flex',alignItems:'center',gap:12,padding:'11px 14px',background:'var(--sf2)',borderRadius:10,border:'1px solid var(--bd)'}}>
                <${Av} u=${m} size=${36}/>
                <div style=${{flex:1}}><div style=${{fontSize:13,fontWeight:600,color:'var(--tx)'}}>${m.name}</div><div style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace'}}>${m.email}</div></div>
                <span class="badge" style=${{background:'var(--ac)22',color:'var(--ac2)'}}>${m.role}</span>
              </div>`)}
            </div>`:null}
        </div>
      </div>

      ${showNew?html`<${TaskModal} task=${null} onClose=${()=>setShowNew(false)} onSave=${saveTask} projects=${[project]} users=${projUsers.length?projUsers:allUsers} cu=${cu} defaultPid=${project.id} onSetReminder=${onSetReminder} teams=${teams||[]} activeTeam=${activeTeam}/>`:null}
      ${editTask?html`<${TaskModal} task=${editTask} onClose=${()=>setEditTask(null)} onSave=${saveTask} onDel=${delTask} projects=${[project]} users=${projUsers.length?projUsers:allUsers} cu=${cu} defaultPid=${project.id} onSetReminder=${onSetReminder} teams=${teams||[]}/>`:null}
    </div>`;
}

/* ─── ProjectsView ────────────────────────────────────────────────────────── */
function ProjectsView({projects,tasks,users,cu,reload,onSetReminder,teams,activeTeam,initialProjectId,onClearInitial}){
  const [showNew,setShowNew]=useState(false);const [detail,setDetail]=useState(null);

  // Open project from initialProjectId prop OR directly from URL path /projects/<id>
  useEffect(()=>{
    if(safe(projects).length===0) return;
    // Check URL for project id first
    try{
      const parts=window.location.pathname.split('/');
      if(parts[1]==='projects'&&parts[2]){
        const urlProject=safe(projects).find(proj=>proj.id===parts[2]);
        if(urlProject){setDetail(urlProject);return;}
      }
    }catch(e){}
    // Fall back to prop
    if(initialProjectId){
      const p=safe(projects).find(proj=>proj.id===initialProjectId);
      if(p){setDetail(p);onClearInitial&&onClearInitial();}
    }
  },[initialProjectId,projects.length]); // re-run when projects load
  const [name,setName]=useState('');const [desc,setDesc]=useState('');
  const [sDate,setSDate]=useState('');const [tDate,setTDate]=useState('');
  const [color,setColor]=useState('#2563eb');const [members,setMembers]=useState([]);const [err,setErr]=useState('');
  const [search,setSearch]=useState('');
  const [sortBy,setSortBy]=useState('newest');
  const [viewMode,setViewMode]=useState('grid');
  const [projTeam,setProjTeam]=useState('');

  useEffect(()=>{if(detail){
    const fresh=safe(projects).find(p=>p.id===detail.id);if(fresh)setDetail(fresh);
    // Push clean URL with project id
    try{
      const slug=detail.id;
      history.pushState(null,'','/projects/'+slug);
      document.title='VEWIT — '+detail.name+' | Projects';
    }catch(e){}
  } else {
    // Back to /projects when detail closes
    try{
      if(window.location.pathname.startsWith('/projects/')){
        history.pushState(null,'','/projects');
        document.title='VEWIT — Projects | AI-Powered Team Collaboration';
      }
    }catch(e){}
  }},[detail]);
  useEffect(()=>{if(activeTeam)setProjTeam(activeTeam.id);},[activeTeam]);

  // Handle browser back/forward within projects
  useEffect(()=>{
    const onPop=()=>{
      const parts=window.location.pathname.split('/');
      if(parts[1]==='projects'&&parts[2]){
        const p=safe(projects).find(proj=>proj.id===parts[2]);
        if(p){setDetail(p);return;}
      }
      if(window.location.pathname==='/projects'||window.location.pathname==='/projects/'){
        setDetail(null);
      }
    };
    window.addEventListener('popstate',onPop);
    return()=>window.removeEventListener('popstate',onPop);
  },[projects]);

  const create=async()=>{
    if(!name.trim()){setErr('Project name required.');return;}setErr('');
    try{
      let mems=members.includes(cu.id)?members:[cu.id,...members];
      if(projTeam){
        const team=teams.find(t=>t.id===projTeam);
        if(team){
          const teamMids=JSON.parse(team.member_ids||'[]');
          teamMids.forEach(mid=>{if(!mems.includes(mid))mems.push(mid);});
        }
      }
      const newProj=await api.post('/api/projects',{name:name.trim(),description:desc,startDate:sDate,targetDate:tDate,color,members:mems,team_id:projTeam||''});
      if(newProj&&newProj.error){setErr(newProj.error);return;}
      if(!newProj||!newProj.id){setErr('Failed to create project. Please try again.');return;}
      setShowNew(false);setName('');setDesc('');setSDate('');setTDate('');setColor('#2563eb');setMembers([]);setProjTeam('');
      await new Promise(r=>setTimeout(r,300));
      await reload();
      setTimeout(()=>reload(),1000);
    }catch(e){setErr('Error creating project: '+(e.message||'Unknown error'));}
  };

  const filteredProjects=useMemo(()=>{
    let rows=[...safe(projects)];
    if(search.trim()){const q=search.toLowerCase();rows=rows.filter(p=>p.name.toLowerCase().includes(q)||(p.description||'').toLowerCase().includes(q));}
    rows.sort((a,b)=>{
      if(sortBy==='newest') return new Date(b.created||0)-new Date(a.created||0);
      if(sortBy==='oldest') return new Date(a.created||0)-new Date(b.created||0);
      if(sortBy==='name')   return a.name.localeCompare(b.name);
      if(sortBy==='progress'){
        const getP=proj=>{const pt=safe(tasks).filter(t=>t.project===proj.id);return pt.length?Math.round(pt.reduce((s,t)=>s+(t.pct||0),0)/pt.length):(proj.progress||0);};
        return getP(b)-getP(a);
      }
      if(sortBy==='tasks') return safe(tasks).filter(t=>t.project===b.id).length-safe(tasks).filter(t=>t.project===a.id).length;
      return 0;
    });
    return rows;
  },[projects,search,sortBy,tasks]);

  return html`
    <div class="fi" style=${{height:'100%',overflow:'hidden',display:'flex',flexDirection:'column'}}>

            <div style=${{flexShrink:0,padding:'10px 16px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:8,flexWrap:'wrap',background:'var(--bg)'}}>
                <div style=${{position:'relative',flex:'1',minWidth:140,maxWidth:280}}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
            style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" style=${{paddingLeft:26,height:28,fontSize:12}} placeholder="Search projects..."
            value=${search} onInput=${e=>setSearch(e.target.value)}/>
        </div>
        <span style=${{fontSize:11,color:'var(--tx3)',whiteSpace:'nowrap',flexShrink:0}}>${filteredProjects.length} of ${safe(projects).length}</span>

                <div style=${{display:'flex',background:'var(--sf2)',borderRadius:7,padding:2,gap:1,flexShrink:0}}>
          ${[['newest','🕐 Newest'],['oldest','🕐 Oldest'],['name','🔤 Name'],['progress','📊 Progress'],['tasks','📋 Tasks']].map(([k,lbl])=>html`
            <button key=${k} class=${'tb'+(sortBy===k?' act':'')} style=${{fontSize:10,padding:'2px 7px'}}
              onClick=${()=>setSortBy(k)}>${lbl}</button>`)}
        </div>

                <div style=${{display:'flex',background:'var(--sf2)',borderRadius:7,padding:2,gap:1,flexShrink:0}}>
          <button class=${'tb'+(viewMode==='grid'?' act':'')} style=${{fontSize:12,padding:'2px 8px'}}
            onClick=${()=>setViewMode('grid')} title="Card view">⊞</button>
          <button class=${'tb'+(viewMode==='compact'?' act':'')} style=${{fontSize:12,padding:'2px 8px'}}
            onClick=${()=>setViewMode('compact')} title="Compact list">☰</button>
        </div>

        ${search?html`<button class="btn bg" style=${{fontSize:11,padding:'3px 8px',flexShrink:0}}
          onClick=${()=>setSearch('')}>✕</button>`:null}

        ${cu&&cu.role!=='Viewer'&&cu.role!=='Developer'&&cu.role!=='Tester'?html`
          <button class="btn bp" style=${{marginLeft:'auto',whiteSpace:'nowrap',flexShrink:0}}
            onClick=${()=>setShowNew(true)}>+ New Project</button>`:null}
      </div>

            <div style=${{flex:1,minHeight:0,overflowY:'auto',padding:'12px 16px'}}>

        ${filteredProjects.length===0?html`
          <div style=${{textAlign:'center',padding:'60px 0',color:'var(--tx3)'}}>
            <div style=${{fontSize:40,marginBottom:12}}>🔍</div>
            <div style=${{fontSize:14,fontWeight:600,color:'var(--tx2)',marginBottom:6}}>${search?`No projects match "${search}"`:'No projects yet'}</div>
            ${search?html`<button class="btn bg" style=${{fontSize:12}} onClick=${()=>setSearch('')}>Clear search</button>`:null}
          </div>`:null}

                ${viewMode==='grid'&&filteredProjects.length>0?html`
          <div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(275px,1fr))',gap:12}}>
            ${filteredProjects.map(p=>{
              const pt=safe(tasks).filter(t=>t.project===p.id);
              const done=pt.filter(t=>t.stage==='completed').length;
              const pc=pt.length?Math.round(pt.reduce((a,t)=>a+(t.pct||0),0)/pt.length):(p.progress||0);
              const mems=safe(p.members).map(id=>safe(users).find(u=>u.id===id)).filter(Boolean);
              const fmtShort=d=>{if(!d)return '';const dt=new Date(d);return dt.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});};
              const daysWorked=p=>{
                const s=p.start_date?new Date(p.start_date):null;
                const e=p.target_date?new Date(p.target_date):null;
                if(!s||!e)return null;
                const today=new Date();today.setHours(0,0,0,0);s.setHours(0,0,0,0);e.setHours(0,0,0,0);
                const worked=Math.max(0,Math.round((Math.min(today,e)-s)/86400000));
                const total=Math.max(1,Math.round((e-s)/86400000));
                return {worked,total};
              };
              return html`
                <div key=${p.id} class="card"
                  style=${{cursor:'pointer',transition:'all .15s',borderTop:'3px solid '+p.color,padding:'14px'}}
                  onClick=${()=>setDetail(p)}
                  onMouseEnter=${e=>{e.currentTarget.style.transform='translateY(-2px)';e.currentTarget.style.boxShadow='var(--sh)';}}
                  onMouseLeave=${e=>{e.currentTarget.style.transform='';e.currentTarget.style.boxShadow='';}}>
                  <div style=${{display:'flex',alignItems:'flex-start',justifyContent:'space-between',marginBottom:7}}>
                    <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',flex:1,marginRight:6,lineHeight:1.3}}>${p.name}</h3>
                    <span class="badge" style=${{background:p.color+'22',color:p.color,flexShrink:0,fontSize:9}}>${pt.length} tasks</span>
                  </div>
                  <p style=${{fontSize:11,color:'var(--tx2)',lineHeight:1.5,marginBottom:9,display:'-webkit-box',WebkitLineClamp:2,WebkitBoxOrient:'vertical',overflow:'hidden'}}>${p.description||'No description.'}</p>
                  <div style=${{marginBottom:9}}>
                    <div style=${{display:'flex',justifyContent:'space-between',marginBottom:3}}>
                      <span style=${{fontSize:9,color:'var(--tx3)',fontWeight:600,textTransform:'uppercase',letterSpacing:'.5px'}}>Progress</span>
                      <span style=${{fontSize:9,color:'var(--tx2)',fontFamily:'monospace',fontWeight:700}}>${pc}%</span>
                    </div>
                    <${Prog} pct=${pc} color=${p.color}/>
                  </div>
                  <div style=${{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:5,marginBottom:9}}>
                    ${[['Tasks',pt.length,'var(--tx)'],['Done',done,'var(--gn)'],['Open',pt.length-done,'var(--am)']].map(([l,v,c])=>html`
                      <div key=${l} style=${{textAlign:'center',padding:'6px 4px',background:'var(--sf2)',borderRadius:7,border:'1px solid var(--bd2)'}}>
                        <div style=${{fontSize:15,fontWeight:700,color:c}}>${v}</div>
                        <div style=${{fontSize:8,color:'var(--tx3)',marginTop:1,textTransform:'uppercase',letterSpacing:'.5px'}}>${l}</div>
                      </div>`)}
                  </div>
                  <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                    <div style=${{display:'flex'}}>
                      ${mems.slice(0,5).map((m,i)=>html`<div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-6:0,border:'2px solid var(--sf)',borderRadius:'50%',zIndex:5-i}}><${Av} u=${m} size=${20}/></div>`)}
                    </div>
                    <div style=${{display:'flex',alignItems:'center',gap:5}}>
                      ${(()=>{const pt=safe(teams).find(t=>t.id===p.team_id);return pt?html`<span style=${{fontSize:9,color:'var(--tx2)',background:'var(--sf2)',border:'1px solid var(--bd)',padding:'1px 6px',borderRadius:4,fontWeight:600}}>${pt.name}</span>`:
                        cu&&(cu.role==='Admin'||cu.role==='Manager')&&safe(teams).length>0?html`<select style=${{fontSize:9,padding:'1px 4px',borderRadius:4,border:'1px solid var(--bd)',background:'var(--sf2)',color:'var(--tx3)',cursor:'pointer'}}
                          value="" onChange=${async e=>{if(!e.target.value)return;await api.post('/api/projects/bulk-assign-team',{team_id:e.target.value,project_ids:[p.id]});reload();}}
                          onClick=${e=>e.stopPropagation()}>
                          <option value="">+ Team</option>
                          ${safe(teams).map(t=>html`<option key=${t.id} value=${t.id}>${t.name}</option>`)}
                        </select>`:null;})()}
                      <div style=${{display:'flex',flexDirection:'column',alignItems:'flex-end',gap:2}}>
                        <span style=${{fontSize:9,color:'var(--tx3)'}}>
                          ${p.start_date?fmtShort(p.start_date)+' – ':''}${fmtShort(p.target_date)||'No date'}
                        </span>
                        ${(()=>{const dw=daysWorked(p);return dw?html`
                          <span style=${{fontSize:9,fontWeight:600,color:dw.worked>=dw.total?'var(--am)':'var(--cy)'}}>
                            ${dw.worked} / ${dw.total} days
                          </span>`:null;})()}
                      </div>
                    </div>
                  </div>
                </div>`;
            })}
          </div>`:null}

        ${viewMode==='compact'&&filteredProjects.length>0?html`
          <div style=${{display:'flex',flexDirection:'column',gap:3}}>
                        <div style=${{display:'grid',gridTemplateColumns:'1fr 90px 50px 50px 50px 90px',gap:8,padding:'4px 12px', fontSize:9,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5}}>
              <span>Project</span><span>Progress</span><span style=${{textAlign:'center'}}>Tasks</span>
              <span style=${{textAlign:'center'}}>Done</span><span style=${{textAlign:'center'}}>Open</span>
              <span style=${{textAlign:'right'}}>End Date</span>
            </div>
            ${filteredProjects.map(p=>{
              const pt=safe(tasks).filter(t=>t.project===p.id);
              const done=pt.filter(t=>t.stage==='completed').length;
              const pc=pt.length?Math.round(pt.reduce((a,t)=>a+(t.pct||0),0)/pt.length):(p.progress||0);
              return html`
                <div key=${p.id}
                  style=${{display:'grid',gridTemplateColumns:'1fr 90px 50px 50px 50px 90px',gap:8, alignItems:'center',padding:'8px 12px', background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:8, cursor:'pointer',transition:'background .1s',borderLeft:'3px solid '+p.color}}
                  onClick=${()=>setDetail(p)}
                  onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'}
                  onMouseLeave=${e=>e.currentTarget.style.background='var(--sf)'}>
                                    <div style=${{minWidth:0}}>
                    <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${p.name}</div>
                    <div style=${{fontSize:10,color:'var(--tx3)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginTop:1}}>${p.description||'—'}</div>
                  </div>
                                    <div style=${{display:'flex',alignItems:'center',gap:5}}>
                    <div style=${{flex:1,height:4,background:'var(--bd)',borderRadius:100,overflow:'hidden'}}>
                      <div style=${{height:'100%',width:pc+'%',background:p.color,borderRadius:100}}></div>
                    </div>
                    <span style=${{fontSize:9,fontFamily:'monospace',color:'var(--tx3)',flexShrink:0,minWidth:24}}>${pc}%</span>
                  </div>
                                    <div style=${{textAlign:'center',fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em'}}>${pt.length}</div>
                  <div style=${{textAlign:'center',fontSize:13,fontWeight:700,color:'var(--gn)'}}>${done}</div>
                  <div style=${{textAlign:'center',fontSize:13,fontWeight:700,color:'var(--am)'}}>${pt.length-done}</div>
                                    <div style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textAlign:'right'}}>
                    ${p.target_date?new Date(p.target_date).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'2-digit'}):'—'}
                  </div>
                </div>`;
            })}
          </div>`:null}
      </div>

      ${showNew?html`
        <div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowNew(false)}>
          <div class="mo fi" style=${{maxWidth:520}}>
            <div style=${{display:'flex',justifyContent:'space-between',marginBottom:18}}>
              <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)'}}>New Project</h2>
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setShowNew(false)}>✕</button>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:12}}>
              <div><label class="lbl">Project Name *</label>
                <input class="inp" placeholder="E.g. Google SecOps Integration" value=${name} onInput=${e=>setName(e.target.value)}/></div>
              <div><label class="lbl">Description</label>
                <textarea class="inp" rows="3" placeholder="What is this project about?" onInput=${e=>setDesc(e.target.value)}>${desc}</textarea></div>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div><label class="lbl">Start Date</label>
                  <input class="inp" type="date" value=${sDate} onChange=${e=>setSDate(e.target.value)} onFocus=${e=>{if(!e.target.value)e.target.value=new Date().toISOString().split('T')[0];}}/></div>
                <div><label class="lbl">End Date</label>
                  <input class="inp" type="date" value=${tDate} onChange=${e=>setTDate(e.target.value)} onFocus=${e=>{if(!e.target.value){e.target.value=new Date().toISOString().split('T')[0];}}}/></div>
              </div>
              <div><label class="lbl">Color</label>
                <div style=${{display:'flex',gap:7,flexWrap:'wrap',marginTop:4}}>
                  ${PAL.map(c=>html`<button key=${c} onClick=${()=>setColor(c)}
                    style=${{width:26,height:26,borderRadius:6,background:c, border:'3px solid '+(color===c?'#fff':'transparent'), cursor:'pointer',transform:color===c?'scale(1.15)':'none'}}></button>`)}
                </div>
              </div>
              <div><label class="lbl">Add Members</label>
                <${MemberPicker} allUsers=${users} selected=${members} onChange=${setMembers}/></div>
              ${cu&&(cu.role==='Admin'||cu.role==='Manager')&&teams.length>0?html`
              <div><label class="lbl">Assign to Team <span style=${{fontSize:10,color:'var(--tx3)',fontWeight:400}}>(optional — adds all team members)</span></label>
                <select class="sel" value=${projTeam} onChange=${e=>setProjTeam(e.target.value)}>
                  <option value="">— No team —</option>
                  ${safe(teams).map(t=>{
                    const mids=JSON.parse(t.member_ids||'[]');
                    return html`<option key=${t.id} value=${t.id}>${t.name} (${mids.length} member${mids.length!==1?'s':''})</option>`;
                  })}
                </select>
              </div>`:null}
              ${err?html`<div style=${{color:'var(--rd)',fontSize:12,padding:'7px 11px',background:'rgba(248,113,113,.07)',borderRadius:7}}>${err}</div>`:null}
              <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:4}}>
                <button class="btn bg" onClick=${()=>setShowNew(false)}>Cancel</button>
                <button class="btn bp" onClick=${create}>Create Project</button>
              </div>
            </div>
          </div>
        </div>`:null}

      ${detail?html`<${ProjectDetail} project=${detail} allTasks=${tasks} allUsers=${users} cu=${cu}
        onClose=${()=>setDetail(null)} onReload=${reload} onSetReminder=${onSetReminder} teams=${teams} activeTeam=${activeTeam}/>`:null}
    </div>`;
}

/* ─── TasksView with inline stage dropdown ────────────────────────────────── */
const STAGE_DAYS={backlog:0,planning:7,development:21,code_review:28,testing:35,uat:42,release:49,production:56,completed:60,blocked:0};
const STAGE_PCT={backlog:0,planning:10,development:35,code_review:55,testing:70,uat:80,release:90,production:95,completed:100,blocked:null};
function addDays(n){const d=new Date();d.setDate(d.getDate()+n);return d.toISOString().split('T')[0];}

function TasksView({tasks,projects,users,cu,reload,onSetReminder,initialStage,initialPriority,initialAssignee,teams,activeTeam}){
  const [mode,setMode]=useState('kanban');
  const [pid,setPid]=useState('all');
  const [teamF,setTeamF]=useState('all');
  const [priF,setPriF]=useState(initialPriority||'all');
  const [stageF,setStageF]=useState(initialStage||'all');
  const [assF,setAssF]=useState(initialAssignee==='me'?(cu&&cu.id)||'all':'all');
  const [dueF,setDueF]=useState('all');
  const [typeF,setTypeF]=useState('all');
  const [search,setSearch]=useState('');
  const [showFilters,setShowFilters]=useState(!!(initialStage||initialPriority));
  const [showResolved,setShowResolved]=useState(true);
  const [sortCol,setSortCol]=useState(null);
  const [sortDir,setSortDir]=useState('asc');
  const [sprintFilter,setSprintFilter]=useState('');
  const [editT,setEditT]=useState(null);const [newT,setNewT]=useState(false);
  const [csvImporting,setCsvImporting]=useState(false);
  const [csvResult,setCsvResult]=useState(null);
  const csvRef=useRef(null);
  useEffect(()=>{
    const h=(e)=>{
      if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.isContentEditable)return;
      if(e.key==='n'||e.key==='N'){e.preventDefault();setNewT(true);}
      if(e.key==='Escape'){setEditT(null);setNewT(false);}
    };
    document.addEventListener('keydown',h);
    return()=>document.removeEventListener('keydown',h);
  },[]);

  useEffect(()=>{
    if(initialStage){setStageF(initialStage);setShowFilters(true);}
    if(initialStage==='completed'){setShowResolved(true);}
    if(initialPriority){setPriF(initialPriority);setShowFilters(true);}
    if(initialAssignee==='me'&&cu){setAssF(cu.id);setShowFilters(true);}
  },[initialStage,initialPriority,initialAssignee,cu]);

  const RESOLVED_STAGES=new Set(['completed']);

  const activeFilters=[pid,teamF,priF,stageF,assF,dueF].filter(v=>v!=='all').length;
  const clearAll=()=>{setPid('all');setTeamF('all');setPriF('all');setStageF('all');setAssF('all');setDueF('all');setSearch('');setShowResolved(false);};

  const teamFilterMemberIds=useMemo(()=>{
    if(teamF==='all')return null;
    const team=safe(teams).find(t=>t.id===teamF);
    return team?new Set(JSON.parse(team.member_ids||'[]')):null;
  },[teamF,teams]);

  const filtered=useMemo(()=>{
    const today=new Date();today.setHours(0,0,0,0);
    const endOfWeek=new Date(today);endOfWeek.setDate(today.getDate()+7);
    const endOfMonth=new Date(today);endOfMonth.setDate(today.getDate()+30);
    return safe(tasks).filter(t=>{
      if(!showResolved && RESOLVED_STAGES.has(t.stage) && stageF!=='completed') return false;
      if(pid!=='all'&&t.project!==pid)return false;
      if(priF!=='all'&&t.priority!==priF)return false;
      if(stageF!=='all'&&t.stage!==stageF)return false;
      if(assF!=='all'&&t.assignee!==assF)return false;
      if(teamF!=='all'){
        const byTeamId=t.team_id&&t.team_id===teamF;
        const byAssignee=teamFilterMemberIds&&t.assignee&&teamFilterMemberIds.has(t.assignee);
        if(!byTeamId&&!byAssignee)return false;
      }
      if(search){const sq=search.toLowerCase();if(!t.title.toLowerCase().includes(sq)&&!t.id.toLowerCase().includes(sq))return false;}
      if(dueF!=='all'&&t.due){
        const d=new Date(t.due);d.setHours(0,0,0,0);
        if(dueF==='overdue'&&d>=today)return false;
        if(dueF==='today'&&d.getTime()!==today.getTime())return false;
        if(dueF==='week'&&(d<today||d>endOfWeek))return false;
        if(dueF==='month'&&(d<today||d>endOfMonth))return false;
      } else if(dueF!=='all'&&!t.due) return false;
      if(typeF!=='all'&&t.task_type!==typeF)return false;
      if(sprintFilter&&t.sprint!==sprintFilter)return false;
      return true;
    });
  },[tasks,pid,teamF,teamFilterMemberIds,priF,stageF,assF,dueF,search,showResolved,sprintFilter,typeF]);

  const toggleSort=col=>{if(sortCol===col)setSortDir(d=>d==='asc'?'desc':'asc');else{setSortCol(col);setSortDir('asc');}};

  const PRI_ORD={critical:0,high:1,medium:2,low:3};
  const STAGE_ORD={backlog:0,planning:1,development:2,code_review:3,testing:4,uat:5,release:6,production:7,completed:8,blocked:9};

  const sorted=useMemo(()=>{
    if(!sortCol)return filtered;
    return [...filtered].sort((a,b)=>{
      let av,bv;
      if(sortCol==='assignee'){const au=safe(users).find(u=>u.id===a.assignee);const bu=safe(users).find(u=>u.id===b.assignee);av=(au&&au.name)||'';bv=(bu&&bu.name)||'';}
      else if(sortCol==='priority'){av=PRI_ORD[a.priority]??99;bv=PRI_ORD[b.priority]??99;return sortDir==='asc'?av-bv:bv-av;}
      else if(sortCol==='stage'){av=STAGE_ORD[a.stage]??99;bv=STAGE_ORD[b.stage]??99;return sortDir==='asc'?av-bv:bv-av;}
      else if(sortCol==='due'){av=a.due||'9999';bv=b.due||'9999';}
      else if(sortCol==='pct'){av=a.pct||0;bv=b.pct||0;return sortDir==='asc'?av-bv:bv-av;}
      return sortDir==='asc'?av.localeCompare(bv):bv.localeCompare(av);
    });
  },[filtered,sortCol,sortDir,users]);

  const saveT=async p=>{let r;if(p.id&&safe(tasks).find(t=>t.id===p.id))r=await api.put('/api/tasks/'+p.id,p);else r=await api.post('/api/tasks',p);reload();return r;};
  const delT=async id=>{await api.del('/api/tasks/'+id);reload();};
  const quickStage=async(tid,stage)=>{
    const autoPct=STAGE_PCT[stage];
    const payload={stage};
    if(autoPct!==null&&autoPct!==undefined)payload.pct=autoPct;
    await api.put('/api/tasks/'+tid,payload);reload();
  };

  const importCsv=async(e)=>{
    const file=e.target.files&&e.target.files[0];
    if(!file)return;
    setCsvImporting(true);setCsvResult(null);
    const fd=new FormData();fd.append('file',file);
    const r=await api.upload('/api/import/csv',fd);
    setCsvImporting(false);
    setCsvResult(r);
    reload();
    e.target.value='';
  };

  return html`
    <div class="fi" style=${{display:'flex',flexDirection:'column',height:'100%',overflow:'hidden'}}>
      <div style=${{padding:'8px 18px',borderBottom:'1px solid var(--bd)',background:'var(--sf)',flexShrink:0}}>
        <div style=${{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
          <div style=${{position:'relative',flex:'1 1 160px',minWidth:130}}>
            <span style=${{position:'absolute',left:10,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',fontSize:13}}>🔍</span>
            <input class="inp" style=${{paddingLeft:30}} placeholder="Search by task ID or name (e.g. T-015)" value=${search} onInput=${e=>setSearch(e.target.value)}/>
          </div>
          <button class=${'btn bg'+(showFilters?' act':'')} style=${{position:'relative',padding:'8px 13px',fontSize:12,borderColor:activeFilters>0?'var(--ac)':'',color:activeFilters>0?'var(--ac2)':''}}
            onClick=${()=>setShowFilters(!showFilters)}>
            ⚙ Filters${activeFilters>0?html` <span style=${{background:'var(--ac)',color:'#fff',borderRadius:8,fontSize:9,padding:'1px 5px',marginLeft:3,fontFamily:'monospace'}}>${activeFilters}</span>`:''}
          </button>
          ${assF!=='all'&&assF===cu.id?html`
            <div style=${{display:'flex',alignItems:'center',gap:6,padding:'5px 10px 5px 8px',background:'var(--ac3)',border:'1px solid var(--ac)',borderRadius:20,flexShrink:0}}>
              <div style=${{width:6,height:6,borderRadius:'50%',background:'var(--ac)',flexShrink:0}}></div>
              <span style=${{fontSize:11,fontWeight:700,color:'var(--tx2)'}}>My Tasks</span>
              <button onClick=${()=>setAssF('all')}
                style=${{background:'none',border:'none',cursor:'pointer',color:'var(--ac)',fontSize:14,lineHeight:1,padding:'0 2px'}}>×</button>
            </div>`:null}
          ${activeFilters>0?html`<button class="btn bam" style=${{padding:'7px 11px',fontSize:11}} onClick=${clearAll}>✕ Clear</button>`:null}
                    <div style=${{display:'flex',background:'var(--sf2)',borderRadius:9,padding:3,gap:2,flex:'0 0 auto'}}>
            <button class=${'tb'+(mode==='kanban'?' act':'')} onClick=${()=>setMode('kanban')}>⊞ Board</button>
            <button class=${'tb'+(mode==='list'?' act':'')} onClick=${()=>setMode('list')}>☰ List</button>
          </div>
          <input ref=${csvRef} type="file" accept=".csv" style=${{display:'none'}} onChange=${importCsv}/>
          ${cu&&(cu.role==='Admin'||cu.role==='Manager'||cu.role==='TeamLead')?html`
          <div style=${{display:'flex',gap:0,flex:'0 0 auto',borderRadius:100,overflow:'hidden',border:'1px solid var(--bd)'}}>
            <button style=${{fontSize:12,padding:'7px 12px',background:'transparent',border:'none',borderRight:'1px solid var(--bd)',cursor:'pointer',color:'var(--tx2)',fontWeight:600,display:'inline-flex',alignItems:'center',gap:5,transition:'background .12s'}}
              onClick=${()=>csvRef.current&&csvRef.current.click()} disabled=${csvImporting} title="Import tasks from CSV"
              onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'} onMouseLeave=${e=>e.currentTarget.style.background='transparent'}>
              ${csvImporting?html`<span class="spin"></span>`:html`<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`}
              Import
            </button>
            <a href="/api/export/csv" style=${{fontSize:12,padding:'7px 12px',background:'transparent',color:'var(--tx2)',fontWeight:600,textDecoration:'none',display:'inline-flex',alignItems:'center',gap:5,transition:'background .12s'}}
              title="Export tasks to CSV"
              onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'} onMouseLeave=${e=>e.currentTarget.style.background='transparent'}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Export
            </a>
          </div>`:null}
          <button class=${'btn '+(showResolved?'bg':'bp')} style=${{flex:'0 0 auto',fontSize:12,padding:'7px 13px'}}
            onClick=${()=>setShowResolved(v=>!v)}
            title=${showResolved?'Click to hide completed tasks':'Click to show completed tasks'}>
            ${showResolved?'Hide Completed':'Show Completed'}
          </button>
          <button class="btn bp" style=${{flex:'0 0 auto',fontSize:12,padding:'7px 13px'}} onClick=${()=>setNewT(true)}>+ New Task</button>
        </div>
        ${csvResult?html`<div style=${{marginTop:8,padding:'8px 12px',borderRadius:8,fontSize:12,background:csvResult.error?'rgba(185,28,28,0.10)':'rgba(21,128,61,0.12)',border:'1px solid '+(csvResult.error?'rgba(255,68,68,.2)':'rgba(62,207,110,.2)'),color:csvResult.error?'var(--rd)':'var(--gn)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <span>${csvResult.error?'✕ '+csvResult.error:'✓ Imported '+csvResult.created_tasks+' task(s)'+(csvResult.created_projects>0?' & '+csvResult.created_projects+' project(s)':'')+(csvResult.errors&&csvResult.errors.length?' · '+csvResult.errors.length+' skipped':'')}</span>
          <button class="btn bg" style=${{padding:'3px 8px',fontSize:10}} onClick=${()=>setCsvResult(null)}>✕</button>
        </div>`:null}
        ${showFilters?html`
          <div style=${{display:'flex',gap:8,flexWrap:'wrap',marginTop:9,paddingTop:9,borderTop:'1px solid var(--bd)'}}>
            ${cu&&(cu.role==='Admin'||cu.role==='Manager')?html`
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Team</label>
              <select class="sel" style=${{width:140,fontSize:12}} value=${teamF} onChange=${e=>setTeamF(e.target.value)}>
                <option value="all">All Teams</option>
                ${safe(teams).map(t=>html`<option key=${t.id} value=${t.id}>${t.name}</option>`)}
              </select>
            </div>`:null}
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Project</label>
              <select class="sel" style=${{width:155,fontSize:12}} value=${pid} onChange=${e=>setPid(e.target.value)}>
                <option value="all">All Projects</option>
                ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Assignee</label>
              <button class=${'chip'+(assF===cu.id?' on':'')} style=${{fontSize:11,marginBottom:5,display:'inline-flex',alignItems:'center',gap:5}}
                onClick=${()=>setAssF(assF===cu.id?'all':cu.id)}>
                👤 My Tasks only
              </button>
              <select class="sel" style=${{width:140,fontSize:12}} value=${assF} onChange=${e=>setAssF(e.target.value)}>
                <option value="all">All Members</option>
                ${safe(users).map(u=>html`<option key=${u.id} value=${u.id}>${u.name}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Priority</label>
              <select class="sel" style=${{width:125,fontSize:12}} value=${priF} onChange=${e=>setPriF(e.target.value)}>
                <option value="all">All Priority</option>
                ${Object.entries(PRIS).map(([k,v])=>html`<option key=${k} value=${k}>${v.sym} ${v.label}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Stage</label>
              <select class="sel" style=${{width:130,fontSize:12}} value=${stageF} onChange=${e=>setStageF(e.target.value)}>
                <option value="all">All Stages</option>
                ${Object.entries(STAGES).map(([k,v])=>html`<option key=${k} value=${k}>${v.label}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Due Date</label>
              <select class="sel" style=${{width:120,fontSize:12}} value=${typeF} onChange=${e=>setTypeF(e.target.value)}>
                <option value="all">All Types</option>
                ${['task','story','bug','epic','spike'].map(tp=>html`<option key=${tp} value=${tp}>${tp.charAt(0).toUpperCase()+tp.slice(1)}</option>`)}
              </select>
              <select class="sel" style=${{width:130,fontSize:12}} value=${dueF} onChange=${e=>setDueF(e.target.value)}>
                <option value="all">Any Due Date</option>
                <option value="overdue">⚠ Overdue</option>
                <option value="today">📅 Due Today</option>
                <option value="week">📆 Due This Week</option>
                <option value="month">🗓 Due This Month</option>
              </select>
            </div>
            <div style=${{display:'flex',alignItems:'flex-end',paddingBottom:1}}>
              <span style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace',padding:'0 4px'}}>${filtered.length} task${filtered.length!==1?'s':''} shown</span>
            </div>
          </div>`:null}
      </div>

      ${mode==='kanban'?html`
        <div style=${{flex:1,overflowX:'auto',overflowY:'hidden',padding:'13px 18px'}}>
          <div style=${{display:'flex',gap:11,height:'100%',minWidth:'fit-content'}}>
            ${KCOLS.map(st=>{
              const col=filtered.filter(t=>t.stage===st);const si=STAGES[st];
              return html`<div key=${st} style=${{flex:'0 0 220px',background:'var(--sf2)',border:'1px solid var(--bd)',borderRadius:11,padding:10,display:'flex',flexDirection:'column',gap:7,borderTop:'3px solid '+si.color,maxHeight:'100%'}}>
                <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',paddingBottom:7,borderBottom:'1px solid var(--bd)'}}>
                  <div style=${{display:'flex',alignItems:'center',gap:6}}><div style=${{width:7,height:7,borderRadius:2,background:si.color}}></div><span style=${{fontSize:11,fontWeight:700,color:'var(--tx)'}}>${si.label}</span></div>
                  <span style=${{fontSize:9,color:'var(--tx3)',background:'var(--bd)',padding:'2px 6px',borderRadius:4,fontFamily:'monospace'}}>${col.length}</span>
                </div>
                <div style=${{overflowY:'auto',display:'flex',flexDirection:'column',gap:7,flex:1}}>
                  ${col.map(tk=>{
                    const au=safe(users).find(u=>u.id===tk.assignee);
                    const proj=safe(projects).find(p=>p.id===tk.project);
                    const isOverdue=tk.due&&new Date(tk.due)<new Date()&&tk.stage!=='completed';
                    const isDueToday=tk.due&&fmtD(tk.due)===fmtD(new Date().toISOString().split('T')[0]);
                    return html`<div key=${tk.id} class="tkc" onClick=${()=>setEditT(tk)}>
                      <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:4}}>
                        <div style=${{display:'flex',alignItems:'center',gap:4}}>
                          <span style=${{width:10,height:10,borderRadius:2,display:'inline-block',flexShrink:0,background:TYPE_COLORS[tk.task_type||'task']||'#1d4ed8'}}></span>
                          <span style=${{fontSize:9,fontWeight:700,fontFamily:'monospace',padding:'1px 6px',borderRadius:4,background:TYPE_BG[tk.task_type||'task']||'rgba(29,78,216,0.10)',color:TYPE_COLORS[tk.task_type||'task']||'#1d4ed8',border:'1px solid '+(TYPE_BORDER[tk.task_type||'task']||'rgba(29,78,216,0.2)')}}>${tk.id}</span>
                        </div>
                        <${PB} p=${tk.priority}/>
                      </div>
                      ${(tk.task_type&&tk.task_type!=='task')||tk.story_points>0?html`
                        <div style=${{display:'flex',gap:4,marginBottom:4,alignItems:'center'}}>
                          ${tk.task_type&&tk.task_type!=='task'?html`<span style=${{fontSize:8,fontWeight:800,padding:'1px 5px',borderRadius:3,textTransform:'uppercase',flexShrink:0,background:({'story':'rgba(21,128,61,0.12)','bug':'rgba(185,28,28,0.10)','epic':'rgba(109,40,217,0.12)','spike':'rgba(180,83,9,0.10)'})[tk.task_type]||'var(--ac3)',color:({'story':'var(--gn)','bug':'var(--rd)','epic':'var(--pu)','spike':'var(--am)'})[tk.task_type]||'var(--ac)'}}>${tk.task_type}</span>`:null}
                          ${tk.story_points>0?html`<span style=${{fontSize:8,fontWeight:700,padding:'1px 5px',borderRadius:3,background:'var(--sf2)',color:'var(--tx2)',border:'1px solid var(--bd)'}}>${tk.story_points}pt${tk.story_points>1?'s':''}</span>`:null}
                        </div>`:null}
                      <p style=${{fontSize:12,fontWeight:600,color:'var(--tx)',marginBottom:5,lineHeight:1.4}}>${tk.title}</p>
                      ${proj?html`<div style=${{fontSize:9,color:'var(--tx3)',marginBottom:5,display:'flex',alignItems:'center',gap:3}}>
                        <div style=${{width:5,height:5,borderRadius:1,background:proj.color,flexShrink:0}}></div>
                        <span style=${{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${proj.name}</span>
                      </div>`:null}
                      ${tk.pct>0?html`<div style=${{marginBottom:5}}><${Prog} pct=${tk.pct} color=${si.color}/></div>`:null}
                      <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginTop:2}}>
                        ${au?html`<${Av} u=${au} size=${20} title=${au.name}/>`:html`<div style=${{width:20,height:20,borderRadius:'50%',background:'var(--bd)'}}></div>`}
                        ${tk.due?html`<span style=${{fontSize:9,fontFamily:'monospace',color:isOverdue?'var(--rd)':isDueToday?'var(--am)':'var(--tx3)',fontWeight:isOverdue||isDueToday?700:400}}>${isOverdue?'⚠ ':isDueToday?'📅 ':''}${fmtD(tk.due)}</span>`:null}
                      </div>
                    </div>`;
                  })}
                  ${col.length===0?html`<div style=${{padding:'14px 0',textAlign:'center',color:'var(--tx3)',fontSize:12}}>Empty</div>`:null}
                </div>
              </div>`;
            })}
          </div>
        </div>`:null}

      <!-- Sprint + stats bar -->
      ${(()=>{
        const sprints=[...new Set(safe(tasks).filter(t=>t.sprint).map(t=>t.sprint))];
        const totalPts=filtered.reduce((a,t)=>a+(t.story_points||0),0);
        const donePts=filtered.filter(t=>t.stage==='completed').reduce((a,t)=>a+(t.story_points||0),0);
        if(!sprints.length&&!totalPts)return null;
        return html`
          <div style=${{padding:'4px 18px 8px',display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
            ${sprints.length>0?html`
              <span style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5}}>Sprint:</span>
              <button class=${'chip'+(sprintFilter===''?' on':'')} onClick=${()=>setSprintFilter('')} style=${{fontSize:10}}>All</button>
              ${sprints.map(sp=>html`<button key=${sp} class=${'chip'+(sprintFilter===sp?' on':'')} onClick=${()=>setSprintFilter(sp)} style=${{fontSize:10}}>${sp}</button>`)}
              <div style=${{width:1,height:16,background:'var(--bd)',margin:'0 4px'}}></div>
            `:null}
            ${totalPts>0?html`
              <span style=${{fontSize:10,color:'var(--tx3)'}}>
                <b style=${{color:'var(--ac)'}}>${donePts}</b>/<b style=${{color:'var(--tx2)'}}>${totalPts}</b> pts done
              </span>
              <div style=${{height:6,width:80,background:'var(--sf2)',borderRadius:100,overflow:'hidden',border:'1px solid var(--bd)'}}>
                <div style=${{height:'100%',width:(totalPts?Math.round(donePts*100/totalPts):0)+'%',background:'var(--gn)',borderRadius:100}}></div>
              </div>
            `:null}
          </div>`;
      })()}
      ${mode==='list'?html`
        <div style=${{flex:1,overflowY:'auto',padding:'13px 18px'}}>
          <div class="card" style=${{padding:0,overflow:'hidden'}}>
            <table style=${{width:'100%',borderCollapse:'collapse'}}>
              <thead>
                <tr style=${{borderBottom:'2px solid var(--bd)',background:'var(--sf2)'}}>
                  ${[
                    {k:'id', lbl:'ID', s:null}, {k:'type', lbl:'Type', s:null}, {k:'title', lbl:'Title', s:null}, {k:'project', lbl:'Project', s:null}, {k:'assignee',lbl:'Assignee', s:'assignee'}, {k:'priority',lbl:'Priority', s:'priority'}, {k:'stage', lbl:'Stage', s:'stage'}, {k:'due', lbl:'Due', s:'due'}, {k:'pct', lbl:'%', s:'pct'}, {k:'pts', lbl:'Pts', s:null}, ].map(h=>{
                    const isA=sortCol===h.s;const can=!!h.s;
                    return html`<th key=${h.k}
                      onClick=${can?()=>toggleSort(h.s):null}
                      style=${{padding:'10px 13px',textAlign:'left',fontSize:10,fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5,userSelect:'none',cursor:can?'pointer':'default',whiteSpace:'nowrap',color:isA?'var(--ac2)':'var(--tx3)',borderBottom:isA?'2px solid var(--ac)':'2px solid transparent',transition:'all .15s',background:isA?'rgba(99,102,241,.07)':'',position:'relative'}}>
                      <div style=${{display:'flex',alignItems:'center',gap:5}}>
                        <span>${h.lbl}</span>
                        ${can?html`<span style=${{display:'flex',flexDirection:'column',lineHeight:.8,fontSize:8,gap:1}}>
                          <span style=${{color:isA&&sortDir==='asc'?'var(--ac2)':'var(--tx3)',opacity:isA&&sortDir==='asc'?1:.4}}>▲</span>
                          <span style=${{color:isA&&sortDir==='desc'?'var(--ac2)':'var(--tx3)',opacity:isA&&sortDir==='desc'?1:.4}}>▼</span>
                        </span>`:null}
                      </div>
                    </th>`;
                  })}
                </tr>
              </thead>
              <tbody>
                ${sorted.map((tk,i)=>{
                  const pr=safe(projects).find(p=>p.id===tk.project);
                  const au=safe(users).find(u=>u.id===tk.assignee);
                  const si=STAGES[tk.stage]||{color:'#94a3b8'};
                  return html`
                    <tr key=${tk.id} style=${{borderBottom:i<sorted.length-1?'1px solid var(--bd)':'none'}}
                      onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'}
                      onMouseLeave=${e=>e.currentTarget.style.background=''}>
                      <td style=${{padding:'9px 13px'}}><span class="id-badge id-task">${tk.id}</span></td>
                      <td style=${{padding:'9px 13px'}}>${tk.task_type&&tk.task_type!=='task'?html`<span style=${{fontSize:9,fontWeight:800,padding:'2px 6px',borderRadius:3,textTransform:'uppercase',background:({'story':'rgba(21,128,61,0.12)','bug':'rgba(185,28,28,0.10)','epic':'rgba(109,40,217,0.12)','spike':'rgba(180,83,9,0.10)'})[tk.task_type]||'var(--ac3)',color:({'story':'var(--gn)','bug':'var(--rd)','epic':'var(--pu)','spike':'var(--am)'})[tk.task_type]||'var(--ac)'}}>${tk.task_type}</span>`:html`<span style=${{fontSize:9,color:'var(--tx3)'}}>task</span>`}</td>
                      <td style=${{padding:'9px 13px',cursor:'pointer'}} onClick=${()=>setEditT(tk)}><span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${tk.title}</span></td>
                      <td style=${{padding:'9px 13px'}}>${pr?html`<div style=${{display:'flex',alignItems:'center',gap:5}}><div style=${{width:6,height:6,borderRadius:2,background:pr.color}}></div><span style=${{fontSize:12,color:'var(--tx2)'}}>${pr.name}</span></div>`:null}</td>
                      <td style=${{padding:'9px 13px'}}>${au?html`<div style=${{display:'flex',alignItems:'center',gap:6}}><${Av} u=${au} size=${19}/><span style=${{fontSize:12,color:'var(--tx2)'}}>${au.name}</span></div>`:html`<span style=${{color:'var(--tx3)',fontSize:12}}>—</span>`}</td>
                      <td style=${{padding:'7px 11px'}}><${PB} p=${tk.priority}/></td>
                      <td style=${{padding:'5px 9px'}}>
                        <div style=${{position:'relative',display:'inline-flex',alignItems:'center'}}>
                          <select
                            value=${tk.stage}
                            onChange=${e=>{e.stopPropagation();quickStage(tk.id,e.target.value);}}
                            onClick=${e=>e.stopPropagation()}
                            style=${{background:si.color+'1a',border:'2px solid '+si.color,color:si.color,borderRadius:8,padding:'5px 26px 5px 9px',fontSize:11,fontFamily:'monospace',fontWeight:700,cursor:'pointer',outline:'none',appearance:'none',WebkitAppearance:'none',MozAppearance:'none',minWidth:90}}>
                            ${Object.entries(STAGES).map(([k,v])=>html`<option key=${k} value=${k} style=${{background:'#0d0f18',color:'#e2e8f0'}}>${v.label}</option>`)}
                          </select>
                          <span style=${{position:'absolute',right:7,top:'50%',transform:'translateY(-50%)',pointerEvents:'none',fontSize:9,color:si.color,fontWeight:900}}>▾</span>
                        </div>
                      </td>
                      <td style=${{padding:'9px 11px'}}>${(()=>{const isOD=tk.due&&new Date(tk.due)<new Date()&&tk.stage!=='completed';return html`<span style=${{fontSize:11,color:isOD?'var(--rd)':'var(--tx2)',fontFamily:'monospace',fontWeight:isOD?700:400}}>${isOD?'⚠ ':''}${fmtD(tk.due)}</span>`;})()}</td>
                      <td style=${{padding:'9px 11px',minWidth:100}}>
                        <div style=${{display:'flex',alignItems:'center',gap:7}}>
                          <div style=${{flex:1}}><${Prog} pct=${tk.pct} color=${si.color}/></div>
                          <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace',width:28,textAlign:'right',fontWeight:700}}>${tk.pct}%</span>
                        </div>
                      </td>
                      <td style=${{padding:'9px 11px',textAlign:'center'}}>
                        ${tk.story_points>0
                          ?html`<span style=${{fontSize:11,fontWeight:700,color:'var(--tx2)',fontFamily:'monospace',background:'var(--sf2)',padding:'2px 7px',borderRadius:5,border:'1px solid var(--bd)'}}>${tk.story_points}</span>`
                          :html`<span style=${{color:'var(--tx3)',fontSize:11}}>—</span>`}
                      </td>
                    </tr>`;
                })}
              </tbody>
            </table>
            ${sorted.length===0?html`<div style=${{padding:40,textAlign:'center',color:'var(--tx3)',fontSize:13}}><div style=${{fontSize:28,marginBottom:8}}>🔍</div>No tasks match your filters.</div>`:null}
          </div>
        </div>`:null}

      ${editT?html`<${TaskModal} task=${editT} onClose=${()=>setEditT(null)} onSave=${saveT} onDel=${delT} projects=${projects} users=${users} cu=${cu} onSetReminder=${onSetReminder} teams=${teams||[]}/>`:null}
      ${newT?html`<${TaskModal} task=${null} onClose=${()=>setNewT(false)} onSave=${saveT} projects=${projects} users=${users} cu=${cu} onSetReminder=${onSetReminder} teams=${teams||[]} activeTeam=${activeTeam||null}/>`:null}
    </div>`;
}

/* ─── Dashboard ───────────────────────────────────────────────────────────── */
function Dashboard({cu,tasks,projects,users,onNav,activeTeam,teams,setTeamCtx}){
  const [hideOnboarding,setHideOnboarding]=useState(()=>{try{return localStorage.getItem('vw_onboarding_done')==='1';}catch{return false;}});
  const dismissOnboarding=()=>{try{localStorage.setItem('vw_onboarding_done','1');}catch{}setHideOnboarding(true);};
  const t=safe(tasks);const p=safe(projects);const u=safe(users);
  const isAdminManager=cu&&(cu.role==='Admin'||cu.role==='Manager');
  const [teamDropOpen,setTeamDropOpen]=useState(false);
  const [teamSearch,setTeamSearch]=useState('');
  const teamDropRef=useRef(null);
  useEffect(()=>{
    if(!teamDropOpen)return;
    const h=e=>{if(teamDropRef.current&&!teamDropRef.current.contains(e.target))setTeamDropOpen(false);};
    document.addEventListener('mousedown',h);
    return()=>document.removeEventListener('mousedown',h);
  },[teamDropOpen]);
  const filteredTeams=useMemo(()=>safe(teams).filter(t2=>t2.name.toLowerCase().includes(teamSearch.toLowerCase())),[teams,teamSearch]);
  const myT=t.filter(x=>x.assignee===cu.id);
  const myActiveTasks=myT.filter(x=>x.stage!=='completed').sort((a,b)=>new Date(b.created||0)-new Date(a.created||0));
  const done=t.filter(x=>x.stage==='completed').length;
  const active=t.filter(x=>x.stage!=='completed').length;
  const blocked=t.filter(x=>x.stage==='blocked').length;
  const [tickets,setTickets]=useState([]);
  useEffect(()=>{
    const url=activeTeam?'/api/tickets?team_id='+activeTeam.id:'/api/tickets';
    api.get(url).then(d=>setTickets(Array.isArray(d)?d:[]));
  },[activeTeam]);
  const openTickets=tickets.filter(x=>x.status==='open').length;
  const inProgressTickets=tickets.filter(x=>x.status==='in-progress').length;
  const myTickets=tickets.filter(x=>x.assignee===cu.id&&x.status!=='closed'&&x.status!=='resolved').length;
  const activeProjectIds=new Set(p.map(proj=>proj.id));
  const activeTasks=t.filter(x=>activeProjectIds.has(x.project)&&x.stage!=='completed');
  const priChart=[
    {name:'Critical',value:activeTasks.filter(x=>x.priority==='critical').length,color:'var(--rd)',priKey:'critical'}, {name:'High',value:activeTasks.filter(x=>x.priority==='high').length,color:'var(--rd2)',priKey:'high'}, {name:'Medium',value:activeTasks.filter(x=>x.priority==='medium').length,color:'var(--pu)',priKey:'medium'}, {name:'Low',value:activeTasks.filter(x=>x.priority==='low').length,color:'var(--cy)',priKey:'low'}
  ];
  const stats=[
    {label:'Total Projects',val:p.length,color:'#1d4ed8',bg:'rgba(29,78,216,0.10)',icon:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',nav:'projects'}, {label:'Active Tasks',val:active,color:'#0e7490',bg:'rgba(14,116,144,0.10)',icon:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',nav:'tasks'}, {label:'Completed',val:done,color:'var(--gn)',bg:'rgba(21,128,61,0.12)',icon:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',nav:'tasks:stage:completed'}, {label:'Blocked',val:blocked,color:'var(--rd)',bg:'rgba(185,28,28,0.10)',icon:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>',nav:'tasks:stage:blocked'}, {label:'My Tasks',val:myT.filter(x=>x.stage!=='completed').length,color:'var(--am)',bg:'rgba(180,83,9,0.10)',icon:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',nav:'tasks:assignee:me'}, {label:'Team Members',val:u.length,color:'var(--pu)',bg:'rgba(109,40,217,0.10)',icon:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',nav:isAdminManager?'team':'tasks:assignee:me'}, {label:'Open Tickets',val:openTickets,color:'var(--cy)',bg:'rgba(14,116,144,0.10)',icon:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 9a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v1.5a1.5 1.5 0 0 0 0 3V15a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-1.5a1.5 1.5 0 0 0 0-3V9z"/><line x1="9" y1="7" x2="9" y2="17" strokeDasharray="2 2"/></svg>',nav:'tickets:status:open'}, {label:'In Progress',val:inProgressTickets,color:'var(--am)',bg:'rgba(180,83,9,0.10)',icon:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',nav:isAdminManager?'tickets':'tasks:assignee:me'}, {label:'My Tickets',val:myTickets,color:'var(--or)',bg:'rgba(194,65,12,0.10)',icon:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',nav:'tickets:assignee:me'}, ];
  return html`
    <div class="fi" style=${{height:'100%',overflowY:'auto',padding:'12px 20px',display:'flex',flexDirection:'column',gap:12}}>
      ${!hideOnboarding?html`<${OnboardingChecklist} cu=${cu} projects=${projects} users=${users} tasks=${tasks} setView=${onNav} onDismiss=${dismissOnboarding}/>`:null}
      <div style=${{padding:'10px 14px',background:'var(--sf)',borderRadius:12,border:'1px solid var(--bd2)',display:'flex',alignItems:'center',gap:10}}>
        <${Av} u=${cu} size=${32}/>
        <div style=${{flex:1,minWidth:0}}>
          <div style=${{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
            <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em'}}>Good day, ${(cu&&cu.name||'there').split(' ')[0]}! 👋</span>
            ${activeTeam?html`
              <span style=${{display:'inline-flex',alignItems:'center',gap:5,padding:'2px 8px',background:'rgba(29,78,216,0.08)',border:'1px solid rgba(29,78,216,0.2)',borderRadius:20,fontSize:10,fontWeight:600,color:'#1d4ed8',flexShrink:0}}>
                <div style=${{width:5,height:5,borderRadius:1,background:'var(--ac)'}}></div>
                ${activeTeam.name}
              </span>`:null}
          </div>
          <p style=${{color:'var(--tx3)',fontSize:11,marginTop:1}}>
            ${activeTeam?html`${p.length} projects · ${t.length} tasks · ${u.length} members · `:null}
            <b style=${{color:'var(--tx2)'}}>${myT.filter(x=>x.stage!=='completed').length}</b> active task${myT.filter(x=>x.stage!=='completed').length!==1?'s':''} assigned to you
          </p>
        </div>
        ${isAdminManager&&safe(teams).length>0?html`
          <div ref=${teamDropRef} style=${{position:'relative',flexShrink:0}}>
            <button onClick=${()=>setTeamDropOpen(v=>!v)}
              style=${{display:'flex',alignItems:'center',gap:7,padding:'7px 12px 7px 10px',borderRadius:10, border:'1px solid '+(teamDropOpen?'var(--ac)':'var(--bd)'), background:activeTeam?'var(--ac3)':'var(--sf2)', color:activeTeam?'var(--ac)':'var(--tx2)', cursor:'pointer',fontSize:12,fontWeight:600,transition:'all .15s',whiteSpace:'nowrap'}}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><circle cx="17" cy="8" r="3"/><circle cx="7" cy="8" r="3"/><path d="M3 21v-2a5 5 0 0 1 8.66-3.43"/><path d="M13 21v-2a5 5 0 0 1 10 0v2"/></svg>
              ${activeTeam?html`<div style=${{width:7,height:7,borderRadius:2,background:activeTeam.color||'var(--ac)',flexShrink:0}}></div>`:null}
              <span style=${{maxWidth:120,overflow:'hidden',textOverflow:'ellipsis'}}>${activeTeam?activeTeam.name:'All Teams'}</span>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                style=${{transform:teamDropOpen?'rotate(180deg)':'none',transition:'transform .15s',flexShrink:0}}><polyline points="6 9 12 15 18 9"/></svg>
            </button>
            ${teamDropOpen?html`
              <div style=${{position:'absolute',top:'calc(100% + 6px)',right:0,width:240,background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,boxShadow:'0 8px 32px rgba(0,0,0,.25)',zIndex:500,overflow:'hidden'}}>
                <div style=${{padding:'8px 10px',borderBottom:'1px solid var(--bd)'}}>
                  <div style=${{position:'relative'}}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
                      style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
                      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                    </svg>
                    <input class="inp" placeholder="Search teams…" value=${teamSearch} autoFocus
                      style=${{height:28,fontSize:11,paddingLeft:26}} onInput=${e=>setTeamSearch(e.target.value)}/>
                  </div>
                </div>
                <div style=${{maxHeight:200,overflowY:'auto',padding:'4px 6px'}}>
                  <button onClick=${()=>{setTeamCtx&&setTeamCtx('');setTeamDropOpen(false);setTeamSearch('');}}
                    style=${{width:'100%',padding:'7px 10px',borderRadius:7,border:'none', background:!activeTeam?'var(--ac3)':'transparent', color:!activeTeam?'var(--ac)':'var(--tx2)', fontSize:12,fontWeight:!activeTeam?700:400, cursor:'pointer',textAlign:'left',display:'flex',alignItems:'center',gap:8,transition:'all .1s'}}
                    onMouseEnter=${e=>{if(activeTeam)e.currentTarget.style.background='var(--sf2)';}}
                    onMouseLeave=${e=>{if(activeTeam)e.currentTarget.style.background='transparent';}}>
                    🌐 All Teams
                  </button>
                  ${filteredTeams.map(team=>html`
                    <button key=${team.id} onClick=${()=>{setTeamCtx&&setTeamCtx(team.id);setTeamDropOpen(false);setTeamSearch('');}}
                      style=${{width:'100%',padding:'7px 10px',borderRadius:7,border:'none', background:activeTeam&&activeTeam.id===team.id?'var(--ac3)':'transparent', color:activeTeam&&activeTeam.id===team.id?'var(--ac)':'var(--tx2)', fontSize:12,fontWeight:activeTeam&&activeTeam.id===team.id?700:400, cursor:'pointer',textAlign:'left',display:'flex',alignItems:'center',gap:8,transition:'all .1s'}}
                      onMouseEnter=${e=>{if(!(activeTeam&&activeTeam.id===team.id))e.currentTarget.style.background='var(--sf2)';}}
                      onMouseLeave=${e=>{if(!(activeTeam&&activeTeam.id===team.id))e.currentTarget.style.background='transparent';}}>
                      <div style=${{width:8,height:8,borderRadius:2,background:team.color||'var(--ac)',flexShrink:0}}></div>
                      <span style=${{flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${team.name}</span>
                      ${activeTeam&&activeTeam.id===team.id?html`<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>`:null}
                    </button>`)}
                  ${filteredTeams.length===0?html`<div style=${{padding:'10px',fontSize:11,color:'var(--tx3)',textAlign:'center'}}>No teams found</div>`:null}
                </div>
              </div>`:null}
          </div>`:null}
      </div>
            <div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(150px,1fr))',gap:8}}>
        ${stats.map((s,i)=>html`
          <div key=${i} onClick=${()=>onNav(s.nav)}
            style=${{background:'var(--sf)',borderRadius:14,padding:'12px 14px',position:'relative',overflow:'hidden',cursor:'pointer',transition:'all .16s',border:'1px solid var(--bd2)'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor=s.color||'var(--ac)';e.currentTarget.style.transform='translateY(-2px)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='';e.currentTarget.style.transform='';}}>
            <div style=${{position:'absolute',top:0,left:0,right:0,height:2,background:s.color||'var(--ac)',borderRadius:'16px 16px 0 0'}}></div>
            <div style=${{width:26,height:26,borderRadius:7,background:s.bg,display:'flex',alignItems:'center',justifyContent:'center',color:s.color||'var(--ac)',marginBottom:8}} dangerouslySetInnerHTML=${{__html:s.icon}}></div>
            <div style=${{fontSize:24,fontWeight:700,color:'var(--tx)',lineHeight:1,fontFamily:"'Space Grotesk',sans-serif",letterSpacing:-1}}>${s.val}</div>
            <div style=${{fontSize:11,color:'var(--tx2)',marginTop:5,fontWeight:500}}>${s.label}</div>
          </div>`)}
      </div>
      <div style=${{display:'grid',gridTemplateColumns:'240px 1fr 1fr',gap:14}}>
        <div class="card">
          <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:11}}>Priority Split</h3>
          ${priChart.map((item,i)=>html`
              <div key=${i} onClick=${()=>onNav('tasks:priority:'+item.priKey)}
                style=${{display:'flex',alignItems:'center',gap:8,marginBottom:6,cursor:'pointer'}}
                onMouseEnter=${e=>e.currentTarget.style.opacity='.75'}
                onMouseLeave=${e=>e.currentTarget.style.opacity='1'}>
                <div style=${{width:7,height:7,borderRadius:2,background:item.color||'var(--ac)',flexShrink:0}}></div>
                <span style=${{fontSize:11,color:'var(--tx2)',width:50,flexShrink:0}}>${item.name}</span>
                <div style=${{flex:1,height:7,background:'var(--bd)',borderRadius:3,overflow:'hidden'}}>
                  <div style=${{height:'100%',width:(item.value/Math.max(...priChart.map(x=>x.value),1)*100)+'%',background:item.color||'var(--ac)',borderRadius:3,transition:'width .3s'}}></div>
                </div>
                <span style=${{fontSize:11,fontFamily:'monospace',fontWeight:700,color:'var(--tx)',width:18,textAlign:'right',flexShrink:0}}>${item.value}</span>
              </div>`)}

          <p style=${{fontSize:10,color:'var(--tx3)',marginTop:6,textAlign:'center'}}>Click to filter by priority</p>
        </div>
        <div class="card" style=${{display:'flex',flexDirection:'column'}}>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
            <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',margin:0}}>Project Progress</h3>
            <button class="btn bg" style=${{fontSize:10,padding:'2px 9px',height:22}} onClick=${()=>onNav('projects')}>View All</button>
          </div>
          <div style=${{flex:1,overflowY:'auto',maxHeight:220}}>
          ${p.map(proj=>{
            const pt=t.filter(x=>x.project===proj.id);
            const pc=pt.length?Math.round(pt.reduce((a,x)=>a+(x.pct||0),0)/pt.length):(proj.progress||0);
            return html`<div key=${proj.id} style=${{marginBottom:11}}>
              <div style=${{display:'flex',justifyContent:'space-between',marginBottom:4}}>
                <div style=${{display:'flex',alignItems:'center',gap:6}}>
                  <div style=${{width:7,height:7,borderRadius:2,background:proj.color||'var(--ac)'}}></div>
                  <span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${proj.name}</span>
                </div>
                <span style=${{fontSize:11,color:'var(--tx2)',fontFamily:'monospace'}}>${pc}%</span>
              </div>
              <${Prog} pct=${pc} color=${proj.color||'var(--ac)'}/>
            </div>`;
          })}
          </div>
        </div>
        <div class="card">
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
            <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',margin:0}}>My Active Tasks</h3>
            ${myActiveTasks.filter(x=>x.due&&new Date(x.due)<new Date()).length>0?html`
              <span style=${{fontSize:10,color:'var(--rd)',fontWeight:700,background:'rgba(248,113,113,.1)',padding:'2px 8px',borderRadius:10}}>
                ⚠ ${myActiveTasks.filter(x=>x.due&&new Date(x.due)<new Date()).length} overdue
              </span>`:null}
          </div>
          ${myActiveTasks.slice(0,6).map((tk,i)=>html`
            <div key=${tk.id} onClick=${()=>onNav('tasks:assignee:me')}
              style=${{display:'flex',gap:9,padding:'7px 0',borderBottom:i<Math.min(myActiveTasks.length,6)-1?'1px solid var(--bd)':'none',alignItems:'center',cursor:'pointer',borderRadius:6,transition:'background .1s'}}
              onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'}
              onMouseLeave=${e=>e.currentTarget.style.background='transparent'}>
              <div style=${{width:6,height:6,borderRadius:2,background:(STAGES[tk.stage]&&STAGES[tk.stage].color)||'var(--ac)',flexShrink:0,marginLeft:3}}></div>
              <div style=${{flex:1,minWidth:0}}>
                <div style=${{fontSize:12,color:'var(--tx)',fontWeight:500,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${tk.title}</div>
                <div style=${{display:'flex',gap:5,marginTop:2,alignItems:'center'}}><${SP} s=${tk.stage}/><${PB} p=${tk.priority}/>
                  ${tk.due&&new Date(tk.due)<new Date()?html`<span style=${{fontSize:9,color:'var(--rd)',fontWeight:700}}>⚠ Overdue</span>`:null}
                </div>
              </div>
              <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace',flexShrink:0}}>${tk.pct}%</span>
            </div>`)}
          ${myActiveTasks.length===0?html`<div style=${{color:'var(--tx3)',fontSize:13,textAlign:'center',paddingTop:16}}>No active tasks assigned. 🎉</div>`:null}
          ${myActiveTasks.length>0?html`
            <button class="btn bg" style=${{width:'100%',marginTop:10,fontSize:11,padding:'6px 0'}}
              onClick=${()=>onNav('tasks:assignee:me')}>
              View all my tasks →
            </button>`:null}
        </div>
      </div>
    </div>`;
}

/* ─── TimelineView (Admin/Manager only) ───────────────────────────────────── */
function TimelineView({cu,tasks,projects,onNav}){
  const t=safe(tasks);const p=safe(projects);
  const now=new Date();now.setHours(0,0,0,0);
  const [filterHealth,setFilterHealth]=useState('all');
  const [search,setSearch]=useState('');
  const [sortBy,setSortBy]=useState('health');

  const HC={
    'on-track':{label:'On Track',color:'var(--gn)',bg:'rgba(74,222,128,.12)'}, 'warning':{label:'At Risk',color:'var(--am)',bg:'rgba(251,191,36,.12)'}, 'at-risk':{label:'Needs Attention',color:'var(--rd)',bg:'rgba(248,113,113,.12)'}, 'overdue':{label:'Overdue',color:'var(--rd)',bg:'rgba(248,113,113,.2)'}, 'no-dates':{label:'No Dates',color:'var(--tx3)',bg:'rgba(255,255,255,.04)'}, };
  const HO={'overdue':0,'at-risk':1,'warning':2,'on-track':3,'no-dates':4};

  const timelines=useMemo(()=>p.map(proj=>{
    const start=proj.start_date?new Date(proj.start_date):null;
    const end=proj.target_date?new Date(proj.target_date):null;
    if(start)start.setHours(0,0,0,0);
    if(end)end.setHours(0,0,0,0);
    const totalDays=(start&&end)?Math.max(1,Math.round((end-start)/86400000)):null;
    const daysSpent=start?Math.max(0,Math.round((now-start)/86400000)):null;
    const daysLeft=end?Math.round((end-now)/86400000):null;
    const timeProgress=(totalDays&&daysSpent!==null)?Math.min(100,Math.round((daysSpent/totalDays)*100)):null;
    const isOverdue=end&&now>end;
    const pt=t.filter(x=>x.project===proj.id);
    const taskProgress=pt.length?Math.round(pt.reduce((a,x)=>a+(x.pct||0),0)/pt.length):(proj.progress||0);
    const gap=timeProgress!==null?(timeProgress-taskProgress):null;
    const health=gap===null?'no-dates':isOverdue&&taskProgress<100?'overdue':gap>30?'at-risk':gap>15?'warning':'on-track';
    return {...proj,start,end,totalDays,daysSpent,daysLeft,timeProgress,taskProgress,isOverdue,health,gap, taskCount:pt.length,doneTasks:pt.filter(x=>x.stage==='completed').length};
  }),[p,t,now]);

  const filtered=useMemo(()=>{
    let rows=[...timelines];
    if(filterHealth!=='all')rows=rows.filter(r=>r.health===filterHealth);
    if(search.trim()){const q=search.toLowerCase();rows=rows.filter(r=>r.name.toLowerCase().includes(q));}
    rows.sort((a,b)=>{
      if(sortBy==='health')return(HO[a.health]??9)-(HO[b.health]??9);
      if(sortBy==='name')return a.name.localeCompare(b.name);
      if(sortBy==='progress')return b.taskProgress-a.taskProgress;
      if(sortBy==='days_left'){if(a.daysLeft===null)return 1;if(b.daysLeft===null)return-1;return a.daysLeft-b.daysLeft;}
      if(sortBy==='spent'){if(a.daysSpent===null)return 1;if(b.daysSpent===null)return-1;return b.daysSpent-a.daysSpent;}
      return 0;
    });
    return rows;
  },[timelines,filterHealth,search,sortBy]);

  const fmtD=d=>d?d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}):'—';
  const counts={total:timelines.length,...Object.fromEntries(Object.keys(HC).map(k=>[k,timelines.filter(r=>r.health===k).length]))};

  return html`
    <div style=${{flex:1,minHeight:0,overflow:'hidden',display:'flex',flexDirection:'column',background:'var(--bg)'}}>

            <div style=${{flexShrink:0,padding:'12px 20px 10px',borderBottom:'1px solid var(--bd)',background:'var(--bg)'}}>

                <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10}}>
          <div>
            <h2 style=${{fontSize:15,fontWeight:800,color:'var(--tx)',display:'flex',alignItems:'center',gap:7,margin:0}}>📅 Project Timeline Tracker</h2>
            <p style=${{fontSize:11,color:'var(--tx2)',marginTop:2,fontWeight:500}}>Days spent vs. remaining — based on today</p>
          </div>
          <span style=${{fontSize:11,color:'var(--tx3)',background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:7,padding:'4px 10px',fontFamily:'monospace',flexShrink:0}}>
            ${now.toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric',year:'numeric'})}
          </span>
        </div>

                <div style=${{display:'flex',gap:7,marginBottom:10,flexWrap:'wrap'}}>
          ${[['all','All','#1d4ed8','rgba(29,78,216,0.08)',counts.total], ['on-track','On Track','var(--gn)','rgba(74,222,128,.1)',counts['on-track']], ['warning','At Risk','var(--am)','rgba(251,191,36,.1)',counts['warning']], ['at-risk','Needs Attn','var(--rd)','rgba(248,113,113,.1)',counts['at-risk']], ['overdue','Overdue','var(--rd)','rgba(248,113,113,.15)',counts['overdue']], ['no-dates','No Dates','var(--tx3)','rgba(255,255,255,.04)',counts['no-dates']], ].map(([k,lbl,color,bg,cnt])=>html`
            <div key=${k} onClick=${()=>setFilterHealth(k)}
              style=${{background:filterHealth===k?bg:'var(--sf)',border:'2px solid '+(filterHealth===k?color:'var(--bd)'), borderRadius:9,padding:'7px 14px',cursor:'pointer',transition:'all .15s', display:'flex',alignItems:'center',gap:8}}
              onMouseEnter=${e=>{if(filterHealth!==k)e.currentTarget.style.borderColor=color+'66';}}
              onMouseLeave=${e=>{if(filterHealth!==k)e.currentTarget.style.borderColor='var(--bd)';}}>
              <span style=${{fontSize:17,fontWeight:800,color,fontFamily:'monospace',lineHeight:1}}>${cnt}</span>
              <span style=${{fontSize:9,color:filterHealth===k?color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:.5}}>${lbl}</span>
            </div>`)}
        </div>

                <div style=${{display:'flex',gap:8,alignItems:'center'}}>
                    <div style=${{position:'relative',flex:1,maxWidth:260}}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
              style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input class="inp" placeholder="Search projects..." value=${search}
              style=${{height:26,fontSize:11,paddingLeft:26}}
              onInput=${e=>setSearch(e.target.value)}/>
          </div>
                    <span style=${{fontSize:10,color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:.5}}>Sort:</span>
          <div style=${{display:'flex',background:'var(--sf2)',borderRadius:6,padding:2,gap:1}}>
            ${[['health','🚦 Health'],['name','🔤 Name'],['progress','✅ Tasks'],['days_left','⏳ Days Left'],['spent','📆 Days Spent']].map(([k,lbl])=>html`
              <button key=${k} class=${'tb'+(sortBy===k?' act':'')} style=${{fontSize:10,padding:'2px 8px'}} onClick=${()=>setSortBy(k)}>${lbl}</button>`)}
          </div>
                    ${(filterHealth!=='all'||search)?html`
            <button class="btn bg" style=${{fontSize:10,padding:'3px 9px'}}
              onClick=${()=>{setFilterHealth('all');setSearch('');}}>✕ Clear</button>`:null}
          <span style=${{marginLeft:'auto',fontSize:11,color:'var(--tx3)',whiteSpace:'nowrap'}}>${filtered.length}/${timelines.length} projects</span>
        </div>
      </div>

            <div style=${{flex:1,minHeight:0,overflowY:'auto',padding:'12px 20px',display:'flex',flexDirection:'column',gap:10}}>
        ${filtered.length===0?html`
          <div style=${{textAlign:'center',padding:'48px 0',color:'var(--tx3)'}}>
            <div style=${{fontSize:36,marginBottom:10}}>🔍</div>
            <div>No projects match "${search||filterHealth}".</div>
            <button class="btn bg" style=${{marginTop:12,fontSize:11}} onClick=${()=>{setSearch('');setFilterHealth('all');}}>Clear filters</button>
          </div>`:null}
        ${filtered.map(proj=>{
          const hc=HC[proj.health];
          return html`
            <div key=${proj.id} style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12, padding:'13px 17px',borderLeft:'4px solid '+proj.color,transition:'all .15s',cursor:'pointer'}}
              onClick=${()=>onNav&&onNav('projects',proj.id)}
              onMouseEnter=${e=>{e.currentTarget.style.boxShadow='0 4px 20px rgba(0,0,0,.3)';e.currentTarget.style.borderColor=proj.color;}}
              onMouseLeave=${e=>{e.currentTarget.style.boxShadow='';e.currentTarget.style.borderColor='var(--bd)';}}>
              <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:proj.totalDays!==null?10:4}}>
                <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)',flex:1,cursor:'pointer'}}>${proj.name}</span>
                <span style=${{fontSize:10,fontWeight:700,padding:'3px 10px',borderRadius:100,background:hc.bg,color:hc.color}}>${hc.label}</span>
                <span style=${{fontSize:10,color:'var(--tx3)'}}>📋 ${proj.doneTasks}/${proj.taskCount}</span>
              </div>
              ${proj.totalDays!==null?html`
                <div style=${{display:'flex',flexDirection:'column',gap:5,marginBottom:10}}>
                  <div style=${{display:'flex',alignItems:'center',gap:10}}>
                    <span style=${{fontSize:10,color:'var(--tx2)',fontWeight:600,width:90,flexShrink:0}}>⏱ Time elapsed</span>
                    <div style=${{flex:1,height:7,background:'var(--sf3)',borderRadius:100,overflow:'hidden',border:'1px solid var(--bd)'}}>
                      <div style=${{height:'100%',width:proj.timeProgress+'%',borderRadius:100, background:proj.isOverdue?'var(--rd)':proj.timeProgress>70?'var(--am)':'var(--cy)'}}></div>
                    </div>
                    <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',width:34,textAlign:'right',fontWeight:700}}>${proj.timeProgress}%</span>
                  </div>
                  <div style=${{display:'flex',alignItems:'center',gap:10}}>
                    <span style=${{fontSize:10,color:'var(--tx2)',fontWeight:600,width:90,flexShrink:0}}>✅ Tasks done</span>
                    <div style=${{flex:1,height:7,background:'var(--sf3)',borderRadius:100,overflow:'hidden',border:'1px solid var(--bd)'}}>
                      <div style=${{height:'100%',width:proj.taskProgress+'%',borderRadius:100,background:proj.color}}></div>
                    </div>
                    <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',width:34,textAlign:'right',fontWeight:700}}>${proj.taskProgress}%</span>
                  </div>
                </div>
                <div style=${{display:'flex',gap:7,flexWrap:'wrap'}}>
                  ${[
                    {lbl:'Start',val:fmtD(proj.start),c:'var(--tx2)'}, {lbl:'End',val:fmtD(proj.end),c:proj.isOverdue?'var(--rd)':'var(--tx2)'}, {lbl:'Total',val:proj.totalDays+' days',c:'var(--tx2)'}, {lbl:'Spent',val:proj.daysSpent+' days',c:'var(--tx2)'}, {lbl:proj.isOverdue?'Overdue by':'Remaining',val:Math.abs(proj.daysLeft)+' days',c:proj.isOverdue?'var(--rd)':'var(--gn)'}, proj.gap!==null?{lbl:'Gap',val:(proj.gap>0?'+':'')+proj.gap+'%',c:proj.gap>15?'var(--rd)':proj.gap>0?'var(--am)':'var(--gn)'}:null, ].filter(Boolean).map((ch,i)=>html`
                    <div key=${i} style=${{padding:'3px 8px',background:'var(--sf2)',borderRadius:6,border:'1px solid var(--bd)'}}>
                      <span style=${{fontSize:9,color:'var(--tx2)',fontWeight:600,textTransform:'uppercase',letterSpacing:.4}}>${ch.lbl} </span>
                      <span style=${{fontSize:10,fontWeight:700,color:ch.c,fontFamily:'monospace'}}>${ch.val}</span>
                    </div>`)}
                </div>`:html`
                <div style=${{fontSize:11,color:'var(--tx3)',fontStyle:'italic'}}>No dates set — edit project to enable timeline tracking.</div>`}
            </div>`;
        })}
      </div>
    </div>`;
}

/* ─── ProductivityView (Admin/Manager only) ───────────────────────────────── */
function ProductivityView({cu,tasks,projects,users}){
  const t=safe(tasks);const p=safe(projects);const u=safe(users);
  const now=new Date();now.setHours(0,0,0,0);
  const [tab,setTab]=useState('table'); // 'table' | 'chart' | 'detail'
  const [selectedDev,setSelectedDev]=useState(null);
  const [filterRole,setFilterRole]=useState('all');
  const [filterProject,setFilterProject]=useState('all');
  const [sortBy,setSortBy]=useState('score');
  const [search,setSearch]=useState('');
  const roles=[...new Set(u.map(x=>x.role).filter(Boolean))];

  const devStats=useMemo(()=>u.map(dev=>{
    let devTasks=t.filter(x=>x.assignee===dev.id);
    if(filterProject!=='all')devTasks=devTasks.filter(x=>x.project===filterProject);
    const completed=devTasks.filter(x=>x.stage==='completed');
    const inProg=devTasks.filter(x=>x.stage==='in-progress'||x.stage==='development');
    const blocked=devTasks.filter(x=>x.stage==='blocked');
    const overdue=devTasks.filter(x=>x.due&&new Date(x.due)<now&&x.stage!=='completed');
    const total=devTasks.length;
    const completionRate=total?Math.round((completed.length/total)*100):0;
    const avgPct=total?Math.round(devTasks.reduce((a,x)=>a+(x.pct||0),0)/total):0;
    const score=Math.min(100,Math.round(completionRate*0.5+avgPct*0.3+Math.max(0,20-overdue.length*5)));
    const scoreColor=score>=70?'var(--gn)':score>=40?'var(--am)':'var(--rd)';
    const last7=t.filter(x=>x.assignee===dev.id&&(now-new Date(x.created||0))<7*86400000).length;
    const projSet=new Set(devTasks.map(x=>x.project));
    return {...dev,total,completed:completed.length,inProg:inProg.length,blocked:blocked.length, overdue:overdue.length,completionRate,avgPct,score,scoreColor,last7,projCount:projSet.size};
  }),[u,t,filterProject,now]);

  const filtered=useMemo(()=>{
    let rows=[...devStats];
    if(filterRole!=='all')rows=rows.filter(r=>r.role===filterRole);
    if(search.trim())rows=rows.filter(r=>r.name.toLowerCase().includes(search.toLowerCase()));
    rows.sort((a,b)=>{
      if(sortBy==='score')return b.score-a.score;
      if(sortBy==='name')return a.name.localeCompare(b.name);
      if(sortBy==='completed')return b.completed-a.completed;
      if(sortBy==='overdue')return b.overdue-a.overdue;
      if(sortBy==='tasks')return b.total-a.total;
      return 0;
    });
    return rows;
  },[devStats,filterRole,search,sortBy]);

  const selDev=selectedDev?devStats.find(d=>d.id===selectedDev):null;
  const selTasks=selDev?t.filter(x=>x.assignee===selDev.id&&(filterProject==='all'||x.project===filterProject)):[];
  const chartData=filtered.map(d=>({name:d.name.split(' ')[0],Completed:d.completed,'In Progress':d.inProg,Blocked:d.blocked}));

  const openDetail=(devId)=>{setSelectedDev(devId);setTab('detail');};
  const closeDetail=()=>{setSelectedDev(null);setTab('table');};

  return html`
    <div style=${{flex:1,minHeight:0,overflow:'hidden',display:'flex',flexDirection:'column',background:'var(--bg)'}}>

            <div style=${{flexShrink:0,padding:'10px 18px',borderBottom:'1px solid var(--bd)',background:'var(--bg)',display:'flex',alignItems:'center',gap:10,flexWrap:'wrap'}}>

                <div style=${{marginRight:4}}>
          <span style=${{fontSize:14,fontWeight:800,color:'var(--tx)'}}>👩‍💻 Dev Productivity</span>
          <span style=${{fontSize:11,color:'var(--tx3)',marginLeft:8}}>${u.length} developers · ${t.length} tasks</span>
        </div>

                <div style=${{position:'relative',flex:'1',maxWidth:200}}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
            style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" placeholder="Search developer..." value=${search}
            style=${{height:26,fontSize:11,paddingLeft:26}}
            onInput=${e=>setSearch(e.target.value)}/>
        </div>

                <select class="inp" style=${{height:26,fontSize:11,padding:'0 8px',maxWidth:120}} value=${filterRole}
          onChange=${e=>{setFilterRole(e.target.value);setSelectedDev(null);}}>
          <option value="all">All Roles</option>
          ${roles.map(r=>html`<option key=${r} value=${r}>${r}</option>`)}
        </select>

                <select class="inp" style=${{height:26,fontSize:11,padding:'0 8px',maxWidth:140}} value=${filterProject}
          onChange=${e=>{setFilterProject(e.target.value);setSelectedDev(null);}}>
          <option value="all">All Projects</option>
          ${p.map(pr=>html`<option key=${pr.id} value=${pr.id}>${pr.name}</option>`)}
        </select>

                <select class="inp" style=${{height:26,fontSize:11,padding:'0 8px',maxWidth:130}} value=${sortBy}
          onChange=${e=>setSortBy(e.target.value)}>
          <option value="score">Sort: Score</option>
          <option value="name">Sort: Name</option>
          <option value="tasks">Sort: Tasks</option>
          <option value="completed">Sort: Done</option>
          <option value="overdue">Sort: Overdue</option>
        </select>

                <div style=${{display:'flex',background:'var(--sf2)',borderRadius:7,padding:2,gap:1,marginLeft:'auto'}}>
          ${[['table','📋 Table'],['chart','📊 Chart']].map(([k,lbl])=>html`
            <button key=${k} class=${'tb'+(tab===k&&!selDev?' act':'')} style=${{fontSize:10,padding:'3px 10px'}}
              onClick=${()=>{closeDetail();setTab(k);}}>${lbl}</button>`)}
        </div>

        <span style=${{fontSize:11,color:'var(--tx3)',whiteSpace:'nowrap'}}>${filtered.length}/${u.length}</span>
      </div>

            ${!selDev?html`
        <div style=${{flexShrink:0,display:'flex',gap:0,borderBottom:'1px solid var(--bd)',background:'var(--sf2)'}}>
          ${[
            {lbl:'Total Tasks',val:t.length,c:'var(--tx)'}, {lbl:'Completed',val:t.filter(x=>x.stage==='completed').length,c:'var(--gn)'}, {lbl:'In Progress',val:t.filter(x=>x.stage==='in-progress'||x.stage==='development').length,c:'var(--cy)'}, {lbl:'Blocked',val:t.filter(x=>x.stage==='blocked').length,c:'var(--rd)'}, {lbl:'Overdue',val:t.filter(x=>x.due&&new Date(x.due)<now&&x.stage!=='completed').length,c:'var(--am)'}, ].map((s,i)=>html`
            <div key=${i} style=${{flex:1,textAlign:'center',padding:'8px 4px',borderRight:i<4?'1px solid var(--bd)':'none'}}>
              <div style=${{fontSize:16,fontWeight:800,color:s.c,fontFamily:'monospace',lineHeight:1}}>${s.val}</div>
              <div style=${{fontSize:9,color:'var(--tx3)',fontWeight:600,marginTop:2,textTransform:'uppercase',letterSpacing:.4}}>${s.lbl}</div>
            </div>`)}
        </div>`:null}

            <div style=${{flex:1,minHeight:0,overflowY:'auto'}}>

                ${tab==='table'&&!selDev?html`
          <table style=${{width:'100%',borderCollapse:'collapse',fontSize:12}}>
            <thead style=${{position:'sticky',top:0,zIndex:10}}>
              <tr style=${{background:'var(--sf2)',borderBottom:'2px solid var(--bd)'}}>
                ${[['#','36px'],['Developer','180px'],['Role','90px'],['Score','60px'], ['Tasks','60px'],['Done','60px'],['Active','60px'],['Blocked','70px'], ['Overdue','70px'],['Avg %','100px'],['Last 7d','70px'],['Projects','70px'],['','48px']
                ].map(([h,w])=>html`
                  <th key=${h} style=${{padding:'8px 10px',textAlign:'left',fontSize:9,fontWeight:700, color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5,whiteSpace:'nowrap', minWidth:w,width:w}}>${h}</th>`)}
              </tr>
            </thead>
            <tbody>
              ${filtered.map((dev,i)=>html`
                <tr key=${dev.id} style=${{borderBottom:'1px solid var(--bd)',cursor:'pointer',transition:'background .1s'}}
                  onMouseEnter=${e=>e.currentTarget.style.background='rgba(255,255,255,.04)'}
                  onMouseLeave=${e=>e.currentTarget.style.background=''}
                  onClick=${()=>openDetail(dev.id)}>
                                    <td style=${{padding:'9px 10px',textAlign:'center',fontSize:12}}>
                    ${i===0?'🥇':i===1?'🥈':i===2?'🥉':html`<span style=${{color:'var(--tx3)',fontFamily:'monospace',fontSize:10}}>${i+1}</span>`}
                  </td>
                                    <td style=${{padding:'9px 10px'}}>
                    <div style=${{display:'flex',alignItems:'center',gap:8}}>
                      <${Av} u=${dev} size=${28}/>
                      <div>
                        <div style=${{fontWeight:600,color:'var(--tx)',fontSize:12,lineHeight:1.2,whiteSpace:'nowrap'}}>${dev.name}</div>
                        ${dev.id===cu.id?html`<div style=${{fontSize:9,color:'#1d4ed8',fontWeight:700}}>YOU</div>`:null}
                      </div>
                    </div>
                  </td>
                  <td style=${{padding:'9px 10px',color:'var(--tx2)',fontSize:11,whiteSpace:'nowrap'}}>${dev.role||'—'}</td>
                                    <td style=${{padding:'9px 10px'}}>
                    <div style=${{width:32,height:32,borderRadius:'50%',border:'2.5px solid '+dev.scoreColor, display:'flex',alignItems:'center',justifyContent:'center', background:'rgba(255,255,255,.02)',fontSize:10,fontWeight:800, color:dev.scoreColor,fontFamily:'monospace'}}>${dev.score}</div>
                  </td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',fontWeight:600,color:'var(--tx)',textAlign:'center'}}>${dev.total}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',fontWeight:700,color:'var(--gn)',textAlign:'center'}}>${dev.completed}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',color:'var(--cy)',textAlign:'center'}}>${dev.inProg}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',color:dev.blocked>0?'var(--rd)':'var(--tx3)',textAlign:'center'}}>${dev.blocked}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',fontWeight:dev.overdue>0?700:400, color:dev.overdue>0?'var(--rd)':'var(--tx3)',textAlign:'center'}}>${dev.overdue}</td>
                                    <td style=${{padding:'9px 10px'}}>
                    <div style=${{display:'flex',alignItems:'center',gap:5}}>
                      <div style=${{width:50,height:4,background:'var(--bd)',borderRadius:100,overflow:'hidden',flexShrink:0}}>
                        <div style=${{height:'100%',width:dev.avgPct+'%',borderRadius:100, background:dev.avgPct>70?'var(--gn)':dev.avgPct>40?'var(--am)':'var(--rd)'}}></div>
                      </div>
                      <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',flexShrink:0}}>${dev.avgPct}%</span>
                    </div>
                  </td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',color:dev.last7>0?'var(--ac)':'var(--tx3)', fontWeight:dev.last7>0?700:400,textAlign:'center'}}>${dev.last7}</td>
                  <td style=${{padding:'9px 10px',color:'var(--tx2)',fontFamily:'monospace',textAlign:'center'}}>${dev.projCount}</td>
                  <td style=${{padding:'9px 10px',textAlign:'center'}}>
                    <button class="btn bg" style=${{fontSize:10,padding:'3px 8px',whiteSpace:'nowrap'}}
                      onClick=${e=>{e.stopPropagation();openDetail(dev.id);}}>View →</button>
                  </td>
                </tr>`)}
              ${filtered.length===0?html`
                <tr><td colspan="13" style=${{textAlign:'center',padding:'48px',color:'var(--tx3)',fontSize:13}}>
                  <div style=${{fontSize:32,marginBottom:8}}>🔍</div>No developers match the filter.
                </td></tr>`:null}
            </tbody>
          </table>`:null}

                ${tab==='chart'&&!selDev?html`
          <div style=${{padding:'16px 20px',display:'flex',flexDirection:'column',gap:14}}>
            <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'16px 20px'}}>
              <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:14}}>Task Distribution per Developer</h3>
              ${(()=>{
                const maxVal=Math.max(1,...chartData.map(d=>(d.Completed||0)+(d['In Progress']||0)+(d.Blocked||0)));
                const pctW=(v)=>Math.round((v/maxVal)*100);
                return html`<div style=${{display:'flex',flexDirection:'column',gap:6,padding:'4px 0'}}>
                  <div style=${{display:'flex',gap:12,fontSize:10,color:'var(--tx3)',marginBottom:4,paddingLeft:64}}>
                    <span style=${{display:'flex',alignItems:'center',gap:4}}><span style=${{width:8,height:8,borderRadius:2,background:'var(--gn)',display:'inline-block'}}></span>Completed</span>
                    <span style=${{display:'flex',alignItems:'center',gap:4}}><span style=${{width:8,height:8,borderRadius:2,background:'var(--cy)',display:'inline-block'}}></span>In Progress</span>
                    <span style=${{display:'flex',alignItems:'center',gap:4}}><span style=${{width:8,height:8,borderRadius:2,background:'var(--rd)',display:'inline-block'}}></span>Blocked</span>
                  </div>
                  ${chartData.map((d,i)=>{
                    const total=(d.Completed||0)+(d['In Progress']||0)+(d.Blocked||0);
                    const cPct=pctW(d.Completed||0);
                    const pPct=pctW(d['In Progress']||0);
                    const bPct=pctW(d.Blocked||0);
                    return html`<div key=${i} style=${{display:'flex',alignItems:'center',gap:8}}>
                      <span style=${{width:56,fontSize:10,color:'var(--tx2)',textAlign:'right',flexShrink:0,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${d.name}</span>
                      <div style=${{flex:1,display:'flex',height:14,borderRadius:4,overflow:'hidden',background:'var(--bd)'}}>
                        ${cPct>0?html`<div style=${{width:cPct+'%',background:'var(--gn)',transition:'width .3s'}}></div>`:null}
                        ${pPct>0?html`<div style=${{width:pPct+'%',background:'var(--cy)',transition:'width .3s'}}></div>`:null}
                        ${bPct>0?html`<div style=${{width:bPct+'%',background:'var(--rd)',transition:'width .3s'}}></div>`:null}
                      </div>
                      <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace',width:20,flexShrink:0}}>${total}</span>
                    </div>`;
                  })}
                </div>`;
              })()}
              <p style=${{fontSize:10,color:'var(--tx3)',marginTop:8,textAlign:'center'}}>All ${filtered.length} developers shown — horizontal bars scale with task count</p>
            </div>
          </div>`:null}

                ${selDev?html`
          <div style=${{padding:'14px 18px',display:'flex',flexDirection:'column',gap:12}}>
                        <div>
              <button onClick=${()=>closeDetail()}
                style=${{display:'inline-flex',alignItems:'center',gap:6,padding:'6px 12px',borderRadius:8,border:'1px solid var(--bd)',background:'var(--sf)',color:'var(--tx2)',fontSize:12,fontWeight:600,cursor:'pointer',transition:'all .12s'}}
                onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';}}
                onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx2)';}}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="15 18 9 12 15 6"/></svg>
                All Developers
              </button>
            </div>
                        <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'16px 20px', display:'flex',alignItems:'center',gap:14,flexWrap:'wrap'}}>
              <${Av} u=${selDev} size=${52}/>
              <div style=${{flex:1,minWidth:100}}>
                <div style=${{fontSize:17,fontWeight:800,color:'var(--tx)'}}>${selDev.name}</div>
                <div style=${{fontSize:12,color:'var(--tx2)',marginTop:3}}>${selDev.role||'Team Member'}</div>
              </div>
              <div style=${{width:58,height:58,borderRadius:'50%',border:'3px solid '+selDev.scoreColor, display:'flex',alignItems:'center',justifyContent:'center',flexDirection:'column', background:'rgba(255,255,255,.03)',flexShrink:0}}>
                <span style=${{fontSize:20,fontWeight:900,color:selDev.scoreColor,fontFamily:'monospace',lineHeight:1}}>${selDev.score}</span>
                <span style=${{fontSize:8,color:'var(--tx3)',textTransform:'uppercase'}}>score</span>
              </div>
              ${[
                {l:'Total',v:selDev.total,c:'var(--tx)'}, {l:'Done',v:selDev.completed,c:'var(--gn)'}, {l:'Active',v:selDev.inProg,c:'var(--cy)'}, {l:'Blocked',v:selDev.blocked,c:selDev.blocked>0?'var(--rd)':'var(--tx3)'}, {l:'Overdue',v:selDev.overdue,c:selDev.overdue>0?'var(--rd)':'var(--tx3)'}, {l:'Avg%',v:selDev.avgPct+'%',c:selDev.avgPct>70?'var(--gn)':selDev.avgPct>40?'var(--am)':'var(--rd)'}, {l:'Last7d',v:selDev.last7,c:selDev.last7>0?'var(--ac)':'var(--tx3)'}, ].map(s=>html`
                <div key=${s.l} style=${{textAlign:'center',padding:'6px 10px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)',minWidth:48}}>
                  <div style=${{fontSize:16,fontWeight:800,color:s.c,fontFamily:'monospace',lineHeight:1}}>${s.v}</div>
                  <div style=${{fontSize:8,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.4,marginTop:2}}>${s.l}</div>
                </div>`)}
            </div>
                        <div style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5}}>
              Assigned Tasks <span style=${{color:'var(--ac)'}}>(${selTasks.length})</span>${filterProject!=='all'?' — filtered':''}
            </div>
            ${selTasks.length===0?html`
              <div style=${{textAlign:'center',padding:'32px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:10,border:'1px solid var(--bd)'}}>
                <div style=${{fontSize:28,marginBottom:8}}>📭</div>No tasks assigned${filterProject!=='all'?' in this project':''}.
              </div>`:null}
            <div style=${{display:'flex',flexDirection:'column',gap:6}}>
              ${selTasks.map(tk=>{
                const proj=p.find(pr=>pr.id===tk.project);
                const isOvd=tk.due&&new Date(tk.due)<now&&tk.stage!=='completed';
                return html`
                  <div key=${tk.id} style=${{display:'flex',gap:10,padding:'9px 14px',background:'var(--sf)',borderRadius:9,border:'1px solid var(--bd)',alignItems:'center'}}>
                    <div style=${{width:6,height:6,borderRadius:2,flexShrink:0,background:(STAGES[tk.stage]&&STAGES[tk.stage].color)||'var(--ac)'}}></div>
                    <div style=${{flex:1,minWidth:0}}>
                      <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${tk.title}</div>
                      <div style=${{display:'flex',gap:5,marginTop:3,flexWrap:'wrap',alignItems:'center'}}>
                        <${SP} s=${tk.stage}/><${PB} p=${tk.priority}/>
                        ${proj?html`<span style=${{fontSize:10,color:'var(--tx3)',display:'flex',alignItems:'center',gap:3}}>
                          <div style=${{width:5,height:5,borderRadius:1,background:proj.color,flexShrink:0}}></div>${proj.name}</span>`:null}
                        ${isOvd?html`<span style=${{fontSize:10,color:'var(--rd)',fontWeight:700}}>⚠ Overdue</span>`:null}
                      </div>
                    </div>
                    <div style=${{display:'flex',alignItems:'center',gap:5,flexShrink:0}}>
                      <div style=${{width:48,height:4,background:'var(--bd)',borderRadius:100,overflow:'hidden'}}>
                        <div style=${{height:'100%',width:(tk.pct||0)+'%',background:proj?proj.color:'var(--ac)',borderRadius:100}}></div>
                      </div>
                      <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',minWidth:26,textAlign:'right'}}>${tk.pct||0}%</span>
                    </div>
                  </div>`;
              })}
            </div>
          </div>`:null}

      </div>    </div>`;
}
function renderMd(text){
  return text.replace(/[*][*](.*?)[*][*]/g,'<b>$1</b>');
}

/* ─── Message Reactions Component ───────────────────────────────────────── */
function MsgReactions({msgId,msgType,cu,users}){
  const [reactions,setReactions]=useState({});
  const [showPicker,setShowPicker]=useState(false);
  const EMOJIS=['👍','❤️','😂','🎉','🚀','👀','✅','🔥'];
  const load=async()=>{const r=await api.get(`/api/reactions/${msgType}/${msgId}`);if(r&&!r.error)setReactions(r);};
  useEffect(()=>{load();},[msgId]);
  const toggle=async(emoji)=>{
    await api.post(`/api/reactions/${msgType}/${msgId}`,{emoji});
    setShowPicker(false);load();
  };
  const uMap={};safe(users||[]).forEach(u=>uMap[u.id]=u);
  const total=Object.values(reactions).reduce((s,arr)=>s+arr.length,0);
  return html`<div style=${{display:'flex',gap:4,flexWrap:'wrap',alignItems:'center',marginTop:2}}>
    ${Object.entries(reactions).map(([emoji,uids])=>html`
      <button key=${emoji} onClick=${()=>toggle(emoji)} title=${uids.map(id=>uMap[id]?.name||id).join(', ')}
        style=${{display:'flex',alignItems:'center',gap:3,padding:'1px 7px',borderRadius:99,border:'1px solid var(--bd)',background:uids.includes(cu?.id)?'rgba(37,99,235,0.12)':'var(--sf2)',cursor:'pointer',fontSize:12,fontWeight:600,color:'var(--tx)'}}>
        ${emoji} <span style=${{fontSize:11,color:'var(--tx3)'}}>${uids.length}</span>
      </button>`)}
    <div style=${{position:'relative'}}>
      <button onClick=${()=>setShowPicker(p=>!p)}
        style=${{padding:'1px 6px',borderRadius:99,border:'1px solid var(--bd)',background:'transparent',cursor:'pointer',fontSize:12,color:'var(--tx3)',opacity:.6}}>
        +
      </button>
      ${showPicker?html`
        <div style=${{position:'absolute',bottom:'100%',left:0,background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:10,padding:6,display:'flex',gap:4,zIndex:100,boxShadow:'0 4px 16px rgba(0,0,0,.15)'}}>
          ${EMOJIS.map(e=>html`
            <button key=${e} onClick=${()=>toggle(e)}
              style=${{background:'none',border:'none',cursor:'pointer',fontSize:16,padding:'2px 4px',borderRadius:6}}>
              ${e}
            </button>`)}
        </div>`:null}
    </div>
  </div>`;
}

function MessagesView({projects,users,cu,tasks}){
  const [showPinned,setShowPinned]=useState(false);
  const [hovMsg,setHovMsg]=useState(null);
  const [allProjects,setAllProjects]=useState(safe(projects));
  const [lastMsgTs,setLastMsgTs]=useState({});
  const [stableOrder,setStableOrder]=useState(null); // null = not yet fetched
  const orderSetRef=useRef(false);

  const allProjectsLoadedRef=useRef(false);
  useEffect(()=>{
    if(allProjectsLoadedRef.current)return;
    api.get('/api/projects/all').then(d=>{
      if(Array.isArray(d)&&d.length){
        allProjectsLoadedRef.current=true;
        setAllProjects(d);
        // If stableOrder not yet set, use creation order (alphabetical) as stable base
        if(!orderSetRef.current){
          orderSetRef.current=true;
          // Sort by name initially — overwritten when first message timestamps arrive
          const initial={};
          d.forEach(p=>{initial[p.id]=p.created||'';});
          setStableOrder(initial);
        }
      }
    });
  },[]);

  useEffect(()=>{
    const fetchTs=async()=>{
      const d=await api.get('/api/projects/last-messages');
      if(d&&typeof d==='object'){
        setLastMsgTs(d);
        if(!orderSetRef.current){
          orderSetRef.current=true;
          setStableOrder(d);
        }
        // Compute unread counts — messages newer than last seen ts
        const newUnread={};
        for(const [projId,latestTs] of Object.entries(d)){
          const lastSeen=lastSeenMsgRef.current[projId]||'';
          if(latestTs&&latestTs>lastSeen&&projId!==pidRef.current){
            // Fetch count of new messages
            newUnread[projId]=(newUnread[projId]||0)+1;
          }
        }
        // Only set unread=1 if the channel has a NEW message since last seen
        // Don't increment on every poll — just mark as unread (1) if newer
        setChannelUnread(prev=>{
          const merged={...prev};
          const newProjs=new Set();
          for(const [projId,latestTs] of Object.entries(d)){
            if(projId===pidRef.current)continue;
            const lastSeen=lastSeenMsgRef.current[projId]||'';
            const prevUnread=prev[projId]||0;
            if(latestTs&&latestTs>lastSeen){
              merged[projId]=prevUnread||1;
              newProjs.add(projId); // mark as having new messages
            }
          }
          if(newProjs.size>0)setNewMsgProjects(prev2=>new Set([...prev2,...newProjs]));
          return merged;
        });
      }
    };
    fetchTs();
    const id=setInterval(fetchTs,8000);
    return()=>clearInterval(id);
  },[]);

  const [pid,setPid]=useState('');
  const pidRef=useRef('');
  useEffect(()=>{pidRef.current=pid;},[pid]);
  const [msgs,setMsgs]=useState([]);const [txt,setTxt]=useState('');const ref=useRef(null);
  const [channelUnread,setChannelUnread]=useState({}); // {projectId: count}
  // Persist lastSeen to localStorage so refresh doesn't reset unread counts
  const _pfLastSeenInit=(()=>{try{return JSON.parse(localStorage.getItem('pfLastSeen')||'{}');}catch{return {};}})();
  const lastSeenMsgRef=useRef(_pfLastSeenInit);
  const saveLastSeen=(obj)=>{try{localStorage.setItem('pfLastSeen',JSON.stringify(obj));}catch{}};
  const [showInfo,setShowInfo]=useState(false);
  const [chanSearch,setChanSearch]=useState('');
  const [newestFirst,setNewestFirst]=useState(false);

  const loadMsgs=useCallback(async(id)=>{
    if(!id)return;
    const d=await api.get('/api/messages?project='+id);
    if(Array.isArray(d)){
      setMsgs(d);
      // Mark channel as read — store the latest message ts
      if(d.length>0){
        const latestTs=d.reduce((mx,m)=>m.ts>mx?m.ts:mx,'');
        lastSeenMsgRef.current[id]=latestTs;
        saveLastSeen(lastSeenMsgRef.current);
      }
      setChannelUnread(prev=>({...prev,[id]:0}));
    }
  },[]);

  useEffect(()=>{loadMsgs(pid);},[pid]);

  useEffect(()=>{
    if(!pid)return;
    const id=setInterval(()=>{
      api.get('/api/messages?project='+pid).then(d=>{
        if(Array.isArray(d)){
          setMsgs(prev=>{
            if(d.length>prev.length){
              playSound('notif');
              if(d.length>0){
                const latest=d.reduce((mx,m)=>m.ts>mx?m.ts:mx,'');
                setLastMsgTs(prev2=>({...prev2,[pid]:latest}));
                lastSeenMsgRef.current[pid]=latest;
                saveLastSeen(lastSeenMsgRef.current);
                setChannelUnread(prev3=>({...prev3,[pid]:0}));
              }
            }
            return d;
          });
        }
      });
    },2000);
    return()=>clearInterval(id);
  },[pid]);

  useEffect(()=>{
    if(ref.current&&!newestFirst) ref.current.scrollTop=ref.current.scrollHeight;
  },[msgs,newestFirst]);

  const sp=allProjects.find(p=>p.id===pid);
  const projTasks=safe(tasks).filter(t=>t.project===pid);
  const projMembers=safe(sp&&sp.members?JSON.parse(sp.members||'[]'):[]).map(id=>safe(users).find(u=>u.id===id)).filter(Boolean);
  const doneTasks=projTasks.filter(t=>t.stage==='completed').length;
  const blockedTasks=projTasks.filter(t=>t.stage==='blocked').length;
  const pc=projTasks.length?Math.round(projTasks.reduce((a,t)=>a+(t.pct||0),0)/projTasks.length):0;

  const send=async()=>{
    if(!txt.trim())return;const c=txt.trim();setTxt('');
    const m=await api.post('/api/messages',{project:pid,content:c});
    setMsgs(prev=>[...prev,m]);
    setLastMsgTs(prev=>({...prev,[pid]:m.ts||new Date().toISOString()}));
  };

  // Fixed order ref — set once, never changes (no re-sorting unless new msg arrives)
  const fixedOrderRef=useRef(null);
  const [newMsgProjects,setNewMsgProjects]=useState(new Set()); // projects with new msgs since load

  // Build fixed order ONCE — on first render that has both projects + timestamps
  // After that, never rebuild (prevents glitching)
  const orderBuiltRef=useRef(false);
  useEffect(()=>{
    if(orderBuiltRef.current)return; // never rebuild
    if(!allProjects.length)return;   // wait for projects
    orderBuiltRef.current=true;
    const ts=lastMsgTs||{};
    const sorted=[...allProjects].sort((a,b)=>{
      const at=ts[a.id]||a.created||'';
      const bt=ts[b.id]||b.created||'';
      return bt.localeCompare(at);
    });
    fixedOrderRef.current=sorted.map(p=>p.id);
  },[allProjects.length,Object.keys(lastMsgTs).length]); // only rebuild if item count changes

  const sortedProjects=useMemo(()=>{
    let rows=[...allProjects];
    if(chanSearch.trim()){
      const q=chanSearch.toLowerCase();
      rows=rows.filter(p=>p.name.toLowerCase().includes(q));
      return rows.sort((a,b)=>a.name.localeCompare(b.name));
    }
    const order=fixedOrderRef.current;
    if(order&&order.length){
      // Channels with new messages since load bubble to top
      const newSet=newMsgProjects;
      rows.sort((a,b)=>{
        const an=newSet.has(a.id)?0:1, bn=newSet.has(b.id)?0:1;
        if(an!==bn)return an-bn;
        const ai=order.indexOf(a.id),bi=order.indexOf(b.id);
        return (ai===-1?999:ai)-(bi===-1?999:bi);
      });
    }
    return rows;
  },[allProjects,chanSearch,newMsgProjects]);

  return html`<div class="fi" style=${{display:'flex',height:'100%',overflow:'hidden'}}>

        <div style=${{width:220,borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',flexShrink:0}}>
            <div style=${{padding:'10px 10px 8px',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
        <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:7}}>
          <span style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7}}>Channels</span>
          <span style=${{fontSize:10,color:'var(--tx3)'}}>${sortedProjects.length} of ${allProjects.length}</span>
        </div>
                <div style=${{position:'relative'}}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
            style=${{position:'absolute',left:7,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" placeholder="Search channels..." value=${chanSearch}
            style=${{height:26,fontSize:11,paddingLeft:24,width:'100%'}}
            onInput=${e=>setChanSearch(e.target.value)}/>
        </div>
      </div>
            <div style=${{flex:1,overflowY:'auto',padding:'4px 6px'}}>
        ${sortedProjects.length===0?html`
          <div style=${{textAlign:'center',padding:'24px 8px',color:'var(--tx3)',fontSize:11}}>No channels match "${chanSearch}"</div>`:null}
        ${sortedProjects.map(p=>{
          const pt=safe(tasks).filter(t=>t.project===p.id);
          const activeCnt=pt.filter(t=>t.stage!=='completed'&&t.stage!=='backlog').length;
          const lastMsg=lastMsgTs[p.id];
          const hasRecentMsg=lastMsg&&(Date.now()-new Date(lastMsg).getTime())<3600000; // msg in last 1h
          const fmtLastMsg=ts=>{
            if(!ts)return '';
            const d=new Date(ts);const now=new Date();
            const diff=now-d;
            if(diff<60000)return 'just now';
            if(diff<3600000)return Math.floor(diff/60000)+'m ago';
            if(diff<86400000)return d.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'});
            return d.toLocaleDateString('en-US',{month:'short',day:'numeric'});
          };
          return html`
            <button key=${p.id} class=${'nb'+(pid===p.id?' act':'')}
              style=${{marginBottom:2,fontSize:12,alignItems:'center',height:'auto',padding:'7px 10px',width:'100%',display:'flex'}}
              onClick=${()=>setPid(p.id)}>
              <div style=${{display:'flex',alignItems:'center',gap:7,width:'100%'}}>
                <div style=${{width:7,height:7,borderRadius:2,background:p.color,flexShrink:0}}></div>
                <span style=${{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,textAlign:'left'}}># ${p.name}</span>
                ${channelUnread[p.id]>0?html`<span style=${{fontSize:9,fontWeight:800,background:'var(--ac)',color:'#fff',borderRadius:8,padding:'1px 6px',flexShrink:0,minWidth:16,textAlign:'center'}}>${channelUnread[p.id]>9?'9+':channelUnread[p.id]}</span>`:null}
              </div>
            </button>`;
        })}
      </div>
    </div>

        <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden'}}>
            <div style=${{padding:'9px 14px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:9,flexShrink:0}}>
        ${sp?html`
          <div style=${{width:9,height:9,borderRadius:2,background:sp.color}}></div>
          <span style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}># ${sp.name}</span>
          <span style=${{fontSize:11,color:'var(--tx3)',marginLeft:4}}>${projTasks.length} tasks · ${pc}% done</span>
                    <button class=${'btn bg'+(newestFirst?' act':'')} style=${{fontSize:10,padding:'3px 9px',marginLeft:6}}
            onClick=${()=>setNewestFirst(v=>!v)}
            title=${newestFirst?'Showing newest first — click to show oldest first':'Showing oldest first — click to show newest first'}>
            ${newestFirst?'↓ Newest first':'↑ Oldest first'}
          </button>
          <button class=${'btn bg'+(showInfo?' act':'')} style=${{marginLeft:'auto',fontSize:11,padding:'4px 10px'}} onClick=${()=>setShowInfo(p=>!p)}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            Info
          </button>
        `:html`<span style=${{color:'var(--tx3)'}}>Select a channel</span>`}
      </div>

            ${showInfo&&sp?html`
        <div style=${{padding:'12px 16px',background:'var(--sf2)',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
          <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr 1fr 1fr',gap:10,marginBottom:12}}>
            ${[
              {label:'Total Tasks',val:projTasks.length,color:'var(--tx)'}, {label:'Completed',val:doneTasks,color:'var(--gn)'}, {label:'In Progress',val:projTasks.filter(t=>t.stage==='development'||t.stage==='testing'||t.stage==='uat').length,color:'var(--cy)'}, {label:'Blocked',val:blockedTasks,color:'var(--rd)'}, ].map(s=>html`
              <div key=${s.label} style=${{background:'var(--sf)',borderRadius:9,padding:'10px 12px',border:'1px solid var(--bd)'}}>
                <div style=${{fontSize:20,fontWeight:800,color:s.color,lineHeight:1}}>${s.val}</div>
                <div style=${{fontSize:10,color:'var(--tx3)',marginTop:3}}>${s.label}</div>
              </div>`)}
          </div>
          <div style=${{marginBottom:10}}>
            <div style=${{display:'flex',justifyContent:'space-between',marginBottom:4}}>
              <span class="tx3-11">Overall Progress</span>
              <span style=${{fontSize:11,color:'var(--tx2)',fontFamily:'monospace',fontWeight:700}}>${pc}%</span>
            </div>
            <div style=${{height:6,background:'var(--bd)',borderRadius:100,overflow:'hidden'}}>
              <div style=${{height:'100%',width:pc+'%',background:sp.color,borderRadius:100,transition:'width .5s'}}></div>
            </div>
          </div>
          <div style=${{display:'flex',gap:6,flexWrap:'wrap',marginBottom:10}}>
            ${Object.entries(STAGES).map(([k,v])=>{
              const cnt=projTasks.filter(t=>t.stage===k).length;
              if(!cnt)return null;
              return html`<span key=${k} style=${{fontSize:10,padding:'2px 8px',borderRadius:5,background:v.color+'22',color:v.color,fontWeight:600}}>${v.label}: ${cnt}</span>`;
            })}
          </div>
          <div style=${{display:'flex',alignItems:'center',gap:6}}>
            <span class="tx3-11">Members:</span>
            <div style=${{display:'flex',gap:-4}}>
              ${projMembers.slice(0,8).map((m,i)=>html`<div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-6:0,border:'2px solid var(--sf2)',borderRadius:'50%'}}><${Av} u=${m} size=${22}/></div>`)}
              ${projMembers.length>8?html`<span style=${{fontSize:10,color:'var(--tx3)',marginLeft:6}}>+${projMembers.length-8} more</span>`:null}
            </div>
          </div>
        </div>`:null}

            <div ref=${ref} style=${{flex:1,overflowY:'auto',padding:'13px 15px',display:'flex',flexDirection:'column',gap:0}}>
        ${(()=>{
          const fmtDate=iso=>{const d=new Date(iso);return String(d.getDate()).padStart(2,'0')+'/'+String(d.getMonth()+1).padStart(2,'0')+'/'+d.getFullYear();};
          const fmtTime=iso=>{const d=new Date(iso);return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');};
          const dateLabel=iso=>{
            const today=new Date();today.setHours(0,0,0,0);
            const yesterday=new Date(today);yesterday.setDate(today.getDate()-1);
            const d=new Date(iso);d.setHours(0,0,0,0);
            if(d.getTime()===today.getTime()) return 'Today · '+fmtDate(iso);
            if(d.getTime()===yesterday.getTime()) return 'Yesterday · '+fmtDate(iso);
            return fmtDate(iso);
          };
          const sorted=[...msgs].sort((a,b)=>newestFirst
            ? new Date(b.ts)-new Date(a.ts)
            : new Date(a.ts)-new Date(b.ts));
          const groups=[];let lastDate='';
          sorted.forEach(m=>{
            const d=new Date(m.ts);
            const key=d.getFullYear()+'-'+(d.getMonth()+1)+'-'+d.getDate();
            if(key!==lastDate){groups.push({type:'separator',label:dateLabel(m.ts),key:'sep-'+key});lastDate=key;}
            groups.push({type:'msg',msg:m});
          });
          return groups.map((item,idx)=>{
            if(item.type==='separator') return html`
              <div key=${item.key} style=${{display:'flex',alignItems:'center',gap:10,margin:'14px 0 10px'}}>
                <div style=${{flex:1,height:1,background:'var(--bd)'}}></div>
                <span style=${{fontSize:10,fontWeight:700,color:'var(--tx2)',background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:100,padding:'3px 12px',letterSpacing:.4,whiteSpace:'nowrap'}}>📅 ${item.label}</span>
                <div style=${{flex:1,height:1,background:'var(--bd)'}}></div>
              </div>`;
            const m=item.msg;
            const isSystem=m.is_system===1||m.sender==='system';
            const timeStr=fmtTime(m.ts);
            if(isSystem) return html`
              <div key=${m.id} style=${{display:'flex',justifyContent:'center',padding:'3px 0',marginBottom:6}}>
                <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:3,maxWidth:'90%'}}>
                  <div style=${{fontSize:12,color:'var(--tx)',fontWeight:500,background:'var(--sf2)',border:'1px solid var(--bd)',borderRadius:20,padding:'5px 16px',textAlign:'center',lineHeight:1.5}}
                    dangerouslySetInnerHTML=${{__html:renderMd(m.content)}}></div>
                  <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace',letterSpacing:.2}}>${timeStr}</span>
                </div>
              </div>`;
            const s=safe(users).find(u=>u.id===m.sender);
            const isMe=m.sender===cu.id;
            return html`
              <div key=${m.id} style=${{display:'flex',gap:8,alignItems:'flex-end',flexDirection:isMe?'row-reverse':'row',marginBottom:6}}>
                ${!isMe?html`<${Av} u=${s} size=${25}/>`:null}
                <div style=${{display:'flex',flexDirection:'column',gap:3,alignItems:isMe?'flex-end':'flex-start',maxWidth:'65%'}}>
                  ${!isMe?html`<span style=${{fontSize:11,color:'var(--tx3)',fontWeight:600,marginLeft:2}}>${(s&&s.name)||'?'}</span>`:null}
                  <div style=${{position:'relative'}} onMouseEnter=${()=>setHovMsg(m.id)} onMouseLeave=${()=>setHovMsg(null)}>
                    <div style=${{padding:'9px 13px',borderRadius:12,fontSize:13,lineHeight:1.5, background:isMe?'var(--ac)':'var(--sf2)',color:isMe?'var(--ac-tx)':'var(--tx)', border:isMe?'none':'1px solid var(--bd)', borderBottomRightRadius:isMe?3:12,borderBottomLeftRadius:isMe?12:3}}>${m.content}</div>
                    ${hovMsg===m.id&&cu&&['Admin','Manager','TeamLead'].includes(cu.role)?html`
                      <button onClick=${async()=>{await api.post('/api/messages/'+m.id+'/pin',{});}} title="Pin message"
                        style=${{position:'absolute',top:-8,right:isMe?'auto':-8,left:isMe?-8:'auto',background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:6,padding:'2px 5px',fontSize:11,cursor:'pointer',color:'var(--tx3)',zIndex:10}}>📌</button>`:null}
                  </div>
                  <${MsgReactions} msgId=${m.id} msgType="channel" cu=${cu} users=${users}/>
                  <span class="mono-10">${timeStr}</span>
                </div>
              </div>`;
          });
        })()}
        ${msgs.length===0?html`<div style=${{textAlign:'center',paddingTop:48,color:'var(--tx3)',fontSize:13}}>
          <div style=${{fontSize:28,marginBottom:8}}>💬</div>
          <p>No messages yet. Task activity will appear here automatically.</p>
        </div>`:null}
      </div>

            <div style=${{padding:'10px 14px',borderTop:'1px solid var(--bd)',display:'flex',gap:8,flexShrink:0,alignItems:'flex-end'}}>
        <${MentionInput} value=${txt} onChange=${setTxt} users=${users} cu=${cu}
          placeholder=${'Message in #'+((sp&&sp.name)||'… (@mention)')}
          onKeyDown=${e=>e.key==='Enter'&&!e.shiftKey&&send()}
          style=${{height:36,fontSize:13}}/>
        <button class="btn bp" style=${{padding:'8px 14px',fontSize:12,flexShrink:0}} onClick=${send}>➤</button>
      </div>
    </div>
  </div>`;
}

/* ─── DirectMessages ──────────────────────────────────────────────────────── */
const playSound=(type='notif')=>{
  try{
    const ctx=new(window.AudioContext||window.webkitAudioContext)();
    if(type==='reminder'){
      [[660,0],[880,0.15],[1100,0.3]].forEach(([freq,delay])=>{
        const o=ctx.createOscillator();const g=ctx.createGain();
        o.connect(g);g.connect(ctx.destination);o.type='sine';
        o.frequency.setValueAtTime(freq,ctx.currentTime+delay);
        g.gain.setValueAtTime(0.08,ctx.currentTime+delay);
        g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+delay+0.4);
        o.start(ctx.currentTime+delay);o.stop(ctx.currentTime+delay+0.5);
      });
    } else {
      [[523,0],[659,0.15]].forEach(([freq,delay])=>{
        const o=ctx.createOscillator();const g=ctx.createGain();
        o.connect(g);g.connect(ctx.destination);o.type='sine';
        o.frequency.setValueAtTime(freq,ctx.currentTime+delay);
        g.gain.setValueAtTime(0.06,ctx.currentTime+delay);
        g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+delay+0.35);
        o.start(ctx.currentTime+delay);o.stop(ctx.currentTime+delay+0.5);
      });
    }
  }catch(e){}
};
function DirectMessages({cu,users,dmUnread,onDmRead,dmEnabled=true,initialUserId=null,onClearInitial,onlineUsers=new Set()}){
  const isAdminOrManager=cu&&(cu.role==='Admin'||cu.role==='Manager');
  if(!dmEnabled&&!isAdminOrManager) return html`
    <div style=${{flex:1,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'100%',gap:12,color:'var(--tx3)'}}>
      <div style=${{fontSize:40}}>💬</div>
      <div style=${{fontSize:14,fontWeight:700,color:'var(--tx2)'}}>Direct Messages Disabled</div>
      <div style=${{fontSize:13,color:'var(--tx3)',textAlign:'center',maxWidth:280,lineHeight:1.6}}>Your workspace admin has disabled direct messages. Contact your admin to enable them.</div>
    </div>`;
  const others=safe(users).filter(u=>u.id!==cu.id);
  const [toId,setToId]=useState(others[0]&&others[0].id||'');const [msgs,setMsgs]=useState([]);const [txt,setTxt]=useState('');const [search,setSearch]=useState('');const ref=useRef(null);
  useEffect(()=>{
    if(initialUserId){
      const u=safe(users).find(u=>u.id===initialUserId);
      if(u){setToId(initialUserId);if(onClearInitial)onClearInitial();}
    }
  },[initialUserId]);
  const prevMsgCount=useRef(0);
  const loadMsgs=useCallback(async(id)=>{if(!id)return;const d=await api.get('/api/dm/'+id);if(Array.isArray(d)){setMsgs(d);onDmRead(id);};},[onDmRead]);
  useEffect(()=>{
    if(!toId)return;
    loadMsgs(toId);
    const id=setInterval(async()=>{
      const d=await api.get('/api/dm/'+toId);
      if(Array.isArray(d)){
        setMsgs(prev=>{
          if(d.length>prev.length){playSound('notif');}
          return d;
        });
        onDmRead(toId);
      }
    },3000);
    return()=>clearInterval(id);
  },[toId]);
  useEffect(()=>{if(ref.current)ref.current.scrollTop=ref.current.scrollHeight;},[msgs]);
  const send=async()=>{if(!txt.trim()||!toId)return;const c=txt.trim();setTxt('');const m=await api.post('/api/dm',{recipient:toId,content:c});setMsgs(prev=>[...prev,m]);};
  const filtered=others.filter(u=>u.name.toLowerCase().includes(search.toLowerCase()));
  const toUser=safe(users).find(u=>u.id===toId);
  const unreadFor=id=>(dmUnread.find(x=>x.sender===id)||{cnt:0}).cnt;
  return html`<div class="fi" style=${{display:'flex',height:'100%',overflow:'hidden'}}>
    <div style=${{width:220,borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',flexShrink:0}}>
      <div style=${{padding:'11px 12px',borderBottom:'1px solid var(--bd)'}}><div style=${{fontSize:11,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7,marginBottom:8}}>Direct Messages</div><input class="inp" style=${{fontSize:12,padding:'6px 10px'}} placeholder="Search..." value=${search} onInput=${e=>setSearch(e.target.value)}/></div>
      <div style=${{flex:1,overflowY:'auto',padding:6}}>
        ${filtered.map(u=>{const unr=unreadFor(u.id);const isA=toId===u.id;return html`
          <button key=${u.id} onClick=${()=>setToId(u.id)} style=${{display:'flex',alignItems:'center',gap:9,width:'100%',padding:'8px 10px',border:'none',borderRadius:9,cursor:'pointer',marginBottom:2,background:isA?'rgba(99,102,241,.14)':'transparent',transition:'all .14s'}}>
            <div style=${{position:'relative',flexShrink:0}}>
              <${Av} u=${u} size=${32}/>
              <div style=${{position:'absolute',bottom:0,right:0,width:10,height:10,borderRadius:'50%',background:onlineUsers.has(u.id)?'#22c55e':'#475569',border:'2px solid var(--bg)',boxShadow:onlineUsers.has(u.id)?'0 0 0 1px #22c55e,0 0 6px rgba(34,197,94,.5)':'none',transition:'background .3s,box-shadow .3s'}}></div>
            </div>
            <div style=${{flex:1,minWidth:0,textAlign:'left'}}>
              <div style=${{fontSize:13,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${u.name}</div>
            </div>
            ${unr>0?html`<span style=${{background:'var(--ac)',color:'#fff',borderRadius:10,fontSize:10,padding:'2px 6px',fontFamily:'monospace',fontWeight:700}}>${unr}</span>`:null}
          </button>`;})}
      </div>
    </div>
    <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden'}}>
      <div style=${{padding:'11px 16px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:11,flexShrink:0}}>
        ${toUser?html`
          <div style=${{position:'relative'}}>
            <${Av} u=${toUser} size=${36}/>
            <div style=${{position:'absolute',bottom:0,right:0,width:11,height:11,borderRadius:'50%',background:onlineUsers.has(toUser.id)?'#22c55e':'#475569',border:'2px solid var(--bg)',boxShadow:onlineUsers.has(toUser.id)?'0 0 0 1px #22c55e,0 0 7px rgba(34,197,94,.6)':'none',transition:'background .3s,box-shadow .3s'}}></div>
          </div>
          <div>
            <div style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}>${toUser.name}</div>
            <div style=${{fontSize:11,color:onlineUsers.has(toUser.id)?'#22c55e':'var(--tx3)',fontWeight:500}}>${onlineUsers.has(toUser.id)?'Active now':'Offline'}</div>
          </div>`:html`<span style=${{color:'var(--tx3)'}}>Select someone to chat</span>`}
      </div>
      <div ref=${ref} style=${{flex:1,overflowY:'auto',padding:'16px',display:'flex',flexDirection:'column',gap:12}}>
        ${msgs.length===0?html`<div style=${{textAlign:'center',paddingTop:60,color:'var(--tx3)',fontSize:13}}><div style=${{fontSize:36,marginBottom:10}}>👋</div><div style=${{fontWeight:600,marginBottom:4,color:'var(--tx2)'}}>${toUser?'Start a conversation with '+toUser.name:'Select someone'}</div></div>`:null}
        ${msgs.map((m,i)=>{const isMe=m.sender===cu.id;const showT=i===msgs.length-1||msgs[i+1].sender!==m.sender;return html`
          <div key=${m.id} style=${{display:'flex',gap:8,alignItems:'flex-end',flexDirection:isMe?'row-reverse':'row'}}>
            <div style=${{width:28,flexShrink:0}}>${!isMe&&(i===0||msgs[i-1].sender!==m.sender)?html`<${Av} u=${toUser} size=${28}/>`:null}</div>
            <div style=${{display:'flex',flexDirection:'column',gap:2,alignItems:isMe?'flex-end':'flex-start',maxWidth:'68%'}}>
              <div style=${{padding:'9px 13px',borderRadius:14,fontSize:13,lineHeight:1.55,wordBreak:'break-word',background:isMe?'var(--ac)':'var(--sf2)',color:isMe?'var(--ac-tx)':'var(--tx)',border:isMe?'none':'1px solid var(--bd)',borderBottomRightRadius:isMe?3:14,borderBottomLeftRadius:isMe?14:3}}>${m.content}</div>
              <${MsgReactions} msgId=${m.id} msgType="dm" cu=${cu} users=${[cu,toUser].filter(Boolean)}/>
              ${showT?html`<span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace',margin:'0 2px'}}>${ago(m.ts)}</span>`:null}
            </div>
          </div>`;})}
      </div>
      <div style=${{padding:'11px 16px',borderTop:'1px solid var(--bd)',display:'flex',gap:8,flexShrink:0}}>
        <textarea class="inp" style=${{flex:1,minHeight:40,maxHeight:100,resize:'none',padding:'9px 13px',lineHeight:1.5}} placeholder=${'Message '+((toUser&&toUser.name)||'...')} value=${txt} onInput=${e=>setTxt(e.target.value)} onKeyDown=${e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}}}></textarea>
        <button class="btn bp" style=${{padding:'9px 15px',flexShrink:0}} onClick=${send} disabled=${!txt.trim()||!toId}>➤</button>
      </div>
    </div>
  </div>`;
}

/* ─── NotifsView ──────────────────────────────────────────────────────────── */
function NotifsView({notifs,reload,onNavigate}){
  const NT={
    task_assigned:{icon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',c:'var(--ac)',nav:'tasks',label:'View Tasks'}, status_change:{icon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/></svg>',c:'var(--cy)',nav:'tasks',label:'View Tasks'}, comment:{icon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',c:'var(--pu)',nav:'tasks',label:'View Tasks'}, deadline:{icon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',c:'var(--am)',nav:'tasks',label:'View Tasks'}, dm:{icon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><circle cx="9" cy="10" r="1" fill="currentColor"/><circle cx="12" cy="10" r="1" fill="currentColor"/><circle cx="15" cy="10" r="1" fill="currentColor"/></svg>',c:'#06b6d4',nav:'dm',label:'Open Messages'}, project_added:{icon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><line x1="12" y1="10" x2="12" y2="16"/><line x1="9" y1="13" x2="15" y2="13"/></svg>',c:'#10b981',nav:'projects',label:'View Projects'}, reminder:{icon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',c:'#f59e0b',nav:'tasks',label:'View Tasks'}, call:{icon:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.61 3.28a2 2 0 0 1 1.99-2.18h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 8.96a16 16 0 0 0 6.29 6.29l1.24-.82a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>',c:'#22c55e',nav:'dashboard',label:'Join Instant Meet'}, };
  const unread=safe(notifs).filter(n=>!n.read).length;
  const handleClick=async(n)=>{
    if(!n.read) await api.put('/api/notifications/'+n.id+'/read',{});
    const T=NT[n.type]||NT.comment;
    if(T.nav&&onNavigate){onNavigate(T.nav);}
    reload();
  };
  const clearAll=async()=>{
    await api.put('/api/notifications/read-all',{});
    reload();
  };
  return html`<div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px',boxSizing:'border-box'}}>
    <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
      <span style=${{fontSize:13,color:'var(--tx2)'}}>${unread>0?html`<b style=${{color:'var(--ac)'}}>${unread}</b> unread`:'All caught up!'}</span>
      <div style=${{display:'flex',gap:8}}>
        ${unread>0?html`<button class="btn bg" style=${{fontSize:12}} onClick=${clearAll}>✓ Mark all read</button>`:null}
        ${notifs.length>0?html`<button class="btn brd" style=${{fontSize:12,color:'var(--rd)'}}
          onClick=${()=>{if(window.confirm('Clear all notifications?'))api.del('/api/notifications/all').then(reload);}}>🗑 Clear all</button>`:null}
      </div>
    </div>
    ${notifs.length===0?html`<div style=${{textAlign:'center',padding:'48px 0',color:'var(--tx3)',fontSize:13}}>
      <div style=${{fontSize:32,marginBottom:10}}>🔔</div><p>No notifications yet.</p></div>`:null}
    <div style=${{display:'flex',flexDirection:'column',gap:8,maxWidth:780}}>
      ${safe(notifs).map(n=>{const T=NT[n.type]||NT.comment;return html`
        <div key=${n.id} onClick=${()=>handleClick(n)}
          style=${{display:'flex',gap:12,padding:'12px 15px',background:n.read?'var(--sf)':'rgba(99,102,241,.07)',border:'1px solid '+(n.read?'var(--bd)':'rgba(99,102,241,.22)'),borderRadius:12,cursor:'pointer',alignItems:'center',transition:'all .15s'}}>
          <div style=${{width:36,height:36,borderRadius:10,background:T.c+'22',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}} dangerouslySetInnerHTML=${{__html:T.icon}}></div>
          <div style=${{flex:1}}>
            <p style=${{fontSize:13,color:'var(--tx)',fontWeight:n.read?400:600,marginBottom:3}}>${n.content}</p>
            <div style=${{display:'flex',gap:10,alignItems:'center'}}>
              <span class="mono-10">${ago(n.ts)}</span>
              ${T.nav?html`<span style=${{fontSize:10,color:T.c,fontWeight:600}}>→ ${T.label}</span>`:null}
            </div>
          </div>
          ${!n.read?html`<div style=${{width:8,height:8,borderRadius:'50%',background:'var(--ac)',flexShrink:0}}></div>`:null}
        </div>
        `;})}

    </div>
  </div>`;
}

/* ─── TeamView ────────────────────────────────────────────────────────────── */
/* ─── MemberRow (used inside TeamView members table) ───────────────────── */
function MemberRow({u,cu,i,total,reload,ROLE_COLORS}){
  const [showPw,setShowPw]=useState(false);
  const [editPw,setEditPw]=useState(false);
  const [newPw,setNewPw]=useState('');
  const [saving,setSaving]=useState(false);
  const resetPw=async()=>{
    if(!newPw.trim())return;
    setSaving(true);
    await api.put('/api/users/'+u.id,{password:newPw.trim()});
    setSaving(false);setEditPw(false);setNewPw('');
    reload&&reload();
  };
  return html`
    <tr style=${{borderBottom:i<total-1?'1px solid var(--bd)':'none'}}>
      <td style=${{padding:'11px 15px'}}>
        <div style=${{display:'flex',alignItems:'center',gap:10}}>
          <${Av} u=${u} size=${32}/>
          <div>
            <div style=${{fontSize:13,fontWeight:600,color:'var(--tx)',display:'flex',alignItems:'center',gap:6}}>
              ${u.name}
              ${u.id===cu.id?html`<span style=${{fontSize:9,color:'var(--ac)',background:'rgba(99,102,241,.14)',padding:'2px 6px',borderRadius:4,fontFamily:'monospace'}}>YOU</span>`:null}
            </div>
            <div style=${{fontSize:10,color:ROLE_COLORS[u.role]||'var(--tx3)',marginTop:2}}>${u.role}</div>
          </div>
        </div>
      </td>
      <td style=${{padding:'11px 15px'}}>
        <span style=${{fontSize:12,color:'var(--tx2)',fontFamily:'monospace'}}>${u.email}</span>
      </td>
            <td style=${{padding:'11px 15px',minWidth:180}}>
        ${editPw?html`
          <div style=${{display:'flex',gap:5,alignItems:'center'}}>
            <input class="inp" type="text" placeholder="New password" value=${newPw}
              style=${{height:28,fontSize:12,flex:1,minWidth:0}}
              onInput=${e=>setNewPw(e.target.value)}
              onKeyDown=${e=>{if(e.key==='Enter')resetPw();if(e.key==='Escape'){setEditPw(false);setNewPw('');}}}/>
            <button class="btn bp" style=${{padding:'4px 9px',fontSize:11,flexShrink:0}} onClick=${resetPw} disabled=${saving||!newPw.trim()}>
              ${saving?'…':'Save'}
            </button>
            <button class="btn bg" style=${{padding:'4px 8px',fontSize:11,flexShrink:0}} onClick=${()=>{setEditPw(false);setNewPw('');}}>✕</button>
          </div>`:html`
          <div style=${{display:'flex',alignItems:'center',gap:6}}>
            ${u.plain_password?html`
              <span style=${{
                fontFamily:'monospace',fontSize:12, color:showPw?'var(--tx2)':'transparent', background:showPw?'transparent':'var(--bd)', borderRadius:4,padding:'2px 6px', letterSpacing:showPw?'.5px':'.1px', userSelect:showPw?'text':'none', transition:'all .15s', minWidth:70,display:'inline-block'
              }}>${showPw?u.plain_password:'••••••••'}</span>
              <button title=${showPw?'Hide password':'Show password'}
                style=${{background:'none',border:'none',cursor:'pointer',padding:'2px 4px',color:'var(--tx3)',fontSize:12,transition:'color .1s'}}
                onClick=${()=>setShowPw(v=>!v)}
                onMouseEnter=${e=>e.currentTarget.style.color='var(--tx)'}
                onMouseLeave=${e=>e.currentTarget.style.color='var(--tx3)'}>
                ${showPw?'🙈':'👁'}
              </button>`:html`
              <span style=${{fontSize:11,color:'var(--tx3)',fontStyle:'italic'}}>not recorded</span>`}
            <button title="Reset password"
              style=${{background:'none',border:'none',cursor:'pointer',padding:'2px 5px',color:'var(--tx3)',fontSize:11,borderRadius:5,transition:'all .1s',flexShrink:0}}
              onClick=${()=>setEditPw(true)}
              onMouseEnter=${e=>{e.currentTarget.style.background='rgba(255,255,255,.06)';e.currentTarget.style.color='var(--ac)';}}
              onMouseLeave=${e=>{e.currentTarget.style.background='none';e.currentTarget.style.color='var(--tx3)';}}>
              ✏️ Reset
            </button>
          </div>`}
      </td>
      <td style=${{padding:'11px 15px'}}>
        <select class="sel" style=${{width:130,padding:'6px 28px 6px 10px'}} value=${u.role}
          onChange=${e=>api.put('/api/users/'+u.id,{role:e.target.value}).then(()=>reload&&reload())}
          disabled=${u.id===cu.id&&cu.role==='Admin'}>
          ${ROLES.map(r=>html`<option key=${r}>${r}</option>`)}
        </select>
      </td>
      <td style=${{padding:'11px 15px'}}>
        ${u.id!==cu.id?html`<button class="btn brd" style=${{padding:'5px 11px',fontSize:12}}
          onClick=${()=>window.confirm('Remove '+u.name+'?')&&api.del('/api/users/'+u.id).then(()=>reload&&reload())}>🗑</button>`:null}
      </td>
    </tr>`;
}

function TeamView({users,cu,reload}){
  const [tab,setTab]=useState('teams');
  const [showNew,setShowNew]=useState(false);const [name,setName]=useState('');const [email,setEmail]=useState('');const [pw,setPw]=useState('');const [role,setRole]=useState('Developer');const [err,setErr]=useState('');
  const [teams,setTeams]=useState([]);const [showNewTeam,setShowNewTeam]=useState(false);
  const [editTeam,setEditTeam]=useState(null);
  const [tName,setTName]=useState('');const [tLead,setTLead]=useState('');const [tMembers,setTMembers]=useState([]);
  const [savingTeam,setSavingTeam]=useState(false);
  const [memberSearch,setMemberSearch]=useState('');
  const [teamSearch,setTeamSearch]=useState('');

  const loadTeams=useCallback(async()=>{const d=await api.get('/api/teams');setTeams(Array.isArray(d)?d:[]);},[]);
  useEffect(()=>{loadTeams();},[loadTeams]);

  const add=async()=>{if(!name||!email||!pw){setErr('All fields required.');return;}setErr('');const r=await api.post('/api/users',{name,email,password:pw,role});if(r.error)setErr(r.error);else{await reload();setShowNew(false);setName('');setEmail('');setPw('');}};

  const openNewTeam=()=>{setEditTeam(null);setTName('');setTLead('');setTMembers([]);setShowNewTeam(true);};
  const openEditTeam=t=>{setEditTeam(t);setTName(t.name);setTLead(t.lead_id||'');setTMembers(JSON.parse(t.member_ids||'[]'));setShowNewTeam(true);};
  const saveTeam=async()=>{
    if(!tName.trim())return;
    setSavingTeam(true);
    const payload={name:tName,lead_id:tLead,member_ids:tMembers};
    if(editTeam)await api.put('/api/teams/'+editTeam.id,payload);
    else await api.post('/api/teams',payload);
    setSavingTeam(false);setShowNewTeam(false);setEditTeam(null);
    loadTeams();
  };
  const delTeam=async id=>{if(!window.confirm('Delete this team?'))return;await api.del('/api/teams/'+id);loadTeams();};
  const toggleMember=id=>{setTMembers(prev=>prev.includes(id)?prev.filter(x=>x!==id):[...prev,id]);};

  const umap=safe(users).reduce((a,u)=>{a[u.id]=u;return a;},{});
  const filteredMembers=useMemo(()=>safe(users).filter(u=>!memberSearch||u.name.toLowerCase().includes(memberSearch.toLowerCase())||u.email.toLowerCase().includes(memberSearch.toLowerCase())),[users,memberSearch]);
  const filteredTeams=useMemo(()=>teams.filter(t=>!teamSearch||t.name.toLowerCase().includes(teamSearch.toLowerCase())),[teams,teamSearch]);
  const ROLE_COLORS={Admin:'var(--ac)',Manager:'var(--gn)',TeamLead:'var(--cy)',Developer:'var(--pu)',Tester:'var(--am)',Viewer:'var(--tx3)'};

  return html`<div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px',boxSizing:'border-box'}}>
        <div style=${{display:'flex',gap:4,marginBottom:18,background:'var(--sf2)',borderRadius:12,padding:4,width:'fit-content',border:'1px solid var(--bd)'}}>
      ${['members','teams'].map(t=>html`
        <button key=${t} class="btn" onClick=${()=>setTab(t)}
          style=${{padding:'6px 18px',borderRadius:9,fontSize:12,fontWeight:600,border:'none',cursor:'pointer', background:tab===t?'var(--ac)':'transparent',color:tab===t?'var(--ac-tx)':'var(--tx2)',transition:'all .14s'}}>
          ${t==='members'?'👥 Members':'🏷 Teams'}
        </button>`)}
    </div>

    ${tab==='members'?html`
      <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:16}}>
        <div style=${{position:'relative',flex:1,maxWidth:300}}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
            style=${{position:'absolute',left:10,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" placeholder="Search members by name or email…" value=${memberSearch}
            style=${{paddingLeft:30,height:34,fontSize:12}} onInput=${e=>setMemberSearch(e.target.value)}/>
        </div>
        <span style=${{fontSize:12,color:'var(--tx3)',flexShrink:0}}>${filteredMembers.length} of ${safe(users).length}</span>
        <button class="btn bp" style=${{flexShrink:0}} onClick=${()=>setShowNew(true)}>+ Add Member</button>
      </div>
      <div class="card" style=${{padding:0,overflow:'auto'}}>
        <table style=${{width:'100%',borderCollapse:'collapse'}}>
          <thead><tr style=${{borderBottom:'1px solid var(--bd)',background:'var(--sf2)'}}>
            ${['Member','Email','Password','Role',''].map((h,i)=>html`<th key=${i} style=${{padding:'9px 15px',textAlign:'left',fontSize:10,fontFamily:'monospace',color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5}}>${h}</th>`)}
          </tr></thead>
          <tbody>
            ${filteredMembers.length===0?html`<tr><td colspan="5" style=${{padding:'20px',textAlign:'center',color:'var(--tx3)',fontSize:12}}>No members match your search.</td></tr>`:null}
            ${filteredMembers.map((u,i)=>html`<${MemberRow} key=${u.id} u=${u} cu=${cu} i=${i} total=${filteredMembers.length} reload=${reload} ROLE_COLORS=${ROLE_COLORS}/>`)}
          </tbody>
        </table>
      </div>`:null}

    ${tab==='teams'?html`
      <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:16}}>
        <div style=${{position:'relative',flex:1,maxWidth:300}}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
            style=${{position:'absolute',left:10,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" placeholder="Search teams…" value=${teamSearch}
            style=${{paddingLeft:30,height:34,fontSize:12}} onInput=${e=>setTeamSearch(e.target.value)}/>
        </div>
        <span style=${{fontSize:12,color:'var(--tx3)',flexShrink:0}}>${filteredTeams.length} of ${teams.length}</span>
        <button class="btn bp" style=${{flexShrink:0}} onClick=${openNewTeam}>+ New Team</button>
      </div>
      ${teams.length===0&&teamSearch===''?html`
        <div style=${{textAlign:'center',padding:'40px 16px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:12,border:'1px dashed var(--bd)'}}>
          <div style=${{fontSize:32,marginBottom:10}}>🏷</div>
          <div style=${{fontWeight:600,marginBottom:4}}>No teams yet</div>
          <div>Create sub-teams to group members and manage multi-team workflows</div>
        </div>`:null}
      <div style=${{display:'flex',flexDirection:'column',gap:10}}>
        ${filteredTeams.length===0&&teams.length>0?html`
          <div style=${{textAlign:'center',padding:'20px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:10,border:'1px solid var(--bd)'}}>No teams match your search.</div>`:null}
        ${filteredTeams.map(t=>{
          const members=JSON.parse(t.member_ids||'[]').map(id=>umap[id]).filter(Boolean);
          const lead=t.lead_id?umap[t.lead_id]:null;
          return html`
          <div key=${t.id} class="card" style=${{display:'flex',gap:14,alignItems:'flex-start'}}>
            <div style=${{width:44,height:44,borderRadius:12,background:'var(--ac3)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:20,flexShrink:0}}>🏷</div>
            <div style=${{flex:1,minWidth:0}}>
              <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
                <span style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}>${t.name}</span>
                <span class="tx3-11">${members.length} member${members.length!==1?'s':''}</span>
              </div>
              ${lead?html`<div style=${{display:'flex',alignItems:'center',gap:6,marginBottom:8}}>
                <span class="tx3-11">Lead:</span>
                <${Av} u=${lead} size=${20}/>
                <span style=${{fontSize:12,fontWeight:600,color:'var(--cy)'}}>${lead.name}</span>
              </div>`:null}
              <div style=${{display:'flex',gap:6,flexWrap:'wrap'}}>
                ${members.map(m=>html`
                  <div key=${m.id} style=${{display:'flex',alignItems:'center',gap:5,padding:'4px 10px',background:'var(--sf2)',borderRadius:20,border:'1px solid var(--bd)'}}>
                    <${Av} u=${m} size=${18}/>
                    <div>
                      <div style=${{fontSize:11,color:'var(--tx2)',fontWeight:500}}>${m.name}</div>
                      ${m.plain_password?html`<div style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',letterSpacing:.3}}>pw: ${m.plain_password}</div>`:null}
                    </div>
                  </div>`)}
              </div>
            </div>
            <div style=${{display:'flex',gap:6,flexShrink:0}}>
              <button class="btn bg" style=${{padding:'6px 10px',fontSize:12}} onClick=${()=>openEditTeam(t)}>✏️ Edit</button>
              <button class="btn brd" style=${{padding:'6px 10px',fontSize:12,color:'var(--rd)'}} onClick=${()=>delTeam(t.id)}>🗑</button>
            </div>
          </div>`;
        })}
      </div>`:null}

        ${showNew?html`<div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowNew(false)}>
      <div class="mo fi" style=${{maxWidth:400}}>
        <div style=${{display:'flex',justifyContent:'space-between',marginBottom:18}}><h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)'}}>👤 Add Member</h2><button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setShowNew(false)}>✕</button></div>
        <div style=${{display:'flex',flexDirection:'column',gap:11}}>
          <input class="inp" placeholder="Full Name" value=${name} onInput=${e=>setName(e.target.value)}/>
          <input class="inp" type="email" placeholder="Email" value=${email} onInput=${e=>setEmail(e.target.value)}/>
          <input class="inp" type="password" placeholder="Password" value=${pw} onInput=${e=>setPw(e.target.value)}/>
          <select class="sel" value=${role} onChange=${e=>setRole(e.target.value)}>${ROLES.map(r=>html`<option key=${r}>${r}</option>`)}</select>
          ${err?html`<div style=${{color:'var(--rd)',fontSize:12,padding:'7px 11px',background:'rgba(248,113,113,.07)',borderRadius:7}}>${err}</div>`:null}
          <div style=${{display:'flex',gap:9,justifyContent:'flex-end'}}>
            <button class="btn bg" onClick=${()=>setShowNew(false)}>Cancel</button>
            <button class="btn bp" onClick=${add}>Add Member</button>
          </div>
        </div>
      </div>
    </div>`:null}

        ${showNewTeam?html`<div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowNewTeam(false)}>
      <div class="mo fi" style=${{maxWidth:480}}>
        <div style=${{display:'flex',justifyContent:'space-between',marginBottom:18}}>
          <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)'}}>${editTeam?'✏️ Edit Team':'🏷 New Sub-Team'}</h2>
          <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setShowNewTeam(false)}>✕</button>
        </div>
        <div style=${{display:'flex',flexDirection:'column',gap:13}}>
          <div>
            <label class="lbl">Team Name *</label>
            <input class="inp" value=${tName} onInput=${e=>setTName(e.target.value)} placeholder="e.g. Frontend, Backend, QA, Design…"/>
          </div>
          <div>
            <label class="lbl">Team Lead</label>
            <select class="inp" value=${tLead} onChange=${e=>setTLead(e.target.value)}>
              <option value="">— No lead —</option>
              ${safe(users).map(u=>html`<option key=${u.id} value=${u.id}>${u.name} (${u.role})</option>`)}
            </select>
          </div>
          <div>
            <label class="lbl">Members</label>
            <div style=${{display:'flex',flexDirection:'column',gap:6,maxHeight:200,overflowY:'auto',border:'1px solid var(--bd)',borderRadius:9,padding:'8px 12px',background:'var(--sf2)'}}>
              ${safe(users).map(u=>html`
                <label key=${u.id} style=${{display:'flex',alignItems:'center',gap:10,cursor:'pointer',padding:'5px 0'}}>
                  <input type="checkbox" checked=${tMembers.includes(u.id)} onChange=${()=>toggleMember(u.id)}
                    style=${{width:16,height:16,accentColor:'var(--ac)',cursor:'pointer'}}/>
                  <${Av} u=${u} size=${24}/>
                  <div>
                    <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)'}}>${u.name}</div>
                    <div style=${{fontSize:10,color:ROLE_COLORS[u.role]||'var(--tx3)'}}>${u.role}</div>
                  </div>
                </label>`)}
            </div>
            <div style=${{fontSize:11,color:'var(--tx3)',marginTop:4}}>${tMembers.length} member${tMembers.length!==1?'s':''} selected</div>
          </div>
          <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:4}}>
            <button class="btn bg" onClick=${()=>setShowNewTeam(false)}>Cancel</button>
            <button class="btn bp" onClick=${saveTeam} disabled=${savingTeam||!tName.trim()}>
              ${savingTeam?'Saving...':editTeam?'Save Changes':'Create Team'}
            </button>
          </div>
        </div>
      </div>
    </div>`:null}
  </div>`;
}

/* ─── TicketsView ────────────────────────────────────────────────────────── */
function TicketsView({cu,users,projects,onReload,activeTeam,initialAssignee,initialStatus}){
  const [tickets,setTickets]=useState([]);
  const [busy,setBusy]=useState(true);
  const [filterStatus,setFilterStatus]=useState(initialStatus||'');
  const [filterPriority,setFilterPriority]=useState('');
  const [filterType,setFilterType]=useState('');
  const [filterAssignee,setFilterAssignee]=useState(()=>initialAssignee==='me'&&cu?cu.id:'');
  const [showNew,setShowNew]=useState(false);
  const [editTicket,setEditTicket]=useState(null);
  const [detailTicket,setDetailTicket]=useState(null);
  const [comments,setComments]=useState([]);
  const [newComment,setNewComment]=useState('');
  const [savingComment,setSavingComment]=useState(false);
  const [showResolved,setShowResolved]=useState(false);

  const canEdit=cu&&cu.role!=='Developer'&&cu.role!=='Viewer';
  const canDelete=cu&&['Admin','Manager','TeamLead'].includes(cu.role);

  const [nTitle,setNTitle]=useState('');
  const [nDesc,setNDesc]=useState('');
  const [nType,setNType]=useState('bug');
  const [nPriority,setNPriority]=useState('medium');
  const [nAssignee,setNAssignee]=useState(()=>cu&&(cu.role==='Developer'||cu.role==='Tester')?cu.id:'');
  const [nProject,setNProject]=useState('');
  const [nStatus,setNStatus]=useState('open');
  const [saving,setSaving]=useState(false);

  const load=useCallback(async()=>{
    setBusy(true);
    const url=activeTeam?'/api/tickets?team_id='+activeTeam.id:'/api/tickets';
    const d=await api.get(url);
    setTickets(Array.isArray(d)?d:[]);
    setBusy(false);
  },[activeTeam]);
  useEffect(()=>{load();},[load]);

  const visibleTickets=useMemo(()=>{
    return tickets.filter(t=>{
      const isResolved=t.status==='resolved'||t.status==='closed';
      if(isResolved&&!showResolved&&filterStatus!=='resolved'&&filterStatus!=='closed')return false;
      if(filterStatus&&t.status!==filterStatus)return false;
      if(filterPriority&&t.priority!==filterPriority)return false;
      if(filterType&&t.type!==filterType)return false;
      if(filterAssignee&&t.assignee!==filterAssignee)return false;
      return true;
    });
  },[tickets,showResolved,filterStatus,filterPriority,filterType,filterAssignee]);

  const saveTicket=async()=>{
    if(!nTitle.trim())return;
    setSaving(true);
    const payload={title:nTitle,description:nDesc,type:nType,priority:nPriority,assignee:nAssignee,project:nProject,status:nStatus,team_id:activeTeam?activeTeam.id:''};
    if(editTicket){await api.put('/api/tickets/'+editTicket.id,payload);}
    else{await api.post('/api/tickets',payload);}
    setSaving(false);setShowNew(false);setEditTicket(null);
    setNTitle('');setNDesc('');setNType('bug');setNPriority('medium');setNAssignee('');setNProject('');setNStatus('open');
    load();
  };

  const openEdit=(t)=>{
    setEditTicket(t);setNTitle(t.title);setNDesc(t.description||'');setNType(t.type||'bug');
    setNPriority(t.priority||'medium');setNAssignee(t.assignee||'');setNProject(t.project||'');setNStatus(t.status||'open');
    setShowNew(true);
  };

  const openDetail=async(t)=>{
    setDetailTicket(t);
    const c=await api.get('/api/tickets/'+t.id+'/comments');
    setComments(Array.isArray(c)?c:[]);
  };

  const postComment=async()=>{
    if(!newComment.trim()||!detailTicket)return;
    setSavingComment(true);
    await api.post('/api/tickets/'+detailTicket.id+'/comments',{content:newComment});
    setNewComment('');
    const c=await api.get('/api/tickets/'+detailTicket.id+'/comments');
    setComments(Array.isArray(c)?c:[]);
    setSavingComment(false);
  };

  const quickStatus=async(t,status)=>{
    await api.put('/api/tickets/'+t.id,{status});
    load();
    if(detailTicket&&detailTicket.id===t.id)setDetailTicket(prev=>({...prev,status}));
  };

  const del=async(id)=>{
    if(!window.confirm('Delete this ticket?'))return;
    await api.del('/api/tickets/'+id);
    setDetailTicket(null);load();
  };

  const TYPE_CFG={
    bug:{icon:'🐛',color:'var(--rd)',bg:'rgba(248,113,113,.12)',label:'Bug'}, feature:{icon:'✨',color:'var(--ac)',bg:'rgba(170,255,0,.12)',label:'Feature'}, improvement:{icon:'🔧',color:'var(--cy)',bg:'rgba(34,211,238,.12)',label:'Improvement'}, task:{icon:'✅',color:'var(--gn)',bg:'rgba(74,222,128,.12)',label:'Task'}, question:{icon:'❓',color:'var(--pu)',bg:'rgba(167,139,250,.12)',label:'Question'}, };
  const PRIORITY_CFG={
    critical:{icon:'🔴',color:'#ef4444',label:'Critical'}, high:{icon:'🟠',color:'#f97316',label:'High'}, medium:{icon:'🟡',color:'#eab308',label:'Medium'}, low:{icon:'🟢',color:'#22c55e',label:'Low'}, };
  const STATUS_CFG={
    open:{icon:'🔵',color:'var(--cy)',label:'Open'}, 'in-progress':{icon:'🟡',color:'var(--am)',label:'In Progress'}, review:{icon:'🟣',color:'var(--pu)',label:'In Review'}, resolved:{icon:'🟢',color:'var(--gn)',label:'Resolved'}, closed:{icon:'⚫',color:'var(--tx3)',label:'Closed'}, };

  const statCounts=Object.keys(STATUS_CFG).reduce((a,s)=>{a[s]=tickets.filter(t=>t.status===s).length;return a;},{});
  const myTicketsCount=tickets.filter(t=>t.assignee===cu.id&&t.status!=='closed'&&t.status!=='resolved').length;

  const umap=safe(users).reduce((a,u)=>{a[u.id]=u;return a;},{});

  const FORM=html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&(setShowNew(false),setEditTicket(null))}>
      <div class="mo fi" style=${{maxWidth:560}}>
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:18}}>
          <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)'}}>${editTicket?'✏️ Edit Ticket':'🎫 New Ticket'}</h2>
          <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>{setShowNew(false);setEditTicket(null);}}>✕</button>
        </div>
        <div style=${{display:'flex',flexDirection:'column',gap:13}}>
          <div>
            <label class="lbl">Title *</label>
            <input class="inp" value=${nTitle} onInput=${e=>setNTitle(e.target.value)} placeholder="Brief description of the issue"/>
          </div>
          <div>
            <label class="lbl">Description</label>
            <textarea class="inp" rows="3" style=${{resize:'vertical'}} value=${nDesc} onInput=${e=>setNDesc(e.target.value)} placeholder="Steps to reproduce, expected vs actual behaviour..."></textarea>
          </div>
          <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:10}}>
            <div>
              <label class="lbl">Type</label>
              <select class="inp" value=${nType} onChange=${e=>setNType(e.target.value)}>
                ${Object.entries(TYPE_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
              </select>
            </div>
            <div>
              <label class="lbl">Priority</label>
              <select class="inp" value=${nPriority} onChange=${e=>setNPriority(e.target.value)}>
                ${Object.entries(PRIORITY_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
              </select>
            </div>
            <div>
              <label class="lbl">Status</label>
              <select class="inp" value=${nStatus} onChange=${e=>setNStatus(e.target.value)}>
                ${Object.entries(STATUS_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
              </select>
            </div>
          </div>
          <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
            <div>
              <label class="lbl">Assignee</label>
              <select class="inp" value=${nAssignee} onChange=${e=>setNAssignee(e.target.value)}>
                <option value="">— Unassigned —</option>
                ${safe(users).map(u=>html`<option key=${u.id} value=${u.id}>${u.name}</option>`)}
              </select>
            </div>
            <div>
              <label class="lbl">Project</label>
              <select class="inp" value=${nProject} onChange=${e=>setNProject(e.target.value)}>
                <option value="">— No project —</option>
                ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
              </select>
            </div>
          </div>
          <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:4}}>
            <button class="btn bg" onClick=${()=>{setShowNew(false);setEditTicket(null);}}>Cancel</button>
            <button class="btn bp" onClick=${saveTicket} disabled=${saving||!nTitle.trim()}>
              ${saving?'Saving...':editTicket?'Save Changes':'Create Ticket'}
            </button>
          </div>
        </div>
      </div>
    </div>`;

  const DETAIL=detailTicket?html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&setDetailTicket(null)}>
      <div class="mo fi" style=${{maxWidth:620,maxHeight:'85vh',display:'flex',flexDirection:'column'}}>
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:16,flexShrink:0}}>
          <div style=${{flex:1,minWidth:0,marginRight:12}}>
            <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
              <span style=${{fontSize:18}}>${(TYPE_CFG[detailTicket.type]||TYPE_CFG.bug).icon}</span>
              <span style=${{fontSize:11,padding:'2px 8px',borderRadius:6,background:(PRIORITY_CFG[detailTicket.priority]||PRIORITY_CFG.medium).color+'22',color:(PRIORITY_CFG[detailTicket.priority]||PRIORITY_CFG.medium).color,fontWeight:700}}>${(PRIORITY_CFG[detailTicket.priority]||PRIORITY_CFG.medium).label}</span>
              <select value=${detailTicket.status} onChange=${e=>quickStatus(detailTicket,e.target.value)}
                style=${{fontSize:11,padding:'2px 8px',borderRadius:6,background:'var(--sf2)',border:'1px solid var(--bd)',color:'var(--tx)',cursor:'pointer'}}>
                ${Object.entries(STATUS_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
              <span class="id-badge id-ticket">${detailTicket.id}</span>
              ${detailTicket.type?html`<span class="id-badge" style=${{background:({'bug':'rgba(185,28,28,0.10)','feature':'rgba(29,78,216,0.10)','improvement':'rgba(14,116,144,0.10)','task':'rgba(21,128,61,0.10)','question':'rgba(109,40,217,0.10)'})[detailTicket.type]||'var(--ac3)',color:({'bug':'var(--rd)','feature':'var(--ac)','improvement':'var(--cy)','task':'var(--gn)','question':'var(--pu)'})[detailTicket.type]||'var(--ac)'}}>${detailTicket.type}</span>`:null}
            </div>
            <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)',marginBottom:4}}>${detailTicket.title}</h2>
            <div class="tx3-11">
              Reported by ${(umap[detailTicket.reporter]||{name:'Unknown'}).name} · ${new Date(detailTicket.created).toLocaleDateString()}
              ${detailTicket.assignee?html` · Assigned to <b style=${{color:'var(--tx2)'}}>${(umap[detailTicket.assignee]||{name:'?'}).name}</b>`:null}
            </div>
          </div>
          <div style=${{display:'flex',gap:6,flexShrink:0}}>
            ${canEdit?html`<button class="btn bg" style=${{fontSize:11,padding:'5px 9px'}} onClick=${()=>openEdit(detailTicket)}>✏️ Edit</button>`:null}
            ${canDelete?html`<button class="btn brd" style=${{fontSize:11,padding:'5px 9px',color:'var(--rd)'}} onClick=${()=>del(detailTicket.id)}>🗑</button>`:null}
            <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setDetailTicket(null)}>✕</button>
          </div>
        </div>
        ${detailTicket.description?html`
          <div style=${{background:'var(--sf2)',borderRadius:9,padding:'12px 14px',marginBottom:14,fontSize:13,color:'var(--tx2)',lineHeight:1.6,flexShrink:0,border:'1px solid var(--bd)'}}>
            ${detailTicket.description}
          </div>`:null}
        <div style=${{flex:1,overflowY:'auto',paddingBottom:8}}>
          <div style=${{fontWeight:700,fontSize:12,color:'var(--tx2)',marginBottom:10}}>💬 Comments (${comments.length})</div>
          ${comments.length===0?html`<p style=${{color:'var(--tx3)',fontSize:12,textAlign:'center',padding:'16px 0'}}>No comments yet. Be the first!</p>`:null}
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            ${comments.map(c=>html`
              <div key=${c.id} style=${{display:'flex',gap:10,padding:'10px 12px',background:'var(--sf2)',borderRadius:10,border:'1px solid var(--bd)'}}>
                <${Av} u=${umap[c.user_id]||{name:'?',color:'#888'}} size=${30}/>
                <div style=${{flex:1}}>
                  <div style=${{display:'flex',gap:8,alignItems:'center',marginBottom:4}}>
                    <span style=${{fontSize:12,fontWeight:700,color:'var(--tx)'}}>${(umap[c.user_id]||{name:'?'}).name}</span>
                    <span style=${{fontSize:10,color:'var(--tx3)'}}>${new Date(c.created).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</span>
                  </div>
                  <div style=${{fontSize:12,color:'var(--tx2)',lineHeight:1.5}}>${c.content}</div>
                </div>
              </div>`)}
          </div>
        </div>
        <div style=${{display:'flex',gap:9,paddingTop:12,borderTop:'1px solid var(--bd)',flexShrink:0}}>
          <input class="inp" style=${{flex:1}} value=${newComment} onInput=${e=>setNewComment(e.target.value)}
            onKeyDown=${e=>e.key==='Enter'&&!e.shiftKey&&postComment()}
            placeholder="Add a comment… (Enter to submit)"/>
          <button class="btn bp" onClick=${postComment} disabled=${savingComment||!newComment.trim()}>
            ${savingComment?html`<span class="spin"></span>`:'Send'}
          </button>
        </div>
      </div>
    </div>`:null;

  return html`
    <div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px',background:'var(--bg)'}}>
            <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
        <div style=${{display:'flex',gap:8,flexWrap:'wrap'}}>
          ${Object.entries(STATUS_CFG).map(([s,c])=>html`
            <button key=${s} class=${'chip'+(filterStatus===s?' on':'')} onClick=${()=>setFilterStatus(filterStatus===s?'':s)}
              style=${{fontSize:11,display:'flex',alignItems:'center',gap:4}}>
              ${c.icon} ${c.label} <span style=${{fontWeight:700,color:c.color}}>${statCounts[s]||0}</span>
            </button>`)}
        </div>
        <button class="btn bp" style=${{fontSize:12}} onClick=${()=>{setEditTicket(null);setNTitle('');setNDesc('');setNType('bug');setNPriority('medium');setNAssignee('');setNProject('');setNStatus('open');setShowNew(true);}}>
          + New Ticket
        </button>
      </div>

            <div style=${{display:'flex',gap:8,marginBottom:14,flexWrap:'wrap',alignItems:'center'}}>
        ${filterStatus?html`
          <div style=${{display:'flex',alignItems:'center',gap:6,padding:'4px 10px 4px 8px',background:'var(--sf2)',border:'1px solid var(--bd)',borderRadius:20,flexShrink:0}}>
            <span style=${{fontSize:11,color:'var(--tx2)',fontWeight:600}}>${(STATUS_CFG[filterStatus]||{label:filterStatus}).icon} ${(STATUS_CFG[filterStatus]||{label:filterStatus}).label}</span>
            <button onClick=${()=>setFilterStatus('')}
              style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:13,lineHeight:1,padding:'0 2px'}}>×</button>
          </div>`:null}
        ${filterAssignee?html`
          <div style=${{display:'flex',alignItems:'center',gap:6,padding:'4px 10px 4px 8px',background:'var(--ac3)',border:'1px solid var(--ac)',borderRadius:20,flexShrink:0}}>
            <div style=${{width:6,height:6,borderRadius:'50%',background:'var(--ac)',flexShrink:0}}></div>
            <span style=${{fontSize:11,fontWeight:700,color:'var(--ac)'}}>Assigned to me</span>
            <button onClick=${()=>setFilterAssignee('')}
              style=${{background:'none',border:'none',cursor:'pointer',color:'var(--ac)',fontSize:13,lineHeight:1,padding:'0 2px',marginLeft:2}}
              title="Clear filter">×</button>
          </div>`:null}
        <button class=${'chip'+(filterAssignee===cu.id?' on':'')} style=${{fontSize:11,flexShrink:0}}
          onClick=${()=>setFilterAssignee(filterAssignee===cu.id?'':cu.id)}>
          👤 My Tickets ${myTicketsCount>0?html`<span style=${{fontWeight:700,marginLeft:3}}>(${myTicketsCount})</span>`:null}
        </button>
        <select class="sel" style=${{fontSize:11,padding:'5px 10px',height:30}} value=${filterPriority} onChange=${e=>setFilterPriority(e.target.value)}>
          <option value="">All Priorities</option>
          ${Object.entries(PRIORITY_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
        </select>
        <select class="sel" style=${{fontSize:11,padding:'5px 10px',height:30}} value=${filterType} onChange=${e=>setFilterType(e.target.value)}>
          <option value="">All Types</option>
          ${Object.entries(TYPE_CFG).map(([v,c])=>html`<option key=${v} value=${v}>${c.icon} ${c.label}</option>`)}
        </select>
        <span style=${{fontSize:11,color:'var(--tx3)',alignSelf:'center',marginLeft:4}}>${visibleTickets.length} ticket${visibleTickets.length!==1?'s':''}</span>
      </div>

            ${busy?html`<div style=${{textAlign:'center',padding:40}}><div class="spin" style=${{margin:'0 auto'}}></div></div>`:null}
      ${!busy&&visibleTickets.length===0?html`
        <div style=${{textAlign:'center',padding:'48px 16px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:12,border:'1px solid var(--bd)'}}>
          <div style=${{fontSize:36,marginBottom:12}}>🎫</div>
          <div style=${{fontWeight:600,marginBottom:6}}>No tickets yet</div>
          <div>Create a ticket to track bugs, features, and tasks</div>
        </div>`:null}
      <div style=${{display:'flex',flexDirection:'column',gap:8}}>
        ${visibleTickets.map(t=>{
          const tc=TYPE_CFG[t.type]||TYPE_CFG.bug;
          const pc=PRIORITY_CFG[t.priority]||PRIORITY_CFG.medium;
          const sc=STATUS_CFG[t.status]||STATUS_CFG.open;
          const assignee=t.assignee?umap[t.assignee]:null;
          return html`
          <div key=${t.id} onClick=${()=>openDetail(t)}
            style=${{display:'flex',gap:12,padding:'12px 15px',background:'var(--sf)',borderRadius:11,border:'1px solid var(--bd)',alignItems:'center',cursor:'pointer',transition:'all .14s'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.background='var(--sf2)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.background='var(--sf)';}}>
                        <div style=${{width:36,height:36,borderRadius:9,background:tc.bg,display:'flex',alignItems:'center',justifyContent:'center',fontSize:17,flexShrink:0}}>${tc.icon}</div>
                        <div style=${{flex:1,minWidth:0}}>
              <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:3}}>
                <span class="id-badge id-ticket" style=${{fontSize:9,flexShrink:0}}>${t.id}</span>
                <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1}}>${t.title}</span>
                <span style=${{fontSize:10,padding:'1px 7px',borderRadius:5,background:sc.color+'22',color:sc.color,fontWeight:700,flexShrink:0}}>${sc.icon} ${sc.label}</span>
              </div>
              <div style=${{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
                <span style=${{fontSize:10,padding:'1px 6px',borderRadius:4,background:pc.color+'22',color:pc.color,fontWeight:600}}>${pc.icon} ${pc.label}</span>
                <span style=${{fontSize:10,color:'var(--tx3)'}}>${tc.label}</span>
                ${t.project?html`<span style=${{fontSize:10,color:'var(--tx3)'}}>📁 ${(safe(projects).find(p=>p.id===t.project)||{name:t.project}).name}</span>`:null}
                <span style=${{fontSize:10,color:'var(--tx3)',marginLeft:'auto'}}>${new Date(t.created).toLocaleDateString()}</span>
              </div>
            </div>
                        ${assignee?html`<div style=${{flexShrink:0}}><${Av} u=${assignee} size=${28}/></div>`:null}
          </div>`;})}
      </div>
      ${showNew?FORM:null}
      ${DETAIL}
    </div>`;
}

/* ─── WorkspaceSettings ───────────────────────────────────────────────────── */
function WorkspaceSettings({cu,onReload}){
  const [ws,setWs]=useState(null);const [wsName,setWsName]=useState('');const [aiKey,setAiKey]=useState('');const [showKey,setShowKey]=useState(false);const [saving,setSaving]=useState(false);const [saved,setSaved]=useState(false);
  const [emailEnabled,setEmailEnabled]=useState(true);const [smtpServer,setSmtpServer]=useState('smtp.gmail.com');const [smtpPort,setSmtpPort]=useState(587);const [smtpUsername,setSmtpUsername]=useState('');const [smtpPassword,setSmtpPassword]=useState('');const [fromEmail,setFromEmail]=useState('');const [showSmtpPass,setShowSmtpPass]=useState(false);const [testEmail,setTestEmail]=useState('');const [testingEmail,setTestingEmail]=useState(false);const [testResult,setTestResult]=useState(null);const [otpEnabled,setOtpEnabled]=useState(false);
  const [dmEnabled,setDmEnabled]=useState(true);
  const PERM_DEFAULTS={
    'Create & Edit Projects':   {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Create & Assign Tasks':    {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:false,Viewer:false}, 'Edit Tasks':               {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Delete Tasks':             {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Create Tickets':           {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:false}, 'Edit Tickets':             {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Delete Tickets':           {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Close / Resolve Tickets':  {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:false,Viewer:false}, 'Delete Projects':          {Admin:true, Manager:true, TeamLead:false,Developer:false,Tester:false,Viewer:false}, 'Send Channel Messages':    {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:true}, 'Manage Team Members':      {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false}, 'Manage Workspace Settings':{Admin:true, Manager:false,TeamLead:false,Developer:false,Tester:false,Viewer:false}, 'View All Projects':        {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:true}, 'Start Instant Meet Calls':       {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:true}, 'Delete Team Members':      {Admin:true, Manager:false,TeamLead:false,Developer:false,Tester:false,Viewer:false},
    'Post Announcements':       {Admin:true, Manager:true, TeamLead:false,Developer:false,Tester:false,Viewer:false},
    'Generate AI Standup':      {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:false},
    'AI Standup All Members':   {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false},
    'AI Code Review':           {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:false,Viewer:false},
    'AI Risk Analysis':         {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false},
    'View Time Report All':     {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false},
    'Build Intake Forms':       {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false},
    'Setup 2FA':                {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:true},
    'Enforce 2FA Workspace':    {Admin:true, Manager:false,TeamLead:false,Developer:false,Tester:false,Viewer:false}, };
  const storedPerms=()=>{try{return JSON.parse(localStorage.getItem('pf_perms')||'null');}catch{return null;}};
  const [perms,setPerms]=useState(()=>storedPerms()||PERM_DEFAULTS);
  const togglePerm=(label,role)=>{
    if(role==='Admin')return;// Admin always has all perms
    setPerms(prev=>{const n={...prev,[label]:{...prev[label],[role]:!prev[label][role]}};localStorage.setItem('pf_perms',JSON.stringify(n));return n;});
  };
  const resetPerms=()=>{setPerms(PERM_DEFAULTS);localStorage.removeItem('pf_perms');};

  useEffect(()=>{api.get('/api/workspace').then(d=>{if(!d.error){setWs(d);setWsName(d.name||'');setAiKey(d.ai_api_key?'•'.repeat(20):'');setEmailEnabled(d.email_enabled!==0);setSmtpServer(d.smtp_server||'smtp.gmail.com');setSmtpPort(d.smtp_port||587);setSmtpUsername(d.smtp_username||'');setSmtpPassword(d.smtp_password?'•'.repeat(16):'');setFromEmail(d.from_email||'');setOtpEnabled(!!d.otp_enabled);setDmEnabled(d.dm_enabled!==0);}});},[]);

  const save=async()=>{
    setSaving(true);
    const payload={name:wsName,email_enabled:emailEnabled,smtp_server:smtpServer,smtp_port:smtpPort,smtp_username:smtpUsername,from_email:fromEmail,otp_enabled:otpEnabled,dm_enabled:dmEnabled};
    if(aiKey&&!aiKey.startsWith('•'))payload.ai_api_key=aiKey;
    if(smtpPassword&&!smtpPassword.startsWith('•'))payload.smtp_password=smtpPassword;
    await api.put('/api/workspace',payload);
    setSaving(false);setSaved(true);setTimeout(()=>setSaved(false),2000);
    await onReload();
  };

  const sendTestEmail=async()=>{
    if(!testEmail){alert('Please enter an email address');return;}
    setTestingEmail(true);setTestResult(null);
    const r=await api.post('/api/workspace/test-email',{test_email:testEmail});
    setTestingEmail(false);
    setTestResult(r.success?{success:true,message:r.message}:{success:false,message:r.message||'Failed to send test email'});
    setTimeout(()=>setTestResult(null),5000);
  };

  const newInvite=async()=>{
    if(!window.confirm('Generate a new invite code? The old one will stop working.'))return;
    const r=await api.post('/api/workspace/new-invite',{});
    setWs(prev=>({...prev,invite_code:r.invite_code}));
  };

  const copy=text=>{navigator.clipboard&&navigator.clipboard.writeText(text);};

  if(!ws)return html`<div style=${{padding:40,textAlign:'center'}}><span class="spin"></span></div>`;

  return html`<div class="fi" style=${{height:'100%',overflowY:'auto',padding:'24px'}}>
    <div style=${{maxWidth:640}}>
      <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)',marginBottom:20}}>⚙ Workspace Settings</h2>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>🎨 Theme & Accent Color</h3>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Choose a preset or set a custom accent color for the UI.</p>
        <div style=${{display:'flex',gap:10,flexWrap:'wrap',alignItems:'center',marginBottom:12}}>
          ${[
            {name:'Ocean', ac:'#1d4ed8',ac2:'#1e40af',tx:'#ffffff'}, {name:'Cyan', ac:'#22d3ee',ac2:'#06b6d4',tx:'#001a1f'}, {name:'Purple', ac:'#a78bfa',ac2:'#8b5cf6',tx:'#1a0a2e'}, {name:'Pink', ac:'#f472b6',ac2:'#ec4899',tx:'#2d001a'}, {name:'Orange', ac:'#fb923c',ac2:'#f97316',tx:'#2d0f00'}, {name:'Green', ac:'#4ade80',ac2:'#22c55e',tx:'#002d10'}, ].map(({name,ac,ac2,tx})=>html`
            <button key=${name} title=${name}
              onClick=${()=>{
                const r=document.body.style;
                r.setProperty('--ac',ac);r.setProperty('--ac2',ac2);
                const hex=ac.replace('#','');const bigint=parseInt(hex,16);
                const ri=Math.round((bigint>>16)&255),gi=Math.round((bigint>>8)&255),bi=Math.round(bigint&255);
                r.setProperty('--ac3','rgba('+ri+','+gi+','+bi+',.10)');
                r.setProperty('--ac4','rgba('+ri+','+gi+','+bi+',.06)');
                r.setProperty('--ac-tx',tx);
                localStorage.setItem('pf_accent',JSON.stringify({ac,ac2,tx}));
              }}
              style=${{width:34,height:34,borderRadius:10,background:ac,border:'3px solid '+(localStorage.getItem('pf_accent')&&JSON.parse(localStorage.getItem('pf_accent')).ac===ac?'var(--tx)':'transparent'),cursor:'pointer',transition:'all .14s',boxShadow:'0 2px 8px rgba(0,0,0,.25)'}}
              onMouseEnter=${e=>e.currentTarget.style.transform='scale(1.15)'}
              onMouseLeave=${e=>e.currentTarget.style.transform='scale(1)'}
            ></button>`)}
          <div style=${{display:'flex',alignItems:'center',gap:8,marginLeft:4}}>
            <label style=${{fontSize:12,color:'var(--tx2)'}}>Custom:</label>
            <input type="color" defaultValue="#1d4ed8"
              style=${{width:34,height:34,borderRadius:10,border:'2px solid var(--bd)',cursor:'pointer',background:'none',padding:2}}
              onChange=${e=>{
                const hex=e.target.value;
                const r=document.body.style;
                r.setProperty('--ac',hex);
                const darker='#'+hex.slice(1).replace(/../g,c=>Math.max(0,parseInt(c,16)-16).toString(16).padStart(2,'0'));
                r.setProperty('--ac2',darker);
                const bigint=parseInt(hex.replace('#',''),16);
                const ri=Math.round((bigint>>16)&255),gi=Math.round((bigint>>8)&255),bi=Math.round(bigint&255);
                r.setProperty('--ac3','rgba('+ri+','+gi+','+bi+',.10)');
                r.setProperty('--ac4','rgba('+ri+','+gi+','+bi+',.06)');
                const lum=(0.299*ri+0.587*gi+0.114*bi)/255;
                const tx=lum>0.6?'#111111':'#f5f5f5';
                r.setProperty('--ac-tx',tx);
                localStorage.setItem('pf_accent',JSON.stringify({ac:hex,ac2:darker,tx}));
              }}/>
          </div>
          <button class="btn brd" style=${{fontSize:11,padding:'5px 10px',marginLeft:4}} onClick=${()=>{
            const r=document.body.style;
            r.setProperty('--ac','#1d4ed8');r.setProperty('--ac2','#1e40af');
            r.setProperty('--ac3','rgba(29,78,216,.10)');r.setProperty('--ac4','rgba(29,78,216,.06)');
            r.setProperty('--ac-tx','#ffffff');
            localStorage.removeItem('pf_accent');
          }}>↺ Reset</button>
        </div>
        <div style=${{fontSize:11,color:'var(--tx3)',padding:'8px 12px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)'}}>
          Preview: <span style=${{color:'var(--ac)',fontWeight:700}}>Active color</span> · <button class="btn bp" style=${{fontSize:10,padding:'2px 8px',marginLeft:4}}>Sample button</button>
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:16}}>🏢 Workspace</h3>
        <div style=${{display:'flex',flexDirection:'column',gap:12}}>
          <div><label class="lbl">Workspace Name</label><input class="inp" value=${wsName} onInput=${e=>setWsName(e.target.value)}/></div>
          <div><label class="lbl">Workspace ID</label><div style=${{fontSize:12,color:'var(--tx3)',fontFamily:'monospace',padding:'8px 12px',background:'var(--sf2)',borderRadius:8}}>${ws.id}</div></div>
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>🔗 Invite Code</h3>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Share this code with teammates to join your workspace.</p>
        <div style=${{display:'flex',alignItems:'center',gap:10}}>
          <div style=${{flex:1,textAlign:'center',padding:'14px',background:'linear-gradient(135deg,rgba(170,255,0,.12),rgba(109,40,217,0.10))',borderRadius:12,border:'1px solid rgba(170,255,0,.18)'}}>
            <div style=${{fontSize:28,fontWeight:700,color:'var(--ac2)',fontFamily:'monospace',letterSpacing:4}}>${ws.invite_code}</div>
          </div>
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            <button class="btn bp" style=${{fontSize:12,padding:'8px 14px'}} onClick=${()=>copy(ws.invite_code)}>📋 Copy</button>
            <button class="btn bam" style=${{fontSize:12,padding:'8px 14px'}} onClick=${newInvite}>↻ New Code</button>
          </div>
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>🤖 AI Assistant</h3>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Paste your Anthropic API key to enable the AI assistant. The key is stored securely in your workspace only.</p>
        <div><label class="lbl">Anthropic API Key</label>
          <div style=${{position:'relative'}}>
            <input class="inp" style=${{paddingRight:40,fontFamily:showKey?'monospace':'monospace',letterSpacing:aiKey.startsWith('•')?0:0}} type=${showKey?'text':'password'} placeholder="sk-ant-api..." value=${aiKey}
              onInput=${e=>setAiKey(e.target.value)} onFocus=${()=>{if(aiKey.startsWith('•'))setAiKey('');}}/>
            <button onClick=${()=>setShowKey(!showKey)} style=${{position:'absolute',right:11,top:'50%',transform:'translateY(-50%)',background:'none',border:'none',cursor:'pointer',color:'var(--tx3)'}}>${showKey?'🙈':'👁'}</button>
          </div>
        </div>
        <div style=${{marginTop:10,padding:'9px 12px',background:'rgba(99,102,241,.07)',borderRadius:8,border:'1px solid rgba(170,255,0,.15)',fontSize:12,color:'var(--tx2)'}}>
          💡 Get your API key at <b style=${{color:'var(--ac2)'}}>console.anthropic.com</b>. The AI can answer questions, create tasks, update statuses, and generate EOD reports.
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:4}}>
          <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em'}}>🔐 Role Permissions</h3>
          <button class="btn brd" style=${{fontSize:11,padding:'4px 10px'}} onClick=${resetPerms}>↺ Reset defaults</button>
        </div>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Click checkboxes to toggle permissions per role. Admin always has full access.</p>
        <div style=${{overflowX:'auto'}}>
          <table style=${{width:'100%',borderCollapse:'collapse',fontSize:12}}>
            <thead>
              <tr>
                <th style=${{padding:'8px 12px',textAlign:'left',color:'var(--tx3)',fontWeight:600,borderBottom:'1px solid var(--bd)'}}>Permission</th>
                ${['Admin','Manager','TeamLead','Developer','Tester','Viewer'].map(r=>html`
                  <th key=${r} style=${{padding:'8px 12px',textAlign:'center',color:r==='Admin'?'var(--ac)':'var(--tx3)',fontWeight:700,borderBottom:'1px solid var(--bd)',minWidth:80,fontSize:11}}>
                    ${r}${r==='Admin'?html`<div style=${{fontSize:9,fontWeight:400,color:'var(--tx3)'}}>locked</div>`:null}
                  </th>`)}
              </tr>
            </thead>
            <tbody>
              ${Object.entries(perms).map(([label,roleMap],i)=>html`
                <tr key=${label} style=${{background:i%2===0?'transparent':'var(--sf2)'}}>
                  <td style=${{padding:'9px 12px',color:'var(--tx2)',fontWeight:500,fontSize:12}}>${label}</td>
                  ${['Admin','Manager','TeamLead','Developer','Tester','Viewer'].map(r=>html`
                    <td key=${r} style=${{padding:'9px 12px',textAlign:'center'}}>
                      <label style=${{cursor:r==='Admin'?'not-allowed':'pointer',display:'inline-flex',alignItems:'center',justifyContent:'center'}}>
                        <input type="checkbox" checked=${!!roleMap[r]} disabled=${r==='Admin'}
                          onChange=${()=>togglePerm(label,r)}
                          style=${{width:16,height:16,accentColor:'var(--ac)',cursor:r==='Admin'?'not-allowed':'pointer'}}/>
                      </label>
                    </td>`)}
                </tr>`)}
            </tbody>
          </table>
        </div>
        <div style=${{marginTop:12,padding:'9px 13px',background:'rgba(170,255,0,.05)',borderRadius:9,border:'1px solid rgba(170,255,0,.15)',fontSize:12,color:'var(--tx3)'}}>
          💡 Changes save automatically. Assign roles in the <b style=${{color:'var(--tx2)'}}>Team</b> tab.
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <div style=${{display:'flex',alignItems:'flex-start',justifyContent:'space-between',gap:16}}>
          <div style=${{flex:1}}>
            <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>🔐 Two-Factor Login (OTP)</h3>
            <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:8}}>When enabled, all workspace members must verify their identity with a 6-digit code sent to their email after entering their password. Requires SMTP to be configured above.</p>
            <div style=${{padding:'9px 13px',background:otpEnabled?'rgba(170,255,0,0.06)':'rgba(255,255,255,0.02)',borderRadius:9,border:otpEnabled?'1px solid rgba(170,255,0,0.2)':'1px solid var(--bd)',fontSize:12,color:'var(--tx2)',display:'flex',flexDirection:'column',gap:5}}>
              <div style=${{display:'flex',alignItems:'center',gap:6}}>
                <span>${otpEnabled?'✅':'⬜'}</span>
                <span style=${{fontWeight:600,color:otpEnabled?'var(--ac)':'var(--tx2)'}}>OTP is ${otpEnabled?'ENABLED':'DISABLED'}</span>
              </div>
              ${otpEnabled?html`<div class="tx3-11">📧 A 6-digit code will be emailed to each user on every login · Code expires in 10 minutes · Resend available after 60s</div>`:null}
              ${!otpEnabled?html`<div class="tx3-11">Users log in with email + password only. Enable OTP to add email verification on every login.</div>`:null}
            </div>
            ${otpEnabled&&!smtpUsername?html`<div style=${{marginTop:8,padding:'7px 12px',background:'rgba(239,68,68,0.07)',borderRadius:8,border:'1px solid rgba(239,68,68,0.2)',fontSize:11,color:'#f87171'}}>⚠️ Warning: SMTP is not configured. OTP emails will fail. Configure SMTP above before enabling OTP.</div>`:null}
          </div>
          <div style=${{flexShrink:0,paddingTop:4}}>
            <label style=${{display:'flex',alignItems:'center',gap:10,cursor:'pointer'}}>
              <div onClick=${()=>setOtpEnabled(!otpEnabled)} style=${{
                width:44,height:24,borderRadius:100, background:otpEnabled?'var(--ac)':'rgba(255,255,255,0.1)', border:otpEnabled?'1px solid var(--ac)':'1px solid var(--bd)', position:'relative',cursor:'pointer',transition:'all .2s', flexShrink:0
              }}>
                <div style=${{
                  position:'absolute',top:2, left:otpEnabled?'22px':'2px', width:18,height:18,borderRadius:'50%', background:otpEnabled?'#040506':'var(--tx3)', transition:'left .2s', boxShadow:'0 1px 4px rgba(0,0,0,0.4)'
                }}></div>
              </div>
              <span style=${{fontSize:12,fontWeight:600,color:otpEnabled?'var(--ac)':'var(--tx3)'}}>
                ${otpEnabled?'On':'Off'}
              </span>
            </label>
          </div>
        </div>
      </div>

            <div class="card" style=${{marginBottom:0}}>
        <div style=${{display:'flex',alignItems:'flex-start',justifyContent:'space-between',gap:16}}>
          <div style=${{flex:1}}>
            <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>💬 Direct Messages (DMs)</h3>
            <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:8}}>Control whether workspace members can send private direct messages to each other. When disabled, DMs are hidden for all non-admin users.</p>
            <div style=${{padding:'9px 13px',background:dmEnabled?'rgba(29,78,216,0.06)':'rgba(255,255,255,0.02)',borderRadius:9,border:dmEnabled?'1px solid rgba(29,78,216,0.2)':'1px solid var(--bd)',fontSize:12,color:'var(--tx2)',display:'flex',flexDirection:'column',gap:5}}>
              <div style=${{display:'flex',alignItems:'center',gap:6}}>
                <span>${dmEnabled?'✅':'⬜'}</span>
                <span style=${{fontWeight:600,color:dmEnabled?'var(--ac)':'var(--tx2)'}}>Direct Messages are ${dmEnabled?'ENABLED':'DISABLED'}</span>
              </div>
              <div class="tx3-11">
                ${dmEnabled?'Members can send private messages to each other.':'Members cannot send or view DMs. Admins & Managers can still access DMs.'}
              </div>
            </div>
          </div>
          <div style=${{flexShrink:0,paddingTop:4}}>
            <label style=${{display:'flex',alignItems:'center',gap:10,cursor:'pointer'}}>
              <div onClick=${()=>setDmEnabled(!dmEnabled)} style=${{
                width:44,height:24,borderRadius:100, background:dmEnabled?'var(--ac)':'rgba(255,255,255,0.1)', border:dmEnabled?'1px solid var(--ac)':'1px solid var(--bd)', position:'relative',cursor:'pointer',transition:'all .2s', flexShrink:0
              }}>
                <div style=${{
                  position:'absolute',top:2, left:dmEnabled?'22px':'2px', width:18,height:18,borderRadius:'50%', background:dmEnabled?'#fff':'var(--tx3)', transition:'left .2s', boxShadow:'0 1px 4px rgba(0,0,0,0.4)'
                }}></div>
              </div>
              <span style=${{fontSize:12,fontWeight:600,color:dmEnabled?'var(--ac)':'var(--tx3)'}}>
                ${dmEnabled?'On':'Off'}
              </span>
            </label>
          </div>
        </div>
      </div>

      <${TOTPSetupPanel} cu=${cu}/>


      <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:10,padding:16,marginBottom:16}}>
        <div style=${{fontWeight:700,fontSize:14,color:'var(--tx)',marginBottom:10}}>🏷 White Label</div>
        <div style=${{display:'grid',gap:8}}>
          <div style=${{display:'flex',flexDirection:'column',gap:4}}>
            <label style=${{fontSize:12,fontWeight:600,color:'var(--tx2)'}}>Custom Platform Name</label>
            <input class="inp" id="wl_name" placeholder="e.g. MyTeam Hub" style=${{height:34,fontSize:13}}/>
          </div>
          <div style=${{display:'flex',flexDirection:'column',gap:4}}>
            <label style=${{fontSize:12,fontWeight:600,color:'var(--tx2)'}}>Logo URL</label>
            <input class="inp" id="wl_logo" placeholder="https://..." style=${{height:34,fontSize:13}}/>
          </div>
          <button class="btn bg" style=${{fontSize:12,width:'fit-content'}} onClick=${async()=>{
            const name=document.getElementById('wl_name')?.value||'';
            const logo=document.getElementById('wl_logo')?.value||'';
            await api.put('/api/workspace/white-label',{name,logo});
            alert('White label settings saved!');
          }}>Save White Label</button>
        </div>
      </div>

      <div style=${{display:'flex',gap:10,justifyContent:'flex-end'}}>
        <button class="btn bp" onClick=${save} disabled=${saving}>
          ${saving?html`<span class="spin"></span>`:saved?'✓ Saved!':'Save Settings'}
        </button>
      </div>
    </div>
  </div>`;
}


/* ─── Calendar View ──────────────────────────────────────────────────────── */
/* ─── Kanban Board View ──────────────────────────────────────────────────── */
/* ─── Docs / Wiki View ───────────────────────────────────────────────────── */
function DocsView({projects,cu}){
  const [tab,setTab]=useState('docs');
  const [docs,setDocs]=useState([]);const [sel,setSel]=useState(null);const [editing,setEditing]=useState(false);
  const [form,setForm]=useState({title:'',content:'',project:'',type:'general'});
  const [busy,setBusy]=useState(true);const [search,setSearch]=useState('');
  const [diagrams,setDiagrams]=useState(()=>{try{return JSON.parse(localStorage.getItem('vw_diag')||'[]');}catch{return [];}});
  const [selD,setSelD]=useState(null);const [editD,setEditD]=useState(false);
  const [diagForm,setDiagForm]=useState({title:'',content:'',type:'architecture'});

  const load=useCallback(async()=>{setBusy(true);const d=await api.get('/api/docs');setDocs(Array.isArray(d)?d:[]);setBusy(false);},[]);
  useEffect(()=>{load();},[load]);

  const saveDoc=async()=>{
    if(!form.title.trim())return;
    if(sel)await api.put('/api/docs/'+sel.id,form); else await api.post('/api/docs',form);
    setEditing(false);setSel(null);setForm({title:'',content:'',project:'',type:'general'});load();
  };
  const delDoc=async id=>{if(!window.confirm('Delete?'))return;await api.del('/api/docs/'+id);setSel(null);load();};

  const saveDiag=()=>{
    if(!diagForm.title.trim())return;
    const list=selD?diagrams.map(d=>d.id===selD.id?{...d,...diagForm}:d):[...diagrams,{...diagForm,id:'d'+Date.now(),created:new Date().toISOString()}];
    setDiagrams(list);try{localStorage.setItem('vw_diag',JSON.stringify(list));}catch{}
    setEditD(false);setSelD(null);setDiagForm({title:'',content:'',type:'architecture'});
  };
  const delDiag=id=>{if(!window.confirm('Delete?'))return;const l=diagrams.filter(d=>d.id!==id);setDiagrams(l);try{localStorage.setItem('vw_diag',JSON.stringify(l));}catch{}if(selD&&selD.id===id)setSelD(null);};

  const DTYPE={architecture:'🏗 Architecture',flow:'🔀 Flow Diagram',er:'🗄 ER Diagram',sequence:'📋 Sequence',infra:'☁️ Infrastructure',api:'⚡ API Design'};
  const DOCTYPE={general:'📄 General',technical:'🔧 Technical',process:'📋 Process',api:'⚡ API',meeting:'📝 Meeting Notes'};
  const fDocs=docs.filter(d=>!search||d.title.toLowerCase().includes(search.toLowerCase()));
  const fDiags=diagrams.filter(d=>!search||d.title.toLowerCase().includes(search.toLowerCase()));

  const Sidebar=({items,sel,onSel,empty,getLabel})=>html`
    <div style=${{width:220,borderRight:'1px solid var(--bd)',overflowY:'auto',padding:'6px',flexShrink:0}}>
      ${items.length===0?html`<div style=${{textAlign:'center',padding:'20px 8px',color:'var(--tx3)',fontSize:12}}>${empty}</div>`:null}
      ${items.map(it=>html`
        <button key=${it.id} onClick=${()=>onSel(it)}
          style=${{width:'100%',padding:'8px 10px',borderRadius:8,border:'none',cursor:'pointer',textAlign:'left',fontSize:12,marginBottom:2,
            background:sel&&sel.id===it.id?'var(--ac3)':'transparent',color:sel&&sel.id===it.id?'var(--ac)':'var(--tx2)',display:'flex',flexDirection:'column',gap:2}}>
          <span style=${{fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${it.title}</span>
          <span style=${{fontSize:10,color:'var(--tx3)'}}>${getLabel(it)}</span>
        </button>`)}
    </div>`;

  return html`<div class="fi" style=${{height:'100%',display:'flex',flexDirection:'column',overflow:'hidden'}}>
    <div style=${{flexShrink:0,padding:'10px 18px',borderBottom:'1px solid var(--bd)',display:'flex',gap:10,alignItems:'center'}}>
      <div style=${{display:'flex',background:'var(--sf2)',borderRadius:8,padding:2,gap:1}}>
        ${['docs','diagrams'].map(t=>html`
          <button key=${t} class=${'tb'+(tab===t?' act':'')} style=${{fontSize:12,padding:'5px 14px'}} onClick=${()=>setTab(t)}>
            ${t==='docs'?'📄 Documentation':'🏗 Architecture Diagrams'}
          </button>`)}
      </div>
      <input class="inp" placeholder="Search..." value=${search} onInput=${e=>setSearch(e.target.value)} style=${{height:28,fontSize:12,flex:1,maxWidth:220}}/>
      <button class="btn bp" style=${{fontSize:12}} onClick=${()=>{
        if(tab==='docs'){setSel(null);setForm({title:'',content:'',project:'',type:'general'});setEditing(true);}
        else{setSelD(null);setDiagForm({title:'',content:'',type:'architecture'});setEditD(true);}
      }}>+ New ${tab==='docs'?'Doc':'Diagram'}</button>
    </div>

    ${tab==='docs'?html`<div style=${{flex:1,display:'flex',overflow:'hidden'}}>
      <${Sidebar} items=${fDocs} sel=${sel} onSel=${d=>{setSel(d);setEditing(false);}} empty="No documents yet." getLabel=${d=>(DOCTYPE[d.type]||'📄')+' · '+new Date(d.created||Date.now()).toLocaleDateString()}/>
      <div style=${{flex:1,overflowY:'auto',padding:'16px 20px'}}>
        ${busy?html`<div style=${{textAlign:'center',paddingTop:40}}><div class="spin" style=${{margin:'0 auto'}}></div></div>`:null}
        ${!sel&&!editing&&!busy?html`<div style=${{textAlign:'center',paddingTop:60,color:'var(--tx3)',fontSize:13}}>
          <div style=${{fontSize:36,marginBottom:10}}>📄</div><p>Select a document or create a new one</p>
        </div>`:null}
        ${sel&&!editing?html`<div>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:16}}>
            <div>
              <div style=${{fontSize:9,color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:.5,marginBottom:4}}>${DOCTYPE[sel.type]||'DOC'}</div>
              <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)',margin:0}}>${sel.title}</h2>
            </div>
            <div style=${{display:'flex',gap:6}}>
              <button class="btn bg" style=${{fontSize:12}} onClick=${()=>{setForm({title:sel.title,content:sel.content||'',project:sel.project||'',type:sel.type||'general'});setEditing(true);}}>✏️ Edit</button>
              <button class="btn brd" style=${{fontSize:12,color:'var(--rd)'}} onClick=${()=>delDoc(sel.id)}>🗑</button>
            </div>
          </div>
          <div style=${{fontSize:14,color:'var(--tx2)',lineHeight:1.8,whiteSpace:'pre-wrap',background:'var(--sf)',borderRadius:10,padding:'16px 20px',border:'1px solid var(--bd)'}}>${sel.content||'No content.'}</div>
        </div>`:null}
        ${editing?html`<div style=${{display:'flex',flexDirection:'column',gap:12}}>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
            <h3 style=${{margin:0,fontSize:15,fontWeight:700}}>${sel?'Edit Document':'New Document'}</h3>
            <button class="btn bg" onClick=${()=>setEditing(false)}>✕</button>
          </div>
          <div><label class="lbl">Title</label><input class="inp" value=${form.title} onInput=${e=>setForm(p=>({...p,title:e.target.value}))} placeholder="Document title"/></div>
          <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
            <div><label class="lbl">Type</label>
              <select class="inp" value=${form.type} onChange=${e=>setForm(p=>({...p,type:e.target.value}))}>
                ${Object.entries(DOCTYPE).map(([v,l])=>html`<option key=${v} value=${v}>${l}</option>`)}
              </select></div>
            <div><label class="lbl">Project</label>
              <select class="inp" value=${form.project} onChange=${e=>setForm(p=>({...p,project:e.target.value}))}>
                <option value="">— None —</option>
                ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
              </select></div>
          </div>
          <div><label class="lbl">Content</label>
            <textarea class="inp" rows="14" style=${{resize:'vertical',fontFamily:'monospace',fontSize:13,lineHeight:1.6}}
              value=${form.content} onInput=${e=>setForm(p=>({...p,content:e.target.value}))} placeholder="Write documentation here..."></textarea></div>
          <div style=${{display:'flex',gap:8,justifyContent:'flex-end'}}>
            <button class="btn bg" onClick=${()=>setEditing(false)}>Cancel</button>
            <button class="btn bp" onClick=${saveDoc} disabled=${!form.title.trim()}>Save</button>
          </div>
        </div>`:null}
      </div>
    </div>`:null}

    ${tab==='diagrams'?html`<div style=${{flex:1,display:'flex',overflow:'hidden'}}>
      <${Sidebar} items=${fDiags} sel=${selD} onSel=${d=>{setSelD(d);setEditD(false);}} empty="No diagrams yet." getLabel=${d=>DTYPE[d.type]||'🏗'}/>
      <div style=${{flex:1,overflowY:'auto',padding:'16px 20px'}}>
        ${!selD&&!editD?html`<div style=${{textAlign:'center',paddingTop:60,color:'var(--tx3)',fontSize:13}}>
          <div style=${{fontSize:40,marginBottom:10}}>🏗</div>
          <p style=${{fontWeight:600,color:'var(--tx2)',marginBottom:6}}>Architecture Diagrams</p>
          <p>Document system architecture, flows, ER diagrams,<br/>API designs, and infrastructure maps.</p>
        </div>`:null}
        ${selD&&!editD?html`<div>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
            <div>
              <div style=${{fontSize:9,color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',marginBottom:4}}>${DTYPE[selD.type]||'DIAGRAM'}</div>
              <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)',margin:0}}>${selD.title}</h2>
            </div>
            <div style=${{display:'flex',gap:6}}>
              <button class="btn bg" style=${{fontSize:12}} onClick=${()=>{setDiagForm({title:selD.title,content:selD.content||'',type:selD.type||'architecture'});setEditD(true);}}>✏️ Edit</button>
              <button class="btn brd" style=${{fontSize:12,color:'var(--rd)'}} onClick=${()=>delDiag(selD.id)}>🗑</button>
            </div>
          </div>
          <pre style=${{fontFamily:'monospace',fontSize:13,color:'var(--tx2)',lineHeight:1.7,margin:0,whiteSpace:'pre-wrap',wordBreak:'break-word',background:'var(--sf)',borderRadius:10,padding:'16px 20px',border:'1px solid var(--bd)'}}>${selD.content||'No content.'}</pre>
        </div>`:null}
        ${editD?html`<div style=${{display:'flex',flexDirection:'column',gap:12}}>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
            <h3 style=${{margin:0,fontSize:15,fontWeight:700}}>${selD?'Edit Diagram':'New Diagram'}</h3>
            <button class="btn bg" onClick=${()=>setEditD(false)}>✕</button>
          </div>
          <div><label class="lbl">Title</label><input class="inp" value=${diagForm.title} onInput=${e=>setDiagForm(p=>({...p,title:e.target.value}))} placeholder="e.g. System Architecture"/></div>
          <div><label class="lbl">Type</label>
            <select class="inp" value=${diagForm.type} onChange=${e=>setDiagForm(p=>({...p,type:e.target.value}))}>
              ${Object.entries(DTYPE).map(([v,l])=>html`<option key=${v} value=${v}>${l}</option>`)}
            </select></div>
          <div><label class="lbl">Diagram Content (Mermaid / ASCII / PlantUML / text)</label>
            <textarea class="inp" rows="16" style=${{resize:'vertical',fontFamily:'monospace',fontSize:12,lineHeight:1.6}}
              value=${diagForm.content} onInput=${e=>setDiagForm(p=>({...p,content:e.target.value}))}
              placeholder="graph TD&#10;  A[User] --> B[API Gateway]&#10;  B --> C[Auth Service]&#10;  B --> D[Task Service]&#10;  D --> E[(PostgreSQL)]"></textarea></div>
          <div style=${{padding:'9px 13px',background:'rgba(29,78,216,0.06)',borderRadius:9,border:'1px solid rgba(29,78,216,0.15)',fontSize:12,color:'var(--tx2)'}}>
            💡 Use Mermaid syntax, ASCII art, or plain text. All formats supported.
          </div>
          <div style=${{display:'flex',gap:8,justifyContent:'flex-end'}}>
            <button class="btn bg" onClick=${()=>setEditD(false)}>Cancel</button>
            <button class="btn bp" onClick=${saveDiag} disabled=${!diagForm.title.trim()}>Save</button>
          </div>
        </div>`:null}
      </div>
    </div>`:null}
  </div>`;
}


function TimeTracker({taskId,cu}){
  const [logs,setLogs]=useState([]);
  const [form,setForm]=useState({minutes:'',description:'',logged_date:new Date().toISOString().slice(0,10)});
  const [running,setRunning]=useState(false);
  const [elapsed,setElapsed]=useState(0);
  const [startTime,setStartTime]=useState(null);
  const load=async()=>{const r=await api.get(`/api/time-logs?task_id=${taskId}`);setLogs(r||[]);};
  useEffect(()=>{load();},[taskId]);
  useEffect(()=>{
    if(!running)return;
    const id=setInterval(()=>setElapsed(Math.floor((Date.now()-startTime)/1000)),1000);
    return()=>clearInterval(id);
  },[running,startTime]);
  const startTimer=()=>{setStartTime(Date.now());setRunning(true);setElapsed(0);};
  const stopTimer=async()=>{
    const mins=Math.max(1,Math.round(elapsed/60));
    setRunning(false);
    await api.post('/api/time-logs',{task_id:taskId,minutes:mins,description:'Timer session',logged_date:form.logged_date});
    load();
  };
  const addManual=async()=>{
    if(!form.minutes)return;
    await api.post('/api/time-logs',{task_id:taskId,...form,minutes:+form.minutes});
    setForm({minutes:'',description:'',logged_date:new Date().toISOString().slice(0,10)});
    load();
  };
  const total=logs.reduce((s,l)=>s+l.minutes,0);
  const fmt=m=>`${Math.floor(m/60)}h ${m%60}m`;
  return html`<div style=${{marginTop:12}}>
    <div style=${{fontWeight:700,fontSize:12,color:'var(--tx2)',marginBottom:8}}>TIME TRACKING</div>
    <div style=${{display:'flex',gap:8,marginBottom:10,alignItems:'center'}}>
      ${running?html`
        <span style=${{fontSize:16,fontWeight:700,color:'var(--ac)',fontFamily:'monospace'}}>${String(Math.floor(elapsed/3600)).padStart(2,'0')}:${String(Math.floor((elapsed%3600)/60)).padStart(2,'0')}:${String(elapsed%60).padStart(2,'0')}</span>
        <button class="btn br" style=${{fontSize:12}} onClick=${stopTimer}>⏹ Stop & Log</button>`:
      html`<button class="btn bp" style=${{fontSize:12}} onClick=${startTimer}>▶ Start Timer</button>`}
      <span style=${{fontSize:12,color:'var(--tx3)',marginLeft:'auto'}}>Total: <b>${fmt(total)}</b></span>
    </div>
    <div style=${{display:'flex',gap:6,marginBottom:10}}>
      <input class="inp" type="number" placeholder="Minutes" value=${form.minutes} onInput=${e=>setForm({...form,minutes:e.target.value})} style=${{width:80,height:30,fontSize:12}}/>
      <input class="inp" placeholder="What did you work on?" value=${form.description} onInput=${e=>setForm({...form,description:e.target.value})} style=${{flex:1,height:30,fontSize:12}}/>
      <button class="btn bg" style=${{fontSize:12,height:30,padding:'0 10px'}} onClick=${addManual}>Log</button>
    </div>
    ${logs.slice(0,5).map(l=>html`
      <div key=${l.id} style=${{display:'flex',alignItems:'center',gap:8,padding:'5px 0',borderBottom:'1px solid var(--bd)',fontSize:12}}>
        <span style=${{color:'var(--ac)',fontWeight:600,minWidth:48}}>${fmt(l.minutes)}</span>
        <span style=${{flex:1,color:'var(--tx2)'}}>${l.description||'—'}</span>
        <span style=${{color:'var(--tx3)'}}>${(l.logged_date||'').slice(5)}</span>
        <button style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:13}} onClick=${async()=>{await api.del(`/api/time-logs/${l.id}`);load();}}>✕</button>
      </div>`)}
  </div>`;
}

/* ─── Task Dependencies Panel ────────────────────────────────────────────── */
function TaskDepsPanel({taskId,allTasks}){
  const [deps,setDeps]=useState([]);
  const [selDep,setSelDep]=useState('');
  const load=async()=>{const r=await api.get(`/api/tasks/${taskId}/dependencies`);setDeps(r||[]);};
  useEffect(()=>{load();},[taskId]);
  const add=async()=>{if(!selDep)return;await api.post(`/api/tasks/${taskId}/dependencies`,{dep_id:selDep});setSelDep('');load();};
  const rem=async(depId)=>{await api.del(`/api/tasks/${taskId}/dependencies/${depId}`);load();};
  const available=safe(allTasks).filter(t=>t.id!==taskId&&!deps.find(d=>d.id===t.id));
  return html`<div style=${{marginTop:12}}>
    <div style=${{fontWeight:700,fontSize:12,color:'var(--tx2)',marginBottom:8}}>DEPENDENCIES (BLOCKED BY)</div>
    ${deps.map(d=>html`
      <div key=${d.id} style=${{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
        <span style=${{fontSize:10,padding:'1px 6px',borderRadius:4,background:'var(--sf2)',color:'var(--tx3)',fontFamily:'monospace'}}>${d.id}</span>
        <span style=${{flex:1,fontSize:12,color:d.stage==='completed'?'#15803d':'var(--tx)'}}>${d.title}</span>
        <span style=${{fontSize:10,color:d.stage==='completed'?'#15803d':'#d97706',fontWeight:600}}>${d.stage==='completed'?'✓ Done':'Pending'}</span>
        <button style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)'}} onClick=${()=>rem(d.id)}>✕</button>
      </div>`)}
    <div style=${{display:'flex',gap:6,marginTop:6}}>
      <select class="inp" style=${{flex:1,height:28,fontSize:12}} value=${selDep} onChange=${e=>setSelDep(e.target.value)}>
        <option value="">Add dependency…</option>
        ${available.map(t=>html`<option value=${t.id}>${t.id} – ${t.title}</option>`)}
      </select>
      <button class="btn bg" style=${{fontSize:12,height:28,padding:'0 10px'}} onClick=${add}>Add</button>
    </div>
  </div>`;
}

/* ─── Integrations Dashboard View ────────────────────────────────────────── */
/* ─── Referral Panel (for settings) ─────────────────────────────────────── */
/* ─── Announcements Banner + View ───────────────────────────────────────── */
/* ─── AI Standup View ────────────────────────────────────────────────────── */
/* ─── AI Code Review View ────────────────────────────────────────────────── */
/* ─── AI Risk View ───────────────────────────────────────────────────────── */
/* ─── Time Report View ───────────────────────────────────────────────────── */
function TimeReportView({cu,users}){
  const [logs,setLogs]=useState([]);
  const [period,setPeriod]=useState('week');
  const [userFilter,setUserFilter]=useState('');
  const [loading,setLoading]=useState(false);
  const canSeeAll=cu&&['Admin','Manager','TeamLead'].includes(cu.role);
  const load=async()=>{
    setLoading(true);
    const params=new URLSearchParams({period});
    if(userFilter)params.append('user_id',userFilter);
    const r=await api.get('/api/reports/time?'+params);
    setLogs(r||[]);setLoading(false);
  };
  useEffect(()=>{load();},[period,userFilter]);
  // Group by user
  const byUser={};
  logs.forEach(l=>{
    const key=l.user_name||l.user_id;
    if(!byUser[key])byUser[key]={name:key,total:0,logs:[]};
    byUser[key].total+=l.minutes||0;
    byUser[key].logs.push(l);
  });
  const totalMins=logs.reduce((s,l)=>s+(l.minutes||0),0);
  const fmt=m=>`${Math.floor(m/60)}h ${m%60}m`;
  const exportCSV=()=>{
    const rows=[['Date','User','Task','Project','Minutes','Description'],...logs.map(l=>[l.logged_date,l.user_name,l.task_title,l.project_name,l.minutes,l.description])];
    const csv=rows.map(r=>r.join(',')).join('\n');
    const blob=new Blob([csv],{type:'text/csv'});
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download=`time-report-${period}.csv`;a.click();
  };
  return html`<div style=${{flex:1,overflowY:'auto',padding:'20px 24px'}}>
    <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:20,flexWrap:'wrap'}}>
      <h2 style=${{margin:0,fontSize:20,fontWeight:700,color:'var(--tx)'}}>⏱️ Time Report</h2>
      <div style=${{display:'flex',gap:6,marginLeft:'auto',alignItems:'center',flexWrap:'wrap'}}>
        <select class="inp" style=${{height:32,fontSize:12,width:120}} value=${period} onChange=${e=>setPeriod(e.target.value)}>
          <option value="week">Last 7 days</option>
          <option value="month">This month</option>
          <option value="quarter">Last 90 days</option>
        </select>
        ${canSeeAll?html`
          <select class="inp" style=${{height:32,fontSize:12,width:150}} value=${userFilter} onChange=${e=>setUserFilter(e.target.value)}>
            <option value="">All members</option>
            ${safe(users).map(u=>html`<option value=${u.id}>${u.name}</option>`)}
          </select>`:null}
        <button class="btn bg" style=${{fontSize:12,height:32,padding:'0 12px'}} onClick=${exportCSV}>Export CSV</button>
      </div>
    </div>
    <!-- Summary cards -->
    <div style=${{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:12,marginBottom:20}}>
      <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:10,padding:'14px 16px',textAlign:'center'}}>
        <div style=${{fontSize:24,fontWeight:800,color:'var(--ac)'}}>${fmt(totalMins)}</div>
        <div style=${{fontSize:12,color:'var(--tx3)',marginTop:2}}>Total time logged</div>
      </div>
      <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:10,padding:'14px 16px',textAlign:'center'}}>
        <div style=${{fontSize:24,fontWeight:800,color:'var(--ac)'}}>${Object.keys(byUser).length}</div>
        <div style=${{fontSize:12,color:'var(--tx3)',marginTop:2}}>Active members</div>
      </div>
      <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:10,padding:'14px 16px',textAlign:'center'}}>
        <div style=${{fontSize:24,fontWeight:800,color:'var(--ac)'}}>${logs.length}</div>
        <div style=${{fontSize:12,color:'var(--tx3)',marginTop:2}}>Log entries</div>
      </div>
    </div>
    <!-- By user breakdown -->
    ${Object.values(byUser).sort((a,b)=>b.total-a.total).map(u=>html`
      <div key=${u.name} style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:10,marginBottom:12,overflow:'hidden'}}>
        <div style=${{padding:'12px 16px',display:'flex',alignItems:'center',justifyContent:'space-between',background:'var(--sf2)',borderBottom:'1px solid var(--bd)'}}>
          <div style=${{fontWeight:700,fontSize:14,color:'var(--tx)'}}>${u.name}</div>
          <div style=${{fontWeight:700,fontSize:14,color:'var(--ac)'}}>${fmt(u.total)}</div>
        </div>
        ${u.logs.slice(0,5).map(l=>html`
          <div style=${{display:'flex',gap:12,padding:'8px 16px',borderBottom:'1px solid var(--bd)',fontSize:12}}>
            <span style=${{color:'var(--tx3)',minWidth:80}}>${(l.logged_date||'').slice(5)}</span>
            <span style=${{color:'var(--tx)',flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${l.task_title||'—'}</span>
            <span style=${{color:'var(--tx3)',minWidth:60}}>${l.project_name||'—'}</span>
            <span style=${{fontWeight:600,color:'var(--ac)',minWidth:50,textAlign:'right'}}>${fmt(l.minutes||0)}</span>
          </div>`)}
        ${u.logs.length>5?html`<div style=${{padding:'6px 16px',fontSize:11,color:'var(--tx3)'}}>+${u.logs.length-5} more entries</div>`:null}
      </div>`)}
    ${!loading&&!logs.length?html`<div style=${{textAlign:'center',padding:'40px 0',color:'var(--tx3)'}}><div style=${{fontSize:36,marginBottom:10}}>⏱️</div><div>No time logs for this period</div></div>`:null}
    ${loading?html`<div style=${{textAlign:'center',padding:40}}><span class="spin"></span></div>`:null}
  </div>`;
}

/* ─── Forms & Intake View ────────────────────────────────────────────────── */
/* ─── TOTP 2FA Setup Panel (in settings) ────────────────────────────────── */
function TOTPSetupPanel({cu}){
  const [status,setStatus]=useState(null);
  const [setup,setSetup]=useState(null);
  const [code,setCode]=useState('');
  const [loading,setLoading]=useState(false);
  const [msg,setMsg]=useState('');
  const [copied,setCopied]=useState(false);
  const codeRefs=[useRef(),useRef(),useRef(),useRef(),useRef(),useRef()];

  const load=async()=>{const r=await api.get('/api/totp/status');setStatus(r?.enabled);};
  useEffect(()=>{load();},[]);

  const startSetup=async()=>{
    setLoading(true);setMsg('');
    const r=await api.post('/api/totp/setup',{});
    setLoading(false);
    if(r?.error)setMsg(r.error);
    else{setSetup(r);setCode('');}
  };

  const handleDigit=(i,val)=>{
    const digits=code.split('');
    digits[i]=val.replace(/\D/g,'').slice(-1);
    const nc=digits.join('');
    setCode(nc);
    if(val&&i<5)codeRefs[i+1].current?.focus();
    if(nc.length===6&&digits.every(d=>d))setTimeout(verify,80);
  };

  const handleKey=(i,e)=>{
    if(e.key==='Backspace'&&!code[i]&&i>0)codeRefs[i-1].current?.focus();
    if(e.key==='Enter'&&code.length===6)verify();
  };

  const handlePaste=(e)=>{
    const p=e.clipboardData.getData('text').replace(/\D/g,'').slice(0,6);
    if(p.length===6){setCode(p);setTimeout(verify,120);}
    e.preventDefault();
  };

  const verify=async()=>{
    const c=code.replace(/\D/g,'');
    if(c.length!==6)return;
    setLoading(true);setMsg('');
    const r=await api.post('/api/totp/verify',{code:c});
    setLoading(false);
    if(r?.ok){setMsg('✅ 2FA enabled successfully!');setSetup(null);setCode('');load();}
    else{setMsg(r?.error||'Invalid code — check your authenticator app and try again');setCode('');codeRefs[0].current?.focus();}
  };

  const disable=async()=>{
    if(!confirm('Disable 2FA? Your account will only be protected by your password.'))return;
    await api.post('/api/totp/disable',{});setStatus(false);setMsg('2FA has been disabled.');
  };

  const copySecret=()=>{
    navigator.clipboard?.writeText(setup.secret||'');
    setCopied(true);setTimeout(()=>setCopied(false),2000);
  };

  // QR code via Google Charts API (free, no auth, works offline-friendly)
  const qrUrl=setup?.uri?`https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=${encodeURIComponent(setup.uri)}`:'';

  return html`<div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'18px 20px',marginBottom:16}}>
    <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:12}}>
      <div>
        <div style=${{fontWeight:700,fontSize:14,color:'var(--tx)',display:'flex',alignItems:'center',gap:7}}>
          <span style=${{fontSize:18}}>🔑</span> Two-Factor Authentication
        </div>
        <div style=${{fontSize:12,color:'var(--tx3)',marginTop:3}}>Protect your account with Google Authenticator, Authy, or 1Password</div>
      </div>
      <span style=${{fontSize:11,fontWeight:700,padding:'3px 10px',borderRadius:99,
        background:status?'rgba(21,128,61,0.12)':'rgba(100,116,139,0.1)',
        color:status?'#15803d':'#64748b',border:'1px solid '+(status?'rgba(21,128,61,0.25)':'rgba(100,116,139,0.2)')}}>
        ${status===null?'Checking…':status?'✓ Enabled':'Disabled'}
      </span>
    </div>

    ${msg?html`<div style=${{fontSize:12,padding:'9px 13px',borderRadius:8,marginBottom:12,
      background:msg.startsWith('✅')||msg.includes('disabled')?'rgba(21,128,61,0.08)':'rgba(185,28,28,0.07)',
      color:msg.startsWith('✅')||msg.includes('disabled')?'#15803d':'#b91c1c',
      border:'1px solid '+(msg.startsWith('✅')||msg.includes('disabled')?'rgba(21,128,61,0.2)':'rgba(185,28,28,0.2)')
    }}>${msg}</div>`:null}

    ${!status&&!setup?html`
      <div style=${{display:'flex',gap:10,alignItems:'center'}}>
        <button class="btn bp" style=${{fontSize:13,padding:'8px 18px'}} onClick=${startSetup} disabled=${loading}>
          ${loading?html`<span class="spin"></span>`:null} ${loading?'Generating…':'Set Up Authenticator App'}
        </button>
        <span style=${{fontSize:11,color:'var(--tx3)'}}>Works with Google Authenticator, Authy, 1Password</span>
      </div>`:null}

    ${setup?html`
      <div style=${{display:'flex',gap:24,flexWrap:'wrap'}}>
        <!-- QR Code -->
        <div style=${{flexShrink:0}}>
          <div style=${{fontSize:12,fontWeight:600,color:'var(--tx2)',marginBottom:8}}>Step 1 — Scan QR code</div>
          <div style=${{width:164,height:164,border:'1px solid var(--bd)',borderRadius:10,overflow:'hidden',background:'#fff',display:'flex',alignItems:'center',justifyContent:'center'}}>
            <img src=${qrUrl} width="160" height="160" alt="QR code" style=${{display:'block'}}
              onError=${e=>{e.target.style.display='none';e.target.nextSibling.style.display='flex';}}/>
            <div style=${{display:'none',flexDirection:'column',alignItems:'center',padding:12,textAlign:'center'}}>
              <div style=${{fontSize:11,color:'var(--tx3)',marginBottom:6}}>QR unavailable</div>
              <div style=${{fontSize:10,color:'var(--tx3)'}}>Use manual key below</div>
            </div>
          </div>
          <div style=${{marginTop:8}}>
            <div style=${{fontSize:10,color:'var(--tx3)',marginBottom:4}}>Or enter key manually:</div>
            <div style=${{display:'flex',gap:4,alignItems:'center'}}>
              <div style=${{fontSize:11,fontFamily:'monospace',background:'var(--sf2)',padding:'5px 8px',borderRadius:6,border:'1px solid var(--bd)',flex:1,overflow:'hidden',textOverflow:'ellipsis',wordBreak:'break-all',color:'var(--tx)',letterSpacing:'.04em'}}>${(setup.secret||'').match(/.{1,4}/g)?.join(' ')}</div>
              <button class="btn bg" style=${{fontSize:10,padding:'5px 8px',flexShrink:0}} onClick=${copySecret}>${copied?'✓':'Copy'}</button>
            </div>
          </div>
        </div>

        <!-- Verify code -->
        <div style=${{flex:1,minWidth:220}}>
          <div style=${{fontSize:12,fontWeight:600,color:'var(--tx2)',marginBottom:8}}>Step 2 — Enter the 6-digit code</div>
          <div style=${{fontSize:11,color:'var(--tx3)',marginBottom:12,lineHeight:1.5}}>Open your authenticator app, find VEWIT, and enter the 6-digit code shown.</div>
          <div style=${{display:'flex',gap:6,marginBottom:14}}>
            ${[0,1,2,3,4,5].map(i=>html`
              <input key=${i} ref=${codeRefs[i]} type="text" inputMode="numeric"
                maxLength="1" value=${code[i]||''}
                onInput=${e=>handleDigit(i,e.target.value)}
                onKeyDown=${e=>handleKey(i,e)}
                onPaste=${i===0?handlePaste:undefined}
                style=${{width:40,height:48,textAlign:'center',fontSize:22,fontWeight:700,fontFamily:'monospace',
                  border:'2px solid '+(code[i]?'var(--ac)':'var(--bd)'),borderRadius:9,background:'var(--sf2)',color:'var(--tx)',
                  outline:'none',transition:'border-color .15s'}}/>`)}
          </div>
          <div style=${{display:'flex',gap:8}}>
            <button class="btn bp" style=${{fontSize:13,padding:'9px 20px'}} onClick=${verify} disabled=${loading||code.replace(/\D/g,'').length<6}>
              ${loading?html`<span class="spin"></span>`:null} ${loading?'Verifying…':'Verify & Enable 2FA'}
            </button>
            <button class="btn bg" style=${{fontSize:12}} onClick=${()=>{setSetup(null);setCode('');}}>Cancel</button>
          </div>
          <div style=${{marginTop:14,padding:'10px 12px',background:'rgba(217,119,6,0.06)',border:'1px solid rgba(217,119,6,0.2)',borderRadius:8}}>
            <div style=${{fontSize:11,fontWeight:700,color:'#b45309',marginBottom:5}}>⚠️ Save your backup codes</div>
            <div style=${{fontSize:11,color:'var(--tx3)',lineHeight:1.6,fontFamily:'monospace',wordBreak:'break-all'}}>
              ${(setup.backup_codes||[]).join(' · ')}
            </div>
            <div style=${{fontSize:10,color:'var(--tx3)',marginTop:4}}>Store these somewhere safe. Each code can only be used once if you lose your phone.</div>
          </div>
        </div>
      </div>`:null}

    ${status?html`
      <div style=${{display:'flex',alignItems:'center',gap:12}}>
        <div style=${{fontSize:13,color:'var(--tx2)'}}>Your account is protected with 2FA.</div>
        <button class="btn br" style=${{fontSize:12,marginLeft:'auto'}} onClick=${disable}>Disable 2FA</button>
      </div>`:null}
  </div>`;
}

/* ─── Goals & OKRs (re-enabled with role gating) ────────────────────────── */



/* ─── Onboarding Checklist ───────────────────────────────────────────────── */
function OnboardingChecklist({cu,projects,users,tasks,setView,onDismiss}){
  const [wsData,setWsData]=useState(null);
  const [totpOn,setTotpOn]=useState(false);
  const [fetched,setFetched]=useState(false);
  useEffect(()=>{
    Promise.all([
      api.get('/api/workspace'),
      api.get('/api/totp/status')
    ]).then(([ws,totp])=>{
      setWsData(ws);
      setTotpOn(totp?.enabled||false);
      setFetched(true);
    }).catch(()=>setFetched(true));
  },[]);
  if(!fetched) return null; // don't flash while loading
  const hasAiKey=wsData&&wsData.ai_api_key&&wsData.ai_api_key.length>0;
  const hasTasks=tasks&&tasks.length>0;
  const steps=[
    {id:'project',label:'Create your first project',done:projects&&projects.length>0,action:()=>setView('projects'),btn:'Create Project'},
    {id:'task',label:'Add your first task',done:hasTasks,action:()=>setView('tasks'),btn:'Go to Board'},
    {id:'invite',label:'Invite a team member',done:users&&users.length>1,action:()=>setView('team'),btn:'Invite Team'},
    {id:'aikey',label:'Add your Anthropic AI key',done:!!hasAiKey,action:()=>setView('settings'),btn:'Open Settings'},
    {id:'2fa',label:'Enable 2FA for your account',done:totpOn,action:()=>setView('settings'),btn:'Setup 2FA'},
  ];
  const done=steps.filter(s=>s.done).length;
  const pct=Math.round((done/steps.length)*100);
  // Auto-dismiss when all done
  useEffect(()=>{if(fetched&&done===steps.length){onDismiss&&onDismiss();}},[fetched,done]);
  if(done===steps.length) return null;
  return html`
    <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'16px 18px',marginBottom:14}}>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10}}>
        <div style=${{fontWeight:700,fontSize:13,color:'var(--tx)'}}>🚀 Get started with VEWIT</div>
        <div style=${{display:'flex',alignItems:'center',gap:10}}>
          <span style=${{fontSize:11,color:'var(--tx3)'}}>${done}/${steps.length} done</span>
          <button onClick=${onDismiss} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:14,padding:'0 4px'}}>✕</button>
        </div>
      </div>
      <div style=${{height:4,background:'var(--sf2)',borderRadius:99,marginBottom:12}}>
        <div style=${{height:4,width:pct+'%',background:'var(--ac)',borderRadius:99,transition:'width .4s'}}></div>
      </div>
      <div style=${{display:'flex',flexDirection:'column',gap:7}}>
        ${steps.map(s=>html`
          <div key=${s.id} style=${{display:'flex',alignItems:'center',gap:10,padding:'7px 10px',borderRadius:8,background:s.done?'rgba(21,128,61,0.06)':'var(--sf2)',border:'1px solid '+(s.done?'rgba(21,128,61,0.2)':'var(--bd)')}}>
            <div style=${{width:18,height:18,borderRadius:'50%',border:'2px solid '+(s.done?'#15803d':'var(--tx3)'),background:s.done?'#15803d':'transparent',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}}>
              ${s.done?html`<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3"><polyline points="20 6 9 17 4 12"/></svg>`:null}
            </div>
            <span style=${{flex:1,fontSize:12,fontWeight:500,color:s.done?'var(--tx3)':'var(--tx)',textDecoration:s.done?'line-through':'none'}}>${s.label}</span>
            ${!s.done?html`<button class="btn bp" style=${{fontSize:11,padding:'3px 10px',height:24}} onClick=${s.action}>${s.btn}</button>`:null}
          </div>`)}
      </div>
    </div>`;
}



/* ─── @Mentions autocomplete ─────────────────────────────────────────────── */
function MentionInput({value,onChange,onKeyDown,users,placeholder,style,cu}){
  const [show,setShow]=useState(false);
  const [query,setQuery]=useState('');
  const [filtered,setFiltered]=useState([]);
  const [selIdx,setSelIdx]=useState(0);
  const ref=useRef(null);

  const handleInput=e=>{
    const v=e.target.value;
    onChange(v);
    const at=v.lastIndexOf('@');
    if(at>=0&&(at===0||v[at-1]===' ')){
      const q=v.slice(at+1);
      if(!q.includes(' ')){
        const f=safe(users||[]).filter(u=>u.name.toLowerCase().includes(q.toLowerCase())&&u.id!==cu?.id);
        setFiltered(f.slice(0,6));setQuery(q);setSelIdx(0);
        setShow(f.length>0);return;
      }
    }
    setShow(false);
  };

  const pick=(u)=>{
    const v=value;
    const at=v.lastIndexOf('@');
    const newVal=v.slice(0,at)+'@'+u.name+' ';
    onChange(newVal);setShow(false);
    ref.current?.focus();
  };

  const handleKey=e=>{
    if(show){
      if(e.key==='ArrowDown'){e.preventDefault();setSelIdx(i=>Math.min(i+1,filtered.length-1));}
      else if(e.key==='ArrowUp'){e.preventDefault();setSelIdx(i=>Math.max(i-1,0));}
      else if(e.key==='Enter'&&filtered[selIdx]){e.preventDefault();pick(filtered[selIdx]);return;}
      else if(e.key==='Escape'){setShow(false);}
    }
    onKeyDown&&onKeyDown(e);
  };

  return html`<div style=${{position:'relative',flex:1}}>
    <input ref=${ref} class="inp" value=${value} placeholder=${placeholder||'Message… (@ to mention)'}
      onInput=${handleInput} onKeyDown=${handleKey} style=${style||{}}/>
    ${show?html`
      <div style=${{position:'absolute',bottom:'100%',left:0,right:0,background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:10,boxShadow:'0 8px 32px rgba(0,0,0,.18)',zIndex:200,overflow:'hidden',marginBottom:4}}>
        ${filtered.map((u,i)=>html`
          <div key=${u.id} onMouseDown=${()=>pick(u)}
            style=${{display:'flex',alignItems:'center',gap:8,padding:'8px 12px',cursor:'pointer',background:i===selIdx?'var(--ac3)':'transparent'}}>
            <div style=${{width:24,height:24,borderRadius:'50%',background:'var(--ac)',color:'#fff',fontSize:10,fontWeight:700,display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}}>${u.name.slice(0,2).toUpperCase()}</div>
            <div>
              <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)'}}>${u.name}</div>
              <div style=${{fontSize:10,color:'var(--tx3)'}}>${u.role}</div>
            </div>
          </div>`)}
      </div>`:null}
  </div>`;
}

/* ─── Pinned Messages Panel ──────────────────────────────────────────────── */
/* ─── Task Templates Panel ───────────────────────────────────────────────── */
/* ─── AIAssistant floating panel ──────────────────────────────────────────── */
function AIAssistant({cu,projects,tasks,users}){
  const [open,setOpen]=useState(false);const [msgs,setMsgs]=useState([]);const [input,setInput]=useState('');const [busy,setBusy]=useState(false);const ref=useRef(null);const iref=useRef(null);

  useEffect(()=>{if(ref.current)ref.current.scrollTop=ref.current.scrollHeight;},[msgs]);

  const QUICK=[
    {label:'📊 EOD Report',msg:'Generate an end-of-day status report for all projects'}, {label:'🔴 Blocked tasks',msg:'What tasks are blocked and need attention?'}, {label:'📈 Progress summary',msg:'Give me a quick summary of overall project progress'}, {label:'⚠️ Overdue',msg:'Are there any overdue tasks?'}, ];

  const send=async(text)=>{
    const m=text||input.trim();
    if(!m||busy)return;
    setInput('');
    const userMsg={role:'user',content:m};
    setMsgs(prev=>[...prev,userMsg]);
    setBusy(true);
    const history=[...msgs,userMsg];
    const r=await api.post('/api/ai/chat',{message:m,history:history.slice(-10)});
    setBusy(false);
    if(r.error&&r.error==='NO_KEY'){
      setMsgs(prev=>[...prev,{role:'ai',content:'⚙️ No API key configured.\n\nGo to **Settings → AI Assistant** and paste your Anthropic API key to get started.',actions:[]}]);
    } else if(r.error){
      setMsgs(prev=>[...prev,{role:'ai',content:'Error: '+(r.message||r.error),actions:[]}]);
    } else {
      setMsgs(prev=>[...prev,{role:'ai',content:r.message||'',actions:r.actions||[]}]);
    }
  };

  const actionLabel=a=>{
    if(a.type==='create_task')return'✅ Created task: '+a.title+' ('+a.id+')';
    if(a.type==='update_task')return'✏️ Updated task: '+a.id;
    if(a.type==='create_project')return'📁 Created project: '+a.name;
    if(a.type==='eod_report')return'📊 EOD Report generated';
    if(a.type==='error')return'⚠️ Error: '+a.message;
    return'✓ '+a.type;
  };

  return html`
    <button class="ai-btn" onClick=${()=>setOpen(!open)} title="AI Assistant">
      ${open?'✕':'🤖'}
    </button>
    ${open?html`
      <div class="ai-panel">
        <div style=${{padding:'14px 16px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:10,flexShrink:0}}>
          <div style=${{width:32,height:32,background:'#2563eb',borderRadius:9,display:'flex',alignItems:'center',justifyContent:'center',fontSize:16,boxShadow:'0 2px 8px rgba(37,99,235,0.3)'}}>🤖</div>
          <div style=${{flex:1}}>
            <div style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}>AI Assistant</div>
            <div style=${{fontSize:10,color:'var(--tx3)'}}>Powered by Claude</div>
          </div>
          ${msgs.length>0?html`<button class="btn bg" style=${{fontSize:10,padding:'4px 9px'}} onClick=${()=>setMsgs([])}>Clear</button>`:null}
        </div>

        <div ref=${ref} style=${{flex:1,overflowY:'auto',padding:'12px',display:'flex',flexDirection:'column',gap:10}}>
          ${msgs.length===0?html`
            <div style=${{paddingTop:8}}>
              <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:12,textAlign:'center'}}>Ask me anything about your projects, or try a quick action:</p>
              <div style=${{display:'flex',flexDirection:'column',gap:6}}>
                ${QUICK.map(q=>html`<button key=${q.label} class="btn bg" style=${{justifyContent:'flex-start',fontSize:12,padding:'8px 12px',textAlign:'left'}} onClick=${()=>send(q.msg)}>${q.label}</button>`)}
              </div>
            </div>`:null}
          ${msgs.map((m,i)=>html`
            <div key=${i}>
              ${m.role==='user'?html`<div class="ai-msg-user">${m.content}</div>`:null}
              ${m.role==='ai'?html`
                <div class="ai-msg-ai">${m.content}</div>
                ${(m.actions||[]).length>0?html`<div style=${{display:'flex',flexDirection:'column',gap:5,marginTop:6}}>
                  ${(m.actions||[]).map((a,j)=>html`<div key=${j} class="ai-action">${actionLabel(a)}${a.type==='eod_report'&&a.summary?html`<pre style=${{marginTop:6,fontSize:10,whiteSpace:'pre-wrap',color:'var(--gn)',lineHeight:1.6}}>${a.summary}</pre>`:null}</div>`)}
                </div>`:null}`:null}
            </div>`)}
          ${busy?html`<div class="ai-msg-ai pulse" style=${{display:'flex',gap:4,alignItems:'center'}}><span style=${{fontSize:16}}>🤖</span><span style=${{fontSize:12}}>Thinking...</span><span class="spin" style=${{width:12,height:12,borderWidth:2}}></span></div>`:null}
        </div>

        <div style=${{padding:'10px 12px',borderTop:'1px solid var(--bd)',flexShrink:0}}>
          <div style=${{display:'flex',gap:7}}>
            <input ref=${iref} class="inp" style=${{flex:1,fontSize:13}} placeholder="Ask about your projects..." value=${input}
              onInput=${e=>setInput(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&!e.shiftKey&&send()}
              disabled=${busy}/>
            <button class="btn bp" style=${{padding:'8px 12px',flexShrink:0}} onClick=${()=>send()} disabled=${!input.trim()||busy}>➤</button>
          </div>
        </div>
      </div>`:null}`;
}

/* ─── Browser Notifications & Badge ──────────────────────────────────────── */
const NOTIF_ICON="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%232563eb'/%3E%3Ccircle cx='32' cy='32' r='9' fill='white'/%3E%3Ccircle cx='32' cy='11' r='6' fill='white' opacity='.95'/%3E%3Ccircle cx='51' cy='43' r='6' fill='white' opacity='.95'/%3E%3Ccircle cx='13' cy='43' r='6' fill='white' opacity='.95'/%3E%3Cline x1='32' y1='17' x2='32' y2='23' stroke='white' stroke-width='3.5' stroke-linecap='round'/%3E%3Cline x1='46' y1='40' x2='40' y2='36' stroke='white' stroke-width='3.5' stroke-linecap='round'/%3E%3Cline x1='18' y1='40' x2='24' y2='36' stroke='white' stroke-width='3.5' stroke-linecap='round'/%3E%3C/svg%3E";

function updateBadge(count){
  try{
    if(navigator.setAppBadge){
      if(count>0)navigator.setAppBadge(count);
      else navigator.clearAppBadge();
    }
  }catch(e){}
  try{
    const canvas=document.createElement('canvas');
    canvas.width=32;canvas.height=32;
    const ctx=canvas.getContext('2d');
    const img=new Image();
    img.onload=()=>{
      ctx.drawImage(img,0,0,32,32);
      if(count>0){
        ctx.fillStyle='#ef4444';
        ctx.beginPath();ctx.arc(24,8,9,0,2*Math.PI);ctx.fill();
        ctx.fillStyle='#fff';ctx.font='bold 10px Inter,sans-serif';
        ctx.textAlign='center';ctx.textBaseline='middle';
        ctx.fillText(count>9?'9+':String(count),24,8);
      }
      const links=document.querySelectorAll("link[rel*='icon']");
      links.forEach(l=>{l.href=canvas.toDataURL();});
      document.title=count>0?'('+count+') VEWIT':'VEWIT';
    };
    img.src=NOTIF_ICON;
  }catch(e){}
}

async function requestNotifPermission(){
  if(window.__TAURI__){
    try{
      const {isPermissionGranted,requestPermission,sendNotification}=window.__TAURI__.notification;
      let ok=await isPermissionGranted();
      if(!ok){const p=await requestPermission();ok=(p==='granted');}
      if(ok)await sendNotification({title:'VEWIT',body:'Notifications enabled.'});
      return;
    }catch(e){}
  }
  if('Notification' in window&&Notification.permission==='default'){
    const p=await Notification.requestPermission();
    if(p==='granted'){
      if(window._pfSWReg){
        try{
          const r=await fetch('/api/push/vapid-key',{credentials:'include'});
          const d=await r.json();
          if(d.publicKey){
            const padding='='.repeat((4-d.publicKey.length%4)%4);
            const base64=(d.publicKey+padding).replace(/-/g,'+').replace(/_/g,'/');
            const raw=window.atob(base64);
            const key=new Uint8Array(raw.length);
            for(let i=0;i<raw.length;i++) key[i]=raw.charCodeAt(i);
            const sub=await window._pfSWReg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
            window._pfPushSub=sub;
            const sj=sub.toJSON();
            fetch('/api/push/subscribe',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint:sj.endpoint,keys:sj.keys})}).catch(()=>{});
          }
        }catch(e){}
      }
      new Notification('VEWIT',{body:'Desktop notifications enabled! You\'ll be notified for tasks, projects & reminders.',icon:NOTIF_ICON,silent:true});
    }
  }
}

async function showBrowserNotif(title,body,onClick,opts={}){
  const tag=opts.tag||'pf-'+Date.now();
  if(onClick){window._pfNotifHandlers=window._pfNotifHandlers||{};window._pfNotifHandlers[tag]=onClick;}
  if(window.__TAURI__){
    try{
      const {isPermissionGranted,requestPermission,sendNotification}=window.__TAURI__.notification;
      let ok=await isPermissionGranted();
      if(!ok){const p=await requestPermission();ok=(p==='granted');}
      if(ok){await sendNotification({title,body});return;}
    }catch(e){}
  }
  if(!('Notification' in window)||Notification.permission!=='granted')return;
  if(window._pfSWReg){
    try{
      await window._pfSWReg.showNotification(title,{body,icon:NOTIF_ICON,badge:NOTIF_ICON,tag,vibrate:[200,100,200],requireInteraction:opts.requireInteraction||false,data:{tag}});
      return;
    }catch(e){}
  }
  try{
    const n=new Notification(title,{body,icon:NOTIF_ICON,badge:NOTIF_ICON,tag,requireInteraction:opts.requireInteraction||false,silent:false});
    if(onClick)n.onclick=()=>{window.focus();onClick();n.close();};
    if(!opts.requireInteraction)setTimeout(()=>n.close(),6000);
  }catch(e){}
}

/* ─── In-App Toast System ─────────────────────────────────────────────────── */
window._pfToast=window._pfToast||null; // will be set to addToast fn after mount

const TOAST_CFG={
  dm:      {icon:'💬', color:'var(--ac)', bg:'var(--ac3)', nav:'dm'}, call:    {icon:'📞', color:'var(--gn)', bg:'rgba(62,207,110,.12)', nav:'dashboard'}, task_assigned:{icon:'✅',color:'var(--cy)', bg:'rgba(34,211,238,.1)', nav:'tasks'}, status_change:{icon:'🔄',color:'var(--pu)', bg:'rgba(167,139,250,.1)',nav:'tasks'}, comment: {icon:'💬', color:'var(--pu)', bg:'rgba(167,139,250,.1)', nav:'tasks'}, deadline:{icon:'⏰', color:'var(--am)', bg:'rgba(245,158,11,.1)', nav:'tasks'}, project_added:{icon:'📁',color:'var(--or)',bg:'rgba(251,146,60,.1)',nav:'projects'}, reminder:{icon:'⏰', color:'var(--rd)', bg:'rgba(255,68,68,.1)', nav:'reminders'}, message: {icon:'#️⃣', color:'#a78bfa', bg:'rgba(167,139,250,.1)', nav:'messages'}, default: {icon:'🔔', color:'var(--ac)', bg:'var(--ac3)', nav:'notifs'},
};

function ToastStack({toasts,onDismiss,onNav}){
  return html`
    <div class="toast-stack">
      ${toasts.map(t=>{
        const cfg=TOAST_CFG[t.type]||TOAST_CFG.default;
        return html`
          <div key=${t.id} class=${'toast'+(t.leaving?' leaving':'')}
            onClick=${()=>{onDismiss(t.id);onNav&&onNav(cfg.nav);}}>
            <div class="toast-bar" style=${{width:t.progress+'%',background:cfg.color}}></div>
            <div class="toast-icon" style=${{background:cfg.bg,color:cfg.color}}>${cfg.icon}</div>
            <div class="toast-body">
              <div class="toast-title">${t.title}</div>
              <div class="toast-msg">${t.body}</div>
              <div class="toast-time">${t.timeStr}</div>
            </div>
            <button class="toast-close" onClick=${e=>{e.stopPropagation();onDismiss(t.id);}}>✕</button>
          </div>`;
      })}
    </div>`;
}

/* ─── ReminderModal ───────────────────────────────────────────────────────── */
function ReminderModal({task,onClose,onSaved}){
  const [remindAt,setRemindAt]=useState('');
  const [minBefore,setMinBefore]=useState('10');
  const [saving,setSaving]=useState(false);
  const [err,setErr]=useState('');

  useEffect(()=>{
    if(task&&task.due){
      try{
        const d=new Date(task.due);
        if(!isNaN(d)){
          d.setHours(9,0,0,0);
          setRemindAt(d.toISOString().slice(0,16));
        }
      }catch(e){}
    } else {
      const d=new Date();d.setHours(d.getHours()+1,0,0,0);
      setRemindAt(d.toISOString().slice(0,16));
    }
  },[task]);

  const save=async()=>{
    if(!remindAt){setErr('Please set a reminder date and time.');return;}
    const remindUtc=new Date(remindAt);
    const alertAt=new Date(remindUtc.getTime()-parseInt(minBefore)*60000);
    setSaving(true);
    const r=await api.post('/api/reminders',{
      task_id:task?task.id:'', task_title:task?task.title:'Reminder', remind_at:alertAt.toISOString(), minutes_before:parseInt(minBefore), });
    setSaving(false);
    if(r.error){setErr(r.error);return;}
    playSound('reminder');onSaved&&onSaved(r);
    onClose();
  };

  return html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&onClose()}>
      <div class="mo" style=${{maxWidth:420}}>
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:18}}>
          <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)'}}>⏰ Set Reminder</h2>
          <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${onClose}>✕</button>
        </div>
        ${task?html`<div style=${{padding:'10px 13px',background:'var(--sf2)',borderRadius:9,border:'1px solid var(--bd)',marginBottom:16,fontSize:13,color:'var(--tx2)'}}>
          Task: <b style=${{color:'var(--tx)'}}>${task.title}</b>
        </div>`:null}
        <div style=${{display:'grid',gap:14}}>
          <div>
            <label class="lbl">Remind me at (date & time)</label>
            <input class="inp" type="datetime-local" value=${remindAt}
              onChange=${e=>setRemindAt(e.target.value)}/>
          </div>
          <div>
            <label class="lbl">Notify me how early?</label>
            <select class="inp" value=${minBefore} onChange=${e=>setMinBefore(e.target.value)}>
              <option value="5">5 minutes before</option>
              <option value="10">10 minutes before</option>
              <option value="15">15 minutes before</option>
              <option value="30">30 minutes before</option>
              <option value="60">1 hour before</option>
              <option value="0">At exact time</option>
            </select>
          </div>
        </div>
        ${err?html`<p style=${{color:'var(--rd)',fontSize:12,marginTop:10}}>${err}</p>`:null}
        <div style=${{display:'flex',gap:9,justifyContent:'flex-end',marginTop:18}}>
          <button class="btn bg" onClick=${onClose}>Cancel</button>
          <button class="btn bp" onClick=${save} disabled=${saving}>
            ${saving?html`<span class="spin"></span>`:'⏰ Set Reminder'}
          </button>
        </div>
      </div>
    </div>`;
}

/* ─── RemindersView ──────────────────────────────────────────────────────── */
function RemindersView({cu,tasks,projects,onSetReminder,onReload,initialView}){
  const [reminders,setReminders]=useState([]);
  const [busy,setBusy]=useState(true);
  const [showAdd,setShowAdd]=useState(false);
  const [addTaskId,setAddTaskId]=useState('');
  const [addCustomTitle,setAddCustomTitle]=useState('');
  const [addDate,setAddDate]=useState('');
  const [addTime,setAddTime]=useState('');
  const [addMins,setAddMins]=useState(10);
  const [saving,setSaving]=useState(false);
  const [addProjId,setAddProjId]=useState('');
  const [showCompleted,setShowCompleted]=useState(false);
  const [editReminder,setEditReminder]=useState(null);
  const [editDate,setEditDate]=useState('');
  const [editTime,setEditTime]=useState('');
  const [editMins,setEditMins]=useState(10);
  const now=new Date();
  const filteredTasks=addProjId?safe(tasks).filter(t=>t.project===addProjId):safe(tasks);

  const load=useCallback(async()=>{
    setBusy(true);
    const d=await api.get('/api/reminders?include_fired=1');
    setReminders(Array.isArray(d)?d:[]);
    setBusy(false);
  },[]);

  useEffect(()=>{load();},[load]);

  const del=async id=>{await api.del('/api/reminders/'+id);load();onReload&&onReload();};

  const openEdit=(r)=>{
    setEditReminder(r);
    const d=new Date(r.remind_at);
    const pad=n=>String(n).padStart(2,'0');
    setEditDate(d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate()));
    setEditTime(pad(d.getHours())+':'+pad(d.getMinutes()));
    setEditMins(r.minutes_before||10);
  };

  const saveEdit=async()=>{
    if(!editDate||!editTime)return;
    setSaving(true);
    const dt=new Date(editDate+'T'+editTime);
    await api.put('/api/reminders/'+editReminder.id,{remind_at:dt.toISOString(),minutes_before:editMins,task_title:editReminder.task_title});
    setSaving(false);setEditReminder(null);load();onReload&&onReload();
  };

  const saveReminder=async()=>{
    const realTaskId=(addTaskId&&addTaskId!=='__custom__')?addTaskId:'';
    const titleToUse=realTaskId
      ?(safe(tasks).find(t=>t.id===realTaskId)||{title:addCustomTitle.trim()||'Reminder'}).title
      :(addCustomTitle.trim()||'Reminder');
    if(!titleToUse||!addDate||!addTime)return;
    setSaving(true);
    const dt=new Date(addDate+'T'+addTime);
    await api.post('/api/reminders',{task_id:realTaskId,task_title:titleToUse,remind_at:dt.toISOString(),minutes_before:addMins});
    setSaving(false);
    setShowAdd(false);
    setAddTaskId('');setAddCustomTitle('');setAddDate('');setAddTime('');setAddMins(10);
    load();
  };

  const active=reminders.filter(r=>!r.fired);
  const completed=reminders.filter(r=>r.fired);
  const upcoming=active.filter(r=>new Date(r.remind_at)>=now).sort((a,b)=>new Date(a.remind_at)-new Date(b.remind_at));
  const overdue=active.filter(r=>new Date(r.remind_at)<now).sort((a,b)=>new Date(b.remind_at)-new Date(a.remind_at));

  const fmtRem=dt=>{
    const d=new Date(dt);
    const diff=d-now;
    if(diff<0)return{label:'Overdue',cls:'var(--rd)',bg:'rgba(248,113,113,.12)'};
    if(diff<3600000)return{label:'< 1 hr',cls:'var(--am)',bg:'rgba(251,191,36,.12)'};
    if(diff<86400000)return{label:'Today',cls:'var(--cy)',bg:'rgba(34,211,238,.12)'};
    if(diff<172800000)return{label:'Tomorrow',cls:'var(--gn)',bg:'rgba(74,222,128,.12)'};
    return{label:d.toLocaleDateString('en-US',{month:'short',day:'numeric'}),cls:'var(--tx2)',bg:'var(--sf2)'};
  };

  const statCards=[
    {label:'Upcoming',val:upcoming.length,color:'var(--cy)',bg:'rgba(34,211,238,.1)',icon:'⚡'}, {label:'Overdue',val:overdue.length,color:'var(--rd)',bg:'rgba(248,113,113,.1)',icon:'🚨'}, {label:'Completed',val:completed.length,color:'var(--gn)',bg:'rgba(74,222,128,.1)',icon:'✅'}, {label:'Today',val:active.filter(r=>{const d=new Date(r.remind_at);return d.toDateString()===now.toDateString();}).length,color:'#1d4ed8',bg:'rgba(29,78,216,0.10)',icon:'📅'}, ];

  return html`
    <div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px',background:'var(--bg)'}}>

      <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
        <div style=${{fontSize:13,color:'var(--tx2)'}}>Set reminders for your tasks — get notified with sound before they're due.</div>
        <div style=${{display:'flex',gap:8}}>
          <button class=${'btn '+(showCompleted?'bp':'bg')} style=${{fontSize:12}} onClick=${()=>setShowCompleted(p=>!p)}>
            ${showCompleted?'Hide Completed':'Show Completed ('+completed.length+')'}
          </button>
          <button class="btn bp" style=${{fontSize:12}} onClick=${()=>setShowAdd(true)}>+ Add Reminder</button>
        </div>
      </div>

      <div style=${{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:12,marginBottom:18}}>
        ${statCards.map(s=>{
          return html`
            <div key=${s.label} style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'14px 16px',display:'flex',alignItems:'center',gap:12}}>
              <div style=${{width:40,height:40,borderRadius:10,background:s.bg,display:'flex',alignItems:'center',justifyContent:'center',fontSize:18}}>${s.icon}</div>
              <div>
                <div style=${{fontSize:24,fontWeight:900,color:s.color,lineHeight:1}}>${s.val}</div>
                <div style=${{fontSize:11,color:'var(--tx2)',marginTop:2,fontWeight:500,fontWeight:600}}>${s.label}</div>
              </div>
            </div>`;
        })}
      </div>

      ${showAdd?html`
        <div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowAdd(false)}>
          <div class="mo fi" style=${{maxWidth:600}}>
                        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:20}}>
              <div>
                <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)',display:'flex',alignItems:'center',gap:8}}>
                  <span style=${{width:32,height:32,borderRadius:9,background:'rgba(251,191,36,.15)',border:'1px solid rgba(251,191,36,.3)',display:'inline-flex',alignItems:'center',justifyContent:'center',fontSize:16}}>⏰</span>
                  Add Reminder
                </h2>
                <p style=${{fontSize:11,color:'var(--tx3)',marginTop:3,marginLeft:40}}>Fill in one or both sections, then pick a date &amp; time.</p>
              </div>
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>{setShowAdd(false);setAddCustomTitle('');setAddTaskId('');}}>✕</button>
            </div>

                        <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14,marginBottom:16}}>

                            <div style=${{background:'var(--sf2)',borderRadius:14,padding:'16px',border:'2px solid '+(addTaskId&&addTaskId!=='__custom__'?'var(--ac)':'var(--bd)'),transition:'border-color .15s',position:'relative',overflow:'hidden'}}>
                <div style=${{position:'absolute',top:0,left:0,right:0,height:3,background:'linear-gradient(90deg,var(--ac),var(--cy))',borderRadius:'14px 14px 0 0',opacity:addTaskId&&addTaskId!=='__custom__'?1:.3,transition:'opacity .15s'}}></div>
                <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:14}}>
                  <div style=${{width:28,height:28,borderRadius:7,background:'rgba(170,255,0,.12)',border:'1px solid rgba(170,255,0,.25)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14}}>📋</div>
                  <div>
                    <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)'}}>Project Reminder</div>
                    <div style=${{fontSize:10,color:'var(--tx3)'}}>Linked to a task</div>
                  </div>
                </div>
                <div style=${{display:'flex',flexDirection:'column',gap:10}}>
                  <div>
                    <label class="lbl">Project <span style=${{color:'var(--tx3)',fontWeight:400,textTransform:'none',fontSize:9}}>(filter tasks)</span></label>
                    <select class="inp" style=${{fontSize:12}} value=${addProjId} onChange=${e=>{setAddProjId(e.target.value);setAddTaskId('');}}>
                      <option value="">— All projects —</option>
                      ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
                    </select>
                  </div>
                  <div>
                    <label class="lbl">Task <span style=${{color:'var(--tx3)',fontWeight:400,textTransform:'none',fontSize:9}}>(optional)</span></label>
                    <select class="inp" style=${{fontSize:12}} value=${addTaskId==='__custom__'?'':addTaskId} onChange=${e=>{setAddTaskId(e.target.value);if(e.target.value)setAddCustomTitle('');}}>
                      <option value="">— Select a task (or set custom below) —</option>
                      ${filteredTasks.map(t=>html`<option key=${t.id} value=${t.id}>${t.title}</option>`)}
                    </select>
                  </div>
                  ${addTaskId&&addTaskId!=='__custom__'?html`
                    <div style=${{padding:'7px 10px',background:'rgba(170,255,0,.07)',borderRadius:8,border:'1px solid rgba(170,255,0,.18)',fontSize:11,color:'var(--tx2)',display:'flex',alignItems:'center',gap:6}}>
                      <span style=${{color:'var(--ac)'}}>✓</span>
                      <span>Linked: <b style=${{color:'var(--tx)'}}>${(safe(tasks).find(t=>t.id===addTaskId)||{title:''}).title}</b></span>
                    </div>`:null}
                </div>
              </div>

                            <div style=${{background:'var(--sf2)',borderRadius:14,padding:'16px',border:'2px solid '+(addTaskId==='__custom__'||(!addTaskId&&addCustomTitle.trim())?'var(--pu)':'var(--bd)'),transition:'border-color .15s',position:'relative',overflow:'hidden'}}>
                <div style=${{position:'absolute',top:0,left:0,right:0,height:3,background:'linear-gradient(90deg,var(--pu),var(--pk))',borderRadius:'14px 14px 0 0',opacity:addTaskId==='__custom__'||(!addTaskId&&addCustomTitle.trim())?1:.3,transition:'opacity .15s'}}></div>
                <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:14}}>
                  <div style=${{width:28,height:28,borderRadius:7,background:'rgba(167,139,250,.12)',border:'1px solid rgba(167,139,250,.25)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14}}>✏️</div>
                  <div>
                    <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)'}}>Custom Reminder</div>
                    <div style=${{fontSize:10,color:'var(--tx3)'}}>Standalone note or meeting</div>
                  </div>
                </div>
                <div>
                  <label class="lbl">Reminder Title <span style=${{color:'var(--rd)',fontWeight:600}}>*</span></label>
                  <input class="inp" style=${{fontSize:12}} value=${addCustomTitle}
                    onInput=${e=>{setAddCustomTitle(e.target.value);if(e.target.value)setAddTaskId('__custom__');else if(addTaskId==='__custom__')setAddTaskId('');}}
                    placeholder="e.g. Team standup, Review designs, Call client…"/>
                </div>
                <div style=${{marginTop:10,padding:'8px 10px',background:'rgba(167,139,250,.07)',borderRadius:8,border:'1px solid rgba(167,139,250,.18)',fontSize:11,color:'var(--tx3)',lineHeight:1.5}}>
                  💡 Use this for meetings, calls, or any non-task reminder.
                </div>
              </div>
            </div>

                        <div style=${{background:'var(--sf2)',borderRadius:14,padding:'16px',border:'1px solid var(--bd)',marginBottom:16}}>
              <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:14}}>
                <div style=${{width:28,height:28,borderRadius:7,background:'rgba(34,211,238,.12)',border:'1px solid rgba(34,211,238,.25)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14}}>📅</div>
                <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)'}}>Date, Time &amp; Notification</div>
              </div>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginBottom:12}}>
                <div>
                  <label class="lbl">Date <span style=${{color:'var(--rd)',fontWeight:600}}>*</span></label>
                  <input class="inp" type="date" value=${addDate} onChange=${e=>setAddDate(e.target.value)} min=${new Date().toISOString().split('T')[0]} onFocus=${e=>{if(!e.target.value)e.target.value=new Date().toISOString().split('T')[0];}}/>
                </div>
                <div>
                  <label class="lbl">Time <span style=${{color:'var(--rd)',fontWeight:600}}>*</span></label>
                  <input class="inp" type="time" value=${addTime} onChange=${e=>setAddTime(e.target.value)}/>
                </div>
              </div>
              <div>
                <label class="lbl">Notify me before</label>
                <div style=${{display:'flex',gap:6,flexWrap:'wrap',marginTop:6}}>
                  ${[5,10,15,30,60].map(m=>html`
                    <button key=${m} onClick=${()=>setAddMins(m)}
                      style=${{padding:'6px 14px',borderRadius:100,fontSize:12,fontWeight:700,border:'2px solid '+(addMins===m?'var(--ac)':'var(--bd)'),background:addMins===m?'var(--ac)':'transparent',color:addMins===m?'var(--ac-tx)':'var(--tx2)',cursor:'pointer',transition:'all .12s'}}>
                      ${m<60?m+' min':'1 hr'}
                    </button>`)}
                </div>
              </div>
              <div style=${{marginTop:12,background:'rgba(170,255,0,.06)',borderRadius:9,padding:'10px 13px',fontSize:12,color:'var(--tx2)',border:'1px solid rgba(170,255,0,.15)',display:'flex',alignItems:'center',gap:8}}>
                <span style=${{fontSize:16}}>🔔</span>
                <span>You'll get a browser notification + sound <b style=${{color:'#1d4ed8'}}>${addMins} min</b> before the reminder time.</span>
              </div>
            </div>

            <div style=${{display:'flex',gap:9,justifyContent:'flex-end'}}>
              <button class="btn bg" onClick=${()=>{setShowAdd(false);setAddCustomTitle('');setAddTaskId('');}}>Cancel</button>
              <button class="btn bp" style=${{minWidth:120}} onClick=${saveReminder}
                disabled=${saving||(!addTaskId&&!addCustomTitle.trim())||!addDate||!addTime}>
                ${saving?html`<span class="spin"></span>`:'⏰ Set Reminder'}
              </button>
            </div>
          </div>
        </div>`:null}

      <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
        <div>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
            <span style=${{fontWeight:700,fontSize:13,color:'var(--tx)'}}>⚡ Upcoming</span>
            <span class="tx3-11">${upcoming.length} reminder${upcoming.length!==1?'s':''}</span>
          </div>
          ${busy?html`<div class="spin" style=${{margin:'20px auto',display:'block'}}></div>`:null}
          ${!busy&&upcoming.length===0?html`
            <div style=${{textAlign:'center',padding:'28px 16px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:10,border:'1px solid var(--bd)'}}>
              <div style=${{fontSize:28,marginBottom:8}}>✅</div>
              <div>No upcoming reminders</div>
            </div>`:null}
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            ${upcoming.map(r=>{
              const ft=fmtRem(r.remind_at);
              return html`
                <div key=${r.id} style=${{display:'flex',gap:10,padding:'11px 13px',background:'var(--sf)',borderRadius:10,border:'1px solid var(--bd)',alignItems:'center'}}>
                  <div style=${{width:36,height:36,borderRadius:9,background:'rgba(251,191,36,.1)',border:'1px solid rgba(251,191,36,.2)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:16,flexShrink:0}}>⏰</div>
                  <div style=${{flex:1,minWidth:0}}>
                    <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginBottom:3}}>${r.task_title}</div>
                    <div style=${{display:'flex',gap:6,alignItems:'center'}}>
                      <span style=${{fontSize:10,padding:'1px 6px',borderRadius:4,background:ft.bg,color:ft.cls,fontWeight:700}}>${ft.label}</span>
                      <span class="mono-10">${new Date(r.remind_at).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</span>
                      ${r.minutes_before>0?html`<span style=${{fontSize:10,color:'var(--am)'}}>🔔 ${r.minutes_before}min before</span>`:null}
                    </div>
                  </div>
                  <button class="btn bg" title="Edit" style=${{fontSize:11,padding:'4px 8px',flexShrink:0,marginRight:4}} onClick=${()=>openEdit(r)}>✏️</button>
                  <button class="btn brd" style=${{fontSize:10,padding:'4px 8px',flexShrink:0}} onClick=${()=>del(r.id)}>✕</button>
                </div>`;
            })}
          </div>
        </div>
        <div>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
            <span style=${{fontWeight:700,fontSize:13,color:'var(--rd)'}}>🚨 Overdue</span>
            <span class="tx3-11">${overdue.length} past due</span>
          </div>
          ${!busy&&overdue.length===0?html`
            <div style=${{textAlign:'center',padding:'28px 16px',color:'var(--tx3)',fontSize:13,background:'var(--sf)',borderRadius:10,border:'1px solid var(--bd)'}}>
              <div style=${{fontSize:28,marginBottom:8}}>🎉</div>
              <div>Nothing overdue!</div>
            </div>`:null}
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            ${overdue.map(r=>html`
              <div key=${r.id} style=${{display:'flex',gap:10,padding:'11px 13px',background:'rgba(248,113,113,.03)',borderRadius:10,border:'1px solid rgba(248,113,113,.15)',alignItems:'center'}}>
                <div style=${{width:36,height:36,borderRadius:9,background:'rgba(248,113,113,.1)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:16,flexShrink:0}}>⚠️</div>
                <div style=${{flex:1,minWidth:0}}>
                  <div style=${{fontSize:12,fontWeight:700,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginBottom:3}}>${r.task_title}</div>
                  <span style=${{fontSize:10,color:'var(--rd)',fontFamily:'monospace'}}>${new Date(r.remind_at).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</span>
                </div>
                <button class="btn brd" style=${{fontSize:10,padding:'4px 8px',flexShrink:0}} onClick=${()=>del(r.id)}>✕</button>
              </div>`)}
          </div>
        </div>
      </div>

      ${showCompleted&&completed.length>0?html`
        <div style=${{marginTop:20}}>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
            <span style=${{fontWeight:700,fontSize:13,color:'var(--gn)'}}>✅ Completed Reminders</span>
            <span class="tx3-11">${completed.length} done</span>
          </div>
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            ${completed.map(r=>html`
              <div key=${r.id} style=${{display:'flex',gap:10,padding:'10px 13px',background:'rgba(74,222,128,.04)',borderRadius:10,border:'1px solid rgba(74,222,128,.15)',alignItems:'center',opacity:.75}}>
                <div style=${{width:32,height:32,borderRadius:8,background:'rgba(74,222,128,.1)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14,flexShrink:0}}>✅</div>
                <div style=${{flex:1,minWidth:0}}>
                  <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',textDecoration:'line-through',opacity:.7}}>${r.task_title}</div>
                  <span class="mono-10">${new Date(r.remind_at).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</span>
                </div>
                <button class="btn brd" style=${{fontSize:10,padding:'4px 8px',flexShrink:0}} onClick=${()=>del(r.id)}>✕</button>
              </div>`)}
          </div>
        </div>`:null}

      ${editReminder?html`
        <div class="ov" onClick=${e=>e.target===e.currentTarget&&setEditReminder(null)}>
          <div class="mo fi" style=${{maxWidth:420}}>
            <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:18}}>
              <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)'}}>✏️ Edit Reminder</h2>
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setEditReminder(null)}>✕</button>
            </div>
            <div style=${{marginBottom:12,padding:'10px 13px',background:'var(--sf2)',borderRadius:9,border:'1px solid var(--bd)'}}>
              <div style=${{fontSize:13,fontWeight:600,color:'var(--tx)'}}>${editReminder.task_title}</div>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:13}}>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div>
                  <label class="lbl">Date *</label>
                  <input class="inp" type="date" value=${editDate} onChange=${e=>setEditDate(e.target.value)} onFocus=${e=>{if(!e.target.value)e.target.value=new Date().toISOString().split('T')[0];}}/>
                </div>
                <div>
                  <label class="lbl">Time *</label>
                  <input class="inp" type="time" value=${editTime} onChange=${e=>setEditTime(e.target.value)}/>
                </div>
              </div>
              <div>
                <label class="lbl">Notify me before</label>
                <div style=${{display:'flex',gap:8,flexWrap:'wrap',marginTop:4}}>
                  ${[5,10,15,30,60].map(m=>html`
                    <button key=${m} class=${'chip'+(editMins===m?' on':'')} onClick=${()=>setEditMins(m)} style=${{fontSize:12,padding:'5px 12px'}}>
                      ${m<60?m+' min':'1 hr'}
                    </button>`)}
                </div>
              </div>
              <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:4}}>
                <button class="btn bg" onClick=${()=>setEditReminder(null)}>Cancel</button>
                <button class="btn bp" onClick=${saveEdit} disabled=${saving||!editDate||!editTime}>
                  ${saving?'Saving...':'Save Changes'}
                </button>
              </div>
            </div>
          </div>
        </div>`:null}
    </div>`;
}
/* ─── RemindersPanel ──────────────────────────────────────────────────────── */
function RemindersPanel({onClose,onReload}){
  const [reminders,setReminders]=useState([]);
  useEffect(()=>{
    api.get('/api/reminders').then(d=>{if(Array.isArray(d))setReminders(d);});
  },[]);
  const del=async(id)=>{
    await api.del('/api/reminders/'+id);
    setReminders(prev=>prev.filter(r=>r.id!==id));
    onReload&&onReload();
  };
  return html`
    <div class="ov" onClick=${e=>e.target===e.currentTarget&&onClose()}>
      <div class="mo" style=${{maxWidth:500}}>
        <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:18}}>
          <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)'}}>⏰ My Reminders</h2>
          <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${onClose}>✕</button>
        </div>
        ${reminders.length===0?html`<p style=${{color:'var(--tx3)',fontSize:13,textAlign:'center',padding:'24px 0'}}>No active reminders.</p>`:null}
        <div style=${{display:'flex',flexDirection:'column',gap:9}}>
          ${reminders.map(r=>html`
            <div key=${r.id} style=${{display:'flex',alignItems:'center',gap:12,padding:'11px 14px',background:'var(--sf2)',borderRadius:11,border:'1px solid var(--bd)'}}>
              <div style=${{fontSize:24}}>⏰</div>
              <div style=${{flex:1}}>
                <p style=${{fontSize:13,fontWeight:600,color:'var(--tx)',marginBottom:3}}>${r.task_title}</p>
                <p class="tx3-11">
                  ${r.minutes_before>0?r.minutes_before+' min before · ':''}
                  ${new Date(r.remind_at).toLocaleString()}
                </p>
              </div>
              <button class="btn brd" style=${{fontSize:11,padding:'5px 9px',color:'var(--rd)'}}
                onClick=${()=>del(r.id)}>✕</button>
            </div>`)}
        </div>
      </div>
    </div>`;
}

function App(){
  const [dark,setDark]=useState(()=>{try{return localStorage.getItem('pf_dark')==='1';}catch{return false;}});const [cu,setCu]=useState(null);const [loading,setLoading]=useState(true);
  // Read initial view from URL path or ?page= param
  const VALID_VIEWS=['dashboard','projects','tasks','messages','dm','tickets','timeline','reminders','docs','settings','team','productivity','timereport','notifs'];
  // Also treat /projects/<id> as valid
  useEffect(()=>{
    try{
      const p=window.location.pathname;
      if(p.startsWith('/projects/')&&p.length>10){
        const pid=p.split('/')[2];
        if(pid)setInitialProjectId(pid);
        setView('projects');
      }
    }catch(e){}
  },[]);
  // Set initial page title based on current URL path
  useEffect(()=>{
    try{
      const p=window.location.pathname.replace(/^\//, '').split('/')[0].trim();
      const VIEW_T={dashboard:'Dashboard',projects:'Projects',tasks:'Kanban Board',messages:'Channels',dm:'Direct Messages',tickets:'Tickets',timeline:'Timeline Tracker',reminders:'Reminders',settings:'Settings',team:'Team Management',productivity:'Dev Productivity',announcements:'Announcements',standup:'AI Standup',codereview:'Code Review',risk:'Risk Predictor',timereport:'Time Report',forms:'Forms & Intake'};
      if(p&&VIEW_T[p]) document.title='VEWIT — '+VIEW_T[p]+' | AI-Powered Team Collaboration';
      else document.title='VEWIT — AI-Powered Team Collaboration Platform';
    }catch(e){}
  },[]);
  const [view,setView]=useState(()=>{
    try{
      const p=window.location.pathname.replace(/^\//, '').split('/')[0].trim();
      if(p&&VALID_VIEWS.includes(p)) return p;
      const sp=new URLSearchParams(window.location.search).get('page');
      if(sp&&VALID_VIEWS.includes(sp)) return sp;
    }catch(e){}
    return 'dashboard';
  });
  // Keep browser URL in sync with current view
  const VIEW_TITLES={
    dashboard:'Dashboard',projects:'Projects',tasks:'Kanban Board',
    messages:'Channels',dm:'Direct Messages',tickets:'Tickets',
    timeline:'Timeline Tracker',reminders:'Reminders',
    settings:'Settings',team:'Team Management',
    productivity:'Dev Productivity',docs:'Documentation & Diagrams',
    timereport:'Time Report',notifs:'Notifications',
  };
  const _setView=useCallback((v)=>{
    setView(v);
    try{
      const base=v.split(':')[0];
      if(VALID_VIEWS.includes(base)){
        history.pushState(null,'','/'+base);
        document.title='VEWIT — '+(VIEW_TITLES[base]||base)+' | AI-Powered Team Collaboration';
      }
    }catch(e){}
  },[]);
  // Handle browser back/forward
  useEffect(()=>{
    const onPop=()=>{
      try{
        const p=window.location.pathname.replace(/^\//, '').split('/')[0].trim();
        if(p&&VALID_VIEWS.includes(p)) setView(p);
        else setView('dashboard');
      }catch(e){}
    };
    window.addEventListener('popstate',onPop);
    return()=>window.removeEventListener('popstate',onPop);
  },[]);
  const [col,setCol]=useState(()=>{try{return localStorage.getItem('pf_col')==='1';}catch{return false;}});
  const [initialProjectId,setInitialProjectId]=useState(null);
  useEffect(()=>{
    try{
      const saved=JSON.parse(localStorage.getItem('pf_accent')||'null');
      const oldGreen=['#aaff00','#99ee00','#aaf000','#aaff00'.toLowerCase(),'#7c3aed','#8b5cf6','#6d28d9','#9333ea','#a855f7'];
      if(saved&&saved.ac&&oldGreen.includes(saved.ac.toLowerCase())){
        localStorage.removeItem('pf_accent');
        return;
      }
      if(saved&&saved.ac){
        const r=document.body.style;
        r.setProperty('--ac',saved.ac);r.setProperty('--ac2',saved.ac2||saved.ac);
        const hex=saved.ac.replace('#','');const bigint=parseInt(hex,16);
        const ri=Math.round((bigint>>16)&255),gi=Math.round((bigint>>8)&255),bi=Math.round(bigint&255);
        r.setProperty('--ac3','rgba('+ri+','+gi+','+bi+',.10)');
        r.setProperty('--ac4','rgba('+ri+','+gi+','+bi+',.06)');
        r.setProperty('--ac-tx',saved.tx||'#ffffff');
      }
    }catch(e){}
  },[]);
  const [data,setData]=useState({users:[],projects:[],tasks:[],notifs:[],teams:[],tickets:[]});
  const [teamCtx,setTeamCtxRaw]=useState(()=>{try{return localStorage.getItem('pf_team_ctx')||'';}catch{return '';}});
  const setTeamCtx=useCallback((id,forceDev=false)=>{
    if(cu&&cu.role!=='Admin'&&cu.role!=='Manager'&&!forceDev)return;
    setTeamCtxRaw(id);
    try{localStorage.setItem('pf_team_ctx',id||'');}catch{}
  },[cu]);
  const [dmUnread,setDmUnread]=useState([]);
  const [globalSearch,setGlobalSearch]=useState('');
  const [showGlobalSearch,setShowGlobalSearch]=useState(false);
  const [searchFilters,setSearchFilters]=useState({type:'all',assignee:'',priority:''});
  const [searchSubtasks,setSearchSubtasks]=useState([]);const [wsName,setWsName]=useState('');const [wsDmEnabled,setWsDmEnabled]=useState(true);const [dmTargetUser,setDmTargetUser]=useState(null);
  const [onlineUsers,setOnlineUsers]=useState(new Set());

  // Presence heartbeat — ping every 30s, fetch online users every 15s
  useEffect(()=>{
    if(!cu)return;
    const fetchPresence=()=>api.get('/api/presence').then(ids=>{
      if(Array.isArray(ids)&&ids.length>=0)setOnlineUsers(new Set(ids));
    }).catch(()=>{});
    const beat=()=>api.post('/api/presence',{}).then(()=>fetchPresence()).catch(()=>{});
    // Fire immediately on mount
    fetchPresence(); // fetch current online users right away (don't wait for beat)
    beat();          // then beat + fetch again
    const beatId=setInterval(beat,30000); // Heartbeat every 30s
    window.addEventListener('focus',()=>{beat();});
    const presId=setInterval(fetchPresence,20000); // Presence check every 20s
    return()=>{clearInterval(beatId);clearInterval(presId);};
  },[cu]);
  const [showReminders,setShowReminders]=useState(false);const [reminderTask,setReminderTask]=useState(null);const [upcomingReminders,setUpcomingReminders]=useState([]);
  const [showNotifBanner,setShowNotifBanner]=useState(false);
  const [toasts,setToasts]=useState([]);
  const toastTimers=useRef({});
  const TOAST_DUR=6000; // ms before auto-dismiss

  const addToast=useCallback((type,title,body)=>{
    const id='t'+Date.now()+Math.random();
    const timeStr=new Date().toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'});
    setToasts(prev=>[{id,type,title,body,timeStr,progress:100,leaving:false},...prev].slice(0,5));
    const start=Date.now();
    const tick=setInterval(()=>{
      const elapsed=Date.now()-start;
      const pct=Math.max(0,100-(elapsed/TOAST_DUR*100));
      setToasts(prev=>prev.map(t=>t.id===id?{...t,progress:pct}:t));
      if(elapsed>=TOAST_DUR){clearInterval(tick);dismissToast(id);}
    },100);
    toastTimers.current[id]=tick;
  },[]);

  const dismissToast=useCallback((id)=>{
    if(toastTimers.current[id]){clearInterval(toastTimers.current[id]);delete toastTimers.current[id];}
    setToasts(prev=>prev.map(t=>t.id===id?{...t,leaving:true}:t));
    setTimeout(()=>setToasts(prev=>prev.filter(t=>t.id!==id)),220);
  },[]);

  useEffect(()=>{window._pfToast=addToast;},[addToast]);

  const notify=useCallback((type,title,body,navTo,opts={})=>{
    addToast(type,title,body);
    showBrowserNotif(title,body,()=>setView(navTo),{...opts,tag:opts.tag||type+'-'+Date.now()});
    playSound(type==='call'?'call':'notif');
  },[addToast]);

  useEffect(()=>{
    if(cu&&'Notification' in window&&Notification.permission==='default'){
      setTimeout(()=>setShowNotifBanner(true),2500);
    }
  },[cu]);


  const [teamLoading,setTeamLoading]=useState(false);

  const load=useCallback(async(overrideTeamCtx)=>{
    if(!cu)return;
    const tCtx=overrideTeamCtx!==undefined?overrideTeamCtx:teamCtx;
    try{
      const projUrl=tCtx?'/api/projects?team_id='+tCtx+'&limit=200':'/api/projects?limit=200';
      const taskUrl=tCtx?'/api/tasks?team_id='+tCtx+'&limit=500':'/api/tasks?limit=500';
      const ticketUrl=tCtx?'/api/tickets?team_id='+tCtx+'&limit=100':'/api/tickets?limit=100';
      const [users,projects,tasks,notifs,dmu,ws,teamsRaw,ticketsRaw]=await Promise.all([
        api.get('/api/users'),api.get(projUrl),api.get(taskUrl), api.get('/api/notifications'),api.get('/api/dm/unread'),api.get('/api/workspace'), api.get('/api/teams'),api.get(ticketUrl), ]);
      const teams=Array.isArray(teamsRaw)?teamsRaw:[];
      const ticketItems=ticketsRaw?.items||ticketsRaw||[];
      const tickets=Array.isArray(ticketItems)?ticketItems:[];
      const projectItems=projects?.items||projects||[];
      const taskItems=tasks?.items||tasks||[];
      setData({users:Array.isArray(users)?users:[],projects:Array.isArray(projectItems)?projectItems:[],tasks:Array.isArray(taskItems)?taskItems:[],notifs:Array.isArray(notifs)?notifs:[],teams,tickets});
      setDmUnread(Array.isArray(dmu)?dmu:[]);
      if(ws&&ws.name)setWsName(ws.name);
      if(ws)setWsDmEnabled(ws.dm_enabled!==0);
      const rems=await api.get('/api/reminders');
      if(Array.isArray(rems)){const now=new Date();setUpcomingReminders(rems.filter(r=>new Date(r.remind_at)>=now).sort((a,b)=>new Date(a.remind_at)-new Date(b.remind_at)));}
    }catch(e){console.error(e);}
  },[cu]);

  useEffect(()=>{
    // Must bypass browser cache — a stale cached response could show a logged-out
    // user as authenticated (the 5s max-age on /api/* endpoints was the root cause)
    fetch('/api/auth/me',{credentials:'include',cache:'no-store'})
      .then(r=>r.ok?r.json():Promise.reject())
      .then(u=>{if(u&&!u.error)setCu(u);})
      .catch(()=>{})
      .finally(()=>setLoading(false));
  },[]);
  // Expose search opener for topbar button
  useEffect(()=>{window._pfOpenSearch=()=>{setShowGlobalSearch(v=>!v);setGlobalSearch('');setSearchSubtasks([]);};},[]);
  // Expose DM target setter for notification click handlers
  useEffect(()=>{window._pfSetDmTarget=(uid)=>{setDmTargetUser(uid);};},[]);
  // Fetch subtask search results
  useEffect(()=>{
    const q=(globalSearch||'').trim();
    if(!q||q.length<2){setSearchSubtasks([]);return;}
    const t=setTimeout(()=>{
      api.get('/api/subtasks/search?q='+encodeURIComponent(q))
        .then(d=>{if(Array.isArray(d))setSearchSubtasks(d);})
        .catch(()=>{});
    },300); // debounce
    return()=>clearTimeout(t);
  },[globalSearch]);
  // Global search shortcut: Cmd+K / Ctrl+K
  useEffect(()=>{
    const h=(e)=>{
      if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();setShowGlobalSearch(v=>!v);setGlobalSearch('');}
      if(e.key==='Escape')setShowGlobalSearch(false);
    };
    document.addEventListener('keydown',h);
    return()=>document.removeEventListener('keydown',h);
  },[]);
  useEffect(()=>{load();},[load]);

  const prevTeamCtxRef=useRef(teamCtx);
  useEffect(()=>{
    if(!cu)return;
    if(prevTeamCtxRef.current===teamCtx)return; // skip initial mount
    prevTeamCtxRef.current=teamCtx;
    setTeamLoading(true);
    setView('dashboard'); // always go to dashboard on team switch
    setData(prev=>({...prev,projects:[],tasks:[],tickets:[]}));
    load(teamCtx).finally(()=>setTeamLoading(false));
  },[teamCtx,cu]);
  useEffect(()=>{
    if(!cu)return;
    // Lightweight poll every 15s — only fetch counts, not full data
    // Full reload triggered by explicit user actions or team switch
    const id=setInterval(async()=>{
      try{
        const r=await api.get('/api/poll');
        if(r&&!r.error){
          // Update DM unread counts from poll
          if(Array.isArray(r.dm_unread)) setDmUnread(r.dm_unread);
          // If there are new notifications, refresh notifications only
          if(r.notif_count>0){
            const notifs=await api.get('/api/notifications');
            if(Array.isArray(notifs)) setData(prev=>({...prev,notifs}));
          }
        }
      }catch(e){}
    },15000);
    // Full data refresh every 5 minutes (in case of external changes)
    const fullId=setInterval(async()=>{
      try{
        const projUrl=teamCtx?'/api/projects?team_id='+teamCtx:'/api/projects';
        const taskUrl=teamCtx?'/api/tasks?team_id='+teamCtx:'/api/tasks';
        const [pr,tk]=await Promise.all([api.get(projUrl),api.get(taskUrl)]);
        const pi=pr?.items||pr||[]; const ti=tk?.items||tk||[];
        if(Array.isArray(pi)&&Array.isArray(ti)){
          setData(prev=>({...prev,projects:pi,tasks:ti}));
        }
      }catch(e){}
    },300000); // 5 min
    return()=>{clearInterval(id);clearInterval(fullId);};
  },[cu,teamCtx]);
  useEffect(()=>{
    document.body.className=dark?'dm':'';
    try{
      const saved=JSON.parse(localStorage.getItem('pf_accent')||'null');
      if(saved&&saved.ac){
        const r=document.body.style;
        const hex=saved.ac.replace('#','');const bigint=parseInt(hex,16);
        const ri=Math.round((bigint>>16)&255),gi=Math.round((bigint>>8)&255),bi=Math.round(bigint&255);
        r.setProperty('--ac',saved.ac);r.setProperty('--ac2',saved.ac2||saved.ac);
        r.setProperty('--ac3','rgba('+ri+','+gi+','+bi+','+(dark?'.10':'.15')+')');
        r.setProperty('--ac4','rgba('+ri+','+gi+','+bi+','+(dark?'.06':'.08')+')');
        r.setProperty('--ac-tx',saved.tx||'#0d1f00');
      }
    }catch(e){}
  },[dark]);

  const prevDmsRef=useRef([]);
  useEffect(()=>{
    if(!cu)return;
    api.get('/api/dm/unread').then(d=>{if(Array.isArray(d)){prevDmsRef.current=d;setDmUnread(d);}});
    const id=setInterval(()=>{
      api.get('/api/dm/unread').then(d=>{
        if(!Array.isArray(d))return;
        const prev=prevDmsRef.current;
        d.forEach(x=>{
          const old=prev.find(p=>p.sender===x.sender);
          if(!old||(x.cnt||0)>(old.cnt||0)){
            const sender=data.users.find(u=>u.id===x.sender);
            const sname=sender?sender.name:'Someone';
            window._pfToast&&window._pfToast('dm','💬 New message from '+sname,'Tap to open Direct Messages');
            showBrowserNotif('💬 '+sname,'New message',()=>{setDmTargetUser(x.sender);_setView('dm');window.focus();},{tag:'dm-'+x.sender});
            playSound('notif');
          }
        });
        prevDmsRef.current=d;
        setDmUnread(d);
      });
    },5000);
    return()=>clearInterval(id);
  },[cu]); // intentionally omit data.users to avoid reset — sender name is best-effort

  const prevNotifIdsRef=useRef(null); // null = not yet seeded
  const NTITLES={
    task_assigned:'✅ Task assigned to you', status_change:'🔄 Task status changed', comment:'💬 New comment on task', deadline:'⏰ Deadline approaching', dm:'📨 New direct message', project_added:'📁 Added to a project', reminder:'⏰ Reminder', call:'📞 Huddle call', message:'#️⃣ New channel message', };
  const NNAV={task_assigned:'tasks',status_change:'tasks',comment:'tasks',deadline:'tasks',dm:'dm',project_added:'projects',reminder:'reminders',call:'dm',message:'messages'};
  useEffect(()=>{
    if(!cu)return;

    const pollOnce=()=>{
      api.get('/api/notifications').then(d=>{
        if(!Array.isArray(d))return;
        if(prevNotifIdsRef.current===null){
          prevNotifIdsRef.current=new Set(d.map(n=>n.id));
          setData(prev=>({...prev,notifs:d}));
          return;
        }
        const brandNew=d.filter(n=>!prevNotifIdsRef.current.has(n.id));
        brandNew.forEach(n=>{
          if(n.type==='dm')return; // DMs handled by separate poll
          if(n.type==='call') return;
          const title=NTITLES[n.type]||'VEWIT';
          const nav=NNAV[n.type]||'notifs';
          addToast(n.type,title,n.content||'');
          showBrowserNotif(title,n.content||'',()=>{
            window.focus();
            if(n.type==='dm'){const sid=n.sender_id||n.sender;if(sid)setDmTargetUser(sid);_setView('dm');}
            else{_setView(nav);}
          },{tag:'notif-'+n.id});
          playSound('notif');
        });
        prevNotifIdsRef.current=new Set(d.map(n=>n.id));
        setData(prev=>({...prev,notifs:d}));
        const unread=d.filter(n=>!n.read).length;
        const dmTotal=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);
        updateBadge(unread+dmTotal);
      });
    };

    api.get('/api/notifications').then(d=>{
      if(Array.isArray(d)){
        prevNotifIdsRef.current=new Set(d.map(n=>n.id));
        setData(prev=>({...prev,notifs:d}));
        const unread=d.filter(n=>!n.read).length;
        updateBadge(unread+dmUnread.reduce((a,x)=>a+(x.cnt||0),0));
      }
    });

    triggerPollRef.current=pollOnce;

    const id=setInterval(pollOnce, 6000);
    return()=>{ clearInterval(id); if(triggerPollRef.current===pollOnce) triggerPollRef.current=null; };
  },[cu,addToast]);

  const onDmRead=useCallback(sid=>{
    setDmUnread(prev=>prev.filter(x=>x.sender!==sid));
    // Also clear DM notifications from this sender in the panel
    setData(prev=>{
      const toDelete=prev.notifs.filter(n=>n.type==='dm'&&(n.sender_id===sid||n.sender===sid));
      toDelete.forEach(n=>{
        api.del('/api/notifications/'+n.id).catch(()=>{});
      });
      return {...prev,notifs:prev.notifs.filter(n=>!(n.type==='dm'&&(n.sender_id===sid||n.sender===sid)))};
    });
  },[]);
  const logout=async()=>{
    if(window._pfPushUnsubscribe) await window._pfPushUnsubscribe().catch(()=>{});
    try{
      await fetch('/api/auth/logout',{
        method:'POST',credentials:'include',
        headers:{'Content-Type':'application/json','Cache-Control':'no-store'},
        body:JSON.stringify({})
      });
    }catch(e){}
    setCu(null);setData({users:[],projects:[],tasks:[],notifs:[]});setDmUnread([]);
    try{localStorage.removeItem('pf_team_ctx');}catch(e){}
    // replace() removes the app from history — back button can't return to authenticated state
    window.location.replace('/?action=login&ts='+Date.now());
  };

  useEffect(()=>{if(cu)requestNotifPermission();},[cu]);

  const triggerPollRef = useRef(null);
  useEffect(()=>{
    window._pfOnVisible = ()=>{
      if(triggerPollRef.current) triggerPollRef.current();
    };
    return ()=>{ window._pfOnVisible = null; };
  },[]);

  useEffect(()=>{
    const unread=safe(data.notifs).filter(n=>!n.read).length;
    const dmTotal=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);
    updateBadge(unread+dmTotal);
  },[data.notifs,dmUnread]);

  const firedEarlyRef=useRef(new Set());
  useEffect(()=>{
    if(!cu)return;
    const checkDue=async()=>{
      const due=await api.get('/api/reminders/due');
      if(Array.isArray(due)&&due.length>0){
        due.forEach(r=>{
          addToast('reminder','⏰ Reminder: '+r.task_title,'Click to view');
          showBrowserNotif('⏰ '+r.task_title,'Reminder is due now!',()=>{
            setView('reminders');
            if(window.electronAPI){window.electronAPI.focusWindow();}else{window.focus();}
          },{tag:'rem-'+r.id,requireInteraction:true});
          playSound('reminder');
        });
      }
      const rems=await api.get('/api/reminders');
      if(Array.isArray(rems)){
        const now=new Date();
        rems.forEach(r=>{
          const remAt=new Date(r.remind_at);
          const minsBefore=r.minutes_before||0;
          if(minsBefore>0){
            const warnAt=new Date(remAt.getTime()-minsBefore*60000);
            const diff=warnAt-now;
            const earlyKey='early-'+r.id+'-'+minsBefore;
            if(diff>=-60000&&diff<=60000&&!firedEarlyRef.current.has(earlyKey)){
              firedEarlyRef.current.add(earlyKey);
              addToast('reminder','⏰ Coming up in '+minsBefore+'min',r.task_title);
              showBrowserNotif('⏰ Reminder in '+minsBefore+' min',r.task_title,()=>{
                setView('reminders');
                if(window.electronAPI){window.electronAPI.focusWindow();}else{window.focus();}
              },{tag:earlyKey,requireInteraction:false});
              playSound('reminder');
            }
          }
        });
        setUpcomingReminders(rems.filter(r=>!r.fired&&new Date(r.remind_at)>=now).sort((a,b)=>new Date(a.remind_at)-new Date(b.remind_at)));
      }
    };
    checkDue();
    const id=setInterval(checkDue,30000);
    return()=>clearInterval(id);
  },[cu,addToast]);

  const isDevRole=cu&&cu.role!=='Admin'&&cu.role!=='Manager';
  const isAdminManager=cu&&(cu.role==='Admin'||cu.role==='Manager');
  const [devNoTeam,setDevNoTeam]=useState(false);
  useEffect(()=>{
    if(!isDevRole||!cu||safe(data.teams).length===0)return;
    const myTeams=safe(data.teams).filter(t=>{
      try{return JSON.parse(t.member_ids||'[]').includes(cu.id);}catch{return false;}
    });
    if(myTeams.length===0){setDevNoTeam(true);return;}
    setDevNoTeam(false);
    if(!teamCtx){
      setTeamCtx(myTeams[0].id,true); // forceDev=true bypasses lock
    } else {
      const valid=myTeams.find(t=>t.id===teamCtx);
      if(!valid)setTeamCtx(myTeams[0].id,true);setView('dashboard');
    }
  },[cu,isDevRole,data.teams,teamCtx,setTeamCtx]);

  const activeTeam=useMemo(()=>teamCtx?safe(data.teams).find(t=>t.id===teamCtx)||null:null,[teamCtx,data.teams]);
  const teamMemberIds=useMemo(()=>activeTeam?new Set(JSON.parse(activeTeam.member_ids||'[]')):new Set(),[activeTeam]);
  const scopedProjects=data.projects;
  const scopedTasks=data.tasks;
  const scopedUsers=useMemo(()=>{
    if(!activeTeam)return data.users;
    return safe(data.users).filter(u=>teamMemberIds.has(u.id));
  },[data.users,activeTeam,teamMemberIds]);

  if(loading)return html`<div style=${{display:'flex',alignItems:'center',justifyContent:'center',height:'100vh',background:'#ffffff',flexDirection:'column',gap:0}}>
    <div style=${{position:'fixed',inset:0,background:'linear-gradient(180deg,#ffffff 0%,#f0f9ff 40%,#dbeafe 70%,#bfdbfe 100%)',zIndex:0}}></div>
    <div style=${{position:'relative',zIndex:1,display:'flex',flexDirection:'column',alignItems:'center',gap:0}}>
      <div style=${{width:72,height:72,background:'#2563eb',borderRadius:18,display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 8px 32px rgba(37,99,235,0.3)',animation:'sp .9s linear infinite'}}>
        <svg width="38" height="38" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white"/><circle cx="51" cy="43" r="6" fill="white"/><circle cx="13" cy="43" r="6" fill="white"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" strokeWidth="3.5" strokeLinecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" strokeWidth="3.5" strokeLinecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" strokeWidth="3.5" strokeLinecap="round"/></svg>
      </div>
      <p style=${{color:'#475569',fontSize:13,marginTop:16,fontFamily:"'DM Sans',sans-serif",letterSpacing:'.3px',fontWeight:500}}>Loading VEWIT...</p>
      <div style=${{marginTop:10,width:110,height:3,background:'#e2e8f0',borderRadius:100,overflow:'hidden'}}>
        <div style=${{height:'100%',background:'#2563eb',borderRadius:100,animation:'loadBar 1.4s ease-in-out infinite'}}></div>
      </div>
    </div>
  </div>`;
  if(!cu)return html`<${AuthScreen} onLogin=${u=>{setCu(u);}}/>`;

  if(isDevRole && devNoTeam && safe(data.teams).length>0) return html`
    <div style=${{display:'flex',alignItems:'center',justifyContent:'center',height:'100vh',background:'var(--bg)',flexDirection:'column',gap:16,padding:24}}>
      <div style=${{width:72,height:72,borderRadius:20,background:'var(--sf)',border:'1px solid var(--bd)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:34}}>🏷</div>
      <div style=${{textAlign:'center',maxWidth:380}}>
        <h2 style=${{fontSize:18,fontWeight:700,color:'var(--tx)',marginBottom:8}}>Not assigned to a team yet</h2>
        <p style=${{fontSize:13,color:'var(--tx2)',lineHeight:1.6}}>You haven't been added to any team. Ask your Admin to assign you to a team before you can access the workspace.</p>
        <div style=${{marginTop:16,padding:'10px 16px',background:'var(--sf)',borderRadius:12,border:'1px solid var(--bd)',fontSize:12,color:'var(--tx3)'}}>
          Logged in as <b style=${{color:'var(--tx)'}}>${cu.name}</b> · ${cu.email}
        </div>
      </div>
      <button class="btn bg" style=${{fontSize:12,marginTop:4}} onClick=${logout}>Sign out</button>
    </div>`;

  const unread=safe(data.notifs).filter(n=>!n.read).length;
  const totalDm=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);

  const activeTeamName=activeTeam?activeTeam.name:'';
  const TITLES={
    dashboard:{title:'Dashboard',sub:activeTeamName?activeTeamName+' Team Dashboard':'Overview of your work'}, projects:{title:'Projects',sub:scopedProjects.length+' projects'+(activeTeamName?' · '+activeTeamName:'')}, tasks:{title:'Kanban Board',sub:scopedTasks.filter(t=>t.stage!=='completed'&&t.stage!=='backlog').length+' active · '+scopedTasks.length+' total'+(activeTeamName?' · '+activeTeamName:'')}, messages:{title:'Channels',sub:(activeTeamName?activeTeamName+' · ':'')+'Project channels'}, dm:{title:'Direct Messages',sub:totalDm>0?totalDm+' unread':'Private conversations'}, reminders:{title:'Reminders',sub:'Upcoming task reminders'}, notifs:{title:'Notifications',sub:unread+' unread'}, team:{title:'Team Management',sub:'Members & sub-teams'}, settings:{title:'Settings',sub:wsName||'Workspace configuration'}, timeline:{title:'Timeline Tracker',sub:activeTeamName?activeTeamName+' project timeline':'Project schedule'}, productivity:{title:'Dev Productivity',sub:'Team performance analytics'}, tickets:{title:'Tickets',sub:activeTeamName?activeTeamName+' tickets':'Support & bug tickets'}, docs:{title:'Documentation & Diagrams',sub:'Docs, architecture & technical diagrams'}, timereport:{title:'Time Report',sub:'Hours logged by member and project'},
  };

  const baseView=(view||'dashboard').split(':')[0];
  const viewParts=view.split(':');
  const taskFilterType=viewParts[1]||null;
  const taskFilterValue=viewParts[2]||null;
  const ticketFilterType=baseView==='tickets'?(viewParts[1]||null):null;
  const ticketFilterValue=baseView==='tickets'?(viewParts[2]||null):null;
  const info=TITLES[baseView]||{title:baseView,sub:''};
  const extra=null;

  return html`
    <div style=${{display:'flex',width:'100vw',height:'100vh',background:'var(--bg)',overflow:'hidden'}}>
      <${Sidebar} cu=${cu} view=${baseView} setView=${v=>{
          if(typeof v==='string'&&v.startsWith('dm:')){const uid=v.slice(3);setDmTargetUser(uid);_setView('dm');}
          else _setView(v);
        }} onLogout=${logout} unread=${unread} dmUnread=${dmUnread} col=${col} setCol=${v=>{setCol(v);try{localStorage.setItem('pf_col',v?'1':'0');}catch{}}} wsName=${wsName}
        dark=${dark} setDark=${setDark} wsDmEnabled=${wsDmEnabled} onlineUsers=${onlineUsers}
        teams=${data.teams} users=${data.users} projects=${scopedProjects} tasks=${scopedTasks}
        teamCtx=${teamCtx} setTeamCtx=${setTeamCtx} activeTeam=${activeTeam}
        />
      <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden',minWidth:0}}>
        <${Header} title=${info.title} sub=${info.sub} dark=${dark} setDark=${setDark} extra=${extra}
          cu=${cu} setCu=${setCu} upcomingReminders=${upcomingReminders} onViewReminders=${()=>setView('reminders')}
          notifs=${data.notifs}
          activeTeam=${activeTeam} teams=${data.teams} setTeamCtx=${setTeamCtx}
          onNotifClick=${async n=>{
            // Mark read + DELETE from panel immediately (natural notification behaviour)
            api.put('/api/notifications/'+n.id+'/read',{}).catch(()=>{});
            api.del('/api/notifications/'+n.id).catch(()=>{});
            // Remove from local state instantly — panel clears without waiting for reload
            setData(prev=>({...prev,notifs:prev.notifs.filter(x=>x.id!==n.id)}));
            const nav={task_assigned:'tasks',status_change:'tasks',comment:'tasks',deadline:'tasks',dm:'dm',project_added:'projects',reminder:'reminders',call:'dm',message:'messages'};
            const dest=nav[n.type]||'notifs';
            // DM: open sender's chat thread
            if(n.type==='dm'||n.type==='message'){
              const senderId=n.sender_id||n.sender||null;
              if(senderId)setDmTargetUser(senderId);
            }
            // Call: open Jitsi with the caller directly
            if(n.type==='call'){
              const senderId=n.sender_id||n.sender||null;
              if(senderId){
                const callerUser=data.users.find(u=>u.id===senderId);
                if(senderId)setDmTargetUser(senderId);
              }
            }
            setView(dest);
          }}
          onMarkAllRead=${async()=>{await api.put('/api/notifications/read-all',{});load();}}
          onClearAll=${async()=>{await api.del('/api/notifications/all');load();}}
        />
        <div style=${{flex:1,overflow:'hidden',display:'flex',flexDirection:'column'}}>
          <${ErrorBoundary}>
            <div key=${baseView+'-'+(teamCtx||'all')} class="page-enter" style=${{flex:1,overflow:'hidden',display:'flex',flexDirection:'column',height:'100%'}}>
            ${baseView==='dashboard'?html`<${Dashboard} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} users=${scopedUsers} onNav=${_setView} activeTeam=${activeTeam} teams=${data.teams} setTeamCtx=${setTeamCtx}/>`:null}
            ${baseView==='projects'?html`<${ProjectsView} projects=${scopedProjects} tasks=${scopedTasks} users=${data.users} cu=${cu} reload=${load} onSetReminder=${t=>setReminderTask(t)} teams=${data.teams} activeTeam=${activeTeam} initialProjectId=${initialProjectId} onClearInitial=${()=>setInitialProjectId(null)}/>`:null}
            ${baseView==='tasks'?html`<${TasksView} tasks=${scopedTasks} projects=${scopedProjects} users=${scopedUsers} cu=${cu} reload=${load} onSetReminder=${t=>setReminderTask(t)} teams=${data.teams} activeTeam=${activeTeam}
              initialStage=${taskFilterType==='stage'?taskFilterValue:null}
              initialPriority=${taskFilterType==='priority'?taskFilterValue:null}
              initialAssignee=${taskFilterType==='assignee'?taskFilterValue:null}
            />`:null}
            ${baseView==='messages'?html`<${MessagesView} projects=${scopedProjects} users=${data.users} cu=${cu} tasks=${scopedTasks} key=${'msgs-'+(teamCtx||'all')}/>`:null}
            ${baseView==='dm'?html`<${DirectMessages} cu=${cu} users=${data.users} dmUnread=${dmUnread} onDmRead=${onDmRead} dmEnabled=${wsDmEnabled} initialUserId=${dmTargetUser} onClearInitial=${()=>setDmTargetUser(null)} onlineUsers=${onlineUsers}/>`:null}
            ${baseView==='reminders'?html`<${RemindersView} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} onSetReminder=${t=>setReminderTask(t)} onReload=${load}/>`:null}
            ${baseView==='notifs'?html`<${NotifsView} notifs=${data.notifs} reload=${load} onNavigate=${_setView}/>`:null}
            ${baseView==='tickets'?html`<${TicketsView} cu=${cu} users=${scopedUsers} projects=${scopedProjects} onReload=${load} activeTeam=${activeTeam} initialAssignee=${ticketFilterType==='assignee'?ticketFilterValue:null} initialStatus=${ticketFilterType==='status'?ticketFilterValue:null}/>`:null}
            ${baseView==='timeline'?html`<${TimelineView} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} onNav=${(v,pid)=>{_setView(v);if(pid)setInitialProjectId(pid);else setInitialProjectId(null);}}/>`:null}
            ${baseView==='docs'?html`<${DocsView} projects=${scopedProjects} cu=${cu}/>`:null}
            ${baseView==='team'&&isAdminManager?html`<${TeamView} users=${data.users} cu=${cu} reload=${load}/>`:null}
            ${baseView==='settings'&&isAdminManager?html`<${WorkspaceSettings} cu=${cu} onReload=${load}/>`:null}
            ${baseView==='timereport'&&isAdminManager?html`<${TimeReportView} cu=${cu} users=${scopedUsers}/>`:null}
            ${baseView==='productivity'&&isAdminManager?html`<${ProductivityView} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} users=${scopedUsers}/>`:null}
            </div>
          <//>
        </div>
      </div>
    </div>
    <${AIAssistant} cu=${cu} projects=${scopedProjects} tasks=${scopedTasks} users=${data.users}/>
    <!-- Global Search Spotlight — Cmd+K -->
    ${showGlobalSearch?html`
      <div style=${{position:'fixed',inset:0,background:'rgba(0,0,0,.55)',zIndex:9800,display:'flex',alignItems:'flex-start',justifyContent:'center',paddingTop:'10vh',backdropFilter:'blur(4px)'}}
        onClick=${e=>{if(e.target===e.currentTarget)setShowGlobalSearch(false);}}>
        <div style=${{width:'min(640px,92vw)',background:'var(--sf)',borderRadius:16,boxShadow:'0 24px 80px rgba(0,0,0,.35)',border:'1px solid var(--bd)',overflow:'hidden'}}>
          <!-- Search input -->
          <div style=${{display:'flex',alignItems:'center',gap:10,padding:'14px 18px',borderBottom:'1px solid var(--bd)'}}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--tx3)" strokeWidth="2.5" strokeLinecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input autoFocus class="inp" style=${{border:'none',background:'transparent',fontSize:16,flex:1,height:28,outline:'none',color:'var(--tx)'}}
              placeholder="Search tasks, tickets, projects… (Ctrl+K)"
              value=${globalSearch}
              onInput=${e=>setGlobalSearch(e.target.value)}
              onKeyDown=${e=>{if(e.key==='Escape')setShowGlobalSearch(false);}}/>
            <span style=${{fontSize:10,color:'var(--tx3)',background:'var(--sf2)',padding:'2px 6px',borderRadius:5,border:'1px solid var(--bd)'}}>ESC</span>
          </div>
          <!-- Filter chips -->
          <div style=${{display:'flex',gap:6,padding:'8px 14px',borderBottom:'1px solid var(--bd)',flexWrap:'wrap',alignItems:'center'}}>
            <span style=${{fontSize:10,fontWeight:600,color:'var(--tx3)'}}>Type:</span>
            ${['all','task','ticket','project'].map(t=>html`
              <button key=${t} onClick=${()=>setSearchFilters(f=>({...f,type:t}))}
                style=${{fontSize:11,padding:'2px 9px',borderRadius:99,border:'1px solid var(--bd)',cursor:'pointer',
                  background:searchFilters.type===t?'var(--ac)':'transparent',
                  color:searchFilters.type===t?'#fff':'var(--tx3)',fontWeight:searchFilters.type===t?700:400,transition:'all .12s'}}>
                ${t}
              </button>`)}
            <span style=${{fontSize:10,fontWeight:600,color:'var(--tx3)',marginLeft:6}}>Priority:</span>
            ${['','critical','high','medium','low'].map(p=>html`
              <button key=${p||'any'} onClick=${()=>setSearchFilters(f=>({...f,priority:p}))}
                style=${{fontSize:11,padding:'2px 9px',borderRadius:99,border:'1px solid var(--bd)',cursor:'pointer',
                  background:searchFilters.priority===p?'var(--ac)':'transparent',
                  color:searchFilters.priority===p?'#fff':'var(--tx3)',fontWeight:searchFilters.priority===p?700:400,transition:'all .12s'}}>
                ${p||'any'}
              </button>`)}
          </div>
          <!-- Results -->
          <div style=${{maxHeight:400,overflowY:'auto'}}>
            ${(()=>{
              const q=(globalSearch||'').trim().toLowerCase();
              if(!q||q.length<1)return html`
  <div style=${{padding:'16px 18px'}}> 
    <div style=${{fontSize:12,color:'var(--tx2)',fontWeight:600,marginBottom:8}}>Search by:</div>
    <div style=${{display:'flex',gap:8,flexWrap:'wrap'}}>
      ${[['Task ID','T-015-305','#1d4ed8'],['Bug','T-xxx bug','#b91c1c'],['Ticket ID','TK-xxx','#c2410c'],['Subtask','ST-xxx','#475569'],['Project name','SecOps','#15803d']].map(([label,ex,color])=>html`
        <div style=${{padding:'4px 10px',borderRadius:7,background:color+'11',border:'1px solid '+color+'33',fontSize:11,color,cursor:'pointer',fontWeight:600}}
          onClick=${()=>setGlobalSearch(ex)}>
          ${label}
        </div>`)}
    </div>
  </div>
`;
              const results=[];
              const sf=searchFilters||{type:'all',priority:''};
              // Search tasks by ID or title
              if(sf.type==='all'||sf.type==='task') safe(data.tasks).forEach(t=>{
                if(!t||!t.id||!t.title)return;
                const tid=(t.id||'').toLowerCase();
                const ttl=(t.title||'').toLowerCase();
                if(tid.includes(q)||ttl.includes(q)){
                  const proj=(data.projects||[]).find(p=>p.id===t.project);
                  results.push({type:t.task_type||'task',id:t.id,title:t.title,sub:proj?proj.name:'',color:TYPE_COLORS[t.task_type||'task']||'#1d4ed8',bg:TYPE_BG[t.task_type||'task']||'rgba(29,78,216,0.1)',item:t,nav:'tasks'});
                }
              });
              // Search tickets by ID, title, or description
              if(sf.type==='all'||sf.type==='ticket') safe(data.tickets||[]).forEach(t=>{
                const tStr=(t.id+' '+(t.title||'')+' '+(t.description||'')).toLowerCase();
                if(tStr.includes(q)){
                  const tColors={bug:'#b91c1c',feature:'#1d4ed8',improvement:'#0e7490',task:'#15803d',question:'#6d28d9'};
                  const tBg={bug:'rgba(185,28,28,0.10)',feature:'rgba(29,78,216,0.10)',improvement:'rgba(14,116,144,0.10)',task:'rgba(21,128,61,0.10)',question:'rgba(109,40,217,0.10)'};
                  results.push({type:'ticket',id:t.id,title:t.title,sub:(t.type||'bug')+' · '+(t.status||'open'),color:tColors[t.type]||'#c2410c',bg:tBg[t.type]||'rgba(194,65,12,0.10)',item:t});
                }
              });
              // Search subtasks by title
              // (subtasks fetched lazily — skip for global search)
              // Search projects
              if(sf.type==='all'||sf.type==='project') safe(data.projects).forEach(p=>{
                if(!p||!p.id||!p.name)return;
                if(p.id.toLowerCase().includes(q)||(p.name||'').toLowerCase().includes(q)){
                  results.push({type:'project',id:p.id,title:p.name,sub:'Project',color:p.color||'#1d4ed8',bg:'rgba(29,78,216,0.06)',item:p,nav:'projects'});
                }
              });
              // Subtask search results (async fetched)
              safe(searchSubtasks).forEach(s=>{
                if(!s||!s.id)return;
                results.push({type:'subtask',id:s.id.slice(0,12),title:s.title||'',sub:'↳ '+(s.task_title||'Task'),color:'#475569',bg:'rgba(71,85,105,0.10)',item:s,nav:'tasks'});
              });
              const filteredResults=sf.priority?results.filter(r=>!r.item?.priority||r.item.priority===sf.priority):results;
              if(!filteredResults.length)return html`<div style=${{padding:'20px',textAlign:'center',color:'var(--tx3)',fontSize:13}}>No results for "${q}"</div>`;
              return filteredResults.slice(0,15).map((r,i)=>html`
                <div key=${i}
                  onClick=${()=>{
                    setShowGlobalSearch(false);
                    if(r.nav==='projects'){setView('projects');setInitialProjectId(r.item.id);}
                    else if(r.type==='ticket'){setView('tickets');}
                    else{setView('tasks');}
                  }}
                  style=${{display:'flex',alignItems:'center',gap:12,padding:'10px 18px',cursor:'pointer',borderBottom:'1px solid var(--bd)',transition:'background .1s'}}
                  onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'}
                  onMouseLeave=${e=>e.currentTarget.style.background='transparent'}>
                  <span style=${{fontSize:9,fontWeight:800,padding:'2px 7px',borderRadius:4,background:r.bg,color:r.color,border:'1px solid '+r.color+'44',flexShrink:0,textTransform:'uppercase'}}>${r.type}</span>
                  <span style=${{fontSize:9,fontWeight:700,fontFamily:'monospace',padding:'2px 7px',borderRadius:4,background:r.bg,color:r.color,border:'1px solid '+r.color+'33',flexShrink:0}}>${r.id}</span>
                  <span style=${{fontSize:13,color:'var(--tx)',fontWeight:500,flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${r.title}</span>
                  ${r.sub?html`<span style=${{fontSize:11,color:'var(--tx3)',flexShrink:0}}>${r.sub}</span>`:null}
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--tx3)" strokeWidth="2" strokeLinecap="round"><path d="M9 18l6-6-6-6"/></svg>
                </div>`);
            })()}
          </div>
          <!-- Footer -->
          <div style=${{padding:'8px 18px',borderTop:'1px solid var(--bd)',display:'flex',gap:12,fontSize:11,color:'var(--tx3)'}}>
            <span>↵ Open</span><span>↑↓ Navigate</span><span style=${{marginLeft:'auto'}}>Ctrl+K to close</span>
          </div>
        </div>
      </div>`:null}

        ${teamLoading?html`
      <div style=${{position:'fixed',top:0,left:0,right:0,bottom:0,zIndex:9999, background:'rgba(0,0,0,.55)',display:'flex',alignItems:'center',justifyContent:'center', backdropFilter:'blur(2px)'}}>
        <div style=${{background:'var(--sf)',borderRadius:16,padding:'24px 32px',display:'flex',flexDirection:'column',alignItems:'center',gap:12,border:'1px solid var(--bd)',boxShadow:'0 8px 40px rgba(0,0,0,.5)'}}>
          <div style=${{width:40,height:40,border:'3px solid var(--bd)',borderTop:'3px solid var(--ac)',borderRadius:'50%',animation:'sp .7s linear infinite'}}></div>
          <div style=${{fontSize:13,fontWeight:600,color:'var(--tx)'}}>Switching to ${activeTeam?activeTeam.name:'workspace'}...</div>
          <div class="tx3-11">Loading team data</div>
        </div>
      </div>`:null}

    <${ToastStack} toasts=${toasts} onDismiss=${dismissToast} onNav=${setView}/>

    ${showNotifBanner?html`
      <div style=${{position:'fixed',bottom:20,left:'50%',transform:'translateX(-50%)',zIndex:9100, background:'var(--sf)',border:'1px solid rgba(170,255,0,.35)',borderRadius:18, padding:'16px 20px',boxShadow:'0 8px 40px rgba(0,0,0,.7)', display:'flex',alignItems:'flex-start',gap:14,maxWidth:440, animation:'slideUp .3s cubic-bezier(.34,1.56,.64,1)'}}>
        <div style=${{width:44,height:44,borderRadius:13,background:'linear-gradient(135deg,rgba(170,255,0,.2),rgba(170,255,0,.05))',border:'1px solid rgba(170,255,0,.35)', display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0,fontSize:22}}>🔔</div>
        <div style=${{flex:1,minWidth:0}}>
          <div style=${{fontSize:13,fontWeight:700,color:'var(--tx)',letterSpacing:'-0.01em',marginBottom:4}}>Enable desktop notifications</div>
          <div style=${{fontSize:11,color:'var(--tx2)',lineHeight:1.55,marginBottom:10}}>
            Stay informed even when the app is minimised or you're in another tab:
          </div>
          <div style=${{display:'flex',flexWrap:'wrap',gap:5,marginBottom:12}}>
            ${['✅ Task assigned','🔄 Status changes','💬 Comments','📁 Project updates','⏰ Reminders'].map(tag=>html`
              <span key=${tag} style=${{fontSize:10,padding:'2px 8px',borderRadius:100,background:'rgba(170,255,0,.08)',border:'1px solid rgba(170,255,0,.2)',color:'var(--ac)',fontWeight:600}}>${tag}</span>`)}
          </div>
          <div style=${{display:'flex',gap:7}}>
            <button class="btn bp" style=${{padding:'7px 16px',fontSize:12}}
              onClick=${()=>{requestNotifPermission();setShowNotifBanner(false);}}>🔔 Allow Notifications</button>
            <button class="btn bg" style=${{padding:'7px 12px',fontSize:11}}
              onClick=${()=>setShowNotifBanner(false)}>Later</button>
          </div>
        </div>
        <button class="btn bg" style=${{padding:'4px 8px',fontSize:11,flexShrink:0,alignSelf:'flex-start'}}
          onClick=${()=>setShowNotifBanner(false)}>✕</button>
      </div>`:null}

    ${reminderTask!==null?html`<${ReminderModal} task=${reminderTask} onClose=${()=>setReminderTask(null)} onSaved=${()=>{setReminderTask(null);load();}}/>`:null}
    ${showReminders?html`<${RemindersPanel} onClose=${()=>{setShowReminders(false);load();}} onReload=${load}/>`:null}`;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${ErrorBoundary}><${App}<//>`);
};
waitForLibs(window._pfStartApp);
})();
</script>
</body>
</html>"""

# ── Utilities ─────────────────────────────────────────────────────────────────
# Module-level init — runs when gunicorn imports app, ensures DB is ready
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(JS_DIR, exist_ok=True)
    init_db()
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
            print(f" ✗ ({e}) — will use CDN fallback"); all_ok=False
    return all_ok

def open_browser(port):
    time.sleep(1.4)
    webbrowser.open(f"http://localhost:{port}")

if __name__=="__main__":
    print("\n⚡ VEWIT v4.0 — Multi-Tenant | AI | Workspaces")
    print("="*54)
    print("  Initializing database...")
    init_db()
    print("  Checking JS libraries...")
    if not download_js():
        print("  ⚠ Some libraries failed. Check your internet connection.")
    port=find_free_port(5000)
    print(f"\n  ✓ Running at  http://localhost:{port}")
    print(f"  ✓ Database:   {DB}")
    print(f"  ✓ Uploads:    {UPLOAD_DIR}")
    print(f"\n  Demo: alice@dev.io / pass123 (Admin)")
    print(f"  New company? Click 'Create Account' → 'New Workspace'")
    print(f"  Invite others? Share your code from Settings ⚙\n")
    threading.Thread(target=open_browser,args=(port,),daemon=True).start()
    app.run(host="0.0.0.0",port=port,debug=False,use_reloader=False)
