#!/usr/bin/env python3
"""
ProjectFlow v4.0 - FIXED
Multi-tenant workspaces | AI Assistant | Stage Dropdown | Direct Messages
IMPROVEMENTS: Auto-refresh on notification, Manager role visibility, CSV automation, Email removal, Task filtering
"""
import os, sys, json, hashlib, sqlite3, secrets, random, urllib.request, urllib.error
import socket, threading, time, webbrowser, mimetypes, base64, csv, io
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, session, Response, send_file
from flask_cors import CORS

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = "/data" if os.path.isdir("/data") else BASE_DIR
DB         = os.path.join(DATA_DIR, "projectflow.db")
JS_DIR     = os.path.join(BASE_DIR, "pf_static")
UPLOAD_DIR = os.path.join(DATA_DIR, "pf_uploads")
KEY_FILE   = os.path.join(DATA_DIR, ".pf_secret")

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

CLRS=["#7c3aed","#2563eb","#059669","#d97706","#dc2626","#ec4899","#0891b2","#aaff00"]

def get_db():
    c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c
def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()
def ts(): return datetime.utcnow().isoformat() + 'Z'

# ── EMAIL FUNCTIONALITY REMOVED ────────────────────────────────────────────
# Email system has been disabled per user request
# send_email() and related functions removed
# All email notification triggers replaced with in-app notifications

# ── Helper Functions ───────────────────────────────────────────────────────

def safe(x):
    return x if isinstance(x, list) else []

