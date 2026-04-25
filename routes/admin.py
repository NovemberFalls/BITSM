"""Admin blueprint: tenant CRUD, module toggling, user management."""

import logging
import re

from flask import Blueprint, jsonify, request

from app import limiter
from config import Config
from routes.auth import login_required, require_role, require_permission, get_current_user
from models.db import fetch_all, fetch_one, insert_returning, execute, cursor

logger = logging.getLogger(__name__)
admin_bp = Blueprint("admin", __name__)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ============================================================
# Setup Status (activation checklist)
# ============================================================

@admin_bp.route("/setup-status", methods=["GET"])
@login_required
def setup_status():
    """Return completion status for the tenant activation checklist.

    Checks actual DB state for each setup step:
    - ai_enabled: AI module (slug='ai') is enabled for this tenant
    - categories_configured: at least 1 active problem category exists
    - team_invited: more than 1 active user exists for this tenant
    - kb_created: at least 1 tenant collection (KB module) exists
    - first_ticket: at least 1 ticket exists
    """
    from routes.auth import get_tenant_id

    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "No tenant"}), 400

    # 1. AI module enabled for this tenant
    ai_row = fetch_one(
        """SELECT 1 FROM tenant_modules tm
           JOIN knowledge_modules km ON km.id = tm.module_id
           WHERE tm.tenant_id = %s AND km.slug = 'ai'
           LIMIT 1""",
        [tenant_id],
    )

    # 2. At least 1 active problem category
    cat_row = fetch_one(
        "SELECT 1 FROM problem_categories WHERE tenant_id = %s AND is_active = true LIMIT 1",
        [tenant_id],
    )

    # 3. More than 1 active user (tenant admin + at least one other)
    user_count_row = fetch_one(
        "SELECT count(*) as cnt FROM users WHERE tenant_id = %s AND is_active = true",
        [tenant_id],
    )
    team_invited = (user_count_row["cnt"] if user_count_row else 0) > 1

    # 4. At least 1 tenant collection (KB content)
    kb_row = fetch_one(
        "SELECT 1 FROM tenant_collections WHERE tenant_id = %s LIMIT 1",
        [tenant_id],
    )

    # 5. At least 1 ticket
    ticket_row = fetch_one(
        "SELECT 1 FROM tickets WHERE tenant_id = %s LIMIT 1",
        [tenant_id],
    )

    steps = {
        "ai_enabled": ai_row is not None,
        "categories_configured": cat_row is not None,
        "team_invited": team_invited,
        "kb_created": kb_row is not None,
        "first_ticket": ticket_row is not None,
    }

    return jsonify({
        "complete": all(steps.values()),
        "steps": steps,
    })


@admin_bp.route("/setup/enable-ai", methods=["POST"])
@login_required
def setup_enable_ai():
    """Self-service: tenant admin enables the AI module for their own tenant."""
    from routes.auth import get_tenant_id

    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if user.get("role") not in ("super_admin", "tenant_admin"):
        return jsonify({"error": "Forbidden"}), 403

    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "No tenant"}), 400

    # Find the AI module
    ai_module = fetch_one(
        "SELECT id FROM knowledge_modules WHERE slug = 'ai' AND is_active = true LIMIT 1"
    )
    if not ai_module:
        return jsonify({"error": "AI module not found"}), 404

    # Enable it (idempotent)
    insert_returning(
        """INSERT INTO tenant_modules (tenant_id, module_id, enabled_by)
           VALUES (%s, %s, %s)
           ON CONFLICT (tenant_id, module_id) DO NOTHING
           RETURNING id""",
        [tenant_id, ai_module["id"], user["email"]],
    )

    # Also enable default AI sub-features (agent_chat, client_chat ON by default)
    default_on_slugs = ('agent_chat', 'client_chat')
    features = fetch_all(
        """SELECT mf.id, mf.slug
           FROM module_features mf
           WHERE mf.module_id = %s AND mf.slug IN %s""",
        [ai_module["id"], default_on_slugs],
    )
    for f in features:
        execute(
            """INSERT INTO tenant_module_features (tenant_id, feature_id, enabled, enabled_by)
               VALUES (%s, %s, true, %s)
               ON CONFLICT (tenant_id, feature_id) DO UPDATE SET enabled = true""",
            [tenant_id, f["id"], user["email"]],
        )

    logger.info("Tenant %s enabled AI module (by %s)", tenant_id, user["email"])
    return jsonify({"ok": True})


# ============================================================
# Tenants
# ============================================================

@admin_bp.route("/tenants", methods=["GET"])
@require_role("super_admin")
def list_tenants():
    tenants = fetch_all(
        """SELECT t.*,
                  count(DISTINCT tm.id) as enabled_modules,
                  count(DISTINCT u.id) as user_count
           FROM tenants t
           LEFT JOIN tenant_modules tm ON tm.tenant_id = t.id
           LEFT JOIN users u ON u.tenant_id = t.id AND u.is_active = true
           GROUP BY t.id
           ORDER BY t.name"""
    )
    return jsonify(tenants)


