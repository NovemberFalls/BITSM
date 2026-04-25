"""Hierarchies blueprint: locations + problem categories (self-referencing trees)."""

import csv as csv_mod
import io
import logging

from flask import Blueprint, jsonify, request, Response

from routes.auth import login_required, require_role, require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one, insert_returning, execute
from routes.connectors import _encrypt_config, _decrypt_config

logger = logging.getLogger(__name__)
hierarchies_bp = Blueprint("hierarchies", __name__)


# ============================================================
# LOCATIONS
# ============================================================

@hierarchies_bp.route("/locations", methods=["GET"])
@login_required
def list_locations():
    """List tenant's locations as flat list (client builds tree from parent_id)."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    conditions = ["is_active = true"]
    params = []

    if user["role"] != "super_admin" and tenant_id:
        conditions.append("tenant_id = %s")
        params.append(tenant_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = fetch_all(
        f"SELECT * FROM locations {where} ORDER BY sort_order, name",
        params,
    )
    return jsonify(rows)


@hierarchies_bp.route("/locations", methods=["POST"])
@login_required
@require_permission("locations.manage")
def create_location():
    data = request.json or {}
    tenant_id = data.get("tenant_id") or get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    loc_id = insert_returning(
        """INSERT INTO locations (tenant_id, parent_id, name, level_label, sort_order, phone, email, created_via)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'manual') RETURNING id""",
        [tenant_id, data.get("parent_id"), name, data.get("level_label"), data.get("sort_order", 0),
         data.get("phone") or None, data.get("email") or None],
    )
    return jsonify({"id": loc_id}), 201


@hierarchies_bp.route("/locations/<int:loc_id>", methods=["PUT"])
@login_required
@require_permission("locations.manage")
def update_location(loc_id: int):
    data = request.json or {}
    allowed = ("name", "level_label", "sort_order", "parent_id", "phone", "email")
    fields, params = [], []
    for col in allowed:
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])

    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    user = get_current_user()
    if user["role"] == "super_admin":
        params.append(loc_id)
        execute(f"UPDATE locations SET {', '.join(fields)} WHERE id = %s", params)
    else:
        params.extend([loc_id, get_tenant_id()])
        execute(f"UPDATE locations SET {', '.join(fields)} WHERE id = %s AND tenant_id = %s", params)
    return jsonify({"ok": True})


@hierarchies_bp.route("/locations/<int:loc_id>", methods=["DELETE"])
@login_required
@require_permission("locations.manage")
def delete_location(loc_id: int):
    """Soft-delete: set is_active = false."""
    user = get_current_user()
    if user["role"] == "super_admin":
        execute("UPDATE locations SET is_active = false WHERE id = %s", [loc_id])
    else:
        execute("UPDATE locations SET is_active = false WHERE id = %s AND tenant_id = %s", [loc_id, get_tenant_id()])
    return jsonify({"ok": True})


@hierarchies_bp.route("/locations/import", methods=["POST"])
@login_required
@require_permission("locations.manage")
def import_locations():
    """Batch import locations from CSV, JSON, or Excel."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "File is required"}), 400

    filename = file.filename.lower()
    content = file.read()

    try:
        from services.import_service import (
            parse_locations_csv, parse_locations_json,
            parse_locations_excel, resolve_and_insert_locations,
        )

        if filename.endswith(".csv"):
            locations = parse_locations_csv(content.decode("utf-8"))
        elif filename.endswith(".json"):
            locations = parse_locations_json(content.decode("utf-8"))
        elif filename.endswith((".xlsx", ".xls")):
            locations = parse_locations_excel(content)
        else:
            return jsonify({"error": "Unsupported format. Use CSV, JSON, or Excel (.xlsx)."}), 400

        if not locations:
            return jsonify({"error": "No valid locations found in file"}), 400

        result = resolve_and_insert_locations(tenant_id, locations)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("Location import failed: %s", e)
        return jsonify({"error": "Import failed. Check file format."}), 500


# ============================================================
# LOCATION DB SYNC
# ============================================================

