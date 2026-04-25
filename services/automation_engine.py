"""Automation engine: fires, evaluates, and executes visual automations.

Called from ticket lifecycle hooks in routes/tickets.py.
Registered as 'run_automation' step in queue_service.py.
"""

import json
import logging
import time
from datetime import datetime, timezone

from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)


# ============================================================
# Public API — called from ticket lifecycle hooks
# ============================================================

def fire_automations(event_type: str, ticket_id: int, tenant_id: int, context: dict | None = None):
    """Find active automations matching this event and enqueue them.

    Non-blocking: inserts into pipeline_queue and returns immediately.
    """
    from services.queue_service import PRIORITY_MAP

    rows = fetch_all(
        """SELECT id, trigger_config
           FROM automations
           WHERE tenant_id = %s AND trigger_type = %s AND is_active = true""",
        [tenant_id, event_type],
    )
    if not rows:
        return

    for auto in rows:
        # Check trigger_config constraints before enqueuing
        if not _trigger_matches(auto.get("trigger_config") or {}, context or {}):
            continue

        payload = json.dumps({
            "automation_id": auto["id"],
            "event_type": event_type,
            "context": context or {},
        })
        insert_returning(
            """INSERT INTO pipeline_queue
               (tenant_id, ticket_id, step_name, priority, uses_llm, payload)
               VALUES (%s, %s, 'run_automation', %s, false, %s::jsonb) RETURNING id""",
            [tenant_id, ticket_id, PRIORITY_MAP.get("p2", 2), payload],
        )

    if rows:
        logger.info(
            "Fired %d automation(s) for event=%s ticket=%s tenant=%s",
            len(rows), event_type, ticket_id, tenant_id,
        )


def _trigger_matches(trigger_config: dict, context: dict) -> bool:
    """Check if trigger config constraints match the event context.

    E.g., status_changed trigger with config {from: "open", to: "resolved"}
    only fires when context has those specific values.
    """
    if not trigger_config:
        return True

    for key in ("from", "to", "tag", "comment_type", "sla_type"):
        expected = trigger_config.get(key)
        if expected and context.get(key) != expected:
            return False

    return True


# ============================================================
# Execution — called by queue processor
# ============================================================

def run_automation_worker(ticket_id: int, tenant_id: int, payload: dict | None = None):
    """Queue worker entry point. Delegates to execute_automation."""
    if not payload:
        logger.warning("run_automation_worker called without payload")
        return "no payload"

    automation_id = payload.get("automation_id")
    if not automation_id:
        return "no automation_id in payload"

    result = execute_automation(automation_id, ticket_id, tenant_id)
    return result.get("summary", "done")