@admin_bp.route("/tenants", methods=["POST"])
@require_role("super_admin")
@limiter.limit("10 per minute")
def create_tenant():
    if not Config.TENANT_CREATION_ENABLED:
        return jsonify({"error": "Tenant creation is currently disabled"}), 403
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    slug = data.get("slug") or _slugify(name)
    domain = data.get("domain", "").strip() or None

    existing = fetch_one("SELECT id FROM tenants WHERE slug = %s", [slug])
    if existing:
        return jsonify({"error": "Slug already exists"}), 409

    tenant_id = insert_returning(
        """INSERT INTO tenants (name, slug, domain, settings)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        [name, slug, domain, data.get("settings", "{}")],
    )
    try:
        from services.audit_service import log_event, TENANT_CREATED, RT_TENANT
        actor = get_current_user()
        log_event(TENANT_CREATED, user_id=actor.get("id"), resource_type=RT_TENANT, resource_id=tenant_id, details={"name": name, "slug": slug}, request=request)
    except Exception:
        pass
    return jsonify({"id": tenant_id, "slug": slug}), 201


@admin_bp.route("/tenants/<int:tenant_id>", methods=["PUT"])
@require_role("super_admin")
def update_tenant(tenant_id: int):
    data = request.json or {}
    fields, params = [], []
    for col in ("name", "domain", "logo_url", "is_active"):
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])
    if "settings" in data:
        fields.append("settings = %s::jsonb")
        params.append(data["settings"] if isinstance(data["settings"], str) else str(data["settings"]))
    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    fields.append("updated_at = now()")
    params.append(tenant_id)
    execute(f"UPDATE tenants SET {', '.join(fields)} WHERE id = %s", params)
    return jsonify({"ok": True})


@admin_bp.route("/tenants/<int:tenant_id>/allowed-domains", methods=["GET"])
@require_permission("users.manage")
def get_allowed_domains(tenant_id: int):
    """Return the allowed_domains setting for a tenant (admin-only)."""
    user = get_current_user()
    if user["role"] == "tenant_admin" and user.get("tenant_id") != tenant_id:
        return jsonify({"error": "Forbidden"}), 403
    tenant = fetch_one("SELECT settings FROM tenants WHERE id = %s", [tenant_id])
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404
    settings = tenant.get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    return jsonify({"allowed_domains": settings.get("allowed_domains", "")})


@admin_bp.route("/tenants/<int:tenant_id>/settings", methods=["PUT"])
@require_permission("users.manage")
def update_tenant_settings(tenant_id: int):
    """Update tenant settings (problem_field_label, location_levels, etc.)."""
    import json as json_mod
    user = get_current_user()
    # tenant_admin can only update their own tenant
    if user["role"] == "tenant_admin" and user.get("tenant_id") != tenant_id:
        return jsonify({"error": "Forbidden"}), 403
    data = request.json or {}
    # Merge with existing settings
    existing = fetch_one("SELECT settings FROM tenants WHERE id = %s", [tenant_id])
    if not existing:
        return jsonify({"error": "Tenant not found"}), 404

    current_settings = existing.get("settings") or {}
    if not isinstance(current_settings, dict):
        current_settings = {}

    # Update only provided keys
    for key in ("problem_field_label", "location_levels", "chat_greeting",
                 "portal_greeting", "portal_background", "portal_cards",
                 "portal_card_opacity", "portal_logo_url",
                 "ai_audit_auto_close_days", "ai_audit_enabled",
                 "ai_fallback_message", "usage_visible",
                 "app_name", "app_url", "inbound_email_domain", "logo_url",
                 "ticket_form_settings", "idle_timeout_minutes", "require_mfa",
                 "allowed_domains"):
        if key in data:
            current_settings[key] = data[key]

    # Validate idle_timeout_minutes: must be 15–480 (SOC 2 CC6.2 minimum 15 min)
    if "idle_timeout_minutes" in data:
        try:
            val = int(data["idle_timeout_minutes"])
            if val < 15 or val > 480:
                return jsonify({"error": "idle_timeout_minutes must be between 15 and 480"}), 400
            current_settings["idle_timeout_minutes"] = val
        except (TypeError, ValueError):
            return jsonify({"error": "idle_timeout_minutes must be a number"}), 400

    execute(
        "UPDATE tenants SET settings = %s::jsonb, updated_at = now() WHERE id = %s",
        [json_mod.dumps(current_settings), tenant_id],
    )

    # Ticket prefix — direct column, available to tenant_admin for their own tenant
    if "ticket_prefix" in data:
        import re
        raw_prefix = str(data["ticket_prefix"]).upper().strip()
        if not raw_prefix:
            return jsonify({"error": "ticket_prefix cannot be blank"}), 400
        if len(raw_prefix) > 20:
            return jsonify({"error": "ticket_prefix must be 20 characters or fewer"}), 400
        if not re.match(r'^[A-Z0-9\-]+$', raw_prefix):
            return jsonify({"error": "ticket_prefix may only contain letters, numbers, and hyphens"}), 400
        execute("UPDATE tenants SET ticket_prefix = %s, updated_at = now() WHERE id = %s", [raw_prefix, tenant_id])

    # Email sender name — any admin can configure the display name for their tenant's outbound emails
    if "email_from_name" in data:
        execute("UPDATE tenants SET email_from_name = %s WHERE id = %s", [data["email_from_name"], tenant_id])

    # Email from address — super_admin only (SPF/DKIM deliverability concern)
    if user["role"] == "super_admin":
        if "email_from_address" in data:
            execute("UPDATE tenants SET email_from_address = %s WHERE id = %s", [data["email_from_address"], tenant_id])

    return jsonify({"ok": True})


@admin_bp.route("/tenants/<int:tenant_id>", methods=["DELETE"])
@require_role("super_admin")
@limiter.limit("5 per minute")
def delete_tenant(tenant_id: int):
    execute("UPDATE tenants SET is_active = false, updated_at = now() WHERE id = %s", [tenant_id])
    return jsonify({"ok": True})


# ============================================================
# Tenant plan tier management
# ============================================================

@admin_bp.route("/tenants/<int:tenant_id>/plan", methods=["GET"])
@require_role("super_admin")
def get_tenant_plan(tenant_id: int):
    """Get tenant plan tier details."""
    tenant = fetch_one(
        "SELECT id, name, plan_tier, plan_expires_at, plan_extended_by, plan_extended_at FROM tenants WHERE id = %s",
        [tenant_id],
    )
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    # Get extender name if exists
    extended_by_name = None
    if tenant.get("plan_extended_by"):
        user = fetch_one("SELECT name FROM users WHERE id = %s", [tenant["plan_extended_by"]])
        extended_by_name = user["name"] if user else None

    return jsonify({
        "tenant_id": tenant["id"],
        "name": tenant["name"],
        "plan_tier": tenant.get("plan_tier", "free"),
        "plan_expires_at": tenant.get("plan_expires_at"),
        "plan_extended_by": tenant.get("plan_extended_by"),
        "plan_extended_by_name": extended_by_name,
        "plan_extended_at": tenant.get("plan_extended_at"),
    })


@admin_bp.route("/tenants/<int:tenant_id>/plan", methods=["PUT"])
@require_role("super_admin")
def update_tenant_plan(tenant_id: int):
    """Update tenant plan tier. Super admin can set tier and extend paid features.

    Body:
      plan_tier: 'free' | 'trial' | 'starter' | 'pro' | 'business' | 'enterprise'
      extend_days: int (optional) — extend from now by this many days
      plan_expires_at: ISO datetime (optional) — set explicit expiration
    """
    user = get_current_user()
    data = request.json or {}

    plan_tier = data.get("plan_tier")
    from services.billing_service import VALID_TIERS
    if plan_tier and plan_tier not in VALID_TIERS:
        return jsonify({"error": "Invalid plan tier"}), 400

    fields, params = [], []

    if plan_tier:
        fields.append("plan_tier = %s")
        params.append(plan_tier)

    extend_days = data.get("extend_days")
    if extend_days and isinstance(extend_days, (int, float)) and extend_days > 0:
        fields.append(f"plan_expires_at = now() + interval '{int(extend_days)} days'")
        fields.append("plan_extended_by = %s")
        params.append(user["id"])
        fields.append("plan_extended_at = now()")
    elif "plan_expires_at" in data:
        if data["plan_expires_at"] is None:
            fields.append("plan_expires_at = NULL")
        else:
            fields.append("plan_expires_at = %s::timestamptz")
            params.append(data["plan_expires_at"])
        fields.append("plan_extended_by = %s")
        params.append(user["id"])
        fields.append("plan_extended_at = now()")

    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    fields.append("updated_at = now()")
    params.append(tenant_id)
    execute(f"UPDATE tenants SET {', '.join(fields)} WHERE id = %s", params)

    return jsonify({"ok": True})


# ============================================================
# Module toggling per tenant
# ============================================================

@admin_bp.route("/tenants/<int:tenant_id>/modules", methods=["GET"])
@require_permission("users.manage")
def get_tenant_modules(tenant_id: int):
    modules = fetch_all(
        """SELECT km.*, km.module_type, tm.enabled_at, tm.enabled_by,
                  CASE WHEN tm.id IS NOT NULL THEN true ELSE false END as enabled,
                  (SELECT count(*) FROM documents d WHERE d.module_id = km.id) as doc_count,
                  (SELECT count(*) FROM document_chunks dc WHERE dc.module_id = km.id) as chunk_count
           FROM knowledge_modules km
           LEFT JOIN tenant_modules tm ON tm.module_id = km.id AND tm.tenant_id = %s
           WHERE km.is_active = true
           ORDER BY km.module_type, km.name""",
        [tenant_id],
    )
    return jsonify(modules)


@admin_bp.route("/tenants/<int:tenant_id>/modules/<int:module_id>/enable", methods=["POST"])
@require_role("super_admin")
def enable_module(tenant_id: int, module_id: int):
    user = get_current_user()
    insert_returning(
        """INSERT INTO tenant_modules (tenant_id, module_id, enabled_by)
           VALUES (%s, %s, %s)
           ON CONFLICT (tenant_id, module_id) DO NOTHING
           RETURNING id""",
        [tenant_id, module_id, user["email"]],
    )
    return jsonify({"ok": True})


@admin_bp.route("/tenants/<int:tenant_id>/modules/<int:module_id>/disable", methods=["POST"])
@require_role("super_admin")
def disable_module(tenant_id: int, module_id: int):
    execute("DELETE FROM tenant_modules WHERE tenant_id = %s AND module_id = %s", [tenant_id, module_id])
    return jsonify({"ok": True})


# ============================================================
# Module features (sub-toggles)
# ============================================================

@admin_bp.route("/tenants/<int:tenant_id>/modules/<int:module_id>/features", methods=["GET"])
@require_permission("atlas.admin")
def get_module_features(tenant_id: int, module_id: int):
    """Get sub-features for a module with tenant-level enable/disable state."""
    features = fetch_all(
        """SELECT mf.*,
                  COALESCE(tmf.enabled,
                      CASE WHEN mf.slug IN ('agent_chat', 'client_chat') THEN true ELSE false END
                  ) as enabled,
                  tmf.enabled_at, tmf.enabled_by
           FROM module_features mf
           LEFT JOIN tenant_module_features tmf
               ON tmf.feature_id = mf.id AND tmf.tenant_id = %s
           WHERE mf.module_id = %s AND mf.is_active = true
           ORDER BY mf.sort_order""",
        [tenant_id, module_id],
    )
    return jsonify(features)


@admin_bp.route("/tenants/<int:tenant_id>/features/<int:feature_id>/enable", methods=["POST"])
@require_permission("atlas.admin")
def enable_feature(tenant_id: int, feature_id: int):
    """Enable a module sub-feature for a tenant."""
    user = get_current_user()
    if user["role"] == "tenant_admin" and user.get("tenant_id") != tenant_id:
        return jsonify({"error": "Forbidden"}), 403
    insert_returning(
        """INSERT INTO tenant_module_features (tenant_id, feature_id, enabled, enabled_by)
           VALUES (%s, %s, true, %s)
           ON CONFLICT (tenant_id, feature_id) DO UPDATE SET enabled = true, enabled_at = now(), enabled_by = EXCLUDED.enabled_by
           RETURNING id""",
        [tenant_id, feature_id, user["email"]],
    )
    return jsonify({"ok": True})


@admin_bp.route("/tenants/<int:tenant_id>/features/<int:feature_id>/disable", methods=["POST"])
@require_permission("atlas.admin")
def disable_feature(tenant_id: int, feature_id: int):
    """Disable a module sub-feature for a tenant."""
    user = get_current_user()
    if user["role"] == "tenant_admin" and user.get("tenant_id") != tenant_id:
        return jsonify({"error": "Forbidden"}), 403
    execute(
        """UPDATE tenant_module_features SET enabled = false, enabled_at = now()
           WHERE tenant_id = %s AND feature_id = %s""",
        [tenant_id, feature_id],
    )
    return jsonify({"ok": True})


# ============================================================
# Knowledge modules catalog
# ============================================================

@admin_bp.route("/modules", methods=["GET"])
@require_role("super_admin")
def list_modules():
    modules = fetch_all(
        """SELECT km.*,
                  (SELECT count(*) FROM documents d WHERE d.module_id = km.id) as doc_count,
                  (SELECT count(*) FROM document_chunks dc WHERE dc.module_id = km.id) as chunk_count
           FROM knowledge_modules km
           ORDER BY km.name"""
    )
    return jsonify(modules)


@admin_bp.route("/ingest/<module_slug>", methods=["POST"])
@require_role("super_admin")
def ingest_module(module_slug: str):
    """Trigger document ingestion for a module from its documents/ directory."""
    import os
    from config import Config
    from services.ingestion_service import ingest_directory

    directory = os.path.join(Config.DOCUMENTS_DIR, module_slug)
    if not os.path.isdir(directory):
        return jsonify({"error": f"No documents directory for '{module_slug}'"}), 404

    result = ingest_directory(directory, module_slug)
    if result.get("error"):
        return jsonify(result), 400
    return jsonify(result)


@admin_bp.route("/modules/<int:module_id>/tenants", methods=["GET"])
@require_role("super_admin")
def get_module_tenants(module_id: int):
    """Get all tenants with enabled state for a specific module."""
    tenants = fetch_all(
        """SELECT t.id, t.name, t.slug,
                  CASE WHEN tm.id IS NOT NULL THEN true ELSE false END as enabled
           FROM tenants t
           LEFT JOIN tenant_modules tm ON tm.module_id = %s AND tm.tenant_id = t.id
           WHERE t.is_active = true
           ORDER BY t.name""",
        [module_id],
    )
    return jsonify(tenants)


@admin_bp.route("/modules", methods=["POST"])
@require_role("super_admin")
def create_module():
    data = request.json or {}
    name = data.get("name", "").strip()
    slug = data.get("slug") or _slugify(name)
    if not name:
        return jsonify({"error": "Name is required"}), 400

    module_id = insert_returning(
        """INSERT INTO knowledge_modules (slug, name, description, icon)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        [slug, name, data.get("description", ""), data.get("icon", "folder")],
    )
    return jsonify({"id": module_id, "slug": slug}), 201


