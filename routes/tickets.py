"""Ticket blueprint: CRUD, status transitions, assignment, SLA, tags.

Lifecycle events (create, close, notify) are dispatched to the internal
pipeline queue (PostgreSQL-backed, with retry, priority, and monitoring).
"""

import logging
import os
from uuid import uuid4

from flask import Blueprint, jsonify, request, session, send_file

from app import limiter
from config import Config
from routes.auth import login_required, require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one, insert_returning, execute
from services.sla_service import check_sla_breaches
from services.atlas_service import set_passive_on_assignment

ATTACHMENT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "attachments")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml",
    "application/pdf", "text/plain", "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel", "application/msword",
    "application/octet-stream",  # fallback for .log etc
}

logger = logging.getLogger(__name__)
tickets_bp = Blueprint("tickets", __name__)


# ============================================================
# Pipeline queue dispatch helpers
# ============================================================

def _dispatch_ticket_create(ticket_id: int, tenant_id: int, priority: str = "p3"):
    """Enqueue ticket-creation side effects (tag, enrich, engage, route)."""
    from services.queue_service import enqueue_ticket_create
    enqueue_ticket_create(ticket_id, tenant_id, priority)


def _dispatch_tag_only(ticket_id: int, tenant_id: int, priority: str = "p3"):
    """Enqueue tag-only pipeline for dev items — no triage/engage/route."""
    import json
    from datetime import datetime, timezone
    from services.queue_service import PRIORITY_MAP
    pval = PRIORITY_MAP.get(priority, 3)
    payload = json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "dev_item": True})
    insert_returning(
        """INSERT INTO pipeline_queue
           (tenant_id, ticket_id, step_name, priority, uses_llm, phase, payload)
           VALUES (%s, %s, 'auto_tag', %s, true, 0, %s::jsonb) RETURNING id""",
        [tenant_id, ticket_id, pval, payload],
    )


def _dispatch_ticket_close(ticket_id: int, tenant_id: int, priority: str = "p3"):
    """Enqueue ticket-close side effects (audit, effort)."""
    from services.queue_service import enqueue_ticket_close
    enqueue_ticket_close(ticket_id, tenant_id, priority)


def _dispatch_notify(tenant_id: int, ticket_id: int, event: str, comment: dict | None = None):
    """Enqueue a notification."""
    from services.queue_service import enqueue_notify
    enqueue_notify(tenant_id, ticket_id, event, comment=comment)


def _dispatch_automations(tenant_id: int, ticket_id: int, event_type: str, context: dict | None = None):
    """Fire matching automations for this ticket event."""
    try:
        from services.automation_engine import fire_automations
        fire_automations(event_type, ticket_id, tenant_id, context)
    except Exception as e:
        logger.warning("Automation dispatch failed for %s: %s", event_type, e)


def _dispatch_connectors(tenant_id: int, ticket_id: int, event: str, ticket_data: dict | None = None):
    """Fire active webhook connectors for this tenant (background, non-blocking)."""
    try:
        from services.connector_service import dispatch_webhook_connectors
        dispatch_webhook_connectors(tenant_id, ticket_id, event, ticket_data)
    except Exception as e:
        logger.warning("Connector dispatch failed for %s: %s", event, e)


def _log_activity(
    tenant_id: int,
    ticket_id: int,
    activity_type: str,
    user_id: int | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    metadata: dict | None = None,
):
    """Insert a row into ticket_activity for timeline tracking."""
    import json as _json
    try:
        execute(
            """INSERT INTO ticket_activity
               (tenant_id, ticket_id, user_id, activity_type, old_value, new_value, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)""",
            [
                tenant_id, ticket_id, user_id, activity_type,
                old_value, new_value,
                _json.dumps(metadata or {}),
            ],
        )
    except Exception as e:
        logger.warning("Failed to log activity for ticket %s: %s", ticket_id, e)

def _upsert_custom_field_values(ticket_id: int, tenant_id: int, values: dict, set_by: int | None = None):
    """Upsert custom field values for a ticket.

    `values` is a dict of {field_key_or_id: value}.
    Only saves values for fields that belong to this tenant and are active.
    """
    import json as _json
    if not values:
        return
    # Load active field definitions for this tenant to map key→id
    defs = fetch_all(
        "SELECT id, field_key FROM custom_field_definitions WHERE tenant_id = %s AND is_active = true",
        [tenant_id],
    )
    key_to_id = {d["field_key"]: d["id"] for d in defs}
    id_set = {d["id"] for d in defs}

    for k, v in values.items():
        # Accept both field_key strings and numeric ids
        try:
            fid = int(k)
            if fid not in id_set:
                continue
        except (ValueError, TypeError):
            fid = key_to_id.get(str(k))
            if not fid:
                continue

        execute(
            """INSERT INTO ticket_custom_field_values (ticket_id, field_id, value, set_by, set_at)
               VALUES (%s, %s, %s::jsonb, %s, now())
               ON CONFLICT (ticket_id, field_id)
               DO UPDATE SET value = EXCLUDED.value, set_by = EXCLUDED.set_by, set_at = now()""",
            [ticket_id, fid, _json.dumps(v), set_by],
        )


def _load_custom_field_defs(tenant_id: int, ticket_type: str, category_id: int | None) -> list:
    """Return active custom field definitions for a ticket's type/category combination.

    Fields inherit down the category tree: a field attached to any ancestor category
    (or the ticket's own category) is included. Global fields (category_id IS NULL)
    are filtered by applies_to ticket type.
    """
    if category_id:
        return fetch_all(
            """WITH RECURSIVE cat_ancestors AS (
                   SELECT id FROM problem_categories WHERE id = %s
                   UNION ALL
                   SELECT pc.parent_id
                   FROM problem_categories pc
                   JOIN cat_ancestors ca ON pc.id = ca.id
                   WHERE pc.parent_id IS NOT NULL
               )
               SELECT * FROM custom_field_definitions
               WHERE tenant_id = %s AND is_active = true
                 AND (category_id IN (SELECT id FROM cat_ancestors)
                      OR (category_id IS NULL AND %s = ANY(applies_to)))
               ORDER BY sort_order, id""",
            [category_id, tenant_id, ticket_type],
        )
    return fetch_all(
        """SELECT * FROM custom_field_definitions
           WHERE tenant_id = %s AND is_active = true
             AND category_id IS NULL AND %s = ANY(applies_to)
           ORDER BY sort_order, id""",
        [tenant_id, ticket_type],
    )


def _validate_required_to_create(
    tenant_id: int, ticket_type: str, category_id: int | None,
    provided: dict, raise_on_missing: bool = False,
):
    """Raise 400 if any is_required_to_create field is not supplied.

    Hidden fields (whose parent show_when condition is not met) are skipped.
    """
    from flask import abort
    defs = _load_custom_field_defs(tenant_id, ticket_type, category_id)
    # Build lookup by id for parent checks
    defs_by_id = {fd["id"]: fd for fd in defs}
    defs_by_key = {fd["field_key"]: fd for fd in defs}
    missing = []
    for fd in defs:
        if not fd.get("is_required_to_create"):
            continue
        # Skip fields whose parent condition is not met (hidden fields)
        if fd.get("parent_field_id"):
            parent = defs_by_id.get(fd["parent_field_id"])
            if parent and fd.get("show_when"):
                parent_val = (provided or {}).get(parent["field_key"])
                show_when = fd["show_when"]
                trigger_vals = show_when.get("values") or ([show_when["value"]] if show_when.get("value") else [])
                if str(parent_val) not in [str(v) for v in trigger_vals]:
                    continue  # parent condition not met — field is hidden, skip
        key = fd["field_key"]
        val = provided.get(key) if provided else None
        if val is None or val == "" or val == []:
            missing.append(fd["name"])
    if missing and raise_on_missing:
        abort(400, description=f"Required fields missing: {', '.join(missing)}")
    return missing