@hierarchies_bp.route("/locations/db-sync", methods=["GET"])
@login_required
@require_permission("locations.manage")
def get_location_db_sync():
    """Return saved DB sync config for this tenant (connection string excluded)."""
    tenant_id = get_tenant_id()
    conn = fetch_one(
        """SELECT id, last_sync_at, last_error, config_encrypted
           FROM connectors
           WHERE tenant_id = %s AND connector_type = 'location_db_sync' AND is_active = true
           LIMIT 1""",
        [tenant_id],
    )
    if not conn:
        return jsonify(None)

    config = _decrypt_config(conn["config_encrypted"])
    _empty_level = {"column": "", "fixed": ""}
    levels = config.get("levels") or {}
    return jsonify({
        "id": conn["id"],
        "db_type": config.get("db_type", "postgresql"),
        "host":    config.get("host", ""),
        "port":    config.get("port", 5432),
        "dbname":  config.get("dbname", ""),
        "db_user": config.get("db_user", ""),
        "schema":  config.get("schema", ""),
        "table":   config.get("table", ""),
        "levels": {
            "company": levels.get("company") or _empty_level,
            "country": levels.get("country") or _empty_level,
            "state":   levels.get("state")   or _empty_level,
            "city":    levels.get("city")    or _empty_level,
            "store":   levels.get("store")   or _empty_level,
        },
        "preview_columns": config.get("preview_columns", []),
        "webhook_token": config.get("webhook_token", ""),
        "last_sync_at": conn["last_sync_at"].isoformat() if conn["last_sync_at"] else None,
        "last_error":   conn["last_error"],
        "last_result":  config.get("last_result"),
    })