# ============================================================
# Users
# ============================================================

# ============================================================
# KB Pipeline management
# ============================================================

@admin_bp.route("/pipeline/<module_slug>/run", methods=["POST"])
@require_role("super_admin")
@limiter.limit("2 per minute")
def run_pipeline(module_slug: str):
    """Trigger KB pipeline (clean → chunk → embed) for a module."""
    from services.pipeline_service import start_pipeline, get_pipeline_status

    current = get_pipeline_status(module_slug)
    if current.get("status") == "running":
        return jsonify({"error": "Pipeline already running", "status": current}), 409

    force = (request.json or {}).get("force", False)
    start_pipeline(module_slug, force=force)
    return jsonify({"ok": True, "message": f"Pipeline started for {module_slug}"})


@admin_bp.route("/pipeline/<module_slug>/status", methods=["GET"])
@require_role("super_admin")
def pipeline_status(module_slug: str):
    """Get current pipeline status for a module."""
    from services.pipeline_service import get_pipeline_status
    return jsonify(get_pipeline_status(module_slug))


# ============================================================
# Token Usage
# ============================================================

@admin_bp.route("/usage", methods=["GET"])
@require_role("super_admin", "tenant_admin")
def get_token_usage():
    """Per-tenant LLM token usage stats.

    Query params:
      period     — one of: day, week, month, quarter, year (default: month)
      start_date — ISO date (YYYY-MM-DD), overrides period
      end_date   — ISO date (YYYY-MM-DD), overrides period
      tenant_id  — filter to single tenant (optional, super_admin only)

    tenant_admin users are auto-scoped to their own tenant.
    """
    _PERIOD_MAP = {
        "day":     "1 day",
        "week":    "7 days",
        "month":   "1 month",
        "quarter": "3 months",
        "year":    "1 year",
    }
    user = get_current_user()
    period     = request.args.get("period", "month")
    start_date = request.args.get("start_date")  # YYYY-MM-DD
    end_date   = request.args.get("end_date")     # YYYY-MM-DD

    # tenant_admin always scoped to own tenant; super_admin can optionally filter
    if user["role"] == "tenant_admin":
        tenant_id = user["tenant_id"]
    else:
        tenant_id = request.args.get("tenant_id", type=int)

    # Date range takes precedence over period presets
    if start_date and end_date:
        base_filter = "WHERE u.created_at >= %s::date AND u.created_at < %s::date + interval '1 day'"
        params: list = [start_date, end_date]
    else:
        interval = _PERIOD_MAP.get(period, "1 month")
        base_filter = "WHERE u.created_at >= now() - %s::interval"
        params: list = [interval]

    if tenant_id:
        base_filter += " AND u.tenant_id = %s"
        params.append(tenant_id)

    # Caller breakdown per tenant (aggregated over full period)
    by_caller = fetch_all(
        f"""SELECT
               COALESCE(t.name, 'System')                      AS tenant_name,
               u.tenant_id,
               u.caller,
               u.model,
               count(*)                                        AS calls,
               sum(u.input_tokens)                            AS input_tokens,
               sum(u.output_tokens)                           AS output_tokens,
               sum(u.cost_usd)                                AS cost_usd
           FROM tenant_token_usage u
           LEFT JOIN tenants t ON t.id = u.tenant_id
           {base_filter}
           GROUP BY t.name, u.tenant_id, u.caller, u.model
           ORDER BY u.tenant_id, cost_usd DESC""",
        params,
    )

    # Tenant-level totals with admin contact info
    totals = fetch_all(
        f"""SELECT
               COALESCE(t.name, 'System')                      AS tenant_name,
               t.slug                                          AS tenant_slug,
               u.tenant_id,
               count(*)                                        AS total_calls,
               sum(u.input_tokens)                            AS total_input_tokens,
               sum(u.output_tokens)                           AS total_output_tokens,
               sum(u.cost_usd)                                AS total_cost_usd,
               min(u.created_at)                              AS first_call,
               max(u.created_at)                              AS last_call,
               (SELECT adm.name  FROM users adm
                WHERE adm.tenant_id = u.tenant_id
                  AND adm.role IN ('tenant_admin', 'super_admin')
                  AND adm.is_active = true
                ORDER BY CASE adm.role WHEN 'tenant_admin' THEN 1 ELSE 2 END, adm.id
                LIMIT 1)                                      AS admin_name,
               (SELECT adm.email FROM users adm
                WHERE adm.tenant_id = u.tenant_id
                  AND adm.role IN ('tenant_admin', 'super_admin')
                  AND adm.is_active = true
                ORDER BY CASE adm.role WHEN 'tenant_admin' THEN 1 ELSE 2 END, adm.id
                LIMIT 1)                                      AS admin_email
           FROM tenant_token_usage u
           LEFT JOIN tenants t ON t.id = u.tenant_id
           {base_filter}
           GROUP BY t.name, t.slug, u.tenant_id
           ORDER BY total_cost_usd DESC""",
        params,
    )

    # Grand total (all tenants)
    grand = fetch_one(
        f"""SELECT
               count(*)           AS total_calls,
               sum(input_tokens)  AS total_input_tokens,
               sum(output_tokens) AS total_output_tokens,
               sum(cost_usd)      AS total_cost_usd
           FROM tenant_token_usage u
           {base_filter}""",
        params,
    ) or {}

    # Phone usage from phone_sessions (ElevenLabs-mediated, not in tenant_token_usage)
    if start_date and end_date:
        phone_filter = "WHERE ps.created_at >= %s::date AND ps.created_at < %s::date + interval '1 day'"
        phone_params: list = [start_date, end_date]
    else:
        phone_filter = "WHERE ps.created_at >= now() - %s::interval"
        phone_params = [interval]
    if tenant_id:
        phone_filter += " AND ps.tenant_id = %s"
        phone_params.append(tenant_id)

    # Split into AI calls (reached ElevenLabs) and dropped/IVR (Twilio-only)
    phone_ai_rows = fetch_all(
        f"""SELECT
               ps.tenant_id,
               count(*)                                                          AS calls,
               COALESCE(sum(ps.el_llm_input_tokens),  0)                        AS input_tokens,
               COALESCE(sum(ps.el_llm_output_tokens), 0)                        AS output_tokens,
               COALESCE(sum(ps.el_cost_credits) / 10000.0, 0)
                 + COALESCE(sum(ps.twilio_cost_cents) / 100.0, 0)               AS cost_usd,
               max(ps.created_at)                                                AS last_call
           FROM phone_sessions ps
           {phone_filter}
             AND ps.elevenlabs_conversation_id IS NOT NULL
             AND ps.elevenlabs_conversation_id != 'unknown'
           GROUP BY ps.tenant_id""",
        phone_params,
    )
    # IVR abandoned = reached the greeting, heard it, but hung up before/after pressing digit
    phone_ivr_rows = fetch_all(
        f"""SELECT
               ps.tenant_id,
               count(*)                                                          AS calls,
               0                                                                 AS input_tokens,
               0                                                                 AS output_tokens,
               COALESCE(sum(ps.twilio_cost_cents) / 100.0, 0)                   AS cost_usd,
               max(ps.created_at)                                                AS last_call
           FROM phone_sessions ps
           {phone_filter}
             AND (ps.elevenlabs_conversation_id IS NULL
                  OR ps.elevenlabs_conversation_id = 'unknown')
             AND ps.status IN ('ivr', 'routing')
           GROUP BY ps.tenant_id""",
        phone_params,
    )
    # Dropped = never reached IVR (very early hang-up, pre-IVR test calls, etc.)
    phone_dropped_rows = fetch_all(
        f"""SELECT
               ps.tenant_id,
               count(*)                                                          AS calls,
               0                                                                 AS input_tokens,
               0                                                                 AS output_tokens,
               COALESCE(sum(ps.twilio_cost_cents) / 100.0, 0)                   AS cost_usd,
               max(ps.created_at)                                                AS last_call
           FROM phone_sessions ps
           {phone_filter}
             AND (ps.elevenlabs_conversation_id IS NULL
                  OR ps.elevenlabs_conversation_id = 'unknown')
             AND ps.status NOT IN ('ivr', 'routing')
           GROUP BY ps.tenant_id""",
        phone_params,
    )
    phone_rows = list(phone_ai_rows) + list(phone_ivr_rows) + list(phone_dropped_rows)

    # Inject as synthetic caller rows so the frontend renders them in the breakdown
    # Also fold phone costs into per-tenant totals and grand total so header matches rows
    by_caller = list(by_caller)
    totals    = [dict(t) for t in totals]
    totals_by_tid = {t["tenant_id"]: t for t in totals}

    phone_grand_calls = phone_grand_input = phone_grand_output = 0
    phone_grand_cost  = 0.0

    _phone_ai_set      = set(id(r) for r in phone_ai_rows)
    _phone_ivr_set     = set(id(r) for r in phone_ivr_rows)

    for pr in list(phone_ai_rows) + list(phone_ivr_rows) + list(phone_dropped_rows):
        cost  = float(pr["cost_usd"] or 0)
        calls = int(pr["calls"] or 0)
        inp   = int(pr["input_tokens"] or 0)
        out   = int(pr["output_tokens"] or 0)

        if id(pr) in _phone_ai_set:
            caller_key = "phone.calls"
            model_label = "ElevenLabs / Haiku 4.5"
        elif id(pr) in _phone_ivr_set:
            caller_key = "phone.ivr"
            model_label = "Twilio only"
        else:
            caller_key = "phone.dropped"
            model_label = "Twilio only"

        by_caller.append({
            "tenant_id":     pr["tenant_id"],
            "caller":        caller_key,
            "model":         model_label,
            "calls":         calls,
            "input_tokens":  inp,
            "output_tokens": out,
            "cost_usd":      cost,
        })

        # Merge into per-tenant total row
        if pr["tenant_id"] in totals_by_tid:
            t = totals_by_tid[pr["tenant_id"]]
            t["total_calls"]         = int(t.get("total_calls") or 0)         + calls
            t["total_input_tokens"]  = int(t.get("total_input_tokens") or 0)  + inp
            t["total_output_tokens"] = int(t.get("total_output_tokens") or 0) + out
            t["total_cost_usd"]      = float(t.get("total_cost_usd") or 0)    + cost

        phone_grand_calls  += calls
        phone_grand_input  += inp
        phone_grand_output += out
        phone_grand_cost   += cost

    # Fold into grand total
    grand = dict(grand)
    grand["total_calls"]         = int(grand.get("total_calls") or 0)         + phone_grand_calls
    grand["total_input_tokens"]  = int(grand.get("total_input_tokens") or 0)  + phone_grand_input
    grand["total_output_tokens"] = int(grand.get("total_output_tokens") or 0) + phone_grand_output
    grand["total_cost_usd"]      = float(grand.get("total_cost_usd") or 0)    + phone_grand_cost

    # Tenant list for filter dropdown (super_admin only)
    tenants_list = []
    if user["role"] == "super_admin":
        tenants_list = fetch_all(
            "SELECT id, name FROM tenants WHERE is_active = true ORDER BY name"
        )

    return jsonify({
        "period":      period,
        "start_date":  start_date,
        "end_date":    end_date,
        "grand_total": grand,
        "by_tenant":   totals,
        "by_caller":   by_caller,
        "tenants":     tenants_list,
    })