def _get_missing_required_to_close(ticket_id: int, tenant_id: int, ticket_type: str) -> list[str]:
    """Return names of is_required_to_close fields that have no value yet.

    Hidden fields (whose parent show_when condition is not met) are skipped.
    """
    ticket = fetch_one("SELECT problem_category_id FROM tickets WHERE id = %s", [ticket_id])
    if not ticket:
        return []
    category_id = ticket.get("problem_category_id")
    defs = _load_custom_field_defs(tenant_id, ticket_type, category_id)
    defs_by_id = {fd["id"]: fd for fd in defs}
    close_defs = [f for f in defs if f.get("is_required_to_close")]
    if not close_defs:
        return []
    # Load ALL field values for this ticket to check parent conditions
    all_vals = fetch_all(
        "SELECT cf.field_key, cv.value, cv.field_id FROM ticket_custom_field_values cv "
        "JOIN custom_field_definitions cf ON cf.id = cv.field_id WHERE cv.ticket_id = %s",
        [ticket_id],
    )
    vals_by_key = {r["field_key"]: r["value"] for r in all_vals}
    filled_ids = set()
    for r in all_vals:
        v = r.get("value")
        if v is not None and v != "" and v != [] and v != "null":
            filled_ids.add(r["field_id"])

    missing = []
    for fd in close_defs:
        # Skip hidden fields whose parent condition is not met
        if fd.get("parent_field_id"):
            parent = defs_by_id.get(fd["parent_field_id"])
            if parent and fd.get("show_when"):
                parent_val = vals_by_key.get(parent["field_key"])
                show_when = fd["show_when"]
                trigger_vals = show_when.get("values") or ([show_when["value"]] if show_when.get("value") else [])
                if str(parent_val) not in [str(v) for v in trigger_vals]:
                    continue
        if fd["id"] not in filled_ids:
            missing.append(fd["name"])
    return missing


# Allowlist for sort columns to prevent SQL injection
SORT_COLUMNS = {
    "created_at": "t.created_at",
    "updated_at": "t.updated_at",
    "sla_due_at": "t.sla_due_at",
    "priority": "CASE t.priority WHEN 'p1' THEN 1 WHEN 'p2' THEN 2 WHEN 'p3' THEN 3 WHEN 'p4' THEN 4 END",
    "priority_age": None,  # compound sort handled separately
}


def _next_ticket_number(tenant_id: int) -> str:
    """Atomically increment per-tenant ticket counter and return formatted number.

    Uses an UPDATE ... RETURNING to guarantee uniqueness even under concurrent
    inserts for the same tenant. Format: {prefix}-{seq:05d} e.g. ACME-00042.
    """
    row = fetch_one(
        """
        UPDATE tenants
        SET ticket_seq_last = ticket_seq_last + 1
        WHERE id = %s
        RETURNING ticket_seq_last, ticket_prefix
        """,
        [tenant_id],
    )
    if not row:
        raise ValueError(f"Tenant {tenant_id} not found when generating ticket number")
    prefix = (row["ticket_prefix"] or "TKT").upper().strip()
    return f"{prefix}-{row['ticket_seq_last']:05d}"


def _apply_sla(ticket_id: int, tenant_id: int, priority: str):
    """Look up SLA policy and set due dates on the ticket."""
    policy = fetch_one(
        "SELECT * FROM sla_policies WHERE tenant_id = %s AND priority = %s LIMIT 1",
        [tenant_id, priority],
    )
    if not policy:
        return
    updates = ["sla_policy_id = %s"]
    params = [policy["id"]]
    if policy.get("resolution_minutes"):
        updates.append("sla_due_at = created_at + make_interval(mins => %s)")
        params.append(int(policy["resolution_minutes"]))
    if policy.get("first_response_minutes"):
        updates.append("sla_first_response_due = created_at + make_interval(mins => %s)")
        params.append(int(policy["first_response_minutes"]))
    params.append(ticket_id)
    execute(f"UPDATE tickets SET {', '.join(updates)} WHERE id = %s", params)


def _get_breadcrumb(table: str, node_id: int, max_depth: int = 10) -> str:
    """Build breadcrumb string for a hierarchical node (locations or problem_categories)."""
    ALLOWED_BREADCRUMB_TABLES = {"locations", "problem_categories"}
    if table not in ALLOWED_BREADCRUMB_TABLES:
        return ""
    parts = []
    current_id = node_id
    for _ in range(max_depth):
        row = fetch_one(f"SELECT id, name, parent_id FROM {table} WHERE id = %s", [current_id])
        if not row:
            break
        parts.append(row["name"])
        if not row["parent_id"]:
            break
        current_id = row["parent_id"]
    parts.reverse()
    return " > ".join(parts)


# ============================================================
# List / Search
# ============================================================

