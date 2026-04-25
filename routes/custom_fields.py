"""Custom Field Definitions — tenant-configurable fields on tickets.

Fields can be:
  - Global (category_id IS NULL) — scoped by ticket type via applies_to[]
  - Category-scoped (category_id set) — appear on any ticket with that problem category

Visibility model:
  is_customer_facing   — end users see / fill this field (on portal)
  is_agent_facing      — agents/admins see / fill this field

Required modes:
  is_required_to_create — must be filled before ticket submission (blocks creation)
  is_required_to_close  — must be filled before closure; Atlas proactively collects it

Endpoints:
  GET  /api/custom-fields                   List definitions for this tenant
  GET  /api/custom-fields?category_id=<id>  Filter by category
  POST /api/custom-fields                   Create
  PUT  /api/custom-fields/<id>              Update
  DELETE /api/custom-fields/<id>            Soft-delete (deactivate)
  PUT  /api/custom-fields/reorder           Bulk sort_order update
"""

import json
import logging
import re

from flask import Blueprint, jsonify, request

from routes.auth import login_required, require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)
custom_fields_bp = Blueprint("custom_fields", __name__)

VALID_TYPES = ("text", "textarea", "number", "select", "multi_select", "checkbox", "date", "url")
VALID_TICKET_TYPES = ("support", "task", "bug", "feature", "custom")


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "field"


def _ensure_unique_key(tenant_id: int, key: str, exclude_id: int | None = None) -> str:
    base = key
    suffix = 2
    while True:
        row = fetch_one(
            "SELECT id FROM custom_field_definitions WHERE tenant_id = %s AND field_key = %s",
            [tenant_id, key],
        )
        if not row or (exclude_id and row["id"] == exclude_id):
            return key
        key = f"{base}_{suffix}"
        suffix += 1


# ============================================================
# Form fields — public (any authenticated user)
# ============================================================