@hierarchies_bp.route("/locations/db-sync", methods=["POST"])
@login_required
@require_permission("locations.manage")
def save_location_db_sync():
    """Create or update DB sync connector config."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    data = request.json or {}
    db_type  = (data.get("db_type") or "postgresql").strip()
    host     = (data.get("host") or "").strip()
    port     = int(data.get("port") or 5432)
    dbname   = (data.get("dbname") or "").strip()
    db_user  = (data.get("db_user") or "").strip()
    password = (data.get("password") or "").strip()
    schema = (data.get("schema") or "").strip() or None
    table  = (data.get("table") or "").strip()
    levels = data.get("levels") or {}
    preview_columns = data.get("preview_columns") or []

    if not table:
        return jsonify({"error": "table is required"}), 400

    # At least one level must be configured
    has_level = any(
        (v.get("column") or "").strip() or (v.get("fixed") or "").strip()
        for v in levels.values()
    )
    if not has_level:
        return jsonify({"error": "At least one hierarchy level must be configured"}), 400

    from services.location_sync_service import generate_webhook_token, build_connection_string

    existing = fetch_one(
        "SELECT id, config_encrypted FROM connectors WHERE tenant_id = %s AND connector_type = 'location_db_sync'",
        [tenant_id],
    )

    if existing:
        old_config = _decrypt_config(existing["config_encrypted"])
        if password and host and dbname and db_user:
            connection_string = build_connection_string(db_type, host, port, dbname, db_user, password)
        else:
            connection_string = old_config.get("connection_string", "")
            if not host:    host    = old_config.get("host", "")
            if not dbname:  dbname  = old_config.get("dbname", "")
            if not db_user: db_user = old_config.get("db_user", "")
        token      = old_config.get("webhook_token")
        token_hash = old_config.get("webhook_token_hash")
        if not token:
            token, token_hash = generate_webhook_token()
    else:
        if not (host and dbname and db_user and password):
            return jsonify({"error": "host, dbname, db_user, and password are required for initial setup"}), 400
        connection_string = build_connection_string(db_type, host, port, dbname, db_user, password)
        token, token_hash = generate_webhook_token()

    config = {
        "connection_string": connection_string,
        "db_type": db_type,
        "host": host,
        "port": port,
        "dbname": dbname,
        "db_user": db_user,
        "schema": schema,
        "table": table,
        "levels": levels,
        "preview_columns": preview_columns,
        "webhook_token": token,
        "webhook_token_hash": token_hash,
        "last_result": (existing and _decrypt_config(existing["config_encrypted"]).get("last_result")) or None,
    }
    encrypted = _encrypt_config(config)

    if existing:
        execute(
            """UPDATE connectors
               SET config_encrypted = %s, webhook_token_hash = %s, is_active = true, last_error = NULL
               WHERE id = %s""",
            [encrypted, token_hash, existing["id"]],
        )
    else:
        insert_returning(
            """INSERT INTO connectors (tenant_id, connector_type, name, config_encrypted, webhook_token_hash)
               VALUES (%s, 'location_db_sync', 'Location DB Sync', %s, %s) RETURNING id""",
            [tenant_id, encrypted, token_hash],
        )

    return jsonify({"ok": True, "webhook_token": token})


@hierarchies_bp.route("/locations/db-sync/test", methods=["POST"])
@login_required
@require_permission("locations.manage")
def test_location_db_sync():
    """Test external DB connection: SELECT * FROM schema.table LIMIT 5."""
    data = request.json or {}
    db_type  = (data.get("db_type") or "").strip()
    host     = (data.get("host") or "").strip()
    port     = int(data.get("port") or 5432)
    dbname   = (data.get("dbname") or "").strip()
    db_user  = (data.get("db_user") or "").strip()
    password = (data.get("password") or "").strip()
    schema   = (data.get("schema") or "").strip() or None
    table    = (data.get("table") or "").strip()

    if not table:
        return jsonify({"error": "table is required"}), 400

    from services.location_sync_service import build_connection_string, test_db_connection, classify_db_error

    if host and dbname and db_user and password:
        connection_string = build_connection_string(db_type or "postgresql", host, port, dbname, db_user, password)
    else:
        saved = fetch_one(
            "SELECT config_encrypted FROM connectors WHERE tenant_id = %s AND connector_type = 'location_db_sync' AND is_active = true",
            [get_tenant_id()],
        )
        if not saved:
            return jsonify({"error": "No saved connection — enter credentials to test"}), 400
        saved_config = _decrypt_config(saved["config_encrypted"])
        connection_string = saved_config.get("connection_string", "")

    try:
        result = test_db_connection(connection_string, schema, table)
        return jsonify(result)
    except Exception as e:
        logger.error("Location DB sync test failed: %s", e)
        status, message = classify_db_error(e)
        return jsonify({"error": message}), status


@hierarchies_bp.route("/locations/db-sync/run", methods=["POST"])
def run_location_db_sync():
    """Execute location sync. Called via webhook — Bearer token auth."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401

    from services.location_sync_service import hash_token
    token_hash = hash_token(auth[7:].strip())

    conn = fetch_one(
        """SELECT id, tenant_id, config_encrypted
           FROM connectors
           WHERE webhook_token_hash = %s AND connector_type = 'location_db_sync' AND is_active = true
           LIMIT 1""",
        [token_hash],
    )
    if not conn:
        return jsonify({"error": "Unauthorized"}), 401

    config = _decrypt_config(conn["config_encrypted"])
    tenant_id = conn["tenant_id"]

    try:
        from services.location_sync_service import run_sync
        result = run_sync(tenant_id, config)

        # Persist last_result back into encrypted config
        config["last_result"] = result
        execute(
            "UPDATE connectors SET last_sync_at = now(), last_error = NULL, config_encrypted = %s WHERE id = %s",
            [_encrypt_config(config), conn["id"]],
        )
        return jsonify(result)
    except Exception as e:
        logger.error("Location DB sync failed for tenant %s: %s", tenant_id, e)
        execute("UPDATE connectors SET last_error = %s WHERE id = %s", [str(e), conn["id"]])
        return jsonify({"error": str(e)}), 500


# ============================================================
# PROBLEM CATEGORIES
# ============================================================

