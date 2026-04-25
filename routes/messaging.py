"""SMS & WhatsApp messaging blueprint.

Endpoints:
  Admin (messaging.manage):
    GET  /api/messaging/config         — get messaging config
    PUT  /api/messaging/config         — save messaging settings
    GET  /api/messaging/webhooks       — webhook URLs for Twilio
    GET  /api/messaging/stats          — 30-day messaging stats

  Conversations (messaging.manage):
    GET  /api/messaging/conversations              — list conversations
    GET  /api/messaging/conversations/<id>         — get conversation
    PUT  /api/messaging/conversations/<id>         — update conversation
    GET  /api/messaging/conversations/<id>/messages — list messages
    POST /api/messaging/conversations/<id>/messages — send message

  Templates (messaging.manage):
    GET    /api/messaging/templates       — list templates
    POST   /api/messaging/templates       — create template
    GET    /api/messaging/templates/<id>  — get template
    PUT    /api/messaging/templates/<id>  — update template
    DELETE /api/messaging/templates/<id>  — delete template

  Twilio webhooks (token-authenticated via ?t=):
    POST /api/messaging/webhook/<tenant_id>/inbound  — inbound SMS/WhatsApp
    POST /api/messaging/webhook/<tenant_id>/status   — delivery status updates
"""

import logging

from flask import Blueprint, jsonify, request

from app import limiter
from routes.auth import get_current_user, login_required, require_permission

logger = logging.getLogger(__name__)
messaging_bp = Blueprint("messaging", __name__)


# ─────────────────────────────────────────────────────────
# Webhook token verification
# ─────────────────────────────────────────────────────────

def _verify_token(tenant_id: int) -> bool:
    from services.messaging_service import verify_messaging_webhook_token
    token = request.args.get("t", "")
    return verify_messaging_webhook_token(tenant_id, token)


# ─────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────

@messaging_bp.route("/api/messaging/config", methods=["GET"])
@require_permission("messaging.manage")
def get_config():
    user = get_current_user()
    from services.messaging_service import get_messaging_config
    config = get_messaging_config(user["tenant_id"])
    return jsonify(config)


@messaging_bp.route("/api/messaging/config", methods=["PUT"])
@require_permission("messaging.manage")
def save_config():
    user = get_current_user()
    data = request.json or {}
    from services.messaging_service import save_messaging_config
    updated = save_messaging_config(user["tenant_id"], data)
    return jsonify(updated)


@messaging_bp.route("/api/messaging/webhooks", methods=["GET"])
@require_permission("messaging.manage")
def get_webhooks():
    """Return webhook URLs the tenant needs to configure in Twilio."""
    from config import Config
    from services.messaging_service import make_messaging_webhook_token

    user = get_current_user()
    tenant_id = user["tenant_id"]
    token = make_messaging_webhook_token(tenant_id)
    base = Config.APP_URL.rstrip("/")

    return jsonify({
        "inbound_webhook": f"{base}/api/messaging/webhook/{tenant_id}/inbound?t={token}",
        "status_callback": f"{base}/api/messaging/webhook/{tenant_id}/status?t={token}",
    })


@messaging_bp.route("/api/messaging/stats", methods=["GET"])
@require_permission("messaging.manage")
def get_stats():
    user = get_current_user()
    from services.messaging_service import get_messaging_stats
    stats = get_messaging_stats(user["tenant_id"])
    return jsonify(stats)


# ─────────────────────────────────────────────────────────
# Conversations
# ─────────────────────────────────────────────────────────

@messaging_bp.route("/api/messaging/conversations", methods=["GET"])
@require_permission("messaging.manage")
def list_conversations():
    user = get_current_user()
    from services.messaging_service import list_conversations as _list

    channel = request.args.get("channel")
    status = request.args.get("status")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    convs = _list(user["tenant_id"], channel=channel, status=status,
                  limit=limit, offset=offset)
    return jsonify(convs)


@messaging_bp.route("/api/messaging/conversations/<int:conv_id>", methods=["GET"])
@require_permission("messaging.manage")
def get_conversation(conv_id: int):
    user = get_current_user()
    from services.messaging_service import get_conversation as _get
    conv = _get(conv_id, user["tenant_id"])
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify(conv)


@messaging_bp.route("/api/messaging/conversations/<int:conv_id>", methods=["PUT"])
@require_permission("messaging.manage")
def update_conversation(conv_id: int):
    user = get_current_user()
    data = request.json or {}
    from services.messaging_service import update_conversation as _update
    conv = _update(conv_id, user["tenant_id"], data)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify(conv)


