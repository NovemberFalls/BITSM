"""Notification management blueprint: groups, settings, anti-loop config."""

import json
import logging

from flask import Blueprint, jsonify, request

from routes.auth import login_required, require_role, require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)
notifications_bp = Blueprint("notifications", __name__)


# ============================================================
# Notification Groups
# ============================================================

@notifications_bp.route("/groups", methods=["GET"])
@login_required
@require_permission("notifications.manage")
def list_groups():
    tenant_id = get_tenant_id()
    conditions = []
    params = []
    user = get_current_user()
    if user["role"] != "super_admin" and tenant_id:
        conditions.append("tenant_id = %s")
        params.append(tenant_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    groups = fetch_all(
        f"""SELECT g.*,
                   (SELECT count(*) FROM notification_group_members m WHERE m.group_id = g.id) as member_count
            FROM notification_groups g {where}
            ORDER BY g.name""",
        params,
    )
    return jsonify(groups)


@notifications_bp.route("/groups", methods=["POST"])
@login_required
@require_permission("notifications.manage")
def create_group():
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    group_id = insert_returning(
        "INSERT INTO notification_groups (tenant_id, name, description) VALUES (%s, %s, %s) RETURNING id",
        [tenant_id, name, data.get("description", "")],
    )
    return jsonify({"id": group_id}), 201


def _verify_group_tenant(group_id: int) -> bool:
    """Verify the group belongs to the current user's tenant. Super_admin bypasses."""
    user = get_current_user()
    if user["role"] == "super_admin":
        return True
    group = fetch_one("SELECT tenant_id FROM notification_groups WHERE id = %s", [group_id])
    return group is not None and group["tenant_id"] == get_tenant_id()


@notifications_bp.route("/groups/<int:group_id>", methods=["PUT"])
@login_required
@require_permission("notifications.manage")
def update_group(group_id: int):
    if not _verify_group_tenant(group_id):
        return jsonify({"error": "Not found"}), 404
    data = request.json or {}
    fields, params = [], []
    if "name" in data:
        fields.append("name = %s")
        params.append(data["name"].strip())
    if "description" in data:
        fields.append("description = %s")
        params.append(data["description"])
    if not fields:
        return jsonify({"error": "No fields to update"}), 400
    params.append(group_id)
    execute(f"UPDATE notification_groups SET {', '.join(fields)} WHERE id = %s", params)
    return jsonify({"ok": True})


@notifications_bp.route("/groups/<int:group_id>", methods=["DELETE"])
@login_required
@require_permission("notifications.manage")
def delete_group(group_id: int):
    if not _verify_group_tenant(group_id):
        return jsonify({"error": "Not found"}), 404
    execute("DELETE FROM notification_groups WHERE id = %s", [group_id])
    return jsonify({"ok": True})


# ============================================================
# Group Members
# ============================================================

@notifications_bp.route("/groups/<int:group_id>/members", methods=["GET"])
@login_required
@require_permission("notifications.manage")
def list_members(group_id: int):
    if not _verify_group_tenant(group_id):
        return jsonify({"error": "Not found"}), 404
    members = fetch_all(
        """SELECT m.id, m.user_id, m.email as external_email, m.created_at,
                  u.name, u.email
           FROM notification_group_members m
           LEFT JOIN users u ON u.id = m.user_id
           WHERE m.group_id = %s
           ORDER BY COALESCE(u.name, m.email)""",
        [group_id],
    )
    # Normalize: for external members, use m.email; for user members, use u.email
    result = []
    for m in members:
        entry = {
            "id": m["id"],
            "user_id": m["user_id"],
            "name": m["name"] or m["external_email"],
            "email": m["email"] or m["external_email"],
            "type": "user" if m["user_id"] else "external",
            "created_at": m["created_at"],
        }
        result.append(entry)
    return jsonify(result)


@notifications_bp.route("/groups/<int:group_id>/members", methods=["POST"])
@login_required
@require_permission("notifications.manage")
def add_member(group_id: int):
    if not _verify_group_tenant(group_id):
        return jsonify({"error": "Not found"}), 404
    data = request.json or {}
    user_id = data.get("user_id")
    email = data.get("email", "").strip() if data.get("email") else None

    if not user_id and not email:
        return jsonify({"error": "user_id or email is required"}), 400

    try:
        if user_id:
            insert_returning(
                "INSERT INTO notification_group_members (group_id, user_id) VALUES (%s, %s) RETURNING id",
                [group_id, user_id],
            )
        else:
            insert_returning(
                "INSERT INTO notification_group_members (group_id, email) VALUES (%s, %s) RETURNING id",
                [group_id, email],
            )
    except Exception:
        return jsonify({"error": "Already in group or invalid"}), 409
    return jsonify({"ok": True}), 201


@notifications_bp.route("/groups/<int:group_id>/members/<int:member_id>", methods=["DELETE"])
@login_required
@require_permission("notifications.manage")
def remove_member(group_id: int, member_id: int):
    if not _verify_group_tenant(group_id):
        return jsonify({"error": "Not found"}), 404
    execute(
        "DELETE FROM notification_group_members WHERE group_id = %s AND id = %s",
        [group_id, member_id],
    )
    return jsonify({"ok": True})


# ============================================================
# Notification Settings (tenant-scoped)
# ============================================================

# ============================================================
# Notification Preferences (per-event email toggles)
# ============================================================

@notifications_bp.route("/preferences", methods=["GET"])
@login_required
@require_permission("notifications.manage")
def get_preferences():
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400
    prefs = fetch_all(
        """SELECT id, event, channel, role_target, enabled
           FROM notification_preferences
           WHERE tenant_id = %s
           ORDER BY event, role_target""",
        [tenant_id],
    )
    return jsonify(prefs)


@notifications_bp.route("/preferences", methods=["PUT"])
@login_required
@require_permission("notifications.manage")
def update_preferences():
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400
    data = request.json or {}
    preferences = data.get("preferences", [])
    for pref in preferences:
        event = pref.get("event")
        channel = pref.get("channel", "email")
        role_target = pref.get("role_target")
        enabled = pref.get("enabled", True)
        if not event or not role_target:
            continue
        execute(
            """INSERT INTO notification_preferences (tenant_id, event, channel, role_target, enabled)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id, event, channel, role_target)
               DO UPDATE SET enabled = EXCLUDED.enabled""",
            [tenant_id, event, channel, role_target, enabled],
        )
    return jsonify({"ok": True})


# ============================================================
# Notification Settings (tenant-scoped)
# ============================================================

@notifications_bp.route("/settings", methods=["GET"])
@login_required
@require_permission("notifications.manage")
def get_settings():
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400
    tenant = fetch_one("SELECT settings FROM tenants WHERE id = %s", [tenant_id])
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404
    settings = tenant.get("settings") or {}
    if isinstance(settings, str):
        settings = json.loads(settings)
    # Return notification-specific settings
    return jsonify({
        "email_blocklist": settings.get("email_blocklist", []),
        "email_loop_detection": settings.get("email_loop_detection", True),
        "teams_webhook_enabled": settings.get("teams_webhook_enabled", True),
        "teams_webhook_url": settings.get("teams_webhook_url", ""),
        "slack_webhook_url": settings.get("slack_webhook_url", ""),
    })


@notifications_bp.route("/settings", methods=["PUT"])
@login_required
@require_permission("notifications.manage")
def update_settings():
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400
    data = request.json or {}

    # Merge notification settings into tenant settings JSONB
    tenant = fetch_one("SELECT settings FROM tenants WHERE id = %s", [tenant_id])
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404
    settings = tenant.get("settings") or {}
    if isinstance(settings, str):
        settings = json.loads(settings)

    # Update allowed notification keys
    allowed_keys = ("email_blocklist", "email_loop_detection", "teams_webhook_enabled",
                    "teams_webhook_url", "slack_webhook_url")
    for key in allowed_keys:
        if key in data:
            settings[key] = data[key]

    execute(
        "UPDATE tenants SET settings = %s::jsonb WHERE id = %s",
        [json.dumps(settings), tenant_id],
    )
    return jsonify({"ok": True})


# ============================================================
# Group Event Subscriptions
# ============================================================

# The canonical ordered list of group-level notification events.
_GROUP_EVENTS = [
    "ticket_created", "task_created", "bug_created", "feature_created",
    "ticket_assigned", "team_assigned",
    "ticket_resolved", "ticket_closed", "status_changed",
    "priority_changed", "category_changed",
    "agent_reply", "requester_reply", "internal_note",
    "sla_warning", "sla_breach",
]


@notifications_bp.route("/group-event-matrix", methods=["GET"])
@login_required
@require_permission("notifications.manage")
def get_group_event_matrix():
    """Return per-group event subscription matrix for the current tenant.

    All 4 group events are always present for every group. If no row exists in
    notification_group_events for a group+event combination the default is
    enabled=true (groups are subscribed to all events until explicitly opted out).
    """
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    # Fetch all groups for this tenant
    groups = fetch_all(
        "SELECT id, name FROM notification_groups WHERE tenant_id = %s ORDER BY name",
        [tenant_id],
    )
    if not groups:
        return jsonify([])

    group_ids = [g["id"] for g in groups]

    # Fetch all existing event rows for these groups in one query.
    # Use ANY(%s) with a list cast — psycopg2 handles list → ARRAY automatically.
    existing_rows = fetch_all(
        """SELECT group_id, event, channel, enabled
           FROM notification_group_events
           WHERE group_id = ANY(%s) AND channel = 'email'""",
        [group_ids],
    )

    # Build a lookup: (group_id, event) -> enabled
    event_lookup: dict[tuple[int, str], bool] = {
        (row["group_id"], row["event"]): row["enabled"]
        for row in existing_rows
    }

    result = []
    for group in groups:
        gid = group["id"]
        events = [
            {
                "event": evt,
                "channel": "email",
                # Default true when no DB row exists (backward-compatible default)
                "enabled": event_lookup.get((gid, evt), True),
            }
            for evt in _GROUP_EVENTS
        ]
        result.append({
            "group_id": gid,
            "group_name": group["name"],
            "events": events,
        })

    return jsonify(result)


@notifications_bp.route("/groups/<int:group_id>/events", methods=["PUT"])
@login_required
@require_permission("notifications.manage")
def update_group_events(group_id: int):
    """Full-replace event subscriptions for a specific group.

    Accepts the complete list of events for the group and upserts each one.
    Only the 4 known group events are accepted; unknown event names are rejected.
    """
    if not _verify_group_tenant(group_id):
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    events = data.get("events", [])

    if not isinstance(events, list):
        return jsonify({"error": "events must be a list"}), 400

    for entry in events:
        event = entry.get("event")
        channel = entry.get("channel", "email")
        enabled = entry.get("enabled", True)

        if event not in _GROUP_EVENTS:
            return jsonify({"error": f"Unknown event: {event!r}. Allowed: {_GROUP_EVENTS}"}), 400

        if not isinstance(enabled, bool):
            return jsonify({"error": f"enabled must be a boolean for event {event!r}"}), 400

        execute(
            """INSERT INTO notification_group_events (group_id, event, channel, enabled)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (group_id, event, channel)
               DO UPDATE SET enabled = EXCLUDED.enabled""",
            [group_id, event, channel, enabled],
        )

    return jsonify({"ok": True})


# ============================================================
# Team Event Subscriptions
# ============================================================

_TEAM_EVENTS = _GROUP_EVENTS  # same event set as groups


@notifications_bp.route("/team-event-matrix", methods=["GET"])
@login_required
@require_permission("notifications.manage")
def get_team_event_matrix():
    """Return per-team event subscription matrix for the current tenant."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    teams = fetch_all(
        "SELECT id, name FROM teams WHERE tenant_id = %s AND is_active = true ORDER BY name",
        [tenant_id],
    )
    if not teams:
        return jsonify([])

    team_ids = [t["id"] for t in teams]
    existing_rows = fetch_all(
        """SELECT team_id, event, channel, enabled
           FROM team_event_subscriptions
           WHERE team_id = ANY(%s) AND channel = 'email'""",
        [team_ids],
    )

    event_lookup: dict[tuple[int, str], bool] = {
        (row["team_id"], row["event"]): row["enabled"]
        for row in existing_rows
    }

    result = []
    for team in teams:
        tid = team["id"]
        events = [
            {
                "event": evt,
                "channel": "email",
                "enabled": event_lookup.get((tid, evt), True),
            }
            for evt in _TEAM_EVENTS
        ]
        result.append({
            "team_id": tid,
            "team_name": team["name"],
            "events": events,
        })

    return jsonify(result)


@notifications_bp.route("/teams/<int:team_id>/events", methods=["PUT"])
@login_required
@require_permission("notifications.manage")
def update_team_events(team_id: int):
    """Full-replace event subscriptions for a specific team."""
    tenant_id = get_tenant_id()
    team = fetch_one("SELECT tenant_id FROM teams WHERE id = %s", [team_id])
    if not team or (tenant_id and str(team["tenant_id"]) != str(tenant_id)):
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    events = data.get("events", [])

    if not isinstance(events, list):
        return jsonify({"error": "events must be a list"}), 400

    for entry in events:
        event = entry.get("event")
        channel = entry.get("channel", "email")
        enabled = entry.get("enabled", True)

        if event not in _TEAM_EVENTS:
            return jsonify({"error": f"Unknown event: {event!r}. Allowed: {_TEAM_EVENTS}"}), 400

        if not isinstance(enabled, bool):
            return jsonify({"error": f"enabled must be a boolean for event {event!r}"}), 400

        execute(
            """INSERT INTO team_event_subscriptions (team_id, event, channel, enabled)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (team_id, event, channel)
               DO UPDATE SET enabled = EXCLUDED.enabled""",
            [team_id, event, channel, enabled],
        )

    return jsonify({"ok": True})


# ============================================================
# Email Template Overrides
# ============================================================

from services.email_templates import TEMPLATE_DEFAULTS, EXTRA_VARS, COMMON_VARS  # noqa: E402


@notifications_bp.route("/templates", methods=["GET"])
@login_required
@require_permission("notifications.manage")
def get_templates():
    """Return all events with their effective template (custom or default) for this tenant."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    custom_rows = fetch_all(
        "SELECT event, subject_template, body_headline, body_intro FROM notification_templates WHERE tenant_id = %s",
        [tenant_id],
    )
    custom_map = {r["event"]: r for r in custom_rows}

    result = []
    for event, defaults in TEMPLATE_DEFAULTS.items():
        custom = custom_map.get(event)
        result.append({
            "event":            event,
            "is_custom":        custom is not None,
            "subject_template": custom["subject_template"] if custom else defaults["subject_template"],
            "body_headline":    custom["body_headline"]    if custom else defaults["body_headline"],
            "body_intro":       custom["body_intro"]       if custom else defaults["body_intro"],
            "default_subject":  defaults["subject_template"],
            "default_headline": defaults["body_headline"],
            "default_intro":    defaults["body_intro"],
            "variables":        COMMON_VARS + EXTRA_VARS.get(event, []),
        })
    return jsonify(result)


