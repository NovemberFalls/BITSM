"""Status Page API — incident management for planned outages / known issues.

Public (portal) endpoint:
    GET /api/status/incidents          — active + recently resolved incidents

Admin endpoints (require status.manage or super_admin):
    POST   /api/status/incidents       — create incident
    GET    /api/status/incidents/<id>   — single incident with timeline updates
    PUT    /api/status/incidents/<id>   — update incident
    DELETE /api/status/incidents/<id>   — soft-delete (hard delete)
    POST   /api/status/incidents/<id>/updates — add timeline update
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from routes.auth import login_required, require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)
status_bp = Blueprint("status_bp", __name__)


# ──────────────────────────────────────────
# Read (portal + staff)
# ──────────────────────────────────────────

@status_bp.route("/incidents", methods=["GET"])
@login_required
def list_incidents():
    """Return active incidents + resolved in the last 7 days."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "No tenant"}), 400

    rows = fetch_all(
        """SELECT si.*,
                  u.name AS author_name
           FROM status_incidents si
           LEFT JOIN users u ON u.id = si.created_by
           WHERE si.tenant_id = %s
             AND (si.status != 'resolved'
                  OR si.resolved_at >= now() - INTERVAL '7 days')
           ORDER BY
             CASE si.status
               WHEN 'resolved' THEN 1
               ELSE 0
             END,
             si.created_at DESC""",
        [tenant_id],
    )
    return jsonify([_serialize_incident(r) for r in rows])


@status_bp.route("/incidents/<int:incident_id>", methods=["GET"])
@login_required
def get_incident(incident_id: int):
    """Single incident with timeline updates."""
    tenant_id = get_tenant_id()
    incident = fetch_one(
        """SELECT si.*, u.name AS author_name
           FROM status_incidents si
           LEFT JOIN users u ON u.id = si.created_by
           WHERE si.id = %s AND si.tenant_id = %s""",
        [incident_id, tenant_id],
    )
    if not incident:
        return jsonify({"error": "Not found"}), 404

    updates = fetch_all(
        """SELECT siu.*, u.name AS author_name
           FROM status_incident_updates siu
           LEFT JOIN users u ON u.id = siu.created_by
           WHERE siu.incident_id = %s
           ORDER BY siu.created_at ASC""",
        [incident_id],
    )

    result = _serialize_incident(incident)
    result["updates"] = [_serialize_update(u) for u in updates]
    return jsonify(result)


# ──────────────────────────────────────────
# Admin CRUD
# ──────────────────────────────────────────

@status_bp.route("/incidents", methods=["POST"])
@require_permission("users.manage")
def create_incident():
    user = get_current_user()
    tenant_id = get_tenant_id()
    data = request.json or {}

    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400

    body = (data.get("body") or "").strip()
    status = data.get("status", "investigating")
    severity = data.get("severity", "minor")
    scheduled_end = data.get("scheduled_end")

    started_at = data.get("started_at") or datetime.now(timezone.utc).isoformat()

    row_id = insert_returning(
        """INSERT INTO status_incidents
           (tenant_id, title, body, status, severity, started_at, scheduled_end, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        [tenant_id, title, body, status, severity, started_at, scheduled_end, user["id"]],
    )

    logger.info("Status incident created: id=%s tenant=%s by=%s", row_id, tenant_id, user["email"])
    return jsonify({"id": row_id}), 201


@status_bp.route("/incidents/<int:incident_id>", methods=["PUT"])
@require_permission("users.manage")
def update_incident(incident_id: int):
    tenant_id = get_tenant_id()
    user = get_current_user()
    data = request.json or {}

    incident = fetch_one(
        "SELECT id, status FROM status_incidents WHERE id = %s AND tenant_id = %s",
        [incident_id, tenant_id],
    )
    if not incident:
        return jsonify({"error": "Not found"}), 404

    sets = []
    params = []

    for field in ("title", "body", "status", "severity", "started_at", "scheduled_end"):
        if field in data:
            sets.append(f"{field} = %s")
            params.append(data[field])

    # Auto-set resolved_at when transitioning to resolved
    new_status = data.get("status")
    if new_status == "resolved" and incident["status"] != "resolved":
        sets.append("resolved_at = %s")
        params.append(datetime.now(timezone.utc))
    elif new_status and new_status != "resolved":
        sets.append("resolved_at = NULL")

    if not sets:
        return jsonify({"error": "No fields to update"}), 400

    sets.append("updated_at = now()")
    params.extend([incident_id, tenant_id])

    execute(
        f"UPDATE status_incidents SET {', '.join(sets)} WHERE id = %s AND tenant_id = %s",
        params,
    )

    logger.info("Status incident updated: id=%s by=%s", incident_id, user["email"])
    return jsonify({"ok": True})


@status_bp.route("/incidents/<int:incident_id>", methods=["DELETE"])
@require_permission("users.manage")
def delete_incident(incident_id: int):
    tenant_id = get_tenant_id()
    user = get_current_user()

    execute(
        "DELETE FROM status_incidents WHERE id = %s AND tenant_id = %s",
        [incident_id, tenant_id],
    )
    logger.info("Status incident deleted: id=%s by=%s", incident_id, user["email"])
    return jsonify({"ok": True})


# ──────────────────────────────────────────
# Timeline updates
# ──────────────────────────────────────────

@status_bp.route("/incidents/<int:incident_id>/updates", methods=["POST"])
@require_permission("users.manage")
def add_update(incident_id: int):
    tenant_id = get_tenant_id()
    user = get_current_user()
    data = request.json or {}

    incident = fetch_one(
        "SELECT id FROM status_incidents WHERE id = %s AND tenant_id = %s",
        [incident_id, tenant_id],
    )
    if not incident:
        return jsonify({"error": "Not found"}), 404

    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Body is required"}), 400

    new_status = data.get("status", "investigating")

    update_id = insert_returning(
        """INSERT INTO status_incident_updates (incident_id, body, status, created_by)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        [incident_id, body, new_status, user["id"]],
    )

    # Also update the parent incident's status
    resolve_clause = ""
    params = [new_status]
    if new_status == "resolved":
        resolve_clause = ", resolved_at = now()"
    params.extend([incident_id, tenant_id])

    execute(
        f"UPDATE status_incidents SET status = %s, updated_at = now(){resolve_clause} WHERE id = %s AND tenant_id = %s",
        params,
    )

    return jsonify({"id": update_id}), 201


# ──────────────────────────────────────────
# Serialization
# ──────────────────────────────────────────

def _serialize_incident(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "body": row.get("body", ""),
        "status": row["status"],
        "severity": row["severity"],
        "started_at": row["started_at"].isoformat() if row.get("started_at") else None,
        "scheduled_end": row["scheduled_end"].isoformat() if row.get("scheduled_end") else None,
        "resolved_at": row["resolved_at"].isoformat() if row.get("resolved_at") else None,
        "author_name": row.get("author_name"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def _serialize_update(row: dict) -> dict:
    return {
        "id": row["id"],
        "body": row["body"],
        "status": row["status"],
        "author_name": row.get("author_name"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }
