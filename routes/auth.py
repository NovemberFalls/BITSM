"""Authentication blueprint: Microsoft 365 (MSAL) + Google OAuth."""

import functools
import logging
import re
import secrets

import msal
import requests as http_requests
from flask import Blueprint, redirect, request, session, url_for, jsonify, render_template
from werkzeug.security import generate_password_hash

from app import limiter
from config import Config
from models.db import fetch_one, insert_returning, execute as db_execute

logger = logging.getLogger(__name__)
auth_bp = Blueprint("auth", __name__)


# ============================================================
# Decorators
# ============================================================

def login_required(f):
    """Redirect to login if not authenticated.

    API routes (/api/*) and the /ping keepalive get a 401 JSON response
    so fetch()-based callers can detect auth failure instead of silently
    following a 302 redirect to the login page.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not Config.AUTH_ENABLED:
            return f(*args, **kwargs)
        if "user" not in session:
            if request.path.startswith("/api/") or request.path == "/ping":
                return jsonify({"error": "Not authenticated", "code": "session_expired"}), 401
            next_url = request.path
            return redirect(url_for("auth.login", next=next_url))
        return f(*args, **kwargs)
    return wrapper


def require_role(*roles):
    """Require the logged-in user to have one of the specified roles.

    DEPRECATED: Use @require_permission() for new code.
    Kept for backward compat during RBAC migration.
    """
    def decorator(f):
        @functools.wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            user = session.get("user", {})
            if user.get("role") not in roles:
                return jsonify({"error": "Forbidden"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


def require_permission(*slugs, any_of=False):
    """Require the logged-in user to have permission slug(s).

    any_of=True:  user needs at least ONE of the listed slugs.
    any_of=False: user needs ALL listed slugs (default).
    super_admin always passes.
    """
    def decorator(f):
        @functools.wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            user = session.get("user", {})
            # super_admin bypasses all permission checks
            if user.get("role") == "super_admin":
                return f(*args, **kwargs)

            user_perms = set(user.get("permissions") or [])

            if any_of:
                if not user_perms.intersection(slugs):
                    return jsonify({"error": f"Missing permission: one of {', '.join(slugs)}"}), 403
            else:
                missing = set(slugs) - user_perms
                if missing:
                    return jsonify({"error": f"Missing permission: {', '.join(sorted(missing))}"}), 403

            return f(*args, **kwargs)
        return wrapper
    return decorator


def get_current_user() -> dict | None:
    """Return current user from session (or dev fallback)."""
    user = session.get("user")
    if user:
        return user

    # Dev mode fallback: auto-provision or load dev user from DB
    if not Config.AUTH_ENABLED:
        return _get_or_create_dev_user()

    return None


def get_tenant_id() -> int | None:
    """Return current user's tenant_id from session."""
    user = get_current_user()
    return user.get("tenant_id") if user else None


def _get_or_create_dev_user() -> dict:
    """Ensure a dev user and tenant exist in the DB for local development."""
    try:
        user = fetch_one(
            "SELECT id, tenant_id, email, name, role FROM users WHERE email = 'dev@localhost' LIMIT 1"
        )
        if user:
            from services.permission_service import enrich_session_permissions
            dev_user = enrich_session_permissions({
                "id": user["id"], "tenant_id": user["tenant_id"],
                "email": user["email"], "name": user["name"], "role": user["role"],
            })
            session["user"] = dev_user
            return dev_user

        # Create dev tenant + user on first access
        tenant_id = insert_returning(
            """INSERT INTO tenants (name, slug, settings)
               VALUES ('Dev Tenant', 'dev', '{}')
               ON CONFLICT (slug) DO UPDATE SET name = 'Dev Tenant'
               RETURNING id""",
            [],
        )

        user_id = insert_returning(
            """INSERT INTO users (tenant_id, email, name, role, provider, created_via)
               VALUES (%s, 'dev@localhost', 'Dev User', 'super_admin', 'dev', 'dev_auto')
               RETURNING id""",
            [tenant_id],
        )

        from services.permission_service import enrich_session_permissions
        dev_user = enrich_session_permissions({
            "id": user_id, "tenant_id": tenant_id,
            "email": "dev@localhost", "name": "Dev User", "role": "super_admin",
        })
        session["user"] = dev_user
        return dev_user

    except Exception as e:
        logger.warning("Dev user setup failed (DB unavailable?): %s", e)
        return {"id": 0, "tenant_id": None, "email": "dev@localhost", "name": "Dev User", "role": "super_admin"}


