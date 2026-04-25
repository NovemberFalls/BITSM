"""Work item types blueprint: CRUD for tenant-configurable work item types."""

import logging
import re

from flask import Blueprint, jsonify, request

from routes.auth import login_required, require_permission, get_current_user
from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)
work_item_types_bp = Blueprint("work_item_types", __name__)


def _get_tenant_id():
    from routes.auth import get_tenant_id
    return get_tenant_id()


# ============================================================
# List work item types (system defaults + tenant-specific)
# ============================================================

@work_item_types_bp.route("", methods=["GET"])
@login_required
def list_work_item_types():
    """Return system defaults (tenant_id IS NULL) plus tenant-specific types."""
    tenant_id = _get_tenant_id()

    rows = fetch_all(
        """SELECT id, name, slug, description, icon, color, sort_order, is_default, tenant_id
           FROM work_item_types
           WHERE tenant_id IS NULL OR tenant_id = %s
           ORDER BY sort_order""",
        [tenant_id],
    )
    return jsonify(rows)


# ============================================================
# Create tenant-specific work item type
# ============================================================

@work_item_types_bp.route("", methods=["POST"])
@login_required
@require_permission("sprints.manage")
def create_work_item_type():
    """Create a new tenant-specific work item type."""
    user = get_current_user()
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    slug = data.get("slug", "").strip()
    if not slug:
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

    type_id = insert_returning(
        """INSERT INTO work_item_types (tenant_id, name, slug, description, icon, color, sort_order, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        [tenant_id, name, slug, data.get("description"), data.get("icon"),
         data.get("color"), data.get("sort_order", 0), user["id"]],
    )
    return jsonify({"id": type_id}), 201


# ============================================================
# Update a tenant work item type
# ============================================================

@work_item_types_bp.route("/<int:type_id>", methods=["PUT"])
@login_required
@require_permission("sprints.manage")
def update_work_item_type(type_id: int):
    """Update a tenant-specific work item type. Cannot update system defaults."""
    tenant_id = _get_tenant_id()

    existing = fetch_one(
        "SELECT id, tenant_id FROM work_item_types WHERE id = %s",
        [type_id],
    )
    if not existing:
        return jsonify({"error": "Not found"}), 404
    if existing["tenant_id"] is None:
        return jsonify({"error": "Cannot modify system default types"}), 403
    if existing["tenant_id"] != tenant_id:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    fields, params = [], []
    for col in ("name", "slug", "description", "icon", "color", "sort_order"):
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])

    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    params.append(type_id)
    execute(f"UPDATE work_item_types SET {', '.join(fields)} WHERE id = %s", params)
    return jsonify({"ok": True})


# ============================================================
# Delete a tenant work item type
# ============================================================

@work_item_types_bp.route("/<int:type_id>", methods=["DELETE"])
@login_required
@require_permission("sprints.manage")
def delete_work_item_type(type_id: int):
    """Delete a tenant-specific work item type. Cannot delete system defaults."""
    tenant_id = _get_tenant_id()

    existing = fetch_one(
        "SELECT id, tenant_id FROM work_item_types WHERE id = %s",
        [type_id],
    )
    if not existing:
        return jsonify({"error": "Not found"}), 404
    if existing["tenant_id"] is None:
        return jsonify({"error": "Cannot delete system default types"}), 403
    if existing["tenant_id"] != tenant_id:
        return jsonify({"error": "Not found"}), 404

    # Clear references on tickets before deleting
    execute(
        "UPDATE tickets SET work_item_type_id = NULL WHERE work_item_type_id = %s",
        [type_id],
    )
    execute("DELETE FROM work_item_types WHERE id = %s", [type_id])
    return jsonify({"ok": True})
