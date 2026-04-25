"""Inbound email processing service.

Handles all business logic for turning an inbound email webhook payload
into either a new ticket or a threaded comment on an existing ticket.
Called by routes/webhooks.py after auth/signature verification.
"""

import logging
import re

from models.db import fetch_one, insert_returning, execute
from services.email_service import _is_blocked_email

logger = logging.getLogger(__name__)


# ============================================================
# Internal helpers
# ============================================================

def _resolve_tenant(slug: str) -> dict | None:
    """Look up an active tenant by slug.

    Returns the tenant row (id, name, inbound_email_enabled) or None if not
    found / inactive.
    """
    return fetch_one(
        "SELECT id, name, inbound_email_enabled FROM tenants WHERE slug = %s AND is_active = true",
        [slug],
    )


def _parse_sender_email(from_field: str) -> tuple[str, str]:
    """Extract (email, display_name) from 'Name <email>' or bare 'email' input."""
    m = re.match(r'^(.+?)\s*<([^>]+)>\s*$', from_field.strip())
    if m:
        return m.group(2).strip().lower(), m.group(1).strip().strip('"\'')
    return from_field.strip().lower(), ""


def _is_blocked(email: str) -> bool:
    """Check email against the default anti-loop blocklist.

    Delegates to email_service._is_blocked_email which handles fnmatch
    patterns and the DEFAULT_BLOCKLIST (noreply, freshdesk, zendesk, etc.).
    Tenant-specific blocklist is not available at webhook call time — the
    tenant row does not carry it — so only the platform-level list is checked
    here, consistent with the original inline implementation.
    """
    return _is_blocked_email(email)


def _find_thread(tenant_id: int, subject: str) -> dict | None:
    """Detect a TKT-##### token in the subject and return the matching ticket row.

    Returns the ticket row (id, ticket_number) if found and it belongs to
    this tenant, otherwise None.
    """
    match = re.search(r'\[TKT-(\d+)\]', subject, re.IGNORECASE)
    if not match:
        return None
    ticket_number = f"TKT-{match.group(1).zfill(5)}"
    return fetch_one(
        "SELECT id, ticket_number FROM tickets WHERE ticket_number = %s AND tenant_id = %s",
        [ticket_number, tenant_id],
    )


def _find_or_create_user(tenant_id: int, email: str, name: str) -> dict:
    """Return existing user dict or insert a new end_user for the inbound sender."""
    user = fetch_one(
        "SELECT id, name FROM users WHERE LOWER(email) = %s AND tenant_id = %s",
        [email.lower(), tenant_id],
    )
    if user:
        return {"id": user["id"], "name": user["name"] or name or email}
    user_id = insert_returning(
        """INSERT INTO users (tenant_id, email, name, role, is_active)
           VALUES (%s, %s, %s, 'end_user', true) RETURNING id""",
        [tenant_id, email, name or email],
    )
    return {"id": user_id, "name": name or email}


def _add_comment_from_email(tenant_id: int, ticket_id: int, user: dict, body_text: str) -> None:
    """Insert a public comment on an existing ticket and fire the requester_reply notification."""
    content = body_text or "(empty reply)"
    insert_returning(
        """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, author_name)
           VALUES (%s, %s, %s, false, %s) RETURNING id""",
        [ticket_id, user["id"], content, user["name"]],
    )
    from services.queue_service import enqueue_notify
    enqueue_notify(tenant_id, ticket_id, "requester_reply",
                   comment={"author_name": user["name"], "content": content})