# ============================================================
# Login page
# ============================================================

@auth_bp.route("/login")
def login():
    if "user" in session:
        next_url = request.args.get("next", "/")
        return redirect(next_url if next_url.startswith("/") else "/")
    # Store return URL for post-OAuth redirect
    next_url = request.args.get("next")
    if next_url and next_url.startswith("/"):
        session["login_next"] = next_url
    return render_template(
        "login.html",
        microsoft_enabled=_microsoft_configured(),
        google_enabled=_google_configured(),
    )


@auth_bp.route("/logout")
def logout():
    try:
        from services.audit_service import log_from_request, LOGOUT
        from flask import session
        user = session.get("user", {})
        log_from_request(LOGOUT, request, user)
    except Exception:
        pass
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/ping", methods=["POST"])
@login_required
def ping():
    """Reset idle timer. Called by frontend to keep session alive."""
    from datetime import datetime, timezone
    from flask import session, jsonify
    session["last_active"] = datetime.now(timezone.utc).isoformat()
    return jsonify({"ok": True})


# ============================================================
# GDPR Right to Erasure (Article 17)
# ============================================================

@auth_bp.route("/api/auth/erase-my-data", methods=["DELETE"])
@login_required
@limiter.limit("3 per day")
def erase_my_data():
    """Permanently anonymize all PII for the currently logged-in user.

    This is irreversible.  Only the authenticated user can erase their own
    data — no admin override, no acting on behalf of others.
    """
    user = get_current_user()
    if not user or not user.get("id"):
        return jsonify({"error": "Authentication required"}), 401

    user_id = user["id"]

    # Safety: prevent erasing the dev/placeholder user
    if user_id == 0:
        return jsonify({"error": "Cannot erase dev placeholder user"}), 400

    # Verify the user still exists and is active
    db_user = fetch_one(
        "SELECT id, is_active FROM users WHERE id = %s",
        [user_id],
    )
    if not db_user:
        return jsonify({"error": "User not found"}), 404
    if not db_user["is_active"]:
        return jsonify({"error": "Account already deactivated"}), 409

    try:
        from services.erasure_service import erase_user_data
        summary = erase_user_data(user_id)
    except Exception as e:
        logger.error("GDPR erasure failed for user_id=%s: %s", user_id, e)
        return jsonify({"error": "Erasure failed — please contact support"}), 500

    # Clear the session so the user is logged out immediately
    session.clear()

    return jsonify({
        "message": "Your data has been erased. You have been logged out.",
        "summary": summary,
    }), 200


# ============================================================
# Microsoft 365 OAuth
# ============================================================

def _microsoft_configured() -> bool:
    return bool(Config.AZURE_CLIENT_ID and Config.AZURE_CLIENT_SECRET)


def _build_msal_app(cache=None):
    return msal.ConfidentialClientApplication(
        Config.AZURE_CLIENT_ID,
        authority=Config.AZURE_AUTHORITY,
        client_credential=Config.AZURE_CLIENT_SECRET,
        token_cache=cache,
    )


@auth_bp.route("/auth/login/microsoft")
@limiter.limit("20 per minute")
def login_microsoft():
    if not _microsoft_configured():
        return "Microsoft auth not configured", 500
    app = _build_msal_app()
    redirect_uri = Config.APP_URL + Config.AZURE_REDIRECT_PATH
    flow = app.initiate_auth_code_flow(
        scopes=["User.Read"],
        redirect_uri=redirect_uri,
    )
    session["ms_auth_flow"] = flow
    return redirect(flow["auth_uri"])