def execute_automation(
    automation_id: int,
    ticket_id: int,
    tenant_id: int,
    dry_run: bool = False,
) -> dict:
    """Load automation graph, walk it, execute actions.

    Returns dict with run status, actions taken, etc.
    """
    start_time = time.time()

    # Load automation + graph
    auto = fetch_one("SELECT * FROM automations WHERE id = %s", [automation_id])
    if not auto:
        return {"status": "failed", "error": "Automation not found"}

    nodes = fetch_all(
        "SELECT * FROM automation_nodes WHERE automation_id = %s",
        [automation_id],
    )
    edges = fetch_all(
        "SELECT * FROM automation_edges WHERE automation_id = %s",
        [automation_id],
    )

    # Load ticket data as snapshot
    ticket = fetch_one(
        """SELECT t.*, u.name AS requester_name, u.role AS requester_role,
                  a.name AS assignee_name,
                  pc.name AS category_name,
                  l.name AS location_name
           FROM tickets t
           LEFT JOIN users u ON u.id = t.requester_id
           LEFT JOIN users a ON a.id = t.assignee_id
           LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
           LEFT JOIN locations l ON l.id = t.location_id
           WHERE t.id = %s""",
        [ticket_id],
    )
    if not ticket:
        return {"status": "failed", "error": "Ticket not found"}

    # Attach custom field values keyed by field_key (for condition evaluation + webhook body)
    try:
        cf_rows = fetch_all(
            """SELECT cfd.field_key, cfv.value
               FROM ticket_custom_field_values cfv
               JOIN custom_field_definitions cfd ON cfd.id = cfv.field_id
               WHERE cfv.ticket_id = %s""",
            [ticket_id],
        )
        ticket["_custom_fields"] = {r["field_key"]: r["value"] for r in (cf_rows or [])}
    except Exception as _cf_err:
        ticket["_custom_fields"] = {}
        logger.warning("Could not load custom fields for ticket %s: %s", ticket_id, _cf_err)

    # Build adjacency list: node_id → [(edge, target_node_id)]
    node_map = {n["id"]: n for n in nodes}
    adjacency: dict[str, list[tuple[dict, str]]] = {}
    for e in edges:
        adjacency.setdefault(e["source_node"], []).append((e, e["target_node"]))

    # Find trigger node
    trigger_node = next((n for n in nodes if n["node_type"] == "trigger"), None)
    if not trigger_node:
        return {"status": "failed", "error": "No trigger node found"}

    # Create run record (unless dry run)
    run_id = None
    if not dry_run:
        run_id = insert_returning(
            """INSERT INTO automation_runs
               (automation_id, ticket_id, tenant_id, status, trigger_type, ticket_snapshot)
               VALUES (%s, %s, %s, 'running', %s, %s::jsonb) RETURNING id""",
            [
                automation_id, ticket_id, tenant_id,
                auto["trigger_type"],
                json.dumps(_ticket_snapshot(ticket)),
            ],
        )

    # Walk the graph (BFS from trigger)
    actions_taken = []
    nodes_executed = 0
    error = None

    try:
        queue = [trigger_node["id"]]
        visited = set()

        while queue:
            node_id = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)

            node = node_map.get(node_id)
            if not node:
                continue

            nodes_executed += 1

            if node["node_type"] == "condition":
                # Evaluate condition → follow true or false handle
                result = _evaluate_condition(node, ticket)
                handle = "true" if result else "false"
                actions_taken.append({
                    "node_id": node_id,
                    "type": "condition",
                    "subtype": node["node_subtype"],
                    "result": handle,
                })
                # Follow edges with matching source_handle
                for edge, target_id in adjacency.get(node_id, []):
                    if edge.get("source_handle") == handle:
                        queue.append(target_id)

            elif node["node_type"] == "action":
                # Execute action
                if not dry_run:
                    action_result = _execute_action(node, ticket_id, tenant_id, ticket)
                else:
                    action_result = "dry_run"
                actions_taken.append({
                    "node_id": node_id,
                    "type": "action",
                    "subtype": node["node_subtype"],
                    "result": action_result,
                })
                # Follow any outgoing edges
                for edge, target_id in adjacency.get(node_id, []):
                    queue.append(target_id)

            else:
                # Trigger node — just follow edges
                for edge, target_id in adjacency.get(node_id, []):
                    queue.append(target_id)

    except Exception as exc:
        error = str(exc)
        logger.error("Automation %s failed on ticket %s: %s", automation_id, ticket_id, exc)

    duration_ms = int((time.time() - start_time) * 1000)
    status = "failed" if error else "completed"

    # Update run record
    if run_id:
        execute(
            """UPDATE automation_runs
               SET status = %s, completed_at = now(), duration_ms = %s,
                   nodes_executed = %s, actions_taken = %s::jsonb, error = %s
               WHERE id = %s""",
            [status, duration_ms, nodes_executed, json.dumps(actions_taken), error, run_id],
        )
        # Update automation stats
        execute(
            """UPDATE automations
               SET run_count = run_count + 1, last_run_at = now()
               WHERE id = %s""",
            [automation_id],
        )

    return {
        "status": status,
        "duration_ms": duration_ms,
        "nodes_executed": nodes_executed,
        "actions_taken": actions_taken,
        "error": error,
        "summary": f"{len([a for a in actions_taken if a['type'] == 'action'])} actions executed",
    }


# ============================================================
# Condition evaluators
# ============================================================

def _evaluate_condition(node: dict, ticket: dict) -> bool:
    """Evaluate a condition node against ticket data.

    Supports two formats:
      - Multi-condition: config has {logic: "and"|"or", conditions: [{subtype, config}, ...]}
      - Single-condition (legacy): node has node_subtype + config directly
    """
    config = node.get("config") or {}
    conditions = config.get("conditions")

    if conditions:
        logic = config.get("logic", "and")
        results = [
            _evaluate_single_condition(c.get("subtype", ""), c.get("config") or {}, ticket)
            for c in conditions
        ]
        return all(results) if logic == "and" else any(results)

    # Legacy single-condition format
    return _evaluate_single_condition(node.get("node_subtype", ""), config, ticket)


