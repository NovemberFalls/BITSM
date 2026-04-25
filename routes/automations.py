"""Automations blueprint: visual workflow CRUD, canvas save, run history."""

import json
import logging

from flask import Blueprint, jsonify, request

from routes.auth import require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)
automations_bp = Blueprint("automations", __name__)


# ============================================================
# List / Create
# ============================================================

@automations_bp.route("", methods=["GET"])
@require_permission("automations.manage")
def list_automations():
    tenant_id = get_tenant_id()
    rows = fetch_all(
        """SELECT a.*,
                  u.name AS created_by_name
           FROM automations a
           LEFT JOIN users u ON u.id = a.created_by
           WHERE a.tenant_id = %s
           ORDER BY a.created_at DESC""",
        [tenant_id],
    )
    return jsonify(rows)


@automations_bp.route("", methods=["POST"])
@require_permission("automations.manage")
def create_automation():
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    trigger_type = data.get("trigger_type", "").strip()
    if not trigger_type:
        return jsonify({"error": "Trigger type is required"}), 400

    tenant_id = get_tenant_id()
    user = get_current_user()

    aid = insert_returning(
        """INSERT INTO automations (tenant_id, name, description, trigger_type, trigger_config, created_by)
           VALUES (%s, %s, %s, %s, %s::jsonb, %s) RETURNING id""",
        [
            tenant_id,
            name,
            data.get("description", ""),
            trigger_type,
            json.dumps(data.get("trigger_config", {})),
            user.get("id"),
        ],
    )
    return jsonify({"id": aid}), 201


# ============================================================
# Get / Update / Delete single automation
# ============================================================

@automations_bp.route("/<int:automation_id>", methods=["GET"])
@require_permission("automations.manage")
def get_automation(automation_id: int):
    tenant_id = get_tenant_id()
    auto = fetch_one(
        "SELECT * FROM automations WHERE id = %s AND tenant_id = %s",
        [automation_id, tenant_id],
    )
    if not auto:
        return jsonify({"error": "Not found"}), 404

    nodes = fetch_all(
        "SELECT * FROM automation_nodes WHERE automation_id = %s ORDER BY id",
        [automation_id],
    )
    edges = fetch_all(
        "SELECT * FROM automation_edges WHERE automation_id = %s ORDER BY id",
        [automation_id],
    )
    auto["nodes"] = nodes
    auto["edges"] = edges
    return jsonify(auto)


@automations_bp.route("/<int:automation_id>", methods=["PUT"])
@require_permission("automations.manage")
def update_automation(automation_id: int):
    tenant_id = get_tenant_id()
    user = get_current_user()
    data = request.json or {}

    fields, params = [], []
    for col in ("name", "description", "trigger_type"):
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])

    if "trigger_config" in data:
        fields.append("trigger_config = %s::jsonb")
        params.append(json.dumps(data["trigger_config"]))

    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    fields.append("updated_by = %s")
    params.append(user.get("id"))
    fields.append("updated_at = now()")
    params.extend([automation_id, tenant_id])

    execute(
        f"UPDATE automations SET {', '.join(fields)} WHERE id = %s AND tenant_id = %s",
        params,
    )
    return jsonify({"ok": True})


@automations_bp.route("/<int:automation_id>", methods=["DELETE"])
@require_permission("automations.manage")
def delete_automation(automation_id: int):
    tenant_id = get_tenant_id()
    execute(
        "DELETE FROM automations WHERE id = %s AND tenant_id = %s",
        [automation_id, tenant_id],
    )
    return jsonify({"ok": True})


# ============================================================
# Canvas save (full replace of nodes + edges)
# ============================================================

