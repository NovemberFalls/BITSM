"""
audit_service.py — SOC 2 audit event logging

Writes security-relevant events to the audit_events table.
Non-blocking: all DB errors are caught and logged to stderr, never raised to caller.
"""
import json
import logging
from typing import Optional

from models.db import execute

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical event type constants — use these everywhere
# ---------------------------------------------------------------------------

# Auth
LOGIN_SUCCESS = "login.success"
LOGIN_FAILURE = "login.failure"
LOGOUT = "logout"
SESSION_TIMEOUT = "session.timeout"

# Tenant / user admin
TENANT_CREATED = "tenant.created"
TENANT_UPDATED = "tenant.updated"
TENANT_DELETED = "tenant.deleted"
USER_INVITED = "user.invited"
USER_CREATED = "user.created"
USER_DELETED = "user.deleted"
PERMISSION_CHANGED = "permission.changed"
GROUP_PERMISSIONS_CHANGED = "group.permissions.changed"

# User profile self-service
USER_PHONE_CHANGED = "user.phone_changed"
USER_SMS_OPT_IN = "user.sms_opt_in"
USER_SMS_OPT_OUT = "user.sms_opt_out"

# Data access
TICKET_VIEWED = "ticket.viewed"

# Resource types
RT_TICKET = "ticket"
RT_USER = "user"
RT_TENANT = "tenant"
RT_KB_ARTICLE = "kb_article"
RT_GROUP = "group"


# ---------------------------------------------------------------------------
# Core logging function
# ---------------------------------------------------------------------------

def log_event(
    event_type: str,
    tenant_id: Optional[int] = None,
    user_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[int] = None,
    actor_ip: Optional[str] = None,
    actor_user_agent: Optional[str] = None,
    details: Optional[dict] = None,
    request=None,  # Flask request object — auto-extracts IP and user agent if provided
) -> None:
    """
    Write a security audit event to audit_events.

    Never raises. DB errors are logged to stderr and swallowed.
    Pass request=request from Flask route context for automatic IP/UA extraction.
    """
    if request is not None:
        if actor_ip is None:
            forwarded_for = request.headers.get("X-Forwarded-For", request.remote_addr)
            actor_ip = forwarded_for.split(",")[0].strip()
        if actor_user_agent is None:
            actor_user_agent = request.headers.get("User-Agent")

    if details is None:
        details = {}

    try:
        execute(
            """
            INSERT INTO audit_events
                (tenant_id, user_id, event_type, resource_type, resource_id,
                 actor_ip, actor_user_agent, details)
            VALUES
                (%s, %s, %s, %s, %s, %s::inet, %s, %s::jsonb)
            """,
            [
                tenant_id,
                user_id,
                event_type,
                resource_type,
                resource_id,
                actor_ip,
                actor_user_agent,
                json.dumps(details),
            ],
        )
    except Exception as exc:
        logger.error(
            "audit_service: failed to write event %s (tenant=%s user=%s): %s",
            event_type,
            tenant_id,
            user_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Convenience helper for route handlers
# ---------------------------------------------------------------------------

def log_from_request(event_type: str, request, user: dict, **kwargs) -> None:
    """
    Shorthand for routes: pass current user dict and Flask request.
    Extracts tenant_id and user_id from user dict automatically.

    The user dict is expected to have keys: id, tenant_id, role, email.
    Any additional keyword arguments are forwarded directly to log_event.
    """
    log_event(
        event_type=event_type,
        tenant_id=user.get("tenant_id"),
        user_id=user.get("id"),
        request=request,
        **kwargs,
    )
