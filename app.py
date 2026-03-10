#!/usr/bin/env python3
"""
ProjectFlow v4.0
Multi-tenant workspaces | AI Assistant | Stage Dropdown | Direct Messages
"""
import os, sys, json, hashlib, sqlite3, secrets, random, urllib.request, urllib.error
import socket, threading, time, webbrowser, mimetypes, base64
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, session, Response, send_file
from flask_cors import CORS

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB         = os.path.join(BASE_DIR, "projectflow.db")
JS_DIR     = os.path.join(BASE_DIR, "pf_static")
UPLOAD_DIR = os.path.join(BASE_DIR, "pf_uploads")
KEY_FILE   = os.path.join(BASE_DIR, ".pf_secret")

def get_secret_key():
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

CLRS=["#7c3aed","#2563eb","#059669","#d97706","#dc2626","#ec4899","#0891b2","#c8f135"]

def get_db():
    c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c
def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()
def ts(): return datetime.utcnow().isoformat() + 'Z'

# ── DB Init & Migration ───────────────────────────────────────────────────────
def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY, name TEXT, invite_code TEXT,
                owner_id TEXT, ai_api_key TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, email TEXT,
                password TEXT, role TEXT, avatar TEXT, color TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, description TEXT,
                owner TEXT, members TEXT DEFAULT '[]', start_date TEXT,
                target_date TEXT, progress INTEGER DEFAULT 0, color TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, workspace_id TEXT, title TEXT, description TEXT,
                project TEXT, assignee TEXT, priority TEXT, stage TEXT,
                created TEXT, due TEXT, pct INTEGER DEFAULT 0, comments TEXT DEFAULT '[]');
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
            CREATE TABLE IF NOT EXISTS call_signals (
                id TEXT PRIMARY KEY, workspace_id TEXT, room_id TEXT,
                from_user TEXT, to_user TEXT, type TEXT, data TEXT,
                consumed INTEGER DEFAULT 0, created TEXT);
        """)
        # Add is_system column to messages if not exists (migration)
        try: db.execute("ALTER TABLE messages ADD COLUMN is_system INTEGER DEFAULT 0")
        except: pass
        # Add avatar_data column for profile photos
        try: db.execute("ALTER TABLE users ADD COLUMN avatar_data TEXT")
        except: pass
        # Fix corrupted avatar column: if avatar contains base64 image data, move it to avatar_data and reset avatar to initials
        try:
            corrupted = db.execute("SELECT id, name, avatar FROM users WHERE avatar LIKE 'data:image%' OR (length(avatar) > 10 AND avatar NOT GLOB '[A-Z][A-Z]*')").fetchall()
            for row in corrupted:
                uid, name, av = row['id'], row['name'] or '', row['avatar'] or ''
                initials = ''.join(w[0] for w in name.split() if w)[:2].upper() or '?'
                if av.startswith('data:image'):
                    db.execute("UPDATE users SET avatar=?, avatar_data=? WHERE id=?", (initials, av, uid))
                else:
                    db.execute("UPDATE users SET avatar=? WHERE id=?", (initials, uid))
        except Exception as e:
            print(f"Avatar cleanup migration error: {e}")
        # Migrate legacy data (no workspace_id)
        existing_ws = db.execute("SELECT id FROM workspaces LIMIT 1").fetchone()
        if not existing_ws:
            # Check if legacy users exist (without workspace_id)
            legacy_users = db.execute("SELECT id FROM users WHERE workspace_id IS NULL LIMIT 1").fetchone()
            ws_id = f"ws{int(datetime.now().timestamp()*1000)}"
            invite = secrets.token_hex(4).upper()
            db.execute("INSERT OR IGNORE INTO workspaces VALUES (?,?,?,?,?,?)",
                       (ws_id,"Demo Workspace",invite,"u1",None,ts()))
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
        try: db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?)",(u[0],ws_id,*u[1:],ts()))
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
    with get_db() as db:
        u=db.execute("SELECT * FROM users WHERE email=? AND password=?",
                     (d.get("email",""),hash_pw(d.get("password","")))).fetchone()
        if not u: return jsonify({"error":"Invalid email or password"}),401
        session.permanent=True
        session["user_id"]=u["id"]
        session["workspace_id"]=u["workspace_id"]
        return jsonify(dict(u))

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
            db.execute("INSERT INTO workspaces VALUES (?,?,?,?,?,?)",
                       (ws_id,d["workspace_name"],invite,uid,None,ts()))
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
            db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?)",
                       (uid,ws_id,d["name"],d["email"],hash_pw(d["password"]),
                        d.get("role","Developer"),av,c,ts()))
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
        ws=db.execute("SELECT * FROM workspaces WHERE id=?",(wid(),)).fetchone()
        return jsonify(dict(ws))

@app.route("/api/workspace/new-invite",methods=["POST"])
@login_required
def new_invite():
    invite=secrets.token_hex(4).upper()
    with get_db() as db:
        db.execute("UPDATE workspaces SET invite_code=? WHERE id=?",(invite,wid()))
        return jsonify({"invite_code":invite})

# ── Users ─────────────────────────────────────────────────────────────────────
@app.route("/api/users")
@login_required
def get_users():
    with get_db() as db:
        rows = db.execute("SELECT * FROM users WHERE workspace_id=? ORDER BY name",(wid(),)).fetchall()
        # Strip avatar_data from list response - it's served separately via /api/auth/me and PUT response
        users = []
        for r in rows:
            u = dict(r)
            u.pop('avatar_data', None)
            u.pop('password', None)
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
            db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?)",
                       (uid,wid(),d["name"],d["email"],hash_pw(d["password"]),
                        d.get("role","Developer"),av,c,ts()))
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
        if "avatar_data" in d: db.execute("UPDATE users SET avatar_data=? WHERE id=? AND workspace_id=?",(d["avatar_data"],uid,wid()))
        u=db.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        return jsonify(dict(u) if u else {})

@app.route("/api/users/<uid>",methods=["DELETE"])
@login_required
def del_user(uid):
    with get_db() as db:
        db.execute("DELETE FROM users WHERE id=? AND workspace_id=?",(uid,wid()))
        return jsonify({"ok":True})

# ── Projects ──────────────────────────────────────────────────────────────────
@app.route("/api/projects")
@login_required
def get_projects():
    with get_db() as db:
        # Admins and project creators see all their projects; others see only member projects
        user = db.execute("SELECT role FROM users WHERE id=?", (session["user_id"],)).fetchone()
        is_admin = user and user["role"] == "Admin"
        uid = session["user_id"]
        all_rows = db.execute(
            "SELECT * FROM projects WHERE workspace_id=? ORDER BY created DESC", (wid(),)).fetchall()
        if is_admin:
            rows = all_rows
        else:
            def can_see(r):
                members = json.loads(r["members"] or "[]")
                # Column is "owner" in schema (not created_by)
                owner = r["owner"] if "owner" in r.keys() else None
                return uid in members or owner == uid
            rows = [r for r in all_rows if can_see(r)]
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
        db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (pid,wid(),d["name"],d.get("description",""),session["user_id"],
                    json.dumps(members),d.get("startDate",""),d.get("targetDate",""),0,
                    d.get("color","#c8f135"),ts()))
        p=db.execute("SELECT * FROM projects WHERE id=?",(pid,)).fetchone()
        # Notify all members except creator
        for uid in members:
            if uid != session["user_id"]:
                nid=f"n{int(datetime.now().timestamp()*1000)}"
                db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                           (nid,wid(),"project_added",f"You were added to project '{d['name']}'",uid,0,ts()))
        return jsonify(dict(p))

@app.route("/api/projects/<pid>",methods=["PUT"])
@login_required
def update_project(pid):
    d=request.json or {}
    with get_db() as db:
        p=db.execute("SELECT * FROM projects WHERE id=? AND workspace_id=?",(pid,wid())).fetchone()
        if not p: return jsonify({"error":"Not found"}),404
        db.execute("""UPDATE projects SET name=?,description=?,target_date=?,color=?,members=?
                      WHERE id=? AND workspace_id=?""",
                   (d.get("name",p["name"]),d.get("description",p["description"]),
                    d.get("target_date",p["target_date"]),d.get("color",p["color"]),
                    json.dumps(d.get("members",json.loads(p["members"]))),pid,wid()))
        return jsonify(dict(db.execute("SELECT * FROM projects WHERE id=?",(pid,)).fetchone()))

@app.route("/api/projects/<pid>",methods=["DELETE"])
@login_required
def del_project(pid):
    with get_db() as db:
        db.execute("DELETE FROM projects WHERE id=? AND workspace_id=?",(pid,wid()))
        db.execute("DELETE FROM tasks WHERE project=? AND workspace_id=?",(pid,wid()))
        db.execute("DELETE FROM files WHERE project_id=? AND workspace_id=?",(pid,wid()))
        return jsonify({"ok":True})

# ── Tasks ─────────────────────────────────────────────────────────────────────
@app.route("/api/tasks")
@login_required
def get_tasks():
    with get_db() as db:
        return jsonify([dict(r) for r in db.execute(
            "SELECT * FROM tasks WHERE workspace_id=? ORDER BY created DESC",(wid(),)).fetchall()])

def next_task_id(db, ws):
    # Use timestamp-based ID to prevent collisions between gunicorn workers
    import time
    base = int(time.time() * 1000)
    # Also embed a sequential number for readability
    count=db.execute("SELECT COUNT(*) FROM tasks WHERE workspace_id=?",(ws,)).fetchone()[0]
    return f"T-{count+1:03d}-{base % 10000}"

@app.route("/api/tasks",methods=["POST"])
@login_required
def create_task():
    d=request.json or {}
    if not d.get("title"): return jsonify({"error":"Title required"}),400
    with get_db() as db:
        tid=next_task_id(db,wid())
        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (tid,wid(),d["title"],d.get("description",""),d.get("project",""),
                    d.get("assignee",""),d.get("priority","medium"),d.get("stage","backlog"),
                    ts(),d.get("due",""),d.get("pct",0),json.dumps(d.get("comments",[]))))
        # Notify assignee
        if d.get("assignee") and d["assignee"]!=session["user_id"]:
            nid=f"n{int(datetime.now().timestamp()*1000)}"
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"task_assigned",f"You were assigned to '{d['title']}'",d["assignee"],0,ts()))
        t=db.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone()
        # Auto-post system message to project channel
        if d.get("project"):
            assignee_name=""
            if d.get("assignee"):
                au=db.execute("SELECT name FROM users WHERE id=?",(d["assignee"],)).fetchone()
                if au: assignee_name=f" → assigned to {au['name']}"
            creator=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
            cname=(creator["name"] if creator else "Someone")
            sysmid=f"m{int(datetime.now().timestamp()*1000)+1}"
            msg=f"📋 **{cname}** created task **{d['title']}**{assignee_name} [{d.get('priority','medium').title()}]"
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                       (sysmid,wid(),"system",d["project"],msg,ts(),1))
        return jsonify(dict(t))

@app.route("/api/tasks/<tid>",methods=["PUT"])
@login_required
def update_task(tid):
    d=request.json or {}
    with get_db() as db:
        t=db.execute("SELECT * FROM tasks WHERE id=? AND workspace_id=?",(tid,wid())).fetchone()
        if not t: return jsonify({"error":"Not found"}),404
        old_stage=t["stage"]
        db.execute("""UPDATE tasks SET title=?,description=?,project=?,assignee=?,
                      priority=?,stage=?,due=?,pct=?,comments=? WHERE id=? AND workspace_id=?""",
                   (d.get("title",t["title"]),d.get("description",t["description"]),
                    d.get("project",t["project"]),d.get("assignee",t["assignee"]),
                    d.get("priority",t["priority"]),d.get("stage",t["stage"]),
                    d.get("due",t["due"]),d.get("pct",t["pct"]),
                    json.dumps(d.get("comments",json.loads(t["comments"]))),tid,wid()))
        if d.get("stage") and d["stage"]!=old_stage:
            nid=f"n{int(datetime.now().timestamp()*1000)}"
            db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                       (nid,wid(),"status_change",f"Task '{t['title']}' moved to {d['stage']}",
                        t["assignee"],0,ts()))
            # Post stage change to channel
            if t["project"]:
                actor=db.execute("SELECT name FROM users WHERE id=?",(session["user_id"],)).fetchone()
                aname=actor["name"] if actor else "Someone"
                sysmid=f"m{int(datetime.now().timestamp()*1000)+2}"
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
        return jsonify(dict(db.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone()))

@app.route("/api/tasks/<tid>",methods=["DELETE"])
@login_required
def del_task(tid):
    with get_db() as db:
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
    with get_db() as db:
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
        return jsonify(dict(db.execute("SELECT * FROM reminders WHERE id=?",(rid,)).fetchone()))

@app.route("/api/reminders/<rid>", methods=["DELETE"])
@login_required
def delete_reminder(rid):
    with get_db() as db:
        db.execute("DELETE FROM reminders WHERE id=? AND user_id=?",(rid,session["user_id"]))
        return jsonify({"ok":True})

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
<action>{{"type":"create_project","name":"Project Name","description":"desc","color":"#c8f135","members":["user_id"]}}</action>
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
                                act.get("color","#c8f135"),ts()))
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

# ── Serve ─────────────────────────────────────────────────────────────────────
@app.route("/js/<path:fn>")
def serve_js(fn):
    path=os.path.join(JS_DIR,fn)
    if not os.path.exists(path): return "Not Found",404
    mime,_=mimetypes.guess_type(fn)
    return Response(open(path,"rb").read(),mimetype=mime or "application/javascript",
                    headers={"Cache-Control":"public,max-age=86400"})

@app.route("/",defaults={"p":""})
@app.route("/<path:p>")
def root(p): return HTML

HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>ProjectFlow</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%236366f1'/%3E%3Crect x='2' y='2' width='28' height='28' rx='6' fill='url(%23g)'/%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%236366f1'/%3E%3Cstop offset='1' stop-color='%23a78bfa'/%3E%3C/linearGradient%3E%3C/defs%3E%3Ccircle cx='16' cy='12' r='4' fill='white'/%3E%3Ccircle cx='8' cy='22' r='3' fill='white' opacity='.85'/%3E%3Ccircle cx='24' cy='22' r='3' fill='white' opacity='.85'/%3E%3Cline x1='16' y1='16' x2='10' y2='20' stroke='white' stroke-width='2.2' stroke-linecap='round'/%3E%3Cline x1='16' y1='16' x2='22' y2='20' stroke='white' stroke-width='2.2' stroke-linecap='round'/%3E%3C/svg%3E"/>
<script>
(function(){
  var svg="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%236366f1'/%3E%3Crect x='2' y='2' width='28' height='28' rx='6' fill='url(%23g)'/%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%236366f1'/%3E%3Cstop offset='1' stop-color='%23a78bfa'/%3E%3C/linearGradient%3E%3C/defs%3E%3Ccircle cx='16' cy='12' r='4' fill='white'/%3E%3Ccircle cx='8' cy='22' r='3' fill='white' opacity='.85'/%3E%3Ccircle cx='24' cy='22' r='3' fill='white' opacity='.85'/%3E%3Cline x1='16' y1='16' x2='10' y2='20' stroke='white' stroke-width='2.2' stroke-linecap='round'/%3E%3Cline x1='16' y1='16' x2='22' y2='20' stroke='white' stroke-width='2.2' stroke-linecap='round'/%3E%3C/svg%3E";
  Array.from(document.querySelectorAll("link[rel*=icon]")).forEach(function(el){el.parentNode.removeChild(el);});
  var l=document.createElement('link');l.rel='icon';l.type='image/svg+xml';l.href=svg;
  document.head.appendChild(l);
})();
</script>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700;800&family=Syne:wght@600;700;800&display=swap" rel="stylesheet"/>
<script src="/js/react.min.js"></script><script src="/js/react-dom.min.js"></script>
<script src="/js/prop-types.min.js"></script><script src="/js/recharts.min.js"></script>
<script src="/js/htm.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}html,body{height:100%;width:100%;overflow:hidden}
body{font-family:'DM Sans',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--tx)}

/* ── Core palette – matches the reference images ────────────────────── */
:root{
  /* Main surfaces */
  --bg:#eeecea;          /* warm off-white canvas */
  --sf:#ffffff;          /* card white */
  --sf2:#f5f4f1;         /* subtle secondary surface */
  --bd:#e2e0db;          /* soft border / divider */
  /* Text */
  --tx:#111111;          /* near-black */
  --tx2:#6b6860;         /* muted label */
  --tx3:#a09e99;         /* placeholder / disabled */
  /* Sidebar */
  --sb:#0a0a0a;          /* sidebar black */
  --sb2:#1a1a1a;         /* sidebar card bg */
  --sb3:#252525;         /* sidebar hover */
  --sbt:#d4d4d4;         /* sidebar icon color */
  /* Accent palette */
  --ac:#c8f135;          /* lime yellow-green (primary) */
  --ac2:#b8e020;         /* lime darker hover */
  --ac-tx:#1a2600;       /* text on lime bg */
  --pu:#9b8ef4;          /* lavender purple */
  --pu2:#b8aeff;         /* lavender light */
  --rd:#f0645a;          /* coral red */
  --rd2:#ff8a82;         /* coral light */
  --cy:#5bc4bf;          /* teal cyan */
  --cy2:#84dbd6;         /* teal light */
  --am:#f5d442;          /* amber yellow */
  --gn:#4ade80;          /* green (status) */
  /* Shadows */
  --sh: 0 2px 12px rgba(0,0,0,.07);
  --sh2: 0 8px 32px rgba(0,0,0,.10);
}

/* ── Light mode (explicit) ───────────────────────────────────────────── */
.lm{
  --bg:#eeecea;--sf:#ffffff;--sf2:#f5f4f1;--bd:#e2e0db;
  --tx:#111111;--tx2:#6b6860;--tx3:#a09e99;
  --sh: 0 2px 12px rgba(0,0,0,.07);
  --sh2: 0 8px 32px rgba(0,0,0,.10);
}

/* ── Dark mode override ──────────────────────────────────────────────── */
.dm{
  --bg:#111113;          /* dark canvas */
  --sf:#1c1c1f;          /* card dark */
  --sf2:#242428;         /* subtle secondary surface */
  --bd:#2e2e33;          /* border / divider */
  --tx:#f0eff0;          /* near-white */
  --tx2:#9b9aa0;         /* muted label */
  --tx3:#5c5b62;         /* placeholder / disabled */
  --sh: 0 2px 12px rgba(0,0,0,.3);
  --sh2: 0 8px 32px rgba(0,0,0,.5);
}

/* ── Scrollbar ──────────────────────────────────────────────────────── */
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--bd);border-radius:4px}

/* ── Date input ─────────────────────────────────────────────────────── */
input[type=date]{color-scheme:light;}
.dm input[type=date]{color-scheme:dark;}
input[type=date]::-webkit-calendar-picker-indicator{cursor:pointer;opacity:.6}

/* ── Card ───────────────────────────────────────────────────────────── */
.card{background:var(--sf);border-radius:18px;padding:20px;box-shadow:var(--sh);border:none}

/* ── Buttons ────────────────────────────────────────────────────────── */
.btn{display:inline-flex;align-items:center;gap:7px;padding:9px 18px;border-radius:100px;border:none;cursor:pointer;font-size:13px;font-weight:600;transition:all .17s;white-space:nowrap;line-height:1.2;font-family:inherit}
/* primary – lime */
.bp{background:var(--ac);color:var(--ac-tx)!important}
.bp:hover{background:var(--ac2);transform:translateY(-1px);box-shadow:0 4px 18px rgba(180,220,20,.35)}
.bp:disabled{opacity:.5;cursor:not-allowed;transform:none}
/* ghost */
.bg{background:transparent;color:var(--tx2)!important;border:1.5px solid var(--bd)}
.bg:hover{background:var(--sf2);color:var(--tx)!important;border-color:var(--tx3)}
/* danger */
.brd{background:rgba(240,100,90,.08);color:var(--rd)!important;border:1.5px solid rgba(240,100,90,.25)}
.brd:hover{background:rgba(240,100,90,.16)}
/* warning */
.bam{background:rgba(245,212,66,.10);color:#9a7c00!important;border:1.5px solid rgba(245,212,66,.4)}
.bam:hover{background:rgba(245,212,66,.2)}
/* dark pill (primary alternative) */
.bdk{background:var(--sb);color:#fff!important;border:none}
.bdk:hover{background:var(--sb3);transform:translateY(-1px)}

/* ── Inputs ─────────────────────────────────────────────────────────── */
.inp{background:var(--sf2);border:1.5px solid var(--bd);border-radius:12px;padding:10px 14px;color:var(--tx);font-size:13px;width:100%;outline:none;transition:border-color .17s;font-family:inherit}
.inp:focus{border-color:var(--ac);box-shadow:0 0 0 3px rgba(200,241,53,.2)}
.inp::placeholder{color:var(--tx3)}
textarea.inp{resize:vertical;min-height:68px}
.sel{background:var(--sf2);border:1.5px solid var(--bd);border-radius:12px;padding:10px 32px 10px 14px;color:var(--tx);font-size:13px;width:100%;outline:none;cursor:pointer;font-family:inherit;-webkit-appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='11' viewBox='0 0 24 24' fill='none' stroke='%236b6860' stroke-width='2.5'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 11px center}
.sel:focus{border-color:var(--ac);outline:none}

/* ── Badges / chips ─────────────────────────────────────────────────── */
.badge{display:inline-flex;align-items:center;padding:3px 9px;border-radius:100px;font-size:10px;font-weight:700;letter-spacing:.2px;text-transform:uppercase}
.nb{display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:12px;cursor:pointer;color:var(--sbt);font-size:13px;font-weight:500;transition:all .15s;border:none;background:transparent;width:100%;text-align:left;position:relative}
.nb:hover{background:var(--sb3);color:#fff}
.nb.act{background:var(--ac);color:var(--ac-tx)!important}
.nb.act svg{stroke:var(--ac-tx)!important}

/* ── Overlay / Modal ─────────────────────────────────────────────────── */
.ov{position:fixed;inset:0;background:rgba(10,10,10,.4);display:flex;align-items:center;justify-content:center;z-index:2000;padding:16px;backdrop-filter:blur(8px)}
.mo{background:var(--sf);border-radius:22px;padding:28px;width:100%;max-width:640px;max-height:94vh;overflow-y:auto;box-shadow:var(--sh2);border:none}
.mo-xl{max-width:920px}

/* ── Task card ──────────────────────────────────────────────────────── */
.tkc{background:var(--sf);border-radius:16px;padding:14px;cursor:pointer;transition:all .18s;box-shadow:var(--sh);border:none}
.tkc:hover{transform:translateY(-2px);box-shadow:var(--sh2)}

/* ── Progress bar ────────────────────────────────────────────────────── */
.prog{height:5px;background:var(--bd);border-radius:100px;overflow:hidden}
.progf{height:100%;border-radius:100px;transition:width .5s}

/* ── Tab buttons ─────────────────────────────────────────────────────── */
.tb{padding:7px 14px;border-radius:100px;cursor:pointer;font-size:12px;font-weight:600;border:none;background:transparent;color:var(--tx2);transition:all .14s;font-family:inherit}
.tb.act{background:var(--sb);color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.15)}
.tb:hover:not(.act){background:var(--sf2);color:var(--tx)}

/* ── Avatar ──────────────────────────────────────────────────────────── */
.av{border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0;letter-spacing:-.5px}

/* ── Labels / chips ──────────────────────────────────────────────────── */
.lbl{color:var(--tx2);font-size:11px;font-family:'DM Sans',sans-serif;margin-bottom:5px;display:block;text-transform:uppercase;letter-spacing:.6px;font-weight:600}
.chip{display:inline-flex;align-items:center;gap:5px;padding:5px 11px;border-radius:100px;font-size:11px;font-weight:600;background:var(--sf2);border:1.5px solid var(--bd);color:var(--tx2);cursor:pointer;transition:all .14s}
.chip:hover{border-color:var(--ac);color:var(--ac-tx);background:rgba(200,241,53,.15)}
.chip.on{background:rgba(200,241,53,.18);border-color:var(--ac);color:var(--ac-tx)}

/* ── Drop zone ───────────────────────────────────────────────────────── */
.drop-zone{border:2px dashed var(--bd);border-radius:14px;padding:22px;text-align:center;cursor:pointer;transition:all .2s;color:var(--tx3);font-size:13px}
.drop-zone:hover,.drop-zone.over{border-color:var(--ac);color:var(--ac-tx);background:rgba(200,241,53,.07)}

/* ── Animations ──────────────────────────────────────────────────────── */
@keyframes fi{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}.fi{animation:fi .22s ease}
@keyframes sp{to{transform:rotate(360deg)}}
.spin{display:inline-block;width:16px;height:16px;border:2.5px solid var(--bd);border-top-color:var(--ac);border-radius:50%;animation:sp .6s linear infinite;vertical-align:middle}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}.pulse{animation:pulse 1.5s ease-in-out infinite}

/* ── AI button & panel ───────────────────────────────────────────────── */
.ai-btn{position:fixed;bottom:22px;right:22px;z-index:1800;width:50px;height:50px;border-radius:50%;background:var(--sb);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:21px;box-shadow:0 4px 20px rgba(0,0,0,.35);transition:all .2s}
.ai-btn:hover{transform:scale(1.08);box-shadow:0 6px 28px rgba(0,0,0,.5)}
.ai-panel{position:fixed;bottom:86px;right:22px;z-index:1800;width:400px;height:560px;background:#fff;border-radius:22px;display:flex;flex-direction:column;box-shadow:0 16px 60px rgba(0,0,0,.2);overflow:hidden;border:none}
@keyframes slideUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}.ai-panel{animation:slideUp .2s ease}
.ai-msg-user{align-self:flex-end;background:var(--sb);color:#fff;border-radius:18px 18px 4px 18px;padding:10px 14px;font-size:13px;max-width:80%;line-height:1.5}
.ai-msg-ai{align-self:flex-start;background:var(--sf2);color:var(--tx);border-radius:18px 18px 18px 4px;padding:10px 14px;font-size:13px;max-width:90%;line-height:1.6;white-space:pre-wrap}
.ai-action{background:rgba(200,241,53,.1);border:1px solid rgba(200,241,53,.3);border-radius:10px;padding:8px 11px;font-size:11px;color:#5a7000;font-family:monospace;margin-top:5px}

/* ── Sidebar-specific overrides ──────────────────────────────────────── */
/* Sidebar nav icon buttons */
.snb{width:42px;height:42px;border-radius:50%;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;background:transparent;color:var(--sbt);transition:all .15s;flex-shrink:0}
.snb:hover{background:var(--sb3)}
.snb.act{background:var(--ac)}
.snb.act svg{stroke:var(--ac-tx)!important}

/* ── Priority / status chips ─────────────────────────────────────────── */
.pri-hi{background:rgba(240,100,90,.12);color:var(--rd);border:1.5px solid rgba(240,100,90,.3)}
.pri-md{background:rgba(155,142,244,.12);color:var(--pu);border:1.5px solid rgba(155,142,244,.3)}
.pri-lo{background:rgba(91,196,191,.12);color:var(--cy);border:1.5px solid rgba(91,196,191,.3)}
.pri-gn{background:rgba(74,222,128,.12);color:#16803c;border:1.5px solid rgba(74,222,128,.3)}

/* ── Stat number display ─────────────────────────────────────────────── */
.stat-num{font-family:'Syne',sans-serif;font-weight:800;line-height:1;letter-spacing:-1px}
</style></head><body class="lm">

<div id="root" style="height:100vh;display:flex;align-items:center;justify-content:center;flex-direction:column">
  <div style="width:88px;height:88px;background:linear-gradient(135deg,#c8f135,#9b8ef4);border-radius:24px;display:flex;align-items:center;justify-content:center;box-shadow:0 0 40px rgba(200,241,53,.45);animation:sp .9s linear infinite">
    <svg width="46" height="46" viewBox="0 0 64 64" fill="none">
      <circle cx="32" cy="32" r="9" fill="white"/>
      <circle cx="32" cy="11" r="6" fill="white" opacity="0.95"/>
      <circle cx="51" cy="43" r="6" fill="white" opacity="0.95"/>
      <circle cx="13" cy="43" r="6" fill="white" opacity="0.95"/>
      <line x1="32" y1="17" x2="32" y2="23" stroke="white" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="46" y1="40" x2="40" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="18" y1="40" x2="24" y2="36" stroke="white" stroke-width="3.5" stroke-linecap="round"/>
    </svg>
  </div>
  <p style="color:var(--tx2);font-size:13px;margin-top:22px;font-family:'DM Sans',sans-serif;letter-spacing:.3px">Loading ProjectFlow...</p>
  <div id="LE" style="display:none;color:var(--rd);font-size:12px;margin-top:14px;max-width:360px;padding:12px 16px;background:rgba(248,113,113,.07);border:1px solid rgba(248,113,113,.2);border-radius:10px;text-align:center"></div>
</div>
<script>
window.onerror=function(m,s,l,c,e){var el=document.getElementById('LE');if(el){el.style.display='block';el.innerHTML='<b>Load Error</b><br>'+(e?e.message:m);}};
</script>
<script>
(function(){
'use strict';
if(typeof React==='undefined'||typeof Recharts==='undefined'){
  var el=document.getElementById('LE');
  if(el){el.style.display='block';el.innerHTML='<b>Missing libraries.</b> Delete the <b>pf_static\\</b> folder and restart.';}
  return;
}
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
const ROLES=['Admin','Developer','Tester','Viewer'];
const PAL=['#7c3aed','#2563eb','#059669','#d97706','#dc2626','#ec4899','#0891b2','#c8f135'];
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
  return html`<div class="av" style=${{width:size,height:size,background:(u&&u.color)||'#c8f135',color:'#fff',fontSize:Math.max(9,Math.floor(size*.33))}}>
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
  constructor(p){super(p);this.state={err:null};}
  static getDerivedStateFromError(e){return{err:e};}
  render(){
    if(this.state.err)return html`<div style=${{padding:40,textAlign:'center',color:'var(--rd)'}}>
      <div style=${{fontSize:28,marginBottom:10}}>⚠</div>
      <p style=${{marginBottom:14}}>${this.state.err.message}</p>
      <button class="btn bp" onClick=${()=>this.setState({err:null})}>Retry</button></div>`;
    return this.props.children;
  }
}

/* ─── AuthScreen with Workspace ──────────────────────────────────────────── */
function AuthScreen({onLogin}){
  const [tab,setTab]=useState('login');
  const [regMode,setRegMode]=useState('create'); // 'create' or 'join'
  const [wsName,setWsName]=useState('');
  const [inviteCode,setInviteCode]=useState('');
  const [name,setName]=useState('');
  const [email,setEmail]=useState('');
  const [pw,setPw]=useState('');
  const [role,setRole]=useState('Developer');
  const [showPw,setShowPw]=useState(false);
  const [err,setErr]=useState('');
  const [busy,setBusy]=useState(false);

  const go=async()=>{
    setErr('');setBusy(true);
    if(tab==='login'){
      const r=await api.post('/api/auth/login',{email,password:pw});
      if(r.error)setErr(r.error); else onLogin(r);
    } else {
      if(!name||!email||!pw){setErr('All fields required.');setBusy(false);return;}
      if(regMode==='create'&&!wsName){setErr('Workspace name required.');setBusy(false);return;}
      if(regMode==='join'&&!inviteCode){setErr('Invite code required.');setBusy(false);return;}
      const r=await api.post('/api/auth/register',{mode:regMode,workspace_name:wsName,invite_code:inviteCode,name,email,password:pw,role});
      if(r.error)setErr(r.error); else onLogin(r);
    }
    setBusy(false);
  };

  return html`
    <div style=${{minHeight:'100vh',background:'var(--bg)',display:'flex',alignItems:'center',justifyContent:'center',padding:20}}>
      <div class="fi" style=${{width:'100%',maxWidth:460}}>
        <div style=${{textAlign:'center',marginBottom:24}}>
          <div style=${{display:'inline-flex',alignItems:'center',justifyContent:'center',width:64,height:64,background:'var(--ac)',borderRadius:20,marginBottom:14,boxShadow:'0 4px 24px rgba(200,241,53,.35)'}}><svg width="34" height="34" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="32" cy="32" r="9" fill="#1a2600"/><circle cx="32" cy="11" r="6" fill="#1a2600" opacity="0.9"/><circle cx="51" cy="43" r="6" fill="#1a2600" opacity="0.9"/><circle cx="13" cy="43" r="6" fill="#1a2600" opacity="0.9"/><line x1="32" y1="17" x2="32" y2="23" stroke="#1a2600" stroke-width="3.5" stroke-linecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="#1a2600" stroke-width="3.5" stroke-linecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="#1a2600" stroke-width="3.5" stroke-linecap="round"/></svg></div>
          <h1 style=${{fontSize:28,fontWeight:800,color:'var(--tx)',letterSpacing:-1,fontFamily:"'Syne',sans-serif"}}>ProjectFlow</h1>
          <p style=${{color:'var(--tx2)',fontSize:13,marginTop:4}}>Team project management, your way</p>
        </div>
        <div class="card" style=${{padding:28}}>
          <div style=${{display:'flex',gap:4,background:'var(--sf2)',borderRadius:10,padding:4,marginBottom:20}}>
            ${['login','register'].map(t=>html`
              <button key=${t} class=${'tb'+(tab===t?' act':'')} style=${{flex:1,padding:'7px 0'}}
                onClick=${()=>{setTab(t);setErr('');}}>
                ${t==='login'?'Sign In':'Create Account'}
              </button>`)}
          </div>

          ${tab==='register'?html`
            <div style=${{display:'flex',gap:4,background:'var(--sf2)',borderRadius:9,padding:3,marginBottom:16}}>
              ${[['create','🏢 New Workspace'],['join','🔗 Join Workspace']].map(([m,lbl])=>html`
                <button key=${m} class=${'tb'+(regMode===m?' act':'')} style=${{flex:1,padding:'6px 0',fontSize:11}}
                  onClick=${()=>setRegMode(m)}>${lbl}</button>`)}
            </div>
            ${regMode==='create'?html`
              <div style=${{marginBottom:12}}><label class="lbl">Workspace Name</label>
                <input class="inp" placeholder="e.g. Acme Corp, My Startup" value=${wsName} onInput=${e=>setWsName(e.target.value)}/></div>`:null}
            ${regMode==='join'?html`
              <div style=${{marginBottom:12,padding:'10px 13px',background:'rgba(99,102,241,.07)',borderRadius:9,border:'1px solid rgba(200,241,53,.18)'}}>
                <label class="lbl">Invite Code</label>
                <input class="inp" placeholder="Enter 8-character invite code" value=${inviteCode} 
                  onInput=${e=>setInviteCode(e.target.value.toUpperCase())}
                  style=${{fontFamily:'monospace',letterSpacing:2,fontSize:15,textAlign:'center'}}/>
                <p style=${{fontSize:11,color:'var(--tx3)',marginTop:6,textAlign:'center'}}>Get this code from your workspace admin</p>
              </div>`:null}`:null}

          <div style=${{display:'flex',flexDirection:'column',gap:12}}>
            ${tab==='register'?html`<div><label class="lbl">Full Name</label>
              <input class="inp" placeholder="Alice Chen" value=${name} onInput=${e=>setName(e.target.value)}/></div>`:null}
            <div><label class="lbl">Email</label>
              <input class="inp" type="email" placeholder="you@company.com" value=${email}
                onInput=${e=>setEmail(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&go()}/></div>
            <div><label class="lbl">Password</label>
              <div style=${{position:'relative'}}>
                <input class="inp" style=${{paddingRight:40}} type=${showPw?'text':'password'}
                  placeholder="••••••••" value=${pw}
                  onInput=${e=>setPw(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&go()}/>
                <button onClick=${()=>setShowPw(!showPw)}
                  style=${{position:'absolute',right:11,top:'50%',transform:'translateY(-50%)',background:'none',border:'none',cursor:'pointer',color:'var(--tx3)',fontSize:14}}>
                  ${showPw?'🙈':'👁'}
                </button>
              </div>
            </div>
            ${tab==='register'?html`<div><label class="lbl">Role</label>
              <select class="sel" value=${role} onChange=${e=>setRole(e.target.value)}>
                ${ROLES.map(r=>html`<option key=${r}>${r}</option>`)}
              </select></div>`:null}
            ${err?html`<div style=${{color:'var(--rd)',fontSize:12,padding:'8px 12px',background:'rgba(248,113,113,.07)',borderRadius:8,border:'1px solid rgba(248,113,113,.2)'}}>${err}</div>`:null}
            <button class="btn bp" style=${{justifyContent:'center',height:42}} onClick=${go} disabled=${busy}>
              ${busy?html`<span class="spin"></span>`:(tab==='login'?'Sign In →':regMode==='create'?'Create Workspace & Account →':'Join & Create Account →')}
            </button>
          </div>
          ${tab==='login'?html`
            <div style=${{marginTop:16,padding:'10px 13px',background:'var(--sf2)',borderRadius:9,fontSize:11,fontFamily:'monospace',color:'var(--tx3)',lineHeight:2.1,border:'1px solid var(--bd)'}}>
              <b style=${{color:'var(--tx2)',display:'block',marginBottom:2}}>Demo Accounts</b>
              alice@dev.io / pass123 (Admin) &nbsp; bob@dev.io / pass123 (Dev)
            </div>`:null}
        </div>
      </div>
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

/* ─── Sidebar ─────────────────────────────────────────────────────────────── */
function Sidebar({cu,view,setView,onLogout,unread,dmUnread,col,setCol,wsName,callState,onCallAction}){
  const totalDm=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);
  const inCall=callState&&callState.status==='in-call';
  const fmtTime=s=>{const m=Math.floor(s/60);const sec=s%60;return m+':'+(sec<10?'0':'')+sec;};
  const ICONS={
    dashboard:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>`,
    projects:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>`,
    tasks:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>`,
    messages:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
    dm:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4c0-1.1.9-2 2-2h8a2 2 0 0 1 2 2v5z"/><path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1"/></svg>`,
    notifs:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>`,
    reminders:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
    team:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="17" cy="8" r="3"/><circle cx="7" cy="8" r="3"/><path d="M3 21v-2a5 5 0 0 1 8.66-3.43"/><path d="M13 21v-2a5 5 0 0 1 10 0v2"/></svg>`,
    settings:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93l-1.41 1.41M4.93 4.93l1.41 1.41M19.07 19.07l-1.41-1.41M4.93 19.07l1.41-1.41M12 2v2M12 20v2M2 12h2M20 12h2"/></svg>`,
  };
  const items=[
    {id:'dashboard',icon:ICONS.dashboard,label:'Dashboard'},
    {id:'projects', icon:ICONS.projects, label:'Projects'},
    {id:'tasks',    icon:ICONS.tasks,    label:'Tasks'},
    {id:'messages', icon:ICONS.messages, label:'Channels'},
    {id:'dm',       icon:ICONS.dm,       label:'Direct Messages',badge:totalDm},
    {id:'reminders',icon:ICONS.reminders,label:'Reminders'},
    ...(cu&&cu.role==='Admin'?[{id:'team',icon:ICONS.team,label:'Team'},{id:'settings',icon:ICONS.settings,label:'Settings'}]:[]),
  ];
  return html`
    <aside style=${{width:68,minWidth:68,background:'var(--sb)',display:'flex',flexDirection:'column',height:'100vh',flexShrink:0,overflow:'hidden',alignItems:'center',paddingBottom:16}}>
      <div style=${{width:'100%',display:'flex',alignItems:'center',justifyContent:'center',padding:'18px 0 14px',borderBottom:'1px solid rgba(255,255,255,.07)',marginBottom:8}}>
        <div style=${{width:38,height:38,background:'var(--ac)',borderRadius:12,display:'flex',alignItems:'center',justifyContent:'center',cursor:'pointer',flexShrink:0}}
          onClick=${()=>setView('dashboard')} title="ProjectFlow">
          <svg width="22" height="22" viewBox="0 0 64 64" fill="none">
            <circle cx="32" cy="32" r="7" fill="#1a2600"/>
            <circle cx="32" cy="13" r="5" fill="#1a2600" opacity="0.9"/>
            <circle cx="48" cy="43" r="5" fill="#1a2600" opacity="0.9"/>
            <circle cx="16" cy="43" r="5" fill="#1a2600" opacity="0.9"/>
            <line x1="32" y1="18" x2="32" y2="25" stroke="#1a2600" strokeWidth="3" strokeLinecap="round"/>
            <line x1="44" y1="40" x2="38" y2="36" stroke="#1a2600" strokeWidth="3" strokeLinecap="round"/>
            <line x1="20" y1="40" x2="26" y2="36" stroke="#1a2600" strokeWidth="3" strokeLinecap="round"/>
          </svg>
        </div>
      </div>
      <nav style=${{flex:1,display:'flex',flexDirection:'column',gap:5,alignItems:'center',width:'100%',overflowY:'auto',padding:'4px 0'}}>
        ${items.map(it=>html`
          <button key=${it.id} title=${it.label} onClick=${()=>setView(it.id)}
            style=${{width:42,height:42,borderRadius:'50%',border:'none',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',position:'relative',flexShrink:0,transition:'all .15s',
              background:view===it.id?'var(--ac)':'transparent',
              color:view===it.id?'var(--ac-tx)':'rgba(255,255,255,.38)'
            }}
            onMouseEnter=${e=>{if(view!==it.id){e.currentTarget.style.background='var(--sb3)';e.currentTarget.style.color='rgba(255,255,255,.85)';}}}
            onMouseLeave=${e=>{if(view!==it.id){e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(255,255,255,.38)';}}}>
            ${it.icon}
            ${it.badge>0?html`<div style=${{position:'absolute',top:5,right:5,width:7,height:7,borderRadius:'50%',background:'var(--rd)',border:'1.5px solid var(--sb)'}}></div>`:null}
          </button>`)}
      </nav>
      <div style=${{display:'flex',flexDirection:'column',gap:6,alignItems:'center'}}>
        ${inCall?html`
          <button title="In Huddle" onClick=${()=>onCallAction&&onCallAction('show')}
            style=${{width:42,height:42,borderRadius:'50%',border:'none',cursor:'pointer',background:'rgba(34,197,94,.12)',color:'#22c55e',display:'flex',alignItems:'center',justifyContent:'center',flexDirection:'column',gap:1}}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>
            <span style=${{fontSize:8,fontWeight:700,lineHeight:1}}>${fmtTime(callState.elapsed||0)}</span>
          </button>`:null}
        <button title="Sign Out" onClick=${onLogout}
          style=${{width:42,height:42,borderRadius:'50%',border:'none',cursor:'pointer',background:'transparent',color:'rgba(255,255,255,.28)',display:'flex',alignItems:'center',justifyContent:'center',transition:'all .15s',marginBottom:8}}
          onMouseEnter=${e=>{e.currentTarget.style.background='rgba(240,100,90,.15)';e.currentTarget.style.color='var(--rd)';}}
          onMouseLeave=${e=>{e.currentTarget.style.background='transparent';e.currentTarget.style.color='rgba(255,255,255,.28)';}}>
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        </button>
        <div style=${{width:36,height:36,borderRadius:'50%',overflow:'hidden',border:'2px solid rgba(255,255,255,.12)',flexShrink:0,cursor:'pointer'}} onClick=${()=>setView('settings')} title=${cu&&cu.name}>
          ${cu&&cu.avatar_url
            ?html`<img src=${cu.avatar_url} style=${{width:'100%',height:'100%',objectFit:'cover'}}/>`
            :html`<div style=${{width:'100%',height:'100%',background:cu&&cu.color||'var(--ac)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:14,fontWeight:800,color:'#1a2600'}}>${cu&&(cu.name||'?')[0].toUpperCase()}</div>`}
        </div>
      </div>
    </aside>`;
}

/* ─── Header ──────────────────────────────────────────────────────────────── */
function Header({title,sub,dark,setDark,extra,cu,setCu,upcomingReminders,onViewReminders,notifs,onNotifClick,onMarkAllRead,onClearAll}){
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
    <div style=${{flexShrink:0,background:'var(--bg)',borderBottom:'1px solid var(--bd)'}}>
      <div style=${{padding:'0 20px',height:52,display:'flex',alignItems:'center',gap:12}}>
        <div style=${{display:'flex',alignItems:'center',gap:8,flexShrink:0,padding:'5px 14px 5px 10px',background:'var(--sb)',borderRadius:100,cursor:'pointer'}} onClick=${onViewReminders}>
          <svg width="13" height="13" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="7" fill="#c8f135"/><circle cx="32" cy="13" r="4" fill="#c8f135" opacity="0.85"/><circle cx="48" cy="43" r="4" fill="#c8f135" opacity="0.85"/><circle cx="16" cy="43" r="4" fill="#c8f135" opacity="0.85"/><line x1="32" y1="17" x2="32" y2="25" stroke="#c8f135" strokeWidth="2.5" strokeLinecap="round"/><line x1="44" y1="40" x2="38" y2="36" stroke="#c8f135" strokeWidth="2.5" strokeLinecap="round"/><line x1="20" y1="40" x2="26" y2="36" stroke="#c8f135" strokeWidth="2.5" strokeLinecap="round"/></svg>
          <span style=${{fontSize:11,fontWeight:700,color:'#fff',letterSpacing:'.3px'}}>Your Schedule</span>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,.4)" strokeWidth="2" strokeLinecap="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          <span style=${{fontSize:11,color:'var(--ac)',fontWeight:700}}>${todayStr}</span>
        </div>
        <div style=${{flex:1,overflowX:'auto',scrollbarWidth:'none',msOverflowStyle:'none',padding:'0 4px'}}>
          <div style=${{height:38,background:'var(--sb)',borderRadius:100,display:'flex',alignItems:'center',padding:'0 16px',gap:0,position:'relative',minWidth:0,overflow:'hidden',boxShadow:'0 2px 10px rgba(0,0,0,.14)'}}>
            ${upcoming.length===0?html`
              <div style=${{display:'flex',alignItems:'center',gap:10,width:'100%',justifyContent:'center'}}>
                <span style=${{fontSize:11,color:'rgba(255,255,255,.35)',fontStyle:'italic'}}>No reminders today</span>
                <button onClick=${onViewReminders} style=${{fontSize:10,padding:'3px 12px',height:22,borderRadius:100,background:'var(--ac)',color:'var(--ac-tx)',border:'none',cursor:'pointer',fontWeight:700}}>+ Add</button>
              </div>
            `:html`
              <div style=${{display:'flex',alignItems:'center',gap:0,width:'100%',overflowX:'auto',scrollbarWidth:'none',position:'relative'}}>
                <div style=${{position:'absolute',top:'50%',left:0,right:40,height:1.5,background:'linear-gradient(90deg,rgba(200,241,53,.15) 0%,rgba(200,241,53,.4) 60%,rgba(200,241,53,.1) 100%)',transform:'translateY(-50%)',borderRadius:2,zIndex:0}}></div>
                ${upcoming.map((r,i)=>{
                  const isNow=Math.abs(new Date(r.remind_at)-new Date())<1800000;
                  const abbr=(r.task_title||'').split(' ').slice(0,2).join(' ');
                  const tStr=fmtT(r.remind_at);
                  return html`
                    <div key=${r.id} style=${{display:'flex',flexDirection:'column',alignItems:'center',marginRight:i<upcoming.length-1?32:0,flexShrink:0,position:'relative',zIndex:1,cursor:'pointer'}} onClick=${onViewReminders} title=${r.task_title}>
                      <div style=${{position:'relative'}}>
                        ${cu&&cu.avatar_data&&cu.avatar_data.startsWith('data:image')?
                          html`<img src=${cu.avatar_data} style=${{width:isNow?30:24,height:isNow?30:24,borderRadius:'50%',objectFit:'cover',border:isNow?'2.5px solid #22c55e':'2px solid '+(dark?'rgba(255,255,255,.3)':'rgba(0,0,0,.15)'),boxShadow:isNow?'0 0 0 3px rgba(34,197,94,.25)':'0 1px 4px rgba(0,0,0,.15)',transition:'all .2s'}}/>`:
                          html`<div style=${{width:isNow?30:24,height:isNow?30:24,borderRadius:'50%',background:isNow?'linear-gradient(135deg,#22c55e,#16a34a)':'linear-gradient(135deg,#c8f135,#b8e020)',border:isNow?'2.5px solid #22c55e':'2px solid '+(dark?'rgba(255,255,255,.3)':'rgba(0,0,0,.1)'),display:'flex',alignItems:'center',justifyContent:'center',fontSize:isNow?11:9,fontWeight:800,color:'#fff',boxShadow:isNow?'0 0 0 3px rgba(34,197,94,.25)':'0 1px 4px rgba(200,241,53,.3)',transition:'all .2s'}}>
                            ${(r.task_title||'?').charAt(0).toUpperCase()}
                          </div>`}
                        ${isNow?html`<div style=${{position:'absolute',bottom:-2,right:-2,width:8,height:8,borderRadius:'50%',background:'#22c55e',border:'1.5px solid #fff',boxShadow:'0 0 4px #22c55e'}}></div>`:null}
                      </div>
                      <div style=${{display:'flex',flexDirection:'column',alignItems:'center',marginTop:1}}>
                        <span style=${{fontSize:9,fontWeight:700,color:isNow?'#16a34a':'var(--ac)',fontFamily:'monospace',lineHeight:1}}>${tStr}</span>
                        <span style=${{fontSize:8,color:'var(--tx3)',maxWidth:52,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',lineHeight:1.2}}>${abbr}</span>
                      </div>
                    </div>`;
                })}
                <button onClick=${onViewReminders} style=${{marginLeft:'auto',flexShrink:0,width:22,height:22,borderRadius:'50%',background:'rgba(200,241,53,.12)',border:'1px solid rgba(200,241,53,.25)',cursor:'pointer',color:'var(--ac)',fontSize:13,display:'flex',alignItems:'center',justifyContent:'center',fontWeight:700,lineHeight:1}} title="Manage reminders">+</button>
              </div>
            `}
          </div>
        </div>
        <div style=${{display:'flex',alignItems:'center',gap:7,flexShrink:0}}>
          <button class="btn bg" style=${{padding:'5px 12px',fontSize:12,height:30,borderRadius:100,border:'1.5px solid var(--bd)',fontWeight:600}} onClick=${()=>setDark(!dark)}>${dark?'☀️ Light':'🌙 Dark'}</button>
          <div style=${{position:'relative'}} ref=${npRef}>
            <button style=${{width:34,height:34,borderRadius:'50%',border:'none',background:showNP?'var(--sf2)':'var(--sf)',boxShadow:showNP?'none':'var(--sh)',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',position:'relative',color:'var(--tx2)',transition:'all .15s'}}
              onClick=${()=>setShowNP(v=>!v)}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
              ${unread>0?html`<div style=${{position:'absolute',top:-3,right:-3,width:15,height:15,borderRadius:'50%',background:'#ef4444',border:'2px solid var(--sf)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:8,fontWeight:800,color:'#fff'}}>${unread>9?'9+':unread}</div>`:null}
            </button>
            ${showNP?html`
              <div style=${{position:'absolute',top:36,right:0,width:320,maxHeight:400,background:'#fff',border:'none',borderRadius:16,boxShadow:'0 8px 40px rgba(0,0,0,.14)',zIndex:3000,overflow:'hidden',display:'flex',flexDirection:'column'}}>
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
                      style=${{display:'flex',gap:9,padding:'9px 13px',borderBottom:'1px solid var(--bd)',cursor:'pointer',background:n.read?'transparent':'rgba(200,241,53,.04)'}}>
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
                <div style=${{fontSize:11,fontWeight:700,color:'var(--tx)'}}>${cu.name.split(' ')[0]}</div>
                <div style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace'}}>${cu.role}</div>
              </div>
            </div>
            ${showProfile?html`
              <div style=${{position:'absolute',top:38,right:0,width:290,background:'#fff',border:'none',borderRadius:18,boxShadow:'0 8px 40px rgba(0,0,0,.15)',zIndex:3000,overflow:'hidden'}}>
                <div style=${{padding:'20px 16px',background:'linear-gradient(135deg,rgba(200,241,53,.12),rgba(184,224,32,.04))',borderBottom:'1px solid var(--bd)',display:'flex',flexDirection:'column',alignItems:'center',gap:10}}>
                  <div style=${{position:'relative',cursor:'pointer'}} title="Click to change photo"
                    onClick=${e=>{e.stopPropagation();prImgRef.current&&prImgRef.current.click();}}>
                    ${(cu.avatar_data&&cu.avatar_data.startsWith('data:image'))?
                      html`<img src=${cu.avatar_data} style=${{width:68,height:68,borderRadius:'50%',objectFit:'cover',border:'3px solid var(--ac)',display:'block'}}/>`:
                      html`<div style=${{width:68,height:68,borderRadius:'50%',background:cu.color||'#c8f135',display:'flex',alignItems:'center',justifyContent:'center',fontSize:24,fontWeight:800,color:'#fff',border:'3px solid var(--ac)'}}>${cu.avatar||'?'}</div>`}
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
                    <div style=${{fontSize:15,fontWeight:800,color:'var(--tx)',marginBottom:2}}>${cu.name}</div>
                    <div style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace',marginBottom:4,wordBreak:'break-all'}}>${cu.email}</div>
                    <span style=${{display:'inline-block',padding:'3px 10px',borderRadius:20,fontSize:10,fontWeight:700,fontFamily:'monospace',background:'rgba(200,241,53,.15)',color:'var(--ac2)',textTransform:'uppercase'}}>${cu.role}</span>
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
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'0 22px',height:54,borderTop:'1px solid var(--bd)'}}>
        <div>
          <h1 style=${{fontSize:18,fontWeight:800,color:'var(--tx)',letterSpacing:-.5,fontFamily:"'Syne',sans-serif"}}>${title}</h1>
          ${sub?html`<p style=${{color:'var(--tx3)',fontSize:11,marginTop:2,fontWeight:500}}>${sub}</p>`:null}
        </div>
        <div style=${{display:'flex',alignItems:'center',gap:7}}>${extra||null}</div>
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
function TaskModal({task,onClose,onSave,onDel,projects,users,cu,defaultPid,onSetReminder}){
  const [title,setTitle]=useState((task&&task.title)||'');
  const [desc,setDesc]=useState((task&&task.description)||'');
  const [pid,setPid]=useState((task&&task.project)||defaultPid||(projects[0]&&projects[0].id)||'');
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
      const payload={title:title.trim()||task.title,description:desc,project:pid,
        assignee:ass,priority:pri,stage,due,pct,comments:updated,id:task.id};
      await api.put('/api/tasks/'+task.id,payload);
    }
  };
  const save=async(opts={})=>{
    if(!title.trim()){setErr('Title required.');return null;}
    setSaving(true);setErr('');
    const payload={title:title.trim(),description:desc,project:pid,assignee:ass,priority:pri,stage,due,pct,comments:cmts};
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
            <h2 style=${{fontSize:17,fontWeight:800,color:'var(--tx)'}}>${isEdit?'Edit Task':'New Task'}</h2>
            ${isEdit?html`<span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${task.id}</span>`:null}
          </div>
          <div style=${{display:'flex',gap:7}}>
            ${isEdit&&onDel?html`<button class="btn brd" style=${{fontSize:12,padding:'6px 11px'}}
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
            <div><label class="lbl">Title *</label>
              <input class="inp" placeholder="Task title..." value=${title} onInput=${e=>setTitle(e.target.value)}/></div>
            <div><label class="lbl">Description</label>
              <textarea class="inp" rows="3" placeholder="Describe the task..." onInput=${e=>setDesc(e.target.value)}>${desc}</textarea></div>
            <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
              <div><label class="lbl">Project</label>
                <select class="sel" value=${pid} onChange=${e=>setPid(e.target.value)}>
                  ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
                </select></div>
              <div><label class="lbl">Assignee</label>
                <select class="sel" value=${ass} onChange=${e=>setAss(e.target.value)}>
                  <option value="">Unassigned</option>
                  ${safe(users).map(u=>html`<option key=${u.id} value=${u.id}>${u.name}</option>`)}
                </select></div>
            </div>
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
                <input class="inp" type="date" value=${due} onChange=${e=>setDue(e.target.value)}/></div>
            </div>
            <div><label class="lbl">Completion: ${pct}%</label>
              <div style=${{display:'flex',alignItems:'center',gap:12}}>
                <input type="range" min="0" max="100" value=${pct} style=${{flex:1,accentColor:'var(--ac)',cursor:'pointer'}} onChange=${e=>setPct(parseInt(e.target.value))}/>
                <span style=${{fontSize:13,color:'var(--ac)',fontWeight:700,fontFamily:'monospace',width:34,textAlign:'right'}}>${pct}%</span>
              </div>
            </div>
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
                  <div style=${{background:'rgba(200,241,53,.06)',borderRadius:10,border:'1px solid rgba(99,102,241,.18)',padding:'12px 14px',display:'flex',flexDirection:'column',gap:10}}>
                    <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
                      <div>
                        <label class="lbl" style=${{fontSize:10,marginBottom:3}}>Reminder Date</label>
                        <input class="inp" type="date" value=${rmDate} onChange=${e=>setRmDate(e.target.value)} min=${new Date().toISOString().split('T')[0]} style=${{fontSize:12}}/>
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
              <button class="btn bg" onClick=${onClose}>Cancel</button>
              ${onSetReminder&&isEdit?html`<button class="btn bam" style=${{fontSize:12}} onClick=${async()=>{const r=await save({keepOpen:true});if(r!==null){onClose();onSetReminder({id:(task&&task.id)||r.id,title:title,due});}}}>⏰ Set Reminder</button>`:null}
              <button class="btn bp" onClick=${save} disabled=${saving}>${saving?html`<span class="spin"></span>`:(isEdit?'Save Changes':'Create Task')}</button>
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
function ProjectDetail({project,allTasks,allUsers,cu,onClose,onReload,onSetReminder}){
  const [tab,setTab]=useState('tasks');const [edit,setEdit]=useState(false);
  const [name,setName]=useState(project.name||'');const [desc,setDesc]=useState(project.description||'');
  const [tDate,setTDate]=useState(project.target_date||'');const [color,setColor]=useState(project.color||'#c8f135');
  const [members,setMembers]=useState(safe(project.members));const [saving,setSaving]=useState(false);
  const [showNew,setShowNew]=useState(false);const [editTask,setEditTask]=useState(null);

  const projTasks=useMemo(()=>safe(allTasks).filter(t=>t.project===project.id),[allTasks,project.id]);
  const projUsers=useMemo(()=>safe(members).map(id=>safe(allUsers).find(u=>u.id===id)).filter(Boolean),[members,allUsers]);
  const done=projTasks.filter(t=>t.stage==='completed').length;
  const pc=projTasks.length?Math.round(projTasks.reduce((a,t)=>a+(t.pct||0),0)/projTasks.length):(project.progress||0);
  const stageGroups=KCOLS.map(s=>({s,tasks:projTasks.filter(t=>t.stage===s)})).filter(g=>g.tasks.length>0);

  const saveEdit=async()=>{setSaving(true);await api.put('/api/projects/'+project.id,{name,description:desc,target_date:tDate,color,members});await onReload();setSaving(false);setEdit(false);};
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
              ${edit?html`<input class="inp" style=${{fontSize:17,fontWeight:800,padding:'4px 8px'}} value=${name} onInput=${e=>setName(e.target.value)}/>`:
                      html`<h2 style=${{fontSize:18,fontWeight:800,color:'var(--tx)'}}>${project.name}</h2>`}
            </div>
            <div style=${{display:'flex',gap:7,flexShrink:0}}>
              ${cu&&cu.role!=='Viewer'&&!edit?html`<button class="btn bg" style=${{fontSize:12,padding:'7px 12px'}} onClick=${()=>setEdit(true)}>✏ Edit</button>`:null}
              ${edit?html`<button class="btn bg" onClick=${()=>setEdit(false)}>Cancel</button><button class="btn bp" onClick=${saveEdit} disabled=${saving}>${saving?html`<span class="spin"></span>`:'Save'}</button>`:null}
              ${cu&&cu.role==='Admin'&&!edit?html`<button class="btn brd" style=${{fontSize:12,padding:'7px 12px'}} onClick=${delProject}>🗑</button>`:null}
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${onClose}>✕</button>
            </div>
          </div>
          ${edit?html`
            <div style=${{display:'flex',flexDirection:'column',gap:11,marginBottom:12}}>
              <textarea class="inp" rows="2" value=${desc} onInput=${e=>setDesc(e.target.value)}>${desc}</textarea>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div><label class="lbl">Target Date</label><input class="inp" type="date" value=${tDate} onChange=${e=>setTDate(e.target.value)}/></div>
                <div><label class="lbl">Color</label>
                  <div style=${{display:'flex',gap:7,flexWrap:'wrap',marginTop:4}}>
                    ${PAL.map(c=>html`<button key=${c} onClick=${()=>setColor(c)} style=${{width:26,height:26,borderRadius:6,background:c,border:'3px solid '+(color===c?'#fff':'transparent'),cursor:'pointer',transform:color===c?'scale(1.15)':'none'}}></button>`)}
                  </div>
                </div>
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
              <div style=${{display:'flex'}}>
                ${projUsers.slice(0,7).map((m,i)=>html`<div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-8:0,border:'2px solid var(--sf)',borderRadius:'50%',zIndex:7-i}}><${Av} u=${m} size=${24}/></div>`)}
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

      ${showNew?html`<${TaskModal} task=${null} onClose=${()=>setShowNew(false)} onSave=${saveTask} projects=${[project]} users=${projUsers.length?projUsers:allUsers} cu=${cu} defaultPid=${project.id} onSetReminder=${onSetReminder}/>`:null}
      ${editTask?html`<${TaskModal} task=${editTask} onClose=${()=>setEditTask(null)} onSave=${saveTask} onDel=${delTask} projects=${[project]} users=${projUsers.length?projUsers:allUsers} cu=${cu} defaultPid=${project.id} onSetReminder=${onSetReminder}/>`:null}
    </div>`;
}

/* ─── ProjectsView ────────────────────────────────────────────────────────── */
function ProjectsView({projects,tasks,users,cu,reload,onSetReminder}){
  const [showNew,setShowNew]=useState(false);const [detail,setDetail]=useState(null);
  const [name,setName]=useState('');const [desc,setDesc]=useState('');const [tDate,setTDate]=useState('');
  const [color,setColor]=useState('#c8f135');const [members,setMembers]=useState([]);const [err,setErr]=useState('');
  const [search,setSearch]=useState('');

  useEffect(()=>{if(detail){const fresh=safe(projects).find(p=>p.id===detail.id);if(fresh)setDetail(fresh);}},[projects]);

  const create=async()=>{
    if(!name.trim()){setErr('Project name required.');return;}setErr('');
    const mems=members.includes(cu.id)?members:[cu.id,...members];
    await api.post('/api/projects',{name:name.trim(),description:desc,targetDate:tDate,color,members:mems,startDate:new Date().toISOString().split('T')[0]});
    await reload();setShowNew(false);setName('');setDesc('');setTDate('');setColor('#c8f135');setMembers([]);
  };

  const filteredProjects=useMemo(()=>{
    if(!search.trim())return safe(projects);
    const q=search.toLowerCase();
    return safe(projects).filter(p=>p.name.toLowerCase().includes(q)||(p.description||'').toLowerCase().includes(q));
  },[projects,search]);

  return html`
    <div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px'}}>
      <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:14,gap:10,flexWrap:'wrap'}}>
        <div style=${{display:'flex',alignItems:'center',gap:9,flex:1,minWidth:200}}>
          <div style=${{position:'relative',flex:1,maxWidth:300}}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style=${{position:'absolute',left:9,top:'50%',transform:'translateY(-50%)',color:'var(--tx3)',pointerEvents:'none'}}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input class="inp" style=${{paddingLeft:30,height:34,fontSize:12}} placeholder="Search projects..." value=${search} onInput=${e=>setSearch(e.target.value)}/>
          </div>
          <span style=${{fontSize:12,color:'var(--tx3)',whiteSpace:'nowrap'}}>${filteredProjects.length} of ${safe(projects).length}</span>
        </div>
        ${cu&&cu.role!=='Viewer'?html`<button class="btn bp" onClick=${()=>setShowNew(true)}>+ New Project</button>`:null}
      </div>
      <div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(300px,1fr))',gap:15}}>
        ${filteredProjects.length===0?html`
          <div style=${{gridColumn:'1/-1',textAlign:'center',padding:'48px 0',color:'var(--tx3)'}}>
            <div style=${{fontSize:36,marginBottom:10}}>🔍</div>
            <div style=${{fontSize:14,fontWeight:600,color:'var(--tx2)',marginBottom:4}}>No projects match "${search}"</div>
            <button class="btn bg" style=${{fontSize:12,marginTop:8}} onClick=${()=>setSearch('')}>Clear search</button>
          </div>`:null}
        ${filteredProjects.map(p=>{
          const pt=safe(tasks).filter(t=>t.project===p.id);
          const done=pt.filter(t=>t.stage==='completed').length;
          const pc=pt.length?Math.round(pt.reduce((a,t)=>a+(t.pct||0),0)/pt.length):(p.progress||0);
          const mems=safe(p.members).map(id=>safe(users).find(u=>u.id===id)).filter(Boolean);
          return html`
            <div key=${p.id} class="card" style=${{cursor:'pointer',transition:'all .18s',borderTop:'3px solid '+p.color}}
              onClick=${()=>setDetail(p)}
              onMouseEnter=${e=>{e.currentTarget.style.transform='translateY(-2px)';e.currentTarget.style.boxShadow='0 8px 24px rgba(0,0,0,.2)';}}
              onMouseLeave=${e=>{e.currentTarget.style.transform='';e.currentTarget.style.boxShadow='';}}>
              <div style=${{display:'flex',alignItems:'flex-start',justifyContent:'space-between',marginBottom:10}}>
                <h3 style=${{fontSize:15,fontWeight:700,color:'var(--tx)',flex:1,marginRight:6}}>${p.name}</h3>
                <span class="badge" style=${{background:p.color+'22',color:p.color,flexShrink:0}}>${pt.length} tasks</span>
              </div>
              <p style=${{fontSize:13,color:'var(--tx2)',lineHeight:1.5,marginBottom:12,display:'-webkit-box',WebkitLineClamp:2,WebkitBoxOrient:'vertical',overflow:'hidden'}}>${p.description||'No description.'}</p>
              <div style=${{marginBottom:12}}>
                <div style=${{display:'flex',justifyContent:'space-between',marginBottom:5}}>
                  <span style=${{fontSize:11,color:'var(--tx3)'}}>Progress</span>
                  <span style=${{fontSize:11,color:'var(--tx2)',fontFamily:'monospace',fontWeight:700}}>${pc}%</span>
                </div>
                <${Prog} pct=${pc} color=${p.color}/>
              </div>
              <div style=${{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:7,marginBottom:12}}>
                ${[['Tasks',pt.length,'var(--tx)'],['Done',done,'var(--gn)'],['Open',pt.length-done,'var(--am)']].map(([l,v,c])=>html`
                  <div key=${l} style=${{textAlign:'center',padding:'9px 4px',background:'var(--sf2)',borderRadius:8,border:'1px solid var(--bd)'}}>
                    <div style=${{fontSize:18,fontWeight:800,color:c}}>${v}</div>
                    <div style=${{fontSize:10,color:'var(--tx3)',marginTop:2}}>${l}</div>
                  </div>`)}
              </div>
              <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                <div style=${{display:'flex'}}>
                  ${mems.slice(0,5).map((m,i)=>html`<div key=${m.id} title=${m.name} style=${{marginLeft:i>0?-7:0,border:'2px solid var(--sf)',borderRadius:'50%',zIndex:5-i}}><${Av} u=${m} size=${25}/></div>`)}
                </div>
                <span style=${{fontSize:11,color:'var(--tx3)',fontFamily:'monospace'}}>Due ${fmtD(p.target_date)}</span>
              </div>
            </div>`;
        })}
      </div>

      ${showNew?html`
        <div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowNew(false)}>
          <div class="mo fi" style=${{maxWidth:500}}>
            <div style=${{display:'flex',justifyContent:'space-between',marginBottom:18}}>
              <h2 style=${{fontSize:17,fontWeight:800,color:'var(--tx)'}}>New Project</h2>
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setShowNew(false)}>✕</button>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:12}}>
              <div><label class="lbl">Project Name *</label><input class="inp" placeholder="E.g. Mobile App Redesign" value=${name} onInput=${e=>setName(e.target.value)}/></div>
              <div><label class="lbl">Description</label><textarea class="inp" rows="3" placeholder="What is this project about?" onInput=${e=>setDesc(e.target.value)}>${desc}</textarea></div>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div><label class="lbl">Target Date</label><input class="inp" type="date" value=${tDate} onChange=${e=>setTDate(e.target.value)}/></div>
                <div><label class="lbl">Color</label>
                  <div style=${{display:'flex',gap:7,flexWrap:'wrap',marginTop:4}}>
                    ${PAL.map(c=>html`<button key=${c} onClick=${()=>setColor(c)} style=${{width:26,height:26,borderRadius:6,background:c,border:'3px solid '+(color===c?'#fff':'transparent'),cursor:'pointer',transform:color===c?'scale(1.15)':'none'}}></button>`)}
                  </div>
                </div>
              </div>
              <div><label class="lbl">Add Members</label><${MemberPicker} allUsers=${users} selected=${members} onChange=${setMembers}/></div>
              ${err?html`<div style=${{color:'var(--rd)',fontSize:12,padding:'7px 11px',background:'rgba(248,113,113,.07)',borderRadius:7}}>${err}</div>`:null}
              <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:4}}>
                <button class="btn bg" onClick=${()=>setShowNew(false)}>Cancel</button>
                <button class="btn bp" onClick=${create}>Create Project</button>
              </div>
            </div>
          </div>
        </div>`:null}
      ${detail?html`<${ProjectDetail} project=${detail} allTasks=${tasks} allUsers=${users} cu=${cu} onClose=${()=>setDetail(null)} onReload=${reload} onSetReminder=${onSetReminder}/>`:null}
    </div>`;
}

/* ─── TasksView with inline stage dropdown ────────────────────────────────── */
// SDLC stage → typical days from today & auto completion %
const STAGE_DAYS={backlog:0,planning:7,development:21,code_review:28,testing:35,uat:42,release:49,production:56,completed:60,blocked:0};
const STAGE_PCT={backlog:0,planning:10,development:35,code_review:55,testing:70,uat:80,release:90,production:95,completed:100,blocked:null};
function addDays(n){const d=new Date();d.setDate(d.getDate()+n);return d.toISOString().split('T')[0];}

function TasksView({tasks,projects,users,cu,reload,onSetReminder}){
  const [mode,setMode]=useState('kanban');
  const [pid,setPid]=useState('all');
  const [priF,setPriF]=useState('all');
  const [stageF,setStageF]=useState('all');
  const [assF,setAssF]=useState('all');
  const [dueF,setDueF]=useState('all'); // 'all','overdue','today','week','month'
  const [search,setSearch]=useState('');
  const [showFilters,setShowFilters]=useState(false);
  const [sortCol,setSortCol]=useState(null);  // 'assignee'|'priority'|'stage'|'due'|'pct'
  const [sortDir,setSortDir]=useState('asc'); // 'asc'|'desc'
  const [editT,setEditT]=useState(null);const [newT,setNewT]=useState(false);

  const activeFilters=[pid,priF,stageF,assF,dueF].filter(v=>v!=='all').length;
  const clearAll=()=>{setPid('all');setPriF('all');setStageF('all');setAssF('all');setDueF('all');setSearch('');};

  const filtered=useMemo(()=>{
    const today=new Date();today.setHours(0,0,0,0);
    const endOfWeek=new Date(today);endOfWeek.setDate(today.getDate()+7);
    const endOfMonth=new Date(today);endOfMonth.setDate(today.getDate()+30);
    return safe(tasks).filter(t=>{
      if(pid!=='all'&&t.project!==pid)return false;
      if(priF!=='all'&&t.priority!==priF)return false;
      if(stageF!=='all'&&t.stage!==stageF)return false;
      if(assF!=='all'&&t.assignee!==assF)return false;
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
  },[tasks,pid,priF,stageF,assF,dueF,search]);

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
          ${activeFilters>0?html`<button class="btn bam" style=${{padding:'7px 11px',fontSize:11}} onClick=${clearAll}>✕ Clear</button>`:null}
          <div style=${{display:'flex',background:'var(--sf2)',borderRadius:9,padding:3,gap:2,flex:'0 0 auto'}}>
            <button class=${'tb'+(mode==='kanban'?' act':'')} onClick=${()=>setMode('kanban')}>⊞ Board</button>
            <button class=${'tb'+(mode==='list'?' act':'')} onClick=${()=>setMode('list')}>☰ List</button>
          </div>
          <button class="btn bp" style=${{flex:'0 0 auto',fontSize:12,padding:'7px 13px'}} onClick=${()=>setNewT(true)}>+ New Task</button>
        </div>
        ${showFilters?html`
          <div style=${{display:'flex',gap:8,flexWrap:'wrap',marginTop:9,paddingTop:9,borderTop:'1px solid var(--bd)'}}>
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Project</label>
              <select class="sel" style=${{width:155,fontSize:12}} value=${pid} onChange=${e=>setPid(e.target.value)}>
                <option value="all">All Projects</option>
                ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
              </select>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:3}}>
              <label style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace',textTransform:'uppercase',letterSpacing:.5}}>Assignee</label>
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
                    return html`<div key=${tk.id} class="tkc" onClick=${()=>setEditT(tk)}>
                      <div style=${{display:'flex',justifyContent:'space-between',marginBottom:5}}><span style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace'}}>${tk.id}</span><${PB} p=${tk.priority}/></div>
                      <p style=${{fontSize:13,fontWeight:500,color:'var(--tx)',marginBottom:6,lineHeight:1.4}}>${tk.title}</p>
                      ${tk.pct>0?html`<div style=${{marginBottom:6}}><${Prog} pct=${tk.pct} color=${si.color}/></div>`:null}
                      <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                        ${au?html`<${Av} u=${au} size=${20}/>`:html`<div style=${{width:20,height:20,borderRadius:'50%',background:'var(--bd)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:9,color:'var(--tx3)'}}>?</div>`}
                        ${tk.due?html`<span style=${{fontSize:9,color:'var(--tx3)',fontFamily:'monospace'}}>${fmtD(tk.due)}</span>`:null}
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

      ${editT?html`<${TaskModal} task=${editT} onClose=${()=>setEditT(null)} onSave=${saveT} onDel=${delT} projects=${projects} users=${users} cu=${cu} onSetReminder=${onSetReminder}/>`:null}
      ${newT?html`<${TaskModal} task=${null} onClose=${()=>setNewT(false)} onSave=${saveT} projects=${projects} users=${users} cu=${cu} onSetReminder=${onSetReminder}/>`:null}
    </div>`;
}

/* ─── Dashboard ───────────────────────────────────────────────────────────── */
function Dashboard({cu,tasks,projects,users,onNav}){
  const t=safe(tasks);const p=safe(projects);const u=safe(users);
  const myT=t.filter(x=>x.assignee===cu.id);
  const done=t.filter(x=>x.stage==='completed').length;
  const active=t.filter(x=>x.stage!=='completed'&&x.stage!=='backlog').length;
  const blocked=t.filter(x=>x.stage==='blocked').length;
  const stageChart=Object.entries(STAGES).map(([k,v])=>({name:v.label,count:t.filter(x=>x.stage===k).length,color:v.color})).filter(d=>d.count>0);
  const activeProjectIds=new Set(p.map(proj=>proj.id));
  const activeTasks=t.filter(x=>activeProjectIds.has(x.project)&&x.stage!=='completed');
  const priChart=[{name:'Critical',value:activeTasks.filter(x=>x.priority==='critical').length,color:'var(--rd)'},{name:'High',value:activeTasks.filter(x=>x.priority==='high').length,color:'var(--rd2)'},{name:'Medium',value:activeTasks.filter(x=>x.priority==='medium').length,color:'var(--pu)'},{name:'Low',value:activeTasks.filter(x=>x.priority==='low').length,color:'var(--cy)'}];
  const stats=[
    {label:'Total Projects',val:p.length,   color:'var(--ac)',bg:'rgba(200,241,53,.1)', icon:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`,nav:'projects'},
    {label:'Active Tasks',  val:active,     color:'var(--cy)',bg:'rgba(34,211,238,.1)', icon:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,nav:'tasks'},
    {label:'Completed',     val:done,       color:'var(--gn)',bg:'rgba(74,222,128,.1)', icon:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,nav:'tasks'},
    {label:'Blocked',       val:blocked,    color:'var(--rd)',bg:'rgba(248,113,113,.1)',icon:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>`,nav:'tasks'},
    {label:'My Tasks',      val:myT.length, color:'var(--am)',bg:'rgba(251,191,36,.1)', icon:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,nav:'tasks'},
    {label:'Team Members',  val:u.length,   color:'var(--pu)',bg:'rgba(167,139,250,.1)',icon:html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,nav:'team'},
  ];
  return html`
    <div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px',display:'flex',flexDirection:'column',gap:16}}>
      <div style=${{padding:'14px 18px',background:'var(--sf)',borderRadius:18,boxShadow:'var(--sh)',border:'none',display:'flex',alignItems:'center',gap:13}}>
        <${Av} u=${cu} size=${42}/>
        <div style=${{flex:1}}>
          <h2 style=${{fontSize:17,fontWeight:800,color:'var(--tx)',fontFamily:"'Syne',sans-serif"}}>Good day, ${(cu&&cu.name||'there').split(' ')[0]}! 👋</h2>
          <p style=${{color:'var(--tx2)',fontSize:13,marginTop:2}}>You have <b style=${{color:'var(--ac2)'}}>${myT.filter(x=>x.stage!=='completed').length}</b> active tasks across <b style=${{color:'var(--ac2)'}}>${new Set(myT.map(x=>x.project)).size}</b> projects.</p>
        </div>
        <div style=${{display:'flex',alignItems:'center',gap:7,background:'rgba(200,241,53,.1)',border:'1px solid rgba(200,241,53,.18)',borderRadius:8,padding:'4px 11px',backdropFilter:'blur(4px)'}}><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style=${{color:'var(--ac)',opacity:.8}}><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg><span style=${{fontSize:11,fontWeight:600,color:'var(--ac2)',fontFamily:'monospace',letterSpacing:'.3px'}}>${new Date().toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'})}</span></div>
      </div>
      <div style=${{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:12}}>
        ${stats.map((s,i)=>html`
          <div key=${i} class="card" onClick=${()=>onNav(s.nav)}
            style=${{padding:'13px 15px',position:'relative',overflow:'hidden',cursor:'pointer',transition:'all .18s'}}
            onMouseEnter=${e=>{e.currentTarget.style.transform='translateY(-2px)';e.currentTarget.style.borderColor=s.color;e.currentTarget.style.boxShadow='var(--sh2)';}}
            onMouseLeave=${e=>{e.currentTarget.style.transform='';e.currentTarget.style.borderColor='';e.currentTarget.style.boxShadow='';}}>
            <div style=${{position:'absolute',top:0,left:0,right:0,height:3,background:s.color,borderRadius:'18px 18px 0 0',opacity:.7}}></div>
            <div style=${{width:34,height:34,borderRadius:9,background:s.bg,display:'flex',alignItems:'center',justifyContent:'center',marginBottom:8,color:s.color}}>${s.icon}</div>
            <div style=${{fontSize:26,fontWeight:800,color:'var(--tx)',lineHeight:1,fontFamily:"'Syne',sans-serif",letterSpacing:-1}}>${s.val}</div>
            <div style=${{fontSize:11,color:'var(--tx2)',marginTop:4}}>${s.label}</div>
            <div style=${{fontSize:9,color:s.color,marginTop:2,fontFamily:'monospace',opacity:.7}}>click →</div>
          </div>`)}
      </div>
      <div style=${{display:'grid',gridTemplateColumns:'1fr 260px',gap:14}}>
        <div class="card">
          <h3 style=${{fontSize:13,fontWeight:800,color:'var(--tx)',marginBottom:13,fontFamily:"'Syne',sans-serif"}}>Tasks by Lifecycle Stage</h3>
          <${RC.ResponsiveContainer} width="100%" height=${180}>
            <${RC.BarChart} data=${stageChart} barSize=${18} margin=${{top:0,right:0,bottom:0,left:-20}}>
              <${RC.CartesianGrid} strokeDasharray="3 3" stroke="var(--bd)" vertical=${false}/>
              <${RC.XAxis} dataKey="name" tick=${{fill:'var(--tx2)',fontSize:10,fontFamily:'monospace'}} axisLine=${false} tickLine=${false}/>
              <${RC.YAxis} tick=${{fill:'var(--tx3)',fontSize:10}} axisLine=${false} tickLine=${false} allowDecimals=${false} domain=${[0,'dataMax+1']}/>
              <${RC.Tooltip} contentStyle=${{background:'#fff',border:'none',borderRadius:12,color:'#111',fontSize:12,boxShadow:'0 4px 20px rgba(0,0,0,.12)'}}/>
              <${RC.Bar} dataKey="count" radius=${[4,4,0,0]}>${stageChart.map((e,i)=>html`<${RC.Cell} key=${i} fill=${e.color}/>`)}<//>
            <//>
          <//>
        </div>
        <div class="card">
          <h3 style=${{fontSize:13,fontWeight:800,color:'var(--tx)',marginBottom:11,fontFamily:"'Syne',sans-serif"}}>Priority Split</h3>
          <${RC.ResponsiveContainer} width="100%" height=${120}>
            <${RC.PieChart}>
              <${RC.Pie} data=${priChart} cx="50%" cy="50%" innerRadius=${34} outerRadius=${52} dataKey="value" paddingAngle=${4}>
                ${priChart.map((e,i)=>html`<${RC.Cell} key=${i} fill=${e.color}/>`)}<//>
              <${RC.Tooltip} contentStyle=${{background:'#fff',border:'none',borderRadius:12,color:'#111',fontSize:12,boxShadow:'0 4px 20px rgba(0,0,0,.12)'}}/>
            <//>
          <//>
          ${priChart.map((item,i)=>html`<div key=${i} style=${{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'5px 0',borderBottom:i<2?'1px solid var(--bd)':'none'}}>
            <div style=${{display:'flex',alignItems:'center',gap:7}}><div style=${{width:7,height:7,borderRadius:2,background:item.color}}></div><span style=${{fontSize:12,color:'var(--tx2)'}}>${item.name}</span></div>
            <span style=${{fontSize:12,color:'var(--tx)',fontFamily:'monospace',fontWeight:700}}>${item.value}</span>
          </div>`)}
        </div>
      </div>
      <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>
        <div class="card">
          <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',marginBottom:12}}>Project Progress</h3>
          ${p.map(proj=>{
            const pt=t.filter(x=>x.project===proj.id);const pc=pt.length?Math.round(pt.reduce((a,x)=>a+(x.pct||0),0)/pt.length):(proj.progress||0);
            return html`<div key=${proj.id} style=${{marginBottom:11}}><div style=${{display:'flex',justifyContent:'space-between',marginBottom:4}}><div style=${{display:'flex',alignItems:'center',gap:6}}><div style=${{width:7,height:7,borderRadius:2,background:proj.color}}></div><span style=${{fontSize:13,color:'var(--tx)',fontWeight:500}}>${proj.name}</span></div><span style=${{fontSize:11,color:'var(--tx2)',fontFamily:'monospace'}}>${pc}%</span></div><${Prog} pct=${pc} color=${proj.color}/></div>`;
          })}
        </div>
        <div class="card">
          <h3 style=${{fontSize:13,fontWeight:700,color:'var(--tx)',marginBottom:12}}>My Recent Tasks</h3>
          ${myT.slice(0,6).map((tk,i)=>html`<div key=${tk.id} style=${{display:'flex',gap:9,padding:'7px 0',borderBottom:i<Math.min(myT.length,6)-1?'1px solid var(--bd)':'none',alignItems:'center'}}>
            <div style=${{width:6,height:6,borderRadius:2,background:(STAGES[tk.stage]&&STAGES[tk.stage].color)||'var(--ac)',flexShrink:0}}></div>
            <div style=${{flex:1,minWidth:0}}><div style=${{fontSize:13,color:'var(--tx)',fontWeight:500,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${tk.title}</div><div style=${{display:'flex',gap:5,marginTop:2}}><${SP} s=${tk.stage}/><${PB} p=${tk.priority}/></div></div>
            <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${tk.pct}%</span>
          </div>`)}
          ${myT.length===0?html`<div style=${{color:'var(--tx3)',fontSize:13,textAlign:'center',paddingTop:16}}>No tasks assigned.</div>`:null}
        </div>
      </div>
    </div>`;
}

/* ─── MessagesView ────────────────────────────────────────────────────────── */
function renderMd(text){
  return text.replace(/[*][*](.*?)[*][*]/g,'<b>$1</b>');
}
function MessagesView({projects,users,cu}){
  const [pid,setPid]=useState((safe(projects)[0]&&safe(projects)[0].id)||'');
  const [msgs,setMsgs]=useState([]);const [txt,setTxt]=useState('');const ref=useRef(null);

  const loadMsgs=useCallback(async(id)=>{
    if(!id)return;
    const d=await api.get('/api/messages?project='+id);
    if(Array.isArray(d)) setMsgs(d);
  },[]);

  // Load on channel change
  useEffect(()=>{loadMsgs(pid);},[pid]);

  // Auto-poll every 4s for new channel messages
  useEffect(()=>{
    if(!pid)return;
    const id=setInterval(()=>{
      api.get('/api/messages?project='+pid).then(d=>{
        if(Array.isArray(d)){
          setMsgs(prev=>{
            if(d.length>prev.length) playSound('notif');
            return d;
          });
        }
      });
    },4000);
    return()=>clearInterval(id);
  },[pid]);

  useEffect(()=>{if(ref.current)ref.current.scrollTop=ref.current.scrollHeight;},[msgs]);
  const sp=safe(projects).find(p=>p.id===pid);
  const send=async()=>{
    if(!txt.trim())return;const c=txt.trim();setTxt('');
    const m=await api.post('/api/messages',{project:pid,content:c});
    setMsgs(prev=>[...prev,m]);
  };

  return html`<div class="fi" style=${{display:'flex',height:'100%',overflow:'hidden'}}>
    <div style=${{width:210,borderRight:'1px solid var(--bd)',display:'flex',flexDirection:'column',flexShrink:0}}>
      <div style=${{padding:'11px 12px',borderBottom:'1px solid var(--bd)'}}><span style=${{fontSize:10,fontWeight:700,color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.7}}>Channels</span></div>
      <div style=${{flex:1,overflowY:'auto',padding:6}}>
        ${safe(projects).map(p=>html`<button key=${p.id} class=${'nb'+(pid===p.id?' act':'')} style=${{marginBottom:2,fontSize:12}} onClick=${()=>setPid(p.id)}>
          <div style=${{width:7,height:7,borderRadius:2,background:p.color,flexShrink:0}}></div>
          <span style=${{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}># ${p.name}</span>
        </button>`)}
      </div>
    </div>
    <div style=${{flex:1,display:'flex',flexDirection:'column',overflow:'hidden'}}>
      <div style=${{padding:'11px 15px',borderBottom:'1px solid var(--bd)',display:'flex',alignItems:'center',gap:9,flexShrink:0}}>
        ${sp?html`<div style=${{width:8,height:8,borderRadius:2,background:sp.color}}></div>`:null}
        <span style=${{fontSize:14,fontWeight:700,color:'var(--tx)'}}>${sp?'# '+sp.name:'Select a channel'}</span>
        ${sp?html`<span style=${{fontSize:11,color:'var(--tx3)',marginLeft:'auto'}}>Tasks & comments auto-post here</span>`:null}
      </div>
      <div ref=${ref} style=${{flex:1,overflowY:'auto',padding:'13px 15px',display:'flex',flexDirection:'column',gap:8}}>
        ${msgs.map(m=>{
          const isSystem=m.is_system===1||m.sender==='system';
          if(isSystem) return html`
            <div key=${m.id} style=${{display:'flex',justifyContent:'center',padding:'4px 0'}}>
              <div style=${{fontSize:11,color:'var(--tx3)',background:'var(--sf2)',border:'1px solid var(--bd)',borderRadius:20,padding:'4px 14px',maxWidth:'80%',textAlign:'center'}}
                dangerouslySetInnerHTML=${{__html:renderMd(m.content)}}></div>
            </div>`;
          const s=safe(users).find(u=>u.id===m.sender);const isMe=m.sender===cu.id;
          return html`
            <div key=${m.id} style=${{display:'flex',gap:8,alignItems:'flex-end',flexDirection:isMe?'row-reverse':'row'}}>
              ${!isMe?html`<${Av} u=${s} size=${25}/>`:null}
              <div style=${{display:'flex',flexDirection:'column',gap:3,alignItems:isMe?'flex-end':'flex-start',maxWidth:'65%'}}>
                ${!isMe?html`<span style=${{fontSize:11,color:'var(--tx3)',fontWeight:600,marginLeft:2}}>${(s&&s.name)||'?'}</span>`:null}
                <div style=${{padding:'9px 13px',borderRadius:12,fontSize:14,lineHeight:1.5,
                  background:isMe?'var(--ac)':'var(--sf2)',color:isMe?'var(--ac-tx)':'var(--tx)',
                  border:isMe?'none':'1px solid var(--bd)',
                  borderBottomRightRadius:isMe?3:12,borderBottomLeftRadius:isMe?12:3}}>${m.content}</div>
                <span style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${ago(m.ts)}</span>
              </div>
            </div>`;
        })}
        ${msgs.length===0?html`<div style=${{textAlign:'center',paddingTop:48,color:'var(--tx3)',fontSize:13}}>
          <div style=${{fontSize:28,marginBottom:8}}>💬</div>
          <p>No messages yet.</p>
          <p style=${{fontSize:11,marginTop:6}}>Task activity will appear here automatically.</p>
        </div>`:null}
      </div>
      <div style=${{padding:'10px 15px',borderTop:'1px solid var(--bd)',display:'flex',gap:8,flexShrink:0}}>
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
            <div style=${{flex:1,minWidth:0,textAlign:'left'}}><div style=${{fontSize:13,fontWeight:600,color:isA?'var(--ac2)':'var(--tx)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${u.name}</div><div style=${{fontSize:10,color:'var(--tx3)',fontFamily:'monospace'}}>${u.role}</div></div>
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
              <div style=${{padding:'9px 13px',borderRadius:14,fontSize:14,lineHeight:1.55,wordBreak:'break-word',background:isMe?'var(--ac)':'var(--sf2)',color:isMe?'var(--ac-tx)':'var(--tx)',border:isMe?'none':'1px solid var(--bd)',borderBottomRightRadius:isMe?3:14,borderBottomLeftRadius:isMe?14:3}}>${m.content}</div>
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
  return html`<div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px'}}>
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
function TeamView({users,cu,reload}){
  const [showNew,setShowNew]=useState(false);const [name,setName]=useState('');const [email,setEmail]=useState('');const [pw,setPw]=useState('');const [role,setRole]=useState('Developer');const [err,setErr]=useState('');
  const add=async()=>{if(!name||!email||!pw){setErr('All fields required.');return;}setErr('');const r=await api.post('/api/users',{name,email,password:pw,role});if(r.error)setErr(r.error);else{await reload();setShowNew(false);setName('');setEmail('');setPw('');}};
  return html`<div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px'}}>
    <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
      <span style=${{fontSize:13,color:'var(--tx2)'}}>${safe(users).length} members in workspace</span>
      <button class="btn bp" onClick=${()=>setShowNew(true)}>+ Add Member</button>
    </div>
    <div class="card" style=${{padding:0,overflow:'hidden',maxWidth:820}}>
      <table style=${{width:'100%',borderCollapse:'collapse'}}>
        <thead><tr style=${{borderBottom:'1px solid var(--bd)',background:'var(--sf2)'}}>
          ${['Member','Email','Role',''].map((h,i)=>html`<th key=${i} style=${{padding:'9px 15px',textAlign:'left',fontSize:10,fontFamily:'monospace',color:'var(--tx3)',textTransform:'uppercase',letterSpacing:.5}}>${h}</th>`)}
        </tr></thead>
        <tbody>
          ${safe(users).map((u,i)=>html`<tr key=${u.id} style=${{borderBottom:i<safe(users).length-1?'1px solid var(--bd)':'none'}}>
            <td style=${{padding:'11px 15px'}}><div style=${{display:'flex',alignItems:'center',gap:10}}><${Av} u=${u} size=${32}/><div><div style=${{fontSize:13,fontWeight:600,color:'var(--tx)',display:'flex',alignItems:'center',gap:6}}>${u.name}${u.id===cu.id?html`<span style=${{fontSize:9,color:'var(--ac)',background:'rgba(99,102,241,.14)',padding:'2px 6px',borderRadius:4,fontFamily:'monospace'}}>YOU</span>`:null}</div></div></div></td>
            <td style=${{padding:'11px 15px'}}><span style=${{fontSize:12,color:'var(--tx2)',fontFamily:'monospace'}}>${u.email}</span></td>
            <td style=${{padding:'11px 15px'}}><select class="sel" style=${{width:130,padding:'6px 28px 6px 10px'}} value=${u.role} onChange=${e=>api.put('/api/users/'+u.id,{role:e.target.value}).then(reload)} disabled=${u.id===cu.id}>${ROLES.map(r=>html`<option key=${r}>${r}</option>`)}</select></td>
            <td style=${{padding:'11px 15px'}}>${u.id!==cu.id?html`<button class="btn brd" style=${{padding:'5px 11px',fontSize:12}} onClick=${()=>window.confirm('Remove '+u.name+'?')&&api.del('/api/users/'+u.id).then(reload)}>🗑 Remove</button>`:null}</td>
          </tr>`)}
        </tbody>
      </table>
    </div>
    ${showNew?html`<div class="ov" onClick=${e=>e.target===e.currentTarget&&setShowNew(false)}>
      <div class="mo fi" style=${{maxWidth:400}}>
        <div style=${{display:'flex',justifyContent:'space-between',marginBottom:18}}><h2 style=${{fontSize:17,fontWeight:800,color:'var(--tx)'}}>Add Member</h2><button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setShowNew(false)}>✕</button></div>
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
  </div>`;
}

/* ─── WorkspaceSettings ───────────────────────────────────────────────────── */
function WorkspaceSettings({cu,onReload}){
  const [ws,setWs]=useState(null);const [wsName,setWsName]=useState('');const [aiKey,setAiKey]=useState('');const [showKey,setShowKey]=useState(false);const [saving,setSaving]=useState(false);const [saved,setSaved]=useState(false);

  useEffect(()=>{api.get('/api/workspace').then(d=>{if(!d.error){setWs(d);setWsName(d.name||'');setAiKey(d.ai_api_key?'•'.repeat(20):'');}});},[]);

  const save=async()=>{
    setSaving(true);
    const payload={name:wsName};
    if(aiKey&&!aiKey.startsWith('•'))payload.ai_api_key=aiKey;
    await api.put('/api/workspace',payload);
    setSaving(false);setSaved(true);setTimeout(()=>setSaved(false),2000);
    await onReload();
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
      <h2 style=${{fontSize:17,fontWeight:800,color:'var(--tx)',marginBottom:20}}>⚙ Workspace Settings</h2>

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
          <div style=${{flex:1,textAlign:'center',padding:'14px',background:'linear-gradient(135deg,rgba(200,241,53,.12),rgba(167,139,250,.08))',borderRadius:12,border:'1px solid rgba(200,241,53,.18)'}}>
            <div style=${{fontSize:28,fontWeight:800,color:'var(--ac2)',fontFamily:'monospace',letterSpacing:4}}>${ws.invite_code}</div>
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
        <div style=${{marginTop:10,padding:'9px 12px',background:'rgba(99,102,241,.07)',borderRadius:8,border:'1px solid rgba(200,241,53,.15)',fontSize:12,color:'var(--tx2)'}}>
          💡 Get your API key at <b style=${{color:'var(--ac2)'}}>console.anthropic.com</b>. The AI can answer questions, create tasks, update statuses, and generate EOD reports.
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
          <div style=${{width:32,height:32,background:'linear-gradient(135deg,#c8f135,#9b8ef4)',borderRadius:9,display:'flex',alignItems:'center',justifyContent:'center',fontSize:16}}>🤖</div>
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
      document.title=count>0?'('+count+') ProjectFlow':'ProjectFlow';
    };
    img.src=NOTIF_ICON;
  }catch(e){}
}

function requestNotifPermission(){
  if('Notification' in window && Notification.permission==='default'){
    Notification.requestPermission().then(perm=>{
      if(perm==='granted'){
        new Notification('ProjectFlow Notifications Enabled',{
          body:'You\'ll get notified for calls, messages, and project updates.',
          icon:NOTIF_ICON,silent:true
        });
      }
    });
  }
}

function showBrowserNotif(title, body, onClick, opts={}){
  if(!('Notification' in window)||Notification.permission!=='granted')return;
  try{
    const n=new Notification(title,{
      body,
      icon:NOTIF_ICON,
      badge:NOTIF_ICON,
      tag:opts.tag||'pf-'+Date.now(),
      requireInteraction:opts.requireInteraction||false,
      silent:false,
    });
    if(onClick) n.onclick=()=>{window.focus();onClick();n.close();};
    if(!opts.requireInteraction) setTimeout(()=>n.close(),7000);
  }catch(e){}
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
          <h2 style=${{fontSize:17,fontWeight:800,color:'var(--tx)'}}>⏰ Set Reminder</h2>
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
function RemindersView({cu,tasks,projects,onSetReminder,onReload}){
  const [reminders,setReminders]=useState([]);
  const [busy,setBusy]=useState(true);
  const [showAdd,setShowAdd]=useState(false);
  const [addTaskId,setAddTaskId]=useState('');
  const [addDate,setAddDate]=useState('');
  const [addTime,setAddTime]=useState('');
  const [addMins,setAddMins]=useState(10);
  const [saving,setSaving]=useState(false);
  const [addProjId,setAddProjId]=useState('');
  const now=new Date();
  const filteredTasks=addProjId?safe(tasks).filter(t=>t.project===addProjId):safe(tasks);

  const load=useCallback(async()=>{
    setBusy(true);
    const d=await api.get('/api/reminders');
    setReminders(Array.isArray(d)?d:[]);
    setBusy(false);
  },[]);

  useEffect(()=>{load();},[load]);

  const del=async id=>{await api.del('/api/reminders/'+id);load();onReload&&onReload();};

  const saveReminder=async()=>{
    if(!addTaskId||!addDate||!addTime){return;}
    setSaving(true);
    const dt=new Date(addDate+'T'+addTime);
    const task=safe(tasks).find(t=>t.id===addTaskId);
    await api.post('/api/reminders',{task_id:addTaskId,task_title:(task&&task.title)||'Reminder',remind_at:dt.toISOString(),minutes_before:addMins});
    setSaving(false);
    setShowAdd(false);
    setAddTaskId('');setAddDate('');setAddTime('');setAddMins(10);
    load();
  };

  const upcoming=reminders.filter(r=>new Date(r.remind_at)>=now).sort((a,b)=>new Date(a.remind_at)-new Date(b.remind_at));
  const overdue=reminders.filter(r=>new Date(r.remind_at)<now).sort((a,b)=>new Date(b.remind_at)-new Date(a.remind_at));

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
    {label:'Total',val:reminders.length,color:'var(--ac)',bg:'rgba(200,241,53,.1)',icon:'⏰'},
    {label:'Upcoming',val:upcoming.length,color:'var(--cy)',bg:'rgba(34,211,238,.1)',icon:'⚡'},
    {label:'Overdue',val:overdue.length,color:'var(--rd)',bg:'rgba(248,113,113,.1)',icon:'🚨'},
    {label:'Today',val:reminders.filter(r=>{const d=new Date(r.remind_at);return d.toDateString()===now.toDateString();}).length,color:'var(--gn)',bg:'rgba(74,222,128,.1)',icon:'📅'},
  ];

  return html`
    <div class="fi" style=${{height:'100%',overflowY:'auto',padding:'18px 22px',background:'var(--bg)'}}>

      <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
        <div style=${{fontSize:13,color:'var(--tx3)'}}>Set reminders for your tasks — get notified with sound before they're due.</div>
        <button class="btn bp" style=${{fontSize:12}} onClick=${()=>setShowAdd(true)}>+ Add Reminder</button>
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
          <div class="mo fi" style=${{maxWidth:460}}>
            <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:18}}>
              <h2 style=${{fontSize:16,fontWeight:800,color:'var(--tx)'}}>⏰ Add Reminder</h2>
              <button class="btn bg" style=${{padding:'7px 10px'}} onClick=${()=>setShowAdd(false)}>✕</button>
            </div>
            <div style=${{display:'flex',flexDirection:'column',gap:13}}>
              <div>
                <label class="lbl">Project (optional filter)</label>
                <select class="inp" value=${addProjId} onChange=${e=>{setAddProjId(e.target.value);setAddTaskId('');}}>
                  <option value="">— All projects —</option>
                  ${safe(projects).map(p=>html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
                </select>
              </div>
              <div>
                <label class="lbl">Task *</label>
                <select class="inp" value=${addTaskId} onChange=${e=>setAddTaskId(e.target.value)}>
                  <option value="">— Select a task —</option>
                  ${filteredTasks.map(t=>html`<option key=${t.id} value=${t.id}>${t.title}</option>`)}
                </select>
              </div>
              <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:11}}>
                <div>
                  <label class="lbl">Date *</label>
                  <input class="inp" type="date" value=${addDate} onChange=${e=>setAddDate(e.target.value)} min=${new Date().toISOString().split('T')[0]}/>
                </div>
                <div>
                  <label class="lbl">Time *</label>
                  <input class="inp" type="time" value=${addTime} onChange=${e=>setAddTime(e.target.value)}/>
                </div>
              </div>
              <div>
                <label class="lbl">Notify me before</label>
                <div style=${{display:'flex',gap:8,flexWrap:'wrap',marginTop:4}}>
                  ${[5,10,15,30,60].map(m=>html`
                    <button key=${m} class=${'chip'+(addMins===m?' on':'')} onClick=${()=>setAddMins(m)} style=${{fontSize:12,padding:'5px 12px'}}>
                      ${m<60?m+' min':'1 hr'}
                    </button>`)}
                </div>
              </div>
              <div style=${{background:'rgba(200,241,53,.06)',borderRadius:9,padding:'10px 13px',fontSize:12,color:'var(--tx2)',border:'1px solid rgba(200,241,53,.15)'}}>
                🔔 You'll get a browser notification + sound ${addMins} min before the reminder time.
              </div>
              <div style=${{display:'flex',gap:9,justifyContent:'flex-end',paddingTop:4}}>
                <button class="btn bg" onClick=${()=>setShowAdd(false)}>Cancel</button>
                <button class="btn bp" onClick=${saveReminder} disabled=${saving||!addTaskId||!addDate||!addTime}>
                  ${saving?'Saving...':'Set Reminder'}
                </button>
              </div>
            </div>
          </div>
        </div>`:null}

      <div style=${{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
        <div>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
            <span style=${{fontWeight:800,fontSize:13,color:'var(--tx)'}}>⚡ Upcoming</span>
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
                    </div>
                  </div>
                  <button class="btn brd" style=${{fontSize:10,padding:'4px 8px',flexShrink:0}} onClick=${()=>del(r.id)}>✕</button>
                </div>`;
            })}
          </div>
        </div>
        <div>
          <div style=${{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
            <span style=${{fontWeight:800,fontSize:13,color:'var(--rd)'}}>🚨 Overdue</span>
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
          <h2 style=${{fontSize:17,fontWeight:800,color:'var(--tx)'}}>⏰ My Reminders</h2>
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
  const [muted,setMuted]=useState(false);
  const [videoOn,setVideoOn]=useState(false);
  const [elapsed,setElapsed]=useState(0);
  const [incomingCall,setIncomingCall]=useState(null);
  const [targetUser,setTargetUser]=useState(null);
  const [speaking,setSpeaking]=useState({});
  const [handRaised,setHandRaised]=useState(false);
  const [screenSharing,setScreenSharing]=useState(false);
  const [showParticipants,setShowParticipants]=useState(false);
  const [showInvite,setShowInvite]=useState(false);
  const [previewMicOk,setPreviewMicOk]=useState(true);
  const [minimized,setMinimized]=useState(false);
  const [popupPos,setPopupPos]=useState({x:null,y:null});
  const [dragging,setDragging]=useState(false);
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
        if(parts.includes(cu.id)){setIncomingCall(null);return;}
        if(c.id===lastId)return; // don't re-notify same call
        lastId=c.id;
        const init=safe(users).find(u=>u.id===c.initiator);
        setIncomingCall({id:c.id,name:c.name,initiatorName:(init&&init.name)||'Someone',initiator:init});
        // Browser notification for incoming call
        showBrowserNotif('📞 Incoming Huddle',(init?init.name:'Someone')+' started a Huddle',null,{requireInteraction:true,tag:'call-'+c.id});
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
        // Set on video el — use ref callback approach
        const el=remoteVideoRefs.current[remoteUid];
        if(el){el.srcObject=stream;el.play().catch(()=>{});}
        else{
          // Retry after DOM update
          setTimeout(()=>{const el2=remoteVideoRefs.current[remoteUid];if(el2){el2.srcObject=stream;el2.play().catch(()=>{});}},500);
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
            if(pc&&pc.remoteDescription){try{await pc.addIceCandidate(new RTCIceCandidate(data));}catch(ex){}}
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
    playSound('notif');startSignalPoll(r.room_id);startPing(r.room_id);startTimer();
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
    setTargetUser(null);setMinimized(false);setShowInvite(false);
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
        const ss=await navigator.mediaDevices.getDisplayMedia({video:{cursor:'always'},audio:false});
        screenStream.current=ss;
        if(screenVideoRef.current){screenVideoRef.current.srcObject=ss;screenVideoRef.current.play().catch(()=>{});}
        ss.getVideoTracks()[0].onended=()=>{setScreenSharing(false);screenStream.current=null;if(screenVideoRef.current)screenVideoRef.current.srcObject=null;};
        // Send to peers
        for(const [uid,pc] of Object.entries(pcs.current)){
          ss.getTracks().forEach(t=>pc.addTrack(t,ss));
          try{
            const offer=await pc.createOffer();
            await pc.setLocalDescription(offer);
            await api.post('/api/calls/'+roomIdRef.current+'/signal',{to_user:uid,type:'offer',data:{type:offer.type,sdp:offer.sdp}});
          }catch(ex){}
        }
        setScreenSharing(true);
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

  const partUsers=participants.map(id=>safe(users).find(u=>u.id===id)||{id,name:'?',avatar:'?',color:'#c8f135'});
  const notInCall=safe(users).filter(u=>u.id!==cu.id&&!participants.includes(u.id));

  // ── INCOMING CALL TOAST
  const incomingToast=incomingCall&&phase==='idle'?html`
    <div style=${{position:'fixed',bottom:24,right:24,zIndex:9100,background:'#1a1625',border:'1px solid rgba(34,197,94,.35)',borderRadius:18,padding:'16px 18px',boxShadow:'0 16px 60px rgba(0,0,0,.7)',minWidth:300,animation:'slideUp .3s cubic-bezier(.2,.8,.4,1)'}}>
      <div style=${{display:'flex',alignItems:'center',gap:11,marginBottom:14}}>
        <div style=${{position:'relative',flexShrink:0}}>
          ${incomingCall.initiator?html`<${Av} u=${incomingCall.initiator} size=${44}/>`:
            html`<div style=${{width:44,height:44,borderRadius:14,background:'linear-gradient(135deg,#22c55e,#16a34a)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:18,fontWeight:800,color:'#fff'}}>${(incomingCall.initiatorName||'?')[0]}</div>`}
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
              html`<div style=${{width:80,height:80,borderRadius:'50%',background:cu.color||'#c8f135',display:'flex',alignItems:'center',justifyContent:'center',fontSize:28,fontWeight:800,color:'#fff',border:'3px solid rgba(255,255,255,.15)'}}>${(cu.avatar||cu.name||'?')[0]}</div>`}
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
    <div style=${{position:'fixed',left:(popupPos&&popupPos.x||100)+'px',top:(popupPos&&popupPos.y||60)+'px',width:minimized?'240px':'780px',height:minimized?'auto':'540px',zIndex:8600,borderRadius:minimized?14:20,overflow:'hidden',boxShadow:'0 32px 100px rgba(0,0,0,.85)',background:'#0d0d1a',border:'1px solid rgba(255,255,255,.07)',display:'flex',flexDirection:'column',transition:dragging?'none':'width .2s, height .2s, border-radius .2s',userSelect:dragging?'none':'auto'}}>
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
          <div style=${{position:'absolute',inset:0,background:'radial-gradient(ellipse at 10% 40%,rgba(200,241,53,.15) 0%,transparent 55%),radial-gradient(ellipse at 85% 15%,rgba(251,146,60,.12) 0%,transparent 50%),radial-gradient(ellipse at 50% 85%,rgba(34,197,94,.08) 0%,transparent 50%)',pointerEvents:'none'}}></div>
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
                    html`<div style=${{width:partUsers.length<=2?72:52,height:partUsers.length<=2?72:52,borderRadius:'50%',background:u.color||'#c8f135',display:'flex',alignItems:'center',justifyContent:'center',fontSize:partUsers.length<=2?26:20,fontWeight:800,color:'#fff',border:'2.5px solid rgba(255,255,255,.15)'}}>${(u.avatar||u.name||'?')[0]}</div>`}
                </div>
                <!-- Name + indicator -->
                <div style=${{position:'absolute',bottom:7,left:7,right:7,zIndex:3,display:'flex',alignItems:'center',gap:5}}>
                  <div style=${{flex:1,background:'rgba(0,0,0,.6)',backdropFilter:'blur(6px)',borderRadius:8,padding:'3px 8px',display:'flex',alignItems:'center',gap:5,minWidth:0}}>
                    ${speaking[u.id]?html`<div style=${{width:6,height:6,borderRadius:'50%',background:'#22c55e',flexShrink:0,animation:'pulse .8s infinite'}}></div>`:null}
                    <span style=${{fontSize:11,fontWeight:600,color:'#fff',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${u.name}${u.id===cu.id?' (you)':''}</span>
                  </div>
                </div>
              </div>`;})}
            <!-- Screen share tile -->
            ${screenSharing?html`
              <div style=${{width:'100%',height:160,borderRadius:12,overflow:'hidden',border:'2px solid rgba(200,241,53,.4)',background:'#000',position:'relative',flexShrink:0}}>
                <video ref=${screenVideoRef} autoPlay playsInline muted style=${{width:'100%',height:'100%',objectFit:'contain'}}></video>
                <div style=${{position:'absolute',top:7,left:7,background:'rgba(200,241,53,.7)',borderRadius:6,padding:'2px 9px',fontSize:10,color:'#fff',fontWeight:700}}>📺 You are sharing</div>
              </div>`:null}
          </div>
          <!-- Side panels -->
          ${showParticipants||showInvite?html`
            <div style=${{width:200,background:'rgba(0,0,0,.55)',backdropFilter:'blur(12px)',borderLeft:'1px solid rgba(255,255,255,.06)',display:'flex',flexDirection:'column',overflow:'hidden',zIndex:2,flexShrink:0}}>
              <div style=${{display:'flex',borderBottom:'1px solid rgba(255,255,255,.05)'}}>
                <button onClick=${()=>{setShowParticipants(true);setShowInvite(false);}}
                  style=${{flex:1,padding:'8px',background:showParticipants?'rgba(200,241,53,.18)':'none',border:'none',cursor:'pointer',fontSize:10,fontWeight:700,color:showParticipants?'#b8e020':'rgba(255,255,255,.4)',textTransform:'uppercase',letterSpacing:.8,transition:'all .15s'}}>People</button>
                <button onClick=${()=>{setShowInvite(true);setShowParticipants(false);}}
                  style=${{flex:1,padding:'8px',background:showInvite?'rgba(200,241,53,.18)':'none',border:'none',cursor:'pointer',fontSize:10,fontWeight:700,color:showInvite?'#b8e020':'rgba(255,255,255,.4)',textTransform:'uppercase',letterSpacing:.8,transition:'all .15s',position:'relative'}}>
                  Invite
                  ${notInCall.length>0?html`<span style=${{position:'absolute',top:4,right:6,width:14,height:14,borderRadius:'50%',background:'#22c55e',fontSize:8,fontWeight:800,color:'#fff',display:'flex',alignItems:'center',justifyContent:'center'}}>${notInCall.length}</span>`:null}
                </button>
              </div>
              <div style=${{flex:1,overflowY:'auto',padding:'8px'}}>
                ${showParticipants?partUsers.map(u=>html`
                  <div key=${u.id} style=${{display:'flex',alignItems:'center',gap:7,padding:'6px 8px',borderRadius:9,background:'rgba(255,255,255,.04)',marginBottom:4}}>
                    <div style=${{position:'relative',flexShrink:0}}>
                      <${Av} u=${u} size=${26}/>
                      <div style=${{position:'absolute',bottom:-1,right:-1,width:9,height:9,borderRadius:'50%',background:speaking[u.id]?'#22c55e':'#374151',border:'1.5px solid #0d0d1a',transition:'background .15s'}}></div>
                    </div>
                    <span style=${{fontSize:11,color:'rgba(255,255,255,.8)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1}}>${u.name}${u.id===cu.id?' (you)':''}</span>
                  </div>`):null}
                 ${showInvite?[
                   html`<div style=${{fontSize:10,color:'rgba(255,255,255,.35)',marginBottom:8,padding:'2px 4px'}}>${notInCall.length===0?'Everyone is already in the call':'Click to invite'}</div>`,
                   ...notInCall.map(u=>html`
                     <button key=${u.id} onClick=${()=>inviteUser(u.id)}
                       style=${{width:'100%',display:'flex',alignItems:'center',gap:7,padding:'6px 8px',borderRadius:9,background:'rgba(255,255,255,.04)',border:'1px solid rgba(255,255,255,.06)',cursor:'pointer',marginBottom:4,transition:'all .15s'}}
                       onMouseEnter=${e=>{e.currentTarget.style.background='rgba(34,197,94,.1)';e.currentTarget.style.borderColor='rgba(34,197,94,.25)';}}
                       onMouseLeave=${e=>{e.currentTarget.style.background='rgba(255,255,255,.04)';e.currentTarget.style.borderColor='rgba(255,255,255,.06)';}}> 
                       <${Av} u=${u} size=${26}/>
                       <span style=${{fontSize:11,color:'rgba(255,255,255,.8)',flex:1,textAlign:'left',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${u.name}</span>
                       <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                     </button>`)
                 ]:null}
              </div>
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
            <button onClick=${toggleVideo} style=${{width:44,height:44,borderRadius:13,background:videoOn?'rgba(200,241,53,.18)':'rgba(255,255,255,.09)',border:'1.5px solid '+(videoOn?'rgba(200,241,53,.35)':'rgba(255,255,255,.12)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:videoOn?'#b8e020':'#fff',transition:'all .15s'}}>
              ${videoOn?html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>`:
              html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 16v1a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2m5.66 0H14a2 2 0 0 1 2 2v3.34"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`}
            </button>
            <span style=${{fontSize:8,color:'rgba(255,255,255,.35)',lineHeight:1}}>${videoOn?'Video on':'Video'}</span>
          </div>
          <!-- Screen Share -->
          <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:1}}>
            <button onClick=${toggleScreenShare} style=${{width:44,height:44,borderRadius:13,background:screenSharing?'rgba(200,241,53,.25)':'rgba(255,255,255,.09)',border:'1.5px solid '+(screenSharing?'rgba(200,241,53,.4)':'rgba(255,255,255,.12)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:screenSharing?'#b8e020':'#fff',transition:'all .15s'}}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
            </button>
            <span style=${{fontSize:8,color:'rgba(255,255,255,.35)',lineHeight:1}}>Share</span>
          </div>
          <!-- Raise Hand -->
          <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:1}}>
            <button onClick=${()=>setHandRaised(h=>!h)} style=${{width:44,height:44,borderRadius:13,background:handRaised?'rgba(251,191,36,.2)':'rgba(255,255,255,.09)',border:'1.5px solid '+(handRaised?'rgba(251,191,36,.4)':'rgba(255,255,255,.12)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',fontSize:18,transition:'all .15s'}}>
              ${handRaised?'✋':'🖐'}
            </button>
            <span style=${{fontSize:8,color:'rgba(255,255,255,.35)',lineHeight:1}}>Hand</span>
          </div>
          <!-- Emoji React -->
          <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:1}}>
            <button style=${{width:44,height:44,borderRadius:13,background:'rgba(255,255,255,.09)',border:'1.5px solid rgba(255,255,255,.12)',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',fontSize:18}}>😊</button>
            <span style=${{fontSize:8,color:'rgba(255,255,255,.35)',lineHeight:1}}>React</span>
          </div>
          <!-- Invite / People -->
          <div style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:1}}>
            <button onClick=${()=>{setShowInvite(p=>!p||showParticipants);setShowParticipants(false);}}
              style=${{width:44,height:44,borderRadius:13,background:(showInvite||showParticipants)?'rgba(200,241,53,.18)':'rgba(255,255,255,.09)',border:'1.5px solid '+((showInvite||showParticipants)?'rgba(200,241,53,.35)':'rgba(255,255,255,.12)'),cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:(showInvite||showParticipants)?'#b8e020':'#fff',transition:'all .15s',position:'relative'}}>
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
              <span style=${{position:'absolute',top:-2,right:-2,width:15,height:15,borderRadius:'50%',background:'#22c55e',fontSize:8,fontWeight:800,color:'#fff',display:'flex',alignItems:'center',justifyContent:'center',border:'1.5px solid #0d0d1a'}}>${participants.length}</span>
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

  return html`<div>${incomingToast}${previewPopup}${callPopup}</div>`;
}


/* ─── App ─────────────────────────────────────────────────────────────────── */
function App(){
  const [dark,setDark]=useState(false);const [cu,setCu]=useState(null);const [loading,setLoading]=useState(true);
  const [view,setView]=useState('dashboard');const [col,setCol]=useState(false);
  const [data,setData]=useState({users:[],projects:[],tasks:[],notifs:[]});
  const [dmUnread,setDmUnread]=useState([]);const [wsName,setWsName]=useState('');
  const [showReminders,setShowReminders]=useState(false);const [reminderTask,setReminderTask]=useState(null);const [upcomingReminders,setUpcomingReminders]=useState([]);
  const [showNotifBanner,setShowNotifBanner]=useState(false);

  // Show notification permission banner after login
  useEffect(()=>{
    if(cu&&'Notification' in window&&Notification.permission==='default'){
      setTimeout(()=>setShowNotifBanner(true),2000);
    }
  },[cu]);
  const [callState,setCallState]=useState({status:'idle',roomId:null,roomName:'',participants:[],elapsed:0,muted:false,incomingCall:null,allUsers:[]});
  const huddleCmdRef=useRef({});

  const load=useCallback(async()=>{
    if(!cu)return;
    try{
      const [users,projects,tasks,notifs,dmu,ws]=await Promise.all([
        api.get('/api/users'),api.get('/api/projects'),api.get('/api/tasks'),
        api.get('/api/notifications'),api.get('/api/dm/unread'),api.get('/api/workspace'),
      ]);
      setData({users:Array.isArray(users)?users:[],projects:Array.isArray(projects)?projects:[],tasks:Array.isArray(tasks)?tasks:[],notifs:Array.isArray(notifs)?notifs:[]});
      setDmUnread(Array.isArray(dmu)?dmu:[]);
      if(ws&&ws.name)setWsName(ws.name);
      const rems=await api.get('/api/reminders');
      if(Array.isArray(rems)){const now=new Date();setUpcomingReminders(rems.filter(r=>new Date(r.remind_at)>=now).sort((a,b)=>new Date(a.remind_at)-new Date(b.remind_at)));}
    }catch(e){console.error(e);}
  },[cu]);

  useEffect(()=>{api.get('/api/auth/me').then(u=>{if(u&&!u.error)setCu(u);setLoading(false);}).catch(()=>setLoading(false));},[]);
  useEffect(()=>{load();},[load]);
  useEffect(()=>{document.body.className=dark?'dm':'lm';},[dark]);
  // Poll DM unread count every 5s — play sound + OS notification when new DMs arrive
  useEffect(()=>{
    if(!cu)return;
    let prevDms=[];
    const id=setInterval(()=>{
      api.get('/api/dm/unread').then(d=>{
        if(Array.isArray(d)){
          const total=d.reduce((a,x)=>a+(x.cnt||0),0);
          const prevTotal=prevDms.reduce((a,x)=>a+(x.cnt||0),0);
          if(total>prevTotal){
            playSound('notif');
            // Find new senders to show their name
            d.forEach(x=>{
              const prev=prevDms.find(p=>p.sender===x.sender);
              if(!prev||(x.cnt||0)>(prev.cnt||0)){
                const sender=safe(data.users).find(u=>u.id===x.sender);
                const sname=sender?sender.name:'Someone';
                showBrowserNotif('💬 New Message',sname+' sent you a message',()=>setView('dm'),{tag:'dm-'+x.sender,requireInteraction:false});
              }
            });
          }
          prevDms=d;
          setDmUnread(d);
        }
      });
    },5000);
    return()=>clearInterval(id);
  },[cu,data.users]);
  // Poll notifications every 8s — OS notification + sound on new items
  useEffect(()=>{
    if(!cu)return;
    let prevUnread=0;
    let prevIds=new Set();
    const NTITLES={task_assigned:'✅ Task Assigned',status_change:'🔄 Status Update',comment:'💬 New Comment',deadline:'⏰ Deadline',dm:'📨 Direct Message',project_added:'📁 Project Update',reminder:'⏰ Reminder',call:'📞 Huddle Call'};
    const id=setInterval(()=>{
      api.get('/api/notifications').then(d=>{
        if(Array.isArray(d)){
          const unread=d.filter(n=>!n.read).length;
          if(unread>prevUnread){
            playSound('notif');
            // Show OS notification for each new item
            d.filter(n=>!n.read&&!prevIds.has(n.id)).forEach(n=>{
              const title=NTITLES[n.type]||'ProjectFlow';
              showBrowserNotif(title,n.content,()=>setView('notifs'),{tag:'notif-'+n.id,requireInteraction:n.type==='call'});
            });
          }
          prevUnread=unread;
          prevIds=new Set(d.map(n=>n.id));
          setData(prev=>({...prev,notifs:d}));
          // Update badge
          const dmTotal=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);
          updateBadge(unread+dmTotal);
        }
      });
    },8000);
    return()=>clearInterval(id);
  },[cu,dmUnread]);

  const onDmRead=useCallback(sid=>{setDmUnread(prev=>prev.filter(x=>x.sender!==sid));},[]);
  const logout=async()=>{await api.post('/api/auth/logout',{});setCu(null);setData({users:[],projects:[],tasks:[],notifs:[]});setDmUnread([]);};

  // Request browser notification permission on login
  useEffect(()=>{if(cu)requestNotifPermission();},[cu]);

  // Update badge on unread changes
  useEffect(()=>{
    const unread=safe(data.notifs).filter(n=>!n.read).length;
    const dmTotal=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);
    updateBadge(unread+dmTotal);
  },[data.notifs,dmUnread]);

  // Poll for due reminders every 30s — fire OS notification + in-app notification + keep topbar in sync
  useEffect(()=>{
    if(!cu)return;
    const id=setInterval(async()=>{
      const due=await api.get('/api/reminders/due');
      if(Array.isArray(due)&&due.length>0){
        due.forEach(r=>{
          playSound('reminder');
          showBrowserNotif('⏰ Reminder Due!',r.task_title,()=>setView('tasks'),{tag:'rem-'+r.id,requireInteraction:true});
          const nid='rn'+Date.now();
          setData(prev=>({...prev,notifs:[
            {id:nid,type:'reminder',content:'⏰ Reminder: '+r.task_title,read:0,ts:new Date().toISOString()},
            ...prev.notifs
          ]}));
        });
      }
      // Keep topbar in sync
      const rems=await api.get('/api/reminders');
      if(Array.isArray(rems)){const now=new Date();setUpcomingReminders(rems.filter(r=>new Date(r.remind_at)>=now).sort((a,b)=>new Date(a.remind_at)-new Date(b.remind_at)));}
    },30000);
    return()=>clearInterval(id);
  },[cu]);

  if(loading)return html`<div style=${{display:'flex',alignItems:'center',justifyContent:'center',height:'100vh',background:'var(--bg)',flexDirection:'column'}}>
    <div style=${{position:'relative',width:100,height:100,display:'flex',alignItems:'center',justifyContent:'center'}}>
      <div style=${{width:88,height:88,background:'linear-gradient(135deg,#c8f135,#9b8ef4)',borderRadius:24,display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 0 40px rgba(200,241,53,.45)',animation:'sp .9s linear infinite'}}>
        <svg width="46" height="46" viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="9" fill="white"/><circle cx="32" cy="11" r="6" fill="white" opacity="0.95"/><circle cx="51" cy="43" r="6" fill="white" opacity="0.95"/><circle cx="13" cy="43" r="6" fill="white" opacity="0.95"/><line x1="32" y1="17" x2="32" y2="23" stroke="white" strokeWidth="3.5" strokeLinecap="round"/><line x1="46" y1="40" x2="40" y2="36" stroke="white" strokeWidth="3.5" strokeLinecap="round"/><line x1="18" y1="40" x2="24" y2="36" stroke="white" strokeWidth="3.5" strokeLinecap="round"/></svg>
      </div>
    </div>
    <p style=${{color:'var(--tx2)',fontSize:13,marginTop:22,letterSpacing:'.3px'}}>Loading ProjectFlow...</p>
  </div>`;
  if(!cu)return html`<${AuthScreen} onLogin=${u=>{setCu(u);}}/>`;

  const unread=safe(data.notifs).filter(n=>!n.read).length;
  const totalDm=dmUnread.reduce((a,x)=>a+(x.cnt||0),0);
  const TITLES={
    dashboard:{title:'Dashboard',sub:'Overview of your work'},
    projects:{title:'Projects',sub:data.projects.length+' projects'},
    tasks:{title:'Task Board',sub:data.tasks.length+' total tasks'},
    messages:{title:'Channels',sub:'Project team channels'},
    dm:{title:'Direct Messages',sub:totalDm>0?totalDm+' unread':'Private conversations'},
    reminders:{title:'Reminders',sub:'Upcoming task reminders'},
    notifs:{title:'Notifications',sub:unread+' unread'},
    team:{title:'Team',sub:data.users.length+' members'},
    settings:{title:'Settings',sub:wsName||'Workspace configuration'},
  };
  const info=TITLES[view]||{title:view,sub:''};
  const extra=view==='tasks'?html`<a href="/api/export/csv" class="btn bg" style=${{fontSize:12,padding:'6px 11px'}}>⬇ CSV</a>`:null;

  return html`
    <div style=${{display:'flex',width:'100vw',height:'100vh',background:'var(--bg)',overflow:'hidden'}}>
      <${Sidebar} cu=${cu} view=${view} setView=${setView} onLogout=${logout} unread=${unread} dmUnread=${dmUnread} col=${col} setCol=${setCol} wsName=${wsName}
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
          onNotifClick=${async n=>{if(!n.read)await api.put('/api/notifications/'+n.id+'/read',{});const nav={task_assigned:'tasks',status_change:'tasks',comment:'tasks',deadline:'tasks',dm:'dm',project_added:'projects',reminder:'reminders',call:'dashboard'};setView(nav[n.type]||'notifs');load();}}
          onMarkAllRead=${async()=>{await api.put('/api/notifications/read-all',{});load();}}
          onClearAll=${async()=>{await api.del('/api/notifications/all');load();}}
        />
        <div style=${{flex:1,overflow:'hidden'}}>
          <${ErrorBoundary}>
            ${view==='dashboard'?html`<${Dashboard} cu=${cu} tasks=${data.tasks} projects=${data.projects} users=${data.users} onNav=${setView}/>`:null}
            ${view==='projects'?html`<${ProjectsView} projects=${data.projects} tasks=${data.tasks} users=${data.users} cu=${cu} reload=${load} onSetReminder=${t=>{setReminderTask(t);}}/>`:null}
            ${view==='tasks'?html`<${TasksView} tasks=${data.tasks} projects=${data.projects} users=${data.users} cu=${cu} reload=${load} onSetReminder=${t=>{setReminderTask(t);}}/>`:null}
            ${view==='messages'?html`<${MessagesView} projects=${data.projects} users=${data.users} cu=${cu}/>`:null}
            ${view==='dm'?html`<${DirectMessages} cu=${cu} users=${data.users} dmUnread=${dmUnread} onDmRead=${onDmRead} onStartHuddle=${u=>{huddleCmdRef.current.openHuddle&&huddleCmdRef.current.openHuddle(u);}}/>`:null}
            ${view==='reminders'?html`<${RemindersView} cu=${cu} tasks=${data.tasks} projects=${data.projects} onSetReminder=${t=>{setReminderTask(t);}} onReload=${load}/>`:null}
            ${view==='notifs'?html`<${NotifsView} notifs=${data.notifs} reload=${load} onNavigate=${setView}/>`:null}
            ${view==='team'&&cu.role==='Admin'?html`<${TeamView} users=${data.users} cu=${cu} reload=${load}/>`:null}
            ${view==='settings'&&cu.role==='Admin'?html`<${WorkspaceSettings} cu=${cu} onReload=${load}/>`:null}
          <//>
        </div>
      </div>
    </div>
    <${AIAssistant} cu=${cu} projects=${data.projects} tasks=${data.tasks} users=${data.users}/>
    <${HuddleCall} cu=${cu} users=${data.users} onStateChange=${s=>setCallState(prev=>({...prev,...s}))} cmdRef=${huddleCmdRef}/>
    ${showNotifBanner?html`
      <div style=${{position:'fixed',bottom:20,left:'50%',transform:'translateX(-50%)',zIndex:9200,background:'#1e1b4b',border:'1px solid rgba(200,241,53,.4)',borderRadius:14,padding:'13px 18px',boxShadow:'0 12px 40px rgba(0,0,0,.5)',display:'flex',alignItems:'center',gap:12,maxWidth:420,animation:'slideUp .3s ease'}}>
        <div style=${{width:36,height:36,borderRadius:10,background:'rgba(200,241,53,.18)',border:'1px solid rgba(200,241,53,.3)',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0,fontSize:18}}>🔔</div>
        <div style=${{flex:1}}>
          <div style=${{fontSize:13,fontWeight:700,color:'#fff',marginBottom:2}}>Enable notifications</div>
          <div style=${{fontSize:11,color:'rgba(255,255,255,.55)'}}>Get alerted for calls, messages & project updates</div>
        </div>
        <button style=${{height:32,borderRadius:9,background:'linear-gradient(135deg,#c8f135,#4f46e5)',border:'none',color:'#fff',padding:'0 14px',cursor:'pointer',fontWeight:600,fontSize:12,flexShrink:0}}
          onClick=${()=>{requestNotifPermission();setShowNotifBanner(false);}}>Allow</button>
        <button style=${{width:28,height:28,borderRadius:8,background:'rgba(255,255,255,.07)',border:'none',cursor:'pointer',color:'rgba(255,255,255,.4)',fontSize:16,flexShrink:0,display:'flex',alignItems:'center',justifyContent:'center'}}
          onClick=${()=>setShowNotifBanner(false)}>✕</button>
      </div>`:null}
    ${reminderTask!==null?html`<${ReminderModal} task=${reminderTask} onClose=${()=>setReminderTask(null)} onSaved=${()=>{setReminderTask(null);load();}}/>`:null}
    ${showReminders?html`<${RemindersPanel} onClose=${()=>{setShowReminders(false);load();}} onReload=${load}/>`:null}`;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${ErrorBoundary}><${App}<//>`);
})();
</script>
</body>
</html>"""

# ── Utilities ──────────────────────────────────────────────────────────────────
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
    print("\n⚡ ProjectFlow v4.0 — Multi-Tenant | AI | Workspaces")
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
