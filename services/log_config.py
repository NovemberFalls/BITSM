"""Structured JSON logging for SaaS observability.

Every log line is JSON with standard fields: timestamp, level, logger, message,
plus contextual fields (tenant_id, user_id, request_id, ticket_id, duration_ms)
injected automatically from Flask request context.
"""

import logging
import time
import uuid
from contextlib import contextmanager

from pythonjsonlogger import json as json_log


# ── Thread-local-ish context via Flask g ────────────────────
# Falls back gracefully when called outside request context.

def _get_context() -> dict:
    """Get request context fields, or empty dict outside Flask."""
    try:
        from flask import g
        return {
            "request_id": getattr(g, "request_id", None),
            "tenant_id": getattr(g, "log_tenant_id", None),
            "user_id": getattr(g, "log_user_id", None),
        }
    except RuntimeError:
        return {}


class ContextFilter(logging.Filter):
    """Inject request context into every log record."""

    def filter(self, record):
        ctx = _get_context()
        record.request_id = ctx.get("request_id")
        record.tenant_id = ctx.get("tenant_id")
        record.user_id = ctx.get("user_id")
        # Allow manual extras to override
        if not hasattr(record, "ticket_id"):
            record.ticket_id = None
        if not hasattr(record, "duration_ms"):
            record.duration_ms = None
        return True


# ── JSON Formatter ──────────────────────────────────────────

class HelpdeskJsonFormatter(json_log.JsonFormatter):
    """JSON formatter with standard SaaS fields."""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        # Move context fields to top level
        for field in ("request_id", "tenant_id", "user_id", "ticket_id", "duration_ms"):
            val = getattr(record, field, None)
            if val is not None:
                log_record[field] = val


def configure_logging(level: str = "INFO"):
    """Set up structured JSON logging for the entire application."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler()
    formatter = HelpdeskJsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)
    handler.addFilter(ContextFilter())
    root.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def setup_request_context(app):
    """Register Flask before_request hook to inject context fields."""

    @app.before_request
    def _inject_context():
        from flask import g, session, request as req
        g.request_id = req.headers.get("X-Request-ID") or str(uuid.uuid4())[:12]
        g.log_tenant_id = session.get("tenant_id")
        g.log_user_id = session.get("user_id")


# ── Performance timer ───────────────────────────────────────

@contextmanager
def log_duration(logger_instance, message: str, **extra):
    """Context manager that logs duration of a block.

    Usage:
        with log_duration(logger, "LLM call", model="haiku"):
            result = call_llm()
    """
    t0 = time.monotonic()
    try:
        yield
    finally:
        ms = int((time.monotonic() - t0) * 1000)
        logger_instance.info(
            message,
            extra={"duration_ms": ms, **extra},
        )