@tickets_bp.route("", methods=["GET"])
@login_required
def list_tickets():
    user = get_current_user()
    tenant_id = get_tenant_id()

    # Pagination
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    # Sort params
    sort_by = request.args.get("sort_by", "priority_age")
    sort_dir = request.args.get("sort_dir", "")

    conditions = []
    params = []
    ctes = []  # Common Table Expressions

    # Tenant scoping (super_admin sees all)
    if user["role"] != "super_admin" and tenant_id:
        conditions.append("t.tenant_id = %s")
        params.append(tenant_id)

    # End-user scoping: only see own tickets
    if user["role"] == "end_user":
        conditions.append("t.requester_id = %s")
        params.append(user["id"])

    # --- Filters ---
    status = request.args.get("status")
    if status:
        conditions.append("t.status = %s")
        params.append(status)

    # Multi-status filter (comma-separated, e.g. "open,pending")
    status_in = request.args.get("status_in")
    if status_in and not status:
        statuses = [s.strip() for s in status_in.split(",") if s.strip()]
        if statuses:
            conditions.append("t.status = ANY(%s::text[])")
            params.append(statuses)

    priority = request.args.get("priority")
    if priority:
        conditions.append("t.priority = %s")
        params.append(priority)

    assignee_id = request.args.get("assignee_id")
    if assignee_id:
        # Magic value: "__me__" resolves to current user
        if assignee_id == "__me__":
            assignee_id = str(user["id"])
        conditions.append("t.assignee_id = %s")
        params.append(int(assignee_id))

    requester_id = request.args.get("requester_id")
    if requester_id:
        conditions.append("t.requester_id = %s")
        params.append(int(requester_id))

    # Location filter with descendant inclusion
    location_id = request.args.get("location_id")
    if location_id:
        ctes.append(
            "loc_tree AS ("
            "  SELECT id FROM locations WHERE id = %s"
            "  UNION ALL"
            "  SELECT l.id FROM locations l JOIN loc_tree lt ON l.parent_id = lt.id WHERE l.is_active = true"
            ")"
        )
        params.insert(0, int(location_id))  # CTE params go first
        conditions.append("t.location_id IN (SELECT id FROM loc_tree)")

    # Problem category filter with descendant inclusion
    problem_category_id = request.args.get("problem_category_id")
    if problem_category_id:
        ctes.append(
            "cat_tree AS ("
            "  SELECT id FROM problem_categories WHERE id = %s"
            "  UNION ALL"
            "  SELECT c.id FROM problem_categories c JOIN cat_tree ct ON c.parent_id = ct.id WHERE c.is_active = true"
            ")"
        )
        params.insert(len(ctes) - 1, int(problem_category_id))  # CTE params go first
        conditions.append("t.problem_category_id IN (SELECT id FROM cat_tree)")

    # Tag filter
    tag = request.args.get("tag")
    if tag:
        conditions.append("%s = ANY(t.tags)")
        params.append(tag.strip().lower())

    # SLA status filter
    sla_status = request.args.get("sla_status")
    if sla_status == "breached":
        conditions.append(
            "(t.sla_breached = true OR (t.sla_due_at < now() AND t.status NOT IN ('resolved', 'closed_not_resolved')))"
        )
    elif sla_status == "at_risk":
        conditions.append(
            "t.sla_due_at IS NOT NULL AND t.sla_due_at BETWEEN now() AND now() + interval '1 hour'"
            " AND t.sla_breached = false AND t.status NOT IN ('resolved', 'closed_not_resolved')"
        )
    elif sla_status == "on_track":
        conditions.append(
            "t.sla_due_at IS NOT NULL AND t.sla_due_at > now() + interval '1 hour'"
            " AND t.sla_breached = false AND t.status NOT IN ('resolved', 'closed_not_resolved')"
        )
    elif sla_status == "no_sla":
        conditions.append("t.sla_due_at IS NULL")

    # Date range filters
    created_after = request.args.get("created_after")
    if created_after:
        conditions.append("t.created_at >= %s::timestamptz")
        params.append(created_after)

    created_before = request.args.get("created_before")
    if created_before:
        conditions.append("t.created_at <= %s::timestamptz")
        params.append(created_before)

    # Team filter
    team_id = request.args.get("team_id")
    if team_id:
        conditions.append("t.team_id = %s")
        params.append(int(team_id))

    # Ticket type filter (supports comma-separated, e.g. "task,bug,feature")
    ticket_type = request.args.get("ticket_type")
    if ticket_type:
        types = [t.strip() for t in ticket_type.split(",") if t.strip()]
        if len(types) == 1:
            conditions.append("t.ticket_type = %s")
            params.append(types[0])
        elif types:
            conditions.append("t.ticket_type = ANY(%s::text[])")
            params.append(types)

    # Sprint filter
    sprint_id = request.args.get("sprint_id")
    if sprint_id == "null":
        conditions.append("t.sprint_id IS NULL")
    elif sprint_id:
        conditions.append("t.sprint_id = %s")
        params.append(int(sprint_id))

    # Text search
    search = request.args.get("search")
    if search:
        conditions.append("(t.ticket_number ILIKE %s OR t.work_item_number ILIKE %s OR t.subject ILIKE %s OR t.description ILIKE %s)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern, pattern])

    # Build WHERE clause
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Build ORDER BY
    if sort_by == "priority_age":
        order_by = (
            "ORDER BY CASE t.priority WHEN 'p1' THEN 1 WHEN 'p2' THEN 2 WHEN 'p3' THEN 3 WHEN 'p4' THEN 4 END ASC, "
            "t.created_at ASC"
        )
    elif sort_by in SORT_COLUMNS and SORT_COLUMNS[sort_by]:
        direction = sort_dir.upper() if sort_dir.upper() in ("ASC", "DESC") else "DESC"
        order_by = f"ORDER BY {SORT_COLUMNS[sort_by]} {direction} NULLS LAST"
    else:
        order_by = "ORDER BY t.created_at DESC"

    # Build CTE prefix
    cte_prefix = ""
    if ctes:
        cte_prefix = "WITH RECURSIVE " + ", ".join(ctes) + " "

    # Count params (before adding limit/offset)
    count_params = list(params)
    params.extend([limit, offset])

    tickets = fetch_all(
        f"""{cte_prefix}SELECT t.*,
                   u_req.name as requester_name,
                   u_asg.name as assignee_name,
                   u_asg.email as assignee_email,
                   loc.name as location_name,
                   pc.name as problem_category_name,
                   tm.name as team_name,
                   sp.name as sprint_name,
                   EXTRACT(EPOCH FROM (now() - t.created_at))::int as age_seconds,
                   CASE
                     WHEN t.sla_due_at IS NULL THEN 'no_sla'
                     WHEN t.sla_breached OR t.sla_due_at < now() THEN 'breached'
                     WHEN t.sla_due_at < now() + interval '1 hour' THEN 'at_risk'
                     ELSE 'on_track'
                   END as sla_status,
                   (SELECT CASE
                      WHEN lc.is_ai_generated THEN 'Atlas'
                      WHEN lc.author_id = t.requester_id THEN 'Client'
                      ELSE COALESCE(lu.name, 'Agent')
                    END
                    FROM ticket_comments lc
                    LEFT JOIN users lu ON lu.id = lc.author_id
                    WHERE lc.ticket_id = t.id AND lc.is_internal = false
                    ORDER BY lc.created_at DESC LIMIT 1
                   ) as last_responder
            FROM tickets t
            LEFT JOIN users u_req ON u_req.id = t.requester_id
            LEFT JOIN users u_asg ON u_asg.id = t.assignee_id
            LEFT JOIN locations loc ON loc.id = t.location_id
            LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
            LEFT JOIN teams tm ON tm.id = t.team_id
            LEFT JOIN sprints sp ON sp.id = t.sprint_id
            {where}
            {order_by}
            LIMIT %s OFFSET %s""",
        params,
    )

    total = fetch_one(
        f"""{cte_prefix}SELECT count(*) as cnt FROM tickets t {where}""",
        count_params,
    )

    # Check for SLA breaches on returned tickets
    if tickets:
        check_sla_breaches([t["id"] for t in tickets])

    return jsonify({"tickets": tickets, "total": total["cnt"]})


# ============================================================
# Get single ticket
# ============================================================

@tickets_bp.route("/<int:ticket_id>", methods=["GET"])
@login_required
def get_ticket(ticket_id: int):
    user = get_current_user()

    # Tenant-scoped fetch (super_admin bypasses)
    if user["role"] == "super_admin":
        ticket_params = [ticket_id]
        tenant_filter = ""
    else:
        ticket_params = [ticket_id, get_tenant_id()]
        tenant_filter = " AND t.tenant_id = %s"

    ticket = fetch_one(
        f"""SELECT t.*,
                  u_req.name as requester_name, u_req.email as requester_email,
                  u_asg.name as assignee_name, u_asg.email as assignee_email,
                  ten.name as tenant_name,
                  EXTRACT(EPOCH FROM (now() - t.created_at))::int as age_seconds,
                  CASE
                    WHEN t.sla_due_at IS NULL THEN 'no_sla'
                    WHEN t.sla_breached OR t.sla_due_at < now() THEN 'breached'
                    WHEN t.sla_due_at < now() + interval '1 hour' THEN 'at_risk'
                    ELSE 'on_track'
                  END as sla_status
           FROM tickets t
           LEFT JOIN users u_req ON u_req.id = t.requester_id
           LEFT JOIN users u_asg ON u_asg.id = t.assignee_id
           LEFT JOIN tenants ten ON ten.id = t.tenant_id
           WHERE t.id = %s{tenant_filter}""",
        ticket_params,
    )
    if not ticket:
        return jsonify({"error": "Not found"}), 404

    # End-user can only see own tickets
    if user["role"] == "end_user" and ticket.get("requester_id") != user["id"]:
        return jsonify({"error": "Not found"}), 404

    try:
        from services.audit_service import log_from_request, TICKET_VIEWED
        from datetime import datetime, timezone
        # Throttle: only log ticket.viewed once per 5 minutes per ticket per session
        # to prevent flooding from the frontend's 5-second polling interval.
        session_key = f"viewed_{ticket_id}"
        last_viewed = session.get(session_key)
        now_ts = datetime.now(timezone.utc).timestamp()
        if not last_viewed or (now_ts - last_viewed) > 300:
            log_from_request(TICKET_VIEWED, request, user, resource_type="ticket", resource_id=ticket_id)
            session[session_key] = now_ts
    except Exception:
        pass

    # Build breadcrumbs for location and problem category
    if ticket.get("location_id"):
        ticket["location_breadcrumb"] = _get_breadcrumb("locations", ticket["location_id"])
    else:
        ticket["location_breadcrumb"] = None

    if ticket.get("problem_category_id"):
        ticket["problem_category_breadcrumb"] = _get_breadcrumb("problem_categories", ticket["problem_category_id"])
    else:
        ticket["problem_category_breadcrumb"] = None

    comments = fetch_all(
        """SELECT c.*,
                  CASE WHEN c.is_ai_generated THEN 'Atlas' ELSE u.name END as author_name
           FROM ticket_comments c
           LEFT JOIN users u ON u.id = c.author_id
           WHERE c.ticket_id = %s
           ORDER BY c.created_at""",
        [ticket_id],
    )

    # For end_users, filter out internal comments
    if user["role"] == "end_user":
        comments = [c for c in comments if not c.get("is_internal")]

    # Attach file attachments to comments
    comment_ids = [c["id"] for c in comments]
    if comment_ids:
        placeholders = ",".join(["%s"] * len(comment_ids))
        attachments = fetch_all(
            f"""SELECT id, comment_id, filename, file_size, content_type
                FROM ticket_attachments
                WHERE comment_id IN ({placeholders})
                ORDER BY created_at""",
            comment_ids,
        )
        att_by_comment: dict = {}
        for att in attachments:
            att_by_comment.setdefault(att["comment_id"], []).append(att)
        for c in comments:
            c["attachments"] = att_by_comment.get(c["id"], [])

    # Tag suggestions
    tag_suggestions = fetch_all(
        "SELECT * FROM tag_suggestions WHERE ticket_id = %s ORDER BY created_at",
        [ticket_id],
    )

    # Custom field definitions: category-scoped OR global (category_id IS NULL) by ticket type
    ticket_tenant_id = ticket.get("tenant_id")
    ticket_type_val = ticket.get("ticket_type", "support")
    problem_cat_id = ticket.get("problem_category_id")

    if ticket_tenant_id:
        if problem_cat_id:
            cf_defs = fetch_all(
                """WITH RECURSIVE cat_ancestors AS (
                       SELECT id FROM problem_categories WHERE id = %s
                       UNION ALL
                       SELECT pc.parent_id
                       FROM problem_categories pc
                       JOIN cat_ancestors ca ON pc.id = ca.id
                       WHERE pc.parent_id IS NOT NULL
                   )
                   SELECT * FROM custom_field_definitions
                   WHERE tenant_id = %s AND is_active = true
                     AND (category_id IN (SELECT id FROM cat_ancestors)
                          OR (category_id IS NULL AND %s = ANY(applies_to)))
                   ORDER BY sort_order, id""",
                [problem_cat_id, ticket_tenant_id, ticket_type_val],
            )
        else:
            cf_defs = fetch_all(
                """SELECT * FROM custom_field_definitions
                   WHERE tenant_id = %s AND is_active = true
                     AND category_id IS NULL AND %s = ANY(applies_to)
                   ORDER BY sort_order, id""",
                [ticket_tenant_id, ticket_type_val],
            )
    else:
        cf_defs = []

    # Filter by visibility: end_users only see customer-facing fields
    if user["role"] == "end_user":
        cf_defs = [f for f in cf_defs if f.get("is_customer_facing")]

    # Values for this ticket
    cf_values_raw = fetch_all(
        "SELECT field_id, value FROM ticket_custom_field_values WHERE ticket_id = %s",
        [ticket_id],
    ) if cf_defs else []
    cf_values = {row["field_id"]: row["value"] for row in cf_values_raw}

    # Attach current value to each definition
    for f in cf_defs:
        f["current_value"] = cf_values.get(f["id"])

    return jsonify({"ticket": ticket, "comments": comments, "tag_suggestions": tag_suggestions, "custom_fields": cf_defs})


# ============================================================
# Lightweight comments refresh (for polling)
# ============================================================

@tickets_bp.route("/<int:ticket_id>/comments", methods=["GET"])
@login_required
def get_comments(ticket_id: int):
    """Return just the comments for a ticket (lightweight, no full ticket reload)."""
    user = get_current_user()

    # Verify ticket exists and user has access (tenant-scoped; super_admin bypasses)
    if user["role"] == "super_admin":
        ticket = fetch_one("SELECT requester_id, tenant_id FROM tickets WHERE id = %s", [ticket_id])
    else:
        ticket = fetch_one("SELECT requester_id, tenant_id FROM tickets WHERE id = %s AND tenant_id = %s", [ticket_id, get_tenant_id()])
    if not ticket:
        return jsonify({"error": "Not found"}), 404

    if user["role"] == "end_user" and ticket.get("requester_id") != user["id"]:
        return jsonify({"error": "Not found"}), 404

    comments = fetch_all(
        """SELECT c.*,
                  CASE WHEN c.is_ai_generated THEN 'Atlas' ELSE u.name END as author_name
           FROM ticket_comments c
           LEFT JOIN users u ON u.id = c.author_id
           WHERE c.ticket_id = %s
           ORDER BY c.created_at""",
        [ticket_id],
    )

    # For end_users, filter out internal comments
    if user["role"] == "end_user":
        comments = [c for c in comments if not c.get("is_internal")]

    return jsonify({"comments": comments})


# ============================================================
# Create ticket
# ============================================================

@tickets_bp.route("", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def create_ticket():
    user = get_current_user()
    data = request.json or {}

    tenant_id = data.get("tenant_id") or get_tenant_id()
    if not tenant_id and user["role"] != "super_admin":
        return jsonify({"error": "Tenant context required"}), 400

    # Enforce required built-in fields from tenant settings
    subject = data.get("subject", "").strip()
    ticket_type = data.get("ticket_type", "support")

    tenant_row = fetch_one("SELECT settings FROM tenants WHERE id = %s", [tenant_id]) if tenant_id else None
    form_settings = {}
    if tenant_row and isinstance(tenant_row.get("settings"), dict):
        raw = tenant_row["settings"].get("ticket_form_settings") or {}
        # Support per-type format: { "support": {...}, "task": {...} }
        # and legacy flat format: { "subject_required": true, ... }
        if ticket_type in raw and isinstance(raw[ticket_type], dict):
            form_settings = raw[ticket_type]
        else:
            form_settings = raw  # legacy flat format

    # Subject is always required unless admin explicitly turned it off
    # For custom type: default to not required (form is purely custom fields)
    subject_default = False if ticket_type == "custom" else True
    if form_settings.get("subject_required", subject_default) and not subject:
        return jsonify({"error": "Subject is required"}), 400

    # Auto-generate subject for custom tickets if not provided
    if not subject and ticket_type == "custom":
        template_id = data.get("form_template_id")
        if template_id:
            tmpl = fetch_one("SELECT name, subject_format FROM form_templates WHERE id = %s", [template_id])
            if tmpl and tmpl.get("subject_format"):
                import re
                fmt = tmpl["subject_format"]
                cf_values = data.get("custom_fields") or {}
                subject = re.sub(
                    r"\{\{(\w+)\}\}",
                    lambda m: str(cf_values.get(m.group(1), "")),
                    fmt,
                ).strip()
                if not subject:
                    subject = tmpl["name"]
            elif tmpl:
                subject = tmpl["name"]
            else:
                subject = "Custom Request"
        else:
            subject = "Custom Request"

    if form_settings.get("description_required") and not data.get("description", "").strip():
        return jsonify({"error": "Description is required"}), 400

    if form_settings.get("location_required") and not data.get("location_id"):
        return jsonify({"error": "Location is required"}), 400

    if form_settings.get("category_required") and not data.get("problem_category_id"):
        return jsonify({"error": "Problem category is required"}), 400

    ticket_number = _next_ticket_number(tenant_id)
    priority = data.get("priority", "p3")
    if priority not in ("p1", "p2", "p3", "p4"):
        priority = "p3"
    source = data.get("source", "web")
    problem_category_id = data.get("problem_category_id")
    ticket_type = data.get("ticket_type", "support")

    # Validate ticket type
    from services.workflow_service import VALID_TICKET_TYPES, get_initial_status
    if ticket_type not in VALID_TICKET_TYPES:
        return jsonify({"error": f"Invalid ticket_type. Must be one of: {', '.join(VALID_TICKET_TYPES)}"}), 400

    # Enforce type-specific creation permission (super_admin bypasses)
    if user["role"] != "super_admin":
        type_perm = f"tickets.create.{ticket_type}"
        user_perms = user.get("permissions") or []
        if type_perm not in user_perms:
            return jsonify({"error": f"You do not have permission to create {ticket_type} tickets"}), 403

    initial_status = get_initial_status(ticket_type)

    # Allow staff to create tickets on behalf of others
    requester_id = user["id"]
    if data.get("requester_id") and user["role"] in ("super_admin", "tenant_admin", "agent"):
        requester_id = data["requester_id"]

    # Generate work item number for dev ticket types
    work_item_number = None
    if ticket_type in ("task", "bug", "feature"):
        wi_row = fetch_one("SELECT nextval('work_item_number_seq') as num")
        work_item_number = f"WI-{wi_row['num']:05d}"

    ticket_id = insert_returning(
        """INSERT INTO tickets (tenant_id, ticket_number, subject, description,
                                priority, category, tags, requester_id, source,
                                location_id, problem_category_id, team_id,
                                ticket_type, status, story_points, sprint_id,
                                work_item_type_id, acceptance_criteria, parent_id,
                                work_item_number,
                                form_template_id,
                                steps_to_reproduce, expected_behavior, actual_behavior)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        [
            tenant_id, ticket_number, subject, data.get("description", ""),
            priority, data.get("category"), data.get("tags", []),
            requester_id, source,
            data.get("location_id"), problem_category_id, data.get("team_id"),
            ticket_type, initial_status, data.get("story_points"), data.get("sprint_id"),
            data.get("work_item_type_id"), data.get("acceptance_criteria"), data.get("parent_id"),
            work_item_number,
            data.get("form_template_id"),
            data.get("steps_to_reproduce"), data.get("expected_behavior"), data.get("actual_behavior"),
        ],
    )

    # Apply category's default_priority if set
    if problem_category_id:
        cat = fetch_one(
            "SELECT default_priority FROM problem_categories WHERE id = %s",
            [problem_category_id],
        )
        if cat and cat.get("default_priority"):
            priority = cat["default_priority"]
            execute(
                "UPDATE tickets SET priority = %s WHERE id = %s",
                [priority, ticket_id],
            )

    _apply_sla(ticket_id, tenant_id, priority)

    # Contact profile: record location visit so Atlas can learn from it
    if requester_id and data.get("location_id"):
        try:
            from services.contact_profile_service import get_or_create_profile, record_location
            _cp_user = fetch_one(
                "SELECT email, phone, name FROM users WHERE id = %s", [requester_id]
            )
            if _cp_user:
                _cp = get_or_create_profile(
                    tenant_id=tenant_id,
                    user_id=requester_id,
                    email=_cp_user.get("email"),
                    phone=_cp_user.get("phone"),
                    name=_cp_user.get("name"),
                )
                if _cp:
                    record_location(_cp["id"], data["location_id"], tenant_id)
        except Exception as _cp_err:
            logger.warning("Contact profile update failed for ticket %s: %s", ticket_id, _cp_err)

    # Validate required-to-create fields before saving
    custom_fields_data = data.get("custom_fields") or {}
    if problem_category_id or ticket_type:
        _validate_required_to_create(
            tenant_id=tenant_id,
            ticket_type=ticket_type,
            category_id=problem_category_id,
            provided=custom_fields_data,
            raise_on_missing=True,
        )

    # Save any custom field values provided at creation time
    if custom_fields_data and isinstance(custom_fields_data, dict):
        _upsert_custom_field_values(ticket_id, tenant_id, custom_fields_data, user["id"])

    # Fire matching automations BEFORE notifications — automations may change
    # assignee, priority, status etc. and notifications should reflect final state
    _dispatch_automations(tenant_id, ticket_id, "ticket_created")

    # Dispatch notification — per-type event for non-support, plus generic ticket_created
    _dispatch_notify(tenant_id, ticket_id, "ticket_created")
    if ticket_type in ("task", "bug", "feature", "custom"):
        _dispatch_notify(tenant_id, ticket_id, f"{ticket_type}_created")

    # Dispatch AI side effects — full pipeline for support + custom tickets,
    # tag-only for dev items (tasks/bugs/features don't need triage)
    if ticket_type in ("task", "bug", "feature"):
        _dispatch_tag_only(ticket_id, tenant_id, priority)
    else:
        _dispatch_ticket_create(ticket_id, tenant_id, priority)

    # Fire webhook connectors
    _dispatch_connectors(tenant_id, ticket_id, "ticket_created")

    # Log creation activity for timeline
    _log_activity(tenant_id, ticket_id, "created", user_id=user["id"])

    return jsonify({"id": ticket_id, "ticket_number": ticket_number}), 201


# ============================================================
# Update ticket
# ============================================================

@tickets_bp.route("/<int:ticket_id>", methods=["PUT"])
@login_required
def update_ticket(ticket_id: int):
    user = get_current_user()
    data = request.json or {}
    allowed = ("subject", "description", "status", "priority", "category", "tags",
               "assignee_id", "requester_id", "location_id", "problem_category_id", "team_id",
               "story_points", "sprint_id", "acceptance_criteria", "work_item_type_id", "parent_id",
               "sort_order", "form_template_id",
               "steps_to_reproduce", "expected_behavior", "actual_behavior")
    fields, params = [], []
    for col in allowed:
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])

    # Tenant-scoped fetch (super_admin bypasses)
    if user["role"] == "super_admin":
        old = fetch_one("SELECT status, priority, tenant_id, assignee_id AS old_assignee_id, ticket_type, team_id, problem_category_id FROM tickets WHERE id = %s", [ticket_id])
    else:
        old = fetch_one("SELECT status, priority, tenant_id, assignee_id AS old_assignee_id, ticket_type, team_id, problem_category_id FROM tickets WHERE id = %s AND tenant_id = %s", [ticket_id, get_tenant_id()])
    if not old:
        return jsonify({"error": "Not found"}), 404

    # Validate status against ticket's workflow
    new_status = data.get("status")
    if new_status:
        from services.workflow_service import is_valid_status
        if not is_valid_status(old["tenant_id"], old.get("ticket_type", "support"), new_status):
            return jsonify({"error": f"Invalid status '{new_status}' for ticket type '{old.get('ticket_type', 'support')}'"}), 400

    # Status transition side effects
    new_status = data.get("status")
    if new_status == "resolved" and old["status"] != "resolved":
        fields.append("resolved_at = now()")
    if new_status == "closed_not_resolved" and old["status"] != "closed_not_resolved":
        fields.append("closed_at = now()")

    has_custom_fields = bool(data.get("custom_fields"))
    if not fields and not has_custom_fields:
        return jsonify({"error": "No fields to update"}), 400

    # Upsert custom field values BEFORE close check (so freshly filled values count)
    custom_fields_data = data.get("custom_fields") or {}
    if custom_fields_data and isinstance(custom_fields_data, dict):
        _upsert_custom_field_values(ticket_id, old["tenant_id"], custom_fields_data, user["id"])

    # Block close BEFORE writing status — ticket stays in previous state
    new_status = data.get("status")
    from services.workflow_service import is_done_status as _is_done
    _ticket_type = old.get("ticket_type", "support")
    if new_status and _is_done(old["tenant_id"], _ticket_type, new_status):
        missing = _get_missing_required_to_close(ticket_id, old["tenant_id"], _ticket_type)
        if missing and not data.get("force_close"):
            return jsonify({
                "error": f"Cannot close ticket: required fields are incomplete: {', '.join(missing)}",
                "missing_fields": missing,
                "code": "required_to_close",
            }), 422

    if fields:
        fields.append("updated_at = now()")
        params.append(ticket_id)
        execute(f"UPDATE tickets SET {', '.join(fields)} WHERE id = %s", params)

    # Auto-set completed_at/completed_by on status transitions
    if "status" in data:
        new_status = data["status"]
        _is_now_done = _is_done(old["tenant_id"], _ticket_type, new_status)
        if _is_now_done:
            execute(
                "UPDATE tickets SET completed_at = now(), completed_by = %s WHERE id = %s AND completed_at IS NULL",
                [user["id"], ticket_id],
            )
        else:
            # If un-completing (moving back), clear completed_at/completed_by
            execute(
                "UPDATE tickets SET completed_at = NULL, completed_by = NULL WHERE id = %s",
                [ticket_id],
            )

    tenant_id_for_notify = get_tenant_id() or old["tenant_id"]

    # Fire matching automations BEFORE notifications — automations may change
    # assignee, priority, status etc. and notifications should reflect final state
    if new_status and new_status != old["status"]:
        _dispatch_automations(tenant_id_for_notify, ticket_id, "status_changed", {"from": old["status"], "to": new_status})
        _dispatch_connectors(tenant_id_for_notify, ticket_id, "status_changed", {"status": new_status, "previous_status": old["status"]})
    if "priority" in data and data["priority"] != old["priority"]:
        _dispatch_automations(tenant_id_for_notify, ticket_id, "priority_changed", {"from": old["priority"], "to": data["priority"]})
        _dispatch_connectors(tenant_id_for_notify, ticket_id, "priority_changed", {"priority": data["priority"]})
    if "assignee_id" in data:
        _dispatch_automations(tenant_id_for_notify, ticket_id, "assignee_changed")

    # Notify on key changes — dispatched via pipeline queue (after automations)
    if "assignee_id" in data:
        _dispatch_notify(tenant_id_for_notify, ticket_id, "ticket_assigned")
        # Atlas goes passive when human takes over (sync — lightweight DB update)
        set_passive_on_assignment(ticket_id)
        # Handoff summary if this is a reassignment (not first assignment)
        if old.get("old_assignee_id") and data["assignee_id"] != old["old_assignee_id"]:
            from services.atlas_service import generate_handoff_summary
            generate_handoff_summary(ticket_id, tenant_id_for_notify)
    if new_status and _is_done(old["tenant_id"], _ticket_type, new_status):
        # Determine notification event based on status key
        if new_status == "resolved":
            _dispatch_notify(tenant_id_for_notify, ticket_id, "ticket_resolved")
            try:
                from services.email_service import send_csat_email
                send_csat_email(ticket_id, tenant_id_for_notify)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error("CSAT dispatch failed for ticket %s: %s", ticket_id, e)
        else:
            _dispatch_notify(tenant_id_for_notify, ticket_id, "ticket_closed")
        _dispatch_ticket_close(ticket_id, tenant_id_for_notify, old.get("priority", "p3"))
    elif new_status and new_status != old["status"]:
        _dispatch_notify(tenant_id_for_notify, ticket_id, "status_changed",
                         comment={"old_status": old["status"], "new_status": new_status})

    # Notify on priority change
    if "priority" in data and data["priority"] != old["priority"]:
        _dispatch_notify(tenant_id_for_notify, ticket_id, "priority_changed",
                         comment={"old_priority": old["priority"], "new_priority": data["priority"]})
    # Notify on team assignment
    if "team_id" in data:
        _dispatch_notify(tenant_id_for_notify, ticket_id, "team_assigned")
    # Notify on category change
    if "problem_category_id" in data:
        _dispatch_notify(tenant_id_for_notify, ticket_id, "category_changed")

    # ---- Activity timeline logging ----
    if new_status and new_status != old["status"]:
        _log_activity(tenant_id_for_notify, ticket_id, "status_changed",
                       user_id=user["id"], old_value=old["status"], new_value=new_status)
    if "priority" in data and data["priority"] != old["priority"]:
        _log_activity(tenant_id_for_notify, ticket_id, "priority_changed",
                       user_id=user["id"], old_value=old["priority"], new_value=data["priority"])
    if "assignee_id" in data:
        old_name = None
        new_name = None
        if old.get("old_assignee_id"):
            _old_u = fetch_one("SELECT name FROM users WHERE id = %s", [old["old_assignee_id"]])
            old_name = _old_u["name"] if _old_u else None
        if data["assignee_id"]:
            _new_u = fetch_one("SELECT name FROM users WHERE id = %s", [data["assignee_id"]])
            new_name = _new_u["name"] if _new_u else None
        _log_activity(tenant_id_for_notify, ticket_id, "assigned",
                       user_id=user["id"], old_value=old_name, new_value=new_name)
    if "team_id" in data and data.get("team_id") != old.get("team_id"):
        old_team_name = None
        new_team_name = None
        if old.get("team_id"):
            _ot = fetch_one("SELECT name FROM teams WHERE id = %s", [old["team_id"]])
            old_team_name = _ot["name"] if _ot else None
        if data["team_id"]:
            _nt = fetch_one("SELECT name FROM teams WHERE id = %s", [data["team_id"]])
            new_team_name = _nt["name"] if _nt else None
        _log_activity(tenant_id_for_notify, ticket_id, "team_assigned",
                       user_id=user["id"], old_value=old_team_name, new_value=new_team_name)
    if "problem_category_id" in data and data.get("problem_category_id") != old.get("problem_category_id"):
        old_cat_name = None
        new_cat_name = None
        if old.get("problem_category_id"):
            _oc = fetch_one("SELECT name FROM problem_categories WHERE id = %s", [old["problem_category_id"]])
            old_cat_name = _oc["name"] if _oc else None
        if data["problem_category_id"]:
            _nc = fetch_one("SELECT name FROM problem_categories WHERE id = %s", [data["problem_category_id"]])
            new_cat_name = _nc["name"] if _nc else None
        _log_activity(tenant_id_for_notify, ticket_id, "category_changed",
                       user_id=user["id"], old_value=old_cat_name, new_value=new_cat_name)

    # Re-tag if description changed
    if "description" in data:
        insert_returning(
            """INSERT INTO pipeline_queue
               (tenant_id, ticket_id, step_name, priority, uses_llm)
               VALUES (%s, %s, 'auto_tag', 3, true) RETURNING id""",
            [tenant_id_for_notify, ticket_id],
        )

    # (custom_fields already upserted above, before close check)

    return jsonify({"ok": True})


# ============================================================
# Comments
# ============================================================

@tickets_bp.route("/<int:ticket_id>/comments", methods=["POST"])
@login_required
@limiter.limit("60 per minute")
def add_comment(ticket_id: int):
    user = get_current_user()
    data = request.json or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "Content is required"}), 400

    # Verify ticket belongs to user's tenant
    if user["role"] != "super_admin":
        ticket = fetch_one("SELECT id FROM tickets WHERE id = %s AND tenant_id = %s", [ticket_id, get_tenant_id()])
        if not ticket:
            return jsonify({"error": "Not found"}), 404

    # End-users can't post internal notes
    is_internal = data.get("is_internal", False)
    if user["role"] == "end_user":
        is_internal = False

    comment_id = insert_returning(
        """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        [ticket_id, user["id"], content, is_internal, data.get("is_ai_generated", False)],
    )

    # Link any pending attachments to this comment
    attachment_ids = data.get("attachment_ids", [])
    if attachment_ids:
        for att_id in attachment_ids:
            execute(
                "UPDATE ticket_attachments SET comment_id = %s WHERE id = %s AND ticket_id = %s AND uploaded_by = %s",
                [comment_id, att_id, ticket_id, user["id"]],
            )

    execute("UPDATE tickets SET updated_at = now() WHERE id = %s", [ticket_id])

    # Track first response time (agent/admin only, non-internal)
    if user["role"] in ("super_admin", "tenant_admin", "agent") and not is_internal:
        execute(
            "UPDATE tickets SET first_response_at = now() WHERE id = %s AND first_response_at IS NULL",
            [ticket_id],
        )

    # Determine comment event: agent_reply vs requester_reply
    if not is_internal:
        ticket_data = fetch_one(
            "SELECT requester_id FROM tickets WHERE id = %s", [ticket_id]
        )
        if ticket_data and user["id"] == ticket_data["requester_id"]:
            comment_event = "requester_reply"
            # Trigger Atlas follow-up when end-user replies (if AI engaged)
            if user["role"] == "end_user":
                try:
                    from services.atlas_service import atlas_follow_up, is_ticket_review_enabled
                    t_id = get_tenant_id()
                    if t_id and is_ticket_review_enabled(t_id):
                        atlas_follow_up(ticket_id, t_id, content)
                except Exception as e:
                    logger.warning("Atlas follow-up dispatch failed: %s", e)
        else:
            comment_event = "agent_reply"
        comment_dict = {"content": content, "author_name": user.get("name", "")}
        _dispatch_notify(get_tenant_id(), ticket_id, comment_event, comment=comment_dict)
    else:
        _dispatch_notify(get_tenant_id(), ticket_id, "internal_note",
                         comment={"content": content, "author_name": user.get("name", "")})

    # Fire matching automations
    _dispatch_automations(get_tenant_id(), ticket_id, "comment_added", {"comment_type": "public" if not is_internal else "internal"})

    # Log comment activity for timeline
    _log_activity(
        get_tenant_id(), ticket_id, "comment_added",
        user_id=user["id"],
        new_value="internal note" if is_internal else "reply",
    )

    return jsonify({"id": comment_id}), 201


# ============================================================
# File attachments
# ============================================================

@tickets_bp.route("/<int:ticket_id>/attachments", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def upload_attachment(ticket_id: int):
    """Upload a file attachment to a ticket."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    # Verify ticket access
    if user["role"] == "end_user":
        ticket = fetch_one("SELECT id FROM tickets WHERE id = %s AND tenant_id = %s AND requester_id = %s", [ticket_id, tenant_id, user["id"]])
    elif user["role"] != "super_admin":
        ticket = fetch_one("SELECT id FROM tickets WHERE id = %s AND tenant_id = %s", [ticket_id, tenant_id])
    else:
        ticket = fetch_one("SELECT id FROM tickets WHERE id = %s", [ticket_id])
    if not ticket:
        return jsonify({"error": "Not found"}), 404

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file provided"}), 400

    # Read file data and check size
    data = f.read()
    if len(data) > MAX_FILE_SIZE:
        return jsonify({"error": f"File too large (max {MAX_FILE_SIZE // (1024*1024)}MB)"}), 400

    content_type = f.content_type or "application/octet-stream"
    filename = f.filename

    # Store file on disk
    store_dir = os.path.join(ATTACHMENT_DIR, str(tenant_id), str(ticket_id))
    os.makedirs(store_dir, exist_ok=True)
    ext = os.path.splitext(filename)[1] if "." in filename else ""
    stored_filename = f"{uuid4().hex}{ext}"
    filepath = os.path.join(store_dir, stored_filename)
    with open(filepath, "wb") as fp:
        fp.write(data)

    att_id = insert_returning(
        """INSERT INTO ticket_attachments (ticket_id, filename, stored_filename, file_size, content_type, uploaded_by)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        [ticket_id, filename, stored_filename, len(data), content_type, user["id"]],
    )

    return jsonify({"id": att_id, "filename": filename, "file_size": len(data), "content_type": content_type}), 201


@tickets_bp.route("/<int:ticket_id>/attachments/<int:attachment_id>", methods=["GET"])
@login_required
def download_attachment(ticket_id: int, attachment_id: int):
    """Download/view a file attachment."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    att = fetch_one(
        """SELECT a.*, t.tenant_id, t.requester_id
           FROM ticket_attachments a
           JOIN tickets t ON t.id = a.ticket_id
           WHERE a.id = %s AND a.ticket_id = %s""",
        [attachment_id, ticket_id],
    )
    if not att:
        return jsonify({"error": "Not found"}), 404

    # Access check
    if user["role"] == "end_user" and att["requester_id"] != user["id"]:
        return jsonify({"error": "Not found"}), 404
    if user["role"] != "super_admin" and att["tenant_id"] != tenant_id:
        return jsonify({"error": "Not found"}), 404

    filepath = os.path.join(ATTACHMENT_DIR, str(att["tenant_id"]), str(ticket_id), att["stored_filename"])
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found on disk"}), 404

    return send_file(filepath, mimetype=att["content_type"], download_name=att["filename"])


# ============================================================
# Tag suggestions
# ============================================================

@tickets_bp.route("/<int:ticket_id>/tags/accept", methods=["POST"])
@login_required
def accept_tag(ticket_id: int):
    """Accept or reject a tag suggestion."""
    user = get_current_user()
    data = request.json or {}
    suggestion_id = data.get("suggestion_id")
    accepted = data.get("accepted")

    if suggestion_id is None or accepted is None:
        return jsonify({"error": "suggestion_id and accepted are required"}), 400

    # Verify ticket belongs to user's tenant
    if user["role"] != "super_admin":
        ticket = fetch_one("SELECT id FROM tickets WHERE id = %s AND tenant_id = %s", [ticket_id, get_tenant_id()])
        if not ticket:
            return jsonify({"error": "Not found"}), 404

    execute(
        "UPDATE tag_suggestions SET accepted = %s WHERE id = %s AND ticket_id = %s",
        [accepted, suggestion_id, ticket_id],
    )

    # If accepted, add to ticket's tags array
    if accepted:
        suggestion = fetch_one("SELECT tag FROM tag_suggestions WHERE id = %s", [suggestion_id])
        if suggestion:
            execute(
                "UPDATE tickets SET tags = array_append(tags, %s), updated_at = now() WHERE id = %s AND NOT (%s = ANY(tags))",
                [suggestion["tag"], ticket_id, suggestion["tag"]],
            )

    return jsonify({"ok": True})


@tickets_bp.route("/<int:ticket_id>/tags", methods=["POST"])
@login_required
def add_tag(ticket_id: int):
    """Manually add a tag to the ticket."""
    user = get_current_user()
    data = request.json or {}
    tag = data.get("tag", "").strip().lower()
    if not tag:
        return jsonify({"error": "Tag is required"}), 400

    # Verify ticket belongs to user's tenant
    if user["role"] != "super_admin":
        ticket = fetch_one("SELECT id FROM tickets WHERE id = %s AND tenant_id = %s", [ticket_id, get_tenant_id()])
        if not ticket:
            return jsonify({"error": "Not found"}), 404

    execute(
        "UPDATE tickets SET tags = array_append(tags, %s), updated_at = now() WHERE id = %s AND NOT (%s = ANY(tags))",
        [tag[:50], ticket_id, tag[:50]],
    )
    return jsonify({"ok": True})


# ============================================================
# Agents list (for assignee dropdown)
# ============================================================

@tickets_bp.route("/agents", methods=["GET"])
@login_required
def list_agents():
    """List agents/admins for assignee dropdown (tenant-scoped).

    ?include_end_users=true — also include end_users (for requester dropdown).
    """
    user = get_current_user()
    tenant_id = get_tenant_id()
    include_end_users = request.args.get("include_end_users", "").lower() == "true"
    if include_end_users:
        conditions = ["is_active = true"]
    else:
        conditions = ["role IN ('super_admin', 'tenant_admin', 'agent')", "is_active = true"]
    params = []
    if user["role"] != "super_admin" and tenant_id:
        conditions.append("tenant_id = %s")
        params.append(tenant_id)
    where = f"WHERE {' AND '.join(conditions)}"
    agents = fetch_all(
        f"SELECT id, name, email, role FROM users {where} ORDER BY name", params
    )
    return jsonify(agents)


# ============================================================
# Atlas insights for ticket detail sidebar
# ============================================================

@tickets_bp.route("/<int:ticket_id>/atlas-insights", methods=["GET"])
@login_required
def get_atlas_insights(ticket_id):
    """Return AI-generated insights for a ticket: routing, category suggestion, metrics."""
    tenant_id = get_tenant_id()

    # Routing + metrics from ticket_metrics
    metrics = fetch_one(
        """SELECT suggested_assignee_id, suggested_assignee_name,
                  routing_confidence, routing_reason,
                  resolution_score, effort_score, fcr
           FROM ticket_metrics WHERE ticket_id = %s""",
        (ticket_id,),
    )

    # Category suggestion from atlas_engagements
    engagement = fetch_one(
        """SELECT suggested_category_id, suggested_category_name,
                  category_confidence, engagement_type, status
           FROM atlas_engagements WHERE ticket_id = %s
           ORDER BY created_at DESC LIMIT 1""",
        (ticket_id,),
    )

    result = {
        "routing": None,
        "category_suggestion": None,
        "metrics": None,
    }

    if metrics:
        if metrics.get("suggested_assignee_name") or metrics.get("routing_confidence"):
            result["routing"] = {
                "suggested_assignee_name": metrics.get("suggested_assignee_name"),
                "confidence": metrics.get("routing_confidence"),
                "reason": metrics.get("routing_reason"),
            }
        result["metrics"] = {
            "resolution_score": metrics.get("resolution_score"),
            "effort_score": metrics.get("effort_score"),
            "fcr": metrics.get("fcr"),
        }

    if engagement:
        if engagement.get("suggested_category_name") or engagement.get("category_confidence"):
            result["category_suggestion"] = {
                "category_name": engagement.get("suggested_category_name"),
                "confidence": engagement.get("category_confidence"),
            }

    return jsonify(result)


# ============================================================
# Similar Tickets — on-demand search (not dependent on auto-engage)
# ============================================================

@tickets_bp.route("/<int:ticket_id>/similar", methods=["GET"])
@login_required
@limiter.limit("20 per minute")
def get_similar_tickets(ticket_id):
    """Find tickets similar to this one by subject + description trigram match.

    Returns up to 5 similar open/pending tickets, independent of whether
    Atlas auto-engage ran.
    """
    tenant_id = get_tenant_id()
    ticket = fetch_one(
        "SELECT subject, description FROM tickets WHERE id = %s AND tenant_id = %s",
        [ticket_id, tenant_id],
    )
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    subject = ticket.get("subject", "")
    description = ticket.get("description", "")

    # Combine subject + first 200 chars of description for better matching
    search_text = subject
    if description:
        search_text += " " + description[:200]

    try:
        similar = fetch_all(
            """SELECT id, ticket_number, subject, status, priority,
                      similarity(subject, %s) as similarity
               FROM tickets
               WHERE tenant_id = %s
                 AND id != %s
                 AND status IN ('open', 'pending')
                 AND similarity(subject, %s) > 0.15
               ORDER BY similarity(subject, %s) DESC
               LIMIT 5""",
            [search_text, tenant_id, ticket_id, search_text, search_text],
        )
        return jsonify(similar or [])
    except Exception:
        # Fallback: keyword match if pg_trgm unavailable
        words = [w for w in subject.split() if len(w) >= 3]
        if not words:
            return jsonify([])
        conditions = " OR ".join(["subject ILIKE %s"] * min(len(words), 5))
        params = [f"%{w}%" for w in words[:5]]
        params.extend([tenant_id, ticket_id])
        similar = fetch_all(
            f"""SELECT id, ticket_number, subject, status, priority
                FROM tickets
                WHERE ({conditions})
                  AND tenant_id = %s
                  AND id != %s
                  AND status IN ('open', 'pending')
                ORDER BY created_at DESC
                LIMIT 5""",
            params,
        )
        return jsonify(similar or [])


# ============================================================
# SLA overdue query (for cron / background jobs)
# ============================================================

@tickets_bp.route("/overdue", methods=["GET"])
@login_required
def overdue_tickets():
    tickets = fetch_all(
        """SELECT t.*, u_asg.name as assignee_name, ten.name as tenant_name
           FROM tickets t
           LEFT JOIN users u_asg ON u_asg.id = t.assignee_id
           LEFT JOIN tenants ten ON ten.id = t.tenant_id
           WHERE t.sla_due_at < now()
             AND t.sla_breached = false
             AND t.status NOT IN ('resolved', 'closed_not_resolved')
           ORDER BY t.sla_due_at"""
    )
    return jsonify(tickets)


# ============================================================
# Ticket Tasks (checklist items)
# ============================================================

@tickets_bp.route("/<int:ticket_id>/tasks", methods=["GET"])
@login_required
def list_tasks(ticket_id: int):
    """List tasks for a ticket."""
    tasks = fetch_all(
        """SELECT tt.id, tt.title, tt.status, tt.sort_order, tt.assignee_id,
                  u.name as assignee_name
           FROM ticket_tasks tt
           LEFT JOIN users u ON u.id = tt.assignee_id
           WHERE tt.ticket_id = %s
           ORDER BY tt.sort_order, tt.id""",
        [ticket_id],
    )
    return jsonify(tasks)


@tickets_bp.route("/<int:ticket_id>/tasks", methods=["POST"])
@login_required
@require_permission("tickets.create")
def create_task(ticket_id: int):
    """Add a task to a ticket."""
    data = request.json or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "Task title is required"}), 400

    # Get max sort_order for this ticket
    max_order = fetch_one(
        "SELECT COALESCE(MAX(sort_order), -1) as mx FROM ticket_tasks WHERE ticket_id = %s",
        [ticket_id],
    )
    task_id = insert_returning(
        """INSERT INTO ticket_tasks (ticket_id, title, assignee_id, sort_order)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        [ticket_id, title, data.get("assignee_id"), (max_order["mx"] or 0) + 1],
    )
    return jsonify({"id": task_id}), 201


@tickets_bp.route("/<int:ticket_id>/tasks/<int:task_id>", methods=["PUT"])
@login_required
@require_permission("tickets.create")
def update_task(ticket_id: int, task_id: int):
    """Update a task's title, status, or assignee."""
    data = request.json or {}
    fields, params = [], []
    for col in ("title", "status", "assignee_id", "sort_order"):
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])

    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    params.extend([task_id, ticket_id])
    execute(f"UPDATE ticket_tasks SET {', '.join(fields)} WHERE id = %s AND ticket_id = %s", params)
    return jsonify({"ok": True})


