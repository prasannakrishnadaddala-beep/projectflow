# gunicorn.conf.py — Production config for ProjectFlow

import os
import multiprocessing

workers = 2
worker_class = "sync"
timeout = 120
keepalive = 5

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"

accesslog = "-"
errorlog  = "-"
loglevel  = "info"

max_requests = 500
max_requests_jitter = 50
graceful_timeout = 30

# Safety net: ensure DB tables exist when gunicorn master starts
# (init_db is also called in start.sh — this is a backup)
def on_starting(server):
    try:
        from app import init_db
        init_db()
        server.log.info("✓ Database initialized via gunicorn hook")
    except Exception as e:
        server.log.error(f"DB init error: {e}")
