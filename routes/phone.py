"""Phone helpdesk blueprint.

Endpoints:
  Admin (phone.manage):
    GET  /api/phone/config           — get tenant phone config
    PUT  /api/phone/config           — save credentials + settings
    GET  /api/phone/config/defaults  — canonical default AI/audio values
    GET  /api/phone/webhooks         — all webhook URLs for this tenant
    POST /api/phone/provision        — create/update ElevenLabs agent (legacy)
    POST /api/phone/link             — link Twilio number to ElevenLabs
    GET  /api/phone/sessions         — call log
    GET  /api/phone/sessions/<id>    — session detail

  Multi-agent CRUD (phone.manage):
    GET    /api/phone/agents           — list all agents
    POST   /api/phone/agents           — create new agent
    GET    /api/phone/agents/<id>      — get agent details
    PUT    /api/phone/agents/<id>      — update agent settings
    DELETE /api/phone/agents/<id>      — delete agent
    POST   /api/phone/agents/<id>/deploy   — provision in ElevenLabs
    POST   /api/phone/agents/<id>/activate — link number + set active
    POST   /api/phone/agents/<id>/reset    — reset to defaults
    GET    /api/phone/agents/default-prompt — get platform default prompt

  ElevenLabs tool webhooks (token-authenticated via ?t=):
    POST /api/phone/tool/<tenant_id>/search_kb
    POST /api/phone/tool/<tenant_id>/create_ticket
    POST /api/phone/tool/<tenant_id>/attempt_transfer
    POST /api/phone/tool/<tenant_id>/collect_email

  ElevenLabs lifecycle webhook (token-authenticated via ?t=):
    POST /api/phone/webhook/<tenant_id>/call_ended

  Twilio IVR webhooks (Twilio request signature validated):
    GET/POST /api/phone/ivr/<tenant_id>
    POST     /api/phone/ivr/<tenant_id>/route
"""

import logging

from flask import Blueprint, jsonify, request

from routes.auth import get_current_user, login_required, require_permission
from models.db import fetch_one

logger = logging.getLogger(__name__)
phone_bp = Blueprint("phone", __name__)


# ─────────────────────────────────────────────────────────
# Admin: Config CRUD
# ─────────────────────────────────────────────────────────

@phone_bp.route("/api/phone/config", methods=["GET"])
@require_permission("phone.manage")
def get_config():
    user = get_current_user()
    from services.phone_service import get_phone_config
    config = get_phone_config(user["tenant_id"])
    if not config:
        return jsonify({"configured": False})
    return jsonify({"configured": True, **config})


@phone_bp.route("/api/phone/config", methods=["PUT"])
@require_permission("phone.manage")
def save_config():
    user = get_current_user()
    data = request.json or {}
    from services.phone_service import save_phone_config
    updated = save_phone_config(user["tenant_id"], data)
    return jsonify(updated)


@phone_bp.route("/api/phone/config/defaults", methods=["GET"])
@require_permission("phone.manage")
def get_defaults():
    """Return canonical default values for all AI/audio settings."""
    from services.phone_service import PHONE_DEFAULTS
    return jsonify(PHONE_DEFAULTS)


@phone_bp.route("/api/phone/webhooks", methods=["GET"])
@require_permission("phone.manage")
def get_webhooks():
    """Return all webhook URLs for the current tenant's phone integration."""
    from config import Config
    from services.phone_service import make_webhook_token

    user = get_current_user()
    tenant_id = user["tenant_id"]
    token = make_webhook_token(tenant_id)
    base = Config.APP_URL.rstrip("/")

    tool_base = f"{base}/api/phone/tool/{tenant_id}"
    return jsonify({
        "tool_search_kb":         f"{tool_base}/search_kb?t={token}",
        "tool_create_ticket":     f"{tool_base}/create_ticket?t={token}",
        "tool_identify_caller":   f"{tool_base}/identify_caller?t={token}",
        "tool_attempt_transfer":  f"{tool_base}/attempt_transfer?t={token}",
        "tool_collect_email":     f"{tool_base}/collect_email?t={token}",
        "webhook_call_ended":     f"{base}/api/phone/webhook/{tenant_id}/call_ended?t={token}",
        "ivr_greeting":           f"{base}/api/phone/ivr/{tenant_id}",
    })