@hierarchies_bp.route("/problem-categories", methods=["GET"])
@login_required
def list_problem_categories():
    """List tenant's problem categories as flat list."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    conditions = ["pc.is_active = true"]
    params = []

    if user["role"] != "super_admin" and tenant_id:
        conditions.append("pc.tenant_id = %s")
        params.append(tenant_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = fetch_all(
        f"""SELECT pc.*, t.name as team_name
            FROM problem_categories pc
            LEFT JOIN teams t ON t.id = pc.team_id
            {where} ORDER BY pc.sort_order, pc.name""",
        params,
    )
    return jsonify(rows)


@hierarchies_bp.route("/problem-categories", methods=["POST"])
@login_required
@require_permission("categories.manage")
def create_problem_category():
    data = request.json or {}
    tenant_id = data.get("tenant_id") or get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    cat_id = insert_returning(
        """INSERT INTO problem_categories (tenant_id, parent_id, name, sort_order, team_id)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        [tenant_id, data.get("parent_id"), name, data.get("sort_order", 0), data.get("team_id")],
    )
    return jsonify({"id": cat_id}), 201


@hierarchies_bp.route("/problem-categories/import", methods=["POST"])
@login_required
@require_permission("categories.manage")
def import_problem_categories():
    """Batch import problem categories from CSV or Excel."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "File is required"}), 400

    filename = file.filename.lower()
    content = file.read()

    try:
        from services.category_import_service import (
            parse_categories_excel, parse_categories_csv,
            resolve_and_insert_categories,
        )

        if filename.endswith(".csv"):
            categories = parse_categories_csv(content.decode("utf-8"))
        elif filename.endswith((".xlsx", ".xls")):
            categories = parse_categories_excel(content)
        else:
            return jsonify({"error": "Unsupported format. Use CSV or Excel (.xlsx)."}), 400

        if not categories:
            return jsonify({"error": "No valid categories found in file"}), 400

        result = resolve_and_insert_categories(tenant_id, categories)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("Category import failed: %s", e)
        return jsonify({"error": "Import failed. Check file format."}), 500


@hierarchies_bp.route("/problem-categories/<int:cat_id>", methods=["PUT"])
@login_required
@require_permission("categories.manage")
def update_problem_category(cat_id: int):
    data = request.json or {}
    allowed = ("name", "sort_order", "parent_id", "default_priority", "team_id")
    fields, params = [], []
    for col in allowed:
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])

    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    user = get_current_user()
    if user["role"] == "super_admin":
        params.append(cat_id)
        execute(f"UPDATE problem_categories SET {', '.join(fields)} WHERE id = %s", params)
    else:
        params.extend([cat_id, get_tenant_id()])
        execute(f"UPDATE problem_categories SET {', '.join(fields)} WHERE id = %s AND tenant_id = %s", params)
    return jsonify({"ok": True})


@hierarchies_bp.route("/problem-categories/<int:cat_id>", methods=["DELETE"])
@login_required
@require_permission("categories.manage")
def delete_problem_category(cat_id: int):
    """Soft-delete: set is_active = false."""
    user = get_current_user()
    if user["role"] == "super_admin":
        execute("UPDATE problem_categories SET is_active = false WHERE id = %s", [cat_id])
    else:
        execute("UPDATE problem_categories SET is_active = false WHERE id = %s AND tenant_id = %s", [cat_id, get_tenant_id()])
    return jsonify({"ok": True})


# ============================================================
# EXPORT & TEMPLATES
# ============================================================

@hierarchies_bp.route("/locations/template", methods=["GET"])
@login_required
@require_permission("locations.manage")
def location_template():
    """Download a blank CSV template for location import."""
    output = io.StringIO()
    writer = csv_mod.writer(output)
    writer.writerow([
        "Location Name", "Parent Location", "Contact Name", "Email",
        "Phone", "Address Line1", "Address Line2", "City", "State",
        "Country", "ZipCode", "Notes",
    ])
    # Level 1 — Brand
    writer.writerow(["Acme Corp", "", "", "", "", "", "", "", "", "", "",
                      "LEVEL 1 — Brand (top level, no parent). Leave contact/address empty for organizational tiers."])
    # Level 2 — Ownership type
    writer.writerow(["Corporate", "Acme Corp", "", "", "", "", "", "", "", "", "",
                      "LEVEL 2 — Ownership type. Parent = Brand above."])
    writer.writerow(["Franchise", "Acme Corp", "", "", "", "", "", "", "", "", "",
                      "LEVEL 2 — Another ownership type under the same Brand."])
    # Level 3 — State
    writer.writerow(["Florida", "Corporate", "", "", "", "", "", "", "FL", "USA", "",
                      "LEVEL 3 — State/Region. Parent = ownership type above."])
    writer.writerow(["Texas", "Franchise", "", "", "", "", "", "", "TX", "USA", "",
                      "LEVEL 3 — This state is under Franchise, not Corporate."])
    # Level 4 — Leaf locations
    writer.writerow([
        "Brickell", "Florida", "Maria Lopez", "maria@example.com",
        "305-555-0101", "1200 Brickell Ave", "Unit 4", "Miami", "FL",
        "USA", "33131",
        "LEVEL 4 — Actual location. Fill in contact & address for leaf locations.",
    ])
    writer.writerow([
        "Coral Gables", "Florida", "James Chen", "james@example.com",
        "305-555-0102", "250 Miracle Mile", "", "Coral Gables", "FL",
        "USA", "33134",
        "LEVEL 4 — Another location under Florida > Corporate.",
    ])
    writer.writerow([
        "Downtown Dallas", "Texas", "Amy Torres", "amy@example.com",
        "214-555-0201", "500 Main St", "Suite 100", "Dallas", "TX",
        "USA", "75201",
        "LEVEL 4 — Location under Texas > Franchise.",
    ])
    # Instruction rows
    writer.writerow([""] * 12)
    writer.writerow(["HOW IT WORKS:", "", "", "", "", "", "", "", "", "", "", ""])
    writer.writerow(["1. Parent Location links each row to its parent — this builds the tree.", "", "", "", "", "", "", "", "", "", "", ""])
    writer.writerow(["2. Upper tiers (Brand, Type, State) are organizational — leave contact/address blank.", "", "", "", "", "", "", "", "", "", "", ""])
    writer.writerow(["3. Leaf locations (actual sites) should have contact name, email, phone, and address filled in.", "", "", "", "", "", "", "", "", "", "", ""])
    writer.writerow(["4. You can nest as many levels as you need. Delete these instruction rows before importing.", "", "", "", "", "", "", "", "", "", "", ""])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=location_import_template.csv"},
    )


@hierarchies_bp.route("/locations/export", methods=["GET"])
@login_required
@require_permission("locations.manage")
def export_locations():
    """Export tenant's current locations as CSV."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    conditions = ["is_active = true"]
    params = []
    if user["role"] != "super_admin" and tenant_id:
        conditions.append("tenant_id = %s")
        params.append(tenant_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = fetch_all(
        f"SELECT id, parent_id, name, level_label, phone, email FROM locations {where} ORDER BY sort_order, name",
        params,
    )

    # Build id→name lookup for parent resolution
    id_to_name = {r["id"]: r["name"] for r in rows}

    output = io.StringIO()
    writer = csv_mod.writer(output)
    writer.writerow([
        "Location Name", "Parent Location", "Level Label", "Email",
        "Phone",
    ])
    for r in rows:
        writer.writerow([
            r["name"],
            id_to_name.get(r["parent_id"], "") if r["parent_id"] else "",
            r["level_label"] or "",
            r["email"] or "",
            r["phone"] or "",
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=locations_export.csv"},
    )


@hierarchies_bp.route("/problem-categories/template", methods=["GET"])
@login_required
@require_permission("categories.manage")
def category_template():
    """Download a blank CSV template for category import."""
    output = io.StringIO()
    writer = csv_mod.writer(output)
    writer.writerow(["Tier 1", "Tier 2", "Tier 3", "Tier 4", "Severity", "Notes"])
    # 2-level examples
    writer.writerow(["Hardware", "Printers", "", "", "p3", "2-tier: Tier 1 > Tier 2. Severity on the deepest tier."])
    writer.writerow(["Hardware", "Monitors", "", "", "p3", "2-tier: same Tier 1, different Tier 2."])
    # 3-level examples
    writer.writerow(["Hardware", "Printers", "Paper Jam", "", "p2", "3-tier: Tier 1 > Tier 2 > Tier 3."])
    writer.writerow(["Hardware", "Printers", "Toner Replace", "", "p4", "3-tier: another issue under Printers."])
    writer.writerow(["Hardware", "Monitors", "Display Flickering", "", "p2", ""])
    writer.writerow(["Hardware", "Monitors", "No Signal", "", "p1", "p1 = Urgent, p2 = High, p3 = Medium, p4 = Low."])
    # 4-level examples
    writer.writerow(["Software", "Email", "Outlook", "Cannot Send", "p2", "4-tier: full depth. Tier 1 > 2 > 3 > 4."])
    writer.writerow(["Software", "Email", "Outlook", "Calendar Sync", "p3", "4-tier: another specific issue under Outlook."])
    writer.writerow(["Software", "Email", "Gmail", "Login Failed", "p2", "Different Tier 3 under Email."])
    # More 3-level
    writer.writerow(["Network", "VPN", "Connection Drops", "", "p1", ""])
    writer.writerow(["Network", "Wi-Fi", "Slow Speed", "", "p3", ""])
    writer.writerow(["Network", "Wi-Fi", "Cannot Connect", "", "p2", ""])
    # Instructions
    writer.writerow(["", "", "", "", "", ""])
    writer.writerow(["HOW IT WORKS:", "", "", "", "", ""])
    writer.writerow(["1. Each row defines a category path from left (broadest) to right (most specific).", "", "", "", "", ""])
    writer.writerow(["2. Leave unused tier columns blank — you can use 1 to 4 levels of depth.", "", "", "", "", ""])
    writer.writerow(["3. Severity is optional: p1 (Urgent), p2 (High), p3 (Medium), p4 (Low).", "", "", "", "", ""])
    writer.writerow(["4. Duplicate rows are skipped — safe to re-import. Delete these instruction rows before importing.", "", "", "", "", ""])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=category_import_template.csv"},
    )


@hierarchies_bp.route("/problem-categories/export", methods=["GET"])
@login_required
@require_permission("categories.manage")
def export_problem_categories():
    """Export tenant's current problem categories as CSV with tier columns."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    conditions = ["is_active = true"]
    params = []
    if user["role"] != "super_admin" and tenant_id:
        conditions.append("tenant_id = %s")
        params.append(tenant_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = fetch_all(
        f"SELECT id, parent_id, name, default_priority FROM problem_categories {where} ORDER BY sort_order, name",
        params,
    )

    # Build id→row lookup
    by_id = {r["id"]: r for r in rows}

    def build_path(row):
        """Walk up the tree to build the full tier path."""
        path = [row["name"]]
        current = row
        while current["parent_id"] and current["parent_id"] in by_id:
            current = by_id[current["parent_id"]]
            path.insert(0, current["name"])
        return path

    # Find leaf nodes (nodes that are not a parent of any other node)
    parent_ids = {r["parent_id"] for r in rows if r["parent_id"]}
    leaves = [r for r in rows if r["id"] not in parent_ids]

    # Determine max depth
    max_depth = 0
    paths = []
    for leaf in leaves:
        path = build_path(leaf)
        max_depth = max(max_depth, len(path))
        paths.append((path, leaf.get("default_priority") or ""))
    max_depth = max(max_depth, 4)  # minimum 4 tier columns

    output = io.StringIO()
    writer = csv_mod.writer(output)
    headers = [f"Tier {i+1}" for i in range(max_depth)] + ["Severity"]
    writer.writerow(headers)

    for path, priority in paths:
        row_data = path + [""] * (max_depth - len(path)) + [priority]
        writer.writerow(row_data)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=categories_export.csv"},
    )