@tickets_bp.route("/<int:ticket_id>/tasks/<int:task_id>", methods=["DELETE"])
@login_required
@require_permission("tickets.create")
def delete_task(ticket_id: int, task_id: int):
    """Delete a task from a ticket."""
    execute("DELETE FROM ticket_tasks WHERE id = %s AND ticket_id = %s", [task_id, ticket_id])
    return jsonify({"ok": True})


# ============================================================
# Workflow — get valid statuses for a ticket type
# ============================================================

@tickets_bp.route("/workflows/<ticket_type>", methods=["GET"])
@login_required
def get_workflow_statuses(ticket_type: str):
    """Return the status workflow for a ticket type."""
    from services.workflow_service import get_workflow, VALID_TICKET_TYPES
    if ticket_type not in VALID_TICKET_TYPES:
        return jsonify({"error": "Invalid ticket type"}), 400

    tenant_id = get_tenant_id()
    statuses = get_workflow(tenant_id, ticket_type)
    return jsonify(statuses)


# ============================================================
# Work Item Hierarchy — parent/children/tree/rollup
# ============================================================

@tickets_bp.route("/<int:ticket_id>/children", methods=["GET"])
@login_required
def get_children(ticket_id: int):
    """Return immediate children of a work item with rollup stats."""
    tenant_id = get_tenant_id()
    children = fetch_all(
        """SELECT t.id, t.ticket_number, t.work_item_number, t.subject, t.status,
                  t.priority, t.story_points, t.ticket_type, t.assignee_id,
                  u.name as assignee_name,
                  wit.name as work_item_type_name, wit.icon as work_item_type_icon,
                  wit.color as work_item_type_color
           FROM tickets t
           LEFT JOIN users u ON u.id = t.assignee_id
           LEFT JOIN work_item_types wit ON wit.id = t.work_item_type_id
           WHERE t.parent_id = %s AND t.tenant_id = %s
           ORDER BY t.sort_order NULLS LAST, t.created_at""",
        [ticket_id, tenant_id],
    )
    return jsonify(children)