# ─────────────────────────────────────────────────────────
# Admin: Phone Agent CRUD
# ─────────────────────────────────────────────────────────

@phone_bp.route("/api/phone/agents", methods=["GET"])
@require_permission("phone.manage")
def list_agents():
    """List all phone agents for the current tenant."""
    user = get_current_user()
    from services.phone_service import list_phone_agents
    agents = list_phone_agents(user["tenant_id"])
    return jsonify(agents)


@phone_bp.route("/api/phone/agents", methods=["POST"])
@require_permission("phone.manage")
def create_agent():
    """Create a new phone agent."""
    user = get_current_user()
    from services.phone_service import check_phone_access, create_phone_agent

    access = check_phone_access(user["tenant_id"])
    if not access["allowed"]:
        return jsonify({"error": access["reason"]}), 403

    data = request.json or {}
    try:
        agent = create_phone_agent(user["tenant_id"], data)
        return jsonify(agent), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@phone_bp.route("/api/phone/agents/<int:agent_id>", methods=["GET"])
@require_permission("phone.manage")
def get_agent(agent_id: int):
    """Get a single phone agent's full details."""
    user = get_current_user()
    from services.phone_service import get_phone_agent
    agent = get_phone_agent(agent_id, user["tenant_id"])
    if not agent:
        return jsonify({"error": "Agent not found"}), 404
    return jsonify(agent)


@phone_bp.route("/api/phone/agents/<int:agent_id>", methods=["PUT"])
@require_permission("phone.manage")
def update_agent(agent_id: int):
    """Update a phone agent's settings."""
    user = get_current_user()
    data = request.json or {}
    try:
        from services.phone_service import update_phone_agent
        agent = update_phone_agent(agent_id, user["tenant_id"], data)
        return jsonify(agent)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@phone_bp.route("/api/phone/agents/<int:agent_id>", methods=["DELETE"])
@require_permission("phone.manage")
def remove_agent(agent_id: int):
    """Delete a phone agent (deprovisions from ElevenLabs)."""
    user = get_current_user()
    try:
        from services.phone_service import delete_phone_agent
        delete_phone_agent(agent_id, user["tenant_id"])
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@phone_bp.route("/api/phone/agents/<int:agent_id>/deploy", methods=["POST"])
@require_permission("phone.manage")
def agent_deploy(agent_id: int):
    """Deploy a phone agent to ElevenLabs (provision ConvAI agent)."""
    user = get_current_user()
    try:
        from services.phone_service import deploy_agent
        result = deploy_agent(agent_id, user["tenant_id"])
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("agent_deploy error agent=%s: %s", agent_id, e)
        return jsonify({"error": "ElevenLabs API error — check credentials"}), 502


@phone_bp.route("/api/phone/agents/<int:agent_id>/activate", methods=["POST"])
@require_permission("phone.manage")
def agent_activate(agent_id: int):
    """Link Twilio number to this agent and activate it."""
    user = get_current_user()
    try:
        from services.phone_service import activate_agent
        result = activate_agent(agent_id, user["tenant_id"])
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("agent_activate error agent=%s: %s", agent_id, e)
        return jsonify({"error": "Activation failed — check Twilio credentials"}), 502


@phone_bp.route("/api/phone/agents/<int:agent_id>/reset", methods=["POST"])
@require_permission("phone.manage")
def agent_reset(agent_id: int):
    """Reset a phone agent's settings to platform defaults."""
    user = get_current_user()
    try:
        from services.phone_service import reset_agent_to_defaults
        agent = reset_agent_to_defaults(agent_id, user["tenant_id"])
        return jsonify(agent)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@phone_bp.route("/api/phone/agents/default-prompt", methods=["GET"])
@require_permission("phone.manage")
def agent_default_prompt():
    """Return the platform default system prompt for preview."""
    user = get_current_user()
    language = request.args.get("language", "en")
    agent_name = request.args.get("agent_name", "Atlas" if language == "en" else "Astra")

    tenant = fetch_one("SELECT name FROM tenants WHERE id = %s", [user["tenant_id"]])
    tenant_name = (tenant or {}).get("name", "Your Company")

    from services.phone_service import get_default_system_prompt
    prompt = get_default_system_prompt(language, tenant_name, agent_name)
    return jsonify({"prompt": prompt, "language": language})


