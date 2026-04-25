"""Gunicorn configuration."""

bind = "0.0.0.0:5060"
workers = 2
threads = 4
timeout = 300
accesslog = "-"
errorlog = "-"
loglevel = "info"
preload_app = True

# JSON access log format for structured logging
access_log_format = '{"method":"%(m)s","path":"%(U)s","status":"%(s)s","duration_ms":"%(M)s","size":"%(B)s","ip":"%(h)s"}'


def post_fork(server, worker):
    """Reinitialize DB pool and restart QueueProcessor in each worker after fork.

    preload_app=True creates the pool and QueueProcessor in the master process
    before workers fork. Threads do not survive fork() — each worker must start
    its own QueueProcessor so the pipeline keeps running and the health endpoint
    can detect liveness via threading.enumerate(). FOR UPDATE SKIP LOCKED in the
    queue ensures concurrent workers don't double-process the same task.

    PG_POOL_MIN=0 (default) ensures no connections are pre-created at startup,
    so resetting the pool here sends no Terminate messages to PostgreSQL.
    """
    try:
        import models.db as _db
        with _db._pool_lock:
            _db._pool = None
        _db.init_pool()
        server.log.info("Worker %s: DB pool reinitialised", worker.pid)
    except Exception as exc:
        server.log.warning("Worker %s: DB pool reinit failed: %s", worker.pid, exc)

    try:
        import os
        from services.queue_service import QueueProcessor
        _processor = QueueProcessor(
            max_llm_concurrency=int(os.environ.get("QUEUE_MAX_LLM_CONCURRENCY", 5)),
            poll_interval=float(os.environ.get("QUEUE_POLL_INTERVAL", 2.0)),
        )
        _processor.start()
        server.log.info("Worker %s: QueueProcessor started", worker.pid)
    except Exception as exc:
        server.log.warning("Worker %s: QueueProcessor not started: %s", worker.pid, exc)