@notifications_bp.route("/templates/<event>", methods=["PUT"])
@login_required
@require_permission("notifications.manage")
def update_template(event: str):
    """Upsert a template override for this tenant+event."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400
    if event not in TEMPLATE_DEFAULTS:
        return jsonify({"error": f"Unknown event: {event!r}"}), 400

    data = request.json or {}
    subject  = (data.get("subject_template") or "").strip()
    headline = (data.get("body_headline") or "").strip()
    intro    = (data.get("body_intro") or "").strip()

    if not subject or not headline:
        return jsonify({"error": "subject_template and body_headline are required"}), 400

    execute(
        """INSERT INTO notification_templates (tenant_id, event, subject_template, body_headline, body_intro, updated_at)
           VALUES (%s, %s, %s, %s, %s, now())
           ON CONFLICT (tenant_id, event)
           DO UPDATE SET subject_template = EXCLUDED.subject_template,
                         body_headline    = EXCLUDED.body_headline,
                         body_intro       = EXCLUDED.body_intro,
                         updated_at       = now()""",
        [tenant_id, event, subject, headline, intro],
    )
    return jsonify({"ok": True})


@notifications_bp.route("/templates/<event>", methods=["DELETE"])
@login_required
@require_permission("notifications.manage")
def reset_template(event: str):
    """Remove custom template override, reverting to the system default."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400
    execute(
        "DELETE FROM notification_templates WHERE tenant_id = %s AND event = %s",
        [tenant_id, event],
    )
    return jsonify({"ok": True})


