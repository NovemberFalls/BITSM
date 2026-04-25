"""SMS & WhatsApp messaging service via Twilio Messages API.

Channels:
  - SMS: Standard Twilio SMS messaging
  - WhatsApp: Twilio WhatsApp Business API (requires approved number)

Credential reuse: Shares Twilio credentials from phone_configs (same account).
Pattern: Mirrors phone_service.py — HMAC webhooks, encrypted creds, cost tracking.
"""

import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from config import Config
from models.db import execute, fetch_all, fetch_one, insert_returning

logger = logging.getLogger(__name__)

MESSAGING_DEFAULTS = {
    "auto_reply_enabled": False,
    "auto_reply_message": "",
    "auto_create_ticket": False,
    "default_language": "en",
}

# Twilio message status progression
TWILIO_STATUS_MAP = {
    "accepted": "queued",
    "queued": "queued",
    "sending": "queued",
    "sent": "sent",
    "delivered": "delivered",
    "read": "read",
    "undelivered": "failed",
    "failed": "failed",
}


# ─────────────────────────────────────────────────────────
# Webhook tokens (separate namespace from phone)
# ─────────────────────────────────────────────────────────

def make_messaging_webhook_token(tenant_id: int) -> str:
    """HMAC token for messaging webhook URLs — separate namespace from phone."""
    key = Config.WEBHOOK_HMAC_KEY or Config.SECRET_KEY
    secret = (key + str(tenant_id)).encode()
    return hmac.new(secret, f"messaging-{tenant_id}".encode(), hashlib.sha256).hexdigest()[:32]


def verify_messaging_webhook_token(tenant_id: int, token: str) -> bool:
    return hmac.compare_digest(make_messaging_webhook_token(tenant_id), token)


# ─────────────────────────────────────────────────────────
# Credential resolution (reuses phone_configs)
# ─────────────────────────────────────────────────────────

def _get_twilio_credentials(tenant_id: int) -> dict:
    """Get Twilio credentials from phone_configs (shared with phone system)."""
    from services.phone_service import get_effective_credentials
    creds = get_effective_credentials(tenant_id)
    return {
        "account_sid": creds.get("twilio_account_sid", ""),
        "auth_token": creds.get("twilio_auth_token", ""),
        "phone_number": creds.get("twilio_phone_number", ""),
    }


def _get_twilio_client(tenant_id: int):
    """Instantiate Twilio REST client for tenant."""
    from twilio.rest import Client as TwilioClient
    creds = _get_twilio_credentials(tenant_id)
    if not creds["account_sid"] or not creds["auth_token"]:
        raise ValueError("Twilio credentials not configured — set up phone integration first")
    return TwilioClient(creds["account_sid"], creds["auth_token"])


# ─────────────────────────────────────────────────────────
# Config CRUD
# ─────────────────────────────────────────────────────────

def get_messaging_config(tenant_id: int) -> dict:
    """Return messaging-specific config from phone_configs. None-safe."""
    row = fetch_one(
        """SELECT sms_enabled, whatsapp_enabled, whatsapp_phone_number,
                  whatsapp_status, messaging_auto_reply, messaging_auto_reply_msg,
                  messaging_auto_create_ticket, messaging_default_language,
                  assigned_phone_number, credentials_mode
           FROM phone_configs WHERE tenant_id = %s""",
        [tenant_id],
    )
    if not row:
        return {
            "configured": False,
            "sms_enabled": False,
            "whatsapp_enabled": False,
            "whatsapp_phone_number": None,
            "whatsapp_status": "not_configured",
            "auto_reply_enabled": False,
            "auto_reply_message": "",
            "auto_create_ticket": False,
            "default_language": "en",
            "sms_phone_number": None,
            "credentials_mode": "platform",
        }

    return {
        "configured": True,
        "sms_enabled": bool(row.get("sms_enabled")),
        "whatsapp_enabled": bool(row.get("whatsapp_enabled")),
        "whatsapp_phone_number": row.get("whatsapp_phone_number"),
        "whatsapp_status": row.get("whatsapp_status") or "not_configured",
        "auto_reply_enabled": bool(row.get("messaging_auto_reply")),
        "auto_reply_message": row.get("messaging_auto_reply_msg") or "",
        "auto_create_ticket": bool(row.get("messaging_auto_create_ticket")),
        "default_language": row.get("messaging_default_language") or "en",
        "sms_phone_number": row.get("assigned_phone_number"),
        "credentials_mode": row.get("credentials_mode") or "platform",
    }


