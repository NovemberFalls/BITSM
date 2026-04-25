"""SLA service: status computation, default policy creation, breach detection."""

import logging
from datetime import datetime, timezone, timedelta

from models.db import fetch_all, fetch_one, execute, insert_returning

logger = logging.getLogger(__name__)


def compute_sla_status(ticket: dict) -> str:
    """Compute real-time SLA status from ticket data."""
    if not ticket.get("sla_due_at"):
        return "no_sla"

    due = ticket["sla_due_at"]
    if isinstance(due, str):
        due = datetime.fromisoformat(due)

    now = datetime.now(timezone.utc)

    if ticket.get("sla_breached") or due < now:
        return "breached"
    if due < now + timedelta(hours=1):
        return "at_risk"
    return "on_track"


def ensure_default_sla(tenant_id: int):
    """Create default SLA policies for a tenant if none exist."""
    existing = fetch_one(
        "SELECT count(*) as cnt FROM sla_policies WHERE tenant_id = %s",
        [tenant_id],
    )
    if existing and existing["cnt"] > 0:
        return

    defaults = [
        ("P1 — Urgent", "p1", 15, 60, False),
        ("P2 — High", "p2", 30, 240, False),
        ("P3 — Medium", "p3", 120, 480, True),
        ("P4 — Low", "p4", 480, 1440, True),
    ]
    for name, priority, first_resp, resolution, biz_hours in defaults:
        insert_returning(
            """INSERT INTO sla_policies (tenant_id, name, priority, first_response_minutes, resolution_minutes, business_hours_only)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            [tenant_id, name, priority, first_resp, resolution, biz_hours],
        )
    logger.info("Created default SLA policies for tenant %s", tenant_id)


def check_sla_breaches(ticket_ids: list[int]):
    """Mark newly-breached tickets and send SLA warnings. Called after listing."""
    if not ticket_ids:
        return
    try:
        from services.notification_service import notify_ticket_event

        # SLA warnings: tickets within 1 hour of deadline (not yet breached)
        # Skip tickets that already received an sla_warning notification
        at_risk = fetch_all(
            """SELECT t.id, t.tenant_id FROM tickets t
               WHERE t.id = ANY(%s)
                 AND t.sla_due_at IS NOT NULL
                 AND t.sla_due_at BETWEEN now() AND now() + interval '1 hour'
                 AND t.sla_breached = false
                 AND t.status NOT IN ('resolved', 'closed_not_resolved')
                 AND NOT EXISTS (
                     SELECT 1 FROM notifications n
                     WHERE n.ticket_id = t.id AND n.channel = 'in_app'
                       AND n.recipient = 'sla_warning'
                 )""",
            [ticket_ids],
        )
        for row in at_risk:
            notify_ticket_event(row["tenant_id"], row["id"], "sla_warning")
        if at_risk:
            logger.info("Sent SLA warnings for %d tickets", len(at_risk))

        # SLA breaches: tickets past deadline
        breached = fetch_all(
            """UPDATE tickets SET sla_breached = true, updated_at = now()
               WHERE id = ANY(%s)
                 AND sla_due_at < now()
                 AND sla_breached = false
                 AND status NOT IN ('resolved', 'closed_not_resolved')
               RETURNING id, tenant_id""",
            [ticket_ids],
        )
        if breached:
            for row in breached:
                notify_ticket_event(row["tenant_id"], row["id"], "sla_breach")
            logger.info("Marked %d tickets as SLA breached", len(breached))
    except Exception as e:
        logger.error("SLA breach check failed: %s", e)