# ─────────────────────────────────────────────────────────
# Admin: Provisioning (legacy — kept for backward compat)
# ─────────────────────────────────────────────────────────

@phone_bp.route("/api/phone/provision", methods=["POST"])
@require_permission("phone.manage")
def provision():
    """Create/update the ElevenLabs agent for this tenant."""
    user = get_current_user()
    tenant_id = user["tenant_id"]

    tenant = fetch_one("SELECT name FROM tenants WHERE id = %s", [tenant_id])
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    try:
        from services.phone_service import provision_agent
        result = provision_agent(tenant_id, tenant["name"])
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("provision error tenant=%s: %s", tenant_id, e)
        return jsonify({"error": "ElevenLabs API error — check your API key"}), 502


@phone_bp.route("/api/phone/provision_es", methods=["POST"])
@require_permission("phone.manage")
def provision_es():
    """Create/update the Spanish ElevenLabs agent (Sofía) for this tenant."""
    user = get_current_user()
    tenant_id = user["tenant_id"]

    tenant = fetch_one("SELECT name FROM tenants WHERE id = %s", [tenant_id])
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    try:
        from services.phone_service import provision_agent_es
        result = provision_agent_es(tenant_id, tenant["name"])
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("provision_es error tenant=%s: %s", tenant_id, e)
        return jsonify({"error": "ElevenLabs API error — check your API key"}), 502


@phone_bp.route("/api/phone/enable", methods=["POST"])
@require_permission("phone.manage")
def enable_platform():
    """One-click platform enable: buy number + provision agent + link. No credentials needed from tenant."""
    user = get_current_user()
    tenant_id = user["tenant_id"]

    tenant = fetch_one("SELECT name FROM tenants WHERE id = %s", [tenant_id])
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    try:
        from services.phone_service import auto_provision
        result = auto_provision(tenant_id, tenant["name"])
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("auto_provision error tenant=%s: %s", tenant_id, e)
        return jsonify({"error": "Provisioning failed — check server logs"}), 502


@phone_bp.route("/api/phone/link", methods=["POST"])
@require_permission("phone.manage")
def link_number():
    """Link tenant's Twilio number to their ElevenLabs agent."""
    user = get_current_user()
    try:
        from services.phone_service import link_twilio_number
        result = link_twilio_number(user["tenant_id"])
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("link_number error tenant=%s: %s", user["tenant_id"], e)
        return jsonify({"error": "Failed to link number — check Twilio credentials"}), 502


# ─────────────────────────────────────────────────────────
# Admin: Call Logs
# ─────────────────────────────────────────────────────────

@phone_bp.route("/api/phone/sessions", methods=["GET"])
@require_permission("phone.manage")
def list_sessions():
    user     = get_current_user()
    limit    = min(int(request.args.get("limit",  50)), 200)
    offset   = int(request.args.get("offset", 0))
    agent_id = request.args.get("agent_id", type=int)
    from services.phone_service import get_call_logs
    rows = get_call_logs(user["tenant_id"], limit=limit, offset=offset, agent_id=agent_id)
    return jsonify(rows)


@phone_bp.route("/api/phone/sessions/<int:session_id>", methods=["GET"])
@require_permission("phone.manage")
def get_session(session_id: int):
    user = get_current_user()
    from models.db import fetch_one as db_fetch, fetch_all as db_fetch_all

    session = db_fetch(
        """SELECT ps.*, t.ticket_number
           FROM phone_sessions ps
           LEFT JOIN tickets t ON t.id = ps.ticket_id
           WHERE ps.id = %s AND ps.tenant_id = %s""",
        [session_id, user["tenant_id"]],
    )
    if not session:
        return jsonify({"error": "Session not found"}), 404

    transfers = db_fetch_all(
        "SELECT * FROM phone_transfer_attempts WHERE session_id = %s ORDER BY attempted_at",
        [session_id],
    )
    return jsonify({**dict(session), "transfers": transfers})


