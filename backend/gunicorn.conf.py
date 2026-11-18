"""Gunicorn settings.

One worker process with many threads: analysis runs execute on an in-process
thread pool and the SSE progress stream holds a thread per open connection.
Scaling out is done by moving runs to a queue (see README, "What's next"),
not by adding gunicorn workers.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = 1
worker_class = "gthread"
threads = int(os.environ.get("WEB_THREADS", "32"))
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = None  # access logs are emitted as structured JSON by the app itself
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
