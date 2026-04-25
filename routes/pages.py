"""Page routes: serves the SPA shell and login page."""

import json

from flask import Blueprint, render_template, session, redirect, url_for, abort, Response

from routes.auth import login_required, get_current_user, require_permission
from config import Config
from models.db import fetch_one

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def index():
    user = get_current_user()
    if not user:
        return render_template(
            "landing.html",
            microsoft_enabled=bool(Config.AZURE_CLIENT_ID),
            google_enabled=bool(Config.GOOGLE_CLIENT_ID),
        )
    slug = _get_tenant_slug(user.get("tenant_id"))
    if user.get("role") == "end_user":
        return redirect(f"/{slug}/portal" if slug else "/portal")
    # Redirect to slug-prefixed tickets
    if slug:
        return redirect(f"/{slug}/tickets")
    return _render_app("tickets", tenant_slug=slug)


@pages_bp.route("/robots.txt")
def robots_txt():
    return Response(
        "User-agent: *\nAllow: /\n",
        mimetype="text/plain",
    )


# --- Slug-prefixed staff routes (primary) ---

@pages_bp.route("/<tenant_slug>/tickets")
@pages_bp.route("/<tenant_slug>/tickets/<int:ticket_id>")
@login_required
def tenant_tickets(tenant_slug, ticket_id=None):
    _validate_tenant_slug(tenant_slug)
    return _render_app("tickets", ticket_id=ticket_id, tenant_slug=tenant_slug)


@pages_bp.route("/<tenant_slug>/kb")
@login_required
def tenant_kb(tenant_slug):
    _validate_tenant_slug(tenant_slug)
    return _render_app("kb", tenant_slug=tenant_slug)


@pages_bp.route("/<tenant_slug>/chat")
@login_required
def tenant_chat(tenant_slug):
    _validate_tenant_slug(tenant_slug)
    return _render_app("chat", tenant_slug=tenant_slug)


@pages_bp.route("/<tenant_slug>/audit")
@login_required
def tenant_audit(tenant_slug):
    _validate_tenant_slug(tenant_slug)
    from services.permission_service import has_permission
    if not has_permission("audit.view"):
        return redirect(f"/{tenant_slug}/tickets")
    return _render_app("audit", tenant_slug=tenant_slug)


@pages_bp.route("/<tenant_slug>/reports")
@pages_bp.route("/<tenant_slug>/reports/<report_id>")
@login_required
def tenant_reports(tenant_slug, report_id=None):
    _validate_tenant_slug(tenant_slug)
    from services.permission_service import has_permission
    if not has_permission("reports.view"):
        return redirect(f"/{tenant_slug}/tickets")
    return _render_app("reports", initial_report=report_id, tenant_slug=tenant_slug)


@pages_bp.route("/<tenant_slug>/automations")
@pages_bp.route("/<tenant_slug>/automations/<int:automation_id>")
@login_required
def tenant_automations(tenant_slug, automation_id=None):
    _validate_tenant_slug(tenant_slug)
    return _render_app("automations", tenant_slug=tenant_slug)


@pages_bp.route("/<tenant_slug>/sprints")
@login_required
def tenant_sprints(tenant_slug):
    _validate_tenant_slug(tenant_slug)
    return _render_app("sprints", tenant_slug=tenant_slug)


@pages_bp.route("/<tenant_slug>/admin")
@pages_bp.route("/<tenant_slug>/admin/<path:section>")
@login_required
def tenant_admin(tenant_slug, section=None):
    _validate_tenant_slug(tenant_slug)
    from services.permission_service import has_permission
    if not has_permission("users.manage") and not has_permission("categories.manage") and not has_permission("locations.manage"):
        return redirect(f"/{tenant_slug}/tickets")
    return _render_app("admin", section=section, tenant_slug=tenant_slug)


# --- Legacy non-slug routes (redirect to slug-prefixed) ---

@pages_bp.route("/tickets")
@pages_bp.route("/tickets/<int:ticket_id>")
@login_required
def tickets(ticket_id=None):
    slug = _get_tenant_slug((get_current_user() or {}).get("tenant_id"))
    if slug:
        qs = f"/{ticket_id}" if ticket_id else ""
        return redirect(f"/{slug}/tickets{qs}")
    return _render_app("tickets", ticket_id=ticket_id, tenant_slug=slug)


