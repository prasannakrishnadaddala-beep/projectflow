# gunicorn.conf.py — Production config for ProjectFlow (small team, 1-20 users)

import multiprocessing

# Workers: 2 is safe for small teams on PaaS free/starter tiers (low RAM)
workers = 2
worker_class = "sync"
worker_connections = 50
timeout = 120          # generous for AI assistant calls (Anthropic can be slow)
keepalive = 5

# Binding — PaaS injects $PORT at runtime
bind = "0.0.0.0:8080"

# Logging — send everything to stdout so PaaS dashboards capture it
accesslog = "-"
errorlog  = "-"
loglevel  = "info"
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sµs'

# Restart workers after N requests to prevent memory creep
max_requests = 500
max_requests_jitter = 50

# Graceful shutdown
graceful_timeout = 30
