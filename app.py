#!/usr/bin/env python3
"""
ProjectFlowPro v4.0
Multi-tenant workspaces | AI Assistant | Stage Dropdown | Direct Messages
"""
import os, sys, json, hashlib, secrets, random, urllib.request, urllib.error
import socket, threading, time, webbrowser, mimetypes, base64, smtplib
from datetime import datetime
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
    # INSERT OR IGNORE → ON CONFLICT DO NOTHING
    if "INSERT OR IGNORE INTO" in sql:
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO").rstrip()
        sql += " ON CONFLICT DO NOTHING"
    # INSERT OR REPLACE for push_subscriptions
    if "INSERT OR REPLACE INTO push_subscriptions" in sql:
        sql = sql.replace("INSERT OR REPLACE INTO push_subscriptions",
                          "INSERT INTO push_subscriptions").rstrip()
        sql += (" ON CONFLICT (endpoint) DO UPDATE SET "
                "p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth, "
                "created=EXCLUDED.created")
    # Convert ? → :p0, :p1, ... and build named params dict
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
        # pg8000 native: pass named params as **kwargs (:p0, :p1, ...)
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
    # Prefer env var (Railway sets this); fall back to file for local dev
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

CLRS=["#7c3aed","#2563eb","#059669","#d97706","#dc2626","#ec4899","#0891b2","#aaff00"]

def get_db(autocommit=False):
    conn = pg8000.native.Connection(**_parse_db_url(DATABASE_URL))
    conn.autocommit = autocommit  # pg8000 supports autocommit property
    return _DB(conn)
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
        # Legacy sha256 hash — verify then silently upgrade to bcrypt
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
    subject = "ProjectFlow — Your Login Code"
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background:#f4f4f4; padding:20px;">
      <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
        <div style="background:#0a1a00;padding:24px 32px;text-align:center;">
          <h1 style="color:#aaff00;margin:0;font-size:22px;letter-spacing:-0.5px;">ProjectFlowPro</h1>
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
          <p style="color:#aaa;font-size:11px;margin:0;">ProjectFlow · Team Project Management</p>
        </div>
      </div>
    </body>
    </html>
    """
    # Try workspace SMTP first, then fallback to global
    try:
        send_email(to_email, subject, body)
        return True
    except Exception as e:
        print(f"[OTP] Email send error: {e}")
        return False
def ts(): return datetime.utcnow().isoformat() + 'Z'

# ── Email Configuration & Function ────────────────────────────────────────────
# Configure these environment variables or modify directly:
EMAIL_ENABLED = os.environ.get('EMAIL_ENABLED', 'true').lower() == 'true'
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
FROM_EMAIL = os.environ.get('FROM_EMAIL', SMTP_USERNAME)
APP_URL = os.environ.get('APP_URL', 'http://localhost:5000')

def send_email(to_email, subject, body_html, workspace_id=None):
    """Send an email notification using workspace-specific SMTP settings"""
    # Get workspace email settings from database
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
    
    # Fall back to environment variables if no workspace config
    if not smtp_config or not smtp_config.get('username') or not smtp_config.get('password'):
        if not SMTP_USERNAME or not SMTP_PASSWORD:
            print(f"[Email] Skipped (not configured): {subject} -> {to_email}")
            return False
        smtp_config = {
            'server': SMTP_SERVER,
            'port': SMTP_PORT,
            'username': SMTP_USERNAME,
            'password': SMTP_PASSWORD,
            'from_email': FROM_EMAIL or SMTP_USERNAME
        }
    
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = smtp_config['from_email']
        msg['To'] = to_email
        
        html_part = MIMEText(body_html, 'html')
        msg.attach(html_part)
        
        with smtplib.SMTP(smtp_config['server'], smtp_config['port'], timeout=30) as server:
            server.starttls()
            server.login(smtp_config['username'], smtp_config['password'])
            server.send_message(msg)
        
        print(f"[Email] Sent: {subject} -> {to_email}")
        return True
    except socket.timeout:
        print(f"[Email] Timeout connecting to {smtp_config['server']}:{smtp_config['port']}")
        return False
    except smtplib.SMTPAuthenticationError:
        print(f"[Email] Authentication failed - check username/password")
        return False
    except Exception as e:
        print(f"[Email] Error sending to {to_email}: {e}")
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
            <p style="color: #6b7280; font-size: 12px; margin-top: 30px;">ProjectFlow Notification System</p>
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
            <p style="color: #6b7280; font-size: 12px; margin-top: 30px;">ProjectFlow Notification System</p>
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
            <p style="color: #6b7280; font-size: 12px; margin-top: 30px;">ProjectFlow Notification System</p>
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
    # Generate new P-256 key pair using pure Python (no pywebpush required)
    try:
        import struct
        # Use os.urandom for the private key scalar (32 bytes)
        priv_bytes = os.urandom(32)
        priv_hex = priv_bytes.hex()
        # For VAPID public key we use a placeholder — real ECDH done by pywebpush if available
        # Store both; real push requires pywebpush or similar
        keys = {"private": priv_hex, "public": "", "generated": ts()}
        # Try to derive real public key using cryptography library
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
            # If push fails (e.g. subscription expired), mark for cleanup
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
                team_id TEXT DEFAULT '');
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
        # Add teams table if not exists (migration)
        try: db.execute('''CREATE TABLE IF NOT EXISTS teams (
            id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
            lead_id TEXT, member_ids TEXT DEFAULT '[]', created TEXT)''')
        except: pass
        # Add tickets tables if not exists (migration)
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
        # Add team_id to projects/tasks/tickets if not exists (migration)
        try: db.execute("ALTER TABLE projects ADD COLUMN team_id TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE tickets ADD COLUMN team_id TEXT DEFAULT ''")
        except: pass
        # Add team_id to tasks if not exists (migration)
        try: db.execute("ALTER TABLE tasks ADD COLUMN team_id TEXT DEFAULT ''")
        except: pass
        # Add is_system column to messages if not exists (migration)
        try: db.execute("ALTER TABLE messages ADD COLUMN is_system INTEGER DEFAULT 0")
        except: pass
        # Add avatar_data column for profile photos
        try: db.execute("ALTER TABLE users ADD COLUMN avatar_data TEXT")
        except: pass
        try: db.execute("ALTER TABLE users ADD COLUMN plain_password TEXT DEFAULT ''")
        except: pass
        # Fix corrupted avatar column: if avatar contains base64 image data, move it to avatar_data and reset avatar to initials
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
        # Add email configuration columns to workspaces (migration)
        try: db.execute("ALTER TABLE workspaces ADD COLUMN otp_enabled INTEGER DEFAULT 0")
        except: pass
        try: 
            db.execute("ALTER TABLE workspaces ADD COLUMN smtp_server TEXT")
            db.execute("ALTER TABLE workspaces ADD COLUMN smtp_port INTEGER DEFAULT 587")
            db.execute("ALTER TABLE workspaces ADD COLUMN smtp_username TEXT")
            db.execute("ALTER TABLE workspaces ADD COLUMN smtp_password TEXT")
            db.execute("ALTER TABLE workspaces ADD COLUMN from_email TEXT")
            db.execute("ALTER TABLE workspaces ADD COLUMN email_enabled INTEGER DEFAULT 1")
        except: pass
        # Migrate legacy data (no workspace_id)
        existing_ws = db.execute("SELECT id FROM workspaces LIMIT 1").fetchone()
        if not existing_ws:
            # Check if legacy users exist (without workspace_id)
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
        # If password was legacy sha256, silently upgrade to bcrypt
        if not (u["password"].startswith("$2b$") or u["password"].startswith("$2a$")):
            try:
                new_hash = hash_pw(password)
                db.execute("UPDATE users SET password=? WHERE id=?",(new_hash, u["id"]))
            except Exception: pass
        # Check if OTP is enabled for this workspace
        ws = db.execute("SELECT * FROM workspaces WHERE id=?",(u["workspace_id"],)).fetchone()
        otp_enabled = ws and ws.get("otp_enabled", 0)
        if otp_enabled:
            # Check if SMTP is configured
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
                # If email fails, fall through to direct login
        # No OTP — log in directly
        session.permanent=True
        session["user_id"]=u["id"]
        session["workspace_id"]=u["workspace_id"]
        return jsonify(dict(u))

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
        # Valid — clear OTP and create session
        del _otp_store[email]
    with get_db() as db:
        u=db.execute("SELECT * FROM users WHERE id=?",(entry["user_id"],)).fetchone()
        if not u: return jsonify({"error":"User not found"}),404
        session.permanent=True
        session["user_id"]=u["id"]
        session["workspace_id"]=u["workspace_id"]
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
        # Rate limit — don't resend if < 60 seconds since last
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
def logout(): session.clear(); return jsonify({"ok":True})

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

@app.route("/api/auth/me")
def me():
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
        # Email settings
        if "smtp_server" in d: db.execute("UPDATE workspaces SET smtp_server=? WHERE id=?",(d["smtp_server"],wid()))
        if "smtp_port" in d: db.execute("UPDATE workspaces SET smtp_port=? WHERE id=?",(d["smtp_port"],wid()))
        if "smtp_username" in d: db.execute("UPDATE workspaces SET smtp_username=? WHERE id=?",(d["smtp_username"],wid()))
        if "smtp_password" in d: db.execute("UPDATE workspaces SET smtp_password=? WHERE id=?",(d["smtp_password"],wid()))
        if "from_email" in d: db.execute("UPDATE workspaces SET from_email=? WHERE id=?",(d["from_email"],wid()))
        if "email_enabled" in d: db.execute("UPDATE workspaces SET email_enabled=? WHERE id=?",(1 if d["email_enabled"] else 0,wid()))
        if "otp_enabled" in d: db.execute("UPDATE workspaces SET otp_enabled=? WHERE id=?",(1 if d["otp_enabled"] else 0,wid()))
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
    
    subject="ProjectFlow Email Test"
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
            <p style="color: #6b7280; font-size: 12px; margin-top: 30px;">ProjectFlow Notification System</p>
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
        rows = db.execute("SELECT * FROM users WHERE workspace_id=? ORDER BY name",(wid(),)).fetchall()
        # Determine caller role for field visibility
        caller = db.execute("SELECT role FROM users WHERE id=?", (session["user_id"],)).fetchone()
        caller_role = caller["role"] if caller else "Developer"
        can_see_passwords = caller_role in ("Admin", "Manager")
        users = []
        for r in rows:
            u = dict(r)
            u.pop('avatar_data', None)
            u.pop('password', None)
            if not can_see_passwords:
                u.pop('plain_password', None)  # only Admin/Manager can see passwords
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
    with get_db() as db:
        if team_id:
            # Return projects directly assigned to this team
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
                    d.get("color","#aaff00"),ts(),d.get("team_id","")))
        p=db.execute("SELECT * FROM projects WHERE id=?",(pid,)).fetchone()
        # Notify all members except creator — DB notif + Web Push
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
        # Notify all project members about the update
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
            # Get the team's member list to find tasks by member assignment too
            team = db.execute("SELECT member_ids FROM teams WHERE id=? AND workspace_id=?",(team_id,wid())).fetchone()
            member_ids = json.loads(team["member_ids"] if team else "[]")
            # Get projects belonging to this team
            team_projects = db.execute(
                "SELECT id FROM projects WHERE workspace_id=? AND team_id=?",(wid(),team_id)).fetchall()
            proj_ids = [p["id"] for p in team_projects]
            # Build query: tasks with team_id match OR assignee in team OR project in team
            all_tasks = db.execute(
                "SELECT * FROM tasks WHERE workspace_id=? ORDER BY created DESC",(wid(),)).fetchall()
            proj_set = set(proj_ids)
            mem_set = set(member_ids)
            filtered = [t for t in all_tasks if
                (t["team_id"] and t["team_id"]==team_id) or
                (t["assignee"] and t["assignee"] in mem_set) or
                (t["project"] and t["project"] in proj_set)]
            return jsonify([dict(r) for r in filtered])
        return jsonify([dict(r) for r in db.execute(
            "SELECT * FROM tasks WHERE workspace_id=? ORDER BY created DESC",(wid(),)).fetchall()])

def next_task_id(db, ws):
    # Use timestamp-based ID to prevent collisions between gunicorn workers
    import time
    base = int(time.time() * 1000)
    # Also embed a sequential number for readability
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
        # Notify assignee (if different from creator)
        if d.get("assignee") and d["assignee"]!=session["user_id"]:
            nid=f"n{base_ts}"
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"task_assigned",f"{cname} assigned you to '{d['title']}'",d["assignee"],0,ts()))
            # Send email notification
            assignee_user=db.execute("SELECT name,email FROM users WHERE id=?",(d["assignee"],)).fetchone()
            if assignee_user and assignee_user["email"]:
                threading.Thread(target=send_task_assigned_email,
                    args=(assignee_user["email"],assignee_user["name"],d["title"],cname,tid,wid()),
                    daemon=True).start()
            # Web Push — assignee
            threading.Thread(target=push_notification_to_user,
                args=(db, d["assignee"], f"✅ New task assigned: {d['title']}",
                      f"{cname} assigned you this task [{d.get('priority','medium')}]", "/"),
                daemon=True).start()
        # Notify all other project members about the new task
        if d.get("project"):
            proj=db.execute("SELECT name,members FROM projects WHERE id=? AND workspace_id=?",(d["project"],wid())).fetchone()
            if proj:
                try:
                    members=json.loads(proj["members"] or "[]")
                except: members=[]
                for i,uid in enumerate(members):
                    # Skip creator and assignee (already notified above)
                    if uid==session["user_id"] or uid==d.get("assignee"): continue
                    nid2=f"n{base_ts+10+i}"
                    db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                               (nid2,wid(),"task_assigned",f"{cname} created task '{d['title']}' in {proj['name']}",uid,0,ts()))
                    threading.Thread(target=push_notification_to_user,
                        args=(db, uid, f"📋 New task in {proj['name']}",
                              f"{cname} created '{d['title']}'", "/"),
                        daemon=True).start()
        t=db.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone()
        # Auto-post system message to project channel
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

        # Permission check:
        # - Admin and Manager: full access always
        # - TeamLead: can fully edit any task in the workspace
        # - Assignee of the task: can update stage/pct only
        # - Project owner: full edit access for tasks in their project
        # - Everyone else: no access
        is_admin_manager = cu_role in ("Admin","Manager")
        is_teamlead = cu_role == "TeamLead"
        is_assignee = t["assignee"] == session["user_id"]
        proj = db.execute("SELECT owner FROM projects WHERE id=? AND workspace_id=?",(t["project"],wid())).fetchone() if t["project"] else None
        is_proj_owner = proj and proj["owner"] == session["user_id"]

        if not (is_admin_manager or is_teamlead or is_proj_owner):
            if is_assignee:
                # Assignee can update stage, pct, and comments
                allowed={"stage","pct","comments"}
                if any(k not in allowed for k in d.keys()):
                    return jsonify({"error":"You can only update stage, progress, and comments on tasks assigned to you."}),403
            else:
                return jsonify({"error":"You do not have permission to edit this task. Only the assignee, project owner, or managers can edit tasks."}),403

        old_stage=t["stage"]
        old_stage=t["stage"]
        db.execute("""UPDATE tasks SET title=?,description=?,project=?,assignee=?,
                      priority=?,stage=?,due=?,pct=?,comments=?,team_id=? WHERE id=? AND workspace_id=?""",
                   (d.get("title",t["title"]),d.get("description",t["description"]),
                    d.get("project",t["project"]),d.get("assignee",t["assignee"]),
                    d.get("priority",t["priority"]),d.get("stage",t["stage"]),
                    d.get("due",t["due"]),d.get("pct",t["pct"]),
                    json.dumps(d.get("comments",json.loads(t["comments"]))),
                    d.get("team_id",t["team_id"] if "team_id" in t.keys() else ""),
                    tid,wid()))
        if d.get("stage") and d["stage"]!=old_stage:
            base_ts2=int(datetime.now().timestamp()*1000)
            # Notify assignee
            if t["assignee"] and t["assignee"]!=session["user_id"]:
                nid=f"n{base_ts2}"
                db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                           (nid,wid(),"status_change",f"Task '{t['title']}' moved to {d['stage']}",
                            t["assignee"],0,ts()))
                # Send email notification
                assignee_user=db.execute("SELECT name,email FROM users WHERE id=?",(t["assignee"],)).fetchone()
                changer_user=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
                changer_name=changer_user["name"] if changer_user else "Someone"
                if assignee_user and assignee_user["email"]:
                    threading.Thread(target=send_status_change_email,
                        args=(assignee_user["email"],assignee_user["name"],t["title"],d["stage"],changer_name,wid()),
                        daemon=True).start()
                # Web Push — assignee
                threading.Thread(target=push_notification_to_user,
                    args=(db, t["assignee"], f"🔄 Task updated: {t['title']}",
                          f"{changer_name} moved it to {d['stage']}", "/"),
                    daemon=True).start()
            # Also notify project members (owner/creator etc)
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
        # Post new comments to channel
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
            # Notify assignee about comment
            if t["assignee"] and t["assignee"]!=session["user_id"]:
                nid2=f"n{int(datetime.now().timestamp()*1000)+4}"
                db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                           (nid2,wid(),"comment",f"{cname} commented on '{t['title']}': {latest.get('text','')}",
                            t["assignee"],0,ts()))
                # Send email notification
                assignee_user=db.execute("SELECT name,email FROM users WHERE id=?",(t["assignee"],)).fetchone()
                if assignee_user and assignee_user["email"]:
                    threading.Thread(target=send_comment_email,
                        args=(assignee_user["email"],assignee_user["name"],t["title"],cname,latest.get('text',''),wid()),
                        daemon=True).start()
                # Web Push — comment
                threading.Thread(target=push_notification_to_user,
                    args=(db, t["assignee"], f"💬 Comment on: {t['title']}",
                          f"{cname}: {latest.get('text','')[:80]}", "/"),
                    daemon=True).start()
        return jsonify(dict(db.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone()))

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
        # Notify all OTHER workspace members about new channel message
        sender=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
        sender_name=sender["name"] if sender else "Someone"
        project_row=db.execute("SELECT name FROM projects WHERE id=? AND workspace_id=?",(d.get("project",""),wid())).fetchone()
        proj_name=project_row["name"] if project_row else "a project"
        preview=d.get("content","")[:60]+("..." if len(d.get("content",""))>60 else "")
        # Get all workspace members except sender
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
        # Also push a notification to the recipient
        sender=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
        sender_name=sender["name"] if sender else "Someone"
        nid=f"n{int(datetime.now().timestamp()*1000)}"
        preview=d["content"][:60]+"..." if len(d["content"])>60 else d["content"]
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
        # Confirm push to the user who set the reminder
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
        # Confirm push — reminder rescheduled
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
        # All tasks assigned to any team member OR tagged with this team
        all_tasks=db.execute("SELECT * FROM tasks WHERE workspace_id=?",(wid(),)).fetchall()
        team_tasks=[t for t in all_tasks if t["assignee"] in member_ids or (t["team_id"] if "team_id" in t.keys() else "")==tid]
        # Projects touched by this team
        proj_ids=list({t["project"] for t in team_tasks if t["project"]})
        projects=[]
        for pid in proj_ids:
            p=db.execute("SELECT * FROM projects WHERE id=? AND workspace_id=?",(pid,wid())).fetchone()
            if p: projects.append(dict(p))
        # Per-member stats
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
    status=request.args.get("status","")
    team_id=request.args.get("team_id","")
    with get_db() as db:
        if team_id:
            # Get team member ids for filtering by assignee
            team=db.execute("SELECT member_ids FROM teams WHERE id=? AND workspace_id=?",(team_id,wid())).fetchone()
            member_ids=json.loads(team["member_ids"] if team else "[]")
            # Get projects belonging to this team
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
        # Notify assignee
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
        # Developers can only update status (move ticket along workflow)
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
        for r in rooms:
            rd=dict(r)
            try:
                created=datetime.fromisoformat(rd['created'].replace('Z',''))
                if (datetime.utcnow()-created).total_seconds()>28800:
                    db.execute("UPDATE call_rooms SET status='ended' WHERE id=?",(rd['id'],))
                    continue
            except: pass
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
        room_name=d.get("name",f"{cname}'s Huddle")
        db.execute("INSERT INTO call_rooms VALUES (?,?,?,?,?,?,?)",
                   (room_id,wid(),room_name,session["user_id"],json.dumps([session["user_id"]]),"active",ts()))
        users=db.execute("SELECT id FROM users WHERE workspace_id=? AND id!=?",(wid(),session["user_id"])).fetchall()
        for u in users:
            nid=f"n{int(datetime.now().timestamp()*1000)}{u['id']}"
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"call",f"📞 {cname} started a Huddle — Join now! ({room_name})",u["id"],0,ts()))
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
                   (nid,wid(),"call",f"📞 {cname} is pulling you into: {room['name']} — Join now!",target_id,0,ts()))
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
        # Clean up old consumed signals (keep last 200 per room)
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

        # Build context
        projects=db.execute("SELECT id,name,description,target_date,color FROM projects WHERE workspace_id=?",(wid(),)).fetchall()
        tasks=db.execute("SELECT id,title,stage,priority,assignee,project,due,pct FROM tasks WHERE workspace_id=?",(wid(),)).fetchall()
        users=db.execute("SELECT id,name,role FROM users WHERE workspace_id=?",(wid(),)).fetchall()
        cu=db.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone()

    proj_ctx="\n".join([f"- {p['name']} (id:{p['id']}, due:{p['target_date']})" for p in projects])
    task_ctx="\n".join([f"- [{t['id']}] {t['title']} | stage:{t['stage']} | priority:{t['priority']} | pct:{t['pct']}%" for t in tasks])
    user_ctx="\n".join([f"- {u['name']} (id:{u['id']}, role:{u['role']})" for u in users])

    system=f"""You are an AI assistant for ProjectFlow — a project management tool used by the workspace "{ws['name'] if ws else 'Unknown'}".
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

    # Parse and execute actions
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
                # Normalize keys (strip whitespace)
                row = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
                # --- Project auto-creation ---
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
                # --- Task creation ---
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
                # Resolve assignee by name if given
                assignee_id = row.get("assignee_id", row.get("assignee", "")).strip()
                if assignee_id and not assignee_id.startswith("u"):
                    # Try to match by name
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
    # Try local file first (exists when running locally with download_js())
    path=os.path.join(JS_DIR,fn)
    if os.path.exists(path) and os.path.getsize(path)>1000:
        mime,_=mimetypes.guess_type(fn)
        return Response(open(path,"rb").read(),mimetype=mime or "application/javascript",
                        headers={"Cache-Control":"public,max-age=86400"})
    # Map to stable CDN URLs — use cdnjs (more reliable on Railway/cloud)
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
// ProjectFlow Service Worker v2
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
  const title  = data.title  || 'ProjectFlowPro';
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
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
      // Focus existing tab if open
      for (const c of cs) {
        if (c.url.includes(self.location.origin) && 'focus' in c) {
          c.focus();
          c.postMessage({ type: 'PF_NAVIGATE', url });
          return;
        }
      }
      // Otherwise open new window
      if (clients.openWindow) return clients.openWindow(url);
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
    """PWA manifest for installability."""
    manifest = {
        "name": "ProjectFlowPro",
        "short_name": "ProjectFlowPro",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#111111",
        "theme_color": "#aaff00",
        "icons": [
            {"src": "/favicon.ico", "sizes": "any", "type": "image/x-icon"}
        ]
    }
    return jsonify(manifest)