# ============================================================
# Users
# ============================================================

@admin_bp.route("/users", methods=["GET"])
@require_permission("users.manage")
def list_users():
    user = get_current_user()
    if user["role"] == "super_admin":
        users = fetch_all(
            """SELECT u.id, u.tenant_id, u.email, u.name, u.role, u.is_active,
                      u.first_name, u.last_name, u.phone, u.invite_status,
                      u.invited_at, u.expires_at, u.created_at,
                      t.name as tenant_name
               FROM users u
               LEFT JOIN tenants t ON t.id = u.tenant_id
               ORDER BY u.name"""
        )
    else:
        users = fetch_all(
            """SELECT u.id, u.tenant_id, u.email, u.name, u.role, u.is_active,
                      u.first_name, u.last_name, u.phone, u.invite_status,
                      u.invited_at, u.expires_at, u.created_at,
                      t.name as tenant_name
               FROM users u
               LEFT JOIN tenants t ON t.id = u.tenant_id
               WHERE u.tenant_id = %s
               ORDER BY u.name""",
            [user["tenant_id"]],
        )
    return jsonify(users)


@admin_bp.route("/users", methods=["POST"])
@require_permission("users.invite")
@limiter.limit("20 per minute")
def create_user():
    data = request.json or {}
    email = data.get("email", "").strip().lower()
    first_name = data.get("first_name", "").strip()
    last_name = data.get("last_name", "").strip()
    phone = data.get("phone", "").strip() or None
    role = data.get("role", "end_user")
    tenant_id = data.get("tenant_id")
    expires_at = data.get("expires_at") or None

    # Legacy name field fallback
    name = data.get("name", "").strip()
    if not name and (first_name or last_name):
        name = f"{first_name} {last_name}".strip()

    if not email:
        return jsonify({"error": "Email is required"}), 400
    if role not in ("tenant_admin", "agent", "end_user"):
        return jsonify({"error": "Invalid role"}), 400

    # Check for duplicate email within tenant
    existing = fetch_one(
        "SELECT id FROM users WHERE LOWER(email) = %s AND (tenant_id = %s OR tenant_id IS NULL) LIMIT 1",
        [email, tenant_id],
    )
    if existing:
        return jsonify({"error": "User with this email already exists"}), 409

    current = get_current_user()
    if current["role"] == "tenant_admin":
        tenant_id = current["tenant_id"]

    user_id = insert_returning(
        """INSERT INTO users (tenant_id, email, name, first_name, last_name, phone,
                              role, invite_status, invited_by, invited_at, expires_at, created_via)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'invited', %s, now(), %s, 'invite') RETURNING id""",
        [tenant_id, email, name, first_name or None, last_name or None, phone,
         role, current["id"], expires_at],
    )

    # Send invite email (background thread)
    from services.email_service import send_invite_email
    send_invite_email(
        user_id=user_id, user_email=email, user_name=name,
        role=role, tenant_id=tenant_id, expires_at=expires_at,
        inviter_id=current["id"],
    )

    try:
        from services.audit_service import log_event, USER_INVITED, RT_USER
        actor = get_current_user()
        log_event(USER_INVITED, tenant_id=actor.get("tenant_id"), user_id=actor.get("id"), resource_type=RT_USER, details={"email": email}, request=request)
    except Exception:
        pass
    return jsonify({"id": user_id}), 201


@admin_bp.route("/users/bulk-import", methods=["POST"])
@require_permission("users.invite")
def bulk_import_users():
    """Bulk import users from CSV file."""
    import csv as csv_mod
    import io

    current = get_current_user()
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "File is required"}), 400

    tenant_id = request.form.get("tenant_id") or (current.get("tenant_id") if current["role"] == "tenant_admin" else None)
    if current["role"] == "tenant_admin":
        tenant_id = current["tenant_id"]

    content = file.read().decode("utf-8")
    reader = csv_mod.DictReader(io.StringIO(content))

    created, skipped, errors = 0, 0, []
    for i, row in enumerate(reader, start=2):
        email = (row.get("email") or row.get("Email") or "").strip().lower()
        if not email:
            errors.append(f"Row {i}: missing email")
            continue

        first_name = (row.get("first_name") or row.get("First Name") or "").strip()
        last_name = (row.get("last_name") or row.get("Last Name") or "").strip()
        phone = (row.get("phone") or row.get("Phone") or "").strip() or None
        role = (row.get("role") or row.get("Role") or "end_user").strip().lower()
        if role not in ("tenant_admin", "agent", "end_user"):
            role = "end_user"

        name = f"{first_name} {last_name}".strip() or email

        existing = fetch_one(
            "SELECT id FROM users WHERE LOWER(email) = %s AND (tenant_id = %s OR tenant_id IS NULL) LIMIT 1",
            [email, tenant_id],
        )
        if existing:
            skipped += 1
            continue

        try:
            insert_returning(
                """INSERT INTO users (tenant_id, email, name, first_name, last_name, phone,
                                      role, invite_status, invited_by, invited_at, created_via)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'invited', %s, now(), 'import') RETURNING id""",
                [tenant_id, email, name, first_name or None, last_name or None, phone,
                 role, current["id"]],
            )
            created += 1
        except Exception as e:
            errors.append(f"Row {i}: {str(e)}")

    # --- Assign groups and teams for newly created users ---
    if created > 0:
        # Build lookups for group/team names (case-insensitive, tenant-scoped)
        from routes.auth import get_tenant_id
        tid = tenant_id or get_tenant_id()
        all_groups = {g["name"].lower(): g["id"] for g in fetch_all(
            "SELECT id, name FROM groups WHERE tenant_id = %s AND is_active = true", [tid]
        )} if tid else {}
        all_teams = {t["name"].lower(): t["id"] for t in fetch_all(
            "SELECT id, name FROM teams WHERE tenant_id = %s AND is_active = true", [tid]
        )} if tid else {}

        # Re-read the CSV to process group/team assignments for created users
        reader2 = csv_mod.DictReader(io.StringIO(content))
        for row in reader2:
            email = (row.get("email") or row.get("Email") or "").strip().lower()
            if not email:
                continue
            groups_csv = (row.get("groups") or row.get("Groups") or "").strip()
            teams_csv = (row.get("teams") or row.get("Teams") or "").strip()
            if not groups_csv and not teams_csv:
                continue

            user_row = fetch_one(
                "SELECT id FROM users WHERE LOWER(email) = %s AND tenant_id = %s LIMIT 1",
                [email, tid],
            )
            if not user_row:
                continue

            uid = user_row["id"]
            if groups_csv:
                for gname in [g.strip() for g in groups_csv.split(",") if g.strip()]:
                    gid = all_groups.get(gname.lower())
                    if gid:
                        execute(
                            "INSERT INTO user_group_memberships (user_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            [uid, gid],
                        )
            if teams_csv:
                for tname in [t.strip() for t in teams_csv.split(",") if t.strip()]:
                    tid_team = all_teams.get(tname.lower())
                    if tid_team:
                        execute(
                            "INSERT INTO team_members (team_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            [tid_team, uid],
                        )

    return jsonify({"created": created, "skipped": skipped, "errors": errors})