# ─────────────────────────────────────────────────────────
# Tool Webhooks (called by ElevenLabs)
# ─────────────────────────────────────────────────────────

def _verify_tool_token(tenant_id: int) -> bool:
    """Validate the ?t= HMAC token on tool/lifecycle webhook requests."""
    from services.phone_service import verify_webhook_token
    token = request.args.get("t", "")
    return verify_webhook_token(tenant_id, token)


def _verify_twilio_signature(tenant_id: int) -> bool:
    """Validate Twilio request signature on IVR webhook requests.

    Uses the tenant's Twilio auth token to verify the X-Twilio-Signature header.
    This prevents unauthenticated callers from hitting IVR endpoints directly.
    """
    from twilio.request_validator import RequestValidator
    from services.phone_service import get_effective_credentials
    from urllib.parse import urlparse
    from config import Config

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        logger.warning("Twilio signature missing for tenant=%s", tenant_id)
        return False

    creds = get_effective_credentials(tenant_id)
    auth_token = creds.get("twilio_auth_token", "")
    if not auth_token:
        logger.error("No Twilio auth token available for tenant=%s — cannot validate signature", tenant_id)
        return False

    validator = RequestValidator(auth_token)

    # request.url uses the internal Docker scheme/host — Twilio signed against
    # the public URL. Reconstruct using APP_URL as the base.
    base = Config.APP_URL.rstrip("/")
    parsed = urlparse(request.url)
    url = base + parsed.path + (f"?{parsed.query}" if parsed.query else "")

    if request.method == "POST":
        return validator.validate(url, request.form.to_dict(), signature)
    else:
        return validator.validate(url, {}, signature)


def _get_tenant_and_session(tenant_id: int, body: dict):
    """Resolve tenant row and get/create session from conversation_id."""
    tenant = fetch_one("SELECT id, name FROM tenants WHERE id = %s AND is_active = TRUE", [tenant_id])
    if not tenant:
        return None, None

    conversation_id = body.get("conversation_id") or body.get("parameters", {}).get("conversation_id")
    if not conversation_id:
        return tenant, None

    from services.phone_service import get_or_create_session
    session = get_or_create_session(tenant_id, conversation_id)
    return tenant, session


@phone_bp.route("/api/phone/tool/<int:tenant_id>/search_kb", methods=["POST"])
def tool_search_kb(tenant_id: int):
    if not _verify_tool_token(tenant_id):
        return jsonify({"error": "Unauthorized"}), 401

    body   = request.json or {}
    params = body.get("parameters") or body
    query  = params.get("query", "")

    if not query:
        return jsonify({"result": "No query provided."})

    from services.phone_service import phone_search_kb
    result = phone_search_kb(tenant_id, query)
    return jsonify({"result": result})


@phone_bp.route("/api/phone/tool/<int:tenant_id>/identify_caller", methods=["POST"])
def tool_identify_caller(tenant_id: int):
    if not _verify_tool_token(tenant_id):
        return jsonify({"error": "Unauthorized"}), 401

    body   = request.json or {}
    params = body.get("parameters") or body

    name            = (params.get("name") or "").strip()
    email           = (params.get("email") or "").strip()
    conversation_id = params.get("conversation_id") or body.get("conversation_id", "")

    from services.phone_service import get_or_create_session, identify_caller_from_call

    # Link IVR session to this conversation_id so phone-number lookup has access
    # to the caller_phone Twilio sent us. Without this, phone lookup always fails
    # and we fall through to unreliable ASR name matching.
    if conversation_id:
        get_or_create_session(tenant_id, conversation_id)

    result = identify_caller_from_call(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        name=name,
        email=email or None,
    )

    if result["status"] == "found":
        return jsonify({
            "result": f"Found caller: {result['name']} (user_id={result['user_id']}, confidence={result['confidence']}). Greet them by name.",
            **result,
        })
    elif result["status"] == "created":
        return jsonify({
            "result": f"New caller created: {result['name']} (user_id={result['user_id']}). This is their first call — welcome them warmly.",
            **result,
        })
    elif result["status"] == "multiple_matches":
        return jsonify({
            "result": result["message"],
            **result,
        })
    else:
        return jsonify({
            "result": result.get("message", "Could not identify caller — ask for their name."),
            "status": "not_found",
        })