def _evaluate_single_condition(subtype: str, config: dict, ticket: dict) -> bool:
    """Evaluate a single condition subtype against ticket data."""
    if subtype == "priority_is":
        return ticket.get("priority") in (config.get("values") or [])

    elif subtype == "status_is":
        return ticket.get("status") in (config.get("values") or [])

    elif subtype == "category_is":
        cat_id = ticket.get("problem_category_id")
        return cat_id in (config.get("category_ids") or []) if cat_id else False

    elif subtype == "location_is":
        loc_id = ticket.get("location_id")
        return loc_id in (config.get("location_ids") or []) if loc_id else False

    elif subtype == "tag_contains":
        ticket_tags = ticket.get("tags") or []
        check_tags = config.get("tags") or []
        return bool(set(ticket_tags) & set(check_tags))

    elif subtype == "assignee_set":
        is_set = config.get("is_set", True)
        has_assignee = ticket.get("assignee_id") is not None
        return has_assignee == is_set

    elif subtype == "requester_role":
        return ticket.get("requester_role") in (config.get("roles") or [])

    elif subtype == "hours_since":
        field = config.get("field", "created_at")
        operator = config.get("operator", ">")
        value = config.get("value", 0)
        field_val = ticket.get(field)
        if not field_val:
            return False
        if isinstance(field_val, str):
            try:
                field_val = datetime.fromisoformat(field_val.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return False
        now = datetime.now(timezone.utc)
        hours_diff = (now - field_val).total_seconds() / 3600
        if operator == ">":
            return hours_diff > value
        elif operator == "<":
            return hours_diff < value
        elif operator == ">=":
            return hours_diff >= value
        elif operator == "<=":
            return hours_diff <= value
        return False

    elif subtype == "custom_field_equals":
        # config: {field_key: "...", operator: "eq|neq|contains|set|unset", value: "..."}
        field_key = config.get("field_key", "")
        operator = config.get("operator", "eq")
        expected = config.get("value")
        cf_values = ticket.get("_custom_fields") or {}
        actual = cf_values.get(field_key)
        if operator == "set":
            return actual is not None
        elif operator == "unset":
            return actual is None
        elif operator == "eq":
            return str(actual) == str(expected) if actual is not None else False
        elif operator == "neq":
            return str(actual) != str(expected)
        elif operator == "contains":
            if isinstance(actual, list):
                return str(expected) in [str(v) for v in actual]
            return str(expected) in str(actual) if actual is not None else False
        return False

    logger.warning("Unknown condition subtype: %s", subtype)
    return False





# ============================================================
# Action executors
# ============================================================

def _execute_action(node: dict, ticket_id: int, tenant_id: int, ticket: dict) -> str:
    """Execute a single action node. Returns result string."""
    subtype = node["node_subtype"]
    config = node.get("config") or {}

    try:
        if subtype == "assign_to":
            user_id = config.get("user_id")
            if user_id:
                execute(
                    "UPDATE tickets SET assignee_id = %s, updated_at = now() WHERE id = %s",
                    [user_id, ticket_id],
                )
                return f"assigned to user {user_id}"
            return "no user_id configured"

        elif subtype == "change_priority":
            priority = config.get("priority")
            if priority:
                execute(
                    "UPDATE tickets SET priority = %s, updated_at = now() WHERE id = %s",
                    [priority, ticket_id],
                )
                return f"priority → {priority}"
            return "no priority configured"

        elif subtype == "change_status":
            status = config.get("status")
            if status:
                execute(
                    "UPDATE tickets SET status = %s, updated_at = now() WHERE id = %s",
                    [status, ticket_id],
                )
                return f"status → {status}"
            return "no status configured"

        elif subtype == "add_tag":
            tag = config.get("tag", "").strip()
            if tag:
                execute(
                    """UPDATE tickets
                       SET tags = array_append(
                           CASE WHEN tags IS NULL THEN ARRAY[]::text[] ELSE tags END,
                           %s
                       ), updated_at = now()
                       WHERE id = %s AND NOT (%s = ANY(COALESCE(tags, ARRAY[]::text[])))""",
                    [tag, ticket_id, tag],
                )
                return f"tag added: {tag}"
            return "no tag configured"

        elif subtype == "remove_tag":
            tag = config.get("tag", "").strip()
            if tag:
                execute(
                    "UPDATE tickets SET tags = array_remove(tags, %s), updated_at = now() WHERE id = %s",
                    [tag, ticket_id],
                )
                return f"tag removed: {tag}"
            return "no tag configured"

        elif subtype == "post_comment":
            content = config.get("content", "").strip()
            is_internal = config.get("is_internal", True)
            if content:
                insert_returning(
                    """INSERT INTO ticket_comments
                       (ticket_id, author_id, content, is_internal)
                       VALUES (%s, NULL, %s, %s) RETURNING id""",
                    [ticket_id, f"[Automation] {content}", is_internal],
                )
                return f"comment posted (internal={is_internal})"
            return "no content configured"

        elif subtype == "send_notification":
            channel = config.get("channel", "teams")
            message = config.get("message", "")
            event = config.get("event", "automation_action")
            from services.queue_service import enqueue_notify
            enqueue_notify(tenant_id, ticket_id, event, comment={"content": message})
            return f"notification queued ({channel})"

        elif subtype == "webhook":
            import requests as http_requests
            from services.url_validator import validate_url
            url = config.get("url", "")
            method = config.get("method", "POST").upper()
            headers = config.get("headers", {})
            body = config.get("body", {})
            validate_url(url)  # SSRF protection — raises ValueError for internal addresses
            # Inject ticket data into body
            body["ticket_id"] = ticket_id
            body["tenant_id"] = tenant_id
            body["ticket_number"] = ticket.get("ticket_number")
            body["subject"] = ticket.get("subject")
            body["status"] = ticket.get("status")
            body["priority"] = ticket.get("priority")
            # Include all custom field values so tenants can build API pipelines from them
            if ticket.get("_custom_fields"):
                body["custom_fields"] = ticket["_custom_fields"]
            resp = http_requests.request(method, url, json=body, headers=headers, timeout=10)
            return f"webhook {method} {url} → {resp.status_code}"

        elif subtype == "assign_team":
            team_id = config.get("team_id")
            if team_id:
                execute(
                    "UPDATE tickets SET team_id = %(team_id)s, updated_at = now() WHERE id = %(ticket_id)s",
                    {"team_id": team_id, "ticket_id": ticket_id},
                )
                return json.dumps({"action": "assign_team", "team_id": team_id})
            return "no team_id configured"

        elif subtype == "email_group":
            notification_group_id = config.get("notification_group_id")
            subject = config.get("subject", "")
            body = config.get("body", "")
            if not notification_group_id:
                return "no notification_group_id configured"
            if not subject or not body:
                return "subject and body required"
            # Look up group members (both user-based and external email)
            members = fetch_all(
                """SELECT COALESCE(u.email, ngm.email) AS member_email
                   FROM notification_group_members ngm
                   LEFT JOIN users u ON u.id = ngm.user_id
                   WHERE ngm.group_id = %(group_id)s
                     AND COALESCE(u.email, ngm.email) IS NOT NULL""",
                {"group_id": notification_group_id},
            )
            if not members:
                return "no members in notification group"
            from services.email_service import send_email
            sent_count = 0
            for member in members:
                email_addr = member["member_email"]
                result = send_email(to=email_addr, subject=subject, html_body=body, tenant_id=tenant_id)
                if result:
                    sent_count += 1
            return f"emailed {sent_count}/{len(members)} group members"

        elif subtype == "set_custom_field":
            # config: {field_key: "...", value: <any>}
            field_key = config.get("field_key", "").strip()
            value = config.get("value")
            if not field_key:
                return "no field_key configured"
            # Resolve field_key → field_id for this tenant
            field_def = fetch_one(
                "SELECT id FROM custom_field_definitions WHERE tenant_id = %s AND field_key = %s AND is_active = true",
                [tenant_id, field_key],
            )
            if not field_def:
                return f"custom field '{field_key}' not found for tenant"
            execute(
                """INSERT INTO ticket_custom_field_values (ticket_id, field_id, value, set_at)
                   VALUES (%s, %s, %s::jsonb, now())
                   ON CONFLICT (ticket_id, field_id)
                   DO UPDATE SET value = EXCLUDED.value, set_at = now()""",
                [ticket_id, field_def["id"], json.dumps(value)],
            )
            return f"custom field '{field_key}' set to {json.dumps(value)}"

        elif subtype == "do_nothing":
            return "no-op"

        else:
            return f"unknown action: {subtype}"

    except Exception as exc:
        logger.error("Action %s failed on ticket %s: %s", subtype, ticket_id, exc)
        return f"error: {exc}"


# ============================================================
# Helpers
# ============================================================

def _ticket_snapshot(ticket: dict) -> dict:
    """Create a JSON-safe snapshot of ticket state for the run log."""
    safe_keys = [
        "id", "ticket_number", "subject", "status", "priority",
        "tags", "assignee_id", "requester_id", "location_id",
        "problem_category_id", "requester_role", "assignee_name",
        "category_name", "location_name",
    ]
    snap = {}
    for k in safe_keys:
        v = ticket.get(k)
        if v is not None:
            snap[k] = v
    if ticket.get("_custom_fields"):
        snap["custom_fields"] = ticket["_custom_fields"]
    return snap