@admin_bp.route("/users/export", methods=["GET"])
@require_permission("users.manage")
def export_users():
    """Export tenant users as CSV including groups and teams."""
    import csv as csv_mod
    import io

    from routes.auth import get_tenant_id
    current = get_current_user()
    tenant_id = request.args.get("tenant_id") or get_tenant_id()

    if current["role"] == "tenant_admin":
        tenant_id = current.get("tenant_id")

    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    users = fetch_all(
        """SELECT id, first_name, last_name, email, phone, role
           FROM users WHERE tenant_id = %s ORDER BY name""",
        [tenant_id],
    )

    # Batch-fetch group and team memberships for all users
    user_ids = [u["id"] for u in users]
    group_rows = fetch_all(
        """SELECT ugm.user_id, g.name
           FROM user_group_memberships ugm
           JOIN groups g ON g.id = ugm.group_id AND g.is_active = true
           WHERE ugm.user_id = ANY(%s) ORDER BY g.name""",
        [user_ids],
    ) if user_ids else []

    team_rows = fetch_all(
        """SELECT tm.user_id, t.name
           FROM team_members tm
           JOIN teams t ON t.id = tm.team_id AND t.is_active = true
           WHERE tm.user_id = ANY(%s) ORDER BY t.name""",
        [user_ids],
    ) if user_ids else []

    location_rows = fetch_all(
        """SELECT ul.user_id, l.name
           FROM user_locations ul
           JOIN locations l ON l.id = ul.location_id AND l.is_active = true
           WHERE ul.user_id = ANY(%s) ORDER BY l.name""",
        [user_ids],
    ) if user_ids else []

    # Build lookups: user_id -> comma-separated names
    from collections import defaultdict
    user_groups: dict[int, list[str]] = defaultdict(list)
    for r in group_rows:
        user_groups[r["user_id"]].append(r["name"])

    user_teams: dict[int, list[str]] = defaultdict(list)
    for r in team_rows:
        user_teams[r["user_id"]].append(r["name"])

    user_locations: dict[int, list[str]] = defaultdict(list)
    for r in location_rows:
        user_locations[r["user_id"]].append(r["name"])

    # Write CSV
    output = io.StringIO()
    writer = csv_mod.writer(output)
    writer.writerow(["first_name", "last_name", "email", "phone", "role", "groups", "teams", "locations"])
    for u in users:
        writer.writerow([
            u["first_name"] or "",
            u["last_name"] or "",
            u["email"],
            u["phone"] or "",
            u["role"],
            ", ".join(user_groups.get(u["id"], [])),
            ", ".join(user_teams.get(u["id"], [])),
            ", ".join(user_locations.get(u["id"], [])),
        ])

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"},
    )


@admin_bp.route("/users/<int:user_id>/resend-invite", methods=["POST"])
@require_permission("users.invite")
@limiter.limit("5 per minute")
def resend_invite(user_id: int):
    """Resend an invite — resets invited_at, extends expires_at by 30 days."""
    current = get_current_user()
    target = fetch_one(
        "SELECT id, email, name, role, invite_status, tenant_id, expires_at FROM users WHERE id = %s",
        [user_id],
    )
    if not target:
        return jsonify({"error": "User not found"}), 404
    if current["role"] == "tenant_admin" and target["tenant_id"] != current.get("tenant_id"):
        return jsonify({"error": "Forbidden"}), 403
    if target["invite_status"] != "invited":
        return jsonify({"error": "User is not in invited state"}), 400

    # Reset invited_at, extend expires_at by 30 days from now (if original had one)
    new_expires_str = None
    if target.get("expires_at"):
        execute(
            "UPDATE users SET invited_at = now(), expires_at = now() + interval '30 days' WHERE id = %s",
            [user_id],
        )
        # Fetch the new expires_at for the email
        updated = fetch_one("SELECT expires_at FROM users WHERE id = %s", [user_id])
        new_expires_str = str(updated["expires_at"]) if updated else None
    else:
        execute("UPDATE users SET invited_at = now() WHERE id = %s", [user_id])

    # Resend invite email (background thread)
    from services.email_service import send_invite_email
    send_invite_email(
        user_id=target["id"], user_email=target["email"],
        user_name=target["name"] or target["email"],
        role=target["role"], tenant_id=target["tenant_id"],
        expires_at=new_expires_str or str(target.get("expires_at", "")),
        inviter_id=current["id"],
    )

    return jsonify({"ok": True})


@admin_bp.route("/users/<int:user_id>", methods=["PUT"])
@require_permission("users.manage")
def update_user(user_id: int):
    current = get_current_user()
    data = request.json or {}

    # tenant_admin restrictions
    if current["role"] == "tenant_admin":
        target = fetch_one("SELECT tenant_id, role FROM users WHERE id = %s", [user_id])
        if not target or target["tenant_id"] != current.get("tenant_id"):
            return jsonify({"error": "Forbidden"}), 403
        if data.get("role") == "super_admin":
            return jsonify({"error": "Cannot assign super_admin role"}), 403
        data.pop("tenant_id", None)

    fields, params = [], []
    for col in ("name", "role", "is_active", "tenant_id", "first_name", "last_name", "phone", "invite_status"):
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])

    # Auto-compute name from first/last if provided (only if name not already in payload)
    if ("first_name" in data or "last_name" in data) and "name" not in data:
        fn = data.get("first_name", "")
        ln = data.get("last_name", "")
        if fn or ln:
            fields.append("name = %s")
            params.append(f"{fn} {ln}".strip())

    if not fields:
        return jsonify({"error": "No fields to update"}), 400
    params.append(user_id)
    execute(f"UPDATE users SET {', '.join(fields)} WHERE id = %s", params)
    return jsonify({"ok": True})


# ============================================================
# RBAC — Groups & Permissions
# ============================================================

@admin_bp.route("/permissions", methods=["GET"])
@require_permission("users.manage")
def list_permissions():
    """List all system-defined permission slugs (for UI matrix)."""
    from services.permission_service import get_all_permissions
    return jsonify(get_all_permissions())


@admin_bp.route("/groups", methods=["GET"])
@require_permission("users.manage")
def list_groups():
    """List groups for the current user's tenant (super_admin sees all)."""
    user = get_current_user()
    if user["role"] == "super_admin":
        tenant_id = request.args.get("tenant_id", type=int)
        if tenant_id:
            groups = fetch_all(
                """SELECT g.*, count(ugm.user_id) as member_count
                   FROM groups g
                   LEFT JOIN user_group_memberships ugm ON ugm.group_id = g.id
                   WHERE g.tenant_id = %s AND g.is_active = true
                   GROUP BY g.id ORDER BY g.name""",
                [tenant_id],
            )
        else:
            groups = fetch_all(
                """SELECT g.*, t.name as tenant_name, count(ugm.user_id) as member_count
                   FROM groups g
                   LEFT JOIN tenants t ON t.id = g.tenant_id
                   LEFT JOIN user_group_memberships ugm ON ugm.group_id = g.id
                   WHERE g.is_active = true
                   GROUP BY g.id, t.name ORDER BY t.name, g.name"""
            )
    else:
        groups = fetch_all(
            """SELECT g.*, count(ugm.user_id) as member_count
               FROM groups g
               LEFT JOIN user_group_memberships ugm ON ugm.group_id = g.id
               WHERE g.tenant_id = %s AND g.is_active = true
               GROUP BY g.id ORDER BY g.name""",
            [user["tenant_id"]],
        )
    return jsonify(groups)