@phone_bp.route("/api/phone/tool/<int:tenant_id>/create_ticket", methods=["POST"])
def tool_create_ticket(tenant_id: int):
    if not _verify_tool_token(tenant_id):
        return jsonify({"error": "Unauthorized"}), 401

    body   = request.json or {}
    params = body.get("parameters") or body

    conversation_id      = params.get("conversation_id") or body.get("conversation_id")
    subject              = params.get("subject", "Phone support request")
    description          = params.get("description", "")
    priority             = params.get("priority", "p3")
    caller_email         = params.get("caller_email") or None
    requester_user_id    = params.get("requester_user_id") or None
    resolved             = bool(params.get("resolved_on_call", False))
    problem_category_id  = params.get("problem_category_id") or None
    custom_fields_raw    = params.get("custom_fields") or None

    if priority not in ("p1", "p2", "p3", "p4"):
        priority = "p3"

    if requester_user_id:
        try:
            requester_user_id = int(requester_user_id)
        except (TypeError, ValueError):
            requester_user_id = None

    if problem_category_id:
        try:
            problem_category_id = int(problem_category_id)
        except (TypeError, ValueError):
            problem_category_id = None

    from services.phone_service import get_or_create_session, create_ticket_from_call

    session = None
    if conversation_id:
        session = get_or_create_session(tenant_id, conversation_id)

    session_id = session["id"] if session else _create_anonymous_session(tenant_id)

    try:
        result = create_ticket_from_call(
            tenant_id=tenant_id,
            session_id=session_id,
            subject=subject,
            description=description,
            priority=priority,
            caller_email=caller_email,
            requester_user_id=requester_user_id,
            resolved_on_call=resolved,
            problem_category_id=problem_category_id,
            custom_fields=custom_fields_raw if isinstance(custom_fields_raw, dict) else None,
        )
        spoken = result.get("ticket_number_spoken", result["ticket_number"])
        msg = f"Ticket created. Your case number is {spoken}. "
        if resolved:
            msg += "Marked as resolved."
        else:
            msg += "Team will follow up."
        # If there are required custom fields, instruct the agent to collect them
        if result.get("required_fields_instruction"):
            msg += " " + result["required_fields_instruction"]
        return jsonify({
            "result": msg,
            **result,
        })
    except Exception as e:
        logger.error("create_ticket tool error tenant=%s: %s", tenant_id, e)
        return jsonify({"result": "Ticket creation failed — please note the issue for manual follow-up."})


@phone_bp.route("/api/phone/tool/<int:tenant_id>/attempt_transfer", methods=["POST"])
def tool_attempt_transfer(tenant_id: int):
    if not _verify_tool_token(tenant_id):
        return jsonify({"error": "Unauthorized"}), 401

    body   = request.json or {}
    params = body.get("parameters") or body

    conversation_id = params.get("conversation_id") or body.get("conversation_id")
    if not conversation_id:
        return jsonify({"success": False, "message": "Missing conversation_id"})

    from services.phone_service import get_or_create_session, attempt_transfer

    session = get_or_create_session(tenant_id, conversation_id)

    # Check for an existing ticket_id from params
    ticket_id = params.get("ticket_id")
    if ticket_id and not session.get("ticket_id"):
        from services.phone_service import update_session
        update_session(session["id"], ticket_id=ticket_id)

    result = attempt_transfer(session["id"], tenant_id)
    return jsonify(result)


@phone_bp.route("/api/phone/tool/<int:tenant_id>/collect_email", methods=["POST"])
def tool_collect_email(tenant_id: int):
    if not _verify_tool_token(tenant_id):
        return jsonify({"error": "Unauthorized"}), 401

    body   = request.json or {}
    params = body.get("parameters") or body

    email           = (params.get("email") or "").strip()
    conversation_id = params.get("conversation_id") or body.get("conversation_id")

    if not email:
        return jsonify({"result": "No email address provided."})

    if conversation_id:
        from services.phone_service import get_or_create_session, update_session
        session = get_or_create_session(tenant_id, conversation_id)
        update_session(session["id"], caller_email=email, status="email_collected")

        # If there's a linked ticket, update it too
        if session.get("ticket_id"):
            from models.db import execute as db_exec
            from models.db import fetch_one as db_fetch
            # Store email in ticket description as a note
            db_exec(
                """UPDATE tickets
                   SET description = description || %s
                   WHERE id = %s AND tenant_id = %s""",
                [f"\n\nCallback email: {email}", session["ticket_id"], tenant_id],
            )

    return jsonify({
        "result": (
            f"Thank you — I've recorded your email as {email}. "
            "A member of our team will reach out to you within one business day."
        )
    })