# ============================================================
# In-App Notifications (bell icon)
# ============================================================

@notifications_bp.route("/in-app/unread", methods=["GET"])
@login_required
def get_unread_notifications():
    """Return unread in-app notifications for the current user.

    Notifications are matched to the user based on:
    - ticket_assigned events where the user is the assignee
    - All in_app notifications for the user's tenant (agents/admins only)
    The `read_at` column tracks whether the user has dismissed them.
    """
    user = get_current_user()
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    notifications = fetch_all(
        """SELECT n.id, n.ticket_id, n.payload, n.status, n.sent_at, n.created_at
           FROM notifications n
           WHERE n.tenant_id = %s
             AND n.channel = 'in_app'
             AND n.read_at IS NULL
             AND n.created_at > now() - interval '7 days'
           ORDER BY n.created_at DESC
           LIMIT 50""",
        [tenant_id],
    )

    # Parse payload JSON for each notification
    result = []
    for n in notifications:
        payload = n.get("payload") or {}
        if isinstance(payload, str):
            import json as _json
            try:
                payload = _json.loads(payload)
            except Exception:
                payload = {}
        result.append({
            "id": n["id"],
            "ticket_id": n["ticket_id"],
            "event": payload.get("event") or n.get("recipient", ""),
            "ticket_number": payload.get("ticket_number", ""),
            "subject": payload.get("subject", ""),
            "status": payload.get("status", ""),
            "priority": payload.get("priority", ""),
            "created_at": str(n.get("created_at") or n.get("sent_at") or ""),
        })

    return jsonify({"count": len(result), "notifications": result})


@notifications_bp.route("/in-app/read", methods=["POST"])
@login_required
def mark_notifications_read():
    """Mark specific notifications as read, or all if no IDs provided."""
    user = get_current_user()
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    data = request.json or {}
    notification_ids = data.get("ids", [])

    if notification_ids:
        execute(
            "UPDATE notifications SET read_at = now() WHERE tenant_id = %s AND channel = 'in_app' AND id = ANY(%s)",
            [tenant_id, notification_ids],
        )
    else:
        # Mark all unread as read
        execute(
            "UPDATE notifications SET read_at = now() WHERE tenant_id = %s AND channel = 'in_app' AND read_at IS NULL",
            [tenant_id],
        )

    return jsonify({"ok": True})