@automations_bp.route("/<int:automation_id>/canvas", methods=["PUT"])
@require_permission("automations.manage")
def save_canvas(automation_id: int):
    tenant_id = get_tenant_id()
    user = get_current_user()

    auto = fetch_one(
        "SELECT id FROM automations WHERE id = %s AND tenant_id = %s",
        [automation_id, tenant_id],
    )
    if not auto:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    # Full replace: delete old, insert new
    execute("DELETE FROM automation_edges WHERE automation_id = %s", [automation_id])
    execute("DELETE FROM automation_nodes WHERE automation_id = %s", [automation_id])

    # Also update trigger_type from the trigger node
    trigger_type = None

    for n in nodes:
        if n.get("node_type") == "trigger":
            trigger_type = n.get("node_subtype", "ticket_created")
        insert_returning(
            """INSERT INTO automation_nodes (id, automation_id, node_type, node_subtype, position_x, position_y, config, label)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING id""",
            [
                n["id"],
                automation_id,
                n["node_type"],
                n["node_subtype"],
                n.get("position_x", 0),
                n.get("position_y", 0),
                json.dumps(n.get("config", {})),
                n.get("label", ""),
            ],
            col="id",
        )

    for e in edges:
        insert_returning(
            """INSERT INTO automation_edges (id, automation_id, source_node, target_node, source_handle)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            [
                e["id"],
                automation_id,
                e["source_node"],
                e["target_node"],
                e.get("source_handle", "default"),
            ],
            col="id",
        )

    # Sync trigger_type + trigger_config from the trigger node
    update_parts = ["updated_by = %s", "updated_at = now()"]
    update_params = [user.get("id")]
    if trigger_type:
        update_parts.append("trigger_type = %s")
        update_params.append(trigger_type)
        # Find trigger node config
        trigger_node = next((n for n in nodes if n.get("node_type") == "trigger"), None)
        if trigger_node:
            update_parts.append("trigger_config = %s::jsonb")
            update_params.append(json.dumps(trigger_node.get("config", {})))

    update_params.extend([automation_id, tenant_id])
    execute(
        f"UPDATE automations SET {', '.join(update_parts)} WHERE id = %s AND tenant_id = %s",
        update_params,
    )

    return jsonify({"ok": True, "nodes": len(nodes), "edges": len(edges)})


# ============================================================
# Toggle active state
# ============================================================

@automations_bp.route("/<int:automation_id>/toggle", methods=["POST"])
@require_permission("automations.manage")
def toggle_automation(automation_id: int):
    tenant_id = get_tenant_id()
    auto = fetch_one(
        "SELECT id, is_active FROM automations WHERE id = %s AND tenant_id = %s",
        [automation_id, tenant_id],
    )
    if not auto:
        return jsonify({"error": "Not found"}), 404

    new_state = not auto["is_active"]
    execute(
        "UPDATE automations SET is_active = %s, updated_at = now() WHERE id = %s",
        [new_state, automation_id],
    )
    return jsonify({"is_active": new_state})


# ============================================================
# Run history
# ============================================================

@automations_bp.route("/<int:automation_id>/runs", methods=["GET"])
@require_permission("automations.manage")
def list_runs_for_automation(automation_id: int):
    tenant_id = get_tenant_id()
    limit = min(int(request.args.get("limit", 50)), 200)
    rows = fetch_all(
        """SELECT r.*, t.ticket_number, t.subject AS ticket_subject
           FROM automation_runs r
           LEFT JOIN tickets t ON t.id = r.ticket_id
           WHERE r.automation_id = %s AND r.tenant_id = %s
           ORDER BY r.started_at DESC
           LIMIT %s""",
        [automation_id, tenant_id, limit],
    )
    return jsonify(rows)


@automations_bp.route("/runs", methods=["GET"])
@require_permission("automations.manage")
def list_all_runs():
    tenant_id = get_tenant_id()
    limit = min(int(request.args.get("limit", 50)), 200)
    rows = fetch_all(
        """SELECT r.*, a.name AS automation_name, t.ticket_number, t.subject AS ticket_subject
           FROM automation_runs r
           JOIN automations a ON a.id = r.automation_id
           LEFT JOIN tickets t ON t.id = r.ticket_id
           WHERE r.tenant_id = %s
           ORDER BY r.started_at DESC
           LIMIT %s""",
        [tenant_id, limit],
    )
    return jsonify(rows)


# ============================================================
# Test / dry-run
# ============================================================

@automations_bp.route("/<int:automation_id>/test", methods=["POST"])
@require_permission("automations.manage")
def test_automation(automation_id: int):
    tenant_id = get_tenant_id()
    data = request.json or {}
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        return jsonify({"error": "ticket_id is required"}), 400

    auto = fetch_one(
        "SELECT * FROM automations WHERE id = %s AND tenant_id = %s",
        [automation_id, tenant_id],
    )
    if not auto:
        return jsonify({"error": "Not found"}), 404

    from services.automation_engine import execute_automation
    result = execute_automation(automation_id, ticket_id, tenant_id, dry_run=True)
    return jsonify(result)
