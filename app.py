#!/usr/bin/env python3
"""
VEWIT v5.0 - Premium Edition
Apple-inspired design | AI Documentation Generator | Architecture Diagrams
Railway.app ready | GitLab deployable
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

# ── PostgreSQL via pg8000 ────────────────────────────────────────────────────
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
    """Convert SQLite SQL to PostgreSQL."""
    if "INSERT OR IGNORE INTO" in sql:
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO").rstrip()
        sql += " ON CONFLICT DO NOTHING"
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
    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._list = list(values)
    def __getitem__(self, key):
        if isinstance(key, int): return self._list[key]
        return super().__getitem__(key)

class _Cursor:
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

class _DB:
    def __init__(self, conn):
        self._conn = conn
    def execute(self, sql, params=()):
        return _Cursor(self._conn).execute(sql, params)
    def executescript(self, sql):
        stmts = [s.strip() for s in sql.split(";") if s.strip()]
        for stmt in stmts:
            try:
                self._conn.run(stmt)
            except Exception as e:
                msg = str(e).lower()
                if any(x in msg for x in ["already exists", "duplicate"]):
                    continue
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

def get_db():
    if DATABASE_URL:
        try:
            kwargs = _parse_db_url(DATABASE_URL)
            conn = pg8000.native.Connection(**kwargs)
            return _DB(conn)
        except Exception as e:
            print(f"PostgreSQL connection error: {e}")
            raise
    raise RuntimeError("No database configured")

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

# ── Database Schema ──────────────────────────────────────────────────────────
def init_db():
    with get_db() as db:
        db.executescript("""
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(255),
    company_id VARCHAR(50),
    role VARCHAR(20) DEFAULT 'member',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS companies (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    invite_code VARCHAR(20) UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS projects (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    company_id VARCHAR(50),
    color VARCHAR(20) DEFAULT '#3b82f6',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS documentation (
    id SERIAL PRIMARY KEY,
    project_id VARCHAR(50) REFERENCES projects(id),
    title VARCHAR(500) NOT NULL,
    content TEXT,
    diagram_data TEXT,
    doc_type VARCHAR(50) DEFAULT 'markdown',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS ai_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    project_id VARCHAR(50) REFERENCES projects(id),
    session_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
        """)
        db.commit()

# ── Auth Helpers ─────────────────────────────────────────────────────────────
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

# ── API Routes ───────────────────────────────────────────────────────────────
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()
    email, password, name = data.get("email"), data.get("password"), data.get("name")
    company_name = data.get("company_name")
    
    if not all([email, password, name]):
        return jsonify({"error": "Missing fields"}), 400
    
    with get_db() as db:
        # Check if user exists
        existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            return jsonify({"error": "Email already registered"}), 400
        
        # Create company
        company_id = f"co_{secrets.token_hex(8)}"
        invite_code = secrets.token_hex(4).upper()
        db.execute("INSERT INTO companies (id, name, invite_code) VALUES (?, ?, ?)",
                  (company_id, company_name or f"{name}'s Workspace", invite_code))
        
        # Create user
        pw_hash = hash_password(password)
        db.execute("INSERT INTO users (email, password_hash, name, company_id, role) VALUES (?, ?, ?, ?, ?)",
                  (email, pw_hash, name, company_id, 'admin'))
        db.commit()
        
        user = db.execute("SELECT id, email, name, company_id, role FROM users WHERE email=?", (email,)).fetchone()
        session['user_id'] = user['id']
        session.permanent = True
        
        return jsonify({
            "user": dict(user),
            "company": {"id": company_id, "invite_code": invite_code}
        })

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    email, password = data.get("email"), data.get("password")
    
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or user['password_hash'] != hash_password(password):
            return jsonify({"error": "Invalid credentials"}), 401
        
        session['user_id'] = user['id']
        session.permanent = True
        
        company = db.execute("SELECT * FROM companies WHERE id=?", (user['company_id'],)).fetchone()
        
        return jsonify({
            "user": {k: user[k] for k in ['id', 'email', 'name', 'company_id', 'role']},
            "company": dict(company) if company else None
        })

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route("/api/me")
@login_required
def get_me():
    with get_db() as db:
        user = db.execute("SELECT id, email, name, company_id, role FROM users WHERE id=?", 
                         (session['user_id'],)).fetchone()
        company = db.execute("SELECT * FROM companies WHERE id=?", (user['company_id'],)).fetchone()
        
        return jsonify({
            "user": dict(user),
            "company": dict(company) if company else None
        })

@app.route("/api/projects")
@login_required
def get_projects():
    with get_db() as db:
        user = db.execute("SELECT company_id FROM users WHERE id=?", (session['user_id'],)).fetchone()
        projects = db.execute("SELECT * FROM projects WHERE company_id=? ORDER BY created_at DESC", 
                             (user['company_id'],)).fetchall()
        return jsonify([dict(p) for p in projects])

@app.route("/api/projects", methods=["POST"])
@login_required
def create_project():
    data = request.get_json()
    with get_db() as db:
        user = db.execute("SELECT company_id FROM users WHERE id=?", (session['user_id'],)).fetchone()
        project_id = f"proj_{secrets.token_hex(8)}"
        
        db.execute("""INSERT INTO projects (id, name, description, company_id, color, created_by) 
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  (project_id, data.get('name'), data.get('description', ''), 
                   user['company_id'], data.get('color', '#3b82f6'), session['user_id']))
        db.commit()
        
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return jsonify(dict(project))

@app.route("/api/projects/<project_id>/docs")
@login_required
def get_docs(project_id):
    with get_db() as db:
        docs = db.execute("SELECT * FROM documentation WHERE project_id=? ORDER BY updated_at DESC", 
                         (project_id,)).fetchall()
        return jsonify([dict(d) for d in docs])

@app.route("/api/projects/<project_id>/docs", methods=["POST"])
@login_required
def create_doc(project_id):
    data = request.get_json()
    with get_db() as db:
        db.execute("""INSERT INTO documentation (project_id, title, content, diagram_data, doc_type, created_by) 
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  (project_id, data.get('title'), data.get('content'), 
                   data.get('diagram_data'), data.get('doc_type', 'markdown'), session['user_id']))
        db.commit()
        
        doc = db.execute("SELECT * FROM documentation WHERE project_id=? ORDER BY id DESC LIMIT 1", 
                        (project_id,)).fetchone()
        return jsonify(dict(doc))

@app.route("/api/ai/generate-doc", methods=["POST"])
@login_required
def ai_generate_doc():
    """AI-powered documentation generation with architectural diagrams"""
    data = request.get_json()
    prompt = data.get('prompt', '')
    project_id = data.get('project_id')
    doc_type = data.get('doc_type', 'architecture')
    
    # Simulate AI generation (replace with actual AI service)
    generated_content = generate_documentation(prompt, doc_type)
    diagram_data = generate_architecture_diagram(prompt, doc_type)
    
    return jsonify({
        "content": generated_content,
        "diagram": diagram_data,
        "suggestions": get_doc_suggestions(doc_type)
    })

def generate_documentation(prompt, doc_type):
    """Generate structured documentation based on prompt and type"""
    templates = {
        "architecture": f"""# System Architecture Documentation

## Overview
{prompt}

## Components

### Frontend Layer
- **Technology Stack**: React, TypeScript, Tailwind CSS
- **State Management**: Redux Toolkit
- **API Communication**: Axios with interceptors
- **Real-time Updates**: WebSocket connection

### Backend Layer
- **Framework**: Flask (Python 3.11+)
- **Database**: PostgreSQL with pg8000
- **Authentication**: Session-based with secure cookies
- **File Storage**: Local filesystem with CDN support

### Infrastructure
- **Deployment**: Railway.app
- **CI/CD**: GitLab pipelines
- **Monitoring**: Application logs and metrics
- **Scaling**: Horizontal pod autoscaling

## Data Flow

1. **User Authentication Flow**
   - User submits credentials
   - Backend validates and creates session
   - Session token returned to client
   - Subsequent requests include session cookie

2. **Document Generation Flow**
   - User provides documentation prompt
   - AI service processes request
   - Structured content generated
   - Architectural diagrams created
   - Results stored in database

## Security Considerations

- All passwords hashed using SHA-256
- HTTPS enforced in production
- CORS configured for cross-origin requests
- Input validation on all endpoints
- Rate limiting on API routes

## Performance Optimization

- Database queries optimized with indexes
- Static assets cached
- Lazy loading for large datasets
- WebSocket for real-time updates
- Compression enabled for responses
""",
        "api": f"""# API Documentation

## {prompt}

### Authentication

All authenticated endpoints require a valid session cookie.

```bash
POST /api/login
Content-Type: application/json

{{
  "email": "user@example.com",
  "password": "secure_password"
}}
```

### Endpoints

#### Projects

**List Projects**
```http
GET /api/projects
Authorization: Session Cookie
```

**Create Project**
```http
POST /api/projects
Content-Type: application/json

{{
  "name": "Project Name",
  "description": "Project description",
  "color": "#3b82f6"
}}
```

#### Documentation

**List Documentation**
```http
GET /api/projects/:project_id/docs
```

**Generate AI Documentation**
```http
POST /api/ai/generate-doc
Content-Type: application/json

{{
  "prompt": "System architecture for e-commerce platform",
  "project_id": "proj_xxx",
  "doc_type": "architecture"
}}
```

### Error Handling

All errors follow this format:
```json
{{
  "error": "Error message",
  "code": "ERROR_CODE",
  "details": {{}}
}}
```

### Rate Limiting

- 100 requests per minute per IP
- 1000 requests per hour per user
""",
        "deployment": f"""# Deployment Guide

## {prompt}

### Prerequisites

- Python 3.11+
- PostgreSQL database
- Git repository

### Environment Variables

```bash
DATABASE_URL=postgresql://user:password@host:port/database
SECRET_KEY=your_secret_key_here
PORT=5000
```

### Railway.app Deployment

1. **Connect Repository**
   - Link your GitLab repository to Railway
   - Railway auto-detects Python app

2. **Configure Environment**
   - Add DATABASE_URL from Railway Postgres
   - Set SECRET_KEY
   - Configure PORT (Railway provides this)

3. **Deploy**
   ```bash
   git push origin main
   # Railway auto-deploys
   ```

### Health Checks

Railway monitors:
- `/` endpoint (should return 200)
- Database connectivity
- Application logs

### Scaling

Railway automatically scales based on:
- Memory usage
- CPU utilization
- Request volume

### Monitoring

Access logs through Railway dashboard:
- Application logs
- Database metrics
- Request traces
- Error tracking
"""
    }
    
    return templates.get(doc_type, f"# {prompt}\n\nDocumentation content will be generated here.")

def generate_architecture_diagram(prompt, doc_type):
    """Generate Mermaid diagram based on documentation type"""
    diagrams = {
        "architecture": """graph TB
    subgraph Client["Client Layer"]
        UI[React UI]
        WS[WebSocket Client]
    end
    
    subgraph API["API Layer"]
        Flask[Flask Server]
        Auth[Authentication]
        Routes[API Routes]
    end
    
    subgraph Data["Data Layer"]
        PG[(PostgreSQL)]
        Cache[Session Cache]
    end
    
    subgraph AI["AI Services"]
        DocGen[Doc Generator]
        DiagramGen[Diagram Generator]
    end
    
    UI -->|HTTP/HTTPS| Flask
    UI -->|WebSocket| WS
    WS --> Flask
    Flask --> Auth
    Auth --> Routes
    Routes --> PG
    Routes --> Cache
    Routes --> DocGen
    Routes --> DiagramGen
    
    style Client fill:#e3f2fd
    style API fill:#f3e5f5
    style Data fill:#e8f5e9
    style AI fill:#fff3e0
""",
        "api": """sequenceDiagram
    participant Client
    participant API
    participant Auth
    participant DB
    participant AI
    
    Client->>API: POST /api/login
    API->>Auth: Validate credentials
    Auth->>DB: Check user
    DB-->>Auth: User data
    Auth-->>API: Session token
    API-->>Client: Success + cookie
    
    Client->>API: POST /api/ai/generate-doc
    API->>Auth: Verify session
    Auth-->>API: User verified
    API->>AI: Generate documentation
    AI-->>API: Content + diagram
    API->>DB: Store documentation
    DB-->>API: Confirmation
    API-->>Client: Generated docs
""",
        "deployment": """graph LR
    subgraph Dev["Development"]
        Code[Local Development]
        Git[Git Commit]
    end
    
    subgraph CI["CI/CD Pipeline"]
        GitLab[GitLab Repository]
        Tests[Automated Tests]
        Build[Build Process]
    end
    
    subgraph Deploy["Railway.app"]
        App[Flask Application]
        DB[(PostgreSQL)]
        CDN[Static Assets]
    end
    
    Code --> Git
    Git --> GitLab
    GitLab --> Tests
    Tests --> Build
    Build --> App
    App --> DB
    App --> CDN
    
    style Dev fill:#e3f2fd
    style CI fill:#f3e5f5
    style Deploy fill:#e8f5e9
"""
    }
    
    return diagrams.get(doc_type, "graph LR\n    A[Start] --> B[End]")

def get_doc_suggestions(doc_type):
    """Provide contextual suggestions for documentation improvement"""
    suggestions = {
        "architecture": [
            "Add security architecture section",
            "Include monitoring and observability",
            "Document disaster recovery procedures",
            "Add performance benchmarks"
        ],
        "api": [
            "Include authentication examples",
            "Add request/response samples",
            "Document error codes",
            "Include rate limiting details"
        ],
        "deployment": [
            "Add rollback procedures",
            "Include health check endpoints",
            "Document scaling strategies",
            "Add troubleshooting guide"
        ]
    }
    
    return suggestions.get(doc_type, [])

# ── HTML/CSS/JS Frontend ─────────────────────────────────────────────────────
@app.route("/")
def index():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VEWIT - AI Documentation Platform</title>
<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/htm@3/dist/htm.js"></script>
<style>
/* ── Apple-Inspired Design System ── */
:root {
  /* Colors - Light Mode */
  --bg-primary: #ffffff;
  --bg-secondary: #f5f5f7;
  --bg-tertiary: #e8e8ed;
  --glass: rgba(255, 255, 255, 0.72);
  --glass-border: rgba(255, 255, 255, 0.18);
  
  --text-primary: #1d1d1f;
  --text-secondary: #6e6e73;
  --text-tertiary: #86868b;
  
  --accent: #0071e3;
  --accent-hover: #0077ed;
  --accent-light: rgba(0, 113, 227, 0.1);
  
  --success: #34c759;
  --warning: #ff9500;
  --error: #ff3b30;
  
  --border: rgba(0, 0, 0, 0.1);
  --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.04);
  --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.08);
  --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.12);
  --shadow-xl: 0 16px 48px rgba(0, 0, 0, 0.16);
  
  /* Typography */
  --font-display: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif;
  --font-text: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'SF Mono', 'Monaco', 'Consolas', monospace;
  
  /* Spacing */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-xl: 24px;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg-primary: #000000;
    --bg-secondary: #1c1c1e;
    --bg-tertiary: #2c2c2e;
    --glass: rgba(28, 28, 30, 0.72);
    --glass-border: rgba(255, 255, 255, 0.12);
    
    --text-primary: #f5f5f7;
    --text-secondary: #a1a1a6;
    --text-tertiary: #86868b;
    
    --border: rgba(255, 255, 255, 0.1);
    --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.3);
    --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.4);
    --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.5);
    --shadow-xl: 0 16px 48px rgba(0, 0, 0, 0.6);
  }
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: var(--font-text);
  background: var(--bg-primary);
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  overflow-x: hidden;
}

/* ── Animations ── */
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes slideInLeft {
  from { opacity: 0; transform: translateX(-30px); }
  to { opacity: 1; transform: translateX(0); }
}

@keyframes slideInRight {
  from { opacity: 0; transform: translateX(30px); }
  to { opacity: 1; transform: translateX(0); }
}

@keyframes scaleIn {
  from { opacity: 0; transform: scale(0.95); }
  to { opacity: 1; transform: scale(1); }
}

@keyframes shimmer {
  0% { background-position: -1000px 0; }
  100% { background-position: 1000px 0; }
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-10px); }
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* ── Glass Morphism ── */
.glass {
  background: var(--glass);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border: 1px solid var(--glass-border);
}

/* ── Buttons ── */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px 24px;
  font-size: 14px;
  font-weight: 500;
  border-radius: var(--radius-xl);
  border: none;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  font-family: var(--font-text);
  text-decoration: none;
}

.btn-primary {
  background: var(--accent);
  color: white;
  box-shadow: 0 4px 12px rgba(0, 113, 227, 0.3);
}

.btn-primary:hover {
  background: var(--accent-hover);
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0, 113, 227, 0.4);
}

.btn-secondary {
  background: var(--bg-secondary);
  color: var(--text-primary);
}

.btn-secondary:hover {
  background: var(--bg-tertiary);
  transform: translateY(-2px);
}

.btn-ghost {
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border);
}

.btn-ghost:hover {
  background: var(--bg-secondary);
  color: var(--text-primary);
}

/* ── Input Fields ── */
.input {
  width: 100%;
  padding: 14px 16px;
  font-size: 15px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-family: var(--font-text);
  transition: all 0.2s;
}

.input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 4px var(--accent-light);
}

.input::placeholder {
  color: var(--text-tertiary);
}

/* ── Cards ── */
.card {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 24px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
}

.card-glass {
  background: var(--glass);
  backdrop-filter: saturate(180%) blur(20px);
  border: 1px solid var(--glass-border);
}

/* ── Loading States ── */
.skeleton {
  background: linear-gradient(
    90deg,
    var(--bg-secondary) 0%,
    var(--bg-tertiary) 50%,
    var(--bg-secondary) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  border-radius: var(--radius-md);
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--bg-tertiary);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ── Layout ── */
.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 24px;
}

.section {
  padding: 80px 0;
}

/* ── Navbar ── */
.navbar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 64px;
  z-index: 1000;
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  background: rgba(255, 255, 255, 0.72);
  border-bottom: 1px solid var(--border);
  transition: all 0.3s;
}

@media (prefers-color-scheme: dark) {
  .navbar {
    background: rgba(0, 0, 0, 0.72);
  }
}

.navbar-content {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.logo {
  font-size: 22px;
  font-weight: 700;
  font-family: var(--font-display);
  background: linear-gradient(135deg, var(--accent) 0%, #00c6ff 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

/* ── Hero Section ── */
.hero {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding-top: 64px;
  background: linear-gradient(180deg, var(--bg-primary) 0%, var(--bg-secondary) 100%);
  position: relative;
  overflow: hidden;
}

.hero::before {
  content: '';
  position: absolute;
  top: -50%;
  left: -50%;
  width: 200%;
  height: 200%;
  background: radial-gradient(circle, rgba(0, 113, 227, 0.1) 0%, transparent 70%);
  animation: float 20s ease-in-out infinite;
}

.hero-title {
  font-size: clamp(48px, 8vw, 80px);
  font-weight: 700;
  font-family: var(--font-display);
  line-height: 1.1;
  margin-bottom: 24px;
  animation: fadeIn 0.8s ease-out;
}

.hero-subtitle {
  font-size: clamp(18px, 3vw, 24px);
  color: var(--text-secondary);
  margin-bottom: 40px;
  animation: fadeIn 0.8s ease-out 0.2s backwards;
}

.hero-cta {
  animation: fadeIn 0.8s ease-out 0.4s backwards;
}

/* ── Feature Grid ── */
.feature-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 24px;
  margin-top: 48px;
}

.feature-card {
  padding: 32px;
  border-radius: var(--radius-xl);
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  animation: fadeIn 0.6s ease-out backwards;
}

.feature-card:nth-child(1) { animation-delay: 0.1s; }
.feature-card:nth-child(2) { animation-delay: 0.2s; }
.feature-card:nth-child(3) { animation-delay: 0.3s; }

.feature-card:hover {
  transform: translateY(-8px);
  box-shadow: var(--shadow-xl);
  border-color: var(--accent);
}

.feature-icon {
  width: 56px;
  height: 56px;
  border-radius: var(--radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  margin-bottom: 20px;
  background: linear-gradient(135deg, var(--accent-light) 0%, transparent 100%);
}

.feature-title {
  font-size: 22px;
  font-weight: 600;
  margin-bottom: 12px;
  font-family: var(--font-display);
}

.feature-description {
  font-size: 15px;
  color: var(--text-secondary);
  line-height: 1.6;
}

/* ── Dashboard Layout ── */
.dashboard {
  display: flex;
  min-height: 100vh;
  padding-top: 64px;
}

.sidebar {
  width: 280px;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  padding: 24px;
  position: fixed;
  left: 0;
  top: 64px;
  bottom: 0;
  overflow-y: auto;
}

.main-content {
  flex: 1;
  margin-left: 280px;
  padding: 40px;
  background: var(--bg-primary);
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s;
  font-size: 14px;
  font-weight: 500;
}

.nav-item:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.nav-item.active {
  background: var(--accent);
  color: white;
}

/* ── Documentation Editor ── */
.doc-editor {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  height: calc(100vh - 200px);
}

.editor-pane {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 24px;
  overflow-y: auto;
}

.editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.editor-title {
  font-size: 16px;
  font-weight: 600;
  font-family: var(--font-display);
}

.code-editor {
  width: 100%;
  min-height: 400px;
  padding: 16px;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-primary);
  color: var(--text-primary);
  resize: vertical;
}

/* ── Diagram Display ── */
.diagram-container {
  background: white;
  border-radius: var(--radius-md);
  padding: 24px;
  min-height: 300px;
}

/* ── AI Assistant Panel ── */
.ai-panel {
  position: fixed;
  right: 0;
  top: 64px;
  bottom: 0;
  width: 400px;
  background: var(--glass);
  backdrop-filter: saturate(180%) blur(20px);
  border-left: 1px solid var(--border);
  transform: translateX(100%);
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 999;
}

.ai-panel.open {
  transform: translateX(0);
}

.ai-header {
  padding: 24px;
  border-bottom: 1px solid var(--border);
}

.ai-chat {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
}

.ai-input-container {
  padding: 24px;
  border-top: 1px solid var(--border);
}

/* ── Responsive ── */
@media (max-width: 768px) {
  .sidebar {
    display: none;
  }
  
  .main-content {
    margin-left: 0;
  }
  
  .doc-editor {
    grid-template-columns: 1fr;
  }
  
  .ai-panel {
    width: 100%;
  }
}

/* ── Utility Classes ── */
.flex { display: flex; }
.flex-col { flex-direction: column; }
.items-center { align-items: center; }
.justify-center { justify-content: center; }
.justify-between { justify-content: space-between; }
.gap-2 { gap: 8px; }
.gap-4 { gap: 16px; }
.gap-6 { gap: 24px; }
.p-4 { padding: 16px; }
.p-6 { padding: 24px; }
.mb-4 { margin-bottom: 16px; }
.mb-6 { margin-bottom: 24px; }
.text-center { text-align: center; }
.font-bold { font-weight: 700; }
.font-semibold { font-weight: 600; }
.text-sm { font-size: 13px; }
.text-base { font-size: 15px; }
.text-lg { font-size: 17px; }
.text-xl { font-size: 20px; }
.text-2xl { font-size: 24px; }
.text-3xl { font-size: 30px; }
.text-4xl { font-size: 36px; }
.rounded { border-radius: var(--radius-md); }
.rounded-lg { border-radius: var(--radius-lg); }
.shadow { box-shadow: var(--shadow-md); }
.shadow-lg { box-shadow: var(--shadow-lg); }

/* ── Status Badges ── */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  border-radius: 100px;
  font-size: 12px;
  font-weight: 600;
}

.badge-success {
  background: rgba(52, 199, 89, 0.1);
  color: var(--success);
}

.badge-warning {
  background: rgba(255, 149, 0, 0.1);
  color: var(--warning);
}

.badge-error {
  background: rgba(255, 59, 48, 0.1);
  color: var(--error);
}

/* ── Tooltips ── */
.tooltip {
  position: relative;
}

.tooltip::after {
  content: attr(data-tooltip);
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  padding: 8px 12px;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border-radius: var(--radius-md);
  font-size: 12px;
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s;
  margin-bottom: 8px;
}

.tooltip:hover::after {
  opacity: 1;
}

/* ── Modal ── */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
  animation: fadeIn 0.2s;
}

.modal {
  background: var(--bg-primary);
  border-radius: var(--radius-xl);
  padding: 32px;
  max-width: 600px;
  width: 90%;
  max-height: 90vh;
  overflow-y: auto;
  box-shadow: var(--shadow-xl);
  animation: scaleIn 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.modal-header {
  font-size: 24px;
  font-weight: 700;
  margin-bottom: 24px;
  font-family: var(--font-display);
}

/* ── Progress Bar ── */
.progress-bar {
  height: 4px;
  background: var(--bg-tertiary);
  border-radius: 100px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent) 0%, #00c6ff 100%);
  transition: width 0.3s ease;
}

/* ── Tabs ── */
.tabs {
  display: flex;
  gap: 4px;
  background: var(--bg-secondary);
  padding: 4px;
  border-radius: var(--radius-lg);
}

.tab {
  flex: 1;
  padding: 10px 16px;
  text-align: center;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  transition: all 0.2s;
}

.tab:hover {
  color: var(--text-primary);
}

.tab.active {
  background: var(--bg-primary);
  color: var(--text-primary);
  box-shadow: var(--shadow-sm);
}
</style>
</head>
<body>
<div id="root"></div>

<script type="module">
const { useState, useEffect, useRef } = React;
const html = htm.bind(React.createElement);

// ── API Client ──────────────────────────────────────────────────────────────
const API = {
  async fetch(endpoint, options = {}) {
    const res = await fetch(`/api${endpoint}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers
      },
      credentials: 'include'
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Request failed');
    return data;
  },
  
  auth: {
    register: (data) => API.fetch('/register', { method: 'POST', body: JSON.stringify(data) }),
    login: (data) => API.fetch('/login', { method: 'POST', body: JSON.stringify(data) }),
    logout: () => API.fetch('/logout', { method: 'POST' }),
    me: () => API.fetch('/me')
  },
  
  projects: {
    list: () => API.fetch('/projects'),
    create: (data) => API.fetch('/projects', { method: 'POST', body: JSON.stringify(data) })
  },
  
  docs: {
    list: (projectId) => API.fetch(`/projects/${projectId}/docs`),
    create: (projectId, data) => API.fetch(`/projects/${projectId}/docs`, { 
      method: 'POST', 
      body: JSON.stringify(data) 
    })
  },
  
  ai: {
    generateDoc: (data) => API.fetch('/ai/generate-doc', { 
      method: 'POST', 
      body: JSON.stringify(data) 
    })
  }
};

// ── Components ──────────────────────────────────────────────────────────────

// Navbar Component
const Navbar = ({ user, onLogout, onShowAI }) => {
  return html`
    <nav className="navbar">
      <div className="container navbar-content">
        <div className="logo">VEWIT</div>
        ${user ? html`
          <div className="flex items-center gap-4">
            <button className="btn btn-ghost" onClick=${onShowAI}>
              <span>✨</span> AI Assistant
            </button>
            <div className="flex items-center gap-3">
              <span className="text-sm text-secondary">${user.name}</span>
              <button className="btn btn-secondary" onClick=${onLogout}>Logout</button>
            </div>
          </div>
        ` : html`
          <div className="flex gap-3">
            <a href="#features" className="btn btn-ghost">Features</a>
            <a href="#docs" className="btn btn-ghost">Documentation</a>
          </div>
        `}
      </div>
    </nav>
  `;
};

// Hero Section
const Hero = ({ onGetStarted }) => {
  return html`
    <section className="hero">
      <div className="container">
        <h1 className="hero-title">
          AI-Powered<br/>
          Documentation Platform
        </h1>
        <p className="hero-subtitle">
          Generate comprehensive documentation and architectural diagrams<br/>
          with the power of artificial intelligence
        </p>
        <div className="hero-cta flex justify-center gap-4">
          <button className="btn btn-primary" onClick=${onGetStarted}>
            Get Started → 
          </button>
          <button className="btn btn-ghost">
            Watch Demo
          </button>
        </div>
      </div>
    </section>
  `;
};

// Features Section
const Features = () => {
  const features = [
    {
      icon: '🤖',
      title: 'AI Documentation Generator',
      description: 'Transform your ideas into comprehensive, structured documentation with AI assistance'
    },
    {
      icon: '📊',
      title: 'Architecture Diagrams',
      description: 'Automatically generate system architecture, sequence, and flow diagrams'
    },
    {
      icon: '⚡',
      title: 'Real-time Collaboration',
      description: 'Work together with your team in real-time on documentation projects'
    },
    {
      icon: '🎨',
      title: 'Beautiful Templates',
      description: 'Choose from pre-designed templates or create your own custom styles'
    },
    {
      icon: '🔒',
      title: 'Secure & Private',
      description: 'Enterprise-grade security with team-based access controls'
    },
    {
      icon: '🚀',
      title: 'Deploy Anywhere',
      description: 'Export to Markdown, PDF, or deploy directly to your platform'
    }
  ];
  
  return html`
    <section className="section" style=${{ background: 'var(--bg-secondary)' }}>
      <div className="container">
        <h2 className="text-4xl font-bold text-center mb-6">
          Everything you need to document better
        </h2>
        <p className="text-lg text-center text-secondary mb-6">
          Powerful features designed for modern development teams
        </p>
        <div className="feature-grid">
          ${features.map((feature, i) => html`
            <div key=${i} className="feature-card">
              <div className="feature-icon">${feature.icon}</div>
              <h3 className="feature-title">${feature.title}</h3>
              <p className="feature-description">${feature.description}</p>
            </div>
          `)}
        </div>
      </div>
    </section>
  `;
};

// Auth Modal
const AuthModal = ({ onClose, onSuccess }) => {
  const [isLogin, setIsLogin] = useState(true);
  const [formData, setFormData] = useState({
    email: '',
    password: '',
    name: '',
    company_name: ''
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  
  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    
    try {
      const result = isLogin 
        ? await API.auth.login(formData)
        : await API.auth.register(formData);
      onSuccess(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
  
  return html`
    <div className="modal-overlay" onClick=${onClose}>
      <div className="modal" onClick=${(e) => e.stopPropagation()}>
        <div className="modal-header">
          ${isLogin ? 'Welcome back' : 'Create your account'}
        </div>
        
        ${error && html`
          <div className="badge badge-error mb-4">${error}</div>
        `}
        
        <form onSubmit=${handleSubmit} className="flex flex-col gap-4">
          ${!isLogin && html`
            <input
              className="input"
              type="text"
              placeholder="Full name"
              value=${formData.name}
              onChange=${(e) => setFormData({...formData, name: e.target.value})}
              required
            />
          `}
          
          <input
            className="input"
            type="email"
            placeholder="Email address"
            value=${formData.email}
            onChange=${(e) => setFormData({...formData, email: e.target.value})}
            required
          />
          
          <input
            className="input"
            type="password"
            placeholder="Password"
            value=${formData.password}
            onChange=${(e) => setFormData({...formData, password: e.target.value})}
            required
          />
          
          ${!isLogin && html`
            <input
              className="input"
              type="text"
              placeholder="Workspace name"
              value=${formData.company_name}
              onChange=${(e) => setFormData({...formData, company_name: e.target.value})}
              required
            />
          `}
          
          <button type="submit" className="btn btn-primary" disabled=${loading}>
            ${loading ? 'Processing...' : (isLogin ? 'Sign in' : 'Create account')}
          </button>
        </form>
        
        <div className="text-center mt-6 text-sm text-secondary">
          ${isLogin ? "Don't have an account?" : 'Already have an account?'}
          <button 
            className="btn btn-ghost" 
            style=${{ padding: '4px 8px', marginLeft: '8px' }}
            onClick=${() => setIsLogin(!isLogin)}
          >
            ${isLogin ? 'Sign up' : 'Sign in'}
          </button>
        </div>
      </div>
    </div>
  `;
};

// Dashboard Sidebar
const Sidebar = ({ activeView, setActiveView, projects, onNewProject }) => {
  const navItems = [
    { id: 'overview', label: 'Overview', icon: '📊' },
    { id: 'projects', label: 'Projects', icon: '📁' },
    { id: 'ai-docs', label: 'AI Documentation', icon: '🤖' },
    { id: 'diagrams', label: 'Diagrams', icon: '📈' },
    { id: 'settings', label: 'Settings', icon: '⚙️' }
  ];
  
  return html`
    <div className="sidebar">
      <div className="mb-6">
        <button className="btn btn-primary" style=${{ width: '100%' }} onClick=${onNewProject}>
          <span>+</span> New Project
        </button>
      </div>
      
      <div className="sidebar-nav">
        ${navItems.map(item => html`
          <div
            key=${item.id}
            className=${`nav-item ${activeView === item.id ? 'active' : ''}`}
            onClick=${() => setActiveView(item.id)}
          >
            <span>${item.icon}</span>
            <span>${item.label}</span>
          </div>
        `)}
      </div>
      
      ${projects && projects.length > 0 && html`
        <div style=${{ marginTop: '32px' }}>
          <div className="text-sm font-semibold text-tertiary mb-3" style=${{ padding: '0 16px' }}>
            Recent Projects
          </div>
          ${projects.slice(0, 3).map(p => html`
            <div key=${p.id} className="nav-item">
              <div style=${{ 
                width: '8px', 
                height: '8px', 
                borderRadius: '50%', 
                background: p.color 
              }}></div>
              <span>${p.name}</span>
            </div>
          `)}
        </div>
      `}
    </div>
  `;
};

// AI Documentation Generator
const AIDocGenerator = ({ projectId }) => {
  const [prompt, setPrompt] = useState('');
  const [docType, setDocType] = useState('architecture');
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState(null);
  const [diagramCode, setDiagramCode] = useState('');
  
  const handleGenerate = async () => {
    if (!prompt.trim()) return;
    
    setGenerating(true);
    try {
      const data = await API.ai.generateDoc({
        prompt,
        project_id: projectId,
        doc_type: docType
      });
      setResult(data);
      setDiagramCode(data.diagram);
    } catch (err) {
      alert('Error generating documentation: ' + err.message);
    } finally {
      setGenerating(false);
    }
  };
  
  useEffect(() => {
    if (diagramCode && window.mermaid) {
      window.mermaid.initialize({ startOnLoad: true, theme: 'default' });
      window.mermaid.contentLoaded();
    }
  }, [diagramCode]);
  
  return html`
    <div className="p-6">
      <div className="mb-6">
        <h2 className="text-3xl font-bold mb-3">AI Documentation Generator</h2>
        <p className="text-secondary">
          Describe your system or project, and AI will generate comprehensive documentation
          with architectural diagrams
        </p>
      </div>
      
      <div className="tabs mb-6">
        ${['architecture', 'api', 'deployment'].map(type => html`
          <div
            key=${type}
            className=${`tab ${docType === type ? 'active' : ''}`}
            onClick=${() => setDocType(type)}
          >
            ${type.charAt(0).toUpperCase() + type.slice(1)}
          </div>
        `)}
      </div>
      
      <div className="card mb-6">
        <textarea
          className="code-editor"
          placeholder="Describe your system... (e.g., 'E-commerce platform with microservices architecture')"
          value=${prompt}
          onChange=${(e) => setPrompt(e.target.value)}
          style=${{ minHeight: '120px', marginBottom: '16px' }}
        />
        
        <button 
          className="btn btn-primary"
          onClick=${handleGenerate}
          disabled=${generating || !prompt.trim()}
        >
          ${generating ? html`
            <span className="spinner"></span> Generating...
          ` : html`
            <span>✨</span> Generate Documentation
          `}
        </button>
      </div>
      
      ${result && html`
        <div className="doc-editor">
          <div className="editor-pane">
            <div className="editor-header">
              <div className="editor-title">📝 Generated Documentation</div>
              <button className="btn btn-ghost" style=${{ padding: '6px 12px' }}>
                Copy
              </button>
            </div>
            <div style=${{ 
              whiteSpace: 'pre-wrap', 
              fontFamily: 'var(--font-mono)', 
              fontSize: '13px',
              lineHeight: '1.6'
            }}>
              ${result.content}
            </div>
          </div>
          
          <div className="editor-pane">
            <div className="editor-header">
              <div className="editor-title">📊 Architecture Diagram</div>
              <button className="btn btn-ghost" style=${{ padding: '6px 12px' }}>
                Download
              </button>
            </div>
            <div className="diagram-container">
              <div className="mermaid">
                ${diagramCode}
              </div>
            </div>
            
            ${result.suggestions && html`
              <div style=${{ marginTop: '24px' }}>
                <div className="text-sm font-semibold mb-3">💡 Suggestions</div>
                ${result.suggestions.map((s, i) => html`
                  <div key=${i} className="badge badge-success" style=${{ 
                    marginRight: '8px', 
                    marginBottom: '8px' 
                  }}>
                    ${s}
                  </div>
                `)}
              </div>
            `}
          </div>
        </div>
      `}
    </div>
  `;
};

// Projects View
const ProjectsView = ({ projects, onSelectProject }) => {
  return html`
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-3xl font-bold mb-2">Projects</h2>
          <p className="text-secondary">Manage your documentation projects</p>
        </div>
      </div>
      
      <div className="feature-grid">
        ${projects.map(p => html`
          <div 
            key=${p.id} 
            className="card"
            style=${{ cursor: 'pointer' }}
            onClick=${() => onSelectProject(p)}
          >
            <div className="flex items-center gap-3 mb-4">
              <div style=${{ 
                width: '48px', 
                height: '48px', 
                borderRadius: 'var(--radius-md)',
                background: p.color + '20',
                border: '2px solid ' + p.color,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '20px'
              }}>
                📁
              </div>
              <div>
                <div className="font-semibold text-lg">${p.name}</div>
                <div className="text-sm text-tertiary">
                  ${new Date(p.created_at).toLocaleDateString()}
                </div>
              </div>
            </div>
            ${p.description && html`
              <p className="text-sm text-secondary">${p.description}</p>
            `}
          </div>
        `)}
      </div>
    </div>
  `;
};

// Main Dashboard
const Dashboard = ({ user, company, onLogout }) => {
  const [activeView, setActiveView] = useState('ai-docs');
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState(null);
  const [showNewProject, setShowNewProject] = useState(false);
  const [showAI, setShowAI] = useState(false);
  
  useEffect(() => {
    loadProjects();
  }, []);
  
  const loadProjects = async () => {
    try {
      const data = await API.projects.list();
      setProjects(data);
    } catch (err) {
      console.error('Failed to load projects:', err);
    }
  };
  
  const handleNewProject = async (projectData) => {
    try {
      await API.projects.create(projectData);
      await loadProjects();
      setShowNewProject(false);
    } catch (err) {
      alert('Failed to create project: ' + err.message);
    }
  };
  
  const renderView = () => {
    switch(activeView) {
      case 'projects':
        return html`<${ProjectsView} projects=${projects} onSelectProject=${setSelectedProject} />`;
      case 'ai-docs':
        return html`<${AIDocGenerator} projectId=${selectedProject?.id} />`;
      default:
        return html`
          <div className="p-6">
            <h2 className="text-3xl font-bold mb-6">Overview</h2>
            <div className="feature-grid">
              <div className="card">
                <div className="text-4xl mb-2">${projects.length}</div>
                <div className="text-secondary">Active Projects</div>
              </div>
              <div className="card">
                <div className="text-4xl mb-2">24</div>
                <div className="text-secondary">Documents Generated</div>
              </div>
              <div className="card">
                <div className="text-4xl mb-2">12</div>
                <div className="text-secondary">Diagrams Created</div>
              </div>
            </div>
          </div>
        `;
    }
  };
  
  return html`
    <div>
      <${Navbar} user=${user} onLogout=${onLogout} onShowAI=${() => setShowAI(!showAI)} />
      
      <div className="dashboard">
        <${Sidebar} 
          activeView=${activeView}
          setActiveView=${setActiveView}
          projects=${projects}
          onNewProject=${() => setShowNewProject(true)}
        />
        
        <div className="main-content">
          ${renderView()}
        </div>
      </div>
      
      ${showNewProject && html`
        <${NewProjectModal} 
          onClose=${() => setShowNewProject(false)}
          onSubmit=${handleNewProject}
        />
      `}
    </div>
  `;
};

// New Project Modal
const NewProjectModal = ({ onClose, onSubmit }) => {
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    color: '#3b82f6'
  });
  
  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit(formData);
  };
  
  const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];
  
  return html`
    <div className="modal-overlay" onClick=${onClose}>
      <div className="modal" onClick=${(e) => e.stopPropagation()}>
        <div className="modal-header">Create New Project</div>
        
        <form onSubmit=${handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="text-sm text-secondary mb-2" style=${{ display: 'block' }}>
              Project Name
            </label>
            <input
              className="input"
              type="text"
              placeholder="My Awesome Project"
              value=${formData.name}
              onChange=${(e) => setFormData({...formData, name: e.target.value})}
              required
            />
          </div>
          
          <div>
            <label className="text-sm text-secondary mb-2" style=${{ display: 'block' }}>
              Description
            </label>
            <textarea
              className="input"
              placeholder="What is this project about?"
              value=${formData.description}
              onChange=${(e) => setFormData({...formData, description: e.target.value})}
              style=${{ minHeight: '80px' }}
            />
          </div>
          
          <div>
            <label className="text-sm text-secondary mb-2" style=${{ display: 'block' }}>
              Color
            </label>
            <div className="flex gap-2">
              ${colors.map(color => html`
                <div
                  key=${color}
                  style=${{
                    width: '40px',
                    height: '40px',
                    borderRadius: 'var(--radius-md)',
                    background: color,
                    cursor: 'pointer',
                    border: formData.color === color ? '3px solid var(--text-primary)' : '2px solid var(--border)',
                    transition: 'all 0.2s'
                  }}
                  onClick=${() => setFormData({...formData, color})}
                />
              `)}
            </div>
          </div>
          
          <div className="flex gap-3" style=${{ marginTop: '16px' }}>
            <button type="submit" className="btn btn-primary" style=${{ flex: 1 }}>
              Create Project
            </button>
            <button type="button" className="btn btn-secondary" onClick=${onClose}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  `;
};

// Main App
const App = () => {
  const [user, setUser] = useState(null);
  const [company, setCompany] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showAuth, setShowAuth] = useState(false);
  
  useEffect(() => {
    checkAuth();
    loadMermaid();
  }, []);
  
  const loadMermaid = () => {
    if (!window.mermaid) {
      const script = document.createElement('script');
      script.src = 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js';
      script.onload = () => {
        window.mermaid.initialize({ startOnLoad: true, theme: 'default' });
      };
      document.body.appendChild(script);
    }
  };
  
  const checkAuth = async () => {
    try {
      const data = await API.auth.me();
      setUser(data.user);
      setCompany(data.company);
    } catch (err) {
      // Not logged in
    } finally {
      setLoading(false);
    }
  };
  
  const handleAuthSuccess = (data) => {
    setUser(data.user);
    setCompany(data.company);
    setShowAuth(false);
  };
  
  const handleLogout = async () => {
    try {
      await API.auth.logout();
      setUser(null);
      setCompany(null);
    } catch (err) {
      console.error('Logout failed:', err);
    }
  };
  
  if (loading) {
    return html`
      <div style=${{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh'
      }}>
        <div className="spinner" style=${{ width: '40px', height: '40px' }}></div>
      </div>
    `;
  }
  
  if (user) {
    return html`<${Dashboard} user=${user} company=${company} onLogout=${handleLogout} />`;
  }
  
  return html`
    <div>
      <${Navbar} user=${null} />
      <${Hero} onGetStarted=${() => setShowAuth(true)} />
      <${Features} />
      
      ${showAuth && html`
        <${AuthModal} 
          onClose=${() => setShowAuth(false)}
          onSuccess=${handleAuthSuccess}
        />
      `}
    </div>
  `;
};

// Render
ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`);
</script>
</body>
</html>"""

# ── Server Entry Point ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n⚡ VEWIT v5.0 — Premium AI Documentation Platform")
    print("="*60)
    print("  Initializing database...")
    init_db()
    
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  ✓ Running at http://0.0.0.0:{port}")
    print(f"  ✓ Database: PostgreSQL")
    print(f"  ✓ Railway.app ready")
    print(f"\n  Create your account to get started!\n")
    
    app.run(host="0.0.0.0", port=port, debug=False)