@tickets_bp.route("/<int:ticket_id>/tree", methods=["GET"])
@login_required
def get_tree(ticket_id: int):
    """Return full subtree using recursive CTE."""
    tenant_id = get_tenant_id()
    rows = fetch_all(
        """WITH RECURSIVE tree AS (
               SELECT t.id, t.ticket_number, t.work_item_number, t.subject, t.status,
                      t.story_points, t.parent_id, t.ticket_type,
                      wit.name as work_item_type_name, wit.icon as work_item_type_icon,
                      wit.slug as work_item_type_slug,
                      0 as depth
               FROM tickets t
               LEFT JOIN work_item_types wit ON wit.id = t.work_item_type_id
               WHERE t.id = %s AND t.tenant_id = %s
               UNION ALL
               SELECT c.id, c.ticket_number, c.work_item_number, c.subject, c.status,
                      c.story_points, c.parent_id, c.ticket_type,
                      cwit.name, cwit.icon, cwit.slug,
                      tree.depth + 1
               FROM tickets c
               JOIN tree ON c.parent_id = tree.id
               LEFT JOIN work_item_types cwit ON cwit.id = c.work_item_type_id
               WHERE tree.depth < 4
           )
           SELECT * FROM tree ORDER BY depth, id""",
        [ticket_id, tenant_id],
    )
    return jsonify(rows)