def save_messaging_config(tenant_id: int, data: dict) -> dict:
    """Update messaging-specific columns in phone_configs. Creates row if missing."""
    existing = fetch_one(
        "SELECT id FROM phone_configs WHERE tenant_id = %s",
        [tenant_id],
    )

    fields = {
        "sms_enabled": bool(data.get("sms_enabled", False)),
        "whatsapp_enabled": bool(data.get("whatsapp_enabled", False)),
        "whatsapp_phone_number": data.get("whatsapp_phone_number") or None,
        "whatsapp_status": data.get("whatsapp_status") or "not_configured",
        "messaging_auto_reply": bool(data.get("auto_reply_enabled", False)),
        "messaging_auto_reply_msg": data.get("auto_reply_message") or None,
        "messaging_auto_create_ticket": bool(data.get("auto_create_ticket", False)),
        "messaging_default_language": data.get("default_language") or "en",
    }

    if existing:
        sets = ", ".join(f"{k} = %s" for k in fields)
        sets += ", updated_at = NOW()"
        execute(
            f"UPDATE phone_configs SET {sets} WHERE tenant_id = %s",
            list(fields.values()) + [tenant_id],
        )
    else:
        fields["tenant_id"] = tenant_id
        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["%s"] * len(fields))
        execute(
            f"INSERT INTO phone_configs ({cols}) VALUES ({placeholders})",
            list(fields.values()),
        )

    logger.info("Messaging config saved: tenant=%s sms=%s whatsapp=%s",
                tenant_id, fields["sms_enabled"], fields["whatsapp_enabled"])
    return get_messaging_config(tenant_id)


def check_messaging_access(tenant_id: int) -> dict:
    """Check if tenant can use messaging. Free = no, Paid = yes."""
    row = fetch_one(
        "SELECT plan_tier FROM tenants WHERE id = %s",
        [tenant_id],
    )
    tier = (row or {}).get("plan_tier", "free") or "free"
    if tier == "free":
        return {"allowed": False, "reason": "Messaging requires a paid plan."}
    return {"allowed": True, "tier": tier}


# ─────────────────────────────────────────────────────────
# Conversations
# ─────────────────────────────────────────────────────────