@auth_bp.route(Config.AZURE_REDIRECT_PATH)
@limiter.limit("20 per minute")
def auth_callback_microsoft():
    flow = session.pop("ms_auth_flow", None)
    if not flow:
        return redirect(url_for("auth.login"))

    app = _build_msal_app()
    result = app.acquire_token_by_auth_code_flow(flow, request.args)
    if "error" in result:
        logger.warning("Microsoft auth error: %s", result.get("error_description"))
        try:
            from services.audit_service import log_event, LOGIN_FAILURE
            log_event(LOGIN_FAILURE, details={"provider": "microsoft", "error": result.get("error_description", result.get("error"))}, request=request)
        except Exception:
            pass
        return redirect(url_for("auth.login"))

    # Fetch user info
    token = result.get("access_token")
    graph = http_requests.get(
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    ).json()

    email = (graph.get("mail") or graph.get("userPrincipalName", "")).lower().strip()
    name = graph.get("displayName", email)

    if Config.ALLOWED_DOMAIN and not email.endswith(f"@{Config.ALLOWED_DOMAIN.lower()}"):
        return "Domain not allowed", 403

    # Extract MFA status from id_token claims (MSAL pre-decodes them)
    id_claims = result.get("id_token_claims", {})
    mfa_verified = "mfa" in (id_claims.get("amr") or [])

    _establish_session(email, name, "microsoft", mfa_verified=mfa_verified)
    next_url = session.pop("login_next", "/")
    return redirect(next_url if next_url.startswith("/") else "/")


# ============================================================
# Google OAuth
# ============================================================

def _google_configured() -> bool:
    return bool(Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET)


@auth_bp.route("/auth/login/google")
@limiter.limit("20 per minute")
def login_google():
    if not _google_configured():
        return "Google auth not configured", 500
    state = secrets.token_urlsafe(16)
    session["google_oauth_state"] = state
    redirect_uri = Config.APP_URL + Config.GOOGLE_REDIRECT_PATH
    params = {
        "client_id": Config.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + "&".join(
        f"{k}={v}" for k, v in params.items()
    )
    return redirect(auth_url)


@auth_bp.route(Config.GOOGLE_REDIRECT_PATH)
@limiter.limit("20 per minute")
def auth_callback_google():
    state = request.args.get("state", "")
    expected = session.pop("google_oauth_state", None)
    if not state or state != expected:
        logger.warning("Google OAuth state mismatch — possible CSRF")
        return redirect(url_for("auth.login"))

    code = request.args.get("code")
    if not code:
        return redirect(url_for("auth.login"))

    redirect_uri = Config.APP_URL + Config.GOOGLE_REDIRECT_PATH
    token_resp = http_requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    ).json()

    access_token = token_resp.get("access_token")
    if not access_token:
        logger.warning("Google auth error: %s", token_resp)
        try:
            from services.audit_service import log_event, LOGIN_FAILURE
            log_event(LOGIN_FAILURE, details={"provider": "google", "error": token_resp.get("error_description", token_resp.get("error", "no_access_token"))}, request=request)
        except Exception:
            pass
        return redirect(url_for("auth.login"))

    user_info = http_requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    ).json()

    email = user_info.get("email", "").lower().strip()
    name = user_info.get("name", email)

    if Config.ALLOWED_DOMAIN and not email.endswith(f"@{Config.ALLOWED_DOMAIN.lower()}"):
        return "Domain not allowed", 403

    # Extract MFA status from Google id_token (JWT payload, base64-decoded)
    mfa_verified = False
    id_token_raw = token_resp.get("id_token")
    if id_token_raw:
        try:
            import base64
            payload_b64 = id_token_raw.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)  # pad
            import json as _json
            claims = _json.loads(base64.urlsafe_b64decode(payload_b64))
            mfa_verified = "mfa" in (claims.get("amr") or [])
        except Exception:
            pass

    _establish_session(email, name, "google", mfa_verified=mfa_verified)
    next_url = session.pop("login_next", "/")
    return redirect(next_url if next_url.startswith("/") else "/")


# ============================================================
# Session establishment
# ============================================================