@app.route("/",defaults={"p":""})
@app.route("/<path:p>")
def root(p):
    action=request.args.get("action","")
    # Serve React app for app actions or any sub-path
    if action in ("login","register") or p!="":
        return HTML
    # Serve landing page at bare /
    return LANDING_HTML

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>ProjectFlowPro — Team Project Management & AI Assistant</title>
<meta name="description" content="ProjectFlowPro v4.0 — Multi-tenant workspaces, AI assistant, real-time collaboration, huddle calls, timeline tracking, and developer productivity analytics."/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet"/>
<style>
:root{
  --bg:#ffffff;--sf:#f8fafc;--sf2:#f1f5f9;--bd:rgba(37,99,235,0.12);--bd2:rgba(0,0,0,0.06);
  --ac:#2563eb;--ac2:#1d4ed8;--ac3:rgba(37,99,235,0.08);
  --tx:#0a0f1e;--tx2:#475569;--tx3:#94a3b8;
  --ocean1:#e0f2fe;--ocean2:#bae6fd;--ocean3:#7dd3fc;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:#ffffff;color:var(--tx);font-family:'DM Sans',sans-serif;font-size:16px;line-height:1.65;overflow-x:hidden;}
h1,h2,h3,h4{font-family:'Syne',sans-serif;line-height:1.15;}

/* NAV */
nav{position:fixed;top:0;left:0;right:0;z-index:200;height:56px;display:flex;align-items:center;background:rgba(255,255,255,0.88);backdrop-filter:blur(20px);border-bottom:1px solid rgba(0,0,0,0.06);transition:background .3s;}
.nav-inner{max-width:1160px;margin:0 auto;padding:0 28px;width:100%;display:flex;align-items:center;justify-content:space-between;}
.logo{font-family:'Syne',sans-serif;font-size:1.15rem;font-weight:800;color:var(--tx);text-decoration:none;display:flex;align-items:center;gap:8px;letter-spacing:-.02em;}
.logo-icon{width:28px;height:28px;border-radius:7px;background:var(--ac);display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(37,99,235,0.3);}
.nav-links{display:flex;align-items:center;gap:4px;list-style:none;}
.nav-links a{color:var(--tx2);text-decoration:none;font-size:.875rem;padding:6px 12px;border-radius:7px;transition:all .18s;}
.nav-links a:hover{color:var(--tx);background:var(--sf2);}
.nav-pill{background:var(--ac3);color:var(--ac)!important;border:1px solid var(--bd);}
.nav-pill:hover{background:rgba(37,99,235,0.12)!important;}
.btn{display:inline-flex;align-items:center;gap:7px;border:none;cursor:pointer;font-family:'DM Sans',sans-serif;font-weight:600;transition:all .18s;text-decoration:none;white-space:nowrap;}
.btn-primary{background:var(--ac);color:#fff;padding:9px 20px;border-radius:9px;font-size:.875rem;box-shadow:0 2px 10px rgba(37,99,235,0.25);}
.btn-primary:hover{background:var(--ac2);box-shadow:0 4px 18px rgba(37,99,235,0.35);transform:translateY(-1px);}
.btn-outline{background:transparent;color:var(--tx);padding:9px 20px;border-radius:9px;font-size:.875rem;border:1.5px solid var(--bd2);}
.btn-outline:hover{border-color:rgba(0,0,0,0.15);background:var(--sf);}
.nav-cta{display:flex;gap:8px;align-items:center;}

/* HERO */
.hero{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:80px 28px 0;position:relative;overflow:hidden;}
#hero-canvas{position:absolute;inset:0;width:100%;height:100%;display:block;z-index:0;}
.hero-content{position:relative;z-index:2;text-align:center;max-width:760px;margin:0 auto;}
.hero-badge{display:inline-flex;align-items:center;gap:7px;background:rgba(37,99,235,0.07);border:1px solid rgba(37,99,235,0.18);padding:5px 14px;border-radius:100px;font-size:.75rem;font-weight:700;color:var(--ac);margin-bottom:24px;letter-spacing:.05em;text-transform:uppercase;}
.hero-badge-dot{width:5px;height:5px;background:var(--ac);border-radius:50%;}
.hero h1{font-size:clamp(2.4rem,5.5vw,4rem);font-weight:800;color:var(--tx);margin-bottom:18px;letter-spacing:-.04em;line-height:1.1;}
.hero h1 .blue{color:var(--ac);}
.hero-sub{font-size:1.05rem;color:var(--tx2);max-width:520px;margin:0 auto 36px;font-weight:300;line-height:1.75;}
.hero-actions{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-bottom:14px;}
.btn-xl{padding:13px 28px!important;font-size:.96rem!important;border-radius:11px!important;}
.hero-note{font-size:.78rem;color:var(--tx3);}
.hero-note span{color:var(--ac);font-weight:600;}

/* APP MOCKUP */
.hero-mockup{position:relative;z-index:2;width:100%;max-width:900px;margin:48px auto 0;}
.mockup-frame{border-radius:16px 16px 0 0;overflow:hidden;box-shadow:0 -4px 60px rgba(37,99,235,0.12),0 0 0 1px rgba(0,0,0,0.06);background:#fff;}
.mockup-topbar{height:36px;background:#f8fafc;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;padding:0 14px;gap:6px;}
.m-dot{width:9px;height:9px;border-radius:50%;}
.mockup-url{flex:1;margin:0 10px;height:18px;background:#f1f5f9;border-radius:5px;display:flex;align-items:center;padding:0 8px;}
.mockup-url-txt{font-size:.6rem;color:#94a3b8;font-family:monospace;}
.mockup-body{display:flex;height:360px;}
.m-sidebar{width:180px;flex-shrink:0;background:#fafbfc;border-right:1px solid #f1f5f9;padding:12px 10px;display:flex;flex-direction:column;gap:2px;}
.m-ws{display:flex;align-items:center;gap:7px;padding:7px 8px;margin-bottom:6px;border-radius:7px;background:#f1f5f9;}
.m-ws-av{width:26px;height:26px;border-radius:6px;background:var(--ac);display:flex;align-items:center;justify-content:center;font-size:.58rem;font-weight:800;color:#fff;flex-shrink:0;}
.m-ws-n{font-size:.72rem;font-weight:700;color:var(--tx);}
.m-ws-s{font-size:.62rem;color:var(--tx3);}
.m-sec{font-size:.58rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--tx3);padding:8px 8px 3px;}
.m-nav{display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:6px;font-size:.72rem;color:var(--tx2);}
.m-nav.act{background:rgba(37,99,235,0.08);color:var(--ac);}
.m-badge{margin-left:auto;background:var(--ac);color:#fff;font-size:.55rem;font-weight:700;padding:1px 5px;border-radius:100px;}
.m-main{flex:1;overflow:hidden;display:flex;flex-direction:column;}
.m-header{padding:11px 16px;border-bottom:1px solid #f1f5f9;display:flex;align-items:center;justify-content:space-between;}
.m-title{font-family:'Syne',sans-serif;font-size:.82rem;font-weight:700;color:var(--tx);}
.m-tabs{display:flex;gap:2px;}
.m-tab{font-size:.62rem;padding:3px 9px;border-radius:5px;color:var(--tx3);}
.m-tab.act{background:rgba(37,99,235,0.08);color:var(--ac);}
.m-board{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;padding:12px 14px;height:100%;align-content:start;}
.m-col{background:#f8fafc;border:1px solid #f1f5f9;border-radius:7px;padding:8px;}
.m-col-h{font-size:.6rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--tx3);margin-bottom:7px;display:flex;justify-content:space-between;}
.m-col-c{width:15px;height:15px;border-radius:4px;background:#f1f5f9;display:flex;align-items:center;justify-content:center;font-size:.55rem;color:var(--tx3);}
.m-card{background:#fff;border:1px solid #f1f5f9;border-radius:6px;padding:7px;margin-bottom:6px;}
.m-card-t{font-size:.66rem;font-weight:500;color:var(--tx);margin-bottom:5px;line-height:1.4;}
.m-pill{display:inline-block;font-size:.54rem;padding:2px 5px;border-radius:100px;font-weight:600;}
.pill-h{background:rgba(239,68,68,.08);color:#ef4444;}
.pill-m{background:rgba(245,158,11,.08);color:#f59e0b;}
.pill-d{background:rgba(37,99,235,.08);color:#2563eb;}
.pill-ok{background:rgba(34,197,94,.08);color:#16a34a;}
.m-prog{height:2px;background:#f1f5f9;border-radius:100px;margin-top:6px;}
.m-prog-fill{height:100%;border-radius:100px;background:var(--ac);}
.m-prog-fill.g{background:#16a34a;}
.m-prog-fill.o{background:#f59e0b;}

/* STATS BAR */
.stats-bar{padding:40px 0;background:#f8fafc;border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;}
.stats-grid{max-width:1160px;margin:0 auto;padding:0 28px;display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#e2e8f0;border-radius:12px;overflow:hidden;}
.stat-item{background:#f8fafc;text-align:center;padding:24px 16px;}
.stat-num{font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;line-height:1;margin-bottom:5px;color:var(--ac);}
.stat-lbl{font-size:.82rem;color:#475569;font-weight:500;}

/* SECTIONS */
section{padding:80px 0;}
.wrap{max-width:1160px;margin:0 auto;padding:0 28px;}
.sec-tag{display:inline-block;font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ac);margin-bottom:10px;background:var(--ac3);padding:3px 11px;border-radius:100px;border:1px solid var(--bd);}
.sec-title{font-size:clamp(1.7rem,3vw,2.2rem);font-weight:800;max-width:540px;margin-bottom:10px;letter-spacing:-.025em;color:var(--tx);}
.sec-sub{color:var(--tx2);font-size:.95rem;max-width:500px;margin-bottom:44px;font-weight:300;line-height:1.75;}
.centered{text-align:center;}.centered .sec-title,.centered .sec-sub{margin-left:auto;margin-right:auto;}

/* FEATURES BENTO */
.bento{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}
.bc{background:#fff;border:1.5px solid #f1f5f9;border-radius:14px;padding:24px;transition:all .22s;cursor:default;position:relative;overflow:hidden;}
.bc:hover{border-color:rgba(37,99,235,0.2);box-shadow:0 8px 32px rgba(37,99,235,0.08);transform:translateY(-2px);}
.bc.span2{grid-column:span 2;}
.bc.featured{border-color:rgba(37,99,235,0.15);background:linear-gradient(135deg,rgba(37,99,235,0.03) 0%,#fff 60%);}
.b-ico{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px;margin-bottom:13px;background:var(--ac3);border:1px solid var(--bd);}
.bc h3{font-size:1rem;font-weight:700;margin-bottom:8px;color:#0a0f1e;}
.bc p{font-size:.88rem;color:#334155;line-height:1.68;}
.feat-list{list-style:none;margin-top:12px;display:flex;flex-direction:column;gap:6px;}
.feat-list li{display:flex;align-items:flex-start;gap:8px;font-size:.84rem;color:#334155;}
.feat-list li::before{content:'';width:4px;height:4px;border-radius:50%;background:var(--ac);flex-shrink:0;margin-top:7px;}
.role-badge{font-size:.64rem;background:rgba(37,99,235,.08);color:var(--ac);padding:2px 8px;border-radius:100px;margin-left:5px;border:1px solid var(--bd);font-weight:600;}

/* MODULES */
.modules-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:40px;}
.mod-card{background:#fff;border:1.5px solid #f1f5f9;border-radius:11px;padding:16px 14px;text-align:center;transition:all .2s;}
.mod-card:hover{border-color:rgba(37,99,235,.15);transform:translateY(-1px);box-shadow:0 4px 16px rgba(37,99,235,.07);}
.mod-ico{font-size:1.6rem;margin-bottom:8px;}
.mod-n{font-size:.84rem;font-weight:700;margin-bottom:3px;color:#0a0f1e;}
.mod-d{font-size:.76rem;color:#475569;line-height:1.5;}

/* HOW IT WORKS */
.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;margin-top:44px;}
.step{text-align:center;}
.step-num{width:44px;height:44px;border-radius:50%;border:2px solid var(--bd);display:flex;align-items:center;justify-content:center;margin:0 auto 14px;font-family:'Syne',sans-serif;font-weight:800;font-size:.9rem;color:var(--ac);background:var(--ac3);}
.step h3{font-size:.95rem;font-weight:700;margin-bottom:6px;color:#0a0f1e;}
.step p{font-size:.86rem;color:#334155;}

/* CTA */
.cta-section{padding:80px 0;background:linear-gradient(135deg,#eff6ff 0%,#f0f9ff 50%,#e0f2fe 100%);}
.cta-box{text-align:center;border:1.5px solid rgba(37,99,235,0.15);border-radius:20px;padding:64px 40px;background:#fff;position:relative;overflow:hidden;box-shadow:0 4px 40px rgba(37,99,235,0.08);}
.cta-box::before{content:'';position:absolute;top:0;left:50%;transform:translateX(-50%);width:240px;height:2px;background:linear-gradient(90deg,transparent,var(--ac),transparent);}
.cta-box h2{font-size:clamp(1.7rem,3vw,2.3rem);font-weight:800;margin-bottom:12px;color:var(--tx);letter-spacing:-.03em;}
.cta-box p{color:var(--tx2);margin-bottom:30px;font-size:.96rem;max-width:440px;margin-left:auto;margin-right:auto;}
.cta-actions{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-bottom:18px;}
.trust-row{display:flex;justify-content:center;align-items:center;gap:18px;flex-wrap:wrap;}
.trust-item{display:flex;align-items:center;gap:5px;font-size:.78rem;color:var(--tx3);}
.trust-item span{color:var(--ac);font-weight:600;}

/* FOOTER */
footer{padding:48px 0 32px;border-top:1px solid #e2e8f0;background:#fff;}
.footer-top{display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr;gap:36px;margin-bottom:40px;}
.footer-brand p{font-size:.82rem;color:var(--tx3);margin-top:9px;line-height:1.7;max-width:230px;}
.footer-col h4{font-size:.75rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--tx2);margin-bottom:12px;}
.footer-col ul{list-style:none;display:flex;flex-direction:column;gap:7px;}
.footer-col a{color:var(--tx3);font-size:.82rem;text-decoration:none;transition:color .18s;}
.footer-col a:hover{color:var(--tx2);}
.footer-bottom{display:flex;align-items:center;justify-content:space-between;padding-top:20px;border-top:1px solid #f1f5f9;flex-wrap:wrap;gap:10px;}
.footer-copy{color:var(--tx3);font-size:.78rem;}
.footer-badges{display:flex;gap:6px;}
.fb{font-size:.7rem;padding:3px 9px;border-radius:100px;border:1px solid #e2e8f0;color:var(--tx3);}

/* TICKER */
.ticker-wrap{overflow:hidden;border-top:1px solid #f1f5f9;border-bottom:1px solid #f1f5f9;padding:10px 0;background:#fafbfc;}
.ticker{display:flex;animation:ticker 32s linear infinite;}
.ticker-item{display:flex;align-items:center;gap:8px;padding:0 28px;font-size:.76rem;color:var(--tx3);white-space:nowrap;flex-shrink:0;}
.ticker-item .hi{color:var(--ac);font-weight:600;}
.ticker-sep{color:#e2e8f0;margin-left:20px;}
@keyframes ticker{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}

/* ANIMATIONS */
@keyframes fadeUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
.a1{animation:fadeUp .6s .1s both;}.a2{animation:fadeUp .6s .22s both;}.a3{animation:fadeUp .6s .36s both;}
.a4{animation:fadeUp .6s .5s both;}.a5{animation:fadeUp .6s .64s both;}

/* RESPONSIVE */
@media(max-width:960px){.bento{grid-template-columns:1fr 1fr;}.bc.span2{grid-column:span 1;}.modules-grid{grid-template-columns:repeat(2,1fr);}.steps{grid-template-columns:1fr 1fr;}.footer-top{grid-template-columns:1fr 1fr;}.stats-grid{grid-template-columns:repeat(2,1fr);}}
@media(max-width:640px){.nav-links,.nav-cta .btn-outline{display:none;}.bento{grid-template-columns:1fr;}.steps{grid-template-columns:1fr;}.modules-grid{grid-template-columns:repeat(2,1fr);}.footer-top{grid-template-columns:1fr;}.stats-grid{grid-template-columns:1fr 1fr;}.m-board{grid-template-columns:repeat(2,1fr);}.mockup-body{height:240px;}.m-sidebar{width:140px;}}
</style>
</head>
<body>

<nav id="nav">
  <div class="nav-inner">
    <a href="/" class="logo">
      <div class="logo-icon"><svg width="16" height="16" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white"/><circle cx="51" cy="43" r="6" fill="white"/><circle cx="13" cy="43" r="6" fill="white"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/></svg></div>
      ProjectFlowPro
    </a>
    <ul class="nav-links">
      <li><a href="#features">Features</a></li>
      <li><a href="#modules">Modules</a></li>
      <li><a href="#how">How it works</a></li>
      <li><a href="/?action=login" class="nav-pill">Admin Login</a></li>
    </ul>
    <div class="nav-cta">
      <a href="/?action=login" class="btn btn-outline">Sign In</a>
      <a href="/?action=register" class="btn btn-primary">Get Started Free</a>
    </div>
  </div>
</nav>

<!-- HERO with ocean canvas -->
<section class="hero">
  <canvas id="hero-canvas"></canvas>
  <div class="hero-content">
    <div class="hero-badge a1"><span class="hero-badge-dot"></span>ProjectFlowPro v4.0 — AI-Powered</div>
    <h1 class="a2">The workspace your<br/>team <span class="blue">actually uses.</span></h1>
    <p class="hero-sub a3">Multi-tenant workspaces, AI assistant, real-time messaging, voice huddles, timeline tracking, support tickets, and developer analytics — all in one platform.</p>
    <div class="hero-actions a4">
      <a href="/?action=register" class="btn btn-primary btn-xl">Get Started Free →</a>
      <a href="/?action=login" class="btn btn-outline btn-xl">Sign In</a>
    </div>
    <p class="hero-note a4">✓ Free to start &nbsp;·&nbsp; <span>No credit card</span> &nbsp;·&nbsp; Up in 2 minutes</p>
  </div>
  <!-- App mockup -->
  <div class="hero-mockup a5">
    <!-- Tab switcher above mockup -->
    <div style="display:flex;justify-content:center;gap:6px;margin-bottom:12px;">
      <button onclick="showTab('dash')" id="tab-dash" style="padding:6px 16px;border-radius:8px;border:1.5px solid #2563eb;background:#2563eb;color:#fff;font-size:.78rem;font-weight:700;cursor:pointer;font-family:inherit;transition:all .18s;">Dashboard</button>
      <button onclick="showTab('proj')" id="tab-proj" style="padding:6px 16px;border-radius:8px;border:1.5px solid #e2e8f0;background:#fff;color:#64748b;font-size:.78rem;font-weight:600;cursor:pointer;font-family:inherit;transition:all .18s;">Projects</button>
    </div>

    <div class="mockup-frame" id="mock-dash">
      <div class="mockup-topbar">
        <div class="m-dot" style="background:#ff5f57"></div>
        <div class="m-dot" style="background:#febc2e"></div>
        <div class="m-dot" style="background:#28c840"></div>
        <div class="mockup-url"><span class="mockup-url-txt">projectflowpro.up.railway.app</span></div>
      </div>
      <!-- Top bar -->
      <div style="display:flex;align-items:center;justify-content:space-between;padding:0 14px;height:32px;background:#0a0f1e;border-bottom:1px solid #1e293b;">
        <div style="display:flex;align-items:center;gap:6px;">
          <div style="width:20px;height:20px;border-radius:5px;background:#2563eb;display:flex;align-items:center;justify-content:center;"><svg width="10" height="10" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="#0a0f1e"/><circle cx="32" cy="11" r="5" fill="#0a0f1e"/><circle cx="51" cy="43" r="5" fill="#0a0f1e"/><circle cx="13" cy="43" r="5" fill="#0a0f1e"/><line x1="32" y1="16" x2="32" y2="23" stroke="#0a0f1e" stroke-width="3" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="#0a0f1e" stroke-width="3" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="#0a0f1e" stroke-width="3" stroke-linecap="round"/></svg></div>
          <span style="font-size:.62rem;font-weight:800;color:#fff;font-family:Syne,sans-serif;">ProjectFlowPro</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
          <span style="font-size:.6rem;color:#94a3b8;">Your Schedule · Mar 20</span>
          <span style="font-size:.6rem;color:#94a3b8;background:#1e293b;padding:2px 8px;border-radius:100px;">No reminders today</span>
          <div style="width:18px;height:18px;border-radius:50%;background:#2563eb;display:flex;align-items:center;justify-content:center;font-size:.55rem;font-weight:700;color:#fff;">A</div>
        </div>
      </div>
      <div class="mockup-body" style="height:320px;">
        <div class="m-sidebar">
          <div class="m-ws"><div class="m-ws-av">PF</div><div><div class="m-ws-n">Acme Corp</div><div class="m-ws-s">12 members</div></div></div>
          <div class="m-sec">Workspace</div>
          <div class="m-nav act">📊 Dashboard</div>
          <div class="m-nav">📁 Projects</div>
          <div class="m-nav">✅ Tasks</div>
          <div class="m-nav">💬 Messages</div>
          <div class="m-sec">Tools</div>
          <div class="m-nav">📅 Timeline</div>
          <div class="m-nav">🎫 Tickets</div>
          <div class="m-nav">👩‍💻 Analytics</div>
        </div>
        <div class="m-main" style="background:#f8fafc;overflow-y:auto;">
          <!-- Page header -->
          <div style="padding:10px 14px 6px;border-bottom:1px solid #f1f5f9;background:#fff;">
            <div style="font-size:.82rem;font-weight:700;color:#0f172a;">Dashboard <span style="font-size:.68rem;font-weight:400;color:#94a3b8;margin-left:4px;">Acme Corp Team Dashboard</span></div>
          </div>
          <!-- Greeting -->
          <div style="margin:8px 10px;padding:8px 12px;background:#fff;border-radius:10px;border:1px solid #f1f5f9;display:flex;align-items:center;justify-content:space-between;">
            <div style="display:flex;align-items:center;gap:8px;">
              <div style="width:24px;height:24px;border-radius:50%;background:#2563eb;display:flex;align-items:center;justify-content:center;font-size:.6rem;font-weight:700;color:#fff;">P</div>
              <div>
                <div style="font-size:.75rem;font-weight:700;color:#0f172a;">Good day, Alex! 👋 <span style="background:#eff6ff;color:#2563eb;font-size:.6rem;padding:1px 7px;border-radius:100px;border:1px solid #bfdbfe;margin-left:3px;">Acme Corp</span></div>
                <div style="font-size:.62rem;color:#64748b;">8 projects · 24 tasks · 6 members · 5 active tasks assigned to you</div>
              </div>
            </div>
          </div>
          <!-- Stats row -->
          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:5px;margin:0 10px 8px;">
            <div style="background:#fff;border:1px solid #f1f5f9;border-radius:8px;padding:6px 8px;border-top:2px solid #2563eb;"><div style="font-size:1rem;font-weight:800;color:#0f172a;">8</div><div style="font-size:.58rem;color:#64748b;">Total Projects</div></div>
            <div style="background:#fff;border:1px solid #f1f5f9;border-radius:8px;padding:6px 8px;border-top:2px solid #059669;"><div style="font-size:1rem;font-weight:800;color:#0f172a;">24</div><div style="font-size:.58rem;color:#64748b;">Active Tasks</div></div>
            <div style="background:#fff;border:1px solid #f1f5f9;border-radius:8px;padding:6px 8px;border-top:2px solid #7c3aed;"><div style="font-size:1rem;font-weight:800;color:#0f172a;">11</div><div style="font-size:.58rem;color:#64748b;">Completed</div></div>
            <div style="background:#fff;border:1px solid #f1f5f9;border-radius:8px;padding:6px 8px;border-top:2px solid #dc2626;"><div style="font-size:1rem;font-weight:800;color:#0f172a;">0</div><div style="font-size:.58rem;color:#64748b;">Blocked</div></div>
            <div style="background:#fff;border:1px solid #f1f5f9;border-radius:8px;padding:6px 8px;border-top:2px solid #d97706;"><div style="font-size:1rem;font-weight:800;color:#0f172a;">5</div><div style="font-size:.58rem;color:#64748b;">My Tasks</div></div>
          </div>
          <!-- Bottom panels -->
          <div style="display:grid;grid-template-columns:1fr 1.5fr 1fr;gap:6px;margin:0 10px;">
            <!-- Priority split -->
            <div style="background:#fff;border:1px solid #f1f5f9;border-radius:8px;padding:8px;">
              <div style="font-size:.68rem;font-weight:700;color:#0f172a;margin-bottom:6px;">Priority Split</div>
              <div style="display:flex;align-items:center;gap:4px;margin-bottom:3px;"><div style="width:8px;height:8px;border-radius:50%;background:#ef4444;"></div><div style="font-size:.6rem;color:#334155;flex:1;">Critical</div><div style="font-size:.62rem;font-weight:700;color:#0f172a;">3</div></div>
              <div style="display:flex;align-items:center;gap:4px;margin-bottom:3px;"><div style="width:8px;height:8px;border-radius:50%;background:#f97316;"></div><div style="font-size:.6rem;color:#334155;flex:1;">High</div><div style="font-size:.62rem;font-weight:700;color:#0f172a;">9</div></div>
              <div style="display:flex;align-items:center;gap:4px;margin-bottom:3px;"><div style="width:8px;height:8px;border-radius:50%;background:#7c3aed;"></div><div style="font-size:.6rem;color:#334155;flex:1;">Medium</div><div style="font-size:.62rem;font-weight:700;color:#0f172a;">8</div></div>
              <div style="display:flex;align-items:center;gap:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#2563eb;"></div><div style="font-size:.6rem;color:#334155;flex:1;">Low</div><div style="font-size:.62rem;font-weight:700;color:#0f172a;">4</div></div>
            </div>
            <!-- Project progress -->
            <div style="background:#fff;border:1px solid #f1f5f9;border-radius:8px;padding:8px;">
              <div style="font-size:.68rem;font-weight:700;color:#0f172a;margin-bottom:6px;">Project Progress</div>
              <div style="display:flex;flex-direction:column;gap:5px;">
                <div><div style="display:flex;justify-content:space-between;margin-bottom:2px;"><span style="font-size:.6rem;color:#334155;">E-commerce Platform</span><span style="font-size:.6rem;font-weight:700;color:#0f172a;">72%</span></div><div style="height:3px;background:#f1f5f9;border-radius:2px;"><div style="height:100%;width:72%;background:#059669;border-radius:2px;"></div></div></div>
                <div><div style="display:flex;justify-content:space-between;margin-bottom:2px;"><span style="font-size:.6rem;color:#334155;">Mobile App Redesign</span><span style="font-size:.6rem;font-weight:700;color:#0f172a;">65%</span></div><div style="height:3px;background:#f1f5f9;border-radius:2px;"><div style="height:100%;width:65%;background:#f59e0b;border-radius:2px;"></div></div></div>
                <div><div style="display:flex;justify-content:space-between;margin-bottom:2px;"><span style="font-size:.6rem;color:#334155;">API Gateway v2</span><span style="font-size:.6rem;font-weight:700;color:#0f172a;">81%</span></div><div style="height:3px;background:#f1f5f9;border-radius:2px;"><div style="height:100%;width:81%;background:#ef4444;border-radius:2px;"></div></div></div>
                <div><div style="display:flex;justify-content:space-between;margin-bottom:2px;"><span style="font-size:.6rem;color:#334155;">Customer Portal</span><span style="font-size:.6rem;font-weight:700;color:#0f172a;">90%</span></div><div style="height:3px;background:#f1f5f9;border-radius:2px;"><div style="height:100%;width:90%;background:#2563eb;border-radius:2px;"></div></div></div>
                <div><div style="display:flex;justify-content:space-between;margin-bottom:2px;"><span style="font-size:.6rem;color:#334155;">Analytics Dashboard</span><span style="font-size:.6rem;font-weight:700;color:#0f172a;">55%</span></div><div style="height:3px;background:#f1f5f9;border-radius:2px;"><div style="height:100%;width:55%;background:#7c3aed;border-radius:2px;"></div></div></div>
              </div>
            </div>
            <!-- Active tasks -->
            <div style="background:#fff;border:1px solid #f1f5f9;border-radius:8px;padding:8px;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;"><div style="font-size:.68rem;font-weight:700;color:#0f172a;">My Active Tasks</div><div style="font-size:.58rem;color:#ef4444;font-weight:600;">⚠ 1 overdue</div></div>
              <div style="display:flex;flex-direction:column;gap:5px;">
                <div style="padding:5px 7px;border-radius:6px;border-left:3px solid #2563eb;background:#f8fafc;"><div style="font-size:.62rem;font-weight:600;color:#0f172a;">Review Q2 sprint plan</div><div style="display:flex;gap:3px;margin-top:2px;"><span style="font-size:.55rem;background:#dbeafe;color:#1d4ed8;padding:1px 5px;border-radius:3px;">PLANNING</span><span style="font-size:.55rem;background:#fef3c7;color:#d97706;padding:1px 5px;border-radius:3px;">HIGH</span></div></div>
                <div style="padding:5px 7px;border-radius:6px;border-left:3px solid #7c3aed;background:#f8fafc;"><div style="font-size:.62rem;font-weight:600;color:#0f172a;">Fix auth token expiry bug</div><div style="display:flex;gap:3px;margin-top:2px;"><span style="font-size:.55rem;background:#fee2e2;color:#dc2626;padding:1px 5px;border-radius:3px;">CRITICAL</span></div></div>
                <div style="padding:5px 7px;border-radius:6px;border-left:3px solid #059669;background:#f8fafc;"><div style="font-size:.62rem;font-weight:600;color:#0f172a;">Update API documentation</div><div style="display:flex;gap:3px;margin-top:2px;"><span style="font-size:.55rem;background:#dbeafe;color:#1d4ed8;padding:1px 5px;border-radius:3px;">DEV</span></div></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="mockup-frame" id="mock-proj" style="display:none;">
      <div class="mockup-topbar">
        <div class="m-dot" style="background:#ff5f57"></div>
        <div class="m-dot" style="background:#febc2e"></div>
        <div class="m-dot" style="background:#28c840"></div>
        <div class="mockup-url"><span class="mockup-url-txt">projectflowpro.up.railway.app/projects</span></div>
      </div>
      <!-- Top bar -->
      <div style="display:flex;align-items:center;justify-content:space-between;padding:0 14px;height:32px;background:#0a0f1e;border-bottom:1px solid #1e293b;">
        <div style="display:flex;align-items:center;gap:6px;">
          <div style="width:20px;height:20px;border-radius:5px;background:#2563eb;display:flex;align-items:center;justify-content:center;"><svg width="10" height="10" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="#0a0f1e"/><circle cx="32" cy="11" r="5" fill="#0a0f1e"/><circle cx="51" cy="43" r="5" fill="#0a0f1e"/><circle cx="13" cy="43" r="5" fill="#0a0f1e"/><line x1="32" y1="16" x2="32" y2="23" stroke="#0a0f1e" stroke-width="3" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="#0a0f1e" stroke-width="3" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="#0a0f1e" stroke-width="3" stroke-linecap="round"/></svg></div>
          <span style="font-size:.62rem;font-weight:800;color:#fff;font-family:Syne,sans-serif;">ProjectFlowPro</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
          <span style="font-size:.6rem;color:#94a3b8;">Your Schedule · Mar 20</span>
          <div style="width:18px;height:18px;border-radius:50%;background:#2563eb;display:flex;align-items:center;justify-content:center;font-size:.55rem;font-weight:700;color:#fff;">P</div>
        </div>
      </div>
      <div class="mockup-body" style="height:320px;">
        <div class="m-sidebar">
          <div class="m-ws"><div class="m-ws-av">PF</div><div><div class="m-ws-n">Acme Corp</div><div class="m-ws-s">4 members</div></div></div>
          <div class="m-sec">Workspace</div>
          <div class="m-nav">📊 Dashboard</div>
          <div class="m-nav act">📁 Projects <span class="m-badge">12</span></div>
          <div class="m-nav">✅ Tasks</div>
          <div class="m-nav">💬 Messages</div>
          <div class="m-sec">Tools</div>
          <div class="m-nav">📅 Timeline</div>
          <div class="m-nav">🎫 Tickets</div>
          <div class="m-nav">👩‍💻 Analytics</div>
        </div>
        <div class="m-main" style="background:#f8fafc;overflow-y:auto;">
          <div style="padding:8px 14px 6px;border-bottom:1px solid #f1f5f9;background:#fff;display:flex;align-items:center;justify-content:space-between;">
            <div style="font-size:.82rem;font-weight:700;color:#0f172a;">Projects <span style="font-size:.68rem;font-weight:400;color:#94a3b8;">8 projects · Acme Corp</span></div>
            <div style="font-size:.62rem;color:#fff;background:#2563eb;padding:3px 9px;border-radius:6px;font-weight:600;">+ New Project</div>
          </div>
          <div style="padding:8px 10px;display:grid;grid-template-columns:repeat(3,1fr);gap:7px;">
            <div style="background:#fff;border:1px solid #e2e8f0;border-radius:9px;padding:9px;border-top:2px solid #059669;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;"><div style="font-size:.68rem;font-weight:700;color:#0f172a;line-height:1.3;">E-commerce Platform</div><span style="font-size:.55rem;background:#dbeafe;color:#1d4ed8;padding:1px 5px;border-radius:3px;white-space:nowrap;">2 TASKS</span></div>
              <div style="font-size:.6rem;color:#64748b;margin-bottom:5px;line-height:1.4;">Next-gen shopping experience rebuild</div>
              <div style="font-size:.58rem;color:#94a3b8;margin-bottom:3px;letter-spacing:.04em;">PROGRESS</div>
              <div style="height:3px;background:#f1f5f9;border-radius:2px;margin-bottom:4px;"><div style="height:100%;width:68%;background:#059669;border-radius:2px;"></div></div>
              <div style="display:flex;justify-content:space-between;"><span style="font-size:.58rem;color:#64748b;">01 Jan – 30 Jun</span><span style="font-size:.58rem;color:#059669;font-weight:600;">72%</span></div>
            </div>
            <div style="background:#fff;border:1px solid #e2e8f0;border-radius:9px;padding:9px;border-top:2px solid #f59e0b;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;"><div style="font-size:.68rem;font-weight:700;color:#0f172a;line-height:1.3;">Mobile App Redesign</div><span style="font-size:.55rem;background:#dbeafe;color:#1d4ed8;padding:1px 5px;border-radius:3px;white-space:nowrap;">2 TASKS</span></div>
              <div style="font-size:.6rem;color:#64748b;margin-bottom:5px;line-height:1.4;">iOS & Android redesign with new UI</div>
              <div style="font-size:.58rem;color:#94a3b8;margin-bottom:3px;letter-spacing:.04em;">PROGRESS</div>
              <div style="height:3px;background:#f1f5f9;border-radius:2px;margin-bottom:4px;"><div style="height:100%;width:68%;background:#f59e0b;border-radius:2px;"></div></div>
              <div style="display:flex;justify-content:space-between;"><span style="font-size:.58rem;color:#64748b;">15 Feb – 15 May</span><span style="font-size:.58rem;color:#f59e0b;font-weight:600;">65%</span></div>
            </div>
            <div style="background:#fff;border:1px solid #e2e8f0;border-radius:9px;padding:9px;border-top:2px solid #ef4444;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;"><div style="font-size:.68rem;font-weight:700;color:#0f172a;line-height:1.3;">API Gateway v2</div><span style="font-size:.55rem;background:#fee2e2;color:#dc2626;padding:1px 5px;border-radius:3px;white-space:nowrap;">1 TASKS</span></div>
              <div style="font-size:.6rem;color:#64748b;margin-bottom:5px;line-height:1.4;">High-performance REST & GraphQL gateway</div>
              <div style="font-size:.58rem;color:#94a3b8;margin-bottom:3px;letter-spacing:.04em;">PROGRESS</div>
              <div style="height:3px;background:#f1f5f9;border-radius:2px;margin-bottom:4px;"><div style="height:100%;width:78%;background:#ef4444;border-radius:2px;"></div></div>
              <div style="display:flex;justify-content:space-between;"><span style="font-size:.58rem;color:#64748b;">01 Mar – 01 Aug</span><span style="font-size:.58rem;color:#ef4444;font-weight:600;">81%</span></div>
            </div>
            <div style="background:#fff;border:1px solid #e2e8f0;border-radius:9px;padding:9px;border-top:2px solid #2563eb;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;"><div style="font-size:.68rem;font-weight:700;color:#0f172a;line-height:1.3;">Customer Portal</div><span style="font-size:.55rem;background:#dbeafe;color:#1d4ed8;padding:1px 5px;border-radius:3px;white-space:nowrap;">1 TASKS</span></div>
              <div style="font-size:.6rem;color:#64748b;margin-bottom:5px;line-height:1.4;">Self-service customer dashboard & billing</div>
              <div style="font-size:.58rem;color:#94a3b8;margin-bottom:3px;letter-spacing:.04em;">PROGRESS</div>
              <div style="height:3px;background:#f1f5f9;border-radius:2px;margin-bottom:4px;"><div style="height:100%;width:88%;background:#2563eb;border-radius:2px;"></div></div>
              <div style="display:flex;justify-content:space-between;"><span style="font-size:.58rem;color:#64748b;">01 Feb – 31 Mar</span><span style="font-size:.58rem;color:#2563eb;font-weight:600;">90%</span></div>
            </div>
            <div style="background:#fff;border:1px solid #e2e8f0;border-radius:9px;padding:9px;border-top:2px solid #7c3aed;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;"><div style="font-size:.68rem;font-weight:700;color:#0f172a;line-height:1.3;">Analytics Dashboard</div><span style="font-size:.55rem;background:#dbeafe;color:#1d4ed8;padding:1px 5px;border-radius:3px;white-space:nowrap;">2 TASKS</span></div>
              <div style="font-size:.6rem;color:#64748b;margin-bottom:5px;line-height:1.4;">Real-time KPI tracking and reporting</div>
              <div style="font-size:.58rem;color:#94a3b8;margin-bottom:3px;letter-spacing:.04em;">PROGRESS</div>
              <div style="height:3px;background:#f1f5f9;border-radius:2px;margin-bottom:4px;"><div style="height:100%;width:98%;background:#7c3aed;border-radius:2px;"></div></div>
              <div style="display:flex;justify-content:space-between;"><span style="font-size:.58rem;color:#64748b;">10 Jan – 30 Apr</span><span style="font-size:.58rem;color:#7c3aed;font-weight:600;">55%</span></div>
            </div>
            <div style="background:#fff;border:1px solid #e2e8f0;border-radius:9px;padding:9px;border-top:2px solid #059669;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;"><div style="font-size:.68rem;font-weight:700;color:#0f172a;line-height:1.3;">DevOps Pipeline</div><span style="font-size:.55rem;background:#dbeafe;color:#1d4ed8;padding:1px 5px;border-radius:3px;white-space:nowrap;">1 TASKS</span></div>
              <div style="font-size:.6rem;color:#64748b;margin-bottom:5px;line-height:1.4;">CI/CD automation & infrastructure as code</div>
              <div style="font-size:.58rem;color:#94a3b8;margin-bottom:3px;letter-spacing:.04em;">PROGRESS</div>
              <div style="height:3px;background:#f1f5f9;border-radius:2px;margin-bottom:4px;"><div style="height:100%;width:95%;background:#059669;border-radius:2px;"></div></div>
              <div style="display:flex;justify-content:space-between;"><span style="font-size:.58rem;color:#64748b;">01 Mar – 30 Jun</span><span style="font-size:.58rem;color:#059669;font-weight:600;">88%</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>
<script>
function showTab(t){
  document.getElementById('mock-dash').style.display=t==='dash'?'block':'none';
  document.getElementById('mock-proj').style.display=t==='proj'?'block':'none';
  document.getElementById('tab-dash').style.background=t==='dash'?'#2563eb':'#fff';
  document.getElementById('tab-dash').style.color=t==='dash'?'#fff':'#64748b';
  document.getElementById('tab-dash').style.borderColor=t==='dash'?'#2563eb':'#e2e8f0';
  document.getElementById('tab-proj').style.background=t==='proj'?'#2563eb':'#fff';
  document.getElementById('tab-proj').style.color=t==='proj'?'#fff':'#64748b';
  document.getElementById('tab-proj').style.borderColor=t==='proj'?'#2563eb':'#e2e8f0';
}
</script>

<!-- TICKER -->
<div class="ticker-wrap">
  <div class="ticker">
    <div class="ticker-item">📋 Smart Task Boards <span class="ticker-sep">·</span></div>
    <div class="ticker-item">🤖 <span class="hi">AI Assistant</span> <span class="ticker-sep">·</span></div>
    <div class="ticker-item">📅 Timeline Tracker <span class="ticker-sep">·</span></div>
    <div class="ticker-item">📞 <span class="hi">Voice Huddles</span> <span class="ticker-sep">·</span></div>
    <div class="ticker-item">💬 Real-time Messaging <span class="ticker-sep">·</span></div>
    <div class="ticker-item">✉️ Direct DMs <span class="ticker-sep">·</span></div>
    <div class="ticker-item">🎫 Support Tickets <span class="ticker-sep">·</span></div>
    <div class="ticker-item">👩‍💻 <span class="hi">Dev Analytics</span> <span class="ticker-sep">·</span></div>
    <div class="ticker-item">⏰ Smart Reminders <span class="ticker-sep">·</span></div>
    <div class="ticker-item">🔔 Push Notifications <span class="ticker-sep">·</span></div>
    <div class="ticker-item">📋 Smart Task Boards <span class="ticker-sep">·</span></div>
    <div class="ticker-item">🤖 <span class="hi">AI Assistant</span> <span class="ticker-sep">·</span></div>
    <div class="ticker-item">📅 Timeline Tracker <span class="ticker-sep">·</span></div>
    <div class="ticker-item">📞 <span class="hi">Voice Huddles</span> <span class="ticker-sep">·</span></div>
    <div class="ticker-item">💬 Real-time Messaging <span class="ticker-sep">·</span></div>
    <div class="ticker-item">✉️ Direct DMs <span class="ticker-sep">·</span></div>
    <div class="ticker-item">🎫 Support Tickets <span class="ticker-sep">·</span></div>
    <div class="ticker-item">👩‍💻 <span class="hi">Dev Analytics</span> <span class="ticker-sep">·</span></div>
    <div class="ticker-item">⏰ Smart Reminders <span class="ticker-sep">·</span></div>
    <div class="ticker-item">🔔 Push Notifications <span class="ticker-sep">·</span></div>
  </div>
</div>

<!-- STATS -->
<div class="stats-bar">
  <div class="stats-grid">
    <div class="stat-item"><div class="stat-num">∞</div><div class="stat-lbl">Multi-tenant workspaces</div></div>
    <div class="stat-item"><div class="stat-num" style="color:#7c3aed">11+</div><div class="stat-lbl">Built-in modules</div></div>
    <div class="stat-item"><div class="stat-num" style="color:#059669">6</div><div class="stat-lbl">User roles</div></div>
    <div class="stat-item"><div class="stat-num" style="color:#d97706">150MB</div><div class="stat-lbl">Max file upload</div></div>
  </div>
</div>

<!-- FEATURES -->
<section id="features">
  <div class="wrap">
    <div class="centered">
      <div class="sec-tag">Features</div>
      <h2 class="sec-title">Everything your team needs</h2>
      <p class="sec-sub">One platform replaces your task tracker, chat tool, ticketing system, and analytics dashboard.</p>
    </div>
    <div class="bento">
      <div class="bc featured"><div class="b-ico">📋</div><h3>Smart Task Management</h3><p>Create, assign and track tasks with custom stage workflows, priorities, due dates and inline comments.</p><ul class="feat-list"><li>Custom stages: Backlog → Dev → Review → Done</li><li>Priority levels: Low, Medium, High, Critical</li><li>Inline comments & file attachments up to 150MB</li><li>Assignee management with avatar display</li></ul></div>
      <div class="bc"><div class="b-ico">📁</div><h3>Project Workspaces</h3><p>Colour-coded projects with start & end dates, per-member access control, and live progress bars.</p><ul class="feat-list"><li>Start date + target date planning</li><li>Visual progress from task completion</li><li>Per-project team member access control</li></ul></div>
      <div class="bc featured"><div class="b-ico">🤖</div><h3>AI Assistant</h3><p>Floating AI panel with full workspace context. Bring your own Anthropic API key.</p><ul class="feat-list"><li>Ask anything about tasks, projects & blockers</li><li>Smart EOD reports & sprint summaries</li><li>Bring your own API key (Anthropic)</li><li>Context-aware using live workspace data</li></ul></div>
      <div class="bc span2"><div class="b-ico">📅</div><h3>Timeline Tracker <span class="role-badge">Admin / Manager</span></h3><p>Dual progress bars compare time elapsed against task completion % so you catch at-risk projects instantly. Auto health badges calculated from today's date.</p><ul class="feat-list"><li>Days spent & remaining — calculated from today</li><li>Health badges: On Track · At Risk · Needs Attention · Overdue</li><li>Filter by health status, sort by days remaining</li></ul></div>
      <div class="bc"><div class="b-ico">👩‍💻</div><h3>Dev Productivity <span class="role-badge">Admin</span></h3><p>Full leaderboard with productivity score 0–100 per developer. Table + Chart views with drill-down.</p><ul class="feat-list"><li>Productivity score 0–100 auto-calculated</li><li>Table & chart view toggle</li><li>Drill into any developer's full task list</li></ul></div>
      <div class="bc"><div class="b-ico">💬</div><h3>Messaging & DMs</h3><p>Per-project channels plus private one-on-one direct messages with unread badges.</p><ul class="feat-list"><li>Per-project message channels</li><li>Private direct messages</li><li>Start huddle directly from DMs</li></ul></div>
      <div class="bc"><div class="b-ico">📞</div><h3>Huddle Calls</h3><p>Instant in-app voice huddles. Start from sidebar or DMs, invite mid-call.</p><ul class="feat-list"><li>One-click room creation</li><li>Invite participants mid-call</li><li>Mute/unmute controls</li></ul></div>
      <div class="bc"><div class="b-ico">🎫</div><h3>Support Tickets</h3><p>Built-in bug tracking & support ticketing separate from project tasks.</p><ul class="feat-list"><li>Bug, feature-request & incident types</li><li>Status: Open · In Progress · Resolved</li><li>Threaded comments per ticket</li></ul></div>

    </div>
  </div>
</section>

<!-- MODULES -->
<section id="modules" style="padding-top:0;background:#f8fafc;">
  <div class="wrap centered" style="padding-top:60px;padding-bottom:60px;">
    <div class="sec-tag">All Modules</div>
    <h2 class="sec-title">11 modules, one platform</h2>
    <p class="sec-sub">Every module shares data automatically — tasks flow into analytics, tickets link to projects, reminders fire to notifications.</p>
    <div class="modules-grid">
      <div class="mod-card"><div class="mod-ico">📋</div><div class="mod-n">Task Board</div><div class="mod-d">Kanban with custom stages</div></div>
      <div class="mod-card"><div class="mod-ico">📁</div><div class="mod-n">Projects</div><div class="mod-d">Multi-project management</div></div>
      <div class="mod-card"><div class="mod-ico">🤖</div><div class="mod-n">AI Assistant</div><div class="mod-d">Workspace-aware AI</div></div>
      <div class="mod-card"><div class="mod-ico">📅</div><div class="mod-n">Timeline</div><div class="mod-d">Health tracking per project</div></div>
      <div class="mod-card"><div class="mod-ico">👩‍💻</div><div class="mod-n">Dev Analytics</div><div class="mod-d">Productivity leaderboard</div></div>
      <div class="mod-card"><div class="mod-ico">💬</div><div class="mod-n">Messaging</div><div class="mod-d">Per-project channels</div></div>
      <div class="mod-card"><div class="mod-ico">✉️</div><div class="mod-n">Direct DMs</div><div class="mod-d">Private conversations</div></div>
      <div class="mod-card"><div class="mod-ico">📞</div><div class="mod-n">Huddle Calls</div><div class="mod-d">Instant voice rooms</div></div>
      <div class="mod-card"><div class="mod-ico">🎫</div><div class="mod-n">Tickets</div><div class="mod-d">Bug & support tracking</div></div>
      <div class="mod-card"><div class="mod-ico">⏰</div><div class="mod-n">Reminders</div><div class="mod-d">Per-task alerts</div></div>
      <div class="mod-card"><div class="mod-ico">🔔</div><div class="mod-n">Notifications</div><div class="mod-d">Push, email & in-app</div></div>
      <div class="mod-card" style="border-color:rgba(37,99,235,.12);background:rgba(37,99,235,.02);"><div class="mod-ico">⚙️</div><div class="mod-n">Settings</div><div class="mod-d">SMTP, AI key, invite codes</div></div>
    </div>
  </div>
</section>

<!-- HOW IT WORKS -->
<section id="how">
  <div class="wrap centered">
    <div class="sec-tag">How it works</div>
    <h2 class="sec-title">Up and running in 4 steps</h2>
    <p class="sec-sub">No DevOps required. Create a workspace, configure it once, invite your team.</p>
    <div class="steps">
      <div class="step"><div class="step-num">1</div><h3>Create Account</h3><p>Sign up and create a new workspace. An invite code is generated instantly.</p></div>
      <div class="step"><div class="step-num">2</div><h3>Configure</h3><p>Add your AI API key, set up SMTP for email notifications, customise settings.</p></div>
      <div class="step"><div class="step-num">3</div><h3>Invite Team</h3><p>Share the invite code. Members join instantly with their chosen role.</p></div>
      <div class="step"><div class="step-num">4</div><h3>Ship Together</h3><p>Create projects, assign tasks, huddle, use AI, track timelines. Ship faster.</p></div>
    </div>
  </div>
</section>

<!-- CTA -->
<section class="cta-section">
  <div class="wrap">
    <div class="cta-box">
      <h2>Ready to ship faster, together?</h2>
      <p>Create your free workspace in under 2 minutes. No credit card required.</p>
      <div class="cta-actions">
        <a href="/?action=register" class="btn btn-primary btn-xl">🚀 Create Your Workspace</a>
        <a href="/?action=login" class="btn btn-outline btn-xl">Sign In →</a>
      </div>
      <div class="trust-row">
        <div class="trust-item"><span>✓</span> Free to start</div>
        <div class="trust-item"><span>✓</span> No credit card</div>
        <div class="trust-item"><span>✓</span> Multi-tenant</div>
        <div class="trust-item"><span>✓</span> Hosted on Railway</div>
        <div class="trust-item"><span>✓</span> bcrypt security</div>
      </div>
    </div>
  </div>
</section>

<!-- FOOTER -->
<footer>
  <div class="wrap">
    <div class="footer-top">
      <div class="footer-brand">
        <a href="/" class="logo"><div class="logo-icon"><svg width="14" height="14" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white"/><circle cx="51" cy="43" r="6" fill="white"/><circle cx="13" cy="43" r="6" fill="white"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/></svg></div>ProjectFlowPro</a>
        <p>The all-in-one project management platform for engineering teams. AI-powered, multi-tenant, fully featured.</p>
      </div>
      <div class="footer-col"><h4>Product</h4><ul><li><a href="#features">Features</a></li><li><a href="#modules">All Modules</a></li><li><a href="#how">How it works</a></li></ul></div>
      <div class="footer-col"><h4>Platform</h4><ul><li><a href="/?action=register">Create Workspace</a></li><li><a href="/?action=login">Sign In</a></li></ul></div>
      <div class="footer-col"><h4>Capabilities</h4><ul><li><a href="#features">AI Assistant</a></li><li><a href="#features">Voice Huddles</a></li><li><a href="#features">Notifications</a></li></ul></div>
    </div>
    <div class="footer-bottom">
      <div class="footer-copy">© 2025 ProjectFlowPro v4.0 — Hosted on Railway</div>
      <div class="footer-badges"><div class="fb">v4.0</div><div class="fb">PostgreSQL</div><div class="fb">AI-Powered</div><div class="fb">bcrypt</div></div>
    </div>
  </div>
</footer>

<script>
// Scroll animations
const observer=new IntersectionObserver(entries=>{
  entries.forEach(e=>{if(e.isIntersecting){e.target.style.opacity='1';e.target.style.transform='translateY(0)';}});
},{threshold:.08,rootMargin:'0px 0px -20px 0px'});
document.querySelectorAll('.bc,.mod-card,.step').forEach((el,i)=>{
  el.style.opacity='0';el.style.transform='translateY(16px)';
  el.style.transition=`opacity .45s ${i*.045}s ease,transform .45s ${i*.045}s ease`;
  observer.observe(el);
});

// Nav scroll
const nav=document.getElementById('nav');
window.addEventListener('scroll',()=>{
  nav.style.background=window.scrollY>30?'rgba(255,255,255,0.97)':'rgba(255,255,255,0.88)';
},{passive:true});

// Hero ocean waves canvas — same as login page
const cv=document.getElementById('hero-canvas');
const ctx=cv.getContext('2d');
let frame=0,animId;
const resize=()=>{cv.width=cv.offsetWidth||window.innerWidth;cv.height=cv.offsetHeight||window.innerHeight;};
resize();window.addEventListener('resize',resize,{passive:true});

const waveConfigs=[
  {spd:.00042,amp:.048,freq:2.2,ph:0,     fill:'rgba(147,197,253,',stroke:'rgba(96,165,250,', base:.50},
  {spd:.00031,amp:.040,freq:1.8,ph:2.0,   fill:'rgba(96,165,250,', stroke:'rgba(59,130,246,', base:.56},
  {spd:.00051,amp:.032,freq:2.7,ph:4.1,   fill:'rgba(59,130,246,', stroke:'rgba(37,99,235,',  base:.61},
  {spd:.00024,amp:.026,freq:1.5,ph:1.2,   fill:'rgba(37,99,235,',  stroke:'rgba(29,78,216,',  base:.66},
  {spd:.00058,amp:.020,freq:3.1,ph:3.4,   fill:'rgba(29,78,216,',  stroke:'rgba(30,64,175,',  base:.70},
];
const pts=Array.from({length:28},()=>({
  x:Math.random(),y:Math.random()*.45,
  vx:(Math.random()-.5)*.00012,vy:(Math.random()-.5)*.0001,
  r:.6+Math.random()*1.2,ph:Math.random()*Math.PI*2,sp:.006+Math.random()*.008,
}));
const bubbles=Array.from({length:16},()=>({
  x:Math.random(),y:.45+Math.random()*.45,r:1.5+Math.random()*4,
  vy:-.00025-Math.random()*.0003,ph:Math.random()*Math.PI*2,sp:.007+Math.random()*.005,a:.06+Math.random()*.1,
}));
const sparks=Array.from({length:24},()=>({
  x:Math.random(),yb:.46+Math.random()*.18,ph:Math.random()*Math.PI*2,sp:.018+Math.random()*.014,sz:.6+Math.random()*1.2,
}));

function drawCanvas(){
  const W=cv.width,H=cv.height;frame++;const t=frame*.016;
  const bg=ctx.createLinearGradient(0,0,0,H);
  bg.addColorStop(0,'#ffffff');bg.addColorStop(.28,'#f0f9ff');bg.addColorStop(.5,'#dbeafe');bg.addColorStop(.72,'#bfdbfe');bg.addColorStop(1,'#93c5fd');
  ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);
  const sg=ctx.createRadialGradient(W*.8,H*.05,0,W*.8,H*.05,W*.5);
  sg.addColorStop(0,'rgba(254,240,138,0.4)');sg.addColorStop(.3,'rgba(253,224,71,0.12)');sg.addColorStop(1,'rgba(0,0,0,0)');
  ctx.fillStyle=sg;ctx.fillRect(0,0,W,H);
  pts.forEach(p=>{
    p.x+=p.vx;p.y+=p.vy;p.ph+=p.sp;
    if(p.x<0)p.x=1;if(p.x>1)p.x=0;if(p.y<0)p.y=.45;if(p.y>.45)p.y=0;
    const a=(.07+Math.sin(p.ph)*.05)*(1-p.y/.45);
    ctx.beginPath();ctx.arc(p.x*W,p.y*H,p.r,0,Math.PI*2);
    ctx.fillStyle='rgba(59,130,246,'+a+')';ctx.fill();
  });
  ctx.lineWidth=.4;
  for(let i=0;i<pts.length;i++)for(let j=i+1;j<pts.length;j++){
    const dx=(pts[i].x-pts[j].x)*W,dy=(pts[i].y-pts[j].y)*H;
    const d=Math.sqrt(dx*dx+dy*dy);
    if(d<W*.13){ctx.strokeStyle='rgba(147,197,253,'+(0.05*(1-d/(W*.13)))+')';ctx.beginPath();ctx.moveTo(pts[i].x*W,pts[i].y*H);ctx.lineTo(pts[j].x*W,pts[j].y*H);ctx.stroke();}
  }
  const hy=H*.47;
  const hl=ctx.createLinearGradient(0,hy,W,hy);
  hl.addColorStop(0,'rgba(255,255,255,0)');hl.addColorStop(.5,'rgba(219,234,254,0.55)');hl.addColorStop(1,'rgba(255,255,255,0)');
  ctx.fillStyle=hl;ctx.fillRect(0,hy-1,W,3);
  waveConfigs.forEach((w,wi)=>{
    const ph=t*w.spd*1000+w.ph;
    ctx.beginPath();ctx.moveTo(0,H);
    for(let x=0;x<=W;x+=2){const xn=x/W;const y=H*(w.base+Math.sin(xn*Math.PI*w.freq+ph)*w.amp+Math.sin(xn*Math.PI*w.freq*1.7+ph*.65)*(w.amp*.3)+Math.sin(xn*Math.PI*w.freq*.9+ph*1.4)*(w.amp*.18));x===0?ctx.moveTo(x,y):ctx.lineTo(x,y);}
    ctx.lineTo(W,H);ctx.closePath();ctx.fillStyle=w.fill+[.13,.12,.11,.10,.09][wi]+')';ctx.fill();
    ctx.beginPath();
    for(let x=0;x<=W;x+=2){const xn=x/W;const y=H*(w.base+Math.sin(xn*Math.PI*w.freq+ph)*w.amp+Math.sin(xn*Math.PI*w.freq*1.7+ph*.65)*(w.amp*.3)+Math.sin(xn*Math.PI*w.freq*.9+ph*1.4)*(w.amp*.18));x===0?ctx.moveTo(x,y):ctx.lineTo(x,y);}
    ctx.strokeStyle=w.stroke+[.07,.06,.06,.05,.05][wi]+')';ctx.lineWidth=1.1;ctx.stroke();
    if(wi===0){for(let fx=W*.04;fx<W;fx+=W*.09+Math.sin(fx*.01)*W*.02){const xn=fx/W;const fy=H*(w.base+Math.sin(xn*Math.PI*w.freq+ph)*w.amp+Math.sin(xn*Math.PI*w.freq*1.7+ph*.65)*(w.amp*.3));const fa=.09+Math.sin(t*.9+fx*.008)*.04;const fg=ctx.createRadialGradient(fx,fy,0,fx,fy,20+Math.sin(t+fx*.01)*5);fg.addColorStop(0,'rgba(255,255,255,'+fa+')');fg.addColorStop(1,'rgba(255,255,255,0)');ctx.fillStyle=fg;ctx.beginPath();ctx.ellipse(fx,fy,22,5,0,0,Math.PI*2);ctx.fill();}}
  });
  bubbles.forEach(b=>{
    b.y+=b.vy;b.ph+=b.sp;if(b.y<.42)b.y=.5+Math.random()*.3;
    const bx=b.x*W+Math.sin(b.ph)*6,by=b.y*H,ba=b.a*(0.4+Math.sin(b.ph)*.6);
    ctx.beginPath();ctx.arc(bx,by,b.r,0,Math.PI*2);ctx.strokeStyle='rgba(147,197,253,'+ba+')';ctx.lineWidth=.7;ctx.stroke();
    ctx.beginPath();ctx.arc(bx-b.r*.3,by-b.r*.35,b.r*.28,0,Math.PI*2);ctx.fillStyle='rgba(255,255,255,'+(ba*.5)+')';ctx.fill();
  });
  sparks.forEach(s=>{
    s.ph+=s.sp;const sx=s.x*W+Math.sin(s.ph*.4)*10,sy=H*(s.yb+Math.sin(s.ph*.3)*.015),sa=(Math.sin(s.ph)+1)/2;
    if(sa>.35){const a=sa*.2;ctx.save();ctx.translate(sx,sy);ctx.fillStyle='rgba(255,255,255,'+a+')';ctx.beginPath();ctx.moveTo(0,-s.sz*2.2);ctx.lineTo(s.sz*.35,-s.sz*.35);ctx.lineTo(s.sz*2.2,0);ctx.lineTo(s.sz*.35,s.sz*.35);ctx.lineTo(0,s.sz*2.2);ctx.lineTo(-s.sz*.35,s.sz*.35);ctx.lineTo(-s.sz*2.2,0);ctx.lineTo(-s.sz*.35,-s.sz*.35);ctx.closePath();ctx.fill();ctx.restore();}
  });
  animId=requestAnimationFrame(drawCanvas);
}
drawCanvas();
</script>
</body>
</html>"""



HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>ProjectFlowProPro</title>
<link rel="manifest" href="/manifest.json"/>
<meta name="theme-color" content="#2563eb"/>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23aaff00'/%3E%3Ccircle cx='16' cy='16' r='4' fill='%230a1a00'/%3E%3Ccircle cx='16' cy='7' r='3' fill='%230a1a00' opacity='0.9'/%3E%3Ccircle cx='24' cy='22' r='3' fill='%230a1a00' opacity='0.9'/%3E%3Ccircle cx='8' cy='22' r='3' fill='%230a1a00' opacity='0.9'/%3E%3Cline x1='16' y1='10' x2='16' y2='12' stroke='%230a1a00' stroke-width='2' stroke-linecap='round'/%3E%3Cline x1='21' y1='20' x2='19' y2='18' stroke='%230a1a00' stroke-width='2' stroke-linecap='round'/%3E%3Cline x1='11' y1='20' x2='13' y2='18' stroke='%230a1a00' stroke-width='2' stroke-linecap='round'/%3E%3C/svg%3E"/>
<script>
(function(){
  var svg="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23aaff00'/%3E%3Ccircle cx='16' cy='16' r='4' fill='%230a1a00'/%3E%3Ccircle cx='16' cy='7' r='3' fill='%230a1a00' opacity='0.9'/%3E%3Ccircle cx='24' cy='22' r='3' fill='%230a1a00' opacity='0.9'/%3E%3Ccircle cx='8' cy='22' r='3' fill='%230a1a00' opacity='0.9'/%3E%3Cline x1='16' y1='10' x2='16' y2='12' stroke='%230a1a00' stroke-width='2' stroke-linecap='round'/%3E%3Cline x1='21' y1='20' x2='19' y2='18' stroke='%230a1a00' stroke-width='2' stroke-linecap='round'/%3E%3Cline x1='11' y1='20' x2='13' y2='18' stroke='%230a1a00' stroke-width='2' stroke-linecap='round'/%3E%3C/svg%3E";
  Array.from(document.querySelectorAll("link[rel*=icon]")).forEach(function(el){el.parentNode.removeChild(el);});
  var l=document.createElement('link');l.rel='icon';l.type='image/svg+xml';l.href=svg;
  document.head.appendChild(l);
})();
</script>

<!-- ═══════════════════════════════════════════════════════
     SERVICE WORKER + WEB PUSH BOOTSTRAP
     Registers SW immediately so push works even when app
     is minimised or the tab is in the background.
     ═══════════════════════════════════════════════════════ -->
<script>
(function(){
'use strict';

// ── 1. Register Service Worker ───────────────────────────────────────────────
window._pfSWReady = false;
window._pfPushSub = null;

if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js', {scope:'/'})
    .then(function(reg){
      window._pfSWReady = true;
      window._pfSWReg   = reg;
      console.log('[PF] SW registered, scope:', reg.scope);

      // Listen for messages from SW (e.g. navigation requests)
      navigator.serviceWorker.addEventListener('message', function(e){
        if(e.data && e.data.type === 'PF_NAVIGATE'){
          window.location.hash = e.data.url || '/';
          window.focus();
        }
      });

      // ── 2. Request Notification permission then subscribe to Push ───────────
      _pfSetupPush(reg);
    })
    .catch(function(e){ console.warn('[PF] SW registration failed:', e); });
}

// ── urlBase64ToUint8Array helper for VAPID key ───────────────────────────────
function _pfUrlB64(base64String){
  var padding='='.repeat((4-base64String.length%4)%4);
  var base64=(base64String+padding).replace(/-/g,'+').replace(/_/g,'/');
  var rawData=window.atob(base64);
  var outputArray=new Uint8Array(rawData.length);
  for(var i=0;i<rawData.length;++i) outputArray[i]=rawData.charCodeAt(i);
  return outputArray;
}

async function _pfSetupPush(reg){
  // Only proceed if Push API is available
  if(!('PushManager' in window)) return;

  // Fetch VAPID public key from server
  var vapidKey='';
  try{
    var r=await fetch('/api/push/vapid-key',{credentials:'include'});
    var d=await r.json();
    vapidKey=d.publicKey||'';
  }catch(e){ return; }

  if(!vapidKey){
    // pywebpush not available — fall back to polling-only mode
    console.log('[PF] No VAPID key — using polling mode only');
    return;
  }

  // Request notification permission
  var perm = Notification.permission;
  if(perm==='default'){
    perm = await Notification.requestPermission();
  }
  if(perm!=='granted') return;

  // Check for existing subscription
  var existingSub = await reg.pushManager.getSubscription();
  if(existingSub){
    window._pfPushSub = existingSub;
    _pfSendSubToServer(existingSub);
    return;
  }

  // Subscribe to push
  try{
    var sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: _pfUrlB64(vapidKey)
    });
    window._pfPushSub = sub;
    _pfSendSubToServer(sub);
    console.log('[PF] Push subscription created');
  }catch(e){
    console.warn('[PF] Push subscribe failed:', e);
  }
}

function _pfSendSubToServer(sub){
  var subJson = sub.toJSON();
  fetch('/api/push/subscribe',{
    method:'POST',
    credentials:'include',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      endpoint: subJson.endpoint,
      keys: subJson.keys
    })
  }).catch(function(){});
}

// ── 3. Visibility-aware polling accelerator ──────────────────────────────────
// When tab is hidden, browsers throttle setTimeout/setInterval to once per minute.
// We compensate by using the Page Visibility API to trigger an immediate poll
// when the user brings the tab back into focus.
window._pfLastPollTrigger = null;
document.addEventListener('visibilitychange', function(){
  if(document.visibilityState === 'visible'){
    // Signal to the React app that it should poll immediately
    if(typeof window._pfOnVisible === 'function'){
      window._pfOnVisible();
    }
  }
});

// ── 4. Unsubscribe helper (called on logout) ─────────────────────────────────
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
// Load JS libs sequentially: React must finish before ReactDOM loads
(function(){
  var libs=[
    'https://cdnjs.cloudflare.com/ajax/libs/react/18.2.0/umd/react.production.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.2.0/umd/react-dom.production.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/prop-types/15.8.1/prop-types.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/recharts/2.12.7/Recharts.js',
    'https://unpkg.com/htm@3.1.1/dist/htm.js',
  ];
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
  --bg:#f0f6ff;
  --sf:#ffffff;
  --sf2:#f1f5f9;
  --sf3:#e8f0fe;
  --bd:rgba(37,99,235,0.14);
  --bd2:rgba(0,0,0,0.07);
  --tx:#0a0f1e;
  --tx2:#334155;
  --tx3:#64748b;
  --sb:#0f172a;
  --sb2:#1e293b;
  --sb3:#334155;
  --sbt:#94a3b8;
  --ac:#2563eb;
  --ac2:#1d4ed8;
  --ac3:rgba(37,99,235,0.10);
  --ac4:rgba(37,99,235,0.06);
  --ac-tx:#ffffff;
  --rd:#dc2626;
  --rd2:#ef4444;
  --gn:#16a34a;
  --gn2:#22c55e;
  --am:#d97706;
  --cy:#0891b2;
  --pu:#7c3aed;
  --or:#ea580c;
  --pk:#db2777;
  --sh:0 1px 3px rgba(37,99,235,0.08),0 2px 8px rgba(0,0,0,0.06);
  --sh2:0 4px 16px rgba(37,99,235,0.12),0 8px 32px rgba(0,0,0,0.08);
  --sh3:0 0 0 1px var(--bd);
}

/* === LIGHT THEME — via .lm on body. Cards: white on #ebebeb canvas === */
.lm{
  --bg:#f0f6ff;
  --sf:#ffffff;
  --sf2:#f1f5f9;
  --sf3:#e8f0fe;
  --bd:rgba(37,99,235,0.14);
  --bd2:rgba(0,0,0,0.07);
  --tx:#0a0f1e;
  --tx2:#334155;
  --tx3:#64748b;
  --sb:#0f172a;
  --sb2:#1e293b;
  --sb3:#334155;
  --sbt:#94a3b8;
  --ac:#2563eb;
  --ac2:#1d4ed8;
  --ac3:rgba(37,99,235,0.10);
  --ac4:rgba(37,99,235,0.06);
  --ac-tx:#ffffff;
  --rd:#dc2626;
  --rd2:#ef4444;
  --gn:#16a34a;
  --gn2:#22c55e;
  --am:#d97706;
  --cy:#0891b2;
  --pu:#7c3aed;
  --or:#ea580c;
  --pk:#db2777;
  --sh:0 1px 3px rgba(37,99,235,0.08),0 2px 8px rgba(0,0,0,0.06);
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
.brd{background:rgba(255,68,68,.08);color:var(--rd)!important;border:1px solid rgba(255,68,68,.2)}
.brd:hover{background:rgba(255,68,68,.14)}
.bam{background:rgba(245,158,11,.08);color:var(--am)!important;border:1px solid rgba(245,158,11,.25)}
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
// Wait for all CDN libs to load before running app
(function(){
'use strict';
function waitForLibs(cb, attempts){
  attempts = attempts||0;
  if(typeof React!=='undefined' && typeof ReactDOM!=='undefined' && typeof htm!=='undefined'){
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
  get:u=>fetch(u,{credentials:'include'}).then(r=>r.json()).catch(()=>({})),
  post:(u,b)=>fetch(u,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json()).catch(()=>({})),
  put:(u,b)=>fetch(u,{method:'PUT',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json()).catch(()=>({})),
  del:u=>fetch(u,{method:'DELETE',credentials:'include'}).then(r=>r.json()).catch(()=>({})),
  upload:(u,fd)=>fetch(u,{method:'POST',credentials:'include',body:fd}).then(r=>r.json()).catch(()=>({})),
};

const STAGES={
  backlog:    {label:'Backlog',    color:'#94a3b8',bg:'rgba(148,163,184,.13)'},
  planning:   {label:'Planning',  color:'var(--cy)',bg:'rgba(96,165,250,.13)'},
  development:{label:'Dev',       color:'#9b8ef4',bg:'rgba(167,139,250,.13)'},
  code_review:{label:'Review',    color:'#22d3ee',bg:'rgba(34,211,238,.13)'},
  testing:    {label:'Testing',   color:'var(--pu)',bg:'rgba(251,191,36,.13)'},
  uat:        {label:'UAT',       color:'#f472b6',bg:'rgba(244,114,182,.13)'},
  release:    {label:'Release',   color:'#fb923c',bg:'rgba(251,146,60,.13)'},
  production: {label:'Production',color:'#34d399',bg:'rgba(52,211,153,.13)'},
  completed:  {label:'Completed', color:'#4ade80',bg:'rgba(74,222,128,.13)'},
  blocked:    {label:'Blocked',   color:'var(--rd2)',bg:'rgba(248,113,113,.13)'},
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
  const [tab,setTab]=useState(_initTab);
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

    // Ocean waves (white bg, soft blue tones)
    const waveConfigs=[
      {spd:.00042,amp:.048,freq:2.2,ph:0,     fill:'rgba(147,197,253,',stroke:'rgba(96,165,250,', base:.50},
      {spd:.00031,amp:.040,freq:1.8,ph:2.0,   fill:'rgba(96,165,250,', stroke:'rgba(59,130,246,', base:.56},
      {spd:.00051,amp:.032,freq:2.7,ph:4.1,   fill:'rgba(59,130,246,', stroke:'rgba(37,99,235,',  base:.61},
      {spd:.00024,amp:.026,freq:1.5,ph:1.2,   fill:'rgba(37,99,235,',  stroke:'rgba(29,78,216,',  base:.66},
      {spd:.00058,amp:.020,freq:3.1,ph:3.4,   fill:'rgba(29,78,216,',  stroke:'rgba(30,64,175,',  base:.70},
    ];

    // Floating particles (subtle, tech feel)
    const pts=Array.from({length:28},()=>({
      x:Math.random(),y:Math.random()*.5,
      vx:(Math.random()-.5)*.00012,vy:(Math.random()-.5)*.00010,
      r:.6+Math.random()*1.2,ph:Math.random()*Math.PI*2,sp:.006+Math.random()*.008,
    }));

    // Bubbles
    const bubbles=Array.from({length:16},()=>({
      x:Math.random(),y:.45+Math.random()*.45,
      r:1.5+Math.random()*4,vy:-.00025-Math.random()*.0003,
      ph:Math.random()*Math.PI*2,sp:.007+Math.random()*.005,a:.06+Math.random()*.10,
    }));

    // Sparkles
    const sparks=Array.from({length:24},()=>({
      x:Math.random(),yb:.46+Math.random()*.18,
      ph:Math.random()*Math.PI*2,sp:.018+Math.random()*.014,sz:.6+Math.random()*1.2,
    }));

    const draw=()=>{
      const W=cv.width,H=cv.height;
      frame++;const t=frame*.016;

      // White → sky → ocean blue background
      const bg=ctx.createLinearGradient(0,0,0,H);
      bg.addColorStop(0,'#ffffff');
      bg.addColorStop(.30,'#f0f9ff');
      bg.addColorStop(.52,'#dbeafe');
      bg.addColorStop(.75,'#bfdbfe');
      bg.addColorStop(1,'#93c5fd');
      ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);

      // Sun glow top-right
      const sg=ctx.createRadialGradient(W*.78,H*.08,0,W*.78,H*.08,W*.5);
      sg.addColorStop(0,'rgba(254,240,138,0.45)');
      sg.addColorStop(.3,'rgba(253,224,71,0.15)');
      sg.addColorStop(1,'rgba(0,0,0,0)');
      ctx.fillStyle=sg;ctx.fillRect(0,0,W,H);

      // Soft top ambient
      const ta=ctx.createRadialGradient(W*.5,0,0,W*.5,0,H*.55);
      ta.addColorStop(0,'rgba(219,234,254,0.4)');
      ta.addColorStop(1,'rgba(0,0,0,0)');
      ctx.fillStyle=ta;ctx.fillRect(0,0,W,H);

      // Floating dots (sky area)
      pts.forEach(p=>{
        p.x+=p.vx;p.y+=p.vy;p.ph+=p.sp;
        if(p.x<0)p.x=1;if(p.x>1)p.x=0;if(p.y<0)p.y=.5;if(p.y>.5)p.y=0;
        const a=(.08+Math.sin(p.ph)*.06)*(1-p.y/.5);
        ctx.beginPath();ctx.arc(p.x*W,p.y*H,p.r,0,Math.PI*2);
        ctx.fillStyle='rgba(59,130,246,'+a+')';ctx.fill();
      });

      // Connection lines between nearby particles
      ctx.lineWidth=.4;
      for(let i=0;i<pts.length;i++)for(let j=i+1;j<pts.length;j++){
        const dx=(pts[i].x-pts[j].x)*W,dy=(pts[i].y-pts[j].y)*H;
        const d=Math.sqrt(dx*dx+dy*dy);
        if(d<W*.14){
          ctx.strokeStyle='rgba(147,197,253,'+(0.06*(1-d/(W*.14)))+')';
          ctx.beginPath();ctx.moveTo(pts[i].x*W,pts[i].y*H);ctx.lineTo(pts[j].x*W,pts[j].y*H);ctx.stroke();
        }
      }

      // Horizon shimmer line
      const hy=H*.48;
      const hl=ctx.createLinearGradient(0,hy,W,hy);
      hl.addColorStop(0,'rgba(255,255,255,0)');hl.addColorStop(.3,'rgba(255,255,255,0.4)');
      hl.addColorStop(.5,'rgba(219,234,254,0.6)');hl.addColorStop(.7,'rgba(255,255,255,0.4)');
      hl.addColorStop(1,'rgba(255,255,255,0)');
      ctx.fillStyle=hl;ctx.fillRect(0,hy-1,W,3);

      // WAVES
      waveConfigs.forEach((w,wi)=>{
        const ph=t*w.spd*1000+w.ph;
        // fill
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
        // crest line
        ctx.beginPath();
        for(let x=0;x<=W;x+=2){
          const xn=x/W;
          const y=H*(w.base+Math.sin(xn*Math.PI*w.freq+ph)*w.amp
            +Math.sin(xn*Math.PI*w.freq*1.7+ph*.65)*(w.amp*.3)
            +Math.sin(xn*Math.PI*w.freq*.9+ph*1.4)*(w.amp*.18));
          x===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
        }
        ctx.strokeStyle=w.stroke+[.07,.06,.06,.05,.05][wi]+')';ctx.lineWidth=1.1;ctx.stroke();
        // foam on front wave
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

      // Reflection shimmers on water
      for(let rx=W*.02;rx<W;rx+=W*.065){
        const ry=H*(.52+Math.sin(t*.35+rx*.008)*.05);
        const ra=.03+Math.sin(t*.7+rx*.015)*.02;
        const rg=ctx.createLinearGradient(rx,ry,rx+W*.05,ry);
        rg.addColorStop(0,'rgba(255,255,255,0)');rg.addColorStop(.5,'rgba(255,255,255,'+ra+')');rg.addColorStop(1,'rgba(255,255,255,0)');
        ctx.fillStyle=rg;ctx.fillRect(rx,ry,W*.05,2);
      }

      // Bubbles
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

      // Sparkles
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

  // Shared input/label styles — clean light theme
  const inp={
    width:'100%',padding:'12px 15px',borderRadius:10,fontSize:14,outline:'none',
    background:'#f8fafc',border:'1.5px solid #e2e8f0',color:'#0f172a',
    fontFamily:'inherit',transition:'border-color .18s,box-shadow .18s',boxSizing:'border-box',
  };
  const lbl={display:'block',fontSize:11,fontWeight:700,letterSpacing:.07,
    textTransform:'uppercase',color:'#94a3b8',marginBottom:6};

  // ── Left panel: canvas fills everything ──
  const leftPanel=html`
    <div style=${{
      width:'48%',flexShrink:0,minHeight:'100vh',
      position:'relative',overflow:'hidden',
    }}>
      <canvas ref=${cvRef} style=${{
        position:'absolute',top:0,left:0,
        width:'100%',height:'100%',display:'block',
      }}></canvas>

      <!-- Brand overlay top-left -->
      <div style=${{position:'absolute',top:24,left:24,zIndex:10,display:'flex',alignItems:'center',gap:9}}>
        <div style=${{width:32,height:32,borderRadius:9,background:'white',display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 2px 12px rgba(59,130,246,0.2)'}}>
          <svg width="17" height="17" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="#1d4ed8"/><circle cx="32" cy="11" r="6" fill="#1d4ed8"/><circle cx="51" cy="43" r="6" fill="#1d4ed8"/><circle cx="13" cy="43" r="6" fill="#1d4ed8"/><line x1="32" y1="17" x2="32" y2="23" stroke="#1d4ed8" stroke-width="3.5" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="#1d4ed8" stroke-width="3.5" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="#1d4ed8" stroke-width="3.5" stroke-linecap="round"/></svg>
        </div>
        <span style=${{fontFamily:"'Syne',sans-serif",fontWeight:800,fontSize:15,color:'#1e3a5f',letterSpacing:-.3}}>ProjectFlowPro</span>
      </div>

      <!-- Center tagline -->
      <div style=${{
        position:'absolute',top:'50%',left:'50%',
        transform:'translate(-50%,-50%)',
        textAlign:'center',zIndex:10,pointerEvents:'none',
        width:'80%',
      }}>
        <div style=${{display:'inline-flex',alignItems:'center',gap:7,background:'rgba(255,255,255,0.7)',border:'1px solid rgba(147,197,253,0.6)',padding:'5px 14px',borderRadius:100,marginBottom:18,backdropFilter:'blur(8px)'}}>
          <div style=${{width:5,height:5,borderRadius:'50%',background:'#3b82f6'}}></div>
          <span style=${{fontSize:11,color:'#1d4ed8',fontWeight:700,letterSpacing:.05}}>AI-POWERED · MULTI-TENANT · v4.0</span>
        </div>
        <h2 style=${{fontFamily:"'Syne',sans-serif",fontSize:'clamp(1.5rem,2.5vw,2rem)',fontWeight:800,color:'#1e3a5f',lineHeight:1.2,marginBottom:10,letterSpacing:-.03}}>
          Where teams<br/>ship together
        </h2>
        <p style=${{fontSize:13,color:'rgba(30,58,95,0.65)',lineHeight:1.7}}>
          Tasks · AI assistant · Huddles<br/>Timeline · Tickets
        </p>
      </div>

      <!-- Bottom feature pills -->
      <div style=${{
        position:'absolute',bottom:28,left:0,right:0,
        display:'flex',justifyContent:'center',gap:8,flexWrap:'wrap',
        padding:'0 20px',zIndex:10,
      }}>
        ${['📋 Tasks','🤖 AI','📅 Timeline','📞 Huddles','🎫 Tickets'].map(f=>html`
          <div key=${f} style=${{
            background:'rgba(255,255,255,0.72)',
            border:'1px solid rgba(147,197,253,0.5)',
            backdropFilter:'blur(8px)',
            padding:'5px 12px',borderRadius:100,
            fontSize:11,fontWeight:600,color:'#1d4ed8',
          }}>${f}</div>
        `)}
      </div>
    </div>`;

  // ── Right panel: clean white form ──
  const rightPanel=(child)=>html`
    <div style=${{
      flex:1,minHeight:'100vh',background:'#ffffff',
      display:'flex',alignItems:'center',justifyContent:'center',
      padding:'40px 36px',overflowY:'auto',
      borderLeft:'1px solid #f1f5f9',
    }}>
      <div style=${{width:'100%',maxWidth:400}}>
        ${child}
      </div>
    </div>`;

  // ── OTP Screen ──
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
              style=${{flex:1,height:54,borderRadius:10,textAlign:'center',fontSize:20,fontWeight:700,fontFamily:'monospace',outline:'none',boxSizing:'border-box',transition:'all .15s',
                background:otpCode[i]?'#eff6ff':'#f8fafc',
                border:'1.5px solid '+(otpCode[i]?'#3b82f6':'#e2e8f0'),
                color:'#0f172a',boxShadow:otpCode[i]?'0 0 0 3px rgba(59,130,246,0.1)':'none'}}
              maxLength=1 value=${otpCode[i]||''}
              onInput=${e=>handleOtpInput(i,e.target.value)}
              onKeyDown=${e=>handleOtpKey(i,e)}
              onFocus=${e=>e.target.select()}
            />`)}
        </div>
        ${err?html`<div style=${{color:'#dc2626',fontSize:13,padding:'10px 14px',background:'#fef2f2',borderRadius:9,border:'1px solid #fecaca',marginBottom:14}}>${err}</div>`:null}
        <button onClick=${submitOtp} disabled=${busy||otpCode.length!==6}
          style=${{width:'100%',height:46,borderRadius:10,border:'none',fontFamily:'inherit',
            background:otpCode.length===6?'#2563eb':'#e2e8f0',
            color:otpCode.length===6?'#fff':'#94a3b8',
            fontSize:14,fontWeight:700,cursor:otpCode.length===6?'pointer':'default',
            transition:'all .18s',marginBottom:14,
            boxShadow:otpCode.length===6?'0 4px 14px rgba(37,99,235,0.3)':'none'}}>
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

  // ── Main login/register ──
  return html`
    <div style=${{width:'100vw',minHeight:'100vh',display:'flex',overflow:'hidden'}}>
      ${leftPanel}
      ${rightPanel(html`
        <!-- Logo -->
        <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:28}}>
          <div style=${{width:28,height:28,borderRadius:7,background:'#2563eb',display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 2px 8px rgba(37,99,235,0.3)'}}>
            <svg width="15" height="15" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white"/><circle cx="51" cy="43" r="6" fill="white"/><circle cx="13" cy="43" r="6" fill="white"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/></svg>
          </div>
          <span style=${{fontFamily:"'Syne',sans-serif",fontWeight:800,fontSize:14.5,color:'#0f172a',letterSpacing:-.3}}>ProjectFlowPro</span>
        </div>

        <!-- Heading -->
        <h1 style=${{fontFamily:"'Syne',sans-serif",fontSize:'clamp(1.5rem,2.2vw,1.85rem)',fontWeight:800,color:'#0f172a',marginBottom:6,letterSpacing:-.03,lineHeight:1.15}}>
          ${tab==='login'?'Welcome back':'Create account'}
        </h1>
        <p style=${{fontSize:13.5,color:'#64748b',marginBottom:24,lineHeight:1.6}}>
          ${tab==='login'?'Sign in to your ProjectFlowPro workspace':'Set up your workspace and start shipping'}
        </p>

        <!-- Tab switcher -->
        <div style=${{display:'flex',background:'#f1f5f9',borderRadius:11,padding:3,marginBottom:22}}>
          ${['login','register'].map(tp=>html`
            <button key=${tp} onClick=${()=>{setTab(tp);setErr('');}}
              style=${{flex:1,height:35,fontSize:13,fontWeight:600,border:'none',cursor:'pointer',
                borderRadius:9,fontFamily:'inherit',transition:'all .16s',
                background:tab===tp?'#ffffff':'transparent',
                color:tab===tp?'#0f172a':'#94a3b8',
                boxShadow:tab===tp?'0 1px 4px rgba(0,0,0,0.08)':'none'}}>
              ${tp==='login'?'Sign In':'Create Account'}
            </button>`)}
        </div>

        ${tab==='register'?html`
          <div style=${{display:'flex',background:'#f1f5f9',borderRadius:9,padding:3,marginBottom:16}}>
            ${[['create','🏢 New Workspace'],['join','🔗 Join Workspace']].map(([m,lbl])=>html`
              <button key=${m} onClick=${()=>setRegMode(m)}
                style=${{flex:1,height:29,fontSize:11,fontWeight:600,border:'none',cursor:'pointer',
                  borderRadius:7,fontFamily:'inherit',transition:'all .16s',
                  background:regMode===m?'#ffffff':'transparent',
                  color:regMode===m?'#374151':'#94a3b8',
                  boxShadow:regMode===m?'0 1px 3px rgba(0,0,0,0.07)':'none'}}>
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
            <input style=${inp} type="email" placeholder="you@company.com" value=${email}
              onInput=${e=>setEmail(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&go()}/></div>

          <div><label style=${lbl}>Password</label>
            <div style=${{position:'relative'}}>
              <input style=${{...inp,paddingRight:42}} type=${showPw?'text':'password'}
                placeholder="••••••••••" value=${pw}
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
            style=${{height:46,borderRadius:10,border:'none',cursor:busy?'default':'pointer',
              fontFamily:'inherit',
              background:busy?'#bfdbfe':'#2563eb',
              color:busy?'#93c5fd':'#ffffff',
              fontSize:14,fontWeight:700,letterSpacing:.01,
              transition:'all .18s',marginTop:2,
              boxShadow:busy?'none':'0 4px 14px rgba(37,99,235,0.3),inset 0 1px 0 rgba(255,255,255,0.15)'}}>
            ${busy?'Please wait...':(tab==='login'?'Sign In →':regMode==='create'?'Create Workspace & Account →':'Join Workspace →')}
          </button>
        </div>

        <p style=${{fontSize:12.5,color:'#94a3b8',marginTop:18,textAlign:'center'}}>
          ${tab==='login'
            ?html`New to ProjectFlowPro? <button onClick=${()=>{setTab('register');setErr('');}} style=${{background:'none',border:'none',color:'#2563eb',cursor:'pointer',fontSize:12.5,fontWeight:600,padding:'0 0 0 2px',fontFamily:'inherit'}}>Create an account</button>`
            :html`Already have an account? <button onClick=${()=>{setTab('login');setErr('');}} style=${{background:'none',border:'none',color:'#2563eb',cursor:'pointer',fontSize:12.5,fontWeight:600,padding:'0 0 0 2px',fontFamily:'inherit'}}>Sign in</button>`}
        </p>
      `)}
    </div>`;
}

/* ─── SidebarCallsList ─────────────────────────────────────────────────────── */
function SidebarCallsList({cu,onJoin,currentRoomId}){
  const [calls,setCalls]=useState([]);
  useEffect(()=>{
    const load=()=>api.get('/api/calls').then(d=>{if(Array.isArray(d))setCalls(d);});
    load();
    const id=setInterval(load,5000);
    return()=>clearInterval(id);
  },[]);
  // Filter out: rooms user is already in, and rooms that match current active room
  const joinable=calls.filter(c=>{
    const parts=JSON.parse(c.participants||'[]');
    return !parts.includes(cu.id) && c.id!==currentRoomId;
  });
  if(!joinable.length)return html`
    <div style=${{textAlign:'center',padding:'14px 8px'}}>
      <div style=${{fontSize:22,marginBottom:5}}>📞</div>
      <p style=${{fontSize:10,color:'var(--tx3)',lineHeight:1.5}}>No active huddles.<br/>Start one to connect with your team.</p>
    </div>`;
  return html`<div style=${{display:'flex',flexDirection:'column',gap:5}}>
    ${joinable.map(c=>{
      const parts=JSON.parse(c.participants||'[]');
      return html`<div key=${c.id} style=${{background:'rgba(34,197,94,.06)',border:'1px solid rgba(34,197,94,.2)',borderRadius:10,padding:'9px 10px'}}>
        <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:6}}>
          <div style=${{width:7,height:7,borderRadius:'50%',background:'#22c55e',animation:'pulse 1.5s infinite',flexShrink:0}}></div>
          <div style=${{flex:1,minWidth:0}}>
            <div style=${{fontSize:11,fontWeight:700,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${c.name}</div>
            <div style=${{fontSize:9,color:'var(--tx3)'}}>${parts.length} participant${parts.length!==1?'s':''}</div>
          </div>
        </div>
        <button style=${{width:'100%',height:28,borderRadius:7,border:'none',background:'linear-gradient(135deg,#22c55e,#16a34a)',color:'#fff',cursor:'pointer',fontWeight:700,fontSize:11,display:'flex',alignItems:'center',justifyContent:'center',gap:5}}
          onClick=${()=>onJoin(c.id,c.name)}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.61 3.28a2 2 0 0 1 1.99-2.18h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 8.96a16 16 0 0 0 6.29 6.29l1.24-.82a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
          Join Huddle
        </button>
      </div>`;
    })}
  </div>`;
}

/* ─── TeamSidePanel ────────────────────────────────────────────────────────── */
function TeamSidePanel({cu,onClose,onSelectTeam,selectedTeam,teams,users,projects,tasks,onSetView,onReloadTeams,teamCtx,setTeamCtx,activeTeam}){
  const umap=safe(users).reduce((a,u)=>{a[u.id]=u;return a;},{});
  const [search,setSearch]=useState('');
  const [dashboard,setDashboard]=useState(null); // loaded team dashboard data
  const [loadingDash,setLoadingDash]=useState(false);

  useEffect(()=>{
    if(!selectedTeam){setDashboard(null);return;}
    setLoadingDash(true);
    api.get('/api/teams/'+selectedTeam+'/dashboard').then(d=>{
      setDashboard(d&&!d.error?d:null);
      setLoadingDash(false);
    }).catch(()=>setLoadingDash(false));
  },[selectedTeam]);

  const filtered=safe(teams).filter(t=>!search||t.name.toLowerCase().includes(search.toLowerCase()));

  /* ── Team drill-down dashboard ── */
  if(selectedTeam){
    const team=teams.find(t=>t.id===selectedTeam);
    if(!team)return null;
    const memberIds=JSON.parse(team.member_ids||'[]');
    const lead=umap[team.lead_id];
    const members=memberIds.map(id=>umap[id]).filter(Boolean);
    const sum=dashboard&&dashboard.summary;
    const memberStats=dashboard&&dashboard.member_stats||[];
    const teamProjects=dashboard&&dashboard.projects||[];

    return html`
      <div style=${{width:310,background:'var(--sf)',borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',height:'100vh',flexShrink:0,overflow:'hidden'}}>
        <!-- Header -->
        <div style=${{padding:'12px 14px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:8,flexShrink:0}}>
          <button onClick=${()=>onSelectTeam(null)} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:18,padding:'2px 6px',borderRadius:6,lineHeight:1}} title="Back">←</button>
          <div style=${{flex:1,minWidth:0}}>
            <div style=${{fontSize:13,fontWeight:700,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${team.name}</div>
            ${lead?html`<div style=${{fontSize:10,color:'var(--tx3)'}}>Lead: <b style=${{color:'var(--cy)'}}>${lead.name}</b></div>`:
              html`<div style=${{fontSize:10,color:'var(--tx3)'}}>${members.length} members</div>`}
          </div>
          ${teamCtx===team.id?html`
            <button onClick=${()=>setTeamCtx&&setTeamCtx('')}
              style=${{fontSize:10,padding:'4px 8px',borderRadius:7,border:'1px solid var(--ac)',background:'var(--ac)',color:'var(--ac-tx)',cursor:'pointer',fontWeight:700,flexShrink:0,whiteSpace:'nowrap'}}>
              ✓ Active
            </button>`:html`
            <button onClick=${()=>setTeamCtx&&setTeamCtx(team.id)}
              style=${{fontSize:10,padding:'4px 8px',borderRadius:7,border:'1px solid var(--ac)',background:'transparent',color:'var(--ac)',cursor:'pointer',fontWeight:700,flexShrink:0,whiteSpace:'nowrap'}}>
              Switch →
            </button>`}
          <button onClick=${onClose} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:16,padding:'2px 6px'}} title="Close">✕</button>
        </div>
        <!-- Quick nav row -->
        <div style=${{display:'flex',gap:6,padding:'8px 12px',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
          <button onClick=${()=>{setTeamCtx&&setTeamCtx(team.id);onSetView('projects');onClose();}}
            style=${{flex:1,padding:'6px 8px',borderRadius:7,border:'1px solid var(--bd)',background:'var(--sf2)',color:'var(--tx2)',cursor:'pointer',fontSize:11,fontWeight:600,transition:'all .12s'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx2)';}}>
            📁 Projects
          </button>
          <button onClick=${()=>{setTeamCtx&&setTeamCtx(team.id);onSetView('tasks');onClose();}}
            style=${{flex:1,padding:'6px 8px',borderRadius:7,border:'1px solid var(--bd)',background:'var(--sf2)',color:'var(--tx2)',cursor:'pointer',fontSize:11,fontWeight:600,transition:'all .12s'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx2)';}}>
            ☑ Tasks
          </button>
          <button onClick=${()=>{setTeamCtx&&setTeamCtx(team.id);onSetView('productivity');onClose();}}
            style=${{flex:1,padding:'6px 8px',borderRadius:7,border:'1px solid var(--bd)',background:'var(--sf2)',color:'var(--tx2)',cursor:'pointer',fontSize:11,fontWeight:600,transition:'all .12s'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx2)';}}>
            📊 Stats
          </button>
        </div>

        <div style=${{flex:1,overflowY:'auto'}}>
          ${loadingDash?html`<div style=${{textAlign:'center',padding:'40px 0',color:'var(--tx3)',fontSize:12}}>Loading...</div>`:null}

          ${!loadingDash&&sum?html`
          <!-- Summary KPI strip -->
          <div style=${{display:'grid',gridTemplateColumns:'repeat(3,1fr)',borderBottom:'1px solid var(--bd)'}}>
            ${[
              {l:'Projects',v:sum.total_projects,c:'var(--ac)'},
              {l:'Tasks',v:sum.total_tasks,c:'var(--tx)'},
              {l:'Done',v:sum.completed,c:'var(--gn)'},
              {l:'In Prog',v:sum.in_progress,c:'var(--cy)'},
              {l:'Blocked',v:sum.blocked,c:'var(--rd)'},
              {l:'Pending',v:sum.pending,c:'var(--am)'},
            ].map((s,i)=>html`
              <div key=${i} style=${{textAlign:'center',padding:'10px 4px',borderRight:i%3<2?'1px solid var(--bd)':'none',borderBottom:i<3?'1px solid var(--bd)':'none'}}>
                <div style=${{fontSize:18,fontWeight:800,color:s.c,fontFamily:'monospace',lineHeight:1}}>${s.v}</div>
                <div style=${{fontSize:9,color:'var(--tx3)',marginTop:2,textTransform:'uppercase',letterSpacing:.4}}>${s.l}</div>
              </div>`)}
          </div>

          <!-- Member workload -->
          <div style=${{padding:'10px 12px',borderBottom:'1px solid var(--bd)'}}>
            <div style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7,marginBottom:8}}>👥 Member Workload</div>
            ${memberStats.length===0?html`<div style=${{fontSize:11,color:'var(--tx3)',textAlign:'center',padding:'8px 0'}}>No tasks assigned yet</div>`:null}
            ${memberStats.map(m=>html`
              <div key=${m.id} style=${{display:'flex',alignItems:'center',gap:8,marginBottom:8,padding:'7px 8px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)'}}>
                <${Av} u=${m} size=${28}/>
                <div style=${{flex:1,minWidth:0}}>
                  <div style=${{fontSize:11,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${m.name}</div>
                  <div style=${{fontSize:9,color:'var(--tx3)'}}>${m.role}</div>
                </div>
                <div style=${{display:'flex',gap:5,fontSize:10,fontFamily:'monospace'}}>
                  <span style=${{color:'var(--gn)',fontWeight:700}} title="Completed">${m.completed}✓</span>
                  <span style=${{color:'var(--cy)'}} title="In Progress">${m.in_progress}⟳</span>
                  ${m.blocked>0?html`<span style=${{color:'var(--rd)',fontWeight:700}} title="Blocked">${m.blocked}✗</span>`:null}
                  ${m.overdue>0?html`<span style=${{color:'var(--am)',fontWeight:700}} title="Overdue">${m.overdue}!</span>`:null}
                </div>
              </div>`)}
          </div>

          <!-- Projects list -->
          <div style=${{padding:'10px 12px'}}>
            <div style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7,marginBottom:8}}>📁 Projects (${teamProjects.length})</div>
            ${teamProjects.length===0?html`<div style=${{fontSize:11,color:'var(--tx3)',textAlign:'center',padding:'8px 0'}}>No projects yet</div>`:null}
            ${teamProjects.map(p=>{
              const pt=safe(tasks).filter(t=>t.project===p.id);
              const done=pt.filter(t=>t.stage==='completed').length;
              const pc=pt.length?Math.round(pt.reduce((a,t)=>a+(t.pct||0),0)/pt.length):(p.progress||0);
              return html`
                <div key=${p.id} style=${{padding:'8px 10px',borderRadius:8,border:'1px solid var(--bd)',marginBottom:6,background:'var(--sf2)',cursor:'pointer',borderLeft:'3px solid '+p.color,transition:'background .1s'}}
                  onClick=${()=>{onSetView('projects');onClose();}}
                  onMouseEnter=${e=>e.currentTarget.style.background='rgba(255,255,255,.06)'}
                  onMouseLeave=${e=>e.currentTarget.style.background='var(--sf2)'}>
                  <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)',marginBottom:4,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${p.name}</div>
                  <div style=${{display:'flex',alignItems:'center',gap:6,marginBottom:4}}>
                    <div style=${{flex:1,height:3,background:'var(--bd)',borderRadius:100,overflow:'hidden'}}>
                      <div style=${{height:'100%',width:pc+'%',background:p.color,borderRadius:100}}></div>
                    </div>
                    <span style=${{fontSize:9,fontFamily:'monospace',color:'var(--tx3)'}}>${pc}%</span>
                  </div>
                  <div style=${{display:'flex',gap:8,fontSize:10}}>
                    <span style=${{color:'var(--tx3)'}}>${pt.length} tasks</span>
                    <span style=${{color:'var(--gn)'}}>${done} done</span>
                    <span style=${{color:'var(--am)'}}>${pt.length-done} open</span>
                  </div>
                </div>`;
            })}
          </div>`:null}

          ${!loadingDash&&!sum?html`<div style=${{textAlign:'center',padding:'40px 12px',color:'var(--tx3)',fontSize:12}}>
            <div style=${{fontSize:28,marginBottom:8}}>📊</div>
            No task data found for this team yet.<br/>Assign tasks to team members to see stats here.
          </div>`:null}
        </div>
      </div>`;
  }

  /* ── Team list cards ── */
  return html`
    <div style=${{width:240,background:'var(--sf)',borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',height:'100vh',flexShrink:0}}>
      <div style=${{padding:'12px 14px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',justifyContent:'space-between',flexShrink:0}}>
        <div>
          <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)'}}>👥 Teams</span>
          ${activeTeam?html`<div style=${{fontSize:10,color:'var(--ac)',marginTop:2}}>Viewing: <b>${activeTeam.name}</b></div>`:html`<div style=${{fontSize:10,color:'var(--tx3)',marginTop:2}}>All workspace data</div>`}
        </div>
        <div style=${{display:'flex',gap:5,alignItems:'center'}}>
          ${activeTeam?html`<button onClick=${()=>setTeamCtx&&setTeamCtx('')} style=${{fontSize:10,padding:'3px 8px',borderRadius:6,border:'1px solid var(--bd)',background:'transparent',color:'var(--tx3)',cursor:'pointer',whiteSpace:'nowrap'}}>× All</button>`:null}
          ${cu&&(cu.role==='Admin'||cu.role==='Manager')?html`
            <button title="Manage Teams" onClick=${()=>{onSetView('team');}}
              style=${{fontSize:10,padding:'3px 8px',borderRadius:6,border:'1px solid var(--ac)',background:'transparent',color:'var(--ac)',cursor:'pointer',whiteSpace:'nowrap',fontWeight:600}}>
              ⚙ Manage
            </button>`:null}
          <button onClick=${onClose} style=${{background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:16,padding:'2px 6px'}} title="Close">✕</button>
        </div>
      </div>
      <div style=${{padding:'8px 10px',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
        <input class="inp" placeholder="Search teams..." value=${search}
          style=${{height:26,fontSize:11,width:'100%'}}
          onInput=${e=>setSearch(e.target.value)}/>
      </div>
      <div style=${{flex:1,overflowY:'auto',padding:'6px'}}>
        ${filtered.length===0?html`
          <div style=${{textAlign:'center',padding:'24px 8px',color:'var(--tx3)',fontSize:12}}>
            ${safe(teams).length===0?html`<div><div style=${{fontSize:28,marginBottom:6}}>🏷</div>No teams yet.${cu&&(cu.role==='Admin'||cu.role==='Manager')?html`<br/>Click <b>⚙ Manage</b> above to create teams.`:html`<br/>Ask your Admin to create teams.`}</div>`:'No teams match your search.'}
          </div>`:null}
        ${filtered.map(team=>{
          const memberIds=JSON.parse(team.member_ids||'[]');
          const lead=umap[team.lead_id];
          const members=memberIds.map(id=>umap[id]).filter(Boolean);
          const teamTasks=safe(tasks).filter(t=>{
            const byTeam=t.team_id===team.id;
            const byMember=t.assignee&&memberIds.includes(t.assignee);
            return byTeam||byMember;
          });
          const done=teamTasks.filter(t=>t.stage==='completed').length;
          const blocked=teamTasks.filter(t=>t.stage==='blocked').length;
          const teamProjs=new Set(teamTasks.map(t=>t.project).filter(Boolean)).size;
          return html`
            <div key=${team.id}
              style=${{padding:'10px 12px',borderRadius:10,border:'2px solid '+(teamCtx===team.id?'var(--ac)':'var(--bd)'),marginBottom:7,background:teamCtx===team.id?'rgba(170,255,0,.06)':'var(--sf2)',cursor:'pointer',transition:'all .12s'}}
              onClick=${()=>{
                onSelectTeam(team.id);
                setTeamCtx&&setTeamCtx(team.id);
              }}
              onMouseEnter=${e=>{e.currentTarget.style.background='rgba(255,255,255,.06)';e.currentTarget.style.borderColor='var(--ac)77';}}
              onMouseLeave=${e=>{e.currentTarget.style.background=teamCtx===team.id?'rgba(170,255,0,.06)':'var(--sf2)';e.currentTarget.style.borderColor=teamCtx===team.id?'var(--ac)':'var(--bd)';}}>
              <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:7}}>
                <div style=${{width:9,height:9,borderRadius:2,background:teamCtx===team.id?'var(--ac)':'var(--tx3)',flexShrink:0}}></div>
                <span style=${{fontSize:12,fontWeight:700,color:'var(--tx)',flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${team.name}</span>
                ${teamCtx===team.id?html`
                  <span style=${{fontSize:9,color:'var(--ac)',fontWeight:700,background:'rgba(170,255,0,.12)',padding:'2px 6px',borderRadius:4,flexShrink:0}}>ACTIVE</span>`:null}
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="var(--tx3)" strokeWidth="2.5" strokeLinecap="round"><polyline points="9 18 15 12 9 6"/></svg>
              </div>
              ${lead?html`<div style=${{fontSize:10,color:'var(--tx3)',marginBottom:6}}>Lead: <b style=${{color:'var(--cy)'}}>${lead.name}</b></div>`:null}
              <!-- Mini KPIs -->
              <div style=${{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:4,marginBottom:7}}>
                ${[['Tasks',teamTasks.length,'var(--tx)'],['Done',done,'var(--gn)'],['Proj',teamProjs,'var(--ac)']].map(([l,v,c])=>html`
                  <div key=${l} style=${{textAlign:'center',padding:'4px 2px',background:'var(--sf)',borderRadius:5,border:'1px solid var(--bd)'}}>
                    <div style=${{fontSize:13,fontWeight:700,color:c,fontFamily:'monospace',lineHeight:1}}>${v}</div>
                    <div style=${{fontSize:8,color:'var(--tx3)',marginTop:1,textTransform:'uppercase'}}>${l}</div>
                  </div>`)}
              </div>
              ${blocked>0?html`<div style=${{fontSize:10,color:'var(--rd)',fontWeight:600,marginBottom:6}}>⚠ ${blocked} blocked task${blocked!==1?'s':''}</div>`:null}
              <!-- Member avatars -->
              <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
                <div style=${{display:'flex'}}>
                  ${members.slice(0,5).map((m,i)=>html`
                    <div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-5:0,border:'1.5px solid var(--sf2)',borderRadius:'50%',zIndex:5-i}}>
                      <${Av} u=${m} size=${20}/>
                    </div>`)}
                  ${members.length>5?html`<span style=${{fontSize:9,color:'var(--tx3)',marginLeft:5,alignSelf:'center'}}>+${members.length-5}</span>`:null}
                </div>
                <span style=${{fontSize:9,color:'var(--tx3)'}}>${members.length} member${members.length!==1?'s':''}</span>
              </div>
            </div>`;
        })}
      </div>
    </div>`;
}

/* ─── Sidebar ─────────────────────────────────────────────────────────────── */
function Sidebar({cu,view,setView,onLogout,unread,dmUnread,col,setCol,wsName,callState,onCallAction,dark,setDark,teams,users,projects,tasks,teamCtx,setTeamCtx,activeTeam}){
  const inCall=callState&&callState.status==='in-call';
  const fmtTime=s=>{const m=Math.floor(s/60);const sec=s%60;return m+':'+(sec<10?'0':'')+sec;};
  const isAdminManager=cu&&(cu.role==='Admin'||cu.role==='Manager');
  const baseView=(view||'dashboard').split(':')[0];

  // Nav items per role
  const NAV_ICONS={
    dashboard:    html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>`,
    projects:     html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`,
    tasks:        html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>`,
    messages:     html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
    tickets:      html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M2 9a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v1.5a1.5 1.5 0 0 0 0 3V15a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-1.5a1.5 1.5 0 0 0 0-3V9z"/><line x1="9" y1="7" x2="9" y2="17" strokeDasharray="2 2"/></svg>`,
    timeline:     html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="14" x2="10" y2="14"/><line x1="8" y1="18" x2="14" y2="18"/></svg>`,
    productivity: html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/><line x1="2" y1="20" x2="22" y2="20"/></svg>`,
    reminders:    html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
    team:         html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
  };
  const adminNav=[
    {id:'dashboard',   label:'Dashboard'},
    {id:'projects',    label:'Projects'},
    {id:'tasks',       label:'Task Board'},
    {id:'messages',    label:'Channels'},
    {id:'tickets',     label:'Tickets'},
    {id:'timeline',    label:'Timeline Tracker'},
    {id:'productivity',label:'Dev Productivity'},
    {id:'reminders',   label:'Reminders'},
    {id:'team',        label:'Team Management'},
  ];
  const devNav=[
    {id:'dashboard', label:'Dashboard'},
    {id:'projects',  label:'Projects'},
    {id:'tasks',     label:'Task Board'},
    {id:'messages',  label:'Channels'},
    {id:'tickets',   label:'Tickets'},
    {id:'timeline',  label:'Timeline'},
    {id:'reminders', label:'Reminders'},
  ];
  const navItems=isAdminManager?adminNav:devNav;

  const themeIcon=dark
    ?html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`
    :html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;

  const W=col?64:200; // collapsed=64px, expanded=200px

  return html`
    <aside style=${{
      width:W,minWidth:W,maxWidth:W,
      background:'#0f172a',
      display:'flex',flexDirection:'column',
      height:'100vh',flexShrink:0,overflow:'visible',
      borderRight:'1px solid rgba(37,99,235,0.15)',
      transition:'width .2s ease,min-width .2s ease,max-width .2s ease',
      position:'relative'
    }}>

      <!-- ── Logo / workspace name ── -->
      <div style=${{
        padding:col?'14px 0':'12px 14px',
        display:'flex',alignItems:'center',
        gap:8,flexShrink:0,
        borderBottom:'1px solid rgba(37,99,235,0.15)',
        justifyContent:col?'center':'flex-start',
        minHeight:52
      }}>
        <div style=${{width:28,height:28,borderRadius:8,background:'#2563eb',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0,boxShadow:'0 2px 8px rgba(37,99,235,0.4)'}}>
          <svg width="14" height="14" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white" opacity=".9"/><circle cx="51" cy="43" r="6" fill="white" opacity=".9"/><circle cx="13" cy="43" r="6" fill="white" opacity=".9"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" strokeWidth="3.5" strokeLinecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" strokeWidth="3.5" strokeLinecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" strokeWidth="3.5" strokeLinecap="round"/></svg>
        </div>
        ${!col?html`<div style=${{flex:1,minWidth:0}}>
          <div style=${{fontSize:12,fontWeight:700,color:'#ffffff',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${wsName||'ProjectFlowPro'}</div>
          ${activeTeam?html`<div style=${{fontSize:10,color:'var(--ac)',fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'flex',alignItems:'center',gap:4}}>
            ${!isAdminManager?html`<span style=${{color:'rgba(255,255,255,.3)',fontWeight:400}}>My Team</span>`:null}
            ${activeTeam.name}
          </div>`
          :html`<div style=${{fontSize:10,color:'rgba(148,163,184,0.7)'}}>Workspace</div>`}
        </div>`:null}
      </div>

      <!-- ── Nav items ── -->
      <nav style=${{flex:1,overflowY:'auto',padding:'8px 6px',display:'flex',flexDirection:'column',gap:2}}>
        ${navItems.map(it=>html`
          <button key=${it.id}
            title=${col?it.label:''}
            onClick=${()=>setView(it.id)}
            style=${{
              display:'flex',alignItems:'center',
              gap:col?0:10,
              width:'100%',
              padding:col?'10px 0':'9px 10px',
              borderRadius:9,border:'none',cursor:'pointer',
              background:baseView===it.id?'rgba(37,99,235,0.18)':'transparent',
              color:baseView===it.id?'var(--ac)':'rgba(255,255,255,.45)',
              fontSize:12,fontWeight:baseView===it.id?700:500,
              transition:'all .12s',textAlign:'left',
              borderLeft:baseView===it.id&&!col?'2px solid var(--ac)':'2px solid transparent',
              justifyContent:col?'center':'flex-start',
              position:'relative'
            }}
            onMouseEnter=${e=>{if(baseView!==it.id){e.currentTarget.style.background='rgba(37,99,235,0.15)';e.currentTarget.style.color='#93c5fd';}}}
            onMouseLeave=${e=>{if(baseView!==it.id){e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(255,255,255,.45)';}}}> 
            <span style=${{flexShrink:0,width:col?'auto':18,display:'flex',alignItems:'center',justifyContent:'center',opacity:.85}}>${NAV_ICONS[it.id]||null}</span>
            ${!col?html`<span style=${{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',fontSize:12,flex:1}}>${it.label}</span>`:null}
            ${it.id==='notifs'&&unread>0?html`<span style=${{
              position:'absolute',top:6,right:col?6:10,
              minWidth:16,height:16,borderRadius:8,
              background:'var(--rd)',color:'#fff',
              fontSize:9,fontWeight:700,
              display:'flex',alignItems:'center',justifyContent:'center',
              padding:'0 4px'
            }}>${unread>9?'9+':unread}</span>`:null}
            ${it.id==='dm'&&dmUnread.reduce((a,x)=>a+(x.cnt||0),0)>0?html`<span style=${{
              position:'absolute',top:6,right:col?6:10,
              minWidth:16,height:16,borderRadius:8,
              background:'var(--cy)',color:'#fff',
              fontSize:9,fontWeight:700,
              display:'flex',alignItems:'center',justifyContent:'center',
              padding:'0 4px'
            }}>${dmUnread.reduce((a,x)=>a+(x.cnt||0),0)}</span>`:null}
          </button>`)}
      </nav>

      <!-- ── Bottom actions ── -->
      <div style=${{padding:'8px 6px',borderTop:'1px solid rgba(37,99,235,0.15)',display:'flex',flexDirection:'column',gap:2,flexShrink:0}}>
        ${inCall?html`
          <button title="In Huddle" onClick=${()=>onCallAction&&onCallAction('show')}
            style=${{display:'flex',alignItems:'center',gap:col?0:9,width:'100%',padding:col?'9px 0':'8px 10px',borderRadius:9,border:'none',cursor:'pointer',background:'rgba(34,197,94,.1)',color:'#22c55e',justifyContent:col?'center':'flex-start'}}>
            <span style=${{fontSize:15,flexShrink:0,width:col?'auto':18,textAlign:'center'}}>📞</span>
            ${!col?html`<span style=${{fontSize:11,fontWeight:700}}>${fmtTime(callState.elapsed||0)}</span>`:null}
          </button>`:null}
        <button title=${dark?'Light Mode':'Dark Mode'} onClick=${()=>{setDark(d=>{const n=!d;try{localStorage.setItem('pf_dark',n?'1':'0');}catch{}return n;})}}
          style=${{display:'flex',alignItems:'center',gap:col?0:9,width:'100%',padding:col?'9px 0':'8px 10px',borderRadius:9,border:'none',cursor:'pointer',background:'transparent',color:'rgba(255,255,255,.35)',transition:'all .12s',justifyContent:col?'center':'flex-start'}}
          onMouseEnter=${e=>{e.currentTarget.style.background='rgba(37,99,235,0.15)';e.currentTarget.style.color='#93c5fd';}}
          onMouseLeave=${e=>{e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(255,255,255,.35)';}}>
          <span style=${{fontSize:15,flexShrink:0,width:col?'auto':18,display:'flex',alignItems:'center',justifyContent:'center'}}>${themeIcon}</span>
          ${!col?html`<span style=${{fontSize:12}}>${dark?'Light Mode':'Dark Mode'}</span>`:null}
        </button>
        ${(cu&&(cu.role==='Admin'||cu.role==='Manager'||cu.role==='TeamLead'))?html`
          <button title=${col?'Settings':''} onClick=${()=>setView('settings')}
            style=${{display:'flex',alignItems:'center',gap:col?0:9,width:'100%',padding:col?'9px 0':'8px 10px',borderRadius:9,border:'none',cursor:'pointer',
              background:baseView==='settings'?'rgba(37,99,235,0.18)':'transparent',
              color:baseView==='settings'?'var(--ac)':'rgba(255,255,255,.35)',
              transition:'all .12s',justifyContent:col?'center':'flex-start'}}
            onMouseEnter=${e=>{if(baseView!=='settings'){e.currentTarget.style.background='rgba(37,99,235,0.15)';e.currentTarget.style.color='#93c5fd';}}}
            onMouseLeave=${e=>{if(baseView!=='settings'){e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(255,255,255,.35)';}}}> 
            <span style=${{fontSize:15,flexShrink:0,width:col?'auto':18,textAlign:'center'}}>⚙️</span>
            ${!col?html`<span style=${{fontSize:12}}>Settings</span>`:null}
          </button>`:null}
        <button title=${col?'Sign out':''} onClick=${onLogout}
          style=${{display:'flex',alignItems:'center',gap:col?0:9,width:'100%',padding:col?'9px 0':'8px 10px',borderRadius:9,border:'none',cursor:'pointer',background:'transparent',color:'rgba(255,255,255,.3)',transition:'all .12s',justifyContent:col?'center':'flex-start'}}
          onMouseEnter=${e=>{e.currentTarget.style.background='rgba(239,68,68,.1)';e.currentTarget.style.color='#f87171';}}
          onMouseLeave=${e=>{e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(255,255,255,.3)';}}>
          <span style=${{fontSize:15,flexShrink:0,width:col?'auto':18,textAlign:'center'}}>↪</span>
          ${!col?html`<span style=${{fontSize:12}}>Sign out</span>`:null}
        </button>
      </div>
      <!-- Edge collapse/expand tab — floats on the right edge of sidebar -->
      <button title=${col?'Expand sidebar':'Collapse sidebar'} onClick=${()=>setCol(c=>!c)}
        style=${{
          position:'absolute',
          left:col?64:200,
          top:'50%',
          transform:'translateY(-50%)',
          zIndex:200,
          width:14,
          height:40,
          background:'#0f0f0f',
          border:'1px solid rgba(255,255,255,.1)',
          borderLeft:'none',
          borderRadius:'0 6px 6px 0',
          cursor:'pointer',
          display:'flex',
          alignItems:'center',
          justifyContent:'center',
          color:'rgba(255,255,255,.35)',
          transition:'left .2s ease, background .12s, color .12s',
          padding:0,
        }}
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
    <div style=${{flexShrink:0,background:'var(--bg)',borderBottom:'1px solid var(--bd2)',backdropFilter:'blur(8px)'}}>
      <div style=${{padding:'0 18px',height:54,display:'flex',alignItems:'center',gap:10}}>
        <!-- Your Schedule pill -->
        <div style=${{display:'flex',alignItems:'center',gap:8,flexShrink:0,padding:'5px 14px 5px 10px',background:'#1e3a5f',borderRadius:100,cursor:'pointer',border:'1px solid rgba(37,99,235,0.25)',transition:'all .14s'}} onClick=${onViewReminders}>
          <svg width="13" height="13" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="7" fill="#60a5fa"/><circle cx="32" cy="13" r="4" fill="#60a5fa" opacity="0.9"/><circle cx="48" cy="43" r="4" fill="#60a5fa" opacity="0.9"/><circle cx="16" cy="43" r="4" fill="#60a5fa" opacity="0.9"/><line x1="32" y1="17" x2="32" y2="25" stroke="#60a5fa" strokeWidth="2.5" strokeLinecap="round"/><line x1="44" y1="40" x2="38" y2="36" stroke="#aaff00" strokeWidth="2.5" strokeLinecap="round"/><line x1="20" y1="40" x2="26" y2="36" stroke="#aaff00" strokeWidth="2.5" strokeLinecap="round"/></svg>
          <span style=${{fontSize:11,fontWeight:700,color:'#bfdbfe',letterSpacing:'.3px'}}>Your Reminders</span>
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,.35)" strokeWidth="2" strokeLinecap="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          <span style=${{fontSize:11,color:'var(--ac)',fontWeight:700}}>${todayStr}</span>
        </div>
        <!-- team pill removed -->
        <!-- Schedule timeline -->
        <div style=${{flex:1,overflowX:'auto',scrollbarWidth:'none',msOverflowStyle:'none'}}>
          <div style=${{height:40,background:'#0f172a',borderRadius:100,display:'flex',alignItems:'center',padding:'0 14px',gap:0,position:'relative',minWidth:0,overflow:'hidden',border:'1px solid rgba(37,99,235,0.15)'}}>
            ${upcoming.length===0?html`
              <div style=${{display:'flex',alignItems:'center',gap:10,width:'100%',justifyContent:'center'}}>
                <span style=${{fontSize:11,color:'rgba(148,163,184,0.8)',fontStyle:'italic',letterSpacing:'.2px'}}>No reminders today</span>
                <button onClick=${onViewReminders} style=${{fontSize:10,padding:'3px 12px',height:22,borderRadius:100,background:'var(--ac)',color:'var(--ac-tx)',border:'none',cursor:'pointer',fontWeight:700,letterSpacing:'.2px'}}>+ Add</button>
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
          <!-- theme toggle moved to sidebar -->
          <div style=${{position:'relative'}} ref=${npRef}>
            <button style=${{width:34,height:34,borderRadius:'50%',border:'none',background:showNP?'var(--sf2)':'var(--sf)',boxShadow:showNP?'none':'var(--sh)',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',position:'relative',color:'var(--tx2)',transition:'all .15s'}}
              onClick=${()=>setShowNP(v=>!v)}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
              ${unread>0?html`<div style=${{position:'absolute',top:-3,right:-3,width:15,height:15,borderRadius:'50%',background:'#ef4444',border:'2px solid var(--sf)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:8,fontWeight:700,color:'#fff'}}>${unread>9?'9+':unread}</div>`:null}
            </button>
            ${showNP?html`
              <div style=${{position:'absolute',top:38,right:0,width:320,maxHeight:400,background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:16,boxShadow:'var(--sh2)',zIndex:3000,overflow:'hidden',display:'flex',flexDirection:'column'}}>
                <div style=${{padding:'10px 13px 8px',borderBottom:'1px solid var(--bd)',display:'flex',justifyContent:'space-between',alignItems:'center',flexShrink:0}}>
                  <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)'}}>Notifications ${unread>0?html`<span style=${{color:'var(--ac)',fontSize:11}}>(${unread})</span>`:null}</span>
                  <div style=${{display:'flex',gap:5}}>
                    ${unread>0?html`<button class="btn bg" style=${{fontSize:10,padding:'2px 7px',height:20}} onClick=${onMarkAllRead}>✓ Mark all read</button>`:null}
                    <button class="btn brd" style=${{fontSize:10,padding:'2px 7px',height:20}} onClick=${()=>{onClearAll&&onClearAll();setShowNP(false);}}>Clear all</button>
                  </div>
                </div>
                <div style=${{overflowY:'auto',flex:1}}>
                  ${safe(notifs).length===0?html`<div style=${{textAlign:'center',padding:'20px 0',color:'var(--tx3)',fontSize:12}}>🔔 All caught up!</div>`:null}
                  ${safe(notifs).slice(0,25).map(n=>html`
                    <div key=${n.id} onClick=${()=>{onNotifClick&&onNotifClick(n);setShowNP(false);}}
                      style=${{display:'flex',gap:9,padding:'9px 13px',borderBottom:'1px solid var(--bd)',cursor:'pointer',background:n.read?'transparent':'rgba(170,255,0,.04)'}}>
                      <div style=${{width:26,height:26,borderRadius:7,background:(NC[n.type]||'var(--ac)')+'22',display:'flex',alignItems:'center',justifyContent:'center',fontSize:12,flexShrink:0}}>${NI[n.type]||'🔔'}</div>
                      <div style=${{flex:1,minWidth:0}}>
                        <p style=${{fontSize:12,color:'var(--tx)',fontWeight:n.read?400:600,lineHeight:1.35,marginBottom:2,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${n.content}</p>
                        <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${ago(n.ts)}</span>
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
                <div style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace'}}>${cu&&cu.role||''}</div>
              </div>
            </div>
            ${showProfile?html`
              <div style=${{position:'absolute',top:38,right:0,width:290,background:'#fff',border:'none',borderRadius:18,boxShadow:'0 8px 40px rgba(0,0,0,.15)',zIndex:3000,overflow:'hidden'}}>
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
          <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${sz(f.size)} · ${ago(f.ts)}</span>
        </div>
        ${!readOnly?html`<button class="btn brd" style=${{padding:'4px 9px',fontSize:11}} onClick=${()=>del(f.id)}>✕</button>`:null}
      </div>`)}
  </div>`;
}

/* ─── TaskModal ───────────────────────────────────────────────────────────── */
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
  const [cmts,setCmts]=useState(()=>{const r=task&&task.comments;if(!r)return[];if(Array.isArray(r))return r;try{return JSON.parse(r)||[];}catch{return [];}});
  const [nc,setNc]=useState('');
  const [tab,setTab]=useState('details');
  const [saving,setSaving]=useState(false);
  const [err,setErr]=useState('');
  const isEdit=!!(task&&task.id);
  // When team changes, reset assignee if they're not in that team
  const selectedTeam=safe(teams).find(t=>t.id===teamId);
  const teamMemberIds=selectedTeam?JSON.parse(selectedTeam.member_ids||'[]'):null;
  // Assignee options: if a team is selected, filter to team members; otherwise show all
  const assigneeOptions=teamMemberIds
    ? safe(users).filter(u=>teamMemberIds.includes(u.id))
    : safe(users);
  // Role-based + ownership permission checks
  const FULL_EDIT_ROLES=['Admin','Manager','TeamLead'];
  const isAdminManagerTeamLead=cu&&FULL_EDIT_ROLES.includes(cu.role);
  const isAssignee=cu&&task&&task.assignee===cu.id;
  // canEditTask: full form edit (title, desc, priority, assignee, etc.)
  const canEditTask=isAdminManagerTeamLead||(!isEdit); // new tasks always editable
  // canUpdateStage: can move stage/pct only (assignee privilege)
  const canUpdateStage=isAdminManagerTeamLead||isAssignee;
  const canDeleteTask=cu&&FULL_EDIT_ROLES.includes(cu.role);
  // Inline reminder - shown in form before creating task
  const [rmEnabled,setRmEnabled]=useState(false);
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
      // Everyone can add comments; send minimal payload that includes comments
      const payload={comments:updated};
      if(canEditTask){
        Object.assign(payload,{title:title.trim()||task.title,description:desc,project:pid,assignee:ass,priority:pri,stage,due,pct});
      } else {
        // Assignee or read-only — only update comments (backend will accept if assignee)
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
      // Assignee-only: send only stage + pct
      payload={stage,pct};
    } else {
      payload={title:title.trim(),description:desc,project:pid,assignee:ass,priority:pri,stage,due,pct,comments:cmts,team_id:teamId};
    }
    if(task&&task.id)payload.id=task.id;
    const result=await onSave(payload);
    setSaving(false);
    if(result&&result.error){setErr(result.error);return null;}
    // Save reminder atomically if user enabled it on new task
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
            <h2 style=${{fontSize:17,fontWeight:700,color:'var(--tx)'}}>${isEdit?(canEditTask?'Edit Task':canUpdateStage?'Update Stage':'View Task'):'New Task'}</h2>
            ${isEdit?html`<span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${task.id}</span>`:null}
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
          <div style=${{display:'flex',gap:2,background:'var(--sf2)',borderRadius:9,padding:3,marginBottom:14,width:'fit-content'}}>
            ${['details','comments','files'].map(t=>html`
              <button key=${t} class=${'tb'+(tab===t?' act':'')} onClick=${()=>setTab(t)}>
                ${t==='details'?'Details':t==='comments'?'Comments'+(cmts.length?' ('+cmts.length+')':''):'Files'}
              </button>`)}
          </div>`:null}

        ${tab==='details'?html`
          <div style=${{display:'grid',gap:12}}>
            ${!canEditTask&&!canUpdateStage?html`
              <!-- Full read-only view -->
              <div style=${{background:'var(--sf2)',borderRadius:10,padding:'12px 14px',border:'1px solid var(--bd)',display:'grid',gap:8}}>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span style=${{fontSize:11,color:'var(--tx3)'}}>Title</span><span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${title}</span></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span style=${{fontSize:11,color:'var(--tx3)'}}>Stage</span><${SP} s=${stage}/></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span style=${{fontSize:11,color:'var(--tx3)'}}>Priority</span><${PB} p=${pri}/></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span style=${{fontSize:11,color:'var(--tx3)'}}>Due</span><span style=${{fontSize:12,color:'var(--tx2)',fontFamily:'monospace'}}>${fmtD(due)}</span></div>
                <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center'}}><span style=${{fontSize:11,color:'var(--tx3)'}}>Progress</span><span style=${{fontSize:12,color:'var(--ac)',fontWeight:700,fontFamily:'monospace'}}>${pct}%</span></div>
              </div>
            `:canUpdateStage&&!canEditTask?html`
              <!-- Assignee-only view: stage + pct editable, rest read-only -->
              <div style=${{background:'var(--sf2)',borderRadius:10,padding:'12px 14px',border:'1px solid var(--bd)',display:'grid',gap:8,marginBottom:4}}>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span style=${{fontSize:11,color:'var(--tx3)'}}>Title</span><span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${title}</span></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span style=${{fontSize:11,color:'var(--tx3)'}}>Priority</span><${PB} p=${pri}/></div>
                <div style=${{display:'flex',justifyContent:'space-between'}}><span style=${{fontSize:11,color:'var(--tx3)'}}>Due</span><span style=${{fontSize:12,color:'var(--tx2)',fontFamily:'monospace'}}>${fmtD(due)}</span></div>
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
              <!-- Full edit form (Admin/Manager/TeamLead/ProjectOwner) -->
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
                    ${!rmEnabled?html`<span style=${{fontSize:11,color:'var(--tx3)'}}>— get notified before this task is due</span>`:null}
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
                      <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${ago(c.ts)}</span>
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

        ${tab==='files'&&isEdit?html`<${FileAttachments} taskId=${task.id} readOnly=${cu&&cu.role==='Viewer'}/>`:null}
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

  // When team changes, auto-add all team members to project members list
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
              <!-- Team assignment -->
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
                      <div style=${{display:'flex',gap:7,alignItems:'center',marginBottom:4}}><span style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace'}}>${tk.id}</span><${PB} p=${tk.priority}/></div>
                      <div style=${{fontSize:13,fontWeight:500,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${tk.title}</div>
                      ${tk.pct>0?html`<div style=${{marginTop:5}}><${Prog} pct=${tk.pct} color=${si.color}/></div>`:null}
                    </div>
                    <div style=${{display:'flex',flexDirection:'column',alignItems:'flex-end',gap:5,flexShrink:0}}>
                      ${au?html`<${Av} u=${au} size=${24}/>`:html`<div style=${{width:24,height:24,borderRadius:'50%',background:'var(--bd)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:10,color:'var(--tx3)'}}>?</div>`}
                      ${tk.due?html`<span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${fmtD(tk.due)}</span>`:null}
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
  // Auto-open project detail if routed from Timeline Tracker (fires once, then clears immediately)
  useEffect(()=>{
    if(initialProjectId&&safe(projects).length>0){
      const p=safe(projects).find(proj=>proj.id===initialProjectId);
      if(p){
        setDetail(p);
        onClearInitial&&onClearInitial(); // clear immediately so re-visits don't re-open
      }
    }
  },[initialProjectId]); // intentionally only depends on initialProjectId
  const [name,setName]=useState('');const [desc,setDesc]=useState('');
  const [sDate,setSDate]=useState('');const [tDate,setTDate]=useState('');
  const [color,setColor]=useState('#2563eb');const [members,setMembers]=useState([]);const [err,setErr]=useState('');
  const [search,setSearch]=useState('');
  const [sortBy,setSortBy]=useState('newest');
  const [viewMode,setViewMode]=useState('grid');
  const [projTeam,setProjTeam]=useState('');

  useEffect(()=>{if(detail){const fresh=safe(projects).find(p=>p.id===detail.id);if(fresh)setDetail(fresh);}},[projects]);
  // Auto-select active team for new projects
  useEffect(()=>{if(activeTeam)setProjTeam(activeTeam.id);},[activeTeam]);

  const create=async()=>{
    if(!name.trim()){setErr('Project name required.');return;}setErr('');
    try{
      let mems=members.includes(cu.id)?members:[cu.id,...members];
      // If a team is selected, add all team members
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
      // Reload with small delay to ensure DB write is committed before read
      await new Promise(r=>setTimeout(r,300));
      await reload();
      // If reload somehow missed it (race), do a second reload after 1s
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

      <!-- ── TOOLBAR ── -->
      <div style=${{flexShrink:0,padding:'10px 16px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:8,flexWrap:'wrap',background:'var(--bg)'}}>
        <!-- Search -->
        <div style=${{position:'relative',flex:'1',minWidth:140,maxWidth:280}}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
            style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" style=${{paddingLeft:26,height:28,fontSize:12}} placeholder="Search projects..."
            value=${search} onInput=${e=>setSearch(e.target.value)}/>
        </div>
        <span style=${{fontSize:11,color:'var(--tx3)',whiteSpace:'nowrap',flexShrink:0}}>${filteredProjects.length} of ${safe(projects).length}</span>

        <!-- Sort -->
        <div style=${{display:'flex',background:'var(--sf2)',borderRadius:7,padding:2,gap:1,flexShrink:0}}>
          ${[['newest','🕐 Newest'],['oldest','🕐 Oldest'],['name','🔤 Name'],['progress','📊 Progress'],['tasks','📋 Tasks']].map(([k,lbl])=>html`
            <button key=${k} class=${'tb'+(sortBy===k?' act':'')} style=${{fontSize:10,padding:'2px 7px'}}
              onClick=${()=>setSortBy(k)}>${lbl}</button>`)}
        </div>

        <!-- View toggle -->
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

      <!-- ── PROJECT LIST (scrollable) ── -->
      <div style=${{flex:1,minHeight:0,overflowY:'auto',padding:'12px 16px'}}>

        ${filteredProjects.length===0?html`
          <div style=${{textAlign:'center',padding:'60px 0',color:'var(--tx3)'}}>
            <div style=${{fontSize:40,marginBottom:12}}>🔍</div>
            <div style=${{fontSize:14,fontWeight:600,color:'var(--tx2)',marginBottom:6}}>${search?`No projects match "${search}"`:'No projects yet'}</div>
            ${search?html`<button class="btn bg" style=${{fontSize:12}} onClick=${()=>setSearch('')}>Clear search</button>`:null}
          </div>`:null}

        <!-- CARD GRID -->
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
                    <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',flex:1,marginRight:6,lineHeight:1.3}}>${p.name}</h3>
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
                      ${(()=>{const pt=safe(teams).find(t=>t.id===p.team_id);return pt?html`<span style=${{fontSize:9,color:'var(--ac)',background:'rgba(170,255,0,.1)',border:'1px solid rgba(170,255,0,.25)',padding:'1px 6px',borderRadius:4,fontWeight:600}}>${pt.name}</span>`:
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

        <!-- COMPACT LIST — best for 50-200 projects -->
        ${viewMode==='compact'&&filteredProjects.length>0?html`
          <div style=${{display:'flex',flexDirection:'column',gap:3}}>
            <!-- Header row -->
            <div style=${{display:'grid',gridTemplateColumns:'1fr 90px 50px 50px 50px 90px',gap:8,padding:'4px 12px',
              fontSize:9,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5}}>
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
                  style=${{display:'grid',gridTemplateColumns:'1fr 90px 50px 50px 50px 90px',gap:8,
                    alignItems:'center',padding:'8px 12px',
                    background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:8,
                    cursor:'pointer',transition:'background .1s',borderLeft:'3px solid '+p.color}}
                  onClick=${()=>setDetail(p)}
                  onMouseEnter=${e=>e.currentTarget.style.background='var(--sf2)'}
                  onMouseLeave=${e=>e.currentTarget.style.background='var(--sf)'}>
                  <!-- Name + desc -->
                  <div style=${{minWidth:0}}>
                    <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${p.name}</div>
                    <div style=${{fontSize:10,color:'var(--tx3)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginTop:1}}>${p.description||'—'}</div>
                  </div>
                  <!-- Progress bar -->
                  <div style=${{display:'flex',alignItems:'center',gap:5}}>
                    <div style=${{flex:1,height:4,background:'var(--bd)',borderRadius:100,overflow:'hidden'}}>
                      <div style=${{height:'100%',width:pc+'%',background:p.color,borderRadius:100}}></div>
                    </div>
                    <span style=${{fontSize:9,fontFamily:'monospace',color:'var(--tx3)',flexShrink:0,minWidth:24}}>${pc}%</span>
                  </div>
                  <!-- Stats -->
                  <div style=${{textAlign:'center',fontSize:13,fontWeight:700,color:'var(--tx)'}}>${pt.length}</div>
                  <div style=${{textAlign:'center',fontSize:13,fontWeight:700,color:'var(--gn)'}}>${done}</div>
                  <div style=${{textAlign:'center',fontSize:13,fontWeight:700,color:'var(--am)'}}>${pt.length-done}</div>
                  <!-- Date -->
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
                    style=${{width:26,height:26,borderRadius:6,background:c,
                      border:'3px solid '+(color===c?'#fff':'transparent'),
                      cursor:'pointer',transform:color===c?'scale(1.15)':'none'}}></button>`)}
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
// SDLC stage → typical days from today & auto completion %
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
  const [search,setSearch]=useState('');
  const [showFilters,setShowFilters]=useState(!!(initialStage||initialPriority));
  const [showResolved,setShowResolved]=useState(false);
  const [sortCol,setSortCol]=useState(null);
  const [sortDir,setSortDir]=useState('asc');
  const [editT,setEditT]=useState(null);const [newT,setNewT]=useState(false);
  const [csvImporting,setCsvImporting]=useState(false);
  const [csvResult,setCsvResult]=useState(null);
  const csvRef=useRef(null);

  useEffect(()=>{
    if(initialStage){setStageF(initialStage);setShowFilters(true);}
    if(initialStage==='completed'){setShowResolved(true);}
    if(initialPriority){setPriF(initialPriority);setShowFilters(true);}
    if(initialAssignee==='me'&&cu){setAssF(cu.id);setShowFilters(true);}
  },[initialStage,initialPriority,initialAssignee,cu]);

  const RESOLVED_STAGES=new Set(['completed']);

  const activeFilters=[pid,teamF,priF,stageF,assF,dueF].filter(v=>v!=='all').length;
  const clearAll=()=>{setPid('all');setTeamF('all');setPriF('all');setStageF('all');setAssF('all');setDueF('all');setSearch('');setShowResolved(false);};

  // Build team member id set for filter
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
      // Team filter: match by team_id OR assignee in team
      if(teamF!=='all'){
        const byTeamId=t.team_id&&t.team_id===teamF;
        const byAssignee=teamFilterMemberIds&&t.assignee&&teamFilterMemberIds.has(t.assignee);
        if(!byTeamId&&!byAssignee)return false;
      }
      if(search&&!t.title.toLowerCase().includes(search.toLowerCase()))return false;
      if(dueF!=='all'&&t.due){
        const d=new Date(t.due);d.setHours(0,0,0,0);
        if(dueF==='overdue'&&d>=today)return false;
        if(dueF==='today'&&d.getTime()!==today.getTime())return false;
        if(dueF==='week'&&(d<today||d>endOfWeek))return false;
        if(dueF==='month'&&(d<today||d>endOfMonth))return false;
      } else if(dueF!=='all'&&!t.due) return false;
      return true;
    });
  },[tasks,pid,teamF,teamFilterMemberIds,priF,stageF,assF,dueF,search,showResolved]);

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
            <input class="inp" style=${{paddingLeft:30}} placeholder="Search tasks..." value=${search} onInput=${e=>setSearch(e.target.value)}/>
          </div>
          <button class=${'btn bg'+(showFilters?' act':'')} style=${{position:'relative',padding:'8px 13px',fontSize:12,borderColor:activeFilters>0?'var(--ac)':'',color:activeFilters>0?'var(--ac2)':''}}
            onClick=${()=>setShowFilters(!showFilters)}>
            ⚙ Filters${activeFilters>0?html` <span style=${{background:'var(--ac)',color:'#fff',borderRadius:8,fontSize:9,padding:'1px 5px',marginLeft:3,fontFamily:'monospace'}}>${activeFilters}</span>`:''}
          </button>
          ${assF!=='all'&&assF===cu.id?html`
            <div style=${{display:'flex',alignItems:'center',gap:6,padding:'5px 10px 5px 8px',background:'var(--ac3)',border:'1px solid var(--ac)',borderRadius:20,flexShrink:0}}>
              <div style=${{width:6,height:6,borderRadius:'50%',background:'var(--ac)',flexShrink:0}}></div>
              <span style=${{fontSize:11,fontWeight:700,color:'var(--ac)'}}>My Tasks</span>
              <button onClick=${()=>setAssF('all')}
                style=${{background:'none',border:'none',cursor:'pointer',color:'var(--ac)',fontSize:14,lineHeight:1,padding:'0 2px'}}>×</button>
            </div>`:null}
          ${activeFilters>0?html`<button class="btn bam" style=${{padding:'7px 11px',fontSize:11}} onClick=${clearAll}>✕ Clear</button>`:null}
          <!-- Resolved toggle removed -->
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
          <button class="btn bp" style=${{flex:'0 0 auto',fontSize:12,padding:'7px 13px'}} onClick=${()=>setNewT(true)}>+ New Task</button>
        </div>
        ${csvResult?html`<div style=${{marginTop:8,padding:'8px 12px',borderRadius:8,fontSize:12,background:csvResult.error?'rgba(255,68,68,.08)':'rgba(62,207,110,.08)',border:'1px solid '+(csvResult.error?'rgba(255,68,68,.2)':'rgba(62,207,110,.2)'),color:csvResult.error?'var(--rd)':'var(--gn)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
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
                      <div style=${{display:'flex',justifyContent:'space-between',marginBottom:4}}>
                        <span style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace'}}>${tk.id}</span>
                        <${PB} p=${tk.priority}/>
                      </div>
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

      ${mode==='list'?html`
        <div style=${{flex:1,overflowY:'auto',padding:'13px 18px'}}>
          <div class="card" style=${{padding:0,overflow:'hidden'}}>
            <table style=${{width:'100%',borderCollapse:'collapse'}}>
              <thead>
                <tr style=${{borderBottom:'2px solid var(--bd)',background:'var(--sf2)'}}>
                  ${[
                    {k:'id',      lbl:'ID',       s:null},
                    {k:'title',   lbl:'Title',    s:null},
                    {k:'project', lbl:'Project',  s:null},
                    {k:'assignee',lbl:'Assignee', s:'assignee'},
                    {k:'priority',lbl:'Priority', s:'priority'},
                    {k:'stage',   lbl:'Stage',    s:'stage'},
                    {k:'due',     lbl:'Due',      s:'due'},
                    {k:'pct',     lbl:'%',        s:'pct'},
                  ].map(h=>{
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
                      <td style=${{padding:'9px 13px'}}><span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${tk.id}</span></td>
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
  const active=t.filter(x=>x.stage!=='completed'&&x.stage!=='backlog').length;
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
    {name:'Critical',value:activeTasks.filter(x=>x.priority==='critical').length,color:'var(--rd)',priKey:'critical'},
    {name:'High',value:activeTasks.filter(x=>x.priority==='high').length,color:'var(--rd2)',priKey:'high'},
    {name:'Medium',value:activeTasks.filter(x=>x.priority==='medium').length,color:'var(--pu)',priKey:'medium'},
    {name:'Low',value:activeTasks.filter(x=>x.priority==='low').length,color:'var(--cy)',priKey:'low'}
  ];
  const stats=[
    {label:'Total Projects',val:p.length,color:'var(--ac)',bg:'var(--ac3)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`,nav:'projects'},
    {label:'Active Tasks',val:active,color:'var(--cy)',bg:'rgba(34,211,238,.08)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,nav:'tasks:stage:development'},
    {label:'Completed',val:done,color:'var(--gn)',bg:'rgba(62,207,110,.08)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,nav:'tasks:stage:completed'},
    {label:'Blocked',val:blocked,color:'var(--rd)',bg:'rgba(255,68,68,.08)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>`,nav:'tasks:stage:blocked'},
    {label:'My Tasks',val:myT.filter(x=>x.stage!=='completed').length,color:'var(--am)',bg:'rgba(245,158,11,.08)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,nav:'tasks:assignee:me'},
    {label:'Team Members',val:u.length,color:'var(--pu)',bg:'rgba(167,139,250,.08)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,nav:isAdminManager?'team':'tasks:assignee:me'},
    {label:'Open Tickets',val:openTickets,color:'var(--cy)',bg:'rgba(34,211,238,.08)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 9a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v1.5a1.5 1.5 0 0 0 0 3V15a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-1.5a1.5 1.5 0 0 0 0-3V9z"/><line x1="9" y1="7" x2="9" y2="17" strokeDasharray="2 2"/></svg>`,nav:'tickets:status:open'},
    {label:'In Progress',val:inProgressTickets,color:'var(--am)',bg:'rgba(245,158,11,.08)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,nav:isAdminManager?'tickets':'tasks:assignee:me'},
    {label:'My Tickets',val:myTickets,color:'var(--or)',bg:'rgba(251,146,60,.08)',icon:html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,nav:'tickets:assignee:me'},
  ];
  return html`
    <div class="fi" style=${{height:'100%',overflowY:'auto',padding:'12px 20px',display:'flex',flexDirection:'column',gap:12}}>
      <!-- Compact unified header: greeting + team info + dropdown -->
      <div style=${{padding:'10px 14px',background:'var(--sf)',borderRadius:12,border:'1px solid var(--bd2)',display:'flex',alignItems:'center',gap:10}}>
        <${Av} u=${cu} size=${32}/>
        <div style=${{flex:1,minWidth:0}}>
          <div style=${{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
            <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)'}}>Good day, ${(cu&&cu.name||'there').split(' ')[0]}! 👋</span>
            ${activeTeam?html`
              <span style=${{display:'inline-flex',alignItems:'center',gap:5,padding:'2px 8px',background:'var(--ac3)',border:'1px solid var(--ac)',borderRadius:20,fontSize:10,fontWeight:600,color:'var(--ac)',flexShrink:0}}>
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
              style=${{display:'flex',alignItems:'center',gap:7,padding:'7px 12px 7px 10px',borderRadius:10,
                border:'1px solid '+(teamDropOpen?'var(--ac)':'var(--bd)'),
                background:activeTeam?'var(--ac3)':'var(--sf2)',
                color:activeTeam?'var(--ac)':'var(--tx2)',
                cursor:'pointer',fontSize:12,fontWeight:600,transition:'all .15s',whiteSpace:'nowrap'}}>
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
                    style=${{width:'100%',padding:'7px 10px',borderRadius:7,border:'none',
                      background:!activeTeam?'var(--ac3)':'transparent',
                      color:!activeTeam?'var(--ac)':'var(--tx2)',
                      fontSize:12,fontWeight:!activeTeam?700:400,
                      cursor:'pointer',textAlign:'left',display:'flex',alignItems:'center',gap:8,transition:'all .1s'}}
                    onMouseEnter=${e=>{if(activeTeam)e.currentTarget.style.background='var(--sf2)';}}
                    onMouseLeave=${e=>{if(activeTeam)e.currentTarget.style.background='transparent';}}>
                    🌐 All Teams
                  </button>
                  ${filteredTeams.map(team=>html`
                    <button key=${team.id} onClick=${()=>{setTeamCtx&&setTeamCtx(team.id);setTeamDropOpen(false);setTeamSearch('');}}
                      style=${{width:'100%',padding:'7px 10px',borderRadius:7,border:'none',
                        background:activeTeam&&activeTeam.id===team.id?'var(--ac3)':'transparent',
                        color:activeTeam&&activeTeam.id===team.id?'var(--ac)':'var(--tx2)',
                        fontSize:12,fontWeight:activeTeam&&activeTeam.id===team.id?700:400,
                        cursor:'pointer',textAlign:'left',display:'flex',alignItems:'center',gap:8,transition:'all .1s'}}
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
      <!-- Stat cards -->
      <div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(150px,1fr))',gap:8}}>
        ${stats.map((s,i)=>html`
          <div key=${i} onClick=${()=>onNav(s.nav)}
            style=${{background:'var(--sf)',borderRadius:14,padding:'12px 14px',position:'relative',overflow:'hidden',cursor:'pointer',transition:'all .16s',border:'1px solid var(--bd2)'}}
            onMouseEnter=${e=>{e.currentTarget.style.borderColor=s.color;e.currentTarget.style.transform='translateY(-2px)';}}
            onMouseLeave=${e=>{e.currentTarget.style.borderColor='';e.currentTarget.style.transform='';}}>
            <div style=${{position:'absolute',top:0,left:0,right:0,height:2,background:s.color,borderRadius:'16px 16px 0 0'}}></div>
            <div style=${{width:26,height:26,borderRadius:7,background:s.bg,display:'flex',alignItems:'center',justifyContent:'center',color:s.color,marginBottom:8}}>${s.icon}</div>
            <div style=${{fontSize:24,fontWeight:700,color:'var(--tx)',lineHeight:1,fontFamily:"'Space Grotesk',sans-serif",letterSpacing:-1}}>${s.val}</div>
            <div style=${{fontSize:11,color:'var(--tx2)',marginTop:5,fontWeight:500}}>${s.label}</div>
          </div>`)}
      </div>
      <!-- Priority split + Project Progress + My Tasks -->
      <div style=${{display:'grid',gridTemplateColumns:'240px 1fr 1fr',gap:14}}>
        <div class="card">
          <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',marginBottom:11}}>Priority Split</h3>
          <${RC.ResponsiveContainer} width="100%" height=${120}>
            <${RC.PieChart}>
              <${RC.Pie} data=${priChart} cx="50%" cy="50%" innerRadius=${34} outerRadius=${52} dataKey="value" paddingAngle=${4} cursor="pointer"
                onClick=${(data)=>{if(data&&data.priKey)onNav('tasks:priority:'+data.priKey);}}>
                ${priChart.map((e,i)=>html`<${RC.Cell} key=${i} fill=${e.color}/>`)}<//>
              <${RC.Tooltip} contentStyle=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,color:'var(--tx)',fontSize:12}}/>
            <//>
          <//>
          ${priChart.map((item,i)=>html`
            <div key=${i} style=${{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'5px 0',borderBottom:i<3?'1px solid var(--bd)':'none',cursor:'pointer'}}
              onClick=${()=>onNav('tasks:priority:'+item.priKey)}>
              <div style=${{display:'flex',alignItems:'center',gap:7}}>
                <div style=${{width:7,height:7,borderRadius:2,background:item.color}}></div>
                <span style=${{fontSize:12,color:'var(--tx2)'}}>${item.name}</span>
              </div>
              <span style=${{fontSize:12,color:'var(--tx)',fontFamily:'monospace',fontWeight:700}}>${item.value}</span>
            </div>`)}
          <p style=${{fontSize:10,color:'var(--tx3)',marginTop:6,textAlign:'center'}}>Click to filter by priority</p>
        </div>
        <div class="card" style=${{display:'flex',flexDirection:'column'}}>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
            <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',margin:0}}>Project Progress</h3>
            <button class="btn bg" style=${{fontSize:10,padding:'2px 9px',height:22}} onClick=${()=>onNav('projects')}>View All</button>
          </div>
          <div style=${{flex:1,overflowY:'auto',maxHeight:220}}>
          ${p.map(proj=>{
            const pt=t.filter(x=>x.project===proj.id);
            const pc=pt.length?Math.round(pt.reduce((a,x)=>a+(x.pct||0),0)/pt.length):(proj.progress||0);
            return html`<div key=${proj.id} style=${{marginBottom:11}}>
              <div style=${{display:'flex',justifyContent:'space-between',marginBottom:4}}>
                <div style=${{display:'flex',alignItems:'center',gap:6}}>
                  <div style=${{width:7,height:7,borderRadius:2,background:proj.color}}></div>
                  <span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${proj.name}</span>
                </div>
                <span style=${{fontSize:11,color:'var(--tx2)',fontFamily:'monospace'}}>${pc}%</span>
              </div>
              <${Prog} pct=${pc} color=${proj.color}/>
            </div>`;
          })}
          </div>
        </div>
        <div class="card">
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
            <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',margin:0}}>My Active Tasks</h3>
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

  // Config — defined once, used for tabs and filter logic
  const HC={
    'on-track':{label:'On Track',color:'var(--gn)',bg:'rgba(74,222,128,.12)'},
    'warning':{label:'At Risk',color:'var(--am)',bg:'rgba(251,191,36,.12)'},
    'at-risk':{label:'Needs Attention',color:'var(--rd)',bg:'rgba(248,113,113,.12)'},
    'overdue':{label:'Overdue',color:'var(--rd)',bg:'rgba(248,113,113,.2)'},
    'no-dates':{label:'No Dates',color:'var(--tx3)',bg:'rgba(255,255,255,.04)'},
  };
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
    return {...proj,start,end,totalDays,daysSpent,daysLeft,timeProgress,taskProgress,isOverdue,health,gap,
      taskCount:pt.length,doneTasks:pt.filter(x=>x.stage==='completed').length};
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

      <!-- ── FIXED HEADER ── -->
      <div style=${{flexShrink:0,padding:'12px 20px 10px',borderBottom:'1px solid var(--bd)',background:'var(--bg)'}}>

        <!-- Title + today -->
        <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10}}>
          <div>
            <h2 style=${{fontSize:15,fontWeight:800,color:'var(--tx)',display:'flex',alignItems:'center',gap:7,margin:0}}>📅 Project Timeline Tracker</h2>
            <p style=${{fontSize:11,color:'var(--tx3)',marginTop:2}}>Days spent vs. remaining — based on today</p>
          </div>
          <span style=${{fontSize:11,color:'var(--tx3)',background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:7,padding:'4px 10px',fontFamily:'monospace',flexShrink:0}}>
            ${now.toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric',year:'numeric'})}
          </span>
        </div>

        <!-- Health tab cards -->
        <div style=${{display:'flex',gap:7,marginBottom:10,flexWrap:'wrap'}}>
          ${[['all','All','var(--ac)','rgba(170,255,0,.08)',counts.total],
             ['on-track','On Track','var(--gn)','rgba(74,222,128,.1)',counts['on-track']],
             ['warning','At Risk','var(--am)','rgba(251,191,36,.1)',counts['warning']],
             ['at-risk','Needs Attn','var(--rd)','rgba(248,113,113,.1)',counts['at-risk']],
             ['overdue','Overdue','var(--rd)','rgba(248,113,113,.15)',counts['overdue']],
             ['no-dates','No Dates','var(--tx3)','rgba(255,255,255,.04)',counts['no-dates']],
          ].map(([k,lbl,color,bg,cnt])=>html`
            <div key=${k} onClick=${()=>setFilterHealth(k)}
              style=${{background:filterHealth===k?bg:'var(--sf)',border:'2px solid '+(filterHealth===k?color:'var(--bd)'),
                borderRadius:9,padding:'7px 14px',cursor:'pointer',transition:'all .15s',
                display:'flex',alignItems:'center',gap:8}}
              onMouseEnter=${e=>{if(filterHealth!==k)e.currentTarget.style.borderColor=color+'66';}}
              onMouseLeave=${e=>{if(filterHealth!==k)e.currentTarget.style.borderColor='var(--bd)';}}>
              <span style=${{fontSize:17,fontWeight:800,color,fontFamily:'monospace',lineHeight:1}}>${cnt}</span>
              <span style=${{fontSize:9,color:filterHealth===k?color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:.5}}>${lbl}</span>
            </div>`)}
        </div>

        <!-- Search + sort — single compact row -->
        <div style=${{display:'flex',gap:8,alignItems:'center'}}>
          <!-- Search -->
          <div style=${{position:'relative',flex:1,maxWidth:260}}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
              style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input class="inp" placeholder="Search projects..." value=${search}
              style=${{height:26,fontSize:11,paddingLeft:26}}
              onInput=${e=>setSearch(e.target.value)}/>
          </div>
          <!-- Sort -->
          <span style=${{fontSize:10,color:'var(--tx3)',fontWeight:700,textTransform:'uppercase',letterSpacing:.5}}>Sort:</span>
          <div style=${{display:'flex',background:'var(--sf2)',borderRadius:6,padding:2,gap:1}}>
            ${[['health','🚦 Health'],['name','🔤 Name'],['progress','✅ Tasks'],['days_left','⏳ Days Left'],['spent','📆 Days Spent']].map(([k,lbl])=>html`
              <button key=${k} class=${'tb'+(sortBy===k?' act':'')} style=${{fontSize:10,padding:'2px 8px'}} onClick=${()=>setSortBy(k)}>${lbl}</button>`)}
          </div>
          <!-- Clear -->
          ${(filterHealth!=='all'||search)?html`
            <button class="btn bg" style=${{fontSize:10,padding:'3px 9px'}}
              onClick=${()=>{setFilterHealth('all');setSearch('');}}>✕ Clear</button>`:null}
          <span style=${{marginLeft:'auto',fontSize:11,color:'var(--tx3)',whiteSpace:'nowrap'}}>${filtered.length}/${timelines.length} projects</span>
        </div>
      </div>

      <!-- ── SCROLLABLE LIST ── -->
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
            <div key=${proj.id} style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,
              padding:'13px 17px',borderLeft:'4px solid '+proj.color,transition:'all .15s',cursor:'pointer'}}
              onClick=${()=>onNav&&onNav('projects',proj.id)}
              onMouseEnter=${e=>{e.currentTarget.style.boxShadow='0 4px 20px rgba(0,0,0,.3)';e.currentTarget.style.borderColor=proj.color;}}
              onMouseLeave=${e=>{e.currentTarget.style.boxShadow='';e.currentTarget.style.borderColor='var(--bd)';}}>
              <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:proj.totalDays!==null?10:4}}>
                <span style=${{fontSize:13,fontWeight:700,color:'var(--ac)',flex:1,textDecoration:'underline',textDecorationStyle:'dotted',textUnderlineOffset:3}}>${proj.name}</span>
                <span style=${{fontSize:10,fontWeight:700,padding:'3px 10px',borderRadius:100,background:hc.bg,color:hc.color}}>${hc.label}</span>
                <span style=${{fontSize:10,color:'var(--tx3)'}}>📋 ${proj.doneTasks}/${proj.taskCount}</span>
              </div>
              ${proj.totalDays!==null?html`
                <div style=${{display:'flex',flexDirection:'column',gap:5,marginBottom:10}}>
                  <div style=${{display:'flex',alignItems:'center',gap:10}}>
                    <span style=${{fontSize:10,color:'var(--tx3)',width:90,flexShrink:0}}>⏱ Time elapsed</span>
                    <div style=${{flex:1,height:7,background:'var(--sf2)',borderRadius:100,overflow:'hidden',border:'1px solid var(--bd)'}}>
                      <div style=${{height:'100%',width:proj.timeProgress+'%',borderRadius:100,
                        background:proj.isOverdue?'var(--rd)':proj.timeProgress>70?'var(--am)':'var(--cy)'}}></div>
                    </div>
                    <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',width:34,textAlign:'right',fontWeight:700}}>${proj.timeProgress}%</span>
                  </div>
                  <div style=${{display:'flex',alignItems:'center',gap:10}}>
                    <span style=${{fontSize:10,color:'var(--tx3)',width:90,flexShrink:0}}>✅ Tasks done</span>
                    <div style=${{flex:1,height:7,background:'var(--sf2)',borderRadius:100,overflow:'hidden',border:'1px solid var(--bd)'}}>
                      <div style=${{height:'100%',width:proj.taskProgress+'%',borderRadius:100,background:proj.color}}></div>
                    </div>
                    <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',width:34,textAlign:'right',fontWeight:700}}>${proj.taskProgress}%</span>
                  </div>
                </div>
                <div style=${{display:'flex',gap:7,flexWrap:'wrap'}}>
                  ${[
                    {lbl:'Start',val:fmtD(proj.start),c:'var(--tx2)'},
                    {lbl:'End',val:fmtD(proj.end),c:proj.isOverdue?'var(--rd)':'var(--tx2)'},
                    {lbl:'Total',val:proj.totalDays+' days',c:'var(--tx2)'},
                    {lbl:'Spent',val:proj.daysSpent+' days',c:'var(--ac)'},
                    {lbl:proj.isOverdue?'Overdue by':'Remaining',val:Math.abs(proj.daysLeft)+' days',c:proj.isOverdue?'var(--rd)':'var(--gn)'},
                    proj.gap!==null?{lbl:'Gap',val:(proj.gap>0?'+':'')+proj.gap+'%',c:proj.gap>15?'var(--rd)':proj.gap>0?'var(--am)':'var(--gn)'}:null,
                  ].filter(Boolean).map((ch,i)=>html`
                    <div key=${i} style=${{padding:'3px 8px',background:'var(--sf2)',borderRadius:6,border:'1px solid var(--bd)'}}>
                      <span style=${{fontSize:9,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.4}}>${ch.lbl} </span>
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
    return {...dev,total,completed:completed.length,inProg:inProg.length,blocked:blocked.length,
      overdue:overdue.length,completionRate,avgPct,score,scoreColor,last7,projCount:projSet.size};
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

      <!-- ── TOP BAR (very compact) ── -->
      <div style=${{flexShrink:0,padding:'10px 18px',borderBottom:'1px solid var(--bd)',background:'var(--bg)',display:'flex',alignItems:'center',gap:10,flexWrap:'wrap'}}>

        <!-- Title -->
        <div style=${{marginRight:4}}>
          <span style=${{fontSize:14,fontWeight:800,color:'var(--tx)'}}>👩‍💻 Dev Productivity</span>
          <span style=${{fontSize:11,color:'var(--tx3)',marginLeft:8}}>${u.length} developers · ${t.length} tasks</span>
        </div>

        <!-- Search -->
        <div style=${{position:'relative',flex:'1',maxWidth:200}}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
            style=${{position:'absolute',left:8,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}>
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input class="inp" placeholder="Search developer..." value=${search}
            style=${{height:26,fontSize:11,paddingLeft:26}}
            onInput=${e=>setSearch(e.target.value)}/>
        </div>

        <!-- Role filter -->
        <select class="inp" style=${{height:26,fontSize:11,padding:'0 8px',maxWidth:120}} value=${filterRole}
          onChange=${e=>{setFilterRole(e.target.value);setSelectedDev(null);}}>
          <option value="all">All Roles</option>
          ${roles.map(r=>html`<option key=${r} value=${r}>${r}</option>`)}
        </select>

        <!-- Project filter -->
        <select class="inp" style=${{height:26,fontSize:11,padding:'0 8px',maxWidth:140}} value=${filterProject}
          onChange=${e=>{setFilterProject(e.target.value);setSelectedDev(null);}}>
          <option value="all">All Projects</option>
          ${p.map(pr=>html`<option key=${pr.id} value=${pr.id}>${pr.name}</option>`)}
        </select>

        <!-- Sort -->
        <select class="inp" style=${{height:26,fontSize:11,padding:'0 8px',maxWidth:130}} value=${sortBy}
          onChange=${e=>setSortBy(e.target.value)}>
          <option value="score">Sort: Score</option>
          <option value="name">Sort: Name</option>
          <option value="tasks">Sort: Tasks</option>
          <option value="completed">Sort: Done</option>
          <option value="overdue">Sort: Overdue</option>
        </select>

        <!-- Tab switcher -->
        <div style=${{display:'flex',background:'var(--sf2)',borderRadius:7,padding:2,gap:1,marginLeft:'auto'}}>
          ${[['table','📋 Table'],['chart','📊 Chart']].map(([k,lbl])=>html`
            <button key=${k} class=${'tb'+(tab===k&&!selDev?' act':'')} style=${{fontSize:10,padding:'3px 10px'}}
              onClick=${()=>{closeDetail();setTab(k);}}>${lbl}</button>`)}
        </div>

        <span style=${{fontSize:11,color:'var(--tx3)',whiteSpace:'nowrap'}}>${filtered.length}/${u.length}</span>
      </div>

      <!-- ── SUMMARY STRIP ── -->
      ${!selDev?html`
        <div style=${{flexShrink:0,display:'flex',gap:0,borderBottom:'1px solid var(--bd)',background:'var(--sf2)'}}>
          ${[
            {lbl:'Total Tasks',val:t.length,c:'var(--tx)'},
            {lbl:'Completed',val:t.filter(x=>x.stage==='completed').length,c:'var(--gn)'},
            {lbl:'In Progress',val:t.filter(x=>x.stage==='in-progress'||x.stage==='development').length,c:'var(--cy)'},
            {lbl:'Blocked',val:t.filter(x=>x.stage==='blocked').length,c:'var(--rd)'},
            {lbl:'Overdue',val:t.filter(x=>x.due&&new Date(x.due)<now&&x.stage!=='completed').length,c:'var(--am)'},
          ].map((s,i)=>html`
            <div key=${i} style=${{flex:1,textAlign:'center',padding:'8px 4px',borderRight:i<4?'1px solid var(--bd)':'none'}}>
              <div style=${{fontSize:16,fontWeight:800,color:s.c,fontFamily:'monospace',lineHeight:1}}>${s.val}</div>
              <div style=${{fontSize:9,color:'var(--tx3)',fontWeight:600,marginTop:2,textTransform:'uppercase',letterSpacing:.4}}>${s.lbl}</div>
            </div>`)}
        </div>`:null}

      <!-- ── SCROLLABLE CONTENT ── -->
      <div style=${{flex:1,minHeight:0,overflowY:'auto'}}>

        <!-- TABLE TAB -->
        ${tab==='table'&&!selDev?html`
          <table style=${{width:'100%',borderCollapse:'collapse',fontSize:12}}>
            <thead style=${{position:'sticky',top:0,zIndex:10}}>
              <tr style=${{background:'var(--sf2)',borderBottom:'2px solid var(--bd)'}}>
                ${[['#','36px'],['Developer','180px'],['Role','90px'],['Score','60px'],
                   ['Tasks','60px'],['Done','60px'],['Active','60px'],['Blocked','70px'],
                   ['Overdue','70px'],['Avg %','100px'],['Last 7d','70px'],['Projects','70px'],['','48px']
                ].map(([h,w])=>html`
                  <th key=${h} style=${{padding:'8px 10px',textAlign:'left',fontSize:9,fontWeight:700,
                    color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5,whiteSpace:'nowrap',
                    minWidth:w,width:w}}>${h}</th>`)}
              </tr>
            </thead>
            <tbody>
              ${filtered.map((dev,i)=>html`
                <tr key=${dev.id} style=${{borderBottom:'1px solid var(--bd)',cursor:'pointer',transition:'background .1s'}}
                  onMouseEnter=${e=>e.currentTarget.style.background='rgba(255,255,255,.04)'}
                  onMouseLeave=${e=>e.currentTarget.style.background=''}
                  onClick=${()=>openDetail(dev.id)}>
                  <!-- Rank -->
                  <td style=${{padding:'9px 10px',textAlign:'center',fontSize:12}}>
                    ${i===0?'🥇':i===1?'🥈':i===2?'🥉':html`<span style=${{color:'var(--tx3)',fontFamily:'monospace',fontSize:10}}>${i+1}</span>`}
                  </td>
                  <!-- Developer -->
                  <td style=${{padding:'9px 10px'}}>
                    <div style=${{display:'flex',alignItems:'center',gap:8}}>
                      <${Av} u=${dev} size=${28}/>
                      <div>
                        <div style=${{fontWeight:600,color:'var(--tx)',fontSize:12,lineHeight:1.2,whiteSpace:'nowrap'}}>${dev.name}</div>
                        ${dev.id===cu.id?html`<div style=${{fontSize:9,color:'var(--ac)',fontWeight:700}}>YOU</div>`:null}
                      </div>
                    </div>
                  </td>
                  <td style=${{padding:'9px 10px',color:'var(--tx2)',fontSize:11,whiteSpace:'nowrap'}}>${dev.role||'—'}</td>
                  <!-- Score ring -->
                  <td style=${{padding:'9px 10px'}}>
                    <div style=${{width:32,height:32,borderRadius:'50%',border:'2.5px solid '+dev.scoreColor,
                      display:'flex',alignItems:'center',justifyContent:'center',
                      background:'rgba(255,255,255,.02)',fontSize:10,fontWeight:800,
                      color:dev.scoreColor,fontFamily:'monospace'}}>${dev.score}</div>
                  </td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',fontWeight:600,color:'var(--tx)',textAlign:'center'}}>${dev.total}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',fontWeight:700,color:'var(--gn)',textAlign:'center'}}>${dev.completed}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',color:'var(--cy)',textAlign:'center'}}>${dev.inProg}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',color:dev.blocked>0?'var(--rd)':'var(--tx3)',textAlign:'center'}}>${dev.blocked}</td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',fontWeight:dev.overdue>0?700:400,
                    color:dev.overdue>0?'var(--rd)':'var(--tx3)',textAlign:'center'}}>${dev.overdue}</td>
                  <!-- Avg % bar -->
                  <td style=${{padding:'9px 10px'}}>
                    <div style=${{display:'flex',alignItems:'center',gap:5}}>
                      <div style=${{width:50,height:4,background:'var(--bd)',borderRadius:100,overflow:'hidden',flexShrink:0}}>
                        <div style=${{height:'100%',width:dev.avgPct+'%',borderRadius:100,
                          background:dev.avgPct>70?'var(--gn)':dev.avgPct>40?'var(--am)':'var(--rd)'}}></div>
                      </div>
                      <span style=${{fontSize:10,fontFamily:'monospace',color:'var(--tx2)',flexShrink:0}}>${dev.avgPct}%</span>
                    </div>
                  </td>
                  <td style=${{padding:'9px 10px',fontFamily:'monospace',color:dev.last7>0?'var(--ac)':'var(--tx3)',
                    fontWeight:dev.last7>0?700:400,textAlign:'center'}}>${dev.last7}</td>
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

        <!-- CHART TAB -->
        ${tab==='chart'&&!selDev?html`
          <div style=${{padding:'16px 20px',display:'flex',flexDirection:'column',gap:14}}>
            <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'16px 20px'}}>
              <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',marginBottom:14}}>Task Distribution per Developer</h3>
              <${RC.ResponsiveContainer} width="100%" height=${Math.max(200,filtered.length*28)}>
                <${RC.BarChart} data=${chartData} layout="vertical" barSize=${14} margin=${{top:0,right:30,bottom:0,left:60}}>
                  <${RC.CartesianGrid} strokeDasharray="3 3" stroke="var(--bd)" horizontal=${false}/>
                  <${RC.XAxis} type="number" tick=${{fill:'var(--tx3)',fontSize:10}} axisLine=${false} tickLine=${false} allowDecimals=${false}/>
                  <${RC.YAxis} type="category" dataKey="name" tick=${{fill:'var(--tx2)',fontSize:11}} axisLine=${false} tickLine=${false} width=${55}/>
                  <${RC.Tooltip} contentStyle=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:10,color:'var(--tx)',fontSize:11}}/>
                  <${RC.Legend} iconSize=${8} wrapperStyle=${{fontSize:10,color:'var(--tx2)',paddingTop:8}}/>
                  <${RC.Bar} dataKey="Completed" stackId="a" fill="var(--gn)" radius=${[0,0,0,0]}/>
                  <${RC.Bar} dataKey="In Progress" stackId="a" fill="var(--cy)" radius=${[0,0,0,0]}/>
                  <${RC.Bar} dataKey="Blocked" stackId="a" fill="var(--rd)" radius=${[0,4,4,0]}/>
                <//>
              <//>
              <p style=${{fontSize:10,color:'var(--tx3)',marginTop:8,textAlign:'center'}}>All ${filtered.length} developers shown — horizontal bars scale with task count</p>
            </div>
          </div>`:null}

        <!-- DETAIL VIEW -->
        ${selDev?html`
          <div style=${{padding:'14px 18px',display:'flex',flexDirection:'column',gap:12}}>
            <!-- Back button -->
            <div>
              <button onClick=${()=>closeDetail()}
                style=${{display:'inline-flex',alignItems:'center',gap:6,padding:'6px 12px',borderRadius:8,border:'1px solid var(--bd)',background:'var(--sf)',color:'var(--tx2)',fontSize:12,fontWeight:600,cursor:'pointer',transition:'all .12s'}}
                onMouseEnter=${e=>{e.currentTarget.style.borderColor='var(--ac)';e.currentTarget.style.color='var(--ac)';}}
                onMouseLeave=${e=>{e.currentTarget.style.borderColor='var(--bd)';e.currentTarget.style.color='var(--tx2)';}}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="15 18 9 12 15 6"/></svg>
                All Developers
              </button>
            </div>
            <!-- Dev profile card -->
            <div style=${{background:'var(--sf)',border:'1px solid var(--bd)',borderRadius:12,padding:'16px 20px',
              display:'flex',alignItems:'center',gap:14,flexWrap:'wrap'}}>
              <${Av} u=${selDev} size=${52}/>
              <div style=${{flex:1,minWidth:100}}>
                <div style=${{fontSize:17,fontWeight:800,color:'var(--tx)'}}>${selDev.name}</div>
                <div style=${{fontSize:12,color:'var(--tx2)',marginTop:3}}>${selDev.role||'Team Member'}</div>
              </div>
              <div style=${{width:58,height:58,borderRadius:'50%',border:'3px solid '+selDev.scoreColor,
                display:'flex',alignItems:'center',justifyContent:'center',flexDirection:'column',
                background:'rgba(255,255,255,.03)',flexShrink:0}}>
                <span style=${{fontSize:20,fontWeight:900,color:selDev.scoreColor,fontFamily:'monospace',lineHeight:1}}>${selDev.score}</span>
                <span style=${{fontSize:8,color:'var(--tx3)',textTransform:'uppercase'}}>score</span>
              </div>
              ${[
                {l:'Total',v:selDev.total,c:'var(--tx)'},
                {l:'Done',v:selDev.completed,c:'var(--gn)'},
                {l:'Active',v:selDev.inProg,c:'var(--cy)'},
                {l:'Blocked',v:selDev.blocked,c:selDev.blocked>0?'var(--rd)':'var(--tx3)'},
                {l:'Overdue',v:selDev.overdue,c:selDev.overdue>0?'var(--rd)':'var(--tx3)'},
                {l:'Avg%',v:selDev.avgPct+'%',c:selDev.avgPct>70?'var(--gn)':selDev.avgPct>40?'var(--am)':'var(--rd)'},
                {l:'Last7d',v:selDev.last7,c:selDev.last7>0?'var(--ac)':'var(--tx3)'},
              ].map(s=>html`
                <div key=${s.l} style=${{textAlign:'center',padding:'6px 10px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)',minWidth:48}}>
                  <div style=${{fontSize:16,fontWeight:800,color:s.c,fontFamily:'monospace',lineHeight:1}}>${s.v}</div>
                  <div style=${{fontSize:8,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.4,marginTop:2}}>${s.l}</div>
                </div>`)}
            </div>
            <!-- Tasks -->
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

      </div><!-- end scroll -->
    </div>`;
}
function renderMd(text){
  return text.replace(/[*][*](.*?)[*][*]/g,'<b>$1</b>');
}
function MessagesView({projects,users,cu,tasks}){
  const [allProjects,setAllProjects]=useState(safe(projects));
  const [lastMsgTs,setLastMsgTs]=useState({}); // projectId → last message ISO ts
  useEffect(()=>{api.get('/api/projects/all').then(d=>{if(Array.isArray(d)&&d.length)setAllProjects(d);});},[]);
  // Load real last-message timestamps for all channels
  useEffect(()=>{
    const fetchTs=()=>api.get('/api/projects/last-messages').then(d=>{if(d&&typeof d==='object')setLastMsgTs(d);});
    fetchTs();
    const id=setInterval(fetchTs,8000); // refresh every 8s
    return()=>clearInterval(id);
  },[]);

  const [pid,setPid]=useState((safe(projects)[0]&&safe(projects)[0].id)||'');
  const [msgs,setMsgs]=useState([]);const [txt,setTxt]=useState('');const ref=useRef(null);
  const [showInfo,setShowInfo]=useState(false);
  const [chanSearch,setChanSearch]=useState('');
  const [newestFirst,setNewestFirst]=useState(false);

  const loadMsgs=useCallback(async(id)=>{
    if(!id)return;
    const d=await api.get('/api/messages?project='+id);
    if(Array.isArray(d)) setMsgs(d);
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
              // Update last message timestamp for this project
              if(d.length>0){
                const latest=d.reduce((mx,m)=>m.ts>mx?m.ts:mx,'');
                setLastMsgTs(prev=>({...prev,[pid]:latest}));
              }
            }
            return d;
          });
        }
      });
    },4000);
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
    // Immediately update last message ts for this channel
    setLastMsgTs(prev=>({...prev,[pid]:m.ts||new Date().toISOString()}));
  };

  // Sort channels by REAL last message timestamp (most recent activity first)
  const sortedProjects=useMemo(()=>{
    let rows=[...allProjects];
    if(chanSearch.trim()){
      const q=chanSearch.toLowerCase();
      rows=rows.filter(p=>p.name.toLowerCase().includes(q));
    }
    rows.sort((a,b)=>{
      const aTs=lastMsgTs[a.id]||a.created||'';
      const bTs=lastMsgTs[b.id]||b.created||'';
      return bTs.localeCompare(aTs); // ISO strings sort correctly as strings
    });
    return rows;
  },[allProjects,chanSearch,lastMsgTs]);

  return html`<div class="fi" style=${{display:'flex',height:'100%',overflow:'hidden'}}>

    <!-- ── Channel sidebar ── -->
    <div style=${{width:220,borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',flexShrink:0}}>
      <!-- Sidebar header + search -->
      <div style=${{padding:'10px 10px 8px',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
        <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:7}}>
          <span style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7}}>Channels</span>
          <span style=${{fontSize:10,color:'var(--tx3)'}}>${sortedProjects.length} of ${allProjects.length}</span>
        </div>
        <!-- Search -->
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
      <!-- Channel list — sorted by recent activity -->
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
                ${hasRecentMsg?html`<div style=${{width:6,height:6,borderRadius:'50%',background:'var(--ac)',flexShrink:0,boxShadow:'0 0 6px var(--ac)'}} title="Recent activity"></div>`:null}
                ${activeCnt>0?html`<span style=${{fontSize:9,background:p.color+'33',color:p.color,borderRadius:5,padding:'1px 5px',fontWeight:700,flexShrink:0}}>${activeCnt}</span>`:null}
              </div>
            </button>`;
        })}
      </div>
    </div>

    <!-- ── Main chat ── -->
    <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden'}}>
      <!-- Channel header -->
      <div style=${{padding:'9px 14px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:9,flexShrink:0}}>
        ${sp?html`
          <div style=${{width:9,height:9,borderRadius:2,background:sp.color}}></div>
          <span style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}># ${sp.name}</span>
          <span style=${{fontSize:11,color:'var(--tx3)',marginLeft:4}}>${projTasks.length} tasks · ${pc}% done</span>
          <!-- Newest first toggle -->
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

      <!-- Project info panel (collapsible) -->
      ${showInfo&&sp?html`
        <div style=${{padding:'12px 16px',background:'var(--sf2)',borderBottom:'1px solid var(--bd)',flexShrink:0}}>
          <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr 1fr 1fr',gap:10,marginBottom:12}}>
            ${[
              {label:'Total Tasks',val:projTasks.length,color:'var(--tx)'},
              {label:'Completed',val:doneTasks,color:'var(--gn)'},
              {label:'In Progress',val:projTasks.filter(t=>t.stage==='development'||t.stage==='testing'||t.stage==='uat').length,color:'var(--cy)'},
              {label:'Blocked',val:blockedTasks,color:'var(--rd)'},
            ].map(s=>html`
              <div key=${s.label} style=${{background:'var(--sf)',borderRadius:9,padding:'10px 12px',border:'1px solid var(--bd)'}}>
                <div style=${{fontSize:20,fontWeight:800,color:s.color,lineHeight:1}}>${s.val}</div>
                <div style=${{fontSize:10,color:'var(--tx3)',marginTop:3}}>${s.label}</div>
              </div>`)}
          </div>
          <div style=${{marginBottom:10}}>
            <div style=${{display:'flex',justifyContent:'space-between',marginBottom:4}}>
              <span style=${{fontSize:11,color:'var(--tx3)'}}>Overall Progress</span>
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
            <span style=${{fontSize:11,color:'var(--tx3)'}}>Members:</span>
            <div style=${{display:'flex',gap:-4}}>
              ${projMembers.slice(0,8).map((m,i)=>html`<div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-6:0,border:'2px solid var(--sf2)',borderRadius:'50%'}}><${Av} u=${m} size=${22}/></div>`)}
              ${projMembers.length>8?html`<span style=${{fontSize:10,color:'var(--tx3)',marginLeft:6}}>+${projMembers.length-8} more</span>`:null}
            </div>
          </div>
        </div>`:null}

      <!-- Messages area -->
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
          // Sort: newest first OR oldest first based on toggle
          const sorted=[...msgs].sort((a,b)=>newestFirst
            ? new Date(b.ts)-new Date(a.ts)
            : new Date(a.ts)-new Date(b.ts));
          // Group by date
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
                  <div style=${{padding:'9px 13px',borderRadius:12,fontSize:13,lineHeight:1.5,
                    background:isMe?'var(--ac)':'var(--sf2)',color:isMe?'var(--ac-tx)':'var(--tx)',
                    border:isMe?'none':'1px solid var(--bd)',
                    borderBottomRightRadius:isMe?3:12,borderBottomLeftRadius:isMe?12:3}}>${m.content}</div>
                  <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${timeStr}</span>
                </div>
              </div>`;
          });
        })()}
        ${msgs.length===0?html`<div style=${{textAlign:'center',paddingTop:48,color:'var(--tx3)',fontSize:13}}>
          <div style=${{fontSize:28,marginBottom:8}}>💬</div>
          <p>No messages yet. Task activity will appear here automatically.</p>
        </div>`:null}
      </div>

      <!-- Message input -->
      <div style=${{padding:'10px 14px',borderTop:'1px solid var(--bd)',display:'flex',gap:8,flexShrink:0}}>
        <input class="inp" style=${{flex:1}} placeholder=${'Message in #'+((sp&&sp.name)||'...')} value=${txt}
          onInput=${e=>setTxt(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&!e.shiftKey&&send()}/>
        <button class="btn bp" style=${{padding:'8px 14px',fontSize:12}} onClick=${send}>➤</button>
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
function DirectMessages({cu,users,dmUnread,onDmRead,onStartHuddle}){
  const others=safe(users).filter(u=>u.id!==cu.id);
  const [toId,setToId]=useState(others[0]&&others[0].id||'');const [msgs,setMsgs]=useState([]);const [txt,setTxt]=useState('');const [search,setSearch]=useState('');const ref=useRef(null);
  const prevMsgCount=useRef(0);
  const loadMsgs=useCallback(async(id)=>{if(!id)return;const d=await api.get('/api/dm/'+id);if(Array.isArray(d)){setMsgs(d);onDmRead(id);};},[onDmRead]);
  // Auto-poll every 3 seconds for new messages in active chat
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
            <div style=${{position:'relative',flexShrink:0}}><${Av} u=${u} size=${32}/><div style=${{position:'absolute',bottom:0,right:0,width:8,height:8,borderRadius:'50%',background:'var(--gn)',border:'2px solid var(--sf)'}}></div></div>
            <div style=${{flex:1,minWidth:0,textAlign:'left'}}><div style=${{fontSize:13,fontWeight:600,color:'#000000',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${u.name}</div><div style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${u.role}</div></div>
            ${unr>0?html`<span style=${{background:'var(--ac)',color:'#fff',borderRadius:10,fontSize:10,padding:'2px 6px',fontFamily:'monospace',fontWeight:700}}>${unr}</span>`:null}
          </button>`;})}
      </div>
    </div>
    <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden'}}>
      <div style=${{padding:'11px 16px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:11,flexShrink:0}}>
        ${toUser?html`<div style=${{position:'relative'}}><${Av} u=${toUser} size=${36}/><div style=${{position:'absolute',bottom:0,right:0,width:9,height:9,borderRadius:'50%',background:'var(--gn)',border:'2px solid var(--sf)'}}></div></div><div><div style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}>${toUser.name}</div><div style=${{fontSize:11,color:'var(--tx3)'}}>${toUser.role}</div></div>
          <button title=${'Start huddle with '+toUser.name}
            onClick=${()=>onStartHuddle&&onStartHuddle(toUser)}
            style=${{marginLeft:'auto',width:34,height:34,borderRadius:10,border:'1px solid var(--bd)',background:'var(--sf2)',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:'var(--tx2)',transition:'all .15s',flexShrink:0}}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>
            </svg>
          </button>`:html`<span style=${{color:'var(--tx3)'}}>Select someone to chat</span>`}
      </div>
      <div ref=${ref} style=${{flex:1,overflowY:'auto',padding:'16px',display:'flex',flexDirection:'column',gap:12}}>
        ${msgs.length===0?html`<div style=${{textAlign:'center',paddingTop:60,color:'var(--tx3)',fontSize:13}}><div style=${{fontSize:36,marginBottom:10}}>👋</div><div style=${{fontWeight:600,marginBottom:4,color:'var(--tx2)'}}>${toUser?'Start a conversation with '+toUser.name:'Select someone'}</div></div>`:null}
        ${msgs.map((m,i)=>{const isMe=m.sender===cu.id;const showT=i===msgs.length-1||msgs[i+1].sender!==m.sender;return html`
          <div key=${m.id} style=${{display:'flex',gap:8,alignItems:'flex-end',flexDirection:isMe?'row-reverse':'row'}}>
            <div style=${{width:28,flexShrink:0}}>${!isMe&&(i===0||msgs[i-1].sender!==m.sender)?html`<${Av} u=${toUser} size=${28}/>`:null}</div>
            <div style=${{display:'flex',flexDirection:'column',gap:2,alignItems:isMe?'flex-end':'flex-start',maxWidth:'68%'}}>
              <div style=${{padding:'9px 13px',borderRadius:14,fontSize:13,lineHeight:1.55,wordBreak:'break-word',background:isMe?'var(--ac)':'var(--sf2)',color:isMe?'var(--ac-tx)':'var(--tx)',border:isMe?'none':'1px solid var(--bd)',borderBottomRightRadius:isMe?3:14,borderBottomLeftRadius:isMe?14:3}}>${m.content}</div>
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
    task_assigned:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>`,c:'var(--ac)',nav:'tasks',label:'View Tasks'},
    status_change:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/></svg>`,c:'var(--cy)',nav:'tasks',label:'View Tasks'},
    comment:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,c:'var(--pu)',nav:'tasks',label:'View Tasks'},
    deadline:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,c:'var(--am)',nav:'tasks',label:'View Tasks'},
    dm:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><circle cx="9" cy="10" r="1" fill="currentColor"/><circle cx="12" cy="10" r="1" fill="currentColor"/><circle cx="15" cy="10" r="1" fill="currentColor"/></svg>`,c:'#06b6d4',nav:'dm',label:'Open Messages'},
    project_added:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><line x1="12" y1="10" x2="12" y2="16"/><line x1="9" y1="13" x2="15" y2="13"/></svg>`,c:'#10b981',nav:'projects',label:'View Projects'},
    reminder:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,c:'#f59e0b',nav:'tasks',label:'View Tasks'},
    call:{icon:html`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.61 3.28a2 2 0 0 1 1.99-2.18h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 8.96a16 16 0 0 0 6.29 6.29l1.24-.82a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>`,c:'#22c55e',nav:'dashboard',label:'Join Huddle'},
  };
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
          <div style=${{width:36,height:36,borderRadius:10,background:T.c+'22',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}}>${T.icon}</div>
          <div style=${{flex:1}}>
            <p style=${{fontSize:13,color:'var(--tx)',fontWeight:n.read?400:600,marginBottom:3}}>${n.content}</p>
            <div style=${{display:'flex',gap:10,alignItems:'center'}}>
              <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${ago(n.ts)}</span>
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
    // reload to get fresh plain_password
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
      <!-- Password cell -->
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
                fontFamily:'monospace',fontSize:12,
                color:showPw?'var(--tx2)':'transparent',
                background:showPw?'transparent':'var(--bd)',
                borderRadius:4,padding:'2px 6px',
                letterSpacing:showPw?'.5px':'.1px',
                userSelect:showPw?'text':'none',
                transition:'all .15s',
                minWidth:70,display:'inline-block'
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
  // Members tab state
  const [showNew,setShowNew]=useState(false);const [name,setName]=useState('');const [email,setEmail]=useState('');const [pw,setPw]=useState('');const [role,setRole]=useState('Developer');const [err,setErr]=useState('');
  // Teams tab state
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
    <!-- Tab switcher -->
    <div style=${{display:'flex',gap:4,marginBottom:18,background:'var(--sf2)',borderRadius:12,padding:4,width:'fit-content',border:'1px solid var(--bd)'}}>
      ${['members','teams'].map(t=>html`
        <button key=${t} class="btn" onClick=${()=>setTab(t)}
          style=${{padding:'6px 18px',borderRadius:9,fontSize:12,fontWeight:600,border:'none',cursor:'pointer',
            background:tab===t?'var(--ac)':'transparent',color:tab===t?'var(--ac-tx)':'var(--tx2)',transition:'all .14s'}}>
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
                <span style=${{fontSize:11,color:'var(--tx3)'}}>${members.length} member${members.length!==1?'s':''}</span>
              </div>
              ${lead?html`<div style=${{display:'flex',alignItems:'center',gap:6,marginBottom:8}}>
                <span style=${{fontSize:11,color:'var(--tx3)'}}>Lead:</span>
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

    <!-- Add Member Modal -->
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

    <!-- New / Edit Team Modal -->
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

  // Load tickets — team-scoped when activeTeam is set
  const load=useCallback(async()=>{
    setBusy(true);
    const url=activeTeam?'/api/tickets?team_id='+activeTeam.id:'/api/tickets';
    const d=await api.get(url);
    setTickets(Array.isArray(d)?d:[]);
    setBusy(false);
  },[activeTeam]);
  useEffect(()=>{load();},[load]);

  // Client-side filtering (status chip + priority + type + resolved toggle)
  const visibleTickets=useMemo(()=>{
    return tickets.filter(t=>{
      // Resolved toggle: hide resolved/closed unless showResolved OR the status chip for resolved/closed is active
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
    bug:{icon:'🐛',color:'var(--rd)',bg:'rgba(248,113,113,.12)',label:'Bug'},
    feature:{icon:'✨',color:'var(--ac)',bg:'rgba(170,255,0,.12)',label:'Feature'},
    improvement:{icon:'🔧',color:'var(--cy)',bg:'rgba(34,211,238,.12)',label:'Improvement'},
    task:{icon:'✅',color:'var(--gn)',bg:'rgba(74,222,128,.12)',label:'Task'},
    question:{icon:'❓',color:'var(--pu)',bg:'rgba(167,139,250,.12)',label:'Question'},
  };
  const PRIORITY_CFG={
    critical:{icon:'🔴',color:'#ef4444',label:'Critical'},
    high:{icon:'🟠',color:'#f97316',label:'High'},
    medium:{icon:'🟡',color:'#eab308',label:'Medium'},
    low:{icon:'🟢',color:'#22c55e',label:'Low'},
  };
  const STATUS_CFG={
    open:{icon:'🔵',color:'var(--cy)',label:'Open'},
    'in-progress':{icon:'🟡',color:'var(--am)',label:'In Progress'},
    review:{icon:'🟣',color:'var(--pu)',label:'In Review'},
    resolved:{icon:'🟢',color:'var(--gn)',label:'Resolved'},
    closed:{icon:'⚫',color:'var(--tx3)',label:'Closed'},
  };

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
            <h2 style=${{fontSize:16,fontWeight:700,color:'var(--tx)',marginBottom:4}}>${detailTicket.title}</h2>
            <div style=${{fontSize:11,color:'var(--tx3)'}}>
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
      <!-- Header -->
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

      <!-- Filter bar -->
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

      <!-- Ticket list -->
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
            <!-- Type icon -->
            <div style=${{width:36,height:36,borderRadius:9,background:tc.bg,display:'flex',alignItems:'center',justifyContent:'center',fontSize:17,flexShrink:0}}>${tc.icon}</div>
            <!-- Info -->
            <div style=${{flex:1,minWidth:0}}>
              <div style=${{display:'flex',alignItems:'center',gap:7,marginBottom:3}}>
                <span style=${{fontSize:13,fontWeight:700,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1}}>${t.title}</span>
                <span style=${{fontSize:10,padding:'1px 7px',borderRadius:5,background:sc.color+'22',color:sc.color,fontWeight:700,flexShrink:0}}>${sc.icon} ${sc.label}</span>
              </div>
              <div style=${{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
                <span style=${{fontSize:10,padding:'1px 6px',borderRadius:4,background:pc.color+'22',color:pc.color,fontWeight:600}}>${pc.icon} ${pc.label}</span>
                <span style=${{fontSize:10,color:'var(--tx3)'}}>${tc.label}</span>
                ${t.project?html`<span style=${{fontSize:10,color:'var(--tx3)'}}>📁 ${(safe(projects).find(p=>p.id===t.project)||{name:t.project}).name}</span>`:null}
                <span style=${{fontSize:10,color:'var(--tx3)',marginLeft:'auto'}}>${new Date(t.created).toLocaleDateString()}</span>
              </div>
            </div>
            <!-- Assignee avatar -->
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
  const PERM_DEFAULTS={
    'Create & Edit Projects':   {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false},
    'Create & Assign Tasks':    {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:false,Viewer:false},
    'Edit Tasks':               {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false},
    'Delete Tasks':             {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false},
    'Create Tickets':           {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:false},
    'Edit Tickets':             {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false},
    'Delete Tickets':           {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false},
    'Close / Resolve Tickets':  {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:false,Viewer:false},
    'Delete Projects':          {Admin:true, Manager:true, TeamLead:false,Developer:false,Tester:false,Viewer:false},
    'Send Channel Messages':    {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:true},
    'Manage Team Members':      {Admin:true, Manager:true, TeamLead:true, Developer:false,Tester:false,Viewer:false},
    'Manage Workspace Settings':{Admin:true, Manager:false,TeamLead:false,Developer:false,Tester:false,Viewer:false},
    'View All Projects':        {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:true},
    'Start Huddle Calls':       {Admin:true, Manager:true, TeamLead:true, Developer:true, Tester:true, Viewer:true},
    'Delete Team Members':      {Admin:true, Manager:false,TeamLead:false,Developer:false,Tester:false,Viewer:false},
  };
  const storedPerms=()=>{try{return JSON.parse(localStorage.getItem('pf_perms')||'null');}catch{return null;}};
  const [perms,setPerms]=useState(()=>storedPerms()||PERM_DEFAULTS);
  const togglePerm=(label,role)=>{
    if(role==='Admin')return;// Admin always has all perms
    setPerms(prev=>{const n={...prev,[label]:{...prev[label],[role]:!prev[label][role]}};localStorage.setItem('pf_perms',JSON.stringify(n));return n;});
  };
  const resetPerms=()=>{setPerms(PERM_DEFAULTS);localStorage.removeItem('pf_perms');};

  useEffect(()=>{api.get('/api/workspace').then(d=>{if(!d.error){setWs(d);setWsName(d.name||'');setAiKey(d.ai_api_key?'•'.repeat(20):'');setEmailEnabled(d.email_enabled!==0);setSmtpServer(d.smtp_server||'smtp.gmail.com');setSmtpPort(d.smtp_port||587);setSmtpUsername(d.smtp_username||'');setSmtpPassword(d.smtp_password?'•'.repeat(16):'');setFromEmail(d.from_email||'');setOtpEnabled(!!d.otp_enabled);}});},[]);

  const save=async()=>{
    setSaving(true);
    const payload={name:wsName,email_enabled:emailEnabled,smtp_server:smtpServer,smtp_port:smtpPort,smtp_username:smtpUsername,from_email:fromEmail,otp_enabled:otpEnabled};
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
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',marginBottom:4}}>🎨 Theme & Accent Color</h3>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Choose a preset or set a custom accent color for the UI.</p>
        <div style=${{display:'flex',gap:10,flexWrap:'wrap',alignItems:'center',marginBottom:12}}>
          ${[
            {name:'Lime',    ac:'#aaff00',ac2:'#99ee00',tx:'#0d1f00'},
            {name:'Cyan',    ac:'#22d3ee',ac2:'#06b6d4',tx:'#001a1f'},
            {name:'Purple',  ac:'#a78bfa',ac2:'#8b5cf6',tx:'#1a0a2e'},
            {name:'Pink',    ac:'#f472b6',ac2:'#ec4899',tx:'#2d001a'},
            {name:'Orange',  ac:'#fb923c',ac2:'#f97316',tx:'#2d0f00'},
            {name:'Green',   ac:'#4ade80',ac2:'#22c55e',tx:'#002d10'},
          ].map(({name,ac,ac2,tx})=>html`
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
            <input type="color" defaultValue="#aaff00"
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
            r.setProperty('--ac','#aaff00');r.setProperty('--ac2','#99ee00');
            r.setProperty('--ac3','rgba(170,255,0,.10)');r.setProperty('--ac4','rgba(170,255,0,.06)');
            r.setProperty('--ac-tx','#0d1f00');
            localStorage.removeItem('pf_accent');
          }}>↺ Reset</button>
        </div>
        <div style=${{fontSize:11,color:'var(--tx3)',padding:'8px 12px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)'}}>
          Preview: <span style=${{color:'var(--ac)',fontWeight:700}}>Active color</span> · <button class="btn bp" style=${{fontSize:10,padding:'2px 8px',marginLeft:4}}>Sample button</button>
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',marginBottom:16}}>🏢 Workspace</h3>
        <div style=${{display:'flex',flexDirection:'column',gap:12}}>
          <div><label class="lbl">Workspace Name</label><input class="inp" value=${wsName} onInput=${e=>setWsName(e.target.value)}/></div>
          <div><label class="lbl">Workspace ID</label><div style=${{fontSize:12,color:'var(--tx3)',fontFamily:'monospace',padding:'8px 12px',background:'var(--sf2)',borderRadius:8}}>${ws.id}</div></div>
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',marginBottom:4}}>🔗 Invite Code</h3>
        <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:14}}>Share this code with teammates to join your workspace.</p>
        <div style=${{display:'flex',alignItems:'center',gap:10}}>
          <div style=${{flex:1,textAlign:'center',padding:'14px',background:'linear-gradient(135deg,rgba(170,255,0,.12),rgba(167,139,250,.08))',borderRadius:12,border:'1px solid rgba(170,255,0,.18)'}}>
            <div style=${{fontSize:28,fontWeight:700,color:'var(--ac2)',fontFamily:'monospace',letterSpacing:4}}>${ws.invite_code}</div>
          </div>
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            <button class="btn bp" style=${{fontSize:12,padding:'8px 14px'}} onClick=${()=>copy(ws.invite_code)}>📋 Copy</button>
            <button class="btn bam" style=${{fontSize:12,padding:'8px 14px'}} onClick=${newInvite}>↻ New Code</button>
          </div>
        </div>
      </div>

      <div class="card" style=${{marginBottom:16}}>
        <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',marginBottom:4}}>🤖 AI Assistant</h3>
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
          <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)'}}>🔐 Role Permissions</h3>
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
            <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',marginBottom:4}}>🔐 Two-Factor Login (OTP)</h3>
            <p style=${{fontSize:12,color:'var(--tx2)',marginBottom:8}}>When enabled, all workspace members must verify their identity with a 6-digit code sent to their email after entering their password. Requires SMTP to be configured above.</p>
            <div style=${{padding:'9px 13px',background:otpEnabled?'rgba(170,255,0,0.06)':'rgba(255,255,255,0.02)',borderRadius:9,border:otpEnabled?'1px solid rgba(170,255,0,0.2)':'1px solid var(--bd)',fontSize:12,color:'var(--tx2)',display:'flex',flexDirection:'column',gap:5}}>
              <div style=${{display:'flex',alignItems:'center',gap:6}}>
                <span>${otpEnabled?'✅':'⬜'}</span>
                <span style=${{fontWeight:600,color:otpEnabled?'var(--ac)':'var(--tx2)'}}>OTP is ${otpEnabled?'ENABLED':'DISABLED'}</span>
              </div>
              ${otpEnabled?html`<div style=${{fontSize:11,color:'var(--tx3)'}}>📧 A 6-digit code will be emailed to each user on every login · Code expires in 10 minutes · Resend available after 60s</div>`:null}
              ${!otpEnabled?html`<div style=${{fontSize:11,color:'var(--tx3)'}}>Users log in with email + password only. Enable OTP to add email verification on every login.</div>`:null}
            </div>
            ${otpEnabled&&!smtpUsername?html`<div style=${{marginTop:8,padding:'7px 12px',background:'rgba(239,68,68,0.07)',borderRadius:8,border:'1px solid rgba(239,68,68,0.2)',fontSize:11,color:'#f87171'}}>⚠️ Warning: SMTP is not configured. OTP emails will fail. Configure SMTP above before enabling OTP.</div>`:null}
          </div>
          <div style=${{flexShrink:0,paddingTop:4}}>
            <label style=${{display:'flex',alignItems:'center',gap:10,cursor:'pointer'}}>
              <div onClick=${()=>setOtpEnabled(!otpEnabled)} style=${{
                width:44,height:24,borderRadius:100,
                background:otpEnabled?'var(--ac)':'rgba(255,255,255,0.1)',
                border:otpEnabled?'1px solid var(--ac)':'1px solid var(--bd)',
                position:'relative',cursor:'pointer',transition:'all .2s',
                flexShrink:0
              }}>
                <div style=${{
                  position:'absolute',top:2,
                  left:otpEnabled?'22px':'2px',
                  width:18,height:18,borderRadius:'50%',
                  background:otpEnabled?'#040506':'var(--tx3)',
                  transition:'left .2s',
                  boxShadow:'0 1px 4px rgba(0,0,0,0.4)'
                }}></div>
              </div>
              <span style=${{fontSize:12,fontWeight:600,color:otpEnabled?'var(--ac)':'var(--tx3)'}}>
                ${otpEnabled?'On':'Off'}
              </span>
            </label>
          </div>
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

/* ─── AIAssistant floating panel ──────────────────────────────────────────── */
function AIAssistant({cu,projects,tasks,users}){
  const [open,setOpen]=useState(false);const [msgs,setMsgs]=useState([]);const [input,setInput]=useState('');const [busy,setBusy]=useState(false);const ref=useRef(null);const iref=useRef(null);

  useEffect(()=>{if(ref.current)ref.current.scrollTop=ref.current.scrollHeight;},[msgs]);

  const QUICK=[
    {label:'📊 EOD Report',msg:'Generate an end-of-day status report for all projects'},
    {label:'🔴 Blocked tasks',msg:'What tasks are blocked and need attention?'},
    {label:'📈 Progress summary',msg:'Give me a quick summary of overall project progress'},
    {label:'⚠️ Overdue',msg:'Are there any overdue tasks?'},
  ];

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
const NOTIF_ICON="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%236366f1'/%3E%3Ccircle cx='32' cy='32' r='9' fill='white'/%3E%3Ccircle cx='32' cy='11' r='6' fill='white' opacity='.95'/%3E%3Ccircle cx='51' cy='43' r='6' fill='white' opacity='.95'/%3E%3Ccircle cx='13' cy='43' r='6' fill='white' opacity='.95'/%3E%3Cline x1='32' y1='17' x2='32' y2='23' stroke='white' stroke-width='3.5' stroke-linecap='round'/%3E%3Cline x1='46' y1='40' x2='40' y2='36' stroke='white' stroke-width='3.5' stroke-linecap='round'/%3E%3Cline x1='18' y1='40' x2='24' y2='36' stroke='white' stroke-width='3.5' stroke-linecap='round'/%3E%3C/svg%3E";

function updateBadge(count){
  // 1. Browser App Badge API (works in Chrome/Edge/Safari PWA + desktop)
  try{
    if(navigator.setAppBadge){
      if(count>0)navigator.setAppBadge(count);
      else navigator.clearAppBadge();
    }
  }catch(e){}
  // 2. Favicon badge via canvas
  try{
    const canvas=document.createElement('canvas');
    canvas.width=32;canvas.height=32;
    const ctx=canvas.getContext('2d');
    // Draw base icon
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
      // Also update document title
      document.title=count>0?'('+count+') ProjectFlow':'ProjectFlowPro';
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
      if(ok)await sendNotification({title:'ProjectFlowPro',body:'Notifications enabled.'});
      return;
    }catch(e){}
  }
  if('Notification' in window&&Notification.permission==='default'){
    const p=await Notification.requestPermission();
    if(p==='granted'){
      // Permission just granted — bootstrap Push subscription immediately
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
      new Notification('ProjectFlowPro',{body:'Desktop notifications enabled! You\'ll be notified for tasks, projects & reminders.',icon:NOTIF_ICON,silent:true});
    }
  }
}

async function showBrowserNotif(title,body,onClick,opts={}){
  if(window.__TAURI__){
    try{
      const {isPermissionGranted,requestPermission,sendNotification}=window.__TAURI__.notification;
      let ok=await isPermissionGranted();
      if(!ok){const p=await requestPermission();ok=(p==='granted');}
      if(ok){await sendNotification({title,body});if(onClick)window._pfNotifCb=onClick;return;}
    }catch(e){}
  }
  if(!('Notification' in window)||Notification.permission!=='granted')return;
  const tag=opts.tag||'pf-'+Date.now();
  // ── Prefer Service Worker showNotification — works in background tabs ───────
  if(window._pfSWReg){
    try{
      await window._pfSWReg.showNotification(title,{
        body,
        icon:NOTIF_ICON,
        badge:NOTIF_ICON,
        tag,
        vibrate:[200,100,200],
        requireInteraction:opts.requireInteraction||false,
        data:{url:'/'}
      });
      // Store click handler keyed by tag so SW message handler can call it
      if(onClick){
        window._pfNotifHandlers=window._pfNotifHandlers||{};
        window._pfNotifHandlers[tag]=onClick;
      }
      return;
    }catch(e){}
  }
  // ── Fallback: standard Notification API (foreground only) ───────────────────
  try{
    const n=new Notification(title,{body,icon:NOTIF_ICON,badge:NOTIF_ICON,tag,requireInteraction:opts.requireInteraction||false,silent:false});
    if(onClick)n.onclick=()=>{window.focus();onClick();n.close();};
    if(!opts.requireInteraction)setTimeout(()=>n.close(),6000);
  }catch(e){}
}

/* ─── In-App Toast System ─────────────────────────────────────────────────── */
// Global toast queue — controlled from App, shared via window ref
window._pfToast=window._pfToast||null; // will be set to addToast fn after mount

const TOAST_CFG={
  dm:      {icon:'💬', color:'var(--ac)',  bg:'var(--ac3)',    nav:'dm'},
  call:    {icon:'📞', color:'var(--gn)',  bg:'rgba(62,207,110,.12)', nav:'dashboard'},
  task_assigned:{icon:'✅',color:'var(--cy)', bg:'rgba(34,211,238,.1)', nav:'tasks'},
  status_change:{icon:'🔄',color:'var(--pu)', bg:'rgba(167,139,250,.1)',nav:'tasks'},
  comment: {icon:'💬', color:'var(--pu)',  bg:'rgba(167,139,250,.1)', nav:'tasks'},
  deadline:{icon:'⏰', color:'var(--am)',  bg:'rgba(245,158,11,.1)',  nav:'tasks'},
  project_added:{icon:'📁',color:'var(--or)',bg:'rgba(251,146,60,.1)',nav:'projects'},
  reminder:{icon:'⏰', color:'var(--rd)',  bg:'rgba(255,68,68,.1)',   nav:'reminders'},
  message: {icon:'#️⃣', color:'#a78bfa',   bg:'rgba(167,139,250,.1)', nav:'messages'},
  default: {icon:'🔔', color:'var(--ac)',  bg:'var(--ac3)',           nav:'notifs'},
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

  // Pre-fill with task due date/time if exists
  useEffect(()=>{
    if(task&&task.due){
      // Convert due date to datetime-local format
      try{
        const d=new Date(task.due);
        if(!isNaN(d)){
          // Set to 9am on due date by default
          d.setHours(9,0,0,0);
          setRemindAt(d.toISOString().slice(0,16));
        }
      }catch(e){}
    } else {
      // Default to 1 hour from now
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
      task_id:task?task.id:'',
      task_title:task?task.title:'Reminder',
      remind_at:alertAt.toISOString(),
      minutes_before:parseInt(minBefore),
    });
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
    // Accept: task selected OR custom title filled (or both — task takes priority for linking)
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
    {label:'Upcoming',val:upcoming.length,color:'var(--cy)',bg:'rgba(34,211,238,.1)',icon:'⚡'},
    {label:'Overdue',val:overdue.length,color:'var(--rd)',bg:'rgba(248,113,113,.1)',icon:'🚨'},
    {label:'Completed',val:completed.length,color:'var(--gn)',bg:'rgba(74,222,128,.1)',icon:'✅'},
    {label:'Today',val:active.filter(r=>{const d=new Date(r.remind_at);return d.toDateString()===now.toDateString();}).length,color:'var(--ac)',bg:'rgba(170,255,0,.1)',icon:'📅'},
  ];

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
                <div style=${{fontSize:11,color:'var(--tx3)',marginTop:2,fontWeight:600}}>${s.label}</div>
              </div>
            </div>`;
        })}
      </div>

      ${showAdd?html`
        <div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowAdd(false)}>
          <div class="mo fi" style=${{maxWidth:600}}>
            <!-- Header -->
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

            <!-- TWO SECTIONS SIDE BY SIDE -->
            <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14,marginBottom:16}}>

              <!-- SECTION 1: Project Reminder -->
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

              <!-- SECTION 2: Custom Reminder -->
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

            <!-- SHARED DATE / TIME / NOTIFY section -->
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
                <span>You'll get a browser notification + sound <b style=${{color:'var(--ac)'}}>${addMins} min</b> before the reminder time.</span>
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
            <span style=${{fontSize:11,color:'var(--tx3)'}}>${upcoming.length} reminder${upcoming.length!==1?'s':''}</span>
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
                      <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${new Date(r.remind_at).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</span>
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
            <span style=${{fontSize:11,color:'var(--tx3)'}}>${overdue.length} past due</span>
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
            <span style=${{fontSize:11,color:'var(--tx3)'}}>${completed.length} done</span>
          </div>
          <div style=${{display:'flex',flexDirection:'column',gap:8}}>
            ${completed.map(r=>html`
              <div key=${r.id} style=${{display:'flex',gap:10,padding:'10px 13px',background:'rgba(74,222,128,.04)',borderRadius:10,border:'1px solid rgba(74,222,128,.15)',alignItems:'center',opacity:.75}}>
                <div style=${{width:32,height:32,borderRadius:8,background:'rgba(74,222,128,.1)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14,flexShrink:0}}>✅</div>
                <div style=${{flex:1,minWidth:0}}>
                  <div style=${{fontSize:12,fontWeight:600,color:'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',textDecoration:'line-through',opacity:.7}}>${r.task_title}</div>
                  <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${new Date(r.remind_at).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</span>
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
                <p style=${{fontSize:11,color:'var(--tx3)'}}>
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

/* ─── HuddleCall — Slack-style Popup Huddle ──────────────────────────────── */
function HuddleCall({cu,users,onStateChange,cmdRef}){
  const [phase,setPhase]=useState('idle'); // idle | preview | in-call
  const [roomId,setRoomId]=useState(null);
  const [roomName,setRoomName]=useState('');
  const [participants,setParticipants]=useState([]);
  const participantsRef=useRef([]);
  const [muted,setMuted]=useState(false);
  const [videoOn,setVideoOn]=useState(false);
  const [elapsed,setElapsed]=useState(0);
  const [incomingCall,setIncomingCall]=useState(null);
  const [targetUser,setTargetUser]=useState(null);
  const [speaking,setSpeaking]=useState({});
  const [handRaised,setHandRaised]=useState(false);
  const [screenSharing,setScreenSharing]=useState(false);
  const [remoteScreenUid,setRemoteScreenUid]=useState(null);
  const remoteScreenRef=useRef(null);
  const [showParticipants,setShowParticipants]=useState(false);
  const [showInvite,setShowInvite]=useState(false);
  const [previewMicOk,setPreviewMicOk]=useState(true);
  const [minimized,setMinimized]=useState(false);
  const [popupPos,setPopupPos]=useState({x:null,y:null});
  const [dragging,setDragging]=useState(false);
  const [showEmojiPicker,setShowEmojiPicker]=useState(false);
  const [showChat,setShowChat]=useState(false);
  const [chatMsgs,setChatMsgs]=useState([]);
  const [chatTxt,setChatTxt]=useState('');
  const [chatUnread,setChatUnread]=useState(0);
  const chatRef=useRef(null);
  const chatPollRef=useRef(null);
  const lastChatCountRef=useRef(0);
  const [floatingReactions,setFloatingReactions]=useState([]);
  const dragOffset=useRef({x:0,y:0});
  const mutedRef=useRef(false);

  const localStream=useRef(null);
  const localVideoRef=useRef(null);
  const screenStream=useRef(null);
  const screenVideoRef=useRef(null);
  const pcs=useRef({});
  const audioEls=useRef({});
  const remoteVideoRefs=useRef({});
  const pollRef=useRef(null);
  const pingRef=useRef(null);
  const timerRef=useRef(null);
  const roomIdRef=useRef(null);
  const phaseRef=useRef('idle');
  const analyserCtxRef=useRef({});

  useEffect(()=>{roomIdRef.current=roomId;},[roomId]);
  useEffect(()=>{participantsRef.current=participants;},[participants]);
  useEffect(()=>{phaseRef.current=phase;},[phase]);
  useEffect(()=>{mutedRef.current=muted;},[muted]);

  useEffect(()=>{
    onStateChange&&onStateChange({
      status:phase==='in-call'?'in-call':'idle',
      roomId,roomName,participants,elapsed,muted,incomingCall,allUsers:users
    });
  },[phase,roomId,roomName,participants,elapsed,muted,incomingCall]);

  if(cmdRef){
    cmdRef.current={
      openHuddle:(user)=>{setTargetUser(user||null);setPhase('preview');setMinimized(false);},
      start:(name)=>doStart(name),
      join:(rid,rname)=>{setTargetUser(null);doJoin(rid,rname);},
      leave:()=>cleanup(),
      mute:()=>toggleMute(),
    };
  }

  const STUN={iceServers:[{urls:'stun:stun.l.google.com:19302'},{urls:'stun:stun1.l.google.com:19302'}]};
  const fmtTime=s=>{const m=Math.floor(s/60),sec=s%60;return m+':'+(sec<10?'0':'')+sec;};
  const centerPos=(w,h)=>({x:Math.max(40,(window.innerWidth-w)/2),y:Math.max(40,(window.innerHeight-h)/2)});

  // Poll for incoming calls when idle
  useEffect(()=>{
    if(phase!=='idle')return;
    let lastId=null;
    const id=setInterval(async()=>{
      try{
        const calls=await api.get('/api/calls');
        if(!Array.isArray(calls)||calls.length===0){setIncomingCall(null);lastId=null;return;}
        const c=calls[0];
        const parts=JSON.parse(c.participants||'[]');
        // Hide if already in call or if YOU initiated it
        if(parts.includes(cu.id)||c.initiator===cu.id){setIncomingCall(null);return;}
        if(c.id===lastId)return; // don't re-notify same call
        lastId=c.id;
        const init=safe(users).find(u=>u.id===c.initiator);
        setIncomingCall({id:c.id,name:c.name,initiatorName:(init&&init.name)||'Someone',initiator:init});
        // Browser notification for incoming call
        showBrowserNotif('📞 Incoming Huddle',(init?init.name:'Someone')+' started a Huddle — click to join',()=>{
          // Focus window and navigate to dashboard when notification clicked
          if(window.electronAPI){window.electronAPI.focusWindow();window.electronAPI.navigateTo('dashboard');}
          else{window.focus();}
          // Also trigger join if huddle cmd available
          if(typeof huddleCmdRef!=='undefined'&&huddleCmdRef&&huddleCmdRef.current){
            const cc=calls&&calls[0]||c;
            setTimeout(()=>{if(huddleCmdRef.current.join)huddleCmdRef.current.join(cc.id,cc.name);},300);
          }
        },{requireInteraction:true,tag:'call-'+c.id});
      }catch(e){}
    },4000);
    return()=>clearInterval(id);
  },[phase,cu,users]);

  // Check mic access on preview
  useEffect(()=>{
    if(phase!=='preview')return;
    navigator.mediaDevices.getUserMedia({audio:true}).then(s=>{s.getTracks().forEach(t=>t.stop());setPreviewMicOk(true);}).catch(()=>setPreviewMicOk(false));
    setPopupPos(p=>(p&&p.x!==null)?p:centerPos(460,380));
  },[phase]);

  useEffect(()=>{
    if(phase==='in-call')setPopupPos(p=>(p&&p.x!==null)?p:centerPos(780,540));
    if(phase==='idle')setPopupPos({x:null,y:null});
  },[phase]);

  // Drag logic
  const onDragStart=e=>{
    if(e.button!==0)return;
    if(!popupPos||popupPos.x===null)return;
    setDragging(true);
    dragOffset.current={x:e.clientX-popupPos.x,y:e.clientY-popupPos.y};
    e.preventDefault();
  };
  useEffect(()=>{
    if(!dragging)return;
    const mm=e=>setPopupPos({
      x:Math.max(0,Math.min(window.innerWidth-80,e.clientX-dragOffset.current.x)),
      y:Math.max(0,Math.min(window.innerHeight-60,e.clientY-dragOffset.current.y))
    });
    const mu=()=>setDragging(false);
    window.addEventListener('mousemove',mm);window.addEventListener('mouseup',mu);
    return()=>{window.removeEventListener('mousemove',mm);window.removeEventListener('mouseup',mu);};
  },[dragging]);

  const detectSpeaking=(uid,stream)=>{
    try{
      // Close existing
      if(analyserCtxRef.current[uid]){try{analyserCtxRef.current[uid].ctx.close();}catch(e){}}
      const ctx=new(window.AudioContext||window.webkitAudioContext)();
      const src=ctx.createMediaStreamSource(stream);
      const an=ctx.createAnalyser();an.fftSize=512;an.smoothingTimeConstant=0.3;
      src.connect(an);
      analyserCtxRef.current[uid]={ctx,an};
      let raf;
      const tick=()=>{
        if(!analyserCtxRef.current[uid])return;
        const d=new Uint8Array(an.frequencyBinCount);an.getByteFrequencyData(d);
        const avg=d.slice(0,100).reduce((a,b)=>a+b,0)/100;
        setSpeaking(s=>{const v=avg>12;if(s[uid]===v)return s;return{...s,[uid]:v};});
        raf=requestAnimationFrame(tick);
      };
      raf=requestAnimationFrame(tick);
      analyserCtxRef.current[uid].raf=raf;
    }catch(e){}
  };

  const stopSpeakingDetect=(uid)=>{
    if(analyserCtxRef.current[uid]){
      try{cancelAnimationFrame(analyserCtxRef.current[uid].raf);}catch(e){}
      try{analyserCtxRef.current[uid].ctx.close();}catch(e){}
      delete analyserCtxRef.current[uid];
    }
  };

  const createPC=(remoteUid,rid)=>{
    if(pcs.current[remoteUid]){try{pcs.current[remoteUid].close();}catch(e){}}
    const pc=new RTCPeerConnection(STUN);
    // Add all local tracks
    if(localStream.current)localStream.current.getTracks().forEach(t=>pc.addTrack(t,localStream.current));
    if(screenStream.current)screenStream.current.getTracks().forEach(t=>pc.addTrack(t,screenStream.current));

    pc.ontrack=e=>{
      const stream=e.streams[0]||new MediaStream([e.track]);
      if(e.track.kind==='audio'){
        // Create or reuse audio element
        if(!audioEls.current[remoteUid]){
          const el=document.createElement('audio');
          el.autoplay=true;el.playsInline=true;
          el.style.cssText='position:absolute;width:0;height:0;opacity:0;pointer-events:none;';
          document.body.appendChild(el);
          audioEls.current[remoteUid]=el;
        }
        audioEls.current[remoteUid].srcObject=stream;
        audioEls.current[remoteUid].play().catch(()=>{});
        detectSpeaking(remoteUid,stream);
      } else if(e.track.kind==='video'){
        // Detect screen share: browsers set contentHint on display capture tracks
        const lbl=(e.track.label||'').toLowerCase();
        const isScreen=e.track.contentHint==='detail'||e.track.contentHint==='text'||
          lbl.includes('screen')||lbl.includes('display')||lbl.includes('monitor')||lbl.includes('entire');
        if(isScreen){
          setRemoteScreenUid(remoteUid);
          setTimeout(()=>{
            if(remoteScreenRef.current){remoteScreenRef.current.srcObject=stream;remoteScreenRef.current.play().catch(()=>{});}
          },80);
          e.track.onended=()=>{setRemoteScreenUid(null);if(remoteScreenRef.current)remoteScreenRef.current.srcObject=null;};
        } else {
          const el=remoteVideoRefs.current[remoteUid];
          if(el){el.srcObject=stream;el.play().catch(()=>{});}
          else{setTimeout(()=>{const el2=remoteVideoRefs.current[remoteUid];if(el2){el2.srcObject=stream;el2.play().catch(()=>{});}},500);}
        }
      }
    };

    pc.onicecandidate=e=>{
      if(e.candidate&&roomIdRef.current)
        api.post('/api/calls/'+roomIdRef.current+'/signal',{to_user:remoteUid,type:'ice',data:e.candidate.toJSON()});
    };

    pc.onconnectionstatechange=()=>{
      if(pc.connectionState==='failed'||pc.connectionState==='closed'){
        try{pc.close();}catch(ex){}delete pcs.current[remoteUid];
      }
    };

    pcs.current[remoteUid]=pc;return pc;
  };

  const startSignalPoll=rid=>{
    if(pollRef.current)clearInterval(pollRef.current);
    pollRef.current=setInterval(async()=>{
      if(phaseRef.current!=='in-call')return;
      try{
        const sigs=await api.get('/api/calls/'+rid+'/signals');
        if(!Array.isArray(sigs))return;
        for(const sig of sigs){
          const from=sig.from_user;let data;
          try{data=typeof sig.data==='string'?JSON.parse(sig.data):sig.data;}catch{continue;}
          if(sig.type==='offer'){
            const pc=pcs.current[from]||createPC(from,rid);
            try{
              await pc.setRemoteDescription(new RTCSessionDescription(data));
              const ans=await pc.createAnswer();
              await pc.setLocalDescription(ans);
              await api.post('/api/calls/'+rid+'/signal',{to_user:from,type:'answer',data:{type:ans.type,sdp:ans.sdp}});
            }catch(ex){}
          } else if(sig.type==='answer'){
            const pc=pcs.current[from];
            if(pc&&pc.signalingState==='have-local-offer'){try{await pc.setRemoteDescription(new RTCSessionDescription(data));}catch(ex){}}
          } else if(sig.type==='ice'){
            const pc=pcs.current[from];
            if(pc&&pc.remoteDescription){try{await pc.addIceCandidate(new RTCIceCandidate(data));}catch(ex){}}          } else if(sig.type==='screen-start'){
            setRemoteScreenUid(from);
          } else if(sig.type==='screen-stop'){
            setRemoteScreenUid(null);
            if(remoteScreenRef.current)remoteScreenRef.current.srcObject=null;
          } else if(sig.type==='reaction'){
            const id=Date.now();
            setFloatingReactions(prev=>[...prev,{id,emoji:data.emoji,x:20+Math.random()*60,label:data.from}]);
            setTimeout(()=>setFloatingReactions(prev=>prev.filter(r=>r.id!==id)),3000);
          } else if(sig.type==='hand'){
            const hid='hand-'+from;
            if(data.raised){
              setFloatingReactions(prev=>[...prev.filter(r=>r.id!==hid),{id:hid,emoji:'✋',x:10+Math.random()*30,label:data.from}]);
              setTimeout(()=>setFloatingReactions(prev=>prev.filter(r=>r.id!==hid)),6000);
            } else {
              setFloatingReactions(prev=>prev.filter(r=>r.id!==hid));
            }
          }
        }
      }catch(e){}
    },1500);
  };

  const startPing=rid=>{
    if(pingRef.current)clearInterval(pingRef.current);
    pingRef.current=setInterval(async()=>{
      try{
        const r=await api.post('/api/calls/'+rid+'/ping',{});
        if(!r||r.error){cleanup();return;}
        setParticipants(r.participants||[]);
        // Notify when someone new joins
      }catch(e){}
    },4000);
  };

  const startTimer=()=>{
    setElapsed(0);if(timerRef.current)clearInterval(timerRef.current);
    timerRef.current=setInterval(()=>setElapsed(e=>e+1),1000);
  };

  const getAudio=async()=>{
    try{return await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});}
    catch(e){return null;}
  };

  const doStart=async(name)=>{
    const s=await getAudio();
    if(!s){alert('Microphone access required. Please allow it in your browser.');return;}
    localStream.current=s;
    // Apply mute state
    s.getAudioTracks().forEach(t=>{t.enabled=!mutedRef.current;});
    detectSpeaking(cu.id,s);
    const roomLabel=name||(targetUser?cu.name+' ↔ '+targetUser.name:cu.name+"'s Huddle");
    const r=await api.post('/api/calls',{name:roomLabel});
    if(!r||r.error){alert('Could not start huddle.');localStream.current.getTracks().forEach(t=>t.stop());localStream.current=null;return;}
    setRoomId(r.room_id);setRoomName(r.name||roomLabel);
    setParticipants([cu.id]);setPhase('in-call');setIncomingCall(null);
    setPopupPos(centerPos(780,540));
    // If targetUser, auto-invite them
    if(targetUser){
      setTimeout(()=>api.post('/api/calls/'+r.room_id+'/invite/'+targetUser.id,{}),500);
    }
    playSound('notif');startSignalPoll(r.room_id);startPing(r.room_id);startTimer();startChatPoll(r.room_id);
  };

  const doJoin=async(rid,rname)=>{
    const s=await getAudio();
    if(!s){alert('Microphone access required.');return;}
    localStream.current=s;
    s.getAudioTracks().forEach(t=>{t.enabled=!mutedRef.current;});
    detectSpeaking(cu.id,s);
    const r=await api.post('/api/calls/'+rid+'/join',{});
    if(!r||r.error){alert(r&&r.error||'Could not join.');localStream.current.getTracks().forEach(t=>t.stop());localStream.current=null;return;}
    const parts=r.participants||[];
    setRoomId(rid);setRoomName(r.name||rname||'Huddle');
    setParticipants(parts);setPhase('in-call');setIncomingCall(null);
    setPopupPos(centerPos(780,540));
    playSound('notif');
    startChatPoll(rid);
    // Send offers to existing participants
    for(const uid of parts){
      if(uid===cu.id)continue;
      const pc=createPC(uid,rid);
      try{
        const offer=await pc.createOffer();
        await pc.setLocalDescription(offer);
        await api.post('/api/calls/'+rid+'/signal',{to_user:uid,type:'offer',data:{type:offer.type,sdp:offer.sdp}});
      }catch(ex){}
    }
    startSignalPoll(rid);startPing(rid);startTimer();
  };

  const cleanup=async()=>{
    if(pollRef.current)clearInterval(pollRef.current);
    if(pingRef.current)clearInterval(pingRef.current);
    if(timerRef.current)clearInterval(timerRef.current);
    Object.values(pcs.current).forEach(pc=>{try{pc.close();}catch(e){}});pcs.current={};
    Object.keys(analyserCtxRef.current).forEach(uid=>stopSpeakingDetect(uid));
    if(localStream.current){localStream.current.getTracks().forEach(t=>t.stop());localStream.current=null;}
    if(screenStream.current){screenStream.current.getTracks().forEach(t=>t.stop());screenStream.current=null;}
    Object.values(audioEls.current).forEach(el=>{try{el.srcObject=null;el.remove();}catch(e){}});audioEls.current={};
    if(roomIdRef.current)try{await api.post('/api/calls/'+roomIdRef.current+'/leave',{});}catch(e){}
    setRoomId(null);setRoomName('');setParticipants([]);
    setPhase('idle');setMuted(false);setVideoOn(false);setElapsed(0);
    setHandRaised(false);setScreenSharing(false);setSpeaking({});
    if(chatPollRef.current)clearInterval(chatPollRef.current);
    setChatMsgs([]);setChatTxt('');setChatUnread(0);lastChatCountRef.current=0;setShowChat(false);
    setRemoteScreenUid(null);setFloatingReactions([]);
    if(remoteScreenRef.current)remoteScreenRef.current.srcObject=null;
    setTargetUser(null);setMinimized(false);setShowInvite(false);
  };

  const startChatPoll=(rid)=>{
    if(chatPollRef.current)clearInterval(chatPollRef.current);
    const getOther=()=>participantsRef.current.find(uid=>uid!==cu.id)||null;
    const doFetch=async()=>{
      const other=getOther();if(!other)return;
      try{const d=await api.get('/api/dm/'+other);if(Array.isArray(d)){setChatMsgs(d);if(d.length>lastChatCountRef.current){if(!showChat)setChatUnread(p=>p+(d.length-lastChatCountRef.current));lastChatCountRef.current=d.length;}}}catch(e){}
    };
    doFetch();chatPollRef.current=setInterval(doFetch,3000);
  };
  const sendChatMsg=async()=>{
    const txt=chatTxt.trim();if(!txt)return;
    const other=participantsRef.current.find(uid=>uid!==cu.id);if(!other)return;
    setChatTxt('');
    await api.post('/api/dm',{recipient:other,content:txt});
    const d=await api.get('/api/dm/'+other);
    if(Array.isArray(d)){setChatMsgs(d);lastChatCountRef.current=d.length;}
  };
  useEffect(()=>{if(chatRef.current)chatRef.current.scrollTop=chatRef.current.scrollHeight;},[chatMsgs]);
  useEffect(()=>{if(showChat)setChatUnread(0);},[showChat]);

  const sendReaction=(emoji)=>{
    const id=Date.now();
    setFloatingReactions(prev=>[...prev,{id,emoji,x:20+Math.random()*60,label:'You'}]);
    setTimeout(()=>setFloatingReactions(prev=>prev.filter(r=>r.id!==id)),3000);
    const room=roomIdRef.current;if(!room)return;
    participantsRef.current.filter(uid=>uid!==cu.id).forEach(uid=>{
      api.post('/api/calls/'+room+'/signal',{to_user:uid,type:'reaction',data:{emoji,from:cu.name}}).catch(()=>{});
    });
  };

  const toggleMute=()=>{
    if(localStream.current){
      const newMuted=!mutedRef.current;
      localStream.current.getAudioTracks().forEach(t=>{t.enabled=!newMuted;});
      setMuted(newMuted);
    }
  };

  const toggleVideo=async()=>{
    if(!videoOn){
      try{
        const vs=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480},facingMode:'user'}});
        vs.getVideoTracks().forEach(t=>{
          if(localStream.current)localStream.current.addTrack(t);
        });
        // Update local video element
        if(localVideoRef.current&&localStream.current){
          localVideoRef.current.srcObject=localStream.current;
          localVideoRef.current.play().catch(()=>{});
        }
        // Renegotiate with all peers
        for(const [uid,pc] of Object.entries(pcs.current)){
          vs.getVideoTracks().forEach(t=>pc.addTrack(t,localStream.current));
          try{
            const offer=await pc.createOffer();
            await pc.setLocalDescription(offer);
            await api.post('/api/calls/'+roomIdRef.current+'/signal',{to_user:uid,type:'offer',data:{type:offer.type,sdp:offer.sdp}});
          }catch(ex){}
        }
        setVideoOn(true);
      }catch(e){alert('Camera access denied or not available.');}
    } else {
      if(localStream.current){
        localStream.current.getVideoTracks().forEach(t=>{t.stop();try{localStream.current.removeTrack(t);}catch(ex){}});
      }
      if(localVideoRef.current)localVideoRef.current.srcObject=null;
      setVideoOn(false);
    }
  };

  const toggleScreenShare=async()=>{
    if(!screenSharing){
      try{
        const ss=await navigator.mediaDevices.getDisplayMedia({video:{cursor:'always',width:{ideal:1920},height:{ideal:1080}},audio:false});
        screenStream.current=ss;
        setScreenSharing(true); // MUST be first — renders <video ref={screenVideoRef}>
        setTimeout(async()=>{
          if(screenVideoRef.current){screenVideoRef.current.srcObject=ss;screenVideoRef.current.play().catch(()=>{});}
          // Send to peers after state settles
          for(const [uid,pc] of Object.entries(pcs.current)){
            ss.getTracks().forEach(t=>pc.addTrack(t,ss));
            try{
              const offer=await pc.createOffer();
              await pc.setLocalDescription(offer);
              await api.post('/api/calls/'+roomIdRef.current+'/signal',{to_user:uid,type:'offer',data:{type:offer.type,sdp:offer.sdp}});
            }catch(ex){}
            // Explicit signal so receiver shows overlay even if contentHint not set
            api.post('/api/calls/'+roomIdRef.current+'/signal',{to_user:uid,type:'screen-start',data:{from:cu.id,name:cu.name}}).catch(()=>{});
          }
        },80);
        ss.getVideoTracks()[0].onended=()=>{
          setScreenSharing(false);screenStream.current=null;
          if(screenVideoRef.current)screenVideoRef.current.srcObject=null;
          if(roomIdRef.current)Object.keys(pcs.current).forEach(uid=>{
            api.post('/api/calls/'+roomIdRef.current+'/signal',{to_user:uid,type:'screen-stop',data:{}}).catch(()=>{});
          });
        };
      }catch(e){}
    } else {
      if(screenStream.current){screenStream.current.getTracks().forEach(t=>t.stop());screenStream.current=null;}
      if(screenVideoRef.current)screenVideoRef.current.srcObject=null;
      setScreenSharing(false);
    }
  };

  const inviteUser=async(uid)=>{
    if(!roomIdRef.current)return;
    await api.post('/api/calls/'+roomIdRef.current+'/invite/'+uid,{});
    // Also send WebRTC offer so they can join audio immediately when they accept
    const pc=createPC(uid,roomIdRef.current);
    try{
      const offer=await pc.createOffer();
      await pc.setLocalDescription(offer);
      await api.post('/api/calls/'+roomIdRef.current+'/signal',{to_user:uid,type:'offer',data:{type:offer.type,sdp:offer.sdp}});
    }catch(ex){}
  };

  const partUsers=participants.map(id=>safe(users).find(u=>u.id===id)||{id,name:'?',avatar:'?',color:'#aaff00'});
  const notInCall=safe(users).filter(u=>u.id!==cu.id&&!participants.includes(u.id));

  // ── INCOMING CALL TOAST
  const incomingToast=incomingCall&&phase==='idle'?html`
    <div style=${{position:'fixed',bottom:24,right:24,zIndex:9100,background:'#1a1625',border:'1px solid rgba(34,197,94,.35)',borderRadius:18,padding:'16px 18px',boxShadow:'0 16px 60px rgba(0,0,0,.7)',minWidth:300,animation:'slideUp .3s cubic-bezier(.2,.8,.4,1)'}}>
      <div style=${{display:'flex',alignItems:'center',gap:11,marginBottom:14}}>
        <div style=${{position:'relative',flexShrink:0}}>
          ${incomingCall.initiator?html`<${Av} u=${incomingCall.initiator} size=${44}/>`:
            html`<div style=${{width:44,height:44,borderRadius:14,background:'linear-gradient(135deg,#22c55e,#16a34a)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:18,fontWeight:700,color:'#fff'}}>${(incomingCall.initiatorName||'?')[0]}</div>`}
          <div style=${{position:'absolute',bottom:-2,right:-2,width:16,height:16,borderRadius:'50%',background:'#22c55e',border:'2px solid #1a1625',display:'flex',alignItems:'center',justifyContent:'center'}}>
            <svg width="8" height="8" viewBox="0 0 24 24" fill="white"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>
          </div>
        </div>
        <div style=${{flex:1}}>
          <div style=${{fontSize:13,fontWeight:700,color:'#fff',marginBottom:2}}>${incomingCall.initiatorName}</div>
          <div style=${{fontSize:11,color:'rgba(255,255,255,.5)',marginBottom:3}}>${incomingCall.name}</div>
          <div style=${{display:'flex',alignItems:'center',gap:4}}>
            <div style=${{width:6,height:6,borderRadius:'50%',background:'#22c55e',animation:'pulse 1s infinite'}}></div>
            <span style=${{fontSize:10,color:'#22c55e',fontWeight:600}}>Huddle in progress</span>
          </div>
        </div>
        <button onClick=${()=>setIncomingCall(null)} style=${{background:'none',border:'none',cursor:'pointer',color:'rgba(255,255,255,.35)',fontSize:19,lineHeight:1,padding:4}}>✕</button>
      </div>
      <div style=${{display:'flex',gap:8}}>
        <button style=${{flex:1,height:40,borderRadius:11,background:'linear-gradient(135deg,#22c55e,#16a34a)',color:'#fff',border:'none',cursor:'pointer',fontWeight:700,fontSize:13,display:'flex',alignItems:'center',justifyContent:'center',gap:7,boxShadow:'0 4px 18px rgba(34,197,94,.35)'}}
          onClick=${()=>{const c=incomingCall;setIncomingCall(null);doJoin(c.id,c.name);}}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>
          Join Huddle
        </button>
        <button style=${{width:40,height:40,borderRadius:11,background:'rgba(239,68,68,.15)',border:'1px solid rgba(239,68,68,.3)',color:'var(--rd2)',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center'}}
          onClick=${()=>setIncomingCall(null)}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
    </div>`:null;

  // ── PREVIEW POPUP
  const previewPopup=phase==='preview'&&popupPos&&popupPos.x!==null?html`
    <div style=${{position:'fixed',left:(popupPos&&popupPos.x||100)+'px',top:(popupPos&&popupPos.y||60)+'px',width:'460px',zIndex:8600,borderRadius:20,overflow:'hidden',boxShadow:'0 24px 80px rgba(0,0,0,.75)',background:'#1a1625',border:'1px solid rgba(255,255,255,.08)',userSelect:dragging?'none':'auto'}}>
      <div onMouseDown=${onDragStart} style=${{background:'#221e30',padding:'13px 18px',display:'flex',alignItems:'center',gap:10,cursor:'move',borderBottom:'1px solid rgba(255,255,255,.07)'}}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2" strokeLinecap="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>
        <span style=${{fontSize:13,fontWeight:700,color:'#fff',flex:1}}>${targetUser?'Huddle with '+targetUser.name:'Start a Huddle'}</span>
        <button onClick=${()=>{setPhase('idle');setTargetUser(null);}} style=${{background:'none',border:'none',cursor:'pointer',color:'rgba(255,255,255,.4)',fontSize:20,lineHeight:1,padding:0}}>✕</button>
      </div>
      <div style=${{background:'#2d2640',minHeight:200,display:'flex',alignItems:'center',justifyContent:'center',position:'relative',overflow:'hidden',padding:'24px'}}>
        <div style=${{position:'absolute',inset:0,background:'radial-gradient(ellipse at 20% 50%,rgba(99,102,241,.18) 0%,transparent 60%),radial-gradient(ellipse at 80% 30%,rgba(34,197,94,.12) 0%,transparent 60%)',pointerEvents:'none'}}></div>
        <div style=${{position:'relative',zIndex:1,display:'flex',flexDirection:'column',alignItems:'center',gap:16}}>
          <div style=${{position:'relative'}}>
            ${cu&&cu.avatar_data&&cu.avatar_data.startsWith('data:image')?
              html`<img src=${cu.avatar_data} style=${{width:80,height:80,borderRadius:'50%',objectFit:'cover',border:'3px solid rgba(255,255,255,.15)'}}/>`:
              html`<div style=${{width:80,height:80,borderRadius:'50%',background:cu.color||'#aaff00',display:'flex',alignItems:'center',justifyContent:'center',fontSize:28,fontWeight:700,color:'#fff',border:'3px solid rgba(255,255,255,.15)'}}>${(cu.avatar||cu.name||'?')[0]}</div>`}
            ${previewMicOk?html`<div style=${{position:'absolute',bottom:2,right:2,width:20,height:20,borderRadius:'50%',background:'#22c55e',border:'2.5px solid #2d2640',display:'flex',alignItems:'center',justifyContent:'center'}}>
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/></svg>
            </div>`:
            html`<div style=${{position:'absolute',bottom:2,right:2,width:20,height:20,borderRadius:'50%',background:'#ef4444',border:'2.5px solid #2d2640',display:'flex',alignItems:'center',justifyContent:'center'}}>
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round"><line x1="1" y1="1" x2="23" y2="23"/></svg>
            </div>`}
          </div>
          <div style=${{display:'flex',gap:8}}>
            <button onClick=${toggleMute} title=${muted?'Unmute':'Mute'}
              style=${{width:40,height:40,borderRadius:11,background:muted?'rgba(239,68,68,.2)':'rgba(255,255,255,.1)',border:'1.5px solid '+(muted?'rgba(239,68,68,.4)':'rgba(255,255,255,.15)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:muted?'var(--rd2)':'#fff',transition:'all .15s'}}>
              ${muted?html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>`:
              html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>`}
            </button>
          </div>
          ${targetUser?html`<div style=${{fontSize:12,color:'rgba(255,255,255,.5)',textAlign:'center'}}>
            Inviting <b style=${{color:'#fff'}}>${targetUser.name}</b> to join automatically
          </div>`:null}
        </div>
      </div>
      ${!previewMicOk?html`
        <div style=${{background:'rgba(251,191,36,.08)',borderTop:'1px solid rgba(251,191,36,.2)',padding:'8px 16px',display:'flex',alignItems:'center',gap:8}}>
          <span>⚠️</span>
          <span style=${{fontSize:11,color:'var(--pu)'}}>Microphone blocked. Click the lock icon in your address bar to allow access.</span>
        </div>`:null}
      <div style=${{padding:'14px 18px',background:'#1a1625',display:'flex',gap:10}}>
        <button style=${{flex:1,height:42,borderRadius:12,background:'rgba(255,255,255,.07)',border:'1px solid rgba(255,255,255,.1)',color:'rgba(255,255,255,.6)',cursor:'pointer',fontWeight:600,fontSize:13}}
          onClick=${()=>{setPhase('idle');setTargetUser(null);}}>Cancel</button>
        <button style=${{flex:2,height:42,borderRadius:12,background:previewMicOk?'linear-gradient(135deg,#22c55e,#16a34a)':'rgba(255,255,255,.08)',border:'none',color:previewMicOk?'#fff':'rgba(255,255,255,.3)',cursor:previewMicOk?'pointer':'not-allowed',fontWeight:700,fontSize:14,display:'flex',alignItems:'center',justifyContent:'center',gap:8,boxShadow:previewMicOk?'0 6px 20px rgba(34,197,94,.3)':'none',transition:'all .2s'}}
          onClick=${previewMicOk?()=>{doStart();}:null}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>
          Start Huddle
        </button>
      </div>
    </div>`:null;

  // ── IN-CALL POPUP
  const callPopup=phase==='in-call'&&popupPos&&popupPos.x!==null?html`
    <div style=${{position:'fixed',left:minimized?(popupPos&&popupPos.x||100)+'px':'0',top:minimized?(popupPos&&popupPos.y||60)+'px':'0',width:minimized?'240px':'100vw',height:minimized?'auto':'100vh',zIndex:8600,borderRadius:minimized?14:0,overflow:'hidden',boxShadow:'0 32px 100px rgba(0,0,0,.85)',background:'#0d0d1a',border:minimized?'1px solid rgba(255,255,255,.07)':'none',display:'flex',flexDirection:'column',transition:dragging?'none':'all .2s',userSelect:dragging?'none':'auto'}}>
      <!-- Title bar (always visible, draggable) -->
      <div onMouseDown=${onDragStart} style=${{background:'rgba(0,0,0,.5)',padding:'9px 14px',display:'flex',alignItems:'center',gap:8,cursor:'move',flexShrink:0,backdropFilter:'blur(10px)',borderBottom:minimized?'none':'1px solid rgba(255,255,255,.05)'}}>
        <div style=${{width:8,height:8,borderRadius:'50%',background:'#22c55e',animation:'pulse 1.5s infinite',flexShrink:0}}></div>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2" strokeLinecap="round" style=${{flexShrink:0}}><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>
        <span style=${{fontSize:12,fontWeight:700,color:'rgba(255,255,255,.85)',flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${roomName||'Huddle'}</span>
        <span style=${{fontSize:11,color:'#22c55e',fontFamily:'monospace',fontWeight:700,flexShrink:0}}>${fmtTime(elapsed)}</span>
        <!-- Mini participant avatars in minimized -->
        ${minimized?html`
          <div style=${{display:'flex',marginLeft:4}}>
            ${partUsers.slice(0,4).map((u,i)=>html`
              <div key=${u.id} style=${{marginLeft:i>0?-6:0,border:'1.5px solid #0d0d1a',borderRadius:'50%',zIndex:4-i}}>
                <${Av} u=${u} size=${22}/>
              </div>`)}
          </div>`:null}
        <!-- Window controls -->
        <div style=${{display:'flex',gap:4,marginLeft:6,flexShrink:0}}>
          <button onClick=${e=>{e.stopPropagation();setMinimized(m=>!m);}}
            title=${minimized?'Expand':'Minimize'}
            style=${{width:24,height:24,borderRadius:7,background:'rgba(255,255,255,.08)',border:'none',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:'rgba(255,255,255,.5)',transition:'all .15s'}}>
            ${minimized?html`<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>`:
            html`<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="10" y1="14" x2="21" y2="3"/><line x1="3" y1="21" x2="14" y2="10"/></svg>`}
          </button>
          <button onClick=${e=>{e.stopPropagation();cleanup();}} title="End call"
            style=${{width:24,height:24,borderRadius:7,background:'rgba(239,68,68,.2)',border:'none',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:'var(--rd2)',transition:'all .15s'}}>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
      </div>
      ${!minimized?html`
        <!-- Main content -->
        <div style=${{flex:1,display:'flex',overflow:'hidden',position:'relative'}}>
          <!-- Gradient background -->
          <div style=${{position:'absolute',inset:0,background:'radial-gradient(ellipse at 10% 40%,rgba(170,255,0,.15) 0%,transparent 55%),radial-gradient(ellipse at 85% 15%,rgba(251,146,60,.12) 0%,transparent 50%),radial-gradient(ellipse at 50% 85%,rgba(34,197,94,.08) 0%,transparent 50%)',pointerEvents:'none'}}></div>
          <!-- Participant tiles -->
          <div style=${{flex:1,display:'flex',flexWrap:'wrap',gap:10,padding:'14px',alignContent:'center',justifyContent:'center',position:'relative',zIndex:1}}>
            ${partUsers.map(u=>{
              const tileW=partUsers.length===1?360:partUsers.length<=2?320:partUsers.length<=4?220:160;
              const tileH=partUsers.length===1?280:partUsers.length<=2?240:partUsers.length<=4?170:130;
              return html`
              <div key=${u.id} style=${{position:'relative',width:tileW,height:tileH,borderRadius:14,overflow:'hidden',background:'rgba(255,255,255,.05)',border:'2px solid '+(speaking[u.id]?'#22c55e':'rgba(255,255,255,.07)'),transition:'border-color .2s,box-shadow .2s',boxShadow:speaking[u.id]?'0 0 0 3px rgba(34,197,94,.2)':'none',flexShrink:0}}>
                <!-- Video element for this remote user -->
                ${u.id!==cu.id?html`<video ref=${el=>{if(el)remoteVideoRefs.current[u.id]=el;}} autoPlay playsInline style=${{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}></video>`:null}
                ${u.id===cu.id&&videoOn?html`<video ref=${localVideoRef} autoPlay playsInline muted style=${{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}></video>`:null}
                <!-- Avatar fallback (shown when no video) -->
                <div style=${{position:'absolute',inset:0,display:'flex',alignItems:'center',justifyContent:'center',zIndex:1,pointerEvents:'none',background:'rgba(20,20,40,.3)'}}>
                  ${u.avatar_data&&u.avatar_data.startsWith('data:image')?
                    html`<img src=${u.avatar_data} style=${{width:partUsers.length<=2?72:52,height:partUsers.length<=2?72:52,borderRadius:'50%',objectFit:'cover',border:'2.5px solid rgba(255,255,255,.2)',opacity:(u.id===cu.id&&videoOn)||u.id!==cu.id?0:1,transition:'opacity .3s'}}/>`:
                    html`<div style=${{width:partUsers.length<=2?72:52,height:partUsers.length<=2?72:52,borderRadius:'50%',background:u.color||'#aaff00',display:'flex',alignItems:'center',justifyContent:'center',fontSize:partUsers.length<=2?26:20,fontWeight:700,color:'#fff',border:'2.5px solid rgba(255,255,255,.15)'}}>${(u.avatar||u.name||'?')[0]}</div>`}
                </div>
                <!-- Name + indicator -->
                <div style=${{position:'absolute',bottom:7,left:7,right:7,zIndex:3,display:'flex',alignItems:'center',gap:5}}>
                  <div style=${{flex:1,background:'rgba(0,0,0,.6)',backdropFilter:'blur(6px)',borderRadius:8,padding:'3px 8px',display:'flex',alignItems:'center',gap:5,minWidth:0}}>
                    ${speaking[u.id]?html`<div style=${{width:6,height:6,borderRadius:'50%',background:'#22c55e',flexShrink:0,animation:'pulse .8s infinite'}}></div>`:null}
                    <span style=${{fontSize:11,fontWeight:600,color:'#fff',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${u.name}${u.id===cu.id?' (you)':''}</span>
                  </div>
                </div>
              </div>`;})}
          </div>
          <!-- FULL-SCREEN SHARE OVERLAY — sharer + receiver -->
          ${(screenSharing||remoteScreenUid)?html`
            <div style=${{position:'absolute',inset:0,zIndex:20,background:'#000',display:'flex',flexDirection:'column',overflow:'hidden'}}>
              ${screenSharing?html`
                <video ref=${screenVideoRef} autoPlay playsInline muted
                  style=${{flex:1,width:'100%',objectFit:'contain',minHeight:0,display:'block'}}></video>`:null}
              ${remoteScreenUid&&!screenSharing?html`
                <video ref=${remoteScreenRef} autoPlay playsInline
                  style=${{flex:1,width:'100%',objectFit:'contain',minHeight:0,display:'block'}}></video>`:null}
              <div style=${{position:'absolute',top:10,left:12,zIndex:22,display:'flex',gap:8,alignItems:'center'}}>
                <div style=${{background:'var(--ac)',color:'var(--ac-tx)',borderRadius:8,padding:'4px 14px',fontSize:12,fontWeight:700}}>
                  📺 ${screenSharing?'You are sharing':(safe(users).find(u=>u.id===remoteScreenUid)||{name:'Someone'}).name+' is sharing'}
                </div>
                ${screenSharing?html`<button onClick=${toggleScreenShare} style=${{background:'rgba(239,68,68,.9)',border:'none',borderRadius:8,padding:'4px 14px',fontSize:12,fontWeight:700,color:'#fff',cursor:'pointer'}}>Stop</button>`:null}
              </div>
              <div style=${{position:'absolute',bottom:0,left:0,right:0,zIndex:22,height:110,display:'flex',gap:8,padding:'8px 14px',background:'linear-gradient(to top,rgba(0,0,0,.85),transparent)',overflowX:'auto',alignItems:'flex-end'}}>
                ${partUsers.map(u=>html`
                  <div key=${u.id} style=${{position:'relative',width:140,height:90,borderRadius:10,overflow:'hidden',background:'rgba(20,20,40,.95)',border:'2px solid '+(speaking[u.id]?'#22c55e':'rgba(255,255,255,.15)'),flexShrink:0}}>
                    ${u.id!==cu.id?html`<video ref=${el=>{if(el)remoteVideoRefs.current[u.id]=el;}} autoPlay playsInline style=${{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}></video>`:null}
                    ${u.id===cu.id&&videoOn?html`<video ref=${localVideoRef} autoPlay playsInline muted style=${{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}></video>`:null}
                    <div style=${{position:'absolute',inset:0,display:'flex',alignItems:'center',justifyContent:'center'}}>
                      ${u.avatar_data&&u.avatar_data.startsWith('data:image')?html`<img src=${u.avatar_data} style=${{width:36,height:36,borderRadius:'50%',objectFit:'cover'}}/>`:html`<div style=${{width:36,height:36,borderRadius:'50%',background:u.color||'var(--ac)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14,fontWeight:700,color:'#fff'}}>${(u.avatar||u.name||'?')[0]}</div>`}
                    </div>
                    <div style=${{position:'absolute',bottom:3,left:3,right:3,background:'rgba(0,0,0,.75)',borderRadius:5,padding:'2px 6px',display:'flex',alignItems:'center',gap:3}}>
                      ${speaking[u.id]?html`<div style=${{width:5,height:5,borderRadius:'50%',background:'#22c55e',animation:'pulse .8s infinite',flexShrink:0}}></div>`:null}
                      <span style=${{fontSize:10,fontWeight:600,color:'#fff',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${u.name}${u.id===cu.id?' (you)':''}</span>
                    </div>
                  </div>`)}
              </div>
            </div>`:null}
          <!-- Side panels: People / Invite -->
          ${(showParticipants||showInvite)?html`
            <div style=${{width:260,background:'rgba(10,10,22,.93)',backdropFilter:'blur(16px)',borderLeft:'1px solid rgba(255,255,255,.08)',display:'flex',flexDirection:'column',overflow:'hidden',zIndex:2,flexShrink:0}}>
              <div style=${{display:'flex',borderBottom:'1px solid rgba(255,255,255,.07)'}}>
                ${[['People',showParticipants,()=>{setShowParticipants(true);setShowInvite(false);}],
                   ['Invite',showInvite,()=>{setShowInvite(true);setShowParticipants(false);}]
                ].map(([lbl,active,fn])=>html`
                  <button key=${lbl} onClick=${fn} style=${{flex:1,padding:'9px 4px',background:active?'rgba(37,99,235,.12)':'none',border:'none',cursor:'pointer',fontSize:10,fontWeight:700,color:active?'#99ee00':'rgba(255,255,255,.4)',textTransform:'uppercase',letterSpacing:.8,borderBottom:active?'2px solid #99ee00':'2px solid transparent',transition:'all .15s'}}>
                    ${lbl}
                  </button>`)}
              </div>
              ${showParticipants?html`<div style=${{flex:1,overflowY:'auto',padding:'8px'}}>${partUsers.map(u=>html`<div key=${u.id} style=${{display:'flex',alignItems:'center',gap:7,padding:'7px 9px',borderRadius:9,background:'rgba(255,255,255,.04)',marginBottom:4}}><div style=${{position:'relative',flexShrink:0}}><${Av} u=${u} size=${28}/><div style=${{position:'absolute',bottom:-1,right:-1,width:9,height:9,borderRadius:'50%',background:speaking[u.id]?'#22c55e':'#374151',border:'1.5px solid #0a0a16'}}></div></div><span style=${{fontSize:12,color:'rgba(255,255,255,.85)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1}}>${u.name}${u.id===cu.id?' (you)':''}</span></div>`)}</div>`:null}
              ${showInvite?html`<div style=${{flex:1,overflowY:'auto',padding:'8px'}}><div style=${{fontSize:10,color:'rgba(255,255,255,.35)',marginBottom:8}}>Click to invite</div>${notInCall.map(u=>html`<button key=${u.id} onClick=${()=>inviteUser(u.id)} style=${{width:'100%',display:'flex',alignItems:'center',gap:7,padding:'7px 9px',borderRadius:9,background:'rgba(255,255,255,.04)',border:'1px solid rgba(255,255,255,.06)',cursor:'pointer',marginBottom:4}} onMouseEnter=${e=>e.currentTarget.style.background='rgba(34,197,94,.1)'} onMouseLeave=${e=>e.currentTarget.style.background='rgba(255,255,255,.04)'}><${Av} u=${u} size=${26}/><span style=${{fontSize:11,color:'rgba(255,255,255,.8)',flex:1,textAlign:'left',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${u.name}</span><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button>`)}</div>`:null}
            </div>`:null}
        </div>
        <!-- Toolbar -->
        <div style=${{background:'rgba(0,0,0,.65)',backdropFilter:'blur(14px)',padding:'8px 16px',display:'flex',alignItems:'center',gap:6,borderTop:'1px solid rgba(255,255,255,.05)',flexShrink:0}}>
          <!-- Signal indicator left -->
          <button style=${{width:34,height:34,borderRadius:9,background:'rgba(255,255,255,.06)',border:'none',cursor:'default',display:'flex',alignItems:'center',justifyContent:'center',color:'rgba(255,255,255,.4)',marginRight:'auto'}} title="Connection quality">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="1" y="16" width="4" height="6" rx="1" opacity=".4"/><rect x="7" y="11" width="4" height="11" rx="1" opacity=".6"/><rect x="13" y="6" width="4" height="16" rx="1" opacity=".8"/><rect x="19" y="1" width="4" height="21" rx="1"/></svg>
          </button>
          <!-- Mic -->
          ${[
            {icon:muted?'mic-off':'mic',label:muted?'Unmute':'Mute',active:muted,color:'var(--rd2)',action:toggleMute,svgOn:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>`,svgOff:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>`},
          ].map(btn=>html`
            <div key=${btn.label} style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:1}}>
              <button onClick=${btn.action} style=${{width:44,height:44,borderRadius:13,background:btn.active?'rgba(239,68,68,.2)':'rgba(255,255,255,.09)',border:'1.5px solid '+(btn.active?'rgba(239,68,68,.4)':'rgba(255,255,255,.12)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:btn.active?btn.color:'#fff',transition:'all .15s'}}>
                ${btn.active?btn.svgOn:btn.svgOff}
              </button>
              <span style=${{fontSize:8,color:'rgba(255,255,255,.35)',lineHeight:1}}>${btn.label}</span>
            </div>`)}
          <!-- Video -->
          <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:1}}>
            <button onClick=${toggleVideo} style=${{width:44,height:44,borderRadius:13,background:videoOn?'rgba(37,99,235,.18)':'rgba(255,255,255,.09)',border:'1.5px solid '+(videoOn?'rgba(170,255,0,.35)':'rgba(255,255,255,.12)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:videoOn?'#99ee00':'#fff',transition:'all .15s'}}>
              ${videoOn?html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>`:
              html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 16v1a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2m5.66 0H14a2 2 0 0 1 2 2v3.34"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`}
            </button>
            <span style=${{fontSize:8,color:'rgba(255,255,255,.35)',lineHeight:1}}>${videoOn?'Video on':'Video'}</span>
          </div>
          <!-- Screen Share -->
          <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:1}}>
            <button onClick=${toggleScreenShare} style=${{width:44,height:44,borderRadius:13,background:screenSharing?'rgba(37,99,235,.25)':'rgba(255,255,255,.09)',border:'1.5px solid '+(screenSharing?'rgba(170,255,0,.4)':'rgba(255,255,255,.12)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:screenSharing?'#99ee00':'#fff',transition:'all .15s'}}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
            </button>
            <span style=${{fontSize:8,color:'rgba(255,255,255,.35)',lineHeight:1}}>Share</span>
          </div>
          <!-- Raise Hand -->
          <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:1}}>
            <button onClick=${()=>{
              const nv=!handRaised;setHandRaised(nv);
              const room=roomIdRef.current;if(!room)return;
              participantsRef.current.filter(uid=>uid!==cu.id).forEach(uid=>{
                api.post('/api/calls/'+room+'/signal',{to_user:uid,type:'hand',data:{raised:nv,from:cu.name}}).catch(()=>{});
              });
            }} style=${{width:44,height:44,borderRadius:13,background:handRaised?'rgba(251,191,36,.25)':'rgba(255,255,255,.09)',border:'1.5px solid '+(handRaised?'rgba(251,191,36,.6)':'rgba(255,255,255,.12)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',fontSize:18,transition:'all .15s',boxShadow:handRaised?'0 0 14px rgba(251,191,36,.5)':'none'}}>
              ${handRaised?'✋':'🖐'}
            </button>
            <span style=${{fontSize:8,color:handRaised?'rgba(251,191,36,.9)':'rgba(255,255,255,.35)',lineHeight:1}}>Hand</span>
          </div>
          <!-- Emoji React -->
          <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:1,position:'relative'}}>
            <button onClick=${()=>setShowEmojiPicker(p=>!p)} style=${{width:44,height:44,borderRadius:13,background:showEmojiPicker?'rgba(251,191,36,.2)':'rgba(255,255,255,.09)',border:'1.5px solid '+(showEmojiPicker?'rgba(251,191,36,.4)':'rgba(255,255,255,.12)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',fontSize:18,transition:'all .15s'}}>😊</button>
            <span style=${{fontSize:8,color:'rgba(255,255,255,.35)',lineHeight:1}}>React</span>
            ${showEmojiPicker?html`
              <div onMouseDown=${e=>{e.stopPropagation();e.preventDefault();}} style=${{position:'absolute',bottom:60,left:'50%',transform:'translateX(-50%)',background:'#1e1b30',border:'1.5px solid rgba(255,255,255,.2)',borderRadius:16,padding:'10px 14px',display:'flex',gap:10,boxShadow:'0 16px 48px rgba(0,0,0,.95)',zIndex:9999,whiteSpace:'nowrap'}}>
                ${['👍','❤️','😂','🎉','🔥','👏','💯','😮'].map(em=>html`
                  <button key=${em} onClick=${e=>{e.stopPropagation();e.preventDefault();sendReaction(em);setShowEmojiPicker(false);}}
                    style=${{background:'none',border:'none',cursor:'pointer',fontSize:22,padding:'4px',borderRadius:8,transition:'transform .1s'}}
                    onMouseEnter=${e=>e.currentTarget.style.transform='scale(1.3)'}
                    onMouseLeave=${e=>e.currentTarget.style.transform='scale(1)'}>
                    ${em}
                  </button>`)}
              </div>`:null}
          </div>
          <!-- Invite / People -->
          <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:1}}>
            <button onClick=${()=>{setShowInvite(p=>!p||showParticipants);setShowParticipants(false);}}
              style=${{width:44,height:44,borderRadius:13,background:(showInvite||showParticipants)?'rgba(37,99,235,.18)':'rgba(255,255,255,.09)',border:'1.5px solid '+((showInvite||showParticipants)?'rgba(170,255,0,.35)':'rgba(255,255,255,.12)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:(showInvite||showParticipants)?'#99ee00':'#fff',transition:'all .15s',position:'relative'}}>
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
              <span style=${{position:'absolute',top:-2,right:-2,width:15,height:15,borderRadius:'50%',background:'#22c55e',fontSize:8,fontWeight:700,color:'#fff',display:'flex',alignItems:'center',justifyContent:'center',border:'1.5px solid #0d0d1a'}}>${participants.length}</span>
            </button>
            <span style=${{fontSize:8,color:'rgba(255,255,255,.35)',lineHeight:1}}>People</span>
          </div>
          <!-- Leave (right) -->
          <div style=${{marginLeft:'auto'}}>
            <button onClick=${cleanup} style=${{height:42,borderRadius:12,background:'linear-gradient(135deg,#ef4444,#dc2626)',border:'none',color:'#fff',padding:'0 20px',cursor:'pointer',fontWeight:700,fontSize:13,display:'flex',alignItems:'center',gap:7,boxShadow:'0 4px 16px rgba(239,68,68,.35)'}}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M10.68 13.31a16 16 0 0 0 3.41 2.6l1.27-1.27a2 2 0 0 1 2.11-.45c.98.37 2.03.57 3.13.57a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2A18 18 0 0 1 2 5a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2c0 1.1.2 2.15.57 3.13a2 2 0 0 1-.45 2.11L8.09 10.27"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
              Leave
            </button>
          </div>
        </div>`:null}
    </div>`:null;

  const reactionsOverlay=floatingReactions.length>0?html`
    <div style=${{position:'fixed',bottom:130,left:popupPos&&popupPos.x?popupPos.x+'px':'50%',zIndex:9200,pointerEvents:'none',width:300}}>
      ${floatingReactions.map(r=>html`
        <div key=${r.id} style=${{position:'absolute',left:r.x+'%',bottom:0,display:'flex',flexDirection:'column',alignItems:'center',gap:2,animation:'floatUp 2.5s ease-out forwards',pointerEvents:'none'}}>
          <span style=${{fontSize:28}}>${r.emoji}</span>
          ${r.label&&r.label!=='You'?html`<span style=${{fontSize:10,fontWeight:700,color:'#fff',background:'rgba(0,0,0,.7)',borderRadius:6,padding:'1px 6px',whiteSpace:'nowrap'}}>${r.label}</span>`:null}
        </div>`)}
    </div>`:null;

  return html`<div>${incomingToast}${previewPopup}${callPopup}${reactionsOverlay}</div>`;
}


/* ─── App ─────────────────────────────────────────────────────────────────── */
function App(){
  const [dark,setDark]=useState(()=>{try{return localStorage.getItem('pf_dark')==='1';}catch{return false;}});const [cu,setCu]=useState(null);const [loading,setLoading]=useState(true);
  const [view,setView]=useState('dashboard');const [col,setCol]=useState(()=>{try{return localStorage.getItem('pf_col')==='1';}catch{return false;}});
  const [initialProjectId,setInitialProjectId]=useState(null);
  // initialProjectId cleared immediately by onClearInitial callback in ProjectsView
  // Restore saved accent color on mount
  useEffect(()=>{
    try{
      const saved=JSON.parse(localStorage.getItem('pf_accent')||'null');
      if(saved&&saved.ac){
        const r=document.body.style;
        r.setProperty('--ac',saved.ac);r.setProperty('--ac2',saved.ac2||saved.ac);
        const hex=saved.ac.replace('#','');const bigint=parseInt(hex,16);
        const ri=Math.round((bigint>>16)&255),gi=Math.round((bigint>>8)&255),bi=Math.round(bigint&255);
        r.setProperty('--ac3','rgba('+ri+','+gi+','+bi+',.10)');
        r.setProperty('--ac4','rgba('+ri+','+gi+','+bi+',.06)');
        r.setProperty('--ac-tx',saved.tx||'#0d1f00');
      }
    }catch(e){}
  },[]);
  const [data,setData]=useState({users:[],projects:[],tasks:[],notifs:[],teams:[]});
  const [teamCtx,setTeamCtxRaw]=useState(()=>{try{return localStorage.getItem('pf_team_ctx')||'';}catch{return '';}});
  const setTeamCtx=useCallback((id,forceDev=false)=>{
    // Developers cannot manually switch team context (unless forceDev bypass)
    if(cu&&cu.role!=='Admin'&&cu.role!=='Manager'&&!forceDev)return;
    // If cu not yet loaded, allow the call (initial auto-scope)
    setTeamCtxRaw(id);
    try{localStorage.setItem('pf_team_ctx',id||'');}catch{}
  },[cu]);
  const [dmUnread,setDmUnread]=useState([]);const [wsName,setWsName]=useState('');
  const [showReminders,setShowReminders]=useState(false);const [reminderTask,setReminderTask]=useState(null);const [upcomingReminders,setUpcomingReminders]=useState([]);
  const [showNotifBanner,setShowNotifBanner]=useState(false);
  const [toasts,setToasts]=useState([]);
  const toastTimers=useRef({});
  const TOAST_DUR=6000; // ms before auto-dismiss

  // ── Add in-app toast ────────────────────────────────────────────────────────
  const addToast=useCallback((type,title,body)=>{
    const id='t'+Date.now()+Math.random();
    const timeStr=new Date().toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'});
    setToasts(prev=>[{id,type,title,body,timeStr,progress:100,leaving:false},...prev].slice(0,5));
    // Countdown progress bar
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

  // Expose addToast globally so polling closures can call it
  useEffect(()=>{window._pfToast=addToast;},[addToast]);

  // ── Fire both OS notif + in-app toast ───────────────────────────────────────
  const notify=useCallback((type,title,body,navTo,opts={})=>{
    // 1. In-app toast (always works, regardless of OS permission)
    addToast(type,title,body);
    // 2. OS/desktop notification (only when permission granted)
    showBrowserNotif(title,body,()=>setView(navTo),{...opts,tag:opts.tag||type+'-'+Date.now()});
    // 3. Sound
    playSound(type==='call'?'call':'notif');
  },[addToast]);

  // Show notification permission banner after login
  useEffect(()=>{
    if(cu&&'Notification' in window&&Notification.permission==='default'){
      setTimeout(()=>setShowNotifBanner(true),2500);
    }
  },[cu]);

  const [callState,setCallState]=useState({status:'idle',roomId:null,roomName:'',participants:[],elapsed:0,muted:false,incomingCall:null,allUsers:[]});
  const huddleCmdRef=useRef({});

  const [teamLoading,setTeamLoading]=useState(false);

  const load=useCallback(async(overrideTeamCtx)=>{
    if(!cu)return;
    const tCtx=overrideTeamCtx!==undefined?overrideTeamCtx:teamCtx;
    try{
      // Build team-scoped API URLs when a team context is active
      const projUrl=tCtx?'/api/projects?team_id='+tCtx:'/api/projects';
      const taskUrl=tCtx?'/api/tasks?team_id='+tCtx:'/api/tasks';
      const [users,projects,tasks,notifs,dmu,ws,teamsRaw]=await Promise.all([
        api.get('/api/users'),api.get(projUrl),api.get(taskUrl),
        api.get('/api/notifications'),api.get('/api/dm/unread'),api.get('/api/workspace'),
        api.get('/api/teams'),
      ]);
      const teams=Array.isArray(teamsRaw)?teamsRaw:[];
      setData({users:Array.isArray(users)?users:[],projects:Array.isArray(projects)?projects:[],tasks:Array.isArray(tasks)?tasks:[],notifs:Array.isArray(notifs)?notifs:[],teams});
      setDmUnread(Array.isArray(dmu)?dmu:[]);
      if(ws&&ws.name)setWsName(ws.name);
      const rems=await api.get('/api/reminders');
      if(Array.isArray(rems)){const now=new Date();setUpcomingReminders(rems.filter(r=>new Date(r.remind_at)>=now).sort((a,b)=>new Date(a.remind_at)-new Date(b.remind_at)));}
    }catch(e){console.error(e);}
  },[cu]);

  useEffect(()=>{api.get('/api/auth/me').then(u=>{if(u&&!u.error)setCu(u);setLoading(false);}).catch(()=>setLoading(false));},[]);
  useEffect(()=>{load();},[load]);

  // ── Reload all data when team context switches ──────────────────────────────
  const prevTeamCtxRef=useRef(teamCtx);
  useEffect(()=>{
    if(!cu)return;
    if(prevTeamCtxRef.current===teamCtx)return; // skip initial mount
    prevTeamCtxRef.current=teamCtx;
    setTeamLoading(true);
    setView('dashboard'); // always go to dashboard on team switch
    // Clear stale data immediately so old data doesn't flash
    setData(prev=>({...prev,projects:[],tasks:[]}));
    load(teamCtx).finally(()=>setTeamLoading(false));
  },[teamCtx,cu]);
  // Auto-refresh projects+tasks every 30s — team-scoped
  useEffect(()=>{
    if(!cu)return;
    const id=setInterval(async()=>{
      try{
        const projUrl=teamCtx?'/api/projects?team_id='+teamCtx:'/api/projects';
        const taskUrl=teamCtx?'/api/tasks?team_id='+teamCtx:'/api/tasks';
        const [projects,tasks]=await Promise.all([api.get(projUrl),api.get(taskUrl)]);
        if(Array.isArray(projects)&&Array.isArray(tasks)){
          setData(prev=>({...prev,projects,tasks}));
        }
      }catch(e){}
    },30000);
    return()=>clearInterval(id);
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

  // ── Poll DM unread every 5s ─────────────────────────────────────────────────
  // Uses a ref for prevDms so closure stays fresh without re-creating interval
  const prevDmsRef=useRef([]);
  useEffect(()=>{
    if(!cu)return;
    // Seed with current on first mount so we don't false-fire on login
    api.get('/api/dm/unread').then(d=>{if(Array.isArray(d)){prevDmsRef.current=d;setDmUnread(d);}});
    const id=setInterval(()=>{
      api.get('/api/dm/unread').then(d=>{
        if(!Array.isArray(d))return;
        const prev=prevDmsRef.current;
        d.forEach(x=>{
          const old=prev.find(p=>p.sender===x.sender);
          if(!old||(x.cnt||0)>(old.cnt||0)){
            // New DM from this sender
            const sender=data.users.find(u=>u.id===x.sender);
            const sname=sender?sender.name:'Someone';
            window._pfToast&&window._pfToast('dm','💬 New message from '+sname,'Tap to open Direct Messages');
            showBrowserNotif('💬 '+sname+' sent you a message','Tap to open',()=>setView('dm'),{tag:'dm-'+x.sender});
            playSound('notif');
          }
        });
        prevDmsRef.current=d;
        setDmUnread(d);
      });
    },5000);
    return()=>clearInterval(id);
  },[cu]); // intentionally omit data.users to avoid reset — sender name is best-effort

  // ── Poll notifications every 6s — fixed: seed prevIds on mount ─────────────
  const prevNotifIdsRef=useRef(null); // null = not yet seeded
  const NTITLES={
    task_assigned:'✅ Task assigned to you',
    status_change:'🔄 Task status changed',
    comment:'💬 New comment on task',
    deadline:'⏰ Deadline approaching',
    dm:'📨 New direct message',
    project_added:'📁 Added to a project',
    reminder:'⏰ Reminder',
    call:'📞 Huddle call',
    message:'#️⃣ New channel message',
  };
  const NNAV={task_assigned:'tasks',status_change:'tasks',comment:'tasks',deadline:'tasks',dm:'dm',project_added:'projects',reminder:'reminders',call:'dashboard',message:'messages'};
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
          if(n.type==='dm'||n.type==='call')return;
          const title=NTITLES[n.type]||'ProjectFlowPro';
          const nav=NNAV[n.type]||'notifs';
          addToast(n.type,title,n.content||'');
          showBrowserNotif(title,n.content||'',()=>setView(nav),{tag:'notif-'+n.id,requireInteraction:n.type==='call'});
          playSound(n.type==='call'?'call':'notif');
        });
        prevNotifIdsRef.current=new Set(d.map(n=>n.id));
        setData(prev=>({...prev,notifs:d}));
        const unread=d.filter(n=>!n.read).length;
        const dmTotal=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);
        updateBadge(unread+dmTotal);
      });
    };

    // Seed baseline on mount
    api.get('/api/notifications').then(d=>{
      if(Array.isArray(d)){
        prevNotifIdsRef.current=new Set(d.map(n=>n.id));
        setData(prev=>({...prev,notifs:d}));
        const unread=d.filter(n=>!n.read).length;
        updateBadge(unread+dmUnread.reduce((a,x)=>a+(x.cnt||0),0));
      }
    });

    // Register for visibility-based immediate poll
    triggerPollRef.current=pollOnce;

    const id=setInterval(pollOnce, 6000);
    return()=>{ clearInterval(id); if(triggerPollRef.current===pollOnce) triggerPollRef.current=null; };
  },[cu,addToast]);

  const onDmRead=useCallback(sid=>{setDmUnread(prev=>prev.filter(x=>x.sender!==sid));},[]);
  const logout=async()=>{
    // Unsubscribe from Web Push before clearing session
    if(window._pfPushUnsubscribe) await window._pfPushUnsubscribe().catch(()=>{});
    await api.post('/api/auth/logout',{});
    setCu(null);setData({users:[],projects:[],tasks:[],notifs:[]});setDmUnread([]);
  };

  // Request browser notification permission on login
  useEffect(()=>{if(cu)requestNotifPermission();},[cu]);

  // ── Visibility-based instant poll ───────────────────────────────────────────
  // When user switches back to this tab we fire a poll immediately so they
  // see fresh data without waiting for the next interval tick.
  const triggerPollRef = useRef(null);
  useEffect(()=>{
    window._pfOnVisible = ()=>{
      if(triggerPollRef.current) triggerPollRef.current();
    };
    return ()=>{ window._pfOnVisible = null; };
  },[]);

  // Update badge on unread changes
  useEffect(()=>{
    const unread=safe(data.notifs).filter(n=>!n.read).length;
    const dmTotal=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);
    updateBadge(unread+dmTotal);
  },[data.notifs,dmUnread]);

  // Poll for due reminders every 30s + check "minutes_before" early warnings
  const firedEarlyRef=useRef(new Set());
  useEffect(()=>{
    if(!cu)return;
    const checkDue=async()=>{
      // Check exact-time reminders from server
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
      // Check "minutes_before" warnings from local state
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
            // Fire if within 60s window and not already fired
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

  // ── Team Context ─────────────────────────────────────────────────────────────
  // Auto-scope developer to their first assigned team on data load
  const isDevRole=cu&&cu.role!=='Admin'&&cu.role!=='Manager';
  const [devNoTeam,setDevNoTeam]=useState(false);
  useEffect(()=>{
    if(!isDevRole||!cu||safe(data.teams).length===0)return;
    // Find all teams this dev belongs to
    const myTeams=safe(data.teams).filter(t=>{
      try{return JSON.parse(t.member_ids||'[]').includes(cu.id);}catch{return false;}
    });
    if(myTeams.length===0){setDevNoTeam(true);return;}
    setDevNoTeam(false);
    if(!teamCtx){
      setTeamCtx(myTeams[0].id,true); // forceDev=true bypasses lock
    } else {
      // Ensure current teamCtx is one dev belongs to
      const valid=myTeams.find(t=>t.id===teamCtx);
      if(!valid)setTeamCtx(myTeams[0].id,true);setView('dashboard');
    }
  },[cu,isDevRole,data.teams,teamCtx,setTeamCtx]);

  // activeTeam: the currently selected team object (or null = all workspace)
  const activeTeam=useMemo(()=>teamCtx?safe(data.teams).find(t=>t.id===teamCtx)||null:null,[teamCtx,data.teams]);
  const teamMemberIds=useMemo(()=>activeTeam?new Set(JSON.parse(activeTeam.member_ids||'[]')):new Set(),[activeTeam]);
  // When teamCtx is set, data.projects/tasks are server-filtered (API ?team_id=).
  // scopedProjects/scopedTasks are direct aliases — clean, no duplication.
  const scopedProjects=data.projects;
  const scopedTasks=data.tasks;
  // scopedUsers: team members only when a team is active
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
      <p style=${{color:'#475569',fontSize:13,marginTop:16,fontFamily:"'DM Sans',sans-serif",letterSpacing:'.3px',fontWeight:500}}>Loading ProjectFlowPro...</p>
      <div style=${{marginTop:10,width:110,height:3,background:'#e2e8f0',borderRadius:100,overflow:'hidden'}}>
        <div style=${{height:'100%',background:'#2563eb',borderRadius:100,animation:'loadBar 1.4s ease-in-out infinite'}}></div>
      </div>
    </div>
  </div>`;
  if(!cu)return html`<${AuthScreen} onLogin=${u=>{setCu(u);}}/>`;

  // Developer with no team assigned — show friendly blocker
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
    dashboard:{title:'Dashboard',sub:activeTeamName?activeTeamName+' Team Dashboard':'Overview of your work'},
    projects:{title:'Projects',sub:scopedProjects.length+' projects'+(activeTeamName?' · '+activeTeamName:'')},
    tasks:{title:'Task Board',sub:scopedTasks.filter(t=>t.stage!=='completed'&&t.stage!=='backlog').length+' active · '+scopedTasks.length+' total'+(activeTeamName?' · '+activeTeamName:'')},
    messages:{title:'Channels',sub:(activeTeamName?activeTeamName+' · ':'')+'Project channels'},
    dm:{title:'Direct Messages',sub:totalDm>0?totalDm+' unread':'Private conversations'},
    reminders:{title:'Reminders',sub:'Upcoming task reminders'},
    notifs:{title:'Notifications',sub:unread+' unread'},
    team:{title:'Team Management',sub:'Members & sub-teams'},
    settings:{title:'Settings',sub:wsName||'Workspace configuration'},
    timeline:{title:'Timeline Tracker',sub:activeTeamName?activeTeamName+' project timeline':'Project schedule'},
    productivity:{title:'Dev Productivity',sub:activeTeamName?activeTeamName+' performance':'Team performance analytics'},
    tickets:{title:'Tickets',sub:activeTeamName?activeTeamName+' tickets':'Support tickets'},
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
      <${Sidebar} cu=${cu} view=${baseView} setView=${setView} onLogout=${logout} unread=${unread} dmUnread=${dmUnread} col=${col} setCol=${v=>{setCol(v);try{localStorage.setItem('pf_col',v?'1':'0');}catch{}}} wsName=${wsName}
        dark=${dark} setDark=${setDark}
        teams=${data.teams} users=${data.users} projects=${scopedProjects} tasks=${scopedTasks}
        teamCtx=${teamCtx} setTeamCtx=${setTeamCtx} activeTeam=${activeTeam}
        callState=${{...callState,allUsers:data.users}}
        onCallAction=${async cmd=>{
          const h=huddleCmdRef.current;
          if(cmd.action==='open_huddle')h.openHuddle&&h.openHuddle(cmd.targetUser||null);
          else if(cmd.action==='start')h.start&&h.start(cmd.name);
          else if(cmd.action==='join')h.join&&h.join(cmd.roomId,cmd.roomName);
          else if(cmd.action==='leave')h.leave&&h.leave();
          else if(cmd.action==='mute')h.mute&&h.mute();
          else if(cmd.action==='invite'&&cmd.userId&&cmd.roomId){await api.post('/api/calls/'+cmd.roomId+'/invite/'+cmd.userId,{});showBrowserNotif('📞 Invite sent','User invited to your Huddle',null,{});}
        }}/>
      <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden',minWidth:0}}>
        <${Header} title=${info.title} sub=${info.sub} dark=${dark} setDark=${setDark} extra=${extra}
          cu=${cu} setCu=${setCu} upcomingReminders=${upcomingReminders} onViewReminders=${()=>setView('reminders')}
          notifs=${data.notifs}
          activeTeam=${activeTeam} teams=${data.teams} setTeamCtx=${setTeamCtx}
          onNotifClick=${async n=>{
            if(!n.read)await api.put('/api/notifications/'+n.id+'/read',{});
            const nav={task_assigned:'tasks',status_change:'tasks',comment:'tasks',deadline:'tasks',dm:'dm',project_added:'projects',reminder:'reminders',call:'dashboard'};
            const dest=nav[n.type]||'notifs';
            setView(dest);
            await load();
          }}
          onMarkAllRead=${async()=>{await api.put('/api/notifications/read-all',{});load();}}
          onClearAll=${async()=>{await api.del('/api/notifications/all');load();}}
        />
        <div style=${{flex:1,overflow:'hidden',display:'flex',flexDirection:'column'}}>
          <${ErrorBoundary}>
            <div key=${baseView+'-'+(teamCtx||'all')} class="page-enter" style=${{flex:1,overflow:'hidden',display:'flex',flexDirection:'column',height:'100%'}}>
            ${baseView==='dashboard'?html`<${Dashboard} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} users=${scopedUsers} onNav=${setView} activeTeam=${activeTeam} teams=${data.teams} setTeamCtx=${setTeamCtx}/>`:null}
            ${baseView==='projects'?html`<${ProjectsView} projects=${scopedProjects} tasks=${scopedTasks} users=${data.users} cu=${cu} reload=${load} onSetReminder=${t=>{setReminderTask(t);}} teams=${data.teams} activeTeam=${activeTeam} initialProjectId=${initialProjectId} onClearInitial=${()=>setInitialProjectId(null)}/>`:null}
            ${baseView==='tasks'?html`<${TasksView} tasks=${scopedTasks} projects=${scopedProjects} users=${scopedUsers} cu=${cu} reload=${load} onSetReminder=${t=>{setReminderTask(t);}} teams=${data.teams} activeTeam=${activeTeam}
              initialStage=${taskFilterType==='stage'?taskFilterValue:null}
              initialPriority=${taskFilterType==='priority'?taskFilterValue:null}
              initialAssignee=${taskFilterType==='assignee'?taskFilterValue:null}
            />`:null}
            ${baseView==='messages'?html`<${MessagesView} projects=${scopedProjects} users=${data.users} cu=${cu} tasks=${scopedTasks}/>`:null}
            ${baseView==='dm'?html`<${DirectMessages} cu=${cu} users=${data.users} dmUnread=${dmUnread} onDmRead=${onDmRead} onStartHuddle=${u=>{huddleCmdRef.current.openHuddle&&huddleCmdRef.current.openHuddle(u);}}/>`:null}
            ${baseView==='reminders'?html`<${RemindersView} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} onSetReminder=${t=>{setReminderTask(t);}} onReload=${load}/>`:null}
            ${baseView==='notifs'?html`<${NotifsView} notifs=${data.notifs} reload=${load} onNavigate=${setView}/>`:null}
            ${baseView==='tickets'?html`<${TicketsView} cu=${cu} users=${scopedUsers} projects=${scopedProjects} onReload=${load} activeTeam=${activeTeam} initialAssignee=${ticketFilterType==='assignee'?ticketFilterValue:null} initialStatus=${ticketFilterType==='status'?ticketFilterValue:null}/>`:null}
            ${baseView==='team'&&(cu.role==='Admin'||cu.role==='Manager'||cu.role==='TeamLead')?html`<${TeamView} users=${data.users} cu=${cu} reload=${load}/>`:null}
            ${baseView==='settings'&&(cu.role==='Admin'||cu.role==='Manager'||cu.role==='TeamLead')?html`<${WorkspaceSettings} cu=${cu} onReload=${load}/>`:null}
            ${baseView==='timeline'?html`<${TimelineView} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} onNav=${(v,pid)=>{setView(v);if(pid)setInitialProjectId(pid);else setInitialProjectId(null);}}/>`:null}
            ${baseView==='productivity'&&(cu.role==='Admin'||cu.role==='Manager')?html`<${ProductivityView} cu=${cu} tasks=${scopedTasks} projects=${scopedProjects} users=${scopedUsers}/>`:null}
            </div>
          <//>
        </div>
      </div>
    </div>
    <${AIAssistant} cu=${cu} projects=${scopedProjects} tasks=${scopedTasks} users=${data.users}/>
    <${HuddleCall} cu=${cu} users=${data.users} onStateChange=${s=>setCallState(prev=>({...prev,...s}))} cmdRef=${huddleCmdRef}/>

    <!-- Team switch loading overlay -->
    ${teamLoading?html`
      <div style=${{position:'fixed',top:0,left:0,right:0,bottom:0,zIndex:9999,
        background:'rgba(0,0,0,.55)',display:'flex',alignItems:'center',justifyContent:'center',
        backdropFilter:'blur(2px)'}}>
        <div style=${{background:'var(--sf)',borderRadius:16,padding:'24px 32px',display:'flex',flexDirection:'column',alignItems:'center',gap:12,border:'1px solid var(--bd)',boxShadow:'0 8px 40px rgba(0,0,0,.5)'}}>
          <div style=${{width:40,height:40,border:'3px solid var(--bd)',borderTop:'3px solid var(--ac)',borderRadius:'50%',animation:'sp .7s linear infinite'}}></div>
          <div style=${{fontSize:13,fontWeight:600,color:'var(--tx)'}}>Switching to ${activeTeam?activeTeam.name:'workspace'}...</div>
          <div style=${{fontSize:11,color:'var(--tx3)'}}>Loading team data</div>
        </div>
      </div>`:null}

    <!-- ★ In-app toast stack — always visible, no OS permission needed -->
    <${ToastStack} toasts=${toasts} onDismiss=${dismissToast} onNav=${setView}/>

    <!-- Notification permission banner — shown once after login -->
    ${showNotifBanner?html`
      <div style=${{position:'fixed',bottom:20,left:'50%',transform:'translateX(-50%)',zIndex:9100,
        background:'var(--sf)',border:'1px solid rgba(170,255,0,.35)',borderRadius:18,
        padding:'16px 20px',boxShadow:'0 8px 40px rgba(0,0,0,.7)',
        display:'flex',alignItems:'flex-start',gap:14,maxWidth:440,
        animation:'slideUp .3s cubic-bezier(.34,1.56,.64,1)'}}>
        <div style=${{width:44,height:44,borderRadius:13,background:'linear-gradient(135deg,rgba(170,255,0,.2),rgba(170,255,0,.05))',border:'1px solid rgba(170,255,0,.35)',
          display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0,fontSize:22}}>🔔</div>
        <div style=${{flex:1,minWidth:0}}>
          <div style=${{fontSize:13,fontWeight:700,color:'var(--tx)',marginBottom:4}}>Enable desktop notifications</div>
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
// Start app once libs are ready
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
        ("react.min.js",     "https://unpkg.com/react@18/umd/react.production.min.js"),
        ("react-dom.min.js", "https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"),
        ("prop-types.min.js","https://unpkg.com/prop-types@15/prop-types.min.js"),
        ("recharts.min.js",  "https://unpkg.com/recharts@2/umd/Recharts.js"),
        ("htm.min.js",       "https://unpkg.com/htm@3/dist/htm.js"),
    ]
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
    print("\n⚡ ProjectFlowPro v4.0 — Multi-Tenant | AI | Workspaces")
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
