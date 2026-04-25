"""System error tracking service.

Captures unhandled Flask exceptions to the system_errors table for operational
visibility in the Platform Admin → System Log panel.

Design principles:
- capture_exception() NEVER raises — it fails silently if the DB write fails.
- DB write runs in a daemon thread so it never blocks the error response.
- Uses a fresh DB connection (not the request-context pool) so it is safe to
  call from Flask error handlers where the request context may be torn down.
"""

import logging
import threading
import traceback

logger = logging.getLogger(__name__)


def capture_exception(exc, request=None, user_id=None, tenant_id=None):
    """Capture an unhandled exception into the system_errors table.

    Called from the Flask @app.errorhandler(Exception) handler.  Never raises.

    Args:
        exc:        The exception instance.
        request:    The Flask request object (optional — may be None if called
                    outside a request context).
        user_id:    The authenticated user's DB id (optional).
        tenant_id:  The tenant id from the session (optional).
    """
    try:
        # Collect everything we need before spawning the thread so we don't
        # hold a reference to the live request object.
        route = None
        method = None
        if request is not None:
            try:
                route = request.path
                method = request.method
            except Exception:
                pass

        error_type = type(exc).__name__
        message = str(exc)
        stack = traceback.format_exc()

        def _write():
            try:
                import psycopg2
                from config import Config
                conn = psycopg2.connect(
                    host=Config.PG_HOST,
                    port=Config.PG_PORT,
                    dbname=Config.PG_DATABASE,
                    user=Config.PG_USER,
                    password=Config.PG_PASSWORD,
                    options="-c search_path=helpdesk,public",
                )
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO system_errors
                               (severity, route, method, error_type, message,
                                stack_trace, tenant_id, user_id)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                            [
                                "error",
                                route,
                                method,
                                error_type,
                                message,
                                stack,
                                tenant_id,
                                user_id,
                            ],
                        )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as inner:
                # Absolutely must not raise — just log to stderr/stdout so the
                # error response is never blocked.
                logger.debug("error_tracking_service: failed to write error: %s", inner)

        t = threading.Thread(target=_write, daemon=True)
        t.start()

    except Exception as outer:
        # Belt-and-suspenders: capture_exception itself must never raise.
        logger.debug("error_tracking_service: unexpected failure in capture_exception: %s", outer)


def get_errors(tenant_id=None, resolved=None, limit=100, offset=0):
    """Return system_errors rows for the admin API.

    Args:
        tenant_id:  Filter to a specific tenant.  None = all tenants (super_admin).
        resolved:   True / False to filter by resolved flag.  None = all.
        limit:      Max rows to return.
        offset:     Pagination offset.

    Returns:
        Tuple of (rows: list[dict], total: int).
    """
    from models.db import fetch_all, fetch_one

    conditions = []
    params = []

    if tenant_id is not None:
        conditions.append("se.tenant_id = %s")
        params.append(tenant_id)

    if resolved is not None:
        conditions.append("se.resolved = %s")
        params.append(resolved)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = fetch_all(
        f"""SELECT se.id,
                   se.occurred_at,
                   se.severity,
                   se.route,
                   se.method,
                   se.error_type,
                   se.message,
                   se.stack_trace,
                   se.tenant_id,
                   t.name  AS tenant_name,
                   se.user_id,
                   u.name  AS user_name,
                   se.request_id,
                   se.resolved,
                   se.resolved_at,
                   se.notes
            FROM system_errors se
            LEFT JOIN tenants t ON t.id = se.tenant_id
            LEFT JOIN users u   ON u.id = se.user_id
            {where}
            ORDER BY se.occurred_at DESC
            LIMIT %s OFFSET %s""",
        params + [limit, offset],
    )

    count_row = fetch_one(
        f"SELECT count(*) AS cnt FROM system_errors se {where}",
        params,
    )
    total = count_row["cnt"] if count_row else 0

    return rows, total


def resolve_error(error_id, notes=None):
    """Mark a system error as resolved.

    Args:
        error_id:  The system_errors.id to resolve.
        notes:     Optional admin note to attach.

    Returns:
        True if a row was updated, False if not found.
    """
    from models.db import execute

    params = [error_id]
    notes_clause = ""
    if notes is not None:
        notes_clause = ", notes = %s"
        params = [error_id, notes]

    # Reorder params: SET clause values come before the WHERE id
    if notes is not None:
        rowcount = execute(
            "UPDATE system_errors SET resolved = true, resolved_at = NOW()"
            + f"{notes_clause} WHERE id = %s",
            [notes, error_id],
        )
    else:
        rowcount = execute(
            "UPDATE system_errors SET resolved = true, resolved_at = NOW() WHERE id = %s",
            [error_id],
        )

    return rowcount > 0