@tickets_bp.route("/<int:ticket_id>/rollup", methods=["GET"])
@login_required
def get_rollup(ticket_id: int):
    """Return rollup stats: total points, completed points, completion % across all descendants."""
    tenant_id = get_tenant_id()
    row = fetch_one(
        """WITH RECURSIVE tree AS (
               SELECT id FROM tickets WHERE id = %s AND tenant_id = %s
               UNION ALL
               SELECT c.id FROM tickets c JOIN tree ON c.parent_id = tree.id
           )
           SELECT COUNT(*) - 1 as child_count,
                  COALESCE(SUM(t.story_points), 0) as total_points,
                  COALESCE(SUM(t.story_points) FILTER (
                      WHERE t.status IN ('done', 'resolved', 'closed_not_resolved')
                  ), 0) as completed_points
           FROM tree
           JOIN tickets t ON t.id = tree.id
           WHERE tree.id != %s""",
        [ticket_id, tenant_id, ticket_id],
    )
    total = row["total_points"] if row else 0
    completed = row["completed_points"] if row else 0
    return jsonify({
        "child_count": row["child_count"] if row else 0,
        "total_points": total,
        "completed_points": completed,
        "completion_pct": round(completed / total * 100, 1) if total > 0 else 0,
    })


# ============================================================
# Activity Timeline
# ============================================================

@tickets_bp.route("/<int:ticket_id>/activity", methods=["GET"])
@login_required
def get_ticket_activity(ticket_id: int):
    """Return the activity timeline for a ticket, newest first."""
    user = get_current_user()
    if user["role"] == "super_admin":
        ticket = fetch_one("SELECT id, tenant_id FROM tickets WHERE id = %s", [ticket_id])
    else:
        ticket = fetch_one("SELECT id, tenant_id FROM tickets WHERE id = %s AND tenant_id = %s",
                           [ticket_id, get_tenant_id()])
    if not ticket:
        return jsonify({"error": "Not found"}), 404

    rows = fetch_all(
        """SELECT ta.id, ta.activity_type, ta.old_value, ta.new_value,
                  ta.metadata, ta.created_at,
                  u.name AS user_name
           FROM ticket_activity ta
           LEFT JOIN users u ON u.id = ta.user_id
           WHERE ta.ticket_id = %s AND ta.tenant_id = %s
           ORDER BY ta.created_at DESC""",
        [ticket_id, ticket["tenant_id"]],
    )
    return jsonify(rows)