@phone_bp.route("/api/phone/tool/<int:tenant_id>/set_custom_field", methods=["POST"])
def tool_set_custom_field(tenant_id: int):
    """Set a custom field value on a ticket during a phone call."""
    if not _verify_tool_token(tenant_id):
        return jsonify({"error": "Unauthorized"}), 401

    body   = request.json or {}
    params = body.get("parameters") or body

    ticket_id = params.get("ticket_id")
    field_key = (params.get("field_key") or "").strip()
    value     = params.get("value")

    if not ticket_id or not field_key:
        return jsonify({"result": "Missing ticket_id or field_key — cannot set custom field."})

    import json as json_mod
    from models.db import fetch_one as db_fetch, execute as db_exec

    # Resolve field definition
    field_def = db_fetch(
        "SELECT id, name, field_type FROM custom_field_definitions "
        "WHERE tenant_id = %s AND field_key = %s AND is_active = true",
        [tenant_id, field_key],
    )
    if not field_def:
        return jsonify({"result": f"Custom field '{field_key}' not found. Skipping."})

    # Coerce value for number fields
    if field_def["field_type"] == "number":
        try:
            value = float(value) if "." in str(value) else int(value)
        except (ValueError, TypeError):
            return jsonify({"result": f"'{value}' is not a valid number for {field_def['name']}. Please ask again."})

    # Upsert
    db_exec(
        """INSERT INTO ticket_custom_field_values (ticket_id, field_id, value, set_by, set_at)
           VALUES (%s, %s, %s::jsonb, NULL, now())
           ON CONFLICT (ticket_id, field_id)
           DO UPDATE SET value = EXCLUDED.value, set_by = EXCLUDED.set_by, set_at = now()""",
        [ticket_id, field_def["id"], json_mod.dumps(value)],
    )

    logger.info("Phone agent set custom field %s=%s on ticket %s", field_key, value, ticket_id)
    return jsonify({
        "result": f"Got it — I've recorded {field_def['name']} as {value} on the ticket."
    })


@phone_bp.route("/api/phone/tool/<int:tenant_id>/get_category_fields", methods=["POST"])
def tool_get_category_fields(tenant_id: int):
    """Identify the problem category from the caller's issue and return required custom fields."""
    if not _verify_tool_token(tenant_id):
        return jsonify({"error": "Unauthorized"}), 401

    body   = request.json or {}
    params = body.get("parameters") or body

    issue_description = (params.get("issue_description") or "").strip()
    category_name     = (params.get("category_name") or "").strip() or None

    if not issue_description and not category_name:
        return jsonify({"result": "Please describe the issue so I can identify the right category."})

    from services.phone_service import get_category_fields_for_call
    result = get_category_fields_for_call(
        tenant_id=tenant_id,
        issue_description=issue_description or "",
        category_name=category_name,
    )

    # Build spoken result
    if result.get("matched"):
        return jsonify({
            "result": result.get("instruction", f"Category identified: {result['category_name']}."),
            **result,
        })
    else:
        return jsonify({
            "result": result.get("message", "Could not identify a category. Proceed without one."),
            **result,
        })


# ─────────────────────────────────────────────────────────
# ElevenLabs Post-call Webhook
# ─────────────────────────────────────────────────────────