@custom_fields_bp.route("/for-form", methods=["GET"])
@login_required
def list_fields_for_form():
    """Return active custom field definitions for a ticket creation/edit form.

    Accepts:
      category_id   — Problem category (inherits from ancestors)
      ticket_type   — e.g. 'support' (default)

    End-users only see is_customer_facing fields.
    Agents/admins see all (agent + customer).
    """
    tenant_id = get_tenant_id()
    user = get_current_user()
    ticket_type = request.args.get("ticket_type", "support")
    category_id = request.args.get("category_id")
    category_id = int(category_id) if category_id else None

    # If a form template is specified, load its specific fields
    template_id = request.args.get("form_template_id")
    if template_id:
        template = fetch_one(
            "SELECT field_ids FROM form_templates WHERE id = %s AND tenant_id = %s AND is_active = true",
            [int(template_id), tenant_id],
        )
        if template and template.get("field_ids"):
            placeholders = ",".join(["%s"] * len(template["field_ids"]))
            fields = fetch_all(
                f"""SELECT * FROM custom_field_definitions
                    WHERE id IN ({placeholders}) AND tenant_id = %s AND is_active = true
                    ORDER BY sort_order, id""",
                template["field_ids"] + [tenant_id],
            )
            if user["role"] == "end_user":
                fields = [f for f in fields if f.get("is_customer_facing")]
            return jsonify({"fields": fields})

    if category_id:
        fields = fetch_all(
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
    else:
        fields = fetch_all(
            """SELECT * FROM custom_field_definitions
               WHERE tenant_id = %s AND is_active = true
                 AND category_id IS NULL AND %s = ANY(applies_to)
               ORDER BY sort_order, id""",
            [tenant_id, ticket_type],
        )

    # End-users only see customer-facing fields
    if user["role"] == "end_user":
        fields = [f for f in fields if f.get("is_customer_facing")]

    return jsonify({"fields": fields})


# ============================================================
# List
# ============================================================

@custom_fields_bp.route("", methods=["GET"])
@login_required
def list_fields():
    tenant_id = get_tenant_id()
    user = get_current_user()

    if user["role"] == "super_admin" and request.args.get("tenant_id"):
        tenant_id = int(request.args["tenant_id"])

    include_inactive = request.args.get("include_inactive") == "true" and user["role"] in ("super_admin", "tenant_admin")
    active_cond = "" if include_inactive else "AND is_active = true"

    # Optional category filter
    category_id = request.args.get("category_id")
    if category_id == "null":
        cat_cond = "AND category_id IS NULL"
        cat_params = []
    elif category_id:
        cat_cond = "AND category_id = %s"
        cat_params = [int(category_id)]
    else:
        cat_cond = ""
        cat_params = []

    fields = fetch_all(
        f"SELECT * FROM custom_field_definitions WHERE tenant_id = %s {active_cond} {cat_cond} ORDER BY sort_order, id",
        [tenant_id] + cat_params,
    )
    return jsonify({"fields": fields})


# ============================================================
# Create
# ============================================================

@custom_fields_bp.route("", methods=["POST"])
@login_required
@require_permission("categories.manage")
def create_field():
    tenant_id = get_tenant_id()
    data = request.json or {}

    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    field_type = data.get("field_type", "text")
    if field_type not in VALID_TYPES:
        return jsonify({"error": f"field_type must be one of: {', '.join(VALID_TYPES)}"}), 400

    applies_to = data.get("applies_to") or list(VALID_TICKET_TYPES)
    bad = [t for t in applies_to if t not in VALID_TICKET_TYPES]
    if bad:
        return jsonify({"error": f"Invalid applies_to value(s): {bad}"}), 400

    options = data.get("options") or []
    if field_type in ("select", "multi_select") and not options:
        return jsonify({"error": "options required for select/multi_select fields"}), 400

    is_customer_facing = bool(data.get("is_customer_facing", False))
    is_agent_facing = bool(data.get("is_agent_facing", True))
    if not is_customer_facing and not is_agent_facing:
        return jsonify({"error": "At least one of is_customer_facing or is_agent_facing must be true"}), 400

    # Category association (optional)
    category_id = data.get("category_id") or None
    if category_id:
        # Verify category belongs to this tenant
        cat = fetch_one("SELECT id FROM problem_categories WHERE id = %s AND tenant_id = %s", [category_id, tenant_id])
        if not cat:
            return jsonify({"error": "category_id not found for this tenant"}), 400

    base_key = _slugify(name)
    field_key = _ensure_unique_key(tenant_id, base_key)

    max_order = fetch_one(
        "SELECT COALESCE(MAX(sort_order), -1) AS m FROM custom_field_definitions WHERE tenant_id = %s",
        [tenant_id],
    )
    sort_order = (max_order["m"] or 0) + 1

    # Required modes
    is_required_to_create = bool(data.get("is_required_to_create", False))
    is_required_to_close  = bool(data.get("is_required_to_close",  False))

    # Nested field support
    parent_field_id = data.get("parent_field_id") or None
    show_when = data.get("show_when") or None
    if parent_field_id:
        parent = fetch_one(
            "SELECT id, nesting_depth FROM custom_field_definitions WHERE id = %s AND tenant_id = %s",
            [parent_field_id, tenant_id],
        )
        if not parent:
            return jsonify({"error": "parent_field_id not found for this tenant"}), 400
        if (parent.get("nesting_depth") or 0) >= 5:
            return jsonify({"error": "Maximum nesting depth of 5 levels reached"}), 400

    new_id = insert_returning(
        """INSERT INTO custom_field_definitions
           (tenant_id, name, description, field_key, field_type, options,
            applies_to, is_required, is_required_to_create, is_required_to_close,
            is_customer_facing, is_agent_facing, category_id, sort_order,
            parent_field_id, show_when)
           VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
           RETURNING id""",
        [
            tenant_id, name, data.get("description", ""), field_key, field_type,
            json.dumps(options),
            applies_to,
            is_required_to_create,  # keep legacy is_required in sync
            is_required_to_create,
            is_required_to_close,
            is_customer_facing, is_agent_facing,
            category_id,
            sort_order,
            parent_field_id,
            json.dumps(show_when) if show_when else None,
        ],
    )
    row = fetch_one("SELECT * FROM custom_field_definitions WHERE id = %s", [new_id])
    return jsonify({"field": row}), 201


# ============================================================
# Update
# ============================================================

@custom_fields_bp.route("/<int:field_id>", methods=["PUT"])
@login_required
@require_permission("categories.manage")
def update_field(field_id: int):
    tenant_id = get_tenant_id()
    user = get_current_user()

    if user["role"] == "super_admin":
        existing = fetch_one("SELECT * FROM custom_field_definitions WHERE id = %s", [field_id])
    else:
        existing = fetch_one(
            "SELECT * FROM custom_field_definitions WHERE id = %s AND tenant_id = %s",
            [field_id, tenant_id],
        )
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    sets, params = [], []

    if "name" in data:
        name = data["name"].strip()
        if not name:
            return jsonify({"error": "name cannot be empty"}), 400
        sets.append("name = %s"); params.append(name)

    if "description" in data:
        sets.append("description = %s"); params.append(data["description"])

    if "field_type" in data:
        if data["field_type"] not in VALID_TYPES:
            return jsonify({"error": "Invalid field_type"}), 400
        sets.append("field_type = %s"); params.append(data["field_type"])

    if "options" in data:
        sets.append("options = %s::jsonb"); params.append(json.dumps(data["options"] or []))

    if "applies_to" in data:
        bad = [t for t in (data["applies_to"] or []) if t not in VALID_TICKET_TYPES]
        if bad:
            return jsonify({"error": f"Invalid applies_to: {bad}"}), 400
        sets.append("applies_to = %s"); params.append(data["applies_to"])

    if "is_required_to_create" in data:
        v = bool(data["is_required_to_create"])
        sets.append("is_required_to_create = %s"); params.append(v)
        sets.append("is_required = %s"); params.append(v)  # keep legacy col in sync

    if "is_required_to_close" in data:
        sets.append("is_required_to_close = %s"); params.append(bool(data["is_required_to_close"]))

    if "is_customer_facing" in data:
        sets.append("is_customer_facing = %s"); params.append(bool(data["is_customer_facing"]))

    if "is_agent_facing" in data:
        sets.append("is_agent_facing = %s"); params.append(bool(data["is_agent_facing"]))

    if "category_id" in data:
        cid = data["category_id"] or None
        if cid:
            cat = fetch_one("SELECT id FROM problem_categories WHERE id = %s AND tenant_id = %s", [cid, tenant_id])
            if not cat:
                return jsonify({"error": "category_id not found"}), 400
        sets.append("category_id = %s"); params.append(cid)

    if "sort_order" in data:
        sets.append("sort_order = %s"); params.append(int(data["sort_order"]))

    if "is_active" in data:
        sets.append("is_active = %s"); params.append(bool(data["is_active"]))

    if "parent_field_id" in data:
        pid = data["parent_field_id"] or None
        if pid:
            parent = fetch_one(
                "SELECT id, nesting_depth FROM custom_field_definitions WHERE id = %s AND tenant_id = %s",
                [pid, tenant_id],
            )
            if not parent:
                return jsonify({"error": "parent_field_id not found"}), 400
            if (parent.get("nesting_depth") or 0) >= 5:
                return jsonify({"error": "Maximum nesting depth of 5 levels reached"}), 400
        sets.append("parent_field_id = %s"); params.append(pid)

    if "show_when" in data:
        sw = data["show_when"] or None
        sets.append("show_when = %s::jsonb"); params.append(json.dumps(sw) if sw else None)

    if not sets:
        return jsonify({"error": "No fields to update"}), 400

    sets.append("updated_at = now()")
    params.append(field_id)
    execute(f"UPDATE custom_field_definitions SET {', '.join(sets)} WHERE id = %s", params)

    row = fetch_one("SELECT * FROM custom_field_definitions WHERE id = %s", [field_id])
    return jsonify({"field": row})


# ============================================================
# Delete (soft)
# ============================================================

@custom_fields_bp.route("/<int:field_id>", methods=["DELETE"])
@login_required
@require_permission("categories.manage")
def delete_field(field_id: int):
    tenant_id = get_tenant_id()
    user = get_current_user()

    if user["role"] == "super_admin":
        existing = fetch_one("SELECT id FROM custom_field_definitions WHERE id = %s", [field_id])
    else:
        existing = fetch_one(
            "SELECT id FROM custom_field_definitions WHERE id = %s AND tenant_id = %s",
            [field_id, tenant_id],
        )
    if not existing:
        return jsonify({"error": "Not found"}), 404

    execute(
        "UPDATE custom_field_definitions SET is_active = false, updated_at = now() WHERE id = %s",
        [field_id],
    )
    return jsonify({"ok": True})


# ============================================================
# Bulk reorder
# ============================================================

@custom_fields_bp.route("/reorder", methods=["PUT"])
@login_required
@require_permission("categories.manage")
def reorder_fields():
    tenant_id = get_tenant_id()
    data = request.json or {}
    order = data.get("order") or []
    if not order:
        return jsonify({"error": "order list required"}), 400
    for i, fid in enumerate(order):
        execute(
            "UPDATE custom_field_definitions SET sort_order = %s, updated_at = now() WHERE id = %s AND tenant_id = %s",
            [i, fid, tenant_id],
        )
    return jsonify({"ok": True})