def _create_ticket_from_email(tenant_id: int, user_id: int, subject: str, body_text: str) -> tuple[int, str]:
    """Insert a new ticket sourced from email.

    Returns (ticket_id, ticket_number).
    Applies the p3 SLA policy, fires notifications, and triggers automations.
    """
    tn_row = fetch_one("SELECT nextval('helpdesk.ticket_number_seq') as num")
    ticket_number = f"TKT-{tn_row['num']:05d}"

    ticket_id = insert_returning(
        """INSERT INTO tickets (tenant_id, ticket_number, subject, description,
                                priority, requester_id, source)
           VALUES (%s, %s, %s, %s, 'p3', %s, 'email')
           RETURNING id""",
        [tenant_id, ticket_number, subject, body_text, user_id],
    )

    # Apply SLA policy for p3
    sla_policy = fetch_one(
        "SELECT * FROM sla_policies WHERE tenant_id = %s AND priority = 'p3' LIMIT 1",
        [tenant_id],
    )
    if sla_policy:
        sla_updates = ["sla_policy_id = %s"]
        sla_params = [sla_policy["id"]]
        if sla_policy.get("resolution_minutes"):
            sla_updates.append(
                f"sla_due_at = created_at + interval '{sla_policy['resolution_minutes']} minutes'"
            )
        if sla_policy.get("first_response_minutes"):
            sla_updates.append(
                f"sla_first_response_due = created_at + interval '{sla_policy['first_response_minutes']} minutes'"
            )
        sla_params.append(ticket_id)
        execute(f"UPDATE tickets SET {', '.join(sla_updates)} WHERE id = %s", sla_params)

    # Notify + Atlas pipeline
    from services.queue_service import enqueue_notify, enqueue_ticket_create
    enqueue_notify(tenant_id, ticket_id, "ticket_created")
    enqueue_ticket_create(ticket_id, tenant_id, "p3")

    # Automations
    try:
        from services.automation_engine import fire_automations
        fire_automations("ticket_created", ticket_id, tenant_id, {})
    except Exception as e:
        logger.warning("Inbound email: automation dispatch failed: %s", e)

    return ticket_id, ticket_number


# ============================================================
# Public entry point
# ============================================================

def process_inbound_email(
    tenant_slug: str,
    from_addr: str,
    subject: str,
    body_text: str,
    in_reply_to: str | None = None,
) -> dict:
    """Process an inbound email webhook payload.

    Resolves the tenant, checks for blocked senders, attempts thread
    detection, then either adds a comment to an existing ticket or creates
    a new one.

    Returns a dict with keys:
      - status: 'created' | 'threaded' | 'blocked' | 'error'
      - ticket_id: int or None
      - message: str
    """
    # 1. Resolve tenant
    tenant = _resolve_tenant(tenant_slug)
    if not tenant:
        logger.warning("Inbound email: unknown tenant slug '%s'", tenant_slug)
        return {
            "status": "blocked",
            "ticket_id": None,
            "message": "tenant not found",
        }

    if not tenant.get("inbound_email_enabled", True):
        return {
            "status": "blocked",
            "ticket_id": None,
            "message": "inbound email disabled for tenant",
        }

    tenant_id = tenant["id"]

    # 2. Parse sender
    sender_email, sender_name = _parse_sender_email(from_addr)

    # 3. Anti-loop blocklist check
    if _is_blocked(sender_email):
        logger.info("Inbound email: blocked sender %s", sender_email)
        return {
            "status": "blocked",
            "ticket_id": None,
            "message": "blocked sender",
        }

    # 4. Thread detection: [TKT-#####] in subject → add comment
    existing_ticket = _find_thread(tenant_id, subject)
    if existing_ticket:
        ticket_id = existing_ticket["id"]
        ticket_number = existing_ticket["ticket_number"]
        try:
            user = _find_or_create_user(tenant_id, sender_email, sender_name)
            _add_comment_from_email(tenant_id, ticket_id, user, body_text)
            logger.info("Inbound email: comment added to ticket %s", ticket_number)
            return {
                "status": "threaded",
                "ticket_id": ticket_id,
                "message": f"comment added to {ticket_number}",
            }
        except Exception as e:
            logger.exception(
                "Inbound email: failed to add comment to ticket %s", ticket_number
            )
            return {
                "status": "error",
                "ticket_id": ticket_id,
                "message": str(e),
            }

    # 5. New ticket
    try:
        user = _find_or_create_user(tenant_id, sender_email, sender_name)
        ticket_id, ticket_number = _create_ticket_from_email(
            tenant_id, user["id"], subject, body_text
        )
        logger.info(
            "Inbound email: ticket %s created for tenant %s from %s",
            ticket_number, tenant_id, sender_email,
        )
        return {
            "status": "created",
            "ticket_id": ticket_id,
            "message": f"ticket {ticket_number} created",
        }
    except Exception as e:
        logger.exception(
            "Inbound email: failed to create ticket for tenant %s from %s",
            tenant_id, sender_email,
        )
        return {
            "status": "error",
            "ticket_id": None,
            "message": str(e),
        }