@pages_bp.route("/kb")
@login_required
def knowledge_base():
    slug = _get_tenant_slug((get_current_user() or {}).get("tenant_id"))
    if slug:
        return redirect(f"/{slug}/kb")
    return _render_app("kb", tenant_slug=slug)


@pages_bp.route("/chat")
@login_required
def chat():
    slug = _get_tenant_slug((get_current_user() or {}).get("tenant_id"))
    if slug:
        return redirect(f"/{slug}/chat")
    return _render_app("chat", tenant_slug=slug)


@pages_bp.route("/audit")
@login_required
def audit():
    slug = _get_tenant_slug((get_current_user() or {}).get("tenant_id"))
    if slug:
        return redirect(f"/{slug}/audit")
    from services.permission_service import has_permission
    if not has_permission("audit.view"):
        return redirect("/")
    return _render_app("audit", tenant_slug=slug)


@pages_bp.route("/reports")
@pages_bp.route("/reports/<report_id>")
@login_required
def reports(report_id=None):
    slug = _get_tenant_slug((get_current_user() or {}).get("tenant_id"))
    if slug:
        return redirect(f"/{slug}/reports" + (f"/{report_id}" if report_id else ""))
    from services.permission_service import has_permission
    if not has_permission("reports.view"):
        return redirect("/")
    return _render_app("reports", initial_report=report_id, tenant_slug=slug)


@pages_bp.route("/automations")
@pages_bp.route("/automations/<int:automation_id>")
@login_required
def automations(automation_id=None):
    slug = _get_tenant_slug((get_current_user() or {}).get("tenant_id"))
    if slug:
        return redirect(f"/{slug}/automations" + (f"/{automation_id}" if automation_id else ""))
    return _render_app("automations", tenant_slug=slug)


@pages_bp.route("/sprints")
@login_required
def sprints():
    slug = _get_tenant_slug((get_current_user() or {}).get("tenant_id"))
    if slug:
        return redirect(f"/{slug}/sprints")
    return _render_app("sprints", tenant_slug=slug)


@pages_bp.route("/admin")
@pages_bp.route("/admin/<path:section>")
@login_required
def admin(section=None):
    slug = _get_tenant_slug((get_current_user() or {}).get("tenant_id"))
    if slug:
        return redirect(f"/{slug}/admin" + (f"/{section}" if section else ""))
    from services.permission_service import has_permission
    if not has_permission("users.manage") and not has_permission("categories.manage") and not has_permission("locations.manage"):
        return redirect("/")
    return _render_app("admin", section=section, tenant_slug=slug)


@pages_bp.route("/portal")
@pages_bp.route("/portal/<path:subpath>")
@login_required
def portal(subpath=None):
    user = get_current_user()
    # Redirect to tenant-scoped URL if possible
    slug = _get_tenant_slug(user.get("tenant_id") if user else None)
    if slug:
        return redirect(f"/{slug}/portal")
    return _render_app("portal")


@pages_bp.route("/<tenant_slug>/portal")
@pages_bp.route("/<tenant_slug>/portal/<path:subpath>")
def tenant_portal(tenant_slug, subpath=None):
    """Tenant-scoped portal: /<slug>/portal."""
    # Look up tenant by slug
    tenant = fetch_one(
        "SELECT id, name, slug, domain, settings FROM tenants WHERE slug = %s AND is_active = true",
        [tenant_slug],
    )
    if not tenant:
        abort(404)

    # Auth check (manual — can't use @login_required because we need custom redirect)
    if Config.AUTH_ENABLED and "user" not in session:
        return redirect(url_for("auth.login", next=f"/{tenant_slug}/portal"))

    user = get_current_user()
    if not user:
        return redirect(url_for("auth.login", next=f"/{tenant_slug}/portal"))

    # Validate tenant match (super_admin can access any portal)
    if user.get("role") != "super_admin" and user.get("tenant_id") != tenant["id"]:
        abort(403)

    return _render_app("portal", tenant_slug=tenant_slug)


def _validate_tenant_slug(tenant_slug: str):
    """Validate tenant slug matches current user's tenant (or user is super_admin)."""
    user = get_current_user()
    if not user:
        abort(403)
    if user.get("role") == "super_admin":
        return  # super_admin can access any tenant's slug routes
    user_slug = _get_tenant_slug(user.get("tenant_id"))
    if user_slug != tenant_slug:
        abort(404)  # don't reveal existence — just 404