@phone_bp.route("/api/phone/webhook/<int:tenant_id>/call_ended", methods=["POST"])
def webhook_call_ended(tenant_id: int):
    """ElevenLabs fires this when a call ends. Saves transcript + summary."""
    if not _verify_tool_token(tenant_id):
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.json or {}
    conversation_id = (
        payload.get("conversation_id")
        or payload.get("metadata", {}).get("conversation_id")
        or (payload.get("data") or {}).get("conversation_id")
    )

    if not conversation_id:
        logger.warning("call_ended webhook missing conversation_id: %s", payload)
        return jsonify({"ok": True})

    try:
        from services.phone_service import finalize_session
        finalize_session(tenant_id, conversation_id, payload)
    except Exception as e:
        logger.error("finalize_session error tenant=%s: %s", tenant_id, e)

    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────
# IVR — Bilingual greeting + language routing
# ─────────────────────────────────────────────────────────

EL_REGISTER_CALL_URL = "https://api.elevenlabs.io/v1/convai/twilio/register-call"


def _tts_safe(text: str) -> str:
    """Normalize text so Polly pronounces acronyms correctly.

    - 'IT' / 'TI' (all-caps word) → 'I.T.' so Polly says 'eye-tee' not 'it'/'ti'
    - '-IT' / '-it' suffix in company names → '-I.T.' (e.g. 'Acme-IT')
    """
    import re
    text = re.sub(r'\bIT\b', 'I.T.', text)           # standalone uppercase IT (English)
    text = re.sub(r'\bTI\b', 'I.T.', text)           # standalone TI (Spanish variant — use I.T. per tenant preference)
    text = re.sub(r'-[Ii][Tt]\b', '-I.T.', text)     # company-name suffix: e.g. Acme-IT
    return text


POLLY_VOICE_MAP: dict[str, str] = {
    "en": "Polly.Joanna",
    "es": "Polly.Lupe",
    "fr": "Polly.Lea",
    "de": "Polly.Vicki",
    "pt": "Polly.Camila",
}

IVR_GREETING_DEFAULTS: dict[str, str] = {
    "en": "Press {digit} for support in English.",
    "es": "Oprima {digit} para soporte en español.",
    "fr": "Appuyez sur {digit} pour le support en français.",
    "de": "Drücken Sie {digit} für Support auf Deutsch.",
    "pt": "Pressione {digit} para suporte em português.",
}


@phone_bp.route("/api/phone/ivr/<int:tenant_id>", methods=["GET", "POST"])
def ivr_greeting(tenant_id: int):
    """Serve TwiML greeting auto-composed from active phone agents.
    Each agent contributes a <Say> block in its language, ordered by ivr_digit."""
    from flask import Response
    from models.db import execute as db_exec, fetch_one as db_fetch
    from services.phone_service import get_agents_for_ivr
    from markupsafe import escape

    if not _verify_twilio_signature(tenant_id):
        logger.warning("IVR greeting: invalid Twilio signature for tenant=%s", tenant_id)
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Unauthorized request.</Say></Response>',
            status=403, mimetype="text/xml",
        )

    call_sid    = request.form.get("CallSid") or request.args.get("CallSid")
    caller_num  = request.form.get("From", "")

    # Upsert session keyed on Twilio CallSid so re-entries (Gather timeout redirect) don't dupe
    if call_sid:
        existing = db_fetch(
            "SELECT id FROM phone_sessions WHERE twilio_call_sid = %s AND tenant_id = %s",
            [call_sid, tenant_id],
        )
        if not existing:
            db_exec(
                """INSERT INTO phone_sessions (tenant_id, twilio_call_sid, caller_phone, status)
                   VALUES (%s, %s, %s, 'ivr')""",
                [tenant_id, call_sid, caller_num or None],
            )

    # Build greeting from active agents
    agents = get_agents_for_ivr(tenant_id)
    from config import Config
    base = Config.APP_URL.rstrip("/")
    tenant_row = db_fetch("SELECT name FROM tenants WHERE id = %s", [tenant_id])
    tenant_name = (tenant_row or {}).get("name", "support")
    tenant_name_tts = _tts_safe(tenant_name)

    say_blocks = []
    for agent in agents:
        lang = agent.get("language", "en")
        digit = agent.get("ivr_digit", "1")
        voice = POLLY_VOICE_MAP.get(lang, "Polly.Joanna")
        greeting = agent.get("ivr_greeting") or ""
        if not greeting.strip():
            template = IVR_GREETING_DEFAULTS.get(lang, IVR_GREETING_DEFAULTS["en"])
            greeting = template.format(digit=digit)
        say_blocks.append(f'    <Say voice="{voice}">{escape(_tts_safe(greeting))}</Say>')
        say_blocks.append('    <Pause length="1"/>')

    # Fallback: if no agents configured, give a generic English prompt
    if not say_blocks:
        say_blocks = [
            '    <Say voice="Polly.Joanna">Press 1 for support in English.</Say>',
            '    <Pause length="1"/>',
        ]

    inner = "\n".join(say_blocks)
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">This call may be recorded for quality assurance purposes. Thank you for calling {escape(tenant_name_tts)}.</Say>
  <Pause length="1"/>
  <Gather numDigits="1" action="{base}/api/phone/ivr/{tenant_id}/route" method="POST" timeout="10">
{inner}
  </Gather>
  <Redirect method="POST">{base}/api/phone/ivr/{tenant_id}</Redirect>