def _establish_session(email: str, name: str, provider: str, mfa_verified: bool = False):
    """Look up or create user in DB and populate session.

    Routing logic for new users:
    1. Existing user → use their current role/tenant
    2. Email in SUPER_ADMIN_EMAILS → super_admin, no tenant
    3. Domain matches a tenant → end_user in that tenant
    4. Non-personal domain, no tenant → auto-create tenant, user is tenant_admin
    5. Personal email domain → end_user, no tenant (admin assigns later)
    """
    from routes.admin import _slugify
    from services.sla_service import ensure_default_sla
    from services.tenant_setup import seed_notification_preferences, seed_default_groups

    email = email.lower().strip()

    # 1. Existing user? Use their current role/tenant
    #    Check active users first, then inactive (reactivate if found)
    user = fetch_one(
        "SELECT id, tenant_id, email, name, role, invite_status, expires_at, is_active FROM users WHERE LOWER(email) = %s AND is_active = true LIMIT 1",
        [email],
    )

    if not user:
        # Check for inactive user — reactivate them instead of creating a duplicate
        inactive_user = fetch_one(
            "SELECT id, tenant_id, email, name, role, invite_status, expires_at FROM users WHERE LOWER(email) = %s AND is_active = false LIMIT 1",
            [email],
        )
        if inactive_user:
            from models.db import execute as db_exec
            db_exec("UPDATE users SET is_active = true, provider = %s WHERE id = %s", [provider, inactive_user["id"]])
            user = inactive_user
            logger.info("Reactivated user %s (id=%s)", email, user["id"])

    # Revoked users are blocked — treat as if they don't exist
    if user and user.get("invite_status") == "revoked":
        logger.info("Login denied for revoked user %s (id=%s)", email, user["id"])
        user = None

    # Handle invited user activation
    if user and user.get("invite_status") == "invited":
        from models.db import execute as db_execute
        # Check expiry
        if user.get("expires_at"):
            import datetime
            if isinstance(user["expires_at"], str):
                exp = datetime.datetime.fromisoformat(user["expires_at"])
            else:
                exp = user["expires_at"]
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=datetime.timezone.utc)
            if exp < datetime.datetime.now(datetime.timezone.utc):
                db_execute("UPDATE users SET invite_status = 'expired' WHERE id = %s", [user["id"]])
                user = None  # Force deny — will fall through to new user flow
        if user:
            db_execute(
                "UPDATE users SET invite_status = 'active', provider = %s WHERE id = %s",
                [provider, user["id"]],
            )

    if not user:
        domain = email.split("@")[-1] if "@" in email else ""

        # 2. Designated super admin? (explicit email or trusted domain)
        if email in Config.SUPER_ADMIN_EMAILS or domain in Config.SUPER_ADMIN_DOMAINS:
            # Assign to first tenant so AI features, KB, etc. work in the UI
            default_tenant = fetch_one("SELECT id FROM tenants WHERE is_active = true ORDER BY id LIMIT 1")
            default_tid = default_tenant["id"] if default_tenant else None
            user_id = insert_returning(
                """INSERT INTO users (tenant_id, email, name, role, provider, created_via)
                   VALUES (%s, %s, %s, 'super_admin', %s, 'oauth')
                   RETURNING id""",
                [default_tid, email, name, provider],
            )
            user = {"id": user_id, "tenant_id": default_tid, "email": email, "name": name, "role": "super_admin"}

        # 3. Domain matches an existing tenant (by domain column or allowed_domains setting)?
        elif domain and domain not in Config.PERSONAL_EMAIL_DOMAINS:
            # 3a. Check tenant.domain column (primary domain match)
            tenant = fetch_one(
                "SELECT id FROM tenants WHERE LOWER(domain) = %s AND is_active = true LIMIT 1",
                [domain],
            )

            # 3b. Check allowed_domains in tenant settings (domain-based end_user auto-provisioning)
            if not tenant:
                tenant = fetch_one(
                    """SELECT id FROM tenants
                       WHERE is_active = true
                         AND settings->>'allowed_domains' IS NOT NULL
                         AND settings->>'allowed_domains' != ''
                         AND EXISTS (
                           SELECT 1 FROM unnest(
                             string_to_array(LOWER(settings->>'allowed_domains'), ',')
                           ) AS d(val)
                           WHERE TRIM(d.val) = %s
                         )
                       ORDER BY id LIMIT 1""",
                    [domain],
                )
                if tenant:
                    logger.info("Auto-provisioning end_user %s via allowed_domains for tenant_id=%s", email, tenant["id"])

            if tenant:
                user_id = insert_returning(
                    """INSERT INTO users (tenant_id, email, name, role, provider, created_via)
                       VALUES (%s, %s, %s, 'end_user', %s, 'oauth') RETURNING id""",
                    [tenant["id"], email, name, provider],
                )
                user = {"id": user_id, "tenant_id": tenant["id"], "email": email, "name": name, "role": "end_user"}

            # 4. Non-personal domain, no matching tenant → auto-create tenant
            else:
                domain_name = domain.split(".")[0].capitalize()
                slug = _slugify(domain_name)
                import datetime
                if Config.DEMO_MODE:
                    plan_tier = 'demo'
                    expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=Config.DEMO_TENANT_TTL_DAYS)).date()
                else:
                    plan_tier = 'trial'
                    expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=14)).date()
                tenant_id = insert_returning(
                    """INSERT INTO tenants (name, slug, domain, settings, plan_tier, plan_expires_at)
                       VALUES (%s, %s, %s, '{}', %s, %s)
                       ON CONFLICT (slug) DO UPDATE SET domain = EXCLUDED.domain
                       RETURNING id""",
                    [domain_name, slug, domain, plan_tier, expires],
                )
                ensure_default_sla(tenant_id)
                seed_notification_preferences(tenant_id)
                seed_default_groups(tenant_id)
                user_id = insert_returning(
                    """INSERT INTO users (tenant_id, email, name, role, provider, created_via)
                       VALUES (%s, %s, %s, 'tenant_admin', %s, 'oauth') RETURNING id""",
                    [tenant_id, email, name, provider],
                )
                user = {"id": user_id, "tenant_id": tenant_id, "email": email, "name": name, "role": "tenant_admin"}

        # 5. Personal email domain → end_user with no tenant
        else:
            user_id = insert_returning(
                """INSERT INTO users (tenant_id, email, name, role, provider, created_via)
                   VALUES (NULL, %s, %s, 'end_user', %s, 'oauth')
                   RETURNING id""",
                [email, name, provider],
            )
            user = {"id": user_id, "tenant_id": None, "email": email, "name": name, "role": "end_user"}

    from services.permission_service import enrich_session_permissions

    session["user"] = enrich_session_permissions({
        "id": user["id"],
        "tenant_id": user["tenant_id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "provider": provider,
    })
    from datetime import datetime, timezone
    session["last_active"] = datetime.now(timezone.utc).isoformat()

    # Cache tenant settings in session (avoids DB hit on every request)
    tenant_settings = {}
    if user.get("tenant_id"):
        try:
            tenant_row = fetch_one("SELECT settings FROM tenants WHERE id = %s", [user["tenant_id"]])
            tenant_settings = (tenant_row.get("settings") or {}) if tenant_row else {}
            if not isinstance(tenant_settings, dict):
                tenant_settings = {}
            if tenant_settings.get("idle_timeout_minutes"):
                session["idle_timeout_minutes"] = int(tenant_settings["idle_timeout_minutes"])
        except Exception:
            pass

    # SOC 2 CC6.1: MFA enforcement — reject login if tenant requires MFA but it wasn't used
    session["mfa_verified"] = mfa_verified
    if tenant_settings.get("require_mfa") and not mfa_verified:
        session.clear()
        logger.warning("MFA required but not verified for %s (provider=%s)", email, provider)
        try:
            from services.audit_service import log_event, LOGIN_FAILURE
            log_event(LOGIN_FAILURE, details={"provider": provider, "email": email, "reason": "mfa_required"}, request=request)
        except Exception:
            pass
        return  # Caller will redirect to login; session is cleared

    # Generate CSRF token for SPA (SOC 2 CC6.6)
    session["csrf_token"] = secrets.token_urlsafe(32)

    try:
        from services.audit_service import log_event, LOGIN_SUCCESS
        log_event(
            LOGIN_SUCCESS,
            tenant_id=user.get("tenant_id"),
            user_id=user.get("id"),
            details={"provider": provider, "email": user.get("email"), "role": user.get("role")},
            request=request,
        )
    except Exception:
        pass


# ============================================================
# Password reset
# ============================================================

@auth_bp.route("/auth/forgot-password", methods=["POST"])
@limiter.limit("3 per hour")
def forgot_password():
    """Initiate a password reset.  Always returns 200 to avoid leaking email existence."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()

    if email:
        user = fetch_one(
            "SELECT id, email FROM users WHERE LOWER(email) = %s AND is_active = true LIMIT 1",
            [email],
        )
        if user:
            try:
                from services.password_service import generate_reset_token
                from services.email_service import send_password_reset_email

                token = generate_reset_token(None, user["id"])
                reset_url = f"{Config.APP_URL.rstrip('/')}/reset-password?token={token}"
                send_password_reset_email(user["id"], user["email"], reset_url)
            except Exception as e:
                logger.error("forgot_password: token/email error for user_id=%s: %s", user.get("id"), e)

    return jsonify({"message": "If that email is registered you will receive a reset link shortly."}), 200


@auth_bp.route("/auth/reset-password", methods=["POST"])
@limiter.limit("10 per hour")
def reset_password():
    """Complete a password reset using a valid token."""
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    new_password = body.get("new_password") or ""

    if not token or not new_password:
        return jsonify({"error": "token and new_password are required"}), 400

    from services.password_service import validate_reset_token, consume_reset_token

    user = validate_reset_token(None, token)
    if not user:
        return jsonify({"error": "Invalid or expired reset token"}), 400

    password_hash = generate_password_hash(new_password)
    db_execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        [password_hash, user["id"]],
    )
    consume_reset_token(None, token)

    logger.info("Password reset completed for user_id=%s", user["id"])
    return jsonify({"message": "Password updated successfully"}), 200


# ============================================================
# Email verification
# ============================================================

@auth_bp.route("/auth/verify-email/<token>")
@limiter.limit("10 per hour")
def verify_email(token: str):
    """Validate + consume an email verification token, then redirect."""
    from services.verification_service import validate_verification_token, consume_verification_token

    user = validate_verification_token(None, token)
    if not user:
        return jsonify({"error": "Invalid or expired verification link"}), 400

    consume_verification_token(None, token)

    redirect_url = f"{Config.APP_URL.rstrip('/')}/?verified=1"
    return redirect(redirect_url)


# ============================================================
# User profile (phone number + SMS opt-in)
# ============================================================

_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def _profile_row_to_dict(row: dict) -> dict:
    """Normalise a users row into the public profile shape."""
    opted_in_at = row.get("sms_opted_in_at")
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "phone_number": row.get("phone_number"),
        "sms_opted_in": bool(row.get("sms_opted_in")),
        "sms_opted_in_at": opted_in_at.isoformat() if opted_in_at else None,
    }


@auth_bp.route("/api/auth/profile", methods=["GET"])
@login_required
@limiter.limit("30 per minute")
def get_profile():
    """Return the current user's profile including SMS contact fields."""
    user = get_current_user()
    if not user or not user.get("id"):
        return jsonify({"error": "Authentication required"}), 401

    row = fetch_one(
        "SELECT id, name, email, role, phone_number, sms_opted_in, sms_opted_in_at"
        " FROM users WHERE id = %s",
        [user["id"]],
    )
    if not row:
        return jsonify({"error": "User not found"}), 404

    return jsonify(_profile_row_to_dict(row)), 200


@auth_bp.route("/api/auth/profile", methods=["PUT"])
@login_required
@limiter.limit("10 per minute")
def update_profile():
    """Update the current user's phone number and/or SMS opt-in preference.

    Accepts JSON body with any combination of:
        phone_number  — E.164-ish string or null/empty to clear
        sms_opted_in  — boolean

    Opt-in semantics (A2P 10DLC):
        - Setting sms_opted_in=true records sms_opted_in_at=NOW() on the
          first opt-in (not overwritten on subsequent calls).
        - Setting sms_opted_in=false clears sms_opted_in_at.
        - Cannot opt in without a phone number on record after the update.
    """
    user = get_current_user()
    if not user or not user.get("id"):
        return jsonify({"error": "Authentication required"}), 401

    body = request.get_json(silent=True) or {}

    # ---- Resolve which fields were provided ----
    phone_provided = "phone_number" in body
    optin_provided = "sms_opted_in" in body

    if not phone_provided and not optin_provided:
        return jsonify({"error": "Nothing to update"}), 400

    # ---- Validate phone number if given ----
    new_phone = None
    if phone_provided:
        raw = body["phone_number"]
        if raw:
            raw = str(raw).strip()
            if not _PHONE_RE.match(raw):
                return jsonify({"error": "Invalid phone number format"}), 400
            new_phone = raw
        # else: empty/null → clear the field (new_phone stays None)

    # ---- Load current row so we can apply opt-in logic ----
    current = fetch_one(
        "SELECT phone_number, sms_opted_in, sms_opted_in_at FROM users WHERE id = %s",
        [user["id"]],
    )
    if not current:
        return jsonify({"error": "User not found"}), 404

    # Determine effective phone after update
    effective_phone = new_phone if phone_provided else current["phone_number"]

    # Determine effective opt-in flag after update
    new_opted_in = bool(body["sms_opted_in"]) if optin_provided else bool(current["sms_opted_in"])

    # ---- Business rule: cannot opt in without a phone number ----
    if new_opted_in and not effective_phone:
        return jsonify({"error": "A phone number is required to enable SMS notifications"}), 400

    # ---- Build SET clauses dynamically ----
    set_clauses = []
    params = []

    if phone_provided:
        set_clauses.append("phone_number = %s")
        params.append(new_phone)

    if optin_provided:
        set_clauses.append("sms_opted_in = %s")
        params.append(new_opted_in)

        if new_opted_in and not bool(current["sms_opted_in"]):
            # First opt-in — stamp the timestamp
            set_clauses.append("sms_opted_in_at = NOW()")
        elif not new_opted_in:
            # Opted out — clear the timestamp
            set_clauses.append("sms_opted_in_at = NULL")

    params.append(user["id"])
    db_execute(
        f"UPDATE users SET {', '.join(set_clauses)} WHERE id = %s",
        params,
    )

    # ---- Emit audit events for each change that actually occurred ----
    try:
        from services.audit_service import (
            log_event, RT_USER,
            USER_PHONE_CHANGED, USER_SMS_OPT_IN, USER_SMS_OPT_OUT,
        )
        if phone_provided and new_phone != current["phone_number"]:
            log_event(
                USER_PHONE_CHANGED,
                tenant_id=user.get("tenant_id"),
                user_id=user.get("id"),
                resource_type=RT_USER,
                resource_id=user["id"],
                details={
                    "before": current["phone_number"],
                    "after": new_phone,
                },
                request=request,
            )
        if optin_provided:
            old_opted_in = bool(current["sms_opted_in"])
            if new_opted_in and not old_opted_in:
                log_event(
                    USER_SMS_OPT_IN,
                    tenant_id=user.get("tenant_id"),
                    user_id=user.get("id"),
                    resource_type=RT_USER,
                    resource_id=user["id"],
                    details={
                        "phone_number": effective_phone,
                    },
                    request=request,
                )
            elif not new_opted_in and old_opted_in:
                log_event(
                    USER_SMS_OPT_OUT,
                    tenant_id=user.get("tenant_id"),
                    user_id=user.get("id"),
                    resource_type=RT_USER,
                    resource_id=user["id"],
                    details={
                        "phone_number": current["phone_number"],
                    },
                    request=request,
                )
    except Exception:
        pass

    updated = fetch_one(
        "SELECT id, name, email, role, phone_number, sms_opted_in, sms_opted_in_at"
        " FROM users WHERE id = %s",
        [user["id"]],
    )

    return jsonify(_profile_row_to_dict(updated)), 200
