import os

from prometheus_client import multiprocess

bind = "0.0.0.0:8000"
workers = int(os.getenv("WEB_WORKERS", "1"))
worker_class = "uvicorn_worker.UvicornWorker"
timeout = 30
graceful_timeout = 20
keepalive = 10
accesslog = None
errorlog = "-"


def child_exit(server, worker):
    multiprocess.mark_process_dead(worker.pid)