def _get_tenant_slug(tenant_id: int | None) -> str | None:
    """Look up tenant slug from tenant_id."""
    if not tenant_id:
        return None
    tenant = fetch_one("SELECT slug FROM tenants WHERE id = %s", [tenant_id])
    return tenant["slug"] if tenant else None


def _has_byok_keys(tenant_id) -> bool:
    """Check if the tenant has at least one BYOK key configured."""
    if not tenant_id:
        return False
    try:
        from services.billing_service import get_byok_keys
        keys = get_byok_keys(tenant_id)
        if not keys:
            return False
        return bool(keys.get("anthropic") or keys.get("openai") or keys.get("voyage"))
    except Exception:
        return False


def _render_app(mode: str, **kwargs):
    """Render the React SPA shell with injected config."""
    user = get_current_user() or {
        "id": 0, "name": "Dev User", "email": "dev@localhost",
        "role": "super_admin", "tenant_id": None,
    }

    # Load tenant settings for problem_field_label, location_levels, etc.
    tenant_settings = {}
    tenant_name = ""
    tenant_logo_url = ""
    tenant = None
    ai_chat_enabled = False
    ai_features = {}
    if user.get("tenant_id"):
        tenant = fetch_one("SELECT name, logo_url, settings, ticket_prefix, email_from_name, plan_expires_at FROM tenants WHERE id = %s", [user["tenant_id"]])
        if tenant:
            tenant_name = tenant.get("name") or ""
            tenant_logo_url = tenant.get("logo_url") or ""
            if tenant.get("settings"):
                tenant_settings = tenant["settings"] if isinstance(tenant["settings"], dict) else {}
                # Strip admin-only config before exposing to frontend
                tenant_settings = {k: v for k, v in tenant_settings.items() if k != "allowed_domains"}

        # Check if AI module is enabled for this tenant (supports both old 'ai_chat' and new 'ai' slug)
        from models.db import fetch_all
        chat_module = fetch_one(
            """SELECT 1 FROM tenant_modules tm
               JOIN knowledge_modules km ON km.id = tm.module_id
               WHERE tm.tenant_id = %s AND km.slug IN ('ai_chat', 'ai')""",
            [user["tenant_id"]],
        )
        ai_chat_enabled = chat_module is not None

        # Load AI sub-feature toggles
        # Default: agent_chat and client_chat are on, ticket_review and phone_service are off
        if ai_chat_enabled:
            features = fetch_all(
                """SELECT mf.slug,
                          COALESCE(tmf.enabled,
                              CASE WHEN mf.slug IN ('agent_chat', 'client_chat') THEN true ELSE false END
                          ) as enabled
                   FROM module_features mf
                   JOIN knowledge_modules km ON km.id = mf.module_id
                   LEFT JOIN tenant_module_features tmf
                       ON tmf.feature_id = mf.id AND tmf.tenant_id = %s
                   WHERE km.slug IN ('ai_chat', 'ai') AND mf.is_active = true""",
                [user["tenant_id"]],
            )
            ai_features = {f["slug"]: f["enabled"] for f in features}

    app_config = {
        "mode": mode,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "tenant_id": user["tenant_id"],
            "permissions": user.get("permissions", []),
        },
        "app_name": tenant_settings.get("app_name") or Config.APP_NAME,
        "app_url": tenant_settings.get("app_url") or Config.APP_URL,
        "tenant_name": tenant_name,
        "tenant_logo_url": tenant_settings.get("logo_url") or tenant_logo_url,
        "tenant_settings": tenant_settings,
        "ticket_prefix": (tenant.get("ticket_prefix") if tenant else None) or "TKT",
        "email_from_name": (tenant.get("email_from_name") if tenant else None) or "",
        "idle_timeout_minutes": tenant_settings.get("idle_timeout_minutes") or Config.IDLE_TIMEOUT_MINUTES,
        "csrf_token": session.get("csrf_token", ""),
        "ai_chat_enabled": ai_chat_enabled,
        "ai_features": ai_features,
        "tenant_creation_enabled": Config.TENANT_CREATION_ENABLED,
        "demo_mode": Config.DEMO_MODE,
        "trial_expires_at": tenant.get("plan_expires_at").isoformat() if tenant and tenant.get("plan_expires_at") else None,
        "byok_configured": _has_byok_keys(user.get("tenant_id")),
        **kwargs,
    }
    return render_template("app_shell.html", app_config=json.dumps(app_config))