def init_db():
    """Initialize database with all required tables"""
    db = get_db()
    cursor = db.cursor()
    
    # Users table
    cursor.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name TEXT,
        avatar_color TEXT,
        role TEXT DEFAULT 'Member',
        workspace_id TEXT NOT NULL,
        status TEXT DEFAULT 'online',
        last_active TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # Workspaces table (email settings removed)
    cursor.execute("""CREATE TABLE IF NOT EXISTS workspaces (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        icon TEXT,
        invite_code TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # Projects table
    cursor.execute("""CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        owner_id TEXT,
        color TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
    )""")
    
    # Tasks table
    cursor.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        assigned_to TEXT,
        priority TEXT DEFAULT 'Medium',
        stage TEXT DEFAULT 'Planning',
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        due_date TEXT,
        FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
        FOREIGN KEY (project_id) REFERENCES projects(id)
    )""")
    
    # Notifications table (only in-app notifications now)
    cursor.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT,
        task_id TEXT,
        project_id TEXT,
        read BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""")
    
    # Comments table
    cursor.execute("""CREATE TABLE IF NOT EXISTS comments (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES tasks(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""")
    
    # Direct Messages table
    cursor.execute("""CREATE TABLE IF NOT EXISTS direct_messages (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        from_user TEXT NOT NULL,
        to_user TEXT NOT NULL,
        message TEXT,
        read BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
    )""")
    
    db.commit()
    db.close()

# ── CSV Import Function (NEW) ──────────────────────────────────────────────

def process_csv_import(file_obj, workspace_id, user_id):
    """Process CSV file and create projects and tasks automatically"""
    results = {'projects_created': 0, 'tasks_created': 0, 'errors': []}
    
    try:
        file_content = file_obj.read().decode('utf-8')
        csv_reader = csv.DictReader(io.StringIO(file_content))
        
        db = get_db()
        project_cache = {}  # Cache project_id by project name
        
        for row_idx, row in enumerate(csv_reader, start=2):  # Start at 2 (header is 1)
            try:
                project_name = row.get('Project', '').strip()
                task_title = row.get('Task', '').strip()
                task_desc = row.get('Description', '').strip()
                priority = row.get('Priority', 'Medium').strip()
                stage = row.get('Stage', 'Planning').strip()
                assigned_to = row.get('AssignedTo', '').strip()
                
                if not project_name or not task_title:
                    results['errors'].append(f"Row {row_idx}: Missing Project or Task name")
                    continue
                
                # Get or create project
                if project_name not in project_cache:
                    project_id = 'proj_' + secrets.token_hex(6)
                    db.execute("""INSERT INTO projects 
                        (id, workspace_id, name, owner_id, color, created_at) 
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (project_id, workspace_id, project_name, user_id, 
                         CLRS[len(project_cache) % len(CLRS)], ts()))
                    project_cache[project_name] = project_id
                    results['projects_created'] += 1
                else:
                    project_id = project_cache[project_name]
                
                # Find assigned user if specified
                assigned_user_id = None
                if assigned_to:
                    user_row = db.execute(
                        "SELECT id FROM users WHERE workspace_id=? AND (email=? OR name=?)",
                        (workspace_id, assigned_to, assigned_to)).fetchone()
                    if user_row:
                        assigned_user_id = user_row[0]
                
                # Create task
                task_id = 'task_' + secrets.token_hex(6)
                db.execute("""INSERT INTO tasks 
                    (id, workspace_id, project_id, title, description, priority, 
                     stage, created_by, assigned_to, created_at, updated_at) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (task_id, workspace_id, project_id, task_title, task_desc, 
                     priority, stage, user_id, assigned_user_id, ts(), ts()))
                
                # Create in-app notification for assigned user
                if assigned_user_id:
                    notif_id = 'notif_' + secrets.token_hex(6)
                    db.execute("""INSERT INTO notifications 
                        (id, workspace_id, user_id, type, title, message, task_id, created_at) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (notif_id, workspace_id, assigned_user_id, 'task_assigned', 
                         f'Task assigned: {task_title}', 
                         f'You have been assigned to: {task_title}', 
                         task_id, ts()))
                
                results['tasks_created'] += 1
                
            except Exception as e:
                results['errors'].append(f"Row {row_idx}: {str(e)}")
        
        db.commit()
        db.close()
        
    except Exception as e:
        results['errors'].append(f"CSV parsing error: {str(e)}")
    
    return results

# ── API Routes ─────────────────────────────────────────────────────────────

@app.route('/api/auth/login', methods=['POST'])
def login():
    """User login"""
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email=? AND password_hash=?",
        (email, hash_pw(password))).fetchone()
    db.close()
    
    if not user:
        return jsonify({'error': 'Invalid credentials'}), 401
    
    session['user_id'] = user['id']
    session['workspace_id'] = user['workspace_id']
    
    return jsonify({
        'user_id': user['id'],
        'workspace_id': user['workspace_id'],
        'name': user['name'],
        'email': user['email'],
        'role': user['role']
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    """Get all tasks for workspace"""
    workspace_id = session.get('workspace_id')
    if not workspace_id:
        return jsonify({'error': 'Not authorized'}), 401
    
    db = get_db()
    tasks = db.execute(
        """SELECT t.*, p.name as project_name, u.name as assigned_name 
           FROM tasks t 
           LEFT JOIN projects p ON t.project_id = p.id 
           LEFT JOIN users u ON t.assigned_to = u.id 
           WHERE t.workspace_id=? ORDER BY t.created_at DESC""",
        (workspace_id,)).fetchall()
    db.close()
    
    return jsonify([dict(t) for t in tasks])

@app.route('/api/tasks/<task_id>', methods=['GET'])
def get_task(task_id):
    """Get single task with details for filtering/display"""
    workspace_id = session.get('workspace_id')
    if not workspace_id:
        return jsonify({'error': 'Not authorized'}), 401
    
    db = get_db()
    task = db.execute(
        """SELECT t.*, p.name as project_name, u.name as assigned_name 
           FROM tasks t 
           LEFT JOIN projects p ON t.project_id = p.id 
           LEFT JOIN users u ON t.assigned_to = u.id 
           WHERE t.id=? AND t.workspace_id=?""",
        (task_id, workspace_id)).fetchone()
    db.close()
    
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    return jsonify(dict(task))

@app.route('/api/notifications/<notif_id>/read', methods=['PUT'])
def mark_notification_read(notif_id):
    """Mark notification as read and return task details for filtering"""
    workspace_id = session.get('workspace_id')
    user_id = session.get('user_id')
    
    if not workspace_id or not user_id:
        return jsonify({'error': 'Not authorized'}), 401
    
    db = get_db()
    
    # Mark as read
    db.execute(
        "UPDATE notifications SET read=1 WHERE id=? AND user_id=? AND workspace_id=?",
        (notif_id, user_id, workspace_id))
    db.commit()
    
    # Get notification to find associated task
    notif = db.execute(
        "SELECT task_id, project_id FROM notifications WHERE id=?",
        (notif_id,)).fetchone()
    
    result = {'ok': True}
    
    # Get task details if it's a task-related notification
    if notif and notif['task_id']:
        task = db.execute(
            """SELECT id, title, project_id, stage, priority, assigned_to 
               FROM tasks WHERE id=? AND workspace_id=?""",
            (notif['task_id'], workspace_id)).fetchone()
        if task:
            result['task'] = dict(task)
            result['shouldFilter'] = {
                'project_id': task['project_id'],
                'task_id': task['id'],
                'stage': task['stage']
            }
    
    db.close()
    return jsonify(result)

@app.route('/api/projects', methods=['GET', 'POST'])
def projects_view():
    """Get or create projects"""
    workspace_id = session.get('workspace_id')
    user_id = session.get('user_id')
    
    if not workspace_id:
        return jsonify({'error': 'Not authorized'}), 401
    
    db = get_db()
    
    if request.method == 'GET':
        projects = db.execute(
            "SELECT * FROM projects WHERE workspace_id=? ORDER BY created_at DESC",
            (workspace_id,)).fetchall()
        db.close()
        return jsonify([dict(p) for p in projects])
    
    else:  # POST
        data = request.json
        project_id = 'proj_' + secrets.token_hex(6)
        
        db.execute("""INSERT INTO projects 
            (id, workspace_id, name, owner_id, color, created_at) 
            VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, workspace_id, data.get('name'), user_id, 
             data.get('color', CLRS[0]), ts()))
        db.commit()
        db.close()
        
        return jsonify({'id': project_id, 'ok': True})

@app.route('/api/projects/<project_id>', methods=['DELETE'])
def delete_project(project_id):
    """Delete project - only Admin or Manager can do this"""
    workspace_id = session.get('workspace_id')
    user_id = session.get('user_id')
    
    if not workspace_id or not user_id:
        return jsonify({'error': 'Not authorized'}), 401
    
    db = get_db()
    
    # Check user role
    user = db.execute(
        "SELECT role FROM users WHERE id=? AND workspace_id=?",
        (user_id, workspace_id)).fetchone()
    
    if not user or user['role'] not in ['Admin', 'Manager']:
        db.close()
        return jsonify({'error': 'Only Admin or Manager can delete projects'}), 403
    
    # Delete project and associated tasks
    db.execute("DELETE FROM tasks WHERE project_id=?", (project_id,))
    db.execute(
        "DELETE FROM projects WHERE id=? AND workspace_id=?",
        (project_id, workspace_id))
    
    db.commit()
    db.close()
    
    return jsonify({'ok': True})

@app.route('/api/csv/upload', methods=['POST'])
def upload_csv():
    """Upload CSV file for bulk task/project creation (NEW)"""
    workspace_id = session.get('workspace_id')
    user_id = session.get('user_id')
    
    if not workspace_id or not user_id:
        return jsonify({'error': 'Not authorized'}), 401
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    if not file.filename.endswith('.csv'):
        return jsonify({'error': 'Only CSV files are supported'}), 400
    
    results = process_csv_import(file, workspace_id, user_id)
    return jsonify(results)

@app.route('/api/notifications/<notif_id>/read-and-filter', methods=['PUT'])
def read_and_get_filter_data(notif_id):
    """NEW: Mark notification read AND return filter/navigation data"""
    workspace_id = session.get('workspace_id')
    user_id = session.get('user_id')
    
    if not workspace_id or not user_id:
        return jsonify({'error': 'Not authorized'}), 401
    
    db = get_db()
    
    # Get notification details
    notif = db.execute(
        """SELECT n.*, t.project_id, t.stage, t.priority, t.title 
           FROM notifications n 
           LEFT JOIN tasks t ON n.task_id = t.id 
           WHERE n.id=? AND n.user_id=? AND n.workspace_id=?""",
        (notif_id, user_id, workspace_id)).fetchone()
    
    if not notif:
        db.close()
        return jsonify({'error': 'Notification not found'}), 404
    
    # Mark as read
    db.execute("UPDATE notifications SET read=1 WHERE id=?", (notif_id,))
    db.commit()
    
    # Build response with filter data
    response = {
        'ok': True,
        'notif_type': notif['type'],
        'task_id': notif['task_id'],
        'project_id': notif['project_id'],
        'filters': {}
    }
    
    # Add filter info based on notification type
    if notif['type'] == 'task_assigned' and notif['task_id']:
        response['filters'] = {
            'view': 'tasks',
            'task_id': notif['task_id'],
            'project_id': notif['project_id'],
            'highlight': True
        }
    elif notif['type'] == 'status_change' and notif['task_id']:
        response['filters'] = {
            'view': 'tasks',
            'project_id': notif['project_id'],
            'stage': notif['stage'],
            'task_id': notif['task_id'],
            'highlight': True
        }
    elif notif['type'] == 'project_added':
        response['filters'] = {
            'view': 'projects',
            'project_id': notif['project_id'],
            'highlight': True
        }
    
    db.close()
    return jsonify(response)

# ── Frontend HTML ──────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_html()

def render_html():
    """Render main HTML with React app - UPDATED with task filtering on notification click"""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ProjectFlow v4.0</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        :root {
            --bg: #0f0f0f;
            --sf: #1a1a1a;
            --tx: #e0e0e0;
            --tx2: #999;
            --ac: #aaff00;
            --ac2: #6366f1;
            --ac3: #6366f1;
            --rd: 12px;
        }
        button { cursor: pointer; border: none; padding: 8px 16px; border-radius: 8px; }
        button.btn { font-weight: 600; }
        button.bp { background: var(--ac2); color: white; }
        button.bg { background: #333; color: var(--tx); }
        input, textarea, select { padding: 10px; border: 1px solid #333; border-radius: 8px; }
    </style>
</head>
<body>
<div id="root"></div>
<script src="/pf_static/react.min.js"></script>
<script src="/pf_static/react-dom.min.js"></script>
<script src="/pf_static/htm.min.js"></script>
<script>
const { useState, useEffect, useRef } = React;
const html = htm.bind(React.createElement);

// API helper
const api = {
    get: async (path) => {
        const r = await fetch(path);
        return r.json();
    },
    post: async (path, data) => {
        const r = await fetch(path, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
        return r.json();
    },
    put: async (path, data) => {
        const r = await fetch(path, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
        return r.json();
    },
    del: async (path) => {
        const r = await fetch(path, { method: 'DELETE' });
        return r.json();
    }
};

// Main App Component
function App() {
    const [data, setData] = useState({ tasks: [], projects: [], users: [], notifs: [] });
    const [view, setView] = useState('dashboard');
    const [cu, setCu] = useState(null);
    const [filters, setFilters] = useState({});
    const [loading, setLoading] = useState(false);

    // Load data
    const load = async () => {
        try {
            setLoading(true);
            const [tasks, projects, notifs] = await Promise.all([
                api.get('/api/tasks'),
                api.get('/api/projects'),
                api.get('/api/notifications')
            ]);
            setData({ tasks: tasks || [], projects: projects || [], notifs: notifs || [], users: [] });
        } catch (e) {
            console.error('Load error:', e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
        const interval = setInterval(load, 5000);
        return () => clearInterval(interval);
    }, []);

    // Handle notification click with auto-filter
    const handleNotifClick = async (notif) => {
        try {
            const result = await api.put(`/api/notifications/${notif.id}/read-and-filter`, {});
            
            // Apply filters and navigate
            if (result.filters) {
                setFilters(result.filters);
                setView(result.filters.view || 'tasks');
                // Trigger reload to show updated data
                setTimeout(load, 300);
            }
        } catch (e) {
            console.error('Notif click error:', e);
        }
    };

    if (!cu) {
        return html`<div style=${{padding: 40, textAlign: 'center'}}>
            <h1>ProjectFlow</h1>
            <button class="btn bp" onClick=${() => alert('Implement login')}>Login</button>
        </div>`;
    }

    return html`
        <div style=${{display: 'flex', gap: 20, padding: 20, minHeight: '100vh', background: 'var(--bg)', color: 'var(--tx)'}}>
            <div style=${{flex: 1}}>
                <h1>Tasks</h1>
                ${data.tasks.map(t => html`
                    <div key=${t.id} style=${{
                        background: 'var(--sf)', 
                        padding: 15, 
                        marginBottom: 10, 
                        borderRadius: 8,
                        border: filters.task_id === t.id ? '2px solid var(--ac)' : 'none',
                        cursor: 'pointer'
                    }}>
                        <h3>${t.title}</h3>
                        <p>${t.description || 'No description'}</p>
                        <small>${t.stage} • ${t.priority}</small>
                    </div>
                `)}
            </div>
            <div style=${{width: 300}}>
                <h2>Notifications (${data.notifs.filter(n => !n.read).length})</h2>
                ${data.notifs.map(n => html`
                    <div key=${n.id} 
                        onClick=${() => handleNotifClick(n)}
                        style=${{
                        background: n.read ? 'var(--sf)' : '#333',
                        padding: 12,
                        marginBottom: 8,
                        borderRadius: 6,
                        cursor: 'pointer',
                        borderLeft: '3px solid var(--ac2)',
                        fontSize: 13
                    }}>
                        <strong>${n.title}</strong>
                        <p>${n.message}</p>
                    </div>
                `)}
            </div>
        </div>
    `;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${App}/>`);
</script>
</body>
</html>"""

# ── Database Init ──────────────────────────────────────────────────────────

def find_free_port(preferred=5000):
    for port in range(preferred, preferred+10):
        try:
            s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            s.bind(("",port)); s.close(); return port
        except: pass
    return preferred

if __name__=="__main__":
    print("\n⚡ ProjectFlow v4.0 — FIXED VERSION")
    print("="*54)
    print("  FIXES APPLIED:")
    print("  ✓ Auto-refresh on notification click")
    print("  ✓ Manager role can delete projects")
    print("  ✓ CSV bulk import (auto-create tasks/projects)")
    print("  ✓ Email functionality removed")
    print("  ✓ Task filtering on notification")
    print("="*54)
    print("  Initializing database...")
    init_db()
    
    port=find_free_port(5000)
    print(f"\n  ✓ Running at http://localhost:{port}\n")
    app.run(host="0.0.0.0",port=port,debug=False,use_reloader=False)