@admin_bp.route("/groups", methods=["POST"])
@require_permission("users.manage")
def create_group():
    """Create a new group for a tenant."""
    user = get_current_user()
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    tenant_id = data.get("tenant_id") or user.get("tenant_id")
    if not tenant_id:
        return jsonify({"error": "tenant_id is required"}), 400
    if user["role"] != "super_admin" and user.get("tenant_id") != tenant_id:
        return jsonify({"error": "Forbidden"}), 403

    existing = fetch_one(
        "SELECT id FROM groups WHERE tenant_id = %s AND name = %s",
        [tenant_id, name],
    )
    if existing:
        return jsonify({"error": "Group name already exists"}), 409

    group_id = insert_returning(
        """INSERT INTO groups (tenant_id, name, description, is_default)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        [tenant_id, name, data.get("description", ""), data.get("is_default", False)],
    )
    return jsonify({"id": group_id}), 201


@admin_bp.route("/groups/<int:group_id>", methods=["PUT"])
@require_permission("users.manage")
def update_group(group_id: int):
    """Update group name/description/is_default."""
    user = get_current_user()
    group = fetch_one("SELECT tenant_id FROM groups WHERE id = %s", [group_id])
    if not group:
        return jsonify({"error": "Group not found"}), 404
    if user["role"] != "super_admin" and user.get("tenant_id") != group["tenant_id"]:
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    fields, params = [], []
    for col in ("name", "description", "is_default"):
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])
    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    params.append(group_id)
    execute(f"UPDATE groups SET {', '.join(fields)} WHERE id = %s", params)
    return jsonify({"ok": True})


@admin_bp.route("/groups/<int:group_id>", methods=["DELETE"])
@require_permission("users.manage")
def delete_group(group_id: int):
    """Soft-delete a group. Move orphaned users to default group."""
    user = get_current_user()
    group = fetch_one("SELECT tenant_id, is_default FROM groups WHERE id = %s", [group_id])
    if not group:
        return jsonify({"error": "Group not found"}), 404
    if user["role"] != "super_admin" and user.get("tenant_id") != group["tenant_id"]:
        return jsonify({"error": "Forbidden"}), 403
    if group["is_default"]:
        return jsonify({"error": "Cannot delete the default group"}), 400

    # Move members to default group
    default = fetch_one(
        "SELECT id FROM groups WHERE tenant_id = %s AND is_default = true AND is_active = true LIMIT 1",
        [group["tenant_id"]],
    )
    if default:
        # Get current members of this group
        members = fetch_all(
            "SELECT user_id FROM user_group_memberships WHERE group_id = %s", [group_id]
        )
        for m in members:
            # Add to default group if not already there
            execute(
                """INSERT INTO user_group_memberships (user_id, group_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                [m["user_id"], default["id"]],
            )

    # Remove memberships from deleted group
    execute("DELETE FROM user_group_memberships WHERE group_id = %s", [group_id])
    execute("DELETE FROM group_permissions WHERE group_id = %s", [group_id])
    execute("UPDATE groups SET is_active = false WHERE id = %s", [group_id])
    return jsonify({"ok": True})


@admin_bp.route("/groups/<int:group_id>/permissions", methods=["GET"])
@require_permission("users.manage")
def get_group_permissions(group_id: int):
    """Get permission slugs for a group."""
    perms = fetch_all(
        """SELECT p.id, p.slug, p.label, p.category
           FROM group_permissions gp
           JOIN permissions p ON p.id = gp.permission_id
           WHERE gp.group_id = %s
           ORDER BY p.category, p.slug""",
        [group_id],
    )
    return jsonify(perms)


@admin_bp.route("/groups/<int:group_id>/permissions", methods=["PUT"])
@require_permission("users.manage")
def set_group_permissions(group_id: int):
    """Replace all permissions for a group. Body: {"permission_ids": [1,2,3]}"""
    user = get_current_user()
    group = fetch_one("SELECT tenant_id FROM groups WHERE id = %s", [group_id])
    if not group:
        return jsonify({"error": "Group not found"}), 404
    if user["role"] != "super_admin" and user.get("tenant_id") != group["tenant_id"]:
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    perm_ids = data.get("permission_ids", [])

    # Full replace: delete all, re-insert
    execute("DELETE FROM group_permissions WHERE group_id = %s", [group_id])
    for pid in perm_ids:
        execute(
            "INSERT INTO group_permissions (group_id, permission_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [group_id, pid],
        )
    try:
        from services.audit_service import log_event, GROUP_PERMISSIONS_CHANGED, RT_GROUP
        actor = get_current_user()
        log_event(GROUP_PERMISSIONS_CHANGED, tenant_id=actor.get("tenant_id"), user_id=actor.get("id"), resource_type=RT_GROUP, resource_id=group_id, details={"permission_ids": perm_ids}, request=request)
    except Exception:
        pass
    return jsonify({"ok": True})


@admin_bp.route("/groups/<int:group_id>/members", methods=["GET"])
@require_permission("users.manage")
def get_group_members(group_id: int):
    """List members of a group."""
    members = fetch_all(
        """SELECT u.id, u.name, u.email, u.role, ugm.added_at
           FROM user_group_memberships ugm
           JOIN users u ON u.id = ugm.user_id
           WHERE ugm.group_id = %s
           ORDER BY u.name""",
        [group_id],
    )
    return jsonify(members)


@admin_bp.route("/groups/<int:group_id>/members", methods=["PUT"])
@require_permission("users.manage")
def set_group_members(group_id: int):
    """Replace all members for a group. Body: {"user_ids": [1,2,3]}"""
    user = get_current_user()
    group = fetch_one("SELECT tenant_id FROM groups WHERE id = %s", [group_id])
    if not group:
        return jsonify({"error": "Group not found"}), 404
    if user["role"] != "super_admin" and user.get("tenant_id") != group["tenant_id"]:
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    user_ids = data.get("user_ids", [])

    execute("DELETE FROM user_group_memberships WHERE group_id = %s", [group_id])
    for uid in user_ids:
        execute(
            "INSERT INTO user_group_memberships (user_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [uid, group_id],
        )
    return jsonify({"ok": True})


@admin_bp.route("/users/<int:user_id>/locations", methods=["GET"])
@require_permission("users.manage")
def get_user_locations(user_id: int):
    """List locations assigned to a user."""
    rows = fetch_all(
        """SELECT ul.location_id, l.name, l.parent_id
           FROM user_locations ul
           JOIN locations l ON l.id = ul.location_id
           WHERE ul.user_id = %s AND l.is_active = true
           ORDER BY l.name""",
        [user_id],
    )
    return jsonify(rows)


@admin_bp.route("/users/<int:user_id>/locations", methods=["PUT"])
@require_permission("users.manage")
def set_user_locations(user_id: int):
    """Replace all location assignments for a user."""
    current = get_current_user()
    data = request.json or {}
    location_ids = data.get("location_ids", [])

    target = fetch_one("SELECT id, tenant_id FROM users WHERE id = %s", [user_id])
    if not target:
        return jsonify({"error": "User not found"}), 404
    if current["role"] != "super_admin" and target["tenant_id"] != current.get("tenant_id"):
        return jsonify({"error": "Forbidden"}), 403

    tenant_id = target["tenant_id"]
    execute("DELETE FROM user_locations WHERE user_id = %s", [user_id])
    for loc_id in location_ids:
        execute(
            "INSERT INTO user_locations (tenant_id, user_id, location_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [tenant_id, user_id, loc_id],
        )
    return jsonify({"ok": True})


@admin_bp.route("/users/<int:user_id>/permissions", methods=["GET"])
@require_permission("users.manage")
def get_user_permissions(user_id: int):
    """Get user's effective permissions, groups, and overrides."""
    from services.permission_service import get_user_permissions as resolve_perms

    target = fetch_one("SELECT id, role, tenant_id FROM users WHERE id = %s", [user_id])
    if not target:
        return jsonify({"error": "User not found"}), 404

    user = get_current_user()
    if user["role"] != "super_admin" and user.get("tenant_id") != target["tenant_id"]:
        return jsonify({"error": "Forbidden"}), 403

    effective = resolve_perms(user_id, target["role"])

    # Get group memberships
    groups = fetch_all(
        """SELECT g.id, g.name FROM user_group_memberships ugm
           JOIN groups g ON g.id = ugm.group_id
           WHERE ugm.user_id = %s AND g.is_active = true
           ORDER BY g.name""",
        [user_id],
    )

    # Get overrides
    overrides = fetch_all(
        """SELECT p.slug, p.label, upo.granted, upo.reason
           FROM user_permission_overrides upo
           JOIN permissions p ON p.id = upo.permission_id
           WHERE upo.user_id = %s""",
        [user_id],
    )

    return jsonify({
        "user_id": user_id,
        "role": target["role"],
        "effective_permissions": effective,
        "groups": groups,
        "overrides": overrides,
    })


@admin_bp.route("/users/<int:user_id>/permissions/overrides", methods=["PUT"])
@require_permission("users.manage")
def set_user_permission_overrides(user_id: int):
    """Set user permission overrides. Body: {"overrides": [{"permission_id": 1, "granted": true, "reason": "..."}]}"""
    user = get_current_user()
    target = fetch_one("SELECT tenant_id FROM users WHERE id = %s", [user_id])
    if not target:
        return jsonify({"error": "User not found"}), 404
    if user["role"] != "super_admin" and user.get("tenant_id") != target["tenant_id"]:
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    overrides = data.get("overrides", [])

    # Full replace
    execute("DELETE FROM user_permission_overrides WHERE user_id = %s", [user_id])
    for ov in overrides:
        pid = ov.get("permission_id")
        granted = ov.get("granted", True)
        reason = ov.get("reason", "")
        if pid is not None:
            execute(
                """INSERT INTO user_permission_overrides (user_id, permission_id, granted, reason, set_by)
                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id, permission_id)
                   DO UPDATE SET granted = EXCLUDED.granted, reason = EXCLUDED.reason,
                                 set_by = EXCLUDED.set_by, set_at = now()""",
                [user_id, pid, granted, reason, user["id"]],
            )
    try:
        from services.audit_service import log_event, PERMISSION_CHANGED, RT_USER
        actor = get_current_user()
        log_event(PERMISSION_CHANGED, tenant_id=actor.get("tenant_id"), user_id=actor.get("id"), resource_type=RT_USER, resource_id=user_id, details={"changes": data}, request=request)
    except Exception:
        pass
    return jsonify({"ok": True})


@admin_bp.route("/users/<int:user_id>/groups", methods=["GET"])
@require_permission("users.manage")
def get_user_groups(user_id: int):
    """Get groups the user belongs to."""
    user = get_current_user()
    target = fetch_one("SELECT tenant_id FROM users WHERE id = %s", [user_id])
    if not target:
        return jsonify({"error": "User not found"}), 404
    if user["role"] != "super_admin" and user.get("tenant_id") != target["tenant_id"]:
        return jsonify({"error": "Forbidden"}), 403

    groups = fetch_all(
        """SELECT g.id, g.name FROM user_group_memberships ugm
           JOIN groups g ON g.id = ugm.group_id
           WHERE ugm.user_id = %s AND g.is_active = true
           ORDER BY g.name""",
        [user_id],
    )
    return jsonify(groups)


@admin_bp.route("/users/<int:user_id>/groups", methods=["PUT"])
@require_permission("users.manage")
def set_user_groups(user_id: int):
    """Set which groups a user belongs to. Body: {"group_ids": [1,2,3]}"""
    user = get_current_user()
    target = fetch_one("SELECT tenant_id FROM users WHERE id = %s", [user_id])
    if not target:
        return jsonify({"error": "User not found"}), 404
    if user["role"] != "super_admin" and user.get("tenant_id") != target["tenant_id"]:
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    group_ids = data.get("group_ids", [])

    # Verify all groups belong to user's tenant
    if group_ids:
        valid = fetch_all(
            "SELECT id FROM groups WHERE id = ANY(%s) AND tenant_id = %s AND is_active = true",
            [group_ids, target["tenant_id"]],
        )
        valid_ids = {g["id"] for g in valid}
        for gid in group_ids:
            if gid not in valid_ids:
                return jsonify({"error": f"Group {gid} not found or not in user's tenant"}), 400

    execute("DELETE FROM user_group_memberships WHERE user_id = %s", [user_id])
    for gid in group_ids:
        execute(
            "INSERT INTO user_group_memberships (user_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [user_id, gid],
        )
    return jsonify({"ok": True})


@admin_bp.route("/users/<int:user_id>/teams", methods=["GET"])
@require_permission("users.manage")
def get_user_teams(user_id: int):
    """Get teams the user belongs to."""
    user = get_current_user()
    target = fetch_one("SELECT tenant_id FROM users WHERE id = %s", [user_id])
    if not target:
        return jsonify({"error": "User not found"}), 404
    if user["role"] != "super_admin" and user.get("tenant_id") != target["tenant_id"]:
        return jsonify({"error": "Forbidden"}), 403

    teams = fetch_all(
        """SELECT t.id, t.name, tm.role FROM team_members tm
           JOIN teams t ON t.id = tm.team_id
           WHERE tm.user_id = %s AND t.tenant_id = %s
           ORDER BY t.name""",
        [user_id, target["tenant_id"]],
    )
    return jsonify(teams)


@admin_bp.route("/users/<int:user_id>/teams", methods=["PUT"])
@require_permission("users.manage")
def set_user_teams(user_id: int):
    """Set which teams a user belongs to. Body: {"team_ids": [1,2,3]}"""
    user = get_current_user()
    target = fetch_one("SELECT tenant_id FROM users WHERE id = %s", [user_id])
    if not target:
        return jsonify({"error": "User not found"}), 404
    if user["role"] != "super_admin" and user.get("tenant_id") != target["tenant_id"]:
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    team_ids = data.get("team_ids", [])

    if team_ids:
        valid = fetch_all(
            "SELECT id FROM teams WHERE id = ANY(%s) AND tenant_id = %s",
            [team_ids, target["tenant_id"]],
        )
        valid_ids = {t["id"] for t in valid}
        for tid in team_ids:
            if tid not in valid_ids:
                return jsonify({"error": f"Team {tid} not found"}), 400

    execute("DELETE FROM team_members WHERE user_id = %s", [user_id])
    for tid in team_ids:
        execute(
            "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, 'member') ON CONFLICT DO NOTHING",
            [tid, user_id, ],
        )
    return jsonify({"ok": True})


@admin_bp.route("/permissions/matrix", methods=["GET"])
@require_permission("users.manage")
def get_permission_matrix():
    """Return all groups, permissions, and group→permission mapping in one payload.

    Eliminates N+1 queries from the UI permission matrix view.
    Tenant-scoped: non-super_admin sees only their tenant's groups.
    Super_admin: optionally filter by ?tenant_id=X, otherwise sees all groups.
    """
    user = get_current_user()

    # Fetch groups (tenant-scoped)
    if user["role"] == "super_admin":
        tenant_id = request.args.get("tenant_id", type=int)
        if tenant_id:
            groups = fetch_all(
                """SELECT g.id, g.name, g.is_default, count(ugm.user_id) as member_count
                   FROM groups g
                   LEFT JOIN user_group_memberships ugm ON ugm.group_id = g.id
                   WHERE g.tenant_id = %s AND g.is_active = true
                   GROUP BY g.id ORDER BY g.name""",
                [tenant_id],
            )
        else:
            groups = fetch_all(
                """SELECT g.id, g.name, g.is_default, g.tenant_id,
                          t.name as tenant_name, count(ugm.user_id) as member_count
                   FROM groups g
                   LEFT JOIN tenants t ON t.id = g.tenant_id
                   LEFT JOIN user_group_memberships ugm ON ugm.group_id = g.id
                   WHERE g.is_active = true
                   GROUP BY g.id, t.name ORDER BY t.name, g.name"""
            )
    else:
        groups = fetch_all(
            """SELECT g.id, g.name, g.is_default, count(ugm.user_id) as member_count
               FROM groups g
               LEFT JOIN user_group_memberships ugm ON ugm.group_id = g.id
               WHERE g.tenant_id = %s AND g.is_active = true
               GROUP BY g.id ORDER BY g.name""",
            [user["tenant_id"]],
        )

    # Fetch all permissions
    permissions = fetch_all(
        "SELECT id, slug, label, category, description FROM permissions ORDER BY category, slug"
    )

    # Build group_id → [permission_id, ...] matrix
    group_ids = [g["id"] for g in groups]
    matrix: dict[str, list[int]] = {str(gid): [] for gid in group_ids}

    if group_ids:
        gp_rows = fetch_all(
            """SELECT group_id, permission_id FROM group_permissions
               WHERE group_id = ANY(%s)
               ORDER BY group_id, permission_id""",
            [group_ids],
        )
        for row in gp_rows:
            key = str(row["group_id"])
            if key in matrix:
                matrix[key].append(row["permission_id"])

    return jsonify({
        "groups": groups,
        "permissions": permissions,
        "matrix": matrix,
    })


@admin_bp.route("/permissions/matrix", methods=["PUT"])
@require_permission("users.manage")
def save_permission_matrix():
    """Batch-save permissions for multiple groups at once.

    Body: {"groups": {"1": [1, 2, 11], "2": [1, 2, 3, 4, 7, 11]}}
    Each entry is group_id → full list of permission_ids (full replace per group).
    Wrapped in a single transaction for atomicity.
    """
    user = get_current_user()
    data = request.json or {}
    groups_data = data.get("groups")
    if not groups_data or not isinstance(groups_data, dict):
        return jsonify({"error": "groups dict is required"}), 400

    group_ids = []
    for gid_str in groups_data:
        try:
            group_ids.append(int(gid_str))
        except (ValueError, TypeError):
            return jsonify({"error": f"Invalid group id: {gid_str}"}), 400

    # Verify all groups exist and belong to the user's tenant
    if group_ids:
        owned_groups = fetch_all(
            "SELECT id, tenant_id FROM groups WHERE id = ANY(%s) AND is_active = true",
            [group_ids],
        )
    else:
        return jsonify({"error": "No groups provided"}), 400

    owned_map = {g["id"]: g["tenant_id"] for g in owned_groups}
    for gid in group_ids:
        if gid not in owned_map:
            return jsonify({"error": f"Group {gid} not found"}), 404
        if user["role"] != "super_admin" and owned_map[gid] != user.get("tenant_id"):
            return jsonify({"error": "Forbidden"}), 403

    # Validate all permission_ids are lists of ints
    for gid_str, perm_ids in groups_data.items():
        if not isinstance(perm_ids, list):
            return jsonify({"error": f"Permission list for group {gid_str} must be an array"}), 400
        for pid in perm_ids:
            if not isinstance(pid, int):
                return jsonify({"error": f"Invalid permission id in group {gid_str}: {pid}"}), 400

    # Single transaction: delete + re-insert for each group
    updated = 0
    with cursor() as cur:
        for gid_str, perm_ids in groups_data.items():
            gid = int(gid_str)
            cur.execute("DELETE FROM group_permissions WHERE group_id = %s", [gid])
            for pid in perm_ids:
                cur.execute(
                    "INSERT INTO group_permissions (group_id, permission_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    [gid, pid],
                )
            updated += 1

    return jsonify({"ok": True, "updated": updated})


# ============================================================
# Routing Insights — Category coverage + agent specializations
# ============================================================

@admin_bp.route("/routing-insights", methods=["GET"])
@require_permission("metrics.view")
def get_routing_insights():
    """Return routing insights: category coverage, agent specializations, coverage gaps.

    Pure SQL aggregation — no LLM needed.
    """
    from routes.auth import get_tenant_id
    user = get_current_user()
    tenant_id = request.args.get("tenant_id") or get_tenant_id()

    if user["role"] != "super_admin" and str(tenant_id) != str(get_tenant_id()):
        return jsonify({"error": "Access denied"}), 403

    if not tenant_id:
        return jsonify({"error": "tenant_id required"}), 400

    # Category coverage: for each category, how many agents have experience?
    category_coverage = fetch_all(
        """SELECT pc.id as category_id, pc.name as category_name,
                  count(DISTINCT t.assignee_id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')
                      AND t.created_at > now() - interval '90 days') as agents_with_experience,
                  count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')
                      AND t.created_at > now() - interval '90 days') as total_resolved,
                  count(t.id) FILTER (WHERE t.status IN ('open', 'pending')) as open_tickets
           FROM problem_categories pc
           LEFT JOIN tickets t ON t.problem_category_id = pc.id AND t.tenant_id = %s
           WHERE pc.tenant_id = %s AND pc.is_active = true
           GROUP BY pc.id, pc.name
           ORDER BY pc.name""",
        [tenant_id, tenant_id],
    )

    # Agent specializations: for each active agent, stats + top categories
    agent_specializations = fetch_all(
        """SELECT u.id as agent_id, u.name as agent_name,
                  count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')
                      AND t.created_at > now() - interval '90 days') as total_resolved,
                  count(t.id) FILTER (WHERE t.status IN ('open', 'pending')) as open_tickets,
                  coalesce(avg(tm.effort_score) FILTER (WHERE tm.effort_score IS NOT NULL), 0) as avg_effort
           FROM users u
           LEFT JOIN tickets t ON t.assignee_id = u.id AND t.tenant_id = %s
           LEFT JOIN ticket_metrics tm ON tm.ticket_id = t.id
           WHERE u.tenant_id = %s AND u.role IN ('agent', 'tenant_admin') AND u.is_active = true
           GROUP BY u.id, u.name
           ORDER BY total_resolved DESC""",
        [tenant_id, tenant_id],
    )

    # For each agent, get top 5 categories
    for agent in (agent_specializations or []):
        top_cats = fetch_all(
            """SELECT pc.name as category, count(*) as count
               FROM tickets t
               JOIN problem_categories pc ON pc.id = t.problem_category_id
               WHERE t.assignee_id = %s AND t.tenant_id = %s
                 AND t.status IN ('resolved', 'closed_not_resolved')
                 AND t.created_at > now() - interval '90 days'
               GROUP BY pc.name
               ORDER BY count DESC
               LIMIT 5""",
            [agent["agent_id"], tenant_id],
        )
        agent["top_categories"] = top_cats or []

    # Coverage gaps: categories with <=1 experienced agents
    coverage_gaps = [
        c for c in (category_coverage or [])
        if c.get("agents_with_experience", 0) <= 1
    ]

    return jsonify({
        "category_coverage": category_coverage or [],
        "agent_specializations": agent_specializations or [],
        "coverage_gaps": coverage_gaps,
    })


# ============================================================
# Teams — work units within a tenant
# ============================================================

@admin_bp.route("/teams", methods=["GET"])
@require_permission("teams.manage")
def list_teams():
    """List all teams for the current tenant."""
    from routes.auth import get_tenant_id
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify([])

    teams = fetch_all(
        """SELECT t.id, t.name, t.slug, t.description, t.is_active, t.created_at,
                  t.lead_id, u.name as lead_name,
                  (SELECT COUNT(*) FROM team_members tm WHERE tm.team_id = t.id) as member_count,
                  (SELECT COUNT(*) FROM tickets tk WHERE tk.team_id = t.id AND tk.status IN ('open', 'pending')) as open_ticket_count
           FROM teams t
           LEFT JOIN users u ON u.id = t.lead_id
           WHERE t.tenant_id = %s
           ORDER BY t.name""",
        [tenant_id],
    )
    return jsonify(teams)


@admin_bp.route("/teams", methods=["POST"])
@require_permission("teams.manage")
def create_team():
    """Create a new team."""
    from routes.auth import get_tenant_id
    user = get_current_user()
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Team name is required"}), 400
    description = data.get("description", "").strip()
    if not description:
        return jsonify({"error": "Team description is required (used by Atlas for auto-routing)"}), 400

    slug = _slugify(name)
    if not slug:
        slug = "team"

    existing = fetch_one(
        "SELECT id FROM teams WHERE tenant_id = %s AND slug = %s",
        [tenant_id, slug],
    )
    if existing:
        return jsonify({"error": f"Team '{name}' already exists"}), 409

    team_id = insert_returning(
        """INSERT INTO teams (tenant_id, name, slug, description, lead_id)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        [tenant_id, name, slug, data.get("description", ""), data.get("lead_id")],
    )

    # If lead specified, auto-add them as member with 'lead' role
    if data.get("lead_id"):
        execute(
            """INSERT INTO team_members (team_id, user_id, role)
               VALUES (%s, %s, 'lead') ON CONFLICT (team_id, user_id) DO UPDATE SET role = 'lead'""",
            [team_id, data["lead_id"]],
        )

    return jsonify({"id": team_id, "name": name, "slug": slug}), 201


@admin_bp.route("/teams/<int:team_id>", methods=["PUT"])
@require_permission("teams.manage")
def update_team(team_id: int):
    """Update team name, description, lead, or active status."""
    from routes.auth import get_tenant_id
    tenant_id = get_tenant_id()

    existing = fetch_one(
        "SELECT id FROM teams WHERE id = %s AND tenant_id = %s",
        [team_id, tenant_id],
    )
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    fields, params = [], []
    for col in ("name", "description", "is_active"):
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])
    if "lead_id" in data:
        fields.append("lead_id = %s")
        params.append(data["lead_id"])
    if "name" in data:
        fields.append("slug = %s")
        params.append(_slugify(data["name"]))

    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    params.append(team_id)
    execute(f"UPDATE teams SET {', '.join(fields)} WHERE id = %s", params)
    return jsonify({"ok": True})


@admin_bp.route("/teams/<int:team_id>", methods=["DELETE"])
@require_permission("teams.manage")
def delete_team(team_id: int):
    """Delete a team. Tickets with this team_id get set to NULL."""
    from routes.auth import get_tenant_id
    tenant_id = get_tenant_id()

    existing = fetch_one(
        "SELECT id FROM teams WHERE id = %s AND tenant_id = %s",
        [team_id, tenant_id],
    )
    if not existing:
        return jsonify({"error": "Not found"}), 404

    execute("UPDATE tickets SET team_id = NULL WHERE team_id = %s", [team_id])
    execute("DELETE FROM teams WHERE id = %s", [team_id])
    return jsonify({"ok": True})


@admin_bp.route("/teams/<int:team_id>/members", methods=["GET"])
@require_permission("teams.manage")
def get_team_members(team_id: int):
    """List members of a team."""
    from routes.auth import get_tenant_id
    tenant_id = get_tenant_id()

    existing = fetch_one(
        "SELECT id FROM teams WHERE id = %s AND tenant_id = %s",
        [team_id, tenant_id],
    )
    if not existing:
        return jsonify({"error": "Not found"}), 404

    members = fetch_all(
        """SELECT tm.user_id, tm.role, tm.added_at, u.name, u.email, u.role as user_role
           FROM team_members tm
           JOIN users u ON u.id = tm.user_id
           WHERE tm.team_id = %s
           ORDER BY tm.role DESC, u.name""",
        [team_id],
    )
    return jsonify(members)


@admin_bp.route("/teams/<int:team_id>/members", methods=["PUT"])
@require_permission("teams.manage")
def update_team_members(team_id: int):
    """Replace team members. Body: { members: [{ user_id, role }] }"""
    from routes.auth import get_tenant_id
    tenant_id = get_tenant_id()

    existing = fetch_one(
        "SELECT id FROM teams WHERE id = %s AND tenant_id = %s",
        [team_id, tenant_id],
    )
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    members = data.get("members", [])

    execute("DELETE FROM team_members WHERE team_id = %s", [team_id])
    for m in members:
        user_id = m.get("user_id")
        role = m.get("role", "member")
        if user_id:
            execute(
                "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, %s)",
                [team_id, user_id, role],
            )

    return jsonify({"ok": True, "count": len(members)})


# ============================================================
# System Errors  (PLATFORM — super_admin only)
# ============================================================

@admin_bp.route("/system-errors", methods=["GET"])
@require_role("super_admin")
def list_system_errors():
    """List captured system errors.

    Query params:
      resolved  — 'true' | 'false' (omit for all)
      limit     — default 100
      offset    — default 0
    """
    from services.error_tracking_service import get_errors

    resolved_param = request.args.get("resolved")
    resolved = None
    if resolved_param == "true":
        resolved = True
    elif resolved_param == "false":
        resolved = False

    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))

    rows, total = get_errors(tenant_id=None, resolved=resolved, limit=limit, offset=offset)

    # Convert datetimes to ISO strings for JSON serialisation
    def _serial(row):
        out = dict(row)
        for k in ("occurred_at", "resolved_at"):
            if out.get(k) is not None:
                out[k] = out[k].isoformat()
        return out

    return jsonify({
        "errors": [_serial(r) for r in rows],
        "total": total,
    })


@admin_bp.route("/system-errors/<int:error_id>/resolve", methods=["PUT"])
@require_role("super_admin")
def resolve_system_error(error_id: int):
    """Mark a system error as resolved.

    Body (optional):
      notes  — admin note to attach
    """
    from services.error_tracking_service import resolve_error

    data = request.json or {}
    notes = data.get("notes") or None

    found = resolve_error(error_id, notes=notes)
    if not found:
        return jsonify({"error": "Not found"}), 404

    return jsonify({"ok": True})


@admin_bp.route("/system-errors/<int:error_id>", methods=["DELETE"])
@require_role("super_admin")
def delete_system_error(error_id: int):
    """Hard-delete a system error record (super_admin cleanup)."""
    rowcount = execute("DELETE FROM system_errors WHERE id = %s", [error_id])
    if not rowcount:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})
