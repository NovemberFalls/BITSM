"""Flask application factory."""

import logging
import os

from dotenv import load_dotenv
load_dotenv(override=True)

from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_session import Session

from config import Config

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[Config.RATE_LIMIT_DEFAULT],  # SOC 2 CC6.8 — global API rate limit; set RATE_LIMIT_DEFAULT env var to override
    storage_uri=Config.REDIS_URL,
)


def _validate_secrets() -> None:
    """Fail fast if critical secrets are missing or default in production."""
    from config import Config
    if not Config.AUTH_ENABLED:
        return  # dev mode — skip checks

    if not Config.SECRET_KEY or Config.SECRET_KEY == "change-me-in-production":
        raise RuntimeError(
            "SECRET_KEY is not set or is using the default value. "
            "Set SECRET_KEY in your .env file before starting in production."
        )
    if not Config.FERNET_KEY:
        raise RuntimeError(
            "FERNET_KEY is not set. Connector configs cannot be encrypted. "
            "Set FERNET_KEY in your .env file before starting in production."
        )


def create_app() -> Flask:
    _validate_secrets()

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(Config)

    # Structured JSON logging
    from services.log_config import configure_logging, setup_request_context
    configure_logging(level=Config.LOG_LEVEL)
    logger = logging.getLogger(__name__)

    # Ensure data directories
    os.makedirs(Config.DATA_DIR, exist_ok=True)

    # Redis-backed session store
    import redis as redis_lib
    app.config["SESSION_REDIS"] = redis_lib.from_url(Config.REDIS_URL)

    # Session
    Session(app)

    # Rate limiter
    limiter.init_app(app)

    # Request context for structured logging
    setup_request_context(app)

    # Sentry error tracking (optional — set SENTRY_DSN in .env to enable)
    _sentry_dsn = os.environ.get("SENTRY_DSN")
    if _sentry_dsn:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(
            dsn=_sentry_dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.1,
            environment=os.environ.get("APP_ENV", "production"),
        )

    # Database pool — graceful fallback if DB not available (dev mode)
    db_available = False
    from models.db import init_pool, close_pool
    try:
        with app.app_context():
            init_pool()
        db_available = True
    except Exception as e:
        logger.warning("Database not available: %s — running without DB", e)

    if db_available:
        @app.teardown_appcontext
        def shutdown_pool(exception=None):
            pass

        import atexit
        atexit.register(close_pool)

    # --- Pipeline Queue Processor ---
    if db_available:
        try:
            from services.queue_service import QueueProcessor
            _processor = QueueProcessor(
                max_llm_concurrency=int(os.environ.get("QUEUE_MAX_LLM_CONCURRENCY", 5)),
                poll_interval=float(os.environ.get("QUEUE_POLL_INTERVAL", 2.0)),
            )
            _processor.start()
        except Exception as e:
            logger.warning("Queue processor not started: %s", e)

    # --- Register Blueprints ---
    from routes.auth import auth_bp
    app.register_blueprint(auth_bp)

    from routes.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix="/api/admin")

    from routes.tickets import tickets_bp
    app.register_blueprint(tickets_bp, url_prefix="/api/tickets")

    from routes.kb import kb_bp
    app.register_blueprint(kb_bp, url_prefix="/api/kb")

    from routes.ai import ai_bp
    app.register_blueprint(ai_bp, url_prefix="/api/ai")

    from routes.connectors import connectors_bp
    app.register_blueprint(connectors_bp, url_prefix="/api/connectors")

    from routes.hierarchies import hierarchies_bp
    app.register_blueprint(hierarchies_bp, url_prefix="/api/hierarchies")

    from routes.notifications import notifications_bp
    app.register_blueprint(notifications_bp, url_prefix="/api/notifications")

    from routes.webhooks import webhooks_bp
    app.register_blueprint(webhooks_bp, url_prefix="/api/webhooks")

    from routes.audit import audit_bp
    app.register_blueprint(audit_bp, url_prefix="/api/audit")

    from routes.queue import queue_bp
    app.register_blueprint(queue_bp, url_prefix="/api/queue")

    from routes.reports import reports_bp
    app.register_blueprint(reports_bp, url_prefix="/api/reports")

    from routes.automations import automations_bp
    app.register_blueprint(automations_bp, url_prefix="/api/automations")

    from routes.sprints import sprints_bp
    app.register_blueprint(sprints_bp, url_prefix="/api/sprints")

    from routes.work_item_types import work_item_types_bp
    app.register_blueprint(work_item_types_bp, url_prefix="/api/work-item-types")

    from routes.billing import billing_bp
    app.register_blueprint(billing_bp)

    from routes.status import status_bp
    app.register_blueprint(status_bp, url_prefix="/api/status")

    from routes.custom_fields import custom_fields_bp
    app.register_blueprint(custom_fields_bp, url_prefix="/api/custom-fields")

    from routes.form_templates import form_templates_bp
    app.register_blueprint(form_templates_bp, url_prefix="/api/form-templates")

    from routes.phone import phone_bp
    app.register_blueprint(phone_bp)

    from routes.messaging import messaging_bp
    app.register_blueprint(messaging_bp)

    # --- Page Routes ---
    from routes.pages import pages_bp
    app.register_blueprint(pages_bp)

    # --- Legal Pages (unauthenticated) ---
    from routes.legal import legal_bp
    app.register_blueprint(legal_bp)

    # --- Idle session timeout ---
    @app.before_request
    def enforce_idle_timeout():
        """Expire sessions that have been idle longer than IDLE_TIMEOUT_MINUTES."""
        from flask import session, request, redirect, url_for
        from datetime import datetime, timezone
        # Skip static assets, login/logout flows, auth routes (incl. /auth/ping), and legal pages
        skip_prefixes = ("/static/", "/auth/", "/login", "/logout", "/legal/")
        if any(request.path.startswith(p) for p in skip_prefixes):
            return
        if "user" not in session:
            return
        now = datetime.now(timezone.utc)
        last_active_str = session.get("last_active")
        if last_active_str:
            last_active = datetime.fromisoformat(last_active_str)
            idle_seconds = (now - last_active).total_seconds()
            # Tenant-specific timeout (cached in session), fallback to global config
            timeout_minutes = session.get("idle_timeout_minutes") or app.config.get("IDLE_TIMEOUT_MINUTES", 60)
            timeout_seconds = timeout_minutes * 60
            if idle_seconds > timeout_seconds:
                user_id = session.get("user", {}).get("id")
                session.clear()
                # Import here to avoid circular import
                try:
                    from services.audit_service import log_event, SESSION_TIMEOUT
                    log_event(SESSION_TIMEOUT, user_id=user_id, request=request)
                except Exception:
                    pass
                # API/programmatic routes get JSON 401; browser navigations get redirect
                if request.path.startswith("/api/") or request.path == "/ping":
                    from flask import jsonify
                    return jsonify({"error": "Session expired due to inactivity", "code": "session_timeout"}), 401
                return redirect(url_for("auth.login", reason="timeout"))
        # Only update last_active for user-initiated actions (writes) or the
        # explicit /ping keepalive. GET requests include background polls
        # (ticket list, notification badge) which must NOT reset the idle clock.
        if request.method != "GET" or request.path == "/ping":
            session["last_active"] = now.isoformat()

    # --- CSRF protection (SOC 2 CC6.6) ---
    @app.before_request
    def enforce_csrf():
        """Validate CSRF token on state-changing requests from the SPA."""
        from flask import session, request
        # Only check POST/PUT/DELETE (not GET/HEAD/OPTIONS)
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        # Skip routes that use their own auth (API keys, webhooks, OAuth callbacks)
        skip_prefixes = ("/static/", "/auth/", "/login", "/logout", "/legal/",
                         "/api/webhooks/", "/api/billing/webhook",
                         "/api/phone/webhook", "/api/messaging/webhook")
        if any(request.path.startswith(p) for p in skip_prefixes):
            return
        # Skip if no user in session (unauthenticated — login_required handles it)
        if "user" not in session:
            return
        # Validate token from X-CSRF-Token header matches session
        expected = session.get("csrf_token")
        provided = request.headers.get("X-CSRF-Token")
        if not expected or not provided or expected != provided:
            from flask import jsonify
            return jsonify({"error": "CSRF token missing or invalid", "code": "csrf_error"}), 403

    # --- Rate limit → JSON response (not HTML) ---
    from flask import jsonify as _jsonify

    @app.errorhandler(429)
    def ratelimit_handler(exc):
        return _jsonify({"error": "rate_limit_exceeded", "message": str(exc.description)}), 429

    # --- Global error capture (writes to system_errors table) ---
    from flask import request as flask_request, session

    @app.errorhandler(Exception)
    def handle_exception(exc):
        """Capture unhandled server errors into the system_errors table, then re-raise.

        4xx HTTPExceptions (rate limits, auth redirects, not-found) are returned
        directly so Flask handles them normally — we only want real server errors.
        capture_exception() is totally safe and will never block or raise.
        """
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException) and exc.code < 500:
            return exc
        try:
            from services.error_tracking_service import capture_exception
            user_id = None
            tenant_id = None
            try:
                user_id = session.get("user_id")
                tenant_id = session.get("tenant_id")
            except Exception:
                pass
            capture_exception(exc, request=flask_request, user_id=user_id, tenant_id=tenant_id)
        except Exception:
            pass
        raise exc

    logger.info("BITSM app initialized")
    return app