</Response>"""
    return Response(twiml, mimetype="text/xml")


@phone_bp.route("/api/phone/ivr/<int:tenant_id>/route", methods=["POST"])
def ivr_route(tenant_id: int):
    """Handle digit selection — look up phone_agents by ivr_digit, proxy to EL register-call."""
    import requests as http
    from flask import Response
    from services.phone_service import get_agent_for_ivr, get_effective_credentials
    from models.db import execute as db_exec

    if not _verify_twilio_signature(tenant_id):
        logger.warning("IVR route: invalid Twilio signature for tenant=%s", tenant_id)
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Unauthorized request.</Say></Response>',
            status=403, mimetype="text/xml",
        )

    digit    = request.form.get("Digits", "1")
    from_    = request.form.get("From", "")
    to_      = request.form.get("To", "")
    call_sid = request.form.get("CallSid") or request.args.get("CallSid")

    # Mark session as routing (digit received — no longer a pure IVR abandon)
    if call_sid:
        db_exec(
            "UPDATE phone_sessions SET status = 'routing' WHERE twilio_call_sid = %s AND tenant_id = %s AND status = 'ivr'",
            [call_sid, tenant_id],
        )

    # Look up the right agent from phone_agents table
    agent = get_agent_for_ivr(tenant_id, digit)

    creds = get_effective_credentials(tenant_id)
    api_key = creds.get("elevenlabs_api_key", "")

    el_agent_id = (agent or {}).get("el_agent_id")

    if not el_agent_id or not api_key:
        logger.error("IVR route missing agent or api_key for tenant=%s digit=%s", tenant_id, digit)
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response><Say>We're sorry, this service is temporarily unavailable. Please try again later.</Say></Response>"""
        return Response(twiml, mimetype="text/xml")

    # Gate check: reject if tenant is over their billing cap
    try:
        from services.billing_service import check_ai_gate, ApiCapError
        check_ai_gate(tenant_id)
    except ApiCapError:
        logger.warning("Phone AI gate blocked tenant=%s — over cap or free tier", tenant_id)
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response><Say>We're sorry, this service is temporarily unavailable. Please call back later or contact us by email.</Say></Response>"""
        return Response(twiml, mimetype="text/xml")

    # Link session to the phone agent
    if call_sid and agent:
        db_exec(
            "UPDATE phone_sessions SET phone_agent_id = %s WHERE twilio_call_sid = %s AND tenant_id = %s",
            [agent["id"], call_sid, tenant_id],
        )

    r = http.post(
        EL_REGISTER_CALL_URL,
        json={"agent_id": el_agent_id, "from_number": from_, "to_number": to_},
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        timeout=10,
    )

    if r.status_code == 200:
        return Response(r.text, mimetype="text/xml")

    logger.error("EL register-call failed: %s %s", r.status_code, r.text[:200])
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response><Say>We're sorry, we could not connect your call. Please try again.</Say></Response>"""
    return Response(twiml, mimetype="text/xml")


# ─────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────

def _create_anonymous_session(tenant_id: int) -> int:
    """Fallback: create a session without a conversation_id."""
    from models.db import insert_returning as db_ir
    return db_ir(
        "INSERT INTO phone_sessions (tenant_id, status) VALUES (%s, 'active') RETURNING id",
        [tenant_id],
    )