# ─────────────────────────────────────────────────────────
# Messages
# ─────────────────────────────────────────────────────────

@messaging_bp.route("/api/messaging/conversations/<int:conv_id>/messages", methods=["GET"])
@require_permission("messaging.manage")
def get_messages(conv_id: int):
    user = get_current_user()
    from services.messaging_service import list_messages

    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))

    msgs = list_messages(conv_id, user["tenant_id"], limit=limit, offset=offset)
    return jsonify(msgs)


@messaging_bp.route("/api/messaging/conversations/<int:conv_id>/messages", methods=["POST"])
@require_permission("messaging.manage")
@limiter.limit("30 per minute")
def send_message(conv_id: int):
    user = get_current_user()
    data = request.json or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Message body is required"}), 400

    from services.messaging_service import send_message as _send, check_messaging_access

    access = check_messaging_access(user["tenant_id"])
    if not access["allowed"]:
        return jsonify({"error": access["reason"]}), 403

    try:
        result = _send(
            tenant_id=user["tenant_id"],
            conversation_id=conv_id,
            body=body,
            sender_user_id=user["id"],
            template_name=data.get("template_name"),
            media_url=data.get("media_url"),
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# ─────────────────────────────────────────────────────────
# Templates
# ─────────────────────────────────────────────────────────

@messaging_bp.route("/api/messaging/templates", methods=["GET"])
@require_permission("messaging.manage")
def get_templates():
    user = get_current_user()
    from services.messaging_service import list_templates

    language = request.args.get("language")
    templates = list_templates(user["tenant_id"], language=language)
    return jsonify(templates)


@messaging_bp.route("/api/messaging/templates", methods=["POST"])
@require_permission("messaging.manage")
def create_template():
    user = get_current_user()
    data = request.json or {}
    from services.messaging_service import create_template as _create

    try:
        template = _create(user["tenant_id"], data)
        return jsonify(template), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@messaging_bp.route("/api/messaging/templates/<int:template_id>", methods=["GET"])
@require_permission("messaging.manage")
def get_template(template_id: int):
    user = get_current_user()
    from services.messaging_service import get_template as _get
    template = _get(template_id, user["tenant_id"])
    if not template:
        return jsonify({"error": "Template not found"}), 404
    return jsonify(template)


@messaging_bp.route("/api/messaging/templates/<int:template_id>", methods=["PUT"])
@require_permission("messaging.manage")
def update_template(template_id: int):
    user = get_current_user()
    data = request.json or {}
    from services.messaging_service import update_template as _update

    template = _update(template_id, user["tenant_id"], data)
    if not template:
        return jsonify({"error": "Template not found"}), 404
    return jsonify(template)


@messaging_bp.route("/api/messaging/templates/<int:template_id>", methods=["DELETE"])
@require_permission("messaging.manage")
def delete_template(template_id: int):
    user = get_current_user()
    from services.messaging_service import delete_template as _delete

    _delete(template_id, user["tenant_id"])
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────
# Twilio webhooks (token-authenticated)
# ─────────────────────────────────────────────────────────

@messaging_bp.route("/api/messaging/webhook/<int:tenant_id>/inbound", methods=["POST"])
def webhook_inbound(tenant_id: int):
    """Twilio fires this when an SMS or WhatsApp message arrives."""
    if not _verify_token(tenant_id):
        return jsonify({"error": "Unauthorized"}), 401

    # Twilio sends form data, not JSON
    data = request.form.to_dict() if request.form else (request.json or {})

    from services.messaging_service import handle_inbound_message

    try:
        result = handle_inbound_message(tenant_id, data)
        # Return empty TwiML — we handle replies ourselves via the API
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>', 200, {
            "Content-Type": "application/xml"
        }
    except Exception as e:
        logger.error("Inbound webhook error: tenant=%s error=%s", tenant_id, e)
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>', 200, {
            "Content-Type": "application/xml"
        }


@messaging_bp.route("/api/messaging/webhook/<int:tenant_id>/status", methods=["POST"])
def webhook_status(tenant_id: int):
    """Twilio fires this with delivery status updates (sent, delivered, read, failed)."""
    if not _verify_token(tenant_id):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.form.to_dict() if request.form else (request.json or {})

    from services.messaging_service import handle_status_update

    try:
        handle_status_update(tenant_id, data)
    except Exception as e:
        logger.error("Status webhook error: tenant=%s error=%s", tenant_id, e)

    return "", 204
