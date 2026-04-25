"""Form Templates — service catalog items for ticket creation.

Each template defines a pre-configured form (name, description, icon, category group,
referenced custom fields) that creates a ticket of a given type (default: custom).

Endpoints:
  GET  /api/form-templates                List templates for this tenant
  GET  /api/form-templates/catalog        Public catalog view (active, customer-facing)
  GET  /api/form-templates/<id>           Get single template with resolved fields
  POST /api/form-templates                Create
  PUT  /api/form-templates/<id>           Update
  DELETE /api/form-templates/<id>         Soft-delete (deactivate)
  PUT  /api/form-templates/reorder        Bulk sort_order update
"""

import logging

from flask import Blueprint, jsonify, request

from routes.auth import login_required, require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)
form_templates_bp = Blueprint("form_templates", __name__)


@form_templates_bp.route("", methods=["GET"])
@login_required
@require_permission("categories.manage")
def list_templates():
    """List all form templates for admin management."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    include_inactive = request.args.get("include_inactive") == "true"
    active_filter = "" if include_inactive else " AND is_active = true"

    templates = fetch_all(
        f"""SELECT id, name, description, icon, catalog_category, ticket_type,
                   field_ids, default_category_id, default_priority,
                   is_active, is_customer_facing, sort_order, subject_format,
                   created_at, updated_at
            FROM form_templates
            WHERE tenant_id = %s{active_filter}
            ORDER BY sort_order, name""",
        [tenant_id],
    )
    return jsonify(templates)


@form_templates_bp.route("/catalog", methods=["GET"])
@login_required
def get_catalog():
    """Public catalog view — active, customer-facing templates grouped by catalog_category."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    user = get_current_user()
    facing_filter = " AND is_customer_facing = true" if user["role"] == "end_user" else ""

    templates = fetch_all(
        f"""SELECT id, name, description, icon, catalog_category, ticket_type,
                   field_ids, default_category_id, default_priority, sort_order
            FROM form_templates
            WHERE tenant_id = %s AND is_active = true{facing_filter}
            ORDER BY catalog_category NULLS LAST, sort_order, name""",
        [tenant_id],
    )

    # Group by catalog_category
    grouped: dict[str, list] = {}
    for t in templates:
        cat = t.get("catalog_category") or "Other"
        grouped.setdefault(cat, []).append(t)

    return jsonify({"categories": grouped, "all": templates})


@form_templates_bp.route("/<int:template_id>", methods=["GET"])
@login_required
def get_template(template_id: int):
    """Get a single template with resolved field definitions."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    template = fetch_one(
        """SELECT * FROM form_templates
           WHERE id = %s AND tenant_id = %s""",
        [template_id, tenant_id],
    )
    if not template:
        return jsonify({"error": "Not found"}), 404

    # Resolve field definitions
    field_ids = template.get("field_ids") or []
    fields = []
    if field_ids:
        placeholders = ",".join(["%s"] * len(field_ids))
        fields = fetch_all(
            f"""SELECT * FROM custom_field_definitions
                WHERE id IN ({placeholders}) AND tenant_id = %s AND is_active = true
                ORDER BY sort_order""",
            field_ids + [tenant_id],
        )

    template["fields"] = fields
    return jsonify(template)


@form_templates_bp.route("", methods=["POST"])
@login_required
@require_permission("categories.manage")
def create_template():
    """Create a new form template."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    from services.workflow_service import VALID_TICKET_TYPES
    ticket_type = data.get("ticket_type", "custom")
    if ticket_type not in VALID_TICKET_TYPES:
        return jsonify({"error": f"Invalid ticket_type: {ticket_type}"}), 400

    # Auto-increment sort_order
    max_row = fetch_one(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM form_templates WHERE tenant_id = %s",
        [tenant_id],
    )
    next_order = max_row["next_order"] if max_row else 0

    template_id = insert_returning(
        """INSERT INTO form_templates
               (tenant_id, name, description, icon, catalog_category, ticket_type,
                field_ids, default_category_id, default_priority,
                is_customer_facing, sort_order, subject_format)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        [
            tenant_id, name, data.get("description"), data.get("icon"),
            data.get("catalog_category"), ticket_type,
            data.get("field_ids", []), data.get("default_category_id"),
            data.get("default_priority"),
            data.get("is_customer_facing", True), next_order,
            data.get("subject_format"),
        ],
    )

    return jsonify({"id": template_id}), 201


@form_templates_bp.route("/<int:template_id>", methods=["PUT"])
@login_required
@require_permission("categories.manage")
def update_template(template_id: int):
    """Update a form template."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    existing = fetch_one(
        "SELECT id FROM form_templates WHERE id = %s AND tenant_id = %s",
        [template_id, tenant_id],
    )
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    allowed = ("name", "description", "icon", "catalog_category", "ticket_type",
               "field_ids", "default_category_id", "default_priority",
               "is_active", "is_customer_facing", "sort_order", "subject_format")
    fields, params = [], []
    for col in allowed:
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])

    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    fields.append("updated_at = now()")
    params.extend([template_id, tenant_id])
    execute(
        f"UPDATE form_templates SET {', '.join(fields)} WHERE id = %s AND tenant_id = %s",
        params,
    )

    return jsonify({"ok": True})


@form_templates_bp.route("/<int:template_id>", methods=["DELETE"])
@login_required
@require_permission("categories.manage")
def delete_template(template_id: int):
    """Soft-delete a form template."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    execute(
        "UPDATE form_templates SET is_active = false, updated_at = now() WHERE id = %s AND tenant_id = %s",
        [template_id, tenant_id],
    )
    return jsonify({"ok": True})


@form_templates_bp.route("/reorder", methods=["PUT"])
@login_required
@require_permission("categories.manage")
def reorder_templates():
    """Bulk reorder templates."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    data = request.json or {}
    order = data.get("order", [])
    if not isinstance(order, list):
        return jsonify({"error": "order must be an array of template IDs"}), 400

    for i, tid in enumerate(order):
        execute(
            "UPDATE form_templates SET sort_order = %s WHERE id = %s AND tenant_id = %s",
            [i, tid, tenant_id],
        )

    return jsonify({"ok": True})