def list_conversations(
    tenant_id: int,
    channel: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    """List messaging conversations for tenant, newest first."""
    conditions = ["tenant_id = %s"]
    params = [tenant_id]

    if channel:
        conditions.append("channel = %s")
        params.append(channel)
    if status:
        conditions.append("status = %s")
        params.append(status)

    where = " AND ".join(conditions)
    params.extend([limit, offset])

    return fetch_all(
        f"""SELECT id, tenant_id, channel, contact_phone, contact_name,
                   contact_email, user_id, language, ticket_id, status,
                   last_message_at, last_inbound_at, message_count,
                   created_at, updated_at
            FROM messaging_conversations
            WHERE {where}
            ORDER BY last_message_at DESC NULLS LAST, created_at DESC
            LIMIT %s OFFSET %s""",
        params,
    )


def get_conversation(conversation_id: int, tenant_id: int) -> Optional[dict]:
    """Get a single conversation by ID."""
    return fetch_one(
        """SELECT id, tenant_id, channel, contact_phone, contact_name,
                  contact_email, user_id, language, ticket_id, status,
                  last_message_at, last_inbound_at, message_count,
                  created_at, updated_at
           FROM messaging_conversations
           WHERE id = %s AND tenant_id = %s""",
        [conversation_id, tenant_id],
    )


def find_or_create_conversation(
    tenant_id: int,
    channel: str,
    contact_phone: str,
    contact_name: Optional[str] = None,
    language: Optional[str] = None,
) -> dict:
    """Find existing conversation or create a new one."""
    existing = fetch_one(
        """SELECT id, tenant_id, channel, contact_phone, contact_name,
                  contact_email, user_id, language, ticket_id, status,
                  last_message_at, last_inbound_at, message_count,
                  created_at, updated_at
           FROM messaging_conversations
           WHERE tenant_id = %s AND channel = %s AND contact_phone = %s""",
        [tenant_id, channel, contact_phone],
    )
    if existing:
        # Reactivate if archived
        if existing["status"] == "archived":
            execute(
                "UPDATE messaging_conversations SET status = 'active', updated_at = NOW() WHERE id = %s",
                [existing["id"]],
            )
            existing["status"] = "active"
        return existing

    conv_id = insert_returning(
        """INSERT INTO messaging_conversations
               (tenant_id, channel, contact_phone, contact_name, language)
           VALUES (%s, %s, %s, %s, %s)
           RETURNING id""",
        [tenant_id, channel, contact_phone, contact_name, language or "en"],
    )
    logger.info("Conversation created: tenant=%s channel=%s phone=%s id=%s",
                tenant_id, channel, contact_phone, conv_id)
    return get_conversation(conv_id, tenant_id)


def update_conversation(conversation_id: int, tenant_id: int, data: dict) -> dict:
    """Update conversation fields (status, ticket_id, contact_name, language)."""
    allowed = {"status", "ticket_id", "contact_name", "contact_email", "language"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return get_conversation(conversation_id, tenant_id)

    sets = ", ".join(f"{k} = %s" for k in updates)
    sets += ", updated_at = NOW()"
    execute(
        f"UPDATE messaging_conversations SET {sets} WHERE id = %s AND tenant_id = %s",
        list(updates.values()) + [conversation_id, tenant_id],
    )
    return get_conversation(conversation_id, tenant_id)


# ─────────────────────────────────────────────────────────
# Messages
# ─────────────────────────────────────────────────────────

def list_messages(
    conversation_id: int,
    tenant_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list:
    """List messages in a conversation, oldest first."""
    return fetch_all(
        """SELECT id, conversation_id, tenant_id, direction, channel,
                  body, media_url, twilio_message_sid, status,
                  error_code, error_message, segments, cost_cents,
                  language, template_name, sender_user_id, created_at
           FROM messages
           WHERE conversation_id = %s AND tenant_id = %s
           ORDER BY created_at ASC
           LIMIT %s OFFSET %s""",
        [conversation_id, tenant_id, limit, offset],
    )


def send_message(
    tenant_id: int,
    conversation_id: int,
    body: str,
    sender_user_id: Optional[int] = None,
    template_name: Optional[str] = None,
    media_url: Optional[str] = None,
) -> dict:
    """Send an outbound SMS or WhatsApp message via Twilio."""
    conv = get_conversation(conversation_id, tenant_id)
    if not conv:
        raise ValueError("Conversation not found")

    channel = conv["channel"]
    contact_phone = conv["contact_phone"]
    creds = _get_twilio_credentials(tenant_id)
    from_number = creds["phone_number"]

    if not from_number:
        raise ValueError("No phone number configured — set up Twilio first")

    # WhatsApp 24h session window check
    if channel == "whatsapp" and not template_name:
        if conv.get("last_inbound_at"):
            last_inbound = conv["last_inbound_at"]
            if isinstance(last_inbound, str):
                last_inbound = datetime.fromisoformat(last_inbound)
            elapsed = (datetime.now(timezone.utc) - last_inbound).total_seconds()
            if elapsed > 86400:  # 24 hours
                raise ValueError(
                    "WhatsApp 24-hour session expired. Use a template message to re-engage."
                )

    # Gate on per-tenant channel toggles before any DB writes or Twilio calls.
    # sms_enabled / whatsapp_enabled are set to false in the DB during A2P
    # suspension or any other compliance hold.
    config = get_messaging_config(tenant_id)
    if channel == "sms" and not config.get("sms_enabled"):
        raise ValueError(
            "SMS messaging is currently disabled — A2P registration pending"
        )
    if channel == "whatsapp" and not config.get("whatsapp_enabled"):
        raise ValueError(
            "WhatsApp messaging is currently disabled — A2P registration pending"
        )

    # Format numbers for Twilio
    if channel == "whatsapp":
        wa_number = config.get("whatsapp_phone_number") or from_number
        to_addr = f"whatsapp:{contact_phone}"
        from_addr = f"whatsapp:{wa_number}"
    else:
        to_addr = contact_phone
        from_addr = from_number

    # Save message to DB first (status=queued)
    msg_id = insert_returning(
        """INSERT INTO messages
               (conversation_id, tenant_id, direction, channel, body,
                media_url, status, language, template_name, sender_user_id)
           VALUES (%s, %s, 'outbound', %s, %s,
                   %s, 'queued', %s, %s, %s)
           RETURNING id""",
        [
            conversation_id, tenant_id, channel, body,
            media_url, conv.get("language"), template_name, sender_user_id,
        ],
    )

    # Send via Twilio in background thread
    def _send():
        try:
            client = _get_twilio_client(tenant_id)
            kwargs = {
                "body": body,
                "from_": from_addr,
                "to": to_addr,
            }

            # Status callback for delivery tracking
            token = make_messaging_webhook_token(tenant_id)
            base = Config.APP_URL.rstrip("/")
            kwargs["status_callback"] = f"{base}/api/messaging/webhook/{tenant_id}/status?t={token}"

            if media_url:
                kwargs["media_url"] = [media_url]

            tw_msg = client.messages.create(**kwargs)

            execute(
                """UPDATE messages
                   SET twilio_message_sid = %s, status = 'sent',
                       segments = %s, updated_at = NOW()
                   WHERE id = %s""",
                [tw_msg.sid, tw_msg.num_segments or 1, msg_id],
            )

            # Update conversation timestamps
            execute(
                """UPDATE messaging_conversations
                   SET last_message_at = NOW(), message_count = message_count + 1,
                       updated_at = NOW()
                   WHERE id = %s""",
                [conversation_id],
            )

            logger.info("Message sent: tenant=%s conv=%s channel=%s sid=%s",
                        tenant_id, conversation_id, channel, tw_msg.sid)

        except Exception as e:
            logger.error("Message send failed: tenant=%s conv=%s error=%s",
                         tenant_id, conversation_id, e)
            execute(
                """UPDATE messages
                   SET status = 'failed', error_message = %s
                   WHERE id = %s""",
                [str(e)[:500], msg_id],
            )

    threading.Thread(target=_send, daemon=True).start()

    return {
        "id": msg_id,
        "conversation_id": conversation_id,
        "channel": channel,
        "status": "queued",
        "body": body,
    }


def handle_inbound_message(tenant_id: int, data: dict) -> dict:
    """Process an inbound SMS/WhatsApp message from Twilio webhook.

    data contains Twilio webhook params:
      From, To, Body, MessageSid, NumMedia, MediaUrl0, etc.
    """
    from_number = data.get("From", "")
    body = data.get("Body", "")
    message_sid = data.get("MessageSid", "")
    num_media = int(data.get("NumMedia", 0))
    media_url = data.get("MediaUrl0") if num_media > 0 else None

    # Determine channel from phone number format
    if from_number.startswith("whatsapp:"):
        channel = "whatsapp"
        contact_phone = from_number.replace("whatsapp:", "")
    else:
        channel = "sms"
        contact_phone = from_number

    # Check if channel is enabled
    config = get_messaging_config(tenant_id)
    if channel == "sms" and not config.get("sms_enabled"):
        logger.warning("SMS not enabled for tenant=%s, ignoring inbound", tenant_id)
        return {"ignored": True, "reason": "SMS not enabled"}
    if channel == "whatsapp" and not config.get("whatsapp_enabled"):
        logger.warning("WhatsApp not enabled for tenant=%s, ignoring inbound", tenant_id)
        return {"ignored": True, "reason": "WhatsApp not enabled"}

    # Find or create conversation
    conv = find_or_create_conversation(tenant_id, channel, contact_phone)

    # Save inbound message
    msg_id = insert_returning(
        """INSERT INTO messages
               (conversation_id, tenant_id, direction, channel, body,
                media_url, twilio_message_sid, status, language)
           VALUES (%s, %s, 'inbound', %s, %s,
                   %s, %s, 'received', %s)
           RETURNING id""",
        [
            conv["id"], tenant_id, channel, body,
            media_url, message_sid, config.get("default_language", "en"),
        ],
    )

    # Update conversation
    execute(
        """UPDATE messaging_conversations
           SET last_message_at = NOW(), last_inbound_at = NOW(),
               message_count = message_count + 1, updated_at = NOW()
           WHERE id = %s""",
        [conv["id"]],
    )

    logger.info("Inbound message: tenant=%s channel=%s phone=%s conv=%s msg=%s",
                tenant_id, channel, contact_phone, conv["id"], msg_id)

    result = {
        "message_id": msg_id,
        "conversation_id": conv["id"],
        "channel": channel,
        "contact_phone": contact_phone,
    }

    # Auto-reply if enabled
    if config.get("auto_reply_enabled") and config.get("auto_reply_message"):
        try:
            send_message(
                tenant_id=tenant_id,
                conversation_id=conv["id"],
                body=config["auto_reply_message"],
            )
            result["auto_replied"] = True
        except Exception as e:
            logger.warning("Auto-reply failed: tenant=%s conv=%s error=%s",
                           tenant_id, conv["id"], e)

    # Auto-create ticket if enabled
    if config.get("auto_create_ticket"):
        _auto_create_ticket(tenant_id, conv, body, channel)
        result["ticket_created"] = True

    return result


def handle_status_update(tenant_id: int, data: dict) -> None:
    """Process Twilio message status callback (delivered, read, failed, etc.)."""
    message_sid = data.get("MessageSid", "")
    status_raw = data.get("MessageStatus", "")
    error_code = data.get("ErrorCode")
    error_message = data.get("ErrorMessage")

    status = TWILIO_STATUS_MAP.get(status_raw, status_raw)

    updates = ["status = %s"]
    params = [status]

    if error_code:
        updates.append("error_code = %s")
        params.append(str(error_code))
    if error_message:
        updates.append("error_message = %s")
        params.append(error_message[:500])

    # Twilio provides price info on terminal statuses
    price = data.get("Price")
    if price:
        try:
            cost_cents = abs(float(price)) * 100
            updates.append("cost_cents = %s")
            params.append(cost_cents)
        except (ValueError, TypeError):
            pass

    segments = data.get("NumSegments")
    if segments:
        updates.append("segments = %s")
        params.append(int(segments))

    params.append(message_sid)
    execute(
        f"UPDATE messages SET {', '.join(updates)} WHERE twilio_message_sid = %s",
        params,
    )

    logger.debug("Message status update: sid=%s status=%s", message_sid, status)


# ─────────────────────────────────────────────────────────
# Templates (WhatsApp)
# ─────────────────────────────────────────────────────────

def list_templates(tenant_id: int, language: Optional[str] = None) -> list:
    """List WhatsApp message templates for tenant."""
    if language:
        return fetch_all(
            """SELECT id, tenant_id, name, language, body, category,
                      status, twilio_template_sid, variables,
                      created_at, updated_at
               FROM messaging_templates
               WHERE tenant_id = %s AND language = %s
               ORDER BY name""",
            [tenant_id, language],
        )
    return fetch_all(
        """SELECT id, tenant_id, name, language, body, category,
                  status, twilio_template_sid, variables,
                  created_at, updated_at
           FROM messaging_templates
           WHERE tenant_id = %s
           ORDER BY name, language""",
        [tenant_id],
    )


def create_template(tenant_id: int, data: dict) -> dict:
    """Create a new WhatsApp message template."""
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Template name is required")

    body = (data.get("body") or "").strip()
    if not body:
        raise ValueError("Template body is required")

    language = data.get("language", "en")
    category = data.get("category", "utility")
    variables = json.dumps(data.get("variables", []))

    template_id = insert_returning(
        """INSERT INTO messaging_templates
               (tenant_id, name, language, body, category, variables)
           VALUES (%s, %s, %s, %s, %s, %s)
           RETURNING id""",
        [tenant_id, name, language, body, category, variables],
    )

    logger.info("Template created: tenant=%s name=%s lang=%s id=%s",
                tenant_id, name, language, template_id)
    return get_template(template_id, tenant_id)


def get_template(template_id: int, tenant_id: int) -> Optional[dict]:
    """Get a single template by ID."""
    return fetch_one(
        """SELECT id, tenant_id, name, language, body, category,
                  status, twilio_template_sid, variables,
                  created_at, updated_at
           FROM messaging_templates
           WHERE id = %s AND tenant_id = %s""",
        [template_id, tenant_id],
    )


def update_template(template_id: int, tenant_id: int, data: dict) -> dict:
    """Update a template."""
    allowed = {"name", "body", "language", "category", "variables", "status"}
    updates = {}
    for k in allowed:
        if k in data:
            val = data[k]
            if k == "variables":
                val = json.dumps(val) if isinstance(val, list) else val
            updates[k] = val

    if not updates:
        return get_template(template_id, tenant_id)

    sets = ", ".join(f"{k} = %s" for k in updates)
    sets += ", updated_at = NOW()"
    execute(
        f"UPDATE messaging_templates SET {sets} WHERE id = %s AND tenant_id = %s",
        list(updates.values()) + [template_id, tenant_id],
    )
    return get_template(template_id, tenant_id)


def delete_template(template_id: int, tenant_id: int) -> bool:
    """Delete a template."""
    execute(
        "DELETE FROM messaging_templates WHERE id = %s AND tenant_id = %s",
        [template_id, tenant_id],
    )
    logger.info("Template deleted: tenant=%s id=%s", tenant_id, template_id)
    return True


# ─────────────────────────────────────────────────────────
# Auto-ticket creation from inbound messages
# ─────────────────────────────────────────────────────────

def _auto_create_ticket(tenant_id: int, conv: dict, body: str, channel: str) -> None:
    """Create a helpdesk ticket from an inbound message (background thread)."""
    if conv.get("ticket_id"):
        return  # Already linked

    def _create():
        try:
            channel_label = "WhatsApp" if channel == "whatsapp" else "SMS"
            title = f"{channel_label} from {conv['contact_phone']}"
            if body:
                title = f"{channel_label}: {body[:80]}"

            ticket_id = insert_returning(
                """INSERT INTO tickets
                       (tenant_id, title, description, status, priority,
                        source, created_at, updated_at)
                   VALUES (%s, %s, %s, 'open', 'p3',
                           %s, NOW(), NOW())
                   RETURNING id""",
                [tenant_id, title, body or "(no message body)", channel],
            )

            execute(
                "UPDATE messaging_conversations SET ticket_id = %s, updated_at = NOW() WHERE id = %s",
                [ticket_id, conv["id"]],
            )

            logger.info("Ticket auto-created: tenant=%s conv=%s ticket=%s",
                        tenant_id, conv["id"], ticket_id)
        except Exception as e:
            logger.error("Auto-create ticket failed: tenant=%s conv=%s error=%s",
                         tenant_id, conv["id"], e)

    threading.Thread(target=_create, daemon=True).start()


# ─────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────

def get_messaging_stats(tenant_id: int) -> dict:
    """Return messaging stats for dashboard display."""
    row = fetch_one(
        """SELECT
               COUNT(*) FILTER (WHERE status = 'active') AS active_conversations,
               COUNT(*) AS total_conversations
           FROM messaging_conversations
           WHERE tenant_id = %s""",
        [tenant_id],
    )

    msg_row = fetch_one(
        """SELECT
               COUNT(*) FILTER (WHERE direction = 'inbound') AS inbound_count,
               COUNT(*) FILTER (WHERE direction = 'outbound') AS outbound_count,
               COALESCE(SUM(cost_cents), 0) AS total_cost_cents,
               COALESCE(SUM(segments), 0) AS total_segments
           FROM messages
           WHERE tenant_id = %s
             AND created_at >= NOW() - INTERVAL '30 days'""",
        [tenant_id],
    )

    return {
        "active_conversations": (row or {}).get("active_conversations", 0),
        "total_conversations": (row or {}).get("total_conversations", 0),
        "inbound_30d": (msg_row or {}).get("inbound_count", 0),
        "outbound_30d": (msg_row or {}).get("outbound_count", 0),
        "total_cost_cents_30d": float((msg_row or {}).get("total_cost_cents", 0)),
        "total_segments_30d": (msg_row or {}).get("total_segments", 0),
    }
