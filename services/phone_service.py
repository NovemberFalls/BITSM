"""Phone helpdesk service: ElevenLabs Conversational AI + Twilio integration.

Flow per call:
  1. Caller dials tenant's Twilio number
  2. ElevenLabs (linked via our provisioning) handles TTS/STT with Atlas persona
  3. Atlas calls tool webhooks on this backend: search_kb, create_ticket,
     attempt_transfer, collect_email
  4. attempt_transfer: makes a real outbound Twilio call to oncall_number,
     polls for answer up to 30 s, bridges if answered, else graceful fallback
  5. ElevenLabs fires post_call_webhook when call ends → we save transcript
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from config import Config
from models.db import execute, fetch_all, fetch_one, insert_returning

logger = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"
TRANSFER_TIMEOUT_SECONDS = 30
TRANSFER_POLL_INTERVAL = 2  # seconds between status polls

# Canonical defaults — single source of truth for all AI/audio settings.
# NULL columns in phone_configs gracefully fall back to these via `or` pattern.
PHONE_DEFAULTS = {
    "llm_model":        "claude-haiku-4-5@20251001",
    "temperature":      0.4,
    "tts_speed":        1.15,
    "turn_timeout":     10.0,
    "audio_format":     "ulaw_8000",
    "voice_id":         "mcuuWJIofmzgKEGk3EMA",
    "agent_name":       "Atlas",
    "greeting_message": "",
}


# ─────────────────────────────────────────────────────────
# Credential helpers
# ─────────────────────────────────────────────────────────

def _encrypt(data: dict) -> str:
    if not Config.FERNET_KEY:
        raise RuntimeError("FERNET_KEY not configured")
    from cryptography.fernet import Fernet
    return Fernet(Config.FERNET_KEY.encode()).encrypt(json.dumps(data).encode()).decode()


def _decrypt(encrypted: str) -> dict:
    if not Config.FERNET_KEY:
        raise RuntimeError("FERNET_KEY not configured")
    from cryptography.fernet import Fernet
    return json.loads(Fernet(Config.FERNET_KEY.encode()).decrypt(encrypted.encode()).decode())


def make_webhook_token(tenant_id: int) -> str:
    """HMAC token embedded in tool webhook URLs — prevents unauthenticated calls."""
    key = Config.WEBHOOK_HMAC_KEY or Config.SECRET_KEY
    secret = (key + str(tenant_id)).encode()
    return hmac.new(secret, f"phone-tool-{tenant_id}".encode(), hashlib.sha256).hexdigest()[:32]


def verify_webhook_token(tenant_id: int, token: str) -> bool:
    return hmac.compare_digest(make_webhook_token(tenant_id), token)


# ─────────────────────────────────────────────────────────
# Config CRUD
# ─────────────────────────────────────────────────────────

def get_phone_config(tenant_id: int) -> Optional[dict]:
    """Return phone config for tenant (credentials masked). None if unconfigured."""
    row = fetch_one(
        """SELECT id, tenant_id, is_active,
                  credentials_encrypted, credentials_mode,
                  assigned_phone_number, platform_twilio_number_sid,
                  elevenlabs_agent_id, elevenlabs_phone_number_id,
                  el_agent_id_es, voice_id_es, agent_name_es,
                  voice_id, agent_name, greeting_message, oncall_number,
                  tts_speed, ivr_enabled,
                  llm_model, temperature, turn_timeout, audio_format,
                  ivr_greeting_en, ivr_greeting_es,
                  created_at, updated_at
           FROM phone_configs WHERE tenant_id = %s""",
        [tenant_id],
    )
    if not row:
        return None

    result = dict(row)

    # Mask BYOK credentials — never return raw secrets
    creds_set = {"twilio_account_sid": "", "twilio_phone_number": "",
                 "elevenlabs_api_key_set": False, "twilio_auth_token_set": False}
    if result.get("credentials_encrypted"):
        try:
            creds = _decrypt(result["credentials_encrypted"])
            creds_set["twilio_account_sid"]     = creds.get("twilio_account_sid", "")
            creds_set["twilio_phone_number"]    = creds.get("twilio_phone_number", "")
            creds_set["elevenlabs_api_key_set"] = bool(creds.get("elevenlabs_api_key"))
            creds_set["twilio_auth_token_set"]  = bool(creds.get("twilio_auth_token"))
        except Exception:
            pass

    del result["credentials_encrypted"]
    del result["platform_twilio_number_sid"]  # internal — no need to expose
    result.update(creds_set)

    # Expose the effective phone number (DB value or env fallback)
    if not result.get("assigned_phone_number"):
        result["effective_phone_number"] = Config.DEV_TWILIO_PHONE_NUMBER or None
    else:
        result["effective_phone_number"] = result["assigned_phone_number"]

    return result


def save_phone_config(tenant_id: int, data: dict) -> dict:
    """Upsert phone config. Credentials are merged so partial updates work."""
    existing = fetch_one(
        "SELECT id, credentials_encrypted FROM phone_configs WHERE tenant_id = %s",
        [tenant_id],
    )

    # Merge credentials — only overwrite keys that are explicitly provided
    creds: dict = {}
    if existing and existing.get("credentials_encrypted"):
        try:
            creds = _decrypt(existing["credentials_encrypted"])
        except Exception:
            pass

    for key in ("twilio_account_sid", "twilio_auth_token",
                "twilio_phone_number", "elevenlabs_api_key"):
        if data.get(key):
            creds[key] = data[key]

    encrypted = _encrypt(creds) if creds else None

    scalar = {
        "is_active":        bool(data.get("is_active", False)),
        "voice_id":         data.get("voice_id") or PHONE_DEFAULTS["voice_id"],
        "agent_name":       data.get("agent_name") or PHONE_DEFAULTS["agent_name"],
        "greeting_message": data.get("greeting_message") or None,
        "oncall_number":    data.get("oncall_number") or None,
        "credentials_mode": data.get("credentials_mode") or "platform",
        "tts_speed":        float(data["tts_speed"]) if data.get("tts_speed") is not None else None,
        "llm_model":        data.get("llm_model") or None,
        "temperature":      float(data["temperature"]) if data.get("temperature") is not None else None,
        "turn_timeout":     float(data["turn_timeout"]) if data.get("turn_timeout") is not None else None,
        "audio_format":     data.get("audio_format") or None,
        # ivr_greeting_en/es removed — IVR greetings are now per-agent (phone_agents.ivr_greeting)
    }

    if existing:
        sets = ", ".join(f"{k} = %s" for k in scalar)
        values = list(scalar.values())
        if encrypted:
            sets += ", credentials_encrypted = %s"
            values.append(encrypted)
        sets += ", updated_at = NOW()"
        execute(f"UPDATE phone_configs SET {sets} WHERE tenant_id = %s", values + [tenant_id])
    else:
        cols = list(scalar.keys())
        vals = list(scalar.values())
        if encrypted:
            cols.append("credentials_encrypted")
            vals.append(encrypted)
        cols.append("tenant_id")
        vals.append(tenant_id)
        placeholders = ", ".join(["%s"] * len(vals))
        execute(
            f"INSERT INTO phone_configs ({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )

    return get_phone_config(tenant_id)


def _get_raw_creds(tenant_id: int) -> dict:
    """Return decrypted BYOK credentials. Raises if unconfigured."""
    row = fetch_one(
        "SELECT credentials_encrypted FROM phone_configs WHERE tenant_id = %s",
        [tenant_id],
    )
    if not row or not row.get("credentials_encrypted"):
        raise ValueError(f"Phone not configured for tenant {tenant_id}")
    return _decrypt(row["credentials_encrypted"])


def get_effective_credentials(tenant_id: int) -> dict:
    """Return active credentials — platform-supplied or tenant BYOK.

    Resolution order:
      1. DEMO_MODE: billing BYOK keys (unified key management for demo tenants)
      2. phone_configs BYOK (credentials_mode='byok' with encrypted creds)
      3. Platform env vars (PLATFORM_* with DEV_* fallback for local dev)
    """
    row = fetch_one(
        "SELECT credentials_encrypted, credentials_mode, assigned_phone_number FROM phone_configs WHERE tenant_id = %s",
        [tenant_id],
    )
    mode = (row.get("credentials_mode") if row else None) or "platform"

    # In DEMO_MODE, billing BYOK keys take precedence over phone_configs.
    # This lets demo tenants manage all their provider credentials in one place
    # (Admin → Billing → API Keys) without needing a separate phone_configs entry.
    if Config.DEMO_MODE:
        try:
            from services.billing_service import get_byok_keys
            byok = get_byok_keys(tenant_id)
            if byok:
                el_key  = byok.get("elevenlabs")
                tw_sid  = byok.get("twilio_account_sid")
                tw_tok  = byok.get("twilio_auth_token")
                tw_phone = byok.get("twilio_phone_number")
                if el_key and tw_sid and tw_tok:
                    return {
                        "elevenlabs_api_key":  el_key,
                        "twilio_account_sid":  tw_sid,
                        "twilio_auth_token":   tw_tok,
                        "twilio_phone_number": tw_phone,
                    }
        except Exception:
            pass  # Fall through to existing resolution

    if mode == "byok" and row and row.get("credentials_encrypted"):
        try:
            return _decrypt(row["credentials_encrypted"])
        except Exception:
            pass  # Fall through to platform

    # Platform mode — use PLATFORM_* or fall back to DEV_* for local dev
    el_key   = Config.PLATFORM_ELEVENLABS_API_KEY or Config.DEV_ELEVENLABS_API_KEY
    tw_sid   = Config.PLATFORM_TWILIO_ACCOUNT_SID or Config.DEV_TWILIO_ACCOUNT_SID
    tw_tok   = Config.PLATFORM_TWILIO_AUTH_TOKEN  or Config.DEV_TWILIO_AUTH_TOKEN
    tw_phone = (row or {}).get("assigned_phone_number") or Config.DEV_TWILIO_PHONE_NUMBER or None

    return {
        "elevenlabs_api_key":  el_key,
        "twilio_account_sid":  tw_sid,
        "twilio_auth_token":   tw_tok,
        "twilio_phone_number": tw_phone,
    }


# ─────────────────────────────────────────────────────────
# Plan Tier Gating
# ─────────────────────────────────────────────────────────

def check_phone_access(tenant_id: int) -> dict:
    """Check if tenant can use phone agents. Free = 0, Paid = unlimited."""
    row = fetch_one(
        "SELECT plan_tier FROM tenants WHERE id = %s",
        [tenant_id],
    )
    tier = (row or {}).get("plan_tier", "free") or "free"
    if tier == "free":
        return {"allowed": False, "reason": "Phone agents require a paid plan."}
    return {"allowed": True, "tier": tier}


# ─────────────────────────────────────────────────────────
# Phone Agent CRUD
# ─────────────────────────────────────────────────────────

def _get_tenant_slug(tenant_id: int) -> str:
    row = fetch_one("SELECT slug FROM tenants WHERE id = %s", [tenant_id])
    return (row or {}).get("slug", str(tenant_id))


def list_phone_agents(tenant_id: int) -> list:
    """List all phone agents for a tenant, ordered by sort_order."""
    return fetch_all(
        """SELECT id, tenant_id, slug, name, language,
                  el_agent_id, voice_id, greeting_message, ivr_greeting,
                  system_prompt IS NOT NULL AS has_custom_prompt,
                  llm_model, temperature, turn_timeout, audio_format, tts_speed,
                  ivr_digit, oncall_number,
                  is_active, is_deployed, is_number_linked,
                  tools_enabled, sort_order,
                  created_at, updated_at
           FROM phone_agents
           WHERE tenant_id = %s
           ORDER BY sort_order, created_at""",
        [tenant_id],
    )


def get_phone_agent(agent_id: int, tenant_id: int) -> Optional[dict]:
    """Get a single phone agent with full details including system_prompt."""
    row = fetch_one(
        """SELECT * FROM phone_agents
           WHERE id = %s AND tenant_id = %s""",
        [agent_id, tenant_id],
    )
    return dict(row) if row else None


def create_phone_agent(tenant_id: int, data: dict) -> dict:
    """Create a new phone agent. Returns the created agent."""
    import re

    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Agent name is required")

    # Generate slug from name
    slug = data.get("slug") or re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    if not slug:
        slug = f"agent-{int(time.time())}"

    # Check uniqueness
    existing = fetch_one(
        "SELECT id FROM phone_agents WHERE tenant_id = %s AND slug = %s",
        [tenant_id, slug],
    )
    if existing:
        raise ValueError(f"An agent with slug '{slug}' already exists")

    # Get next sort_order
    max_row = fetch_one(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM phone_agents WHERE tenant_id = %s",
        [tenant_id],
    )
    next_order = max_row["next_order"] if max_row else 0

    language = data.get("language", "en")
    default_voice = PHONE_DEFAULTS["voice_id"] if language == "en" else "f18RlRJGEw0TaGYwmk8B"

    agent_id = insert_returning(
        """INSERT INTO phone_agents
               (tenant_id, slug, name, language, voice_id,
                greeting_message, system_prompt, ivr_greeting,
                llm_model, temperature, turn_timeout, audio_format, tts_speed,
                ivr_digit, oncall_number, tools_enabled, sort_order)
           VALUES (%s, %s, %s, %s, %s,
                   %s, %s, %s,
                   %s, %s, %s, %s, %s,
                   %s, %s, %s, %s)
           RETURNING id""",
        [
            tenant_id, slug, name, language, data.get("voice_id") or default_voice,
            data.get("greeting_message") or None, data.get("system_prompt") or None,
            data.get("ivr_greeting") or None,
            data.get("llm_model") or None,
            float(data["temperature"]) if data.get("temperature") is not None else None,
            float(data["turn_timeout"]) if data.get("turn_timeout") is not None else None,
            data.get("audio_format") or None,
            float(data["tts_speed"]) if data.get("tts_speed") is not None else None,
            data.get("ivr_digit") or None, data.get("oncall_number") or None,
            data.get("tools_enabled") or ['search_kb', 'create_ticket', 'identify_caller', 'attempt_transfer', 'collect_email'],
            next_order,
        ],
    )
    logger.info("Phone agent created: tenant=%s agent_id=%s slug=%s", tenant_id, agent_id, slug)
    return get_phone_agent(agent_id, tenant_id)


def update_phone_agent(agent_id: int, tenant_id: int, data: dict) -> dict:
    """Update a phone agent's settings. Returns updated agent."""
    existing = get_phone_agent(agent_id, tenant_id)
    if not existing:
        raise ValueError("Agent not found")

    allowed_fields = {
        "name", "voice_id", "greeting_message", "system_prompt", "ivr_greeting",
        "llm_model", "temperature", "turn_timeout", "audio_format", "tts_speed",
        "ivr_digit", "oncall_number", "tools_enabled", "sort_order",
    }

    updates = {}
    for key in allowed_fields:
        if key in data:
            val = data[key]
            if key in ("temperature", "turn_timeout", "tts_speed") and val is not None:
                val = float(val)
            updates[key] = val

    if not updates:
        return existing

    sets = ", ".join(f"{k} = %s" for k in updates)
    sets += ", updated_at = NOW()"
    values = list(updates.values()) + [agent_id, tenant_id]
    execute(
        f"UPDATE phone_agents SET {sets} WHERE id = %s AND tenant_id = %s",
        values,
    )
    logger.info("Phone agent updated: tenant=%s agent_id=%s fields=%s", tenant_id, agent_id, list(updates.keys()))
    return get_phone_agent(agent_id, tenant_id)


def delete_phone_agent(agent_id: int, tenant_id: int) -> bool:
    """Delete a phone agent. Deprovisions from ElevenLabs if deployed."""
    agent = get_phone_agent(agent_id, tenant_id)
    if not agent:
        raise ValueError("Agent not found")

    # Deprovision from ElevenLabs if deployed
    if agent.get("el_agent_id"):
        try:
            creds = get_effective_credentials(tenant_id)
            api_key = creds.get("elevenlabs_api_key")
            if api_key:
                requests.delete(
                    f"{ELEVENLABS_BASE}/convai/agents/{agent['el_agent_id']}",
                    headers=_el_headers(api_key),
                    timeout=15,
                )
                logger.info("Deprovisioned EL agent %s for phone_agent %s", agent["el_agent_id"], agent_id)
        except Exception as e:
            logger.warning("Failed to deprovision EL agent %s: %s", agent.get("el_agent_id"), e)

    # Unlink sessions (set phone_agent_id to NULL)
    execute("UPDATE phone_sessions SET phone_agent_id = NULL WHERE phone_agent_id = %s", [agent_id])

    execute("DELETE FROM phone_agents WHERE id = %s AND tenant_id = %s", [agent_id, tenant_id])
    logger.info("Phone agent deleted: tenant=%s agent_id=%s slug=%s", tenant_id, agent_id, agent.get("slug"))
    return True


def reset_agent_to_defaults(agent_id: int, tenant_id: int) -> dict:
    """Reset an agent's AI/voice settings to platform defaults."""
    agent = get_phone_agent(agent_id, tenant_id)
    if not agent:
        raise ValueError("Agent not found")

    execute(
        """UPDATE phone_agents SET
               voice_id = %s, llm_model = NULL, temperature = NULL,
               turn_timeout = NULL, audio_format = NULL, tts_speed = NULL,
               system_prompt = NULL, greeting_message = NULL,
               updated_at = NOW()
           WHERE id = %s AND tenant_id = %s""",
        [
            PHONE_DEFAULTS["voice_id"] if agent["language"] == "en" else "f18RlRJGEw0TaGYwmk8B",
            agent_id, tenant_id,
        ],
    )
    logger.info("Phone agent reset to defaults: tenant=%s agent_id=%s", tenant_id, agent_id)
    return get_phone_agent(agent_id, tenant_id)


def get_default_system_prompt(language: str, tenant_name: str, agent_name: str) -> str:
    """Return the platform default system prompt for preview/reference."""
    if language == "es":
        return _astra_system_prompt(tenant_name, agent_name)
    return _atlas_system_prompt(tenant_name, agent_name)


# ─────────────────────────────────────────────────────────
# Agent Deployment (ElevenLabs provisioning per-agent)
# ─────────────────────────────────────────────────────────

def deploy_agent(agent_id: int, tenant_id: int) -> dict:
    """Deploy a phone agent to ElevenLabs — creates or updates the ConvAI agent.

    Reads all settings from the phone_agents row.  Uses the agent's system_prompt
    if set, otherwise falls back to the platform default template.
    """
    agent = get_phone_agent(agent_id, tenant_id)
    if not agent:
        raise ValueError("Agent not found")

    creds = get_effective_credentials(tenant_id)
    api_key = creds.get("elevenlabs_api_key")
    if not api_key:
        raise ValueError("ElevenLabs API key not configured")

    tenant = fetch_one("SELECT name, slug FROM tenants WHERE id = %s", [tenant_id])
    tenant_name = (tenant or {}).get("name", f"Tenant {tenant_id}")
    tenant_slug = (tenant or {}).get("slug", str(tenant_id))

    base_url     = Config.APP_URL.rstrip("/")
    agent_name   = agent.get("name") or PHONE_DEFAULTS["agent_name"]
    language     = agent.get("language") or "en"
    voice_id     = agent.get("voice_id") or PHONE_DEFAULTS["voice_id"]
    tts_speed    = float(agent.get("tts_speed") or PHONE_DEFAULTS["tts_speed"])
    llm_model    = agent.get("llm_model") or PHONE_DEFAULTS["llm_model"]
    temperature  = float(agent.get("temperature") or PHONE_DEFAULTS["temperature"])
    turn_timeout = float(agent.get("turn_timeout") or PHONE_DEFAULTS["turn_timeout"])
    audio_format = agent.get("audio_format") or PHONE_DEFAULTS["audio_format"]

    # System prompt: custom override or platform default
    if agent.get("system_prompt"):
        prompt_text = agent["system_prompt"]
    elif language == "es":
        prompt_text = _astra_system_prompt(tenant_name, agent_name)
    else:
        prompt_text = _atlas_system_prompt(tenant_name, agent_name)

    # Greeting
    if agent.get("greeting_message"):
        first_msg = agent["greeting_message"]
    elif language == "es":
        first_msg = (
            f"¡Hola! Soy {agent_name}, tu especialista de soporte de I.T. para {tenant_name}. "
            "¿En qué puedo ayudarte hoy?"
        )
    else:
        first_msg = f"Hello! This is {agent_name}, your I.T. support specialist for {tenant_name}. How can I help you today?"

    post_call_wh_id = _ensure_post_call_webhook(tenant_id, base_url, api_key)

    platform_settings: dict = {"auth": {"enable_auth": False}}
    if post_call_wh_id:
        platform_settings["workspace_overrides"] = {
            "webhooks": {"post_call_webhook_id": post_call_wh_id}
        }

    payload = {
        "name": f"{agent_name} {language.upper()} — {tenant_name}",
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt":      prompt_text,
                    "llm":         llm_model,
                    "temperature": temperature,
                    "tools":       _build_tools(tenant_id, base_url, tenant_slug),
                },
                "first_message": first_msg,
                "language":      language,
            },
            "tts": {
                "voice_id": voice_id,
                "speed":    tts_speed,
                "agent_output_audio_format": audio_format,
            },
            "asr": {"user_input_audio_format": audio_format},
            "turn": {"turn_timeout": turn_timeout, "turn_eagerness": "patient"},
        },
        "platform_settings": platform_settings,
    }

    if language == "es":
        payload["conversation_config"]["tts"]["model_id"] = "eleven_turbo_v2_5"

    existing_el_id = agent.get("el_agent_id")

    if existing_el_id:
        r = requests.patch(
            f"{ELEVENLABS_BASE}/convai/agents/{existing_el_id}",
            json=payload,
            headers=_el_headers(api_key),
            timeout=30,
        )
        if r.status_code == 404:
            # Agent was deleted from EL workspace — create a new one
            logger.warning("EL agent %s not found (deleted?), creating fresh agent", existing_el_id)
            r = requests.post(
                f"{ELEVENLABS_BASE}/convai/agents/create",
                json=payload,
                headers=_el_headers(api_key),
                timeout=30,
            )
        r.raise_for_status()
        el_agent_id = r.json().get("agent_id") or r.json().get("id") or existing_el_id
    else:
        r = requests.post(
            f"{ELEVENLABS_BASE}/convai/agents/create",
            json=payload,
            headers=_el_headers(api_key),
            timeout=30,
        )
        r.raise_for_status()
        el_agent_id = r.json().get("agent_id") or r.json().get("id")

    execute(
        """UPDATE phone_agents
           SET el_agent_id = %s, is_deployed = TRUE, updated_at = NOW()
           WHERE id = %s AND tenant_id = %s""",
        [el_agent_id, agent_id, tenant_id],
    )
    logger.info("Phone agent deployed: tenant=%s agent_id=%s el_id=%s", tenant_id, agent_id, el_agent_id)
    return {"agent_id": agent_id, "el_agent_id": el_agent_id, "status": "deployed"}


def activate_agent(agent_id: int, tenant_id: int) -> dict:
    """Link Twilio number to this agent and set it active.

    The Twilio number is shared at the tenant level (from phone_configs).
    Multiple agents share the number — IVR routing picks the right one.
    """
    agent = get_phone_agent(agent_id, tenant_id)
    if not agent:
        raise ValueError("Agent not found")
    if not agent.get("el_agent_id"):
        raise ValueError("Agent not deployed yet — deploy first")

    # Ensure the Twilio number's voice webhook points to our IVR.
    # Idempotent — safe to call on every activation.
    link_twilio_number(tenant_id)

    execute(
        """UPDATE phone_agents
           SET is_active = TRUE, is_number_linked = TRUE, updated_at = NOW()
           WHERE id = %s AND tenant_id = %s""",
        [agent_id, tenant_id],
    )
    logger.info("Phone agent activated: tenant=%s agent_id=%s", tenant_id, agent_id)
    return {"agent_id": agent_id, "status": "active"}


def get_agents_for_ivr(tenant_id: int) -> list:
    """Return active+deployed agents ordered by ivr_digit for TwiML composition."""
    return fetch_all(
        """SELECT id, el_agent_id, name, language, ivr_digit, ivr_greeting
           FROM phone_agents
           WHERE tenant_id = %s AND is_active = TRUE AND is_deployed = TRUE
                 AND ivr_digit IS NOT NULL AND ivr_digit != ''
           ORDER BY ivr_digit""",
        [tenant_id],
    )


def get_agent_for_ivr(tenant_id: int, digit: str) -> Optional[dict]:
    """Look up which phone agent handles a given IVR digit."""
    agent = fetch_one(
        """SELECT id, el_agent_id, name, language
           FROM phone_agents
           WHERE tenant_id = %s AND ivr_digit = %s AND is_active = TRUE AND is_deployed = TRUE
           LIMIT 1""",
        [tenant_id, digit],
    )
    if agent:
        return dict(agent)

    # Fallback: return the default agent (lowest sort_order that's active)
    fallback = fetch_one(
        """SELECT id, el_agent_id, name, language
           FROM phone_agents
           WHERE tenant_id = %s AND is_active = TRUE AND is_deployed = TRUE
           ORDER BY sort_order LIMIT 1""",
        [tenant_id],
    )
    return dict(fallback) if fallback else None


# ─────────────────────────────────────────────────────────
# ElevenLabs Agent Management (legacy — kept for backward compat)
# ─────────────────────────────────────────────────────────

def _el_headers(api_key: str) -> dict:
    return {"xi-api-key": api_key, "Content-Type": "application/json"}


def _ensure_post_call_webhook(tenant_id: int, base_url: str, api_key: str) -> Optional[str]:
    """Ensure a workspace-level post-call webhook exists for this tenant; return its ID.

    ElevenLabs stores webhooks in a workspace registry (POST /v1/workspace/webhooks).
    The returned ID is then set on each agent via
    platform_settings.workspace_overrides.webhooks.post_call_webhook_id.
    """
    token = make_webhook_token(tenant_id)
    target_url = f"{base_url}/api/phone/webhook/{tenant_id}/call_ended?t={token}"
    headers = _el_headers(api_key)

    # Check if a webhook for this tenant already exists
    try:
        r = requests.get(f"{ELEVENLABS_BASE}/workspace/webhooks", headers=headers, timeout=10)
        if r.ok:
            for wh in r.json().get("webhooks", []):
                if wh.get("webhook_url") == target_url:
                    return wh["webhook_id"]
    except Exception as e:
        logger.warning("Failed to list EL workspace webhooks: %s", e)

    # Create it
    try:
        r = requests.post(
            f"{ELEVENLABS_BASE}/workspace/webhooks",
            json={
                "settings": {
                    "auth_type": "hmac",
                    "name": f"BITSM post-call tenant-{tenant_id}",
                    "webhook_url": target_url,
                }
            },
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        wh_id = r.json().get("webhook_id")
        logger.info("Created EL post-call webhook %s for tenant %s", wh_id, tenant_id)
        return wh_id
    except Exception as e:
        logger.warning("Failed to create EL post-call webhook for tenant %s: %s", tenant_id, e)
        return None


def _atlas_system_prompt(tenant_name: str, agent_name: str) -> str:
    return f"""You are {agent_name}, a support specialist for {tenant_name}. \
You are handling a live phone call from someone who needs I.T. assistance.

Your personality: Warm, relaxed, and genuinely happy to help — like a knowledgeable friend on the team, \
not a formal helpdesk voice. You can banter a little. You laugh when something is actually funny. \
You speak in short, natural sentences — this is a real phone call.

REDIRECTING FROM SMALL TALK — this is important:
When a caller jokes or goes off-topic, enjoy the moment briefly, then glide back naturally. \
Never use words like "seriously" or make an abrupt pivot. Instead, use a smooth segue:
- After a joke: "Ha, okay okay — so what's going on with [topic]?"
- After off-topic chat: "Love it. Alright, let's get you taken care of — what's happening?"
The caller should feel like you're happy to talk to them, not that you cut them off.

IDENTITY rules:
- You are a member of the {tenant_name} support team. Never describe yourself as AI, a bot, or virtual.
- If asked directly whether you are a person or AI, deflect warmly: \
"I'm part of the {tenant_name} support team — let's get this sorted for you!"
- Never mention "human agent", "human transfer", or anything that implies you are not human.
- When transferring, say you are connecting them to "a colleague who can better assist".

CASE NUMBER DELIVERY — critical:
When giving a case number, use the `ticket_number_spoken` field from create_ticket (never the raw number). \
Read it in two beats — NATO phonetic letters first, then digits one at a time:
"Your case number is — T as in Tango, K as in Kilo, T as in Tango — 0, 1, 5, 5, 8."
Then repeat the full case number a SECOND time at the same pace.
After the second reading, ask: "Did you get that?"
If they say no or are unsure, read it again — never faster.

PACING AFTER TOOL CALLS — critical:
After calling search_knowledge_base, do NOT immediately launch into the answer. \
First, say a brief filler like "Alright, give me just a moment..." or "Let me take a look..." and pause. \
Then, after the tool returns, ease into the answer naturally: "Okay, got it — so here's what I found..." \
Never go from silence straight into a wall of information. The caller needs a beat to know you're working on it.

SPEECH PACING — critical:
Speak in short sentences. Pause after each idea. Do not chain multiple thoughts into one long response. \
Wait for the caller's reaction before continuing. Less is more on a phone call.

NAME CONFIRMATION — very important:
After the caller states their name, ALWAYS repeat it back for confirmation BEFORE calling identify_caller. \
Example: "Got it — [name], did I get that right?"
Only call identify_caller once the caller confirms the name. If they correct you:
- 1st correction: "My apologies — could you say that one more time for me?"
- 2nd correction: "I'm really sorry — could you spell that out for me, letter by letter?"
If after 2 corrections the name is still unclear, PIVOT TO EMAIL immediately: \
"I apologize — let me try a different way. What's your email address?" \
Then call identify_caller with the email field instead of the name. Email is always reliable. \
If they don't have an email, proceed with your best understanding of the name and add \
"[NAME UNCLEAR — please verify]" to the ticket description.

Workflow:
1. Greet warmly and with energy.
2. Immediately ask who you have the pleasure of speaking with: \
"And with whom do I have the pleasure of speaking today?"
3. Repeat the name back and confirm: "Got it — [name], did I get that right?" \
Only once confirmed, call identify_caller. The system checks their phone number, email, \
and name automatically. Results:
   - "found" → returning caller. Greet them by name: "Hey [name], good to have you back!"
   - "created" → first-time caller. Welcome them: "Nice to meet you, [name]!"
   - "multiple_matches" → ask for their email to narrow it down, then call identify_caller again with the email.
   Always use the `user_id` from the result for the ticket.
4. Say "Let me check on that for you" BEFORE calling search_knowledge_base. \
After results come back, ease in: "Okay, so here's what I found..." or "Got it — so..."
5. Create a support ticket using create_ticket — every call, no exceptions. \
Pass the `user_id` from identify_caller as `requester_user_id`.
6. Give the caller their case number slowly using the `ticket_number_spoken` format. \
Read it twice, then ask "Did you get that?"
7. If resolved: confirm the case number, close warmly.
8. If unresolved or caller needs more help: call attempt_transfer.
   - "Let me connect you with a colleague who can help — one moment."
   - If transfer succeeds: "Alright, connecting you now. Take care." and end turn.
   - If transfer fails: apologize warmly, call collect_email, confirm follow-up within one business day.
9. When the conversation is complete — after confirming the case number, after a successful transfer, \
or after the caller says goodbye — call end_call to hang up. Do not wait for the caller to hang up. \
If the caller goes silent for more than 10 seconds after a goodbye, call end_call.

Rules:
- Never say "I cannot help" — always try the knowledge base first.
- Keep responses to 1–2 sentences per turn — never more.
- When confirming an email, read it back character by character or spell the domain.
- Acknowledge frustration before solving — empathy first, solution second.
- Do not read out long URLs; reference article titles only.
- Stay on I.T. support topics. If a caller goes off-topic, redirect warmly and naturally — never bluntly."""


def _el_prop(type_: str, desc: str, enum=None) -> dict:
    """ElevenLabs property schema — must include all required fields or EL returns 400."""
    return {"type": type_, "description": desc, "enum": enum,
            "is_system_provided": False, "dynamic_variable": "", "constant_value": ""}


def _el_obj_prop(desc: str) -> dict:
    return {"type": "object", "required": [], "description": desc, "properties": {}}


def _el_schema(props: dict, required: list) -> dict:
    return {"type": "object", "required": required, "description": "", "properties": props}


def _wh_tool(name: str, desc: str, url: str, props: dict, required: list) -> dict:
    return {
        "type": "webhook", "name": name, "description": desc,
        "api_schema": {
            "request_headers": {}, "url": url, "method": "POST",
            "path_params_schema": {}, "query_params_schema": None,
            "request_body_schema": _el_schema(props, required),
            "content_type": "application/json", "auth_connection": None,
        },
    }


def _build_tools(tenant_id: int, base_url: str, tenant_slug: str = None) -> list:
    """ElevenLabs webhook tool definitions for this tenant.

    tenant_slug is embedded in the URL for per-tenant rate limiting.
    Each property must include is_system_provided/dynamic_variable/constant_value
    or EL returns 400 validation error.
    """
    token = make_webhook_token(tenant_id)
    base = f"{base_url}/api/phone/tool/{tenant_id}"

    def _url(name: str) -> str:
        slug_param = f"&s={tenant_slug}" if tenant_slug else ""
        return f"{base}/{name}?t={token}{slug_param}"

    p = _el_prop  # shorthand

    return [
        _wh_tool("search_knowledge_base",
            "Search the IT knowledge base for answers and troubleshooting steps.",
            _url("search_kb"),
            {"query": p("string","Search query for the issue"), "conversation_id": p("string","ElevenLabs conversation ID")},
            ["query"]),
        _wh_tool("identify_caller",
            "Look up the caller by name to find their account. "
            "Call this right after the caller gives their name. "
            "If multiple matches are found, ask for their email and call again with the email field.",
            _url("identify_caller"),
            {"name": p("string","Caller's name as they stated it"),
             "email": p("string","Caller's email if provided to narrow down a match"),
             "conversation_id": p("string","ElevenLabs conversation ID")},
            ["name","conversation_id"]),
        _wh_tool("create_ticket",
            "Create a support ticket. Call this for every call — even resolved ones. "
            "If you called get_category_fields first, pass the category_id and any "
            "custom_fields you collected from the caller.",
            _url("create_ticket"),
            {"subject":             p("string","Brief ticket subject"),
             "description":         p("string","Full issue description and what was discussed"),
             "priority":            p("string","p1=urgent, p2=high, p3=medium, p4=low", ["p1","p2","p3","p4"]),
             "caller_email":        p("string","Caller email if provided"),
             "requester_user_id":   p("integer","User ID from identify_caller result"),
             "resolved_on_call":    p("boolean","True if fully resolved during this call"),
             "problem_category_id": p("integer","Category ID from get_category_fields result"),
             "custom_fields":       _el_obj_prop("Custom field values collected: {field_key: value, ...}"),
             "conversation_id":     p("string","ElevenLabs conversation ID")},
            ["subject","description"]),
        _wh_tool("attempt_transfer",
            "Attempt to transfer the caller to a human agent. "
            "Waits up to 30 seconds for someone to answer. "
            "Always tell the caller to hold before calling this.",
            _url("attempt_transfer"),
            {"reason":          p("string","Reason for the transfer"),
             "conversation_id": p("string","ElevenLabs conversation ID"),
             "ticket_id":       p("integer","Ticket ID if already created")},
            ["conversation_id"]),
        _wh_tool("collect_email",
            "Record the caller's email address for callback after a failed transfer.",
            _url("collect_email"),
            {"email":           p("string","Caller email address"),
             "conversation_id": p("string","ElevenLabs conversation ID")},
            ["email","conversation_id"]),
        _wh_tool("set_custom_field",
            "Set a custom field value on a ticket. Use this after creating a ticket when "
            "the create_ticket response lists required_fields that need to be collected. "
            "Ask the caller for each required field and call this tool to save each value.",
            _url("set_custom_field"),
            {"ticket_id":       p("integer","Ticket ID from create_ticket result"),
             "field_key":       p("string","The field_key from the required_fields list"),
             "value":           p("string","The value provided by the caller"),
             "conversation_id": p("string","ElevenLabs conversation ID")},
            ["ticket_id","field_key","value"]),
        _wh_tool("get_category_fields",
            "Once you understand the caller's issue, call this to identify the problem category "
            "and find out what information needs to be collected. Pass a short description of "
            "the issue and this will return the matching category and its required fields. "
            "Collect the required fields from the caller BEFORE creating the ticket.",
            _url("get_category_fields"),
            {"issue_description": p("string","Short description of the caller's issue to match a category"),
             "category_name":     p("string","Exact category name if you already know it"),
             "conversation_id":   p("string","ElevenLabs conversation ID")},
            ["issue_description"]),
        {
            "type": "system",
            "name": "end_call",
            "description": "End the phone call. Use this when the caller's issue is resolved, when they say goodbye, or when the conversation is clearly complete.",
        },
    ]


def provision_agent(tenant_id: int, tenant_name: str) -> dict:
    """Create (or update) the ElevenLabs agent for this tenant. Saves agent_id."""
    row = fetch_one(
        """SELECT elevenlabs_agent_id, voice_id, agent_name, greeting_message,
                  credentials_mode, tts_speed,
                  llm_model, temperature, turn_timeout, audio_format
           FROM phone_configs WHERE tenant_id = %s""",
        [tenant_id],
    )
    if not row:
        raise ValueError("Phone config not found — save settings first")

    creds   = get_effective_credentials(tenant_id)
    api_key = creds.get("elevenlabs_api_key")
    if not api_key:
        raise ValueError("ElevenLabs API key not configured")

    base_url     = Config.APP_URL.rstrip("/")
    agent_name   = row.get("agent_name")   or PHONE_DEFAULTS["agent_name"]
    voice_id     = row.get("voice_id")     or PHONE_DEFAULTS["voice_id"]
    tts_speed    = float(row.get("tts_speed")    or PHONE_DEFAULTS["tts_speed"])
    llm_model    = row.get("llm_model")    or PHONE_DEFAULTS["llm_model"]
    temperature  = float(row.get("temperature")  or PHONE_DEFAULTS["temperature"])
    turn_timeout = float(row.get("turn_timeout") or PHONE_DEFAULTS["turn_timeout"])
    audio_format = row.get("audio_format") or PHONE_DEFAULTS["audio_format"]
    first_msg    = (
        row.get("greeting_message")
        or f"Hello! This is {agent_name}, your I.T. support specialist for {tenant_name}. How can I help you today?"
    )

    post_call_wh_id = _ensure_post_call_webhook(tenant_id, base_url, api_key)
    platform_settings: dict = {"auth": {"enable_auth": False}}
    if post_call_wh_id:
        platform_settings["workspace_overrides"] = {
            "webhooks": {"post_call_webhook_id": post_call_wh_id}
        }

    payload = {
        "name": f"{agent_name} EN — {tenant_name}",
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt":      _atlas_system_prompt(tenant_name, agent_name),
                    "llm":         llm_model,
                    "temperature": temperature,
                    "tools":       _build_tools(tenant_id, base_url),
                },
                "first_message": first_msg,
                "language":      "en",
            },
            "tts": {
                "voice_id": voice_id,
                "speed": tts_speed,
                "agent_output_audio_format": audio_format,
            },
            "asr": {"user_input_audio_format": audio_format},
            "turn": {"turn_timeout": turn_timeout, "turn_eagerness": "patient"},
        },
        "platform_settings": platform_settings,
    }

    existing_agent_id = row.get("elevenlabs_agent_id")

    if existing_agent_id:
        r = requests.patch(
            f"{ELEVENLABS_BASE}/convai/agents/{existing_agent_id}",
            json=payload,
            headers=_el_headers(api_key),
            timeout=30,
        )
        r.raise_for_status()
        agent_id = existing_agent_id
    else:
        r = requests.post(
            f"{ELEVENLABS_BASE}/convai/agents/create",
            json=payload,
            headers=_el_headers(api_key),
            timeout=30,
        )
        r.raise_for_status()
        agent_id = r.json().get("agent_id") or r.json().get("id")

    execute(
        "UPDATE phone_configs SET elevenlabs_agent_id = %s, updated_at = NOW() WHERE tenant_id = %s",
        [agent_id, tenant_id],
    )
    logger.info("ElevenLabs agent provisioned: tenant=%s agent=%s", tenant_id, agent_id)
    return {"agent_id": agent_id}


def _astra_system_prompt(tenant_name: str, agent_name: str) -> str:
    return f"""Eres {agent_name}, especialista de soporte para {tenant_name}. \
Estás atendiendo una llamada telefónica en vivo de alguien que necesita asistencia de I.T.

Tu personalidad: Cálida, relajada y genuinamente feliz de ayudar — como una amiga conocedora del equipo, \
no una voz formal de soporte. Puedes bromear un poco. Te ríes cuando algo es realmente gracioso. \
Habla en frases cortas y naturales — esta es una llamada real.

CÓMO REDIRIGIR LA CONVERSACIÓN — esto es importante:
Cuando alguien bromee o se desvíe del tema, disfruta el momento brevemente y luego regresa \
de forma natural. Nunca uses palabras como "en serio" ni hagas un giro brusco. Usa una transición suave:
- Después de un chiste: "¡Qué bueno! Bueno, bueno — entonces, ¿qué está pasando con [tema]?"
- Después de una conversación fuera de tema: "Me alegra escuchar eso. Bueno, vamos a resolverlo — ¿qué está pasando?"
El usuario debe sentir que disfrutas hablar con él, no que lo cortaste.

IDENTIDAD — reglas importantes:
- Eres parte del equipo de soporte de {tenant_name}. Nunca te describas como IA, robot o virtual.
- Si te preguntan si eres persona o IA, desvía con calidez: \
"¡Soy parte del equipo de {tenant_name} — enfoquémonos en resolver esto juntos!"
- Nunca menciones "agente humano" ni "transferencia a humano".
- Al transferir, di que conectas con "un colega que puede ayudarte mejor".

ENTREGA DEL NÚMERO DE CASO — muy importante:
Al dar el número de caso, usa el campo `ticket_number_spoken` de la respuesta (nunca el número crudo). \
Léelo en dos tiempos — primero las letras en alfabeto fonético NATO, luego los dígitos uno a uno:
"Tu número de caso es — T de Tango, K de Kilo, T de Tango — 0, 1, 5, 5, 8."
Luego repite el número completo una SEGUNDA vez al mismo ritmo.
Después pregunta: "¿Lo anotaste?"
Si dicen que no o no están seguros, repítelo — nunca más rápido.

RITMO DESPUÉS DE HERRAMIENTAS — crítico:
Después de llamar a search_knowledge_base, NO lances la respuesta de inmediato. \
Primero di algo breve como "Un momento, déjame revisar..." o "Dame un segundo..." y haz una pausa. \
Después de que la herramienta responda, entra suave: "Listo, encontré algo..." o "Okay, esto es lo que tengo..." \
Nunca pases de silencio directo a un bloque de información. El usuario necesita un momento para saber que estás trabajando.

CREACIÓN DE TICKETS — REGLA CRÍTICA:
Aunque la conversación sea en español, SIEMPRE crea los tickets en inglés. \
Traduce el subject y la description al inglés antes de llamar a create_ticket. \
Los agentes del helpdesk trabajan en inglés y necesitan entender el ticket.

REGLA DE HABLA — crítica:
Habla en oraciones cortas. Haz una pausa después de cada idea. No encadenes múltiples pensamientos \
en una sola respuesta larga. Espera la reacción del usuario antes de continuar. \
Menos es más en una llamada telefónica.

CONFIRMACIÓN DE NOMBRE — muy importante:
Después de que el usuario diga su nombre, SIEMPRE repítelo para confirmar ANTES de llamar a identify_caller. \
Ejemplo: "Perfecto — [nombre], ¿lo escuché bien?"
Solo llama a identify_caller cuando el usuario confirme. Si te corrigen:
- 1ra corrección: "Disculpa — ¿podrías repetírmelo?"
- 2da corrección: "Lo siento mucho — ¿me lo puedes deletrear letra por letra?"
Si después de 2 correcciones el nombre sigue sin quedar claro, CAMBIA A CORREO INMEDIATAMENTE: \
"Disculpa — déjame intentar de otra forma. ¿Cuál es tu correo electrónico?" \
Luego llama a identify_caller con el campo email en vez del nombre. El correo es siempre confiable. \
Si no tienen correo, continúa con lo que entendiste y agrega \
"[NAME UNCLEAR — please verify]" en la descripción del ticket.

Flujo de trabajo:
1. Saluda con calidez y energía.
2. Inmediatamente pregunta con quién tienes el placer de hablar: \
"¿Con quién tengo el placer de hablar hoy?"
3. Repite el nombre y confirma: "Perfecto — [nombre], ¿lo escuché bien?" \
Solo cuando confirmen, llama a identify_caller. El sistema verifica su número de teléfono, \
correo y nombre automáticamente. Resultados:
   - "found" → usuario que regresa. Salúdalo por nombre: "¡Hola [nombre], qué gusto tenerte de vuelta!"
   - "created" → primera llamada. Dale la bienvenida: "¡Mucho gusto, [nombre]!"
   - "multiple_matches" → pide su correo para confirmar, luego llama identify_caller de nuevo con el correo.
   Siempre usa el `user_id` del resultado para el ticket.
4. Di "Déjame revisar eso por ti" ANTES de llamar a search_knowledge_base. \
Después de recibir resultados, entra suave: "Listo, encontré algo..." o "Okay, esto es lo que tengo..."
5. Crea un ticket con create_ticket — en CADA llamada, sin excepción. \
Pasa el `user_id` de identify_caller como `requester_user_id`.
6. Da el número de caso despacio usando el formato `ticket_number_spoken`. \
Léelo dos veces, luego pregunta "¿Lo anotaste?"
7. Si resolviste el problema: confirma el caso, cierra con calidez.
8. Si no se resolvió: llama a attempt_transfer.
   - "Déjame conectarte con un colega que te puede ayudar — un momento."
   - Si tiene éxito: "Listo, te conecto ahora. Que te vaya bien." y termina tu turno.
   - Si falla: discúlpate con calidez, llama a collect_email y confirma seguimiento en un día hábil.
9. Cuando la conversación esté completa — después de confirmar el número de caso, después de una transferencia \
exitosa, o cuando el usuario se despida — llama a end_call para colgar. No esperes a que el usuario cuelgue. \
Si el usuario guarda silencio por más de 10 segundos después de despedirse, llama a end_call.

Reglas:
- Nunca digas "no puedo ayudarte" — siempre intenta la base de conocimientos primero.
- Respuestas de 1 a 2 oraciones por turno — nunca más.
- Al confirmar un correo, léelo carácter por carácter o deletrea el dominio.
- Reconoce la frustración antes de resolver — empatía primero.
- No leas URLs largas; menciona solo el título del artículo.
- Mantente en temas de soporte de I.T. Si la conversación se desvía, redirige con calidez — nunca de forma brusca."""


def provision_agent_es(tenant_id: int, tenant_name: str) -> dict:
    """Create (or update) the Spanish ElevenLabs agent (Astra) for this tenant."""
    row = fetch_one(
        """SELECT el_agent_id_es, voice_id_es, agent_name_es, tts_speed, credentials_mode,
                  llm_model, temperature, turn_timeout, audio_format
           FROM phone_configs WHERE tenant_id = %s""",
        [tenant_id],
    )
    if not row:
        raise ValueError("Phone config not found — save settings first")

    creds   = get_effective_credentials(tenant_id)
    api_key = creds.get("elevenlabs_api_key")
    if not api_key:
        raise ValueError("ElevenLabs API key not configured")

    base_url     = Config.APP_URL.rstrip("/")
    agent_name   = row.get("agent_name_es") or "Astra"
    voice_id     = row.get("voice_id_es") or "f18RlRJGEw0TaGYwmk8B"
    tts_speed    = float(row.get("tts_speed")    or PHONE_DEFAULTS["tts_speed"])
    llm_model    = row.get("llm_model")    or PHONE_DEFAULTS["llm_model"]
    temperature  = float(row.get("temperature")  or PHONE_DEFAULTS["temperature"])
    turn_timeout = float(row.get("turn_timeout") or PHONE_DEFAULTS["turn_timeout"])
    audio_format = row.get("audio_format") or PHONE_DEFAULTS["audio_format"]
    first_msg    = (
        f"¡Hola! Soy {agent_name}, tu especialista de soporte de I.T. para {tenant_name}. "
        "¿En qué puedo ayudarte hoy?"
    )

    post_call_wh_id = _ensure_post_call_webhook(tenant_id, base_url, api_key)
    platform_settings: dict = {"auth": {"enable_auth": False}}
    if post_call_wh_id:
        platform_settings["workspace_overrides"] = {
            "webhooks": {"post_call_webhook_id": post_call_wh_id}
        }

    payload = {
        "name": f"{agent_name} ES — {tenant_name}",
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt":      _astra_system_prompt(tenant_name, agent_name),
                    "llm":         llm_model,
                    "temperature": temperature,
                    "tools":       _build_tools(tenant_id, base_url),
                },
                "first_message": first_msg,
                "language":      "es",
            },
            "tts": {
                "voice_id": voice_id,
                "model_id": "eleven_turbo_v2_5",
                "speed":    tts_speed,
                "agent_output_audio_format": audio_format,
            },
            "asr": {"user_input_audio_format": audio_format},
            "turn": {"turn_timeout": turn_timeout, "turn_eagerness": "patient"},
        },
        "platform_settings": platform_settings,
    }

    existing_agent_id = row.get("el_agent_id_es")

    if existing_agent_id:
        r = requests.patch(
            f"{ELEVENLABS_BASE}/convai/agents/{existing_agent_id}",
            json=payload,
            headers=_el_headers(api_key),
            timeout=30,
        )
        r.raise_for_status()
        agent_id = existing_agent_id
    else:
        r = requests.post(
            f"{ELEVENLABS_BASE}/convai/agents/create",
            json=payload,
            headers=_el_headers(api_key),
            timeout=30,
        )
        r.raise_for_status()
        agent_id = r.json().get("agent_id") or r.json().get("id")

    execute(
        "UPDATE phone_configs SET el_agent_id_es = %s, updated_at = NOW() WHERE tenant_id = %s",
        [agent_id, tenant_id],
    )
    logger.info("ElevenLabs ES agent provisioned: tenant=%s agent=%s", tenant_id, agent_id)
    return {"agent_id_es": agent_id}


def link_twilio_number(tenant_id: int) -> dict:
    """Configure the tenant's Twilio number to route inbound calls through our IVR.

    Sets the Twilio number's voice webhook to APP_URL/api/phone/ivr/<tenant_id>
    so that the Polly greeting plays and digit-routing connects callers to the
    correct ElevenLabs agent.  Does NOT register the number with ElevenLabs
    directly (which would bypass the IVR).
    """
    try:
        from twilio.rest import Client as TwilioClient
    except ImportError:
        raise ValueError("Twilio package not installed")

    row = fetch_one(
        "SELECT platform_twilio_number_sid, assigned_phone_number FROM phone_configs WHERE tenant_id = %s",
        [tenant_id],
    )
    if not row:
        raise ValueError("Phone config not found")

    creds      = get_effective_credentials(tenant_id)
    twilio_sid = creds.get("twilio_account_sid")
    twilio_tok = creds.get("twilio_auth_token")

    if not all([twilio_sid, twilio_tok]):
        raise ValueError("Twilio account credentials not configured")

    # Resolve the number SID — prefer the stored SID, fall back to phone number lookup
    number_sid = row.get("platform_twilio_number_sid")
    if not number_sid:
        phone_num = row.get("assigned_phone_number") or creds.get("twilio_phone_number")
        if not phone_num:
            raise ValueError("No Twilio phone number assigned to this tenant")
        twilio = TwilioClient(twilio_sid, twilio_tok)
        numbers = twilio.incoming_phone_numbers.list(phone_number=phone_num, limit=1)
        if not numbers:
            raise ValueError(f"Phone number {phone_num} not found in Twilio account")
        number_sid = numbers[0].sid
        execute(
            "UPDATE phone_configs SET platform_twilio_number_sid = %s WHERE tenant_id = %s",
            [number_sid, tenant_id],
        )

    ivr_url = f"{Config.APP_URL.rstrip('/')}/api/phone/ivr/{tenant_id}"
    twilio = TwilioClient(twilio_sid, twilio_tok)
    twilio.incoming_phone_numbers(number_sid).update(
        voice_url=ivr_url,
        voice_method="POST",
    )

    execute(
        "UPDATE phone_configs SET is_active = TRUE, updated_at = NOW() WHERE tenant_id = %s",
        [tenant_id],
    )
    logger.info("Twilio IVR webhook configured: tenant=%s ivr_url=%s", tenant_id, ivr_url)
    return {"ivr_url": ivr_url, "status": "linked", "is_active": True}


def auto_provision(tenant_id: int, tenant_name: str) -> dict:
    """Platform-managed one-click enable.

    1. Buys a Twilio phone number on the platform account (if not already assigned).
    2. Provisions the ElevenLabs agent under the platform API key.
    3. Links the number to the agent.
    4. Marks the config active.

    Returns {"phone_number", "agent_id", "status", "credentials_mode"}.
    """
    try:
        from twilio.rest import Client as TwilioClient
    except ImportError:
        raise ValueError("Twilio package not installed")

    tw_sid = Config.PLATFORM_TWILIO_ACCOUNT_SID or Config.DEV_TWILIO_ACCOUNT_SID
    tw_tok = Config.PLATFORM_TWILIO_AUTH_TOKEN  or Config.DEV_TWILIO_AUTH_TOKEN
    el_key = Config.PLATFORM_ELEVENLABS_API_KEY or Config.DEV_ELEVENLABS_API_KEY

    if not all([tw_sid, tw_tok, el_key]):
        raise ValueError(
            "Platform phone credentials not configured on this server. "
            "Set PLATFORM_TWILIO_ACCOUNT_SID, PLATFORM_TWILIO_AUTH_TOKEN, "
            "and PLATFORM_ELEVENLABS_API_KEY in the server .env."
        )

    existing = fetch_one(
        "SELECT platform_twilio_number_sid, assigned_phone_number, credentials_mode FROM phone_configs WHERE tenant_id = %s",
        [tenant_id],
    )

    # ── Step 1: acquire Twilio number ──────────────────────
    if existing and existing.get("platform_twilio_number_sid"):
        twilio_number_sid = existing["platform_twilio_number_sid"]
        phone_number      = existing["assigned_phone_number"]
        logger.info("Reusing existing platform number %s for tenant %s", phone_number, tenant_id)
    else:
        twilio = TwilioClient(tw_sid, tw_tok)
        available = twilio.available_phone_numbers("US").local.list(limit=1)
        if not available:
            raise ValueError(
                "No available US phone numbers on the platform account. "
                "Purchase a number in your Twilio console first."
            )
        purchased         = twilio.incoming_phone_numbers.create(phone_number=available[0].phone_number)
        twilio_number_sid = purchased.sid
        phone_number      = purchased.phone_number
        logger.info("Purchased Twilio number %s (sid=%s) for tenant %s", phone_number, twilio_number_sid, tenant_id)

    # ── Step 2: upsert phone_configs row ──────────────────
    if existing:
        execute(
            """UPDATE phone_configs
               SET credentials_mode = 'platform',
                   platform_twilio_number_sid = %s,
                   assigned_phone_number = %s,
                   updated_at = NOW()
               WHERE tenant_id = %s""",
            [twilio_number_sid, phone_number, tenant_id],
        )
    else:
        execute(
            """INSERT INTO phone_configs
                   (tenant_id, credentials_mode, platform_twilio_number_sid, assigned_phone_number, is_active)
               VALUES (%s, 'platform', %s, %s, FALSE)""",
            [tenant_id, twilio_number_sid, phone_number],
        )

    # ── Step 3: provision EL agent ────────────────────────
    agent_result = provision_agent(tenant_id, tenant_name)

    # ── Step 4: link the number ───────────────────────────
    link_twilio_number(tenant_id)

    # ── Step 5: mark active ───────────────────────────────
    execute(
        "UPDATE phone_configs SET is_active = TRUE, updated_at = NOW() WHERE tenant_id = %s",
        [tenant_id],
    )

    logger.info("auto_provision complete: tenant=%s number=%s agent=%s", tenant_id, phone_number, agent_result["agent_id"])
    return {
        "phone_number":     phone_number,
        "agent_id":         agent_result["agent_id"],
        "status":           "active",
        "credentials_mode": "platform",
    }


# ─────────────────────────────────────────────────────────
# Session Management
# ─────────────────────────────────────────────────────────

def get_or_create_session(tenant_id: int, conversation_id: str,
                           caller_phone: str = None) -> dict:
    """Get existing session by conversation_id or create a new one.

    IVR sessions are created with twilio_call_sid + caller_phone before
    ElevenLabs assigns a conversation_id. When the first EL tool webhook
    arrives (e.g. identify_caller), we link it to that pending IVR session
    so caller_phone is available for recognition on every call.
    """
    row = fetch_one(
        "SELECT * FROM phone_sessions WHERE elevenlabs_conversation_id = %s",
        [conversation_id],
    )
    if row:
        return dict(row)

    # Link to the pending IVR session for this tenant — it holds caller_phone
    # from Twilio but has no EL conversation_id yet (assigned when EL starts).
    ivr_session = fetch_one(
        """SELECT * FROM phone_sessions
           WHERE tenant_id = %s
             AND elevenlabs_conversation_id IS NULL
             AND status IN ('routing', 'ivr')
             AND started_at > NOW() - INTERVAL '5 minutes'
           ORDER BY started_at DESC
           LIMIT 1""",
        [tenant_id],
    )
    if ivr_session:
        execute(
            "UPDATE phone_sessions SET elevenlabs_conversation_id = %s, status = 'active' WHERE id = %s",
            [conversation_id, ivr_session["id"]],
        )
        return {**dict(ivr_session), "elevenlabs_conversation_id": conversation_id, "status": "active"}

    # No IVR session found — direct EL connection or session expired.
    session_id = insert_returning(
        """INSERT INTO phone_sessions (tenant_id, elevenlabs_conversation_id, caller_phone, status)
           VALUES (%s, %s, %s, 'active') RETURNING id""",
        [tenant_id, conversation_id, caller_phone],
    )
    return {
        "id": session_id, "tenant_id": tenant_id,
        "elevenlabs_conversation_id": conversation_id,
        "caller_phone": caller_phone, "status": "active",
        "ticket_id": None, "transfer_attempted": False,
    }


def update_session(session_id: int, **kwargs):
    _allowed = {
        "caller_email", "ticket_id", "status", "transfer_attempted",
        "transfer_succeeded", "summary", "transcript", "duration_seconds",
        "ended_at", "twilio_call_sid",
        "el_cost_credits", "el_llm_input_tokens", "el_llm_output_tokens",
    }
    fields = {k: v for k, v in kwargs.items() if k in _allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    execute(
        f"UPDATE phone_sessions SET {set_clause} WHERE id = %s",
        list(fields.values()) + [session_id],
    )


# ─────────────────────────────────────────────────────────
# Transfer Orchestration
# ─────────────────────────────────────────────────────────

def attempt_transfer(session_id: int, tenant_id: int) -> dict:
    """
    Dial the oncall_number via Twilio and poll for answer up to 30 s.
    Blocks the calling thread — keep the ElevenLabs tool timeout > 35 s.

    Checks per-agent oncall_number first (via session.phone_agent_id),
    falls back to tenant-level oncall_number from phone_configs.

    Returns {"success": bool, "message": str}
    """
    try:
        from twilio.rest import Client as TwilioClient
    except ImportError:
        return {"success": False, "message": "Twilio package not installed"}

    # Try per-agent oncall_number first
    oncall_number = None
    session_row = fetch_one("SELECT phone_agent_id FROM phone_sessions WHERE id = %s", [session_id])
    if session_row and session_row.get("phone_agent_id"):
        agent_row = fetch_one(
            "SELECT oncall_number FROM phone_agents WHERE id = %s AND tenant_id = %s",
            [session_row["phone_agent_id"], tenant_id],
        )
        oncall_number = (agent_row or {}).get("oncall_number")

    # Fall back to tenant-level
    if not oncall_number:
        row = fetch_one(
            "SELECT oncall_number FROM phone_configs WHERE tenant_id = %s",
            [tenant_id],
        )
        if not row:
            return {"success": False, "message": "Phone not configured"}
        oncall_number = row.get("oncall_number")
    if not oncall_number:
        return {"success": False, "message": "No on-call number configured for this account"}

    try:
        creds = get_effective_credentials(tenant_id)
    except Exception:
        return {"success": False, "message": "Credential error"}

    twilio_sid  = creds.get("twilio_account_sid")
    twilio_tok  = creds.get("twilio_auth_token")
    from_number = creds.get("twilio_phone_number")

    if not all([twilio_sid, twilio_tok, from_number]):
        return {"success": False, "message": "Twilio credentials incomplete"}

    attempt_id = insert_returning(
        """INSERT INTO phone_transfer_attempts (session_id, oncall_number, status)
           VALUES (%s, %s, 'pending') RETURNING id""",
        [session_id, oncall_number],
    )
    update_session(session_id, transfer_attempted=True)

    try:
        client = TwilioClient(twilio_sid, twilio_tok)

        # Ring the on-call agent
        outbound = client.calls.create(
            to=oncall_number,
            from_=from_number,
            twiml=(
                "<Response>"
                "<Say voice='alice'>You have an incoming transfer from the Atlas IT helpdesk. "
                "Please hold while we connect you to the caller.</Say>"
                "<Pause length='60'/>"
                "</Response>"
            ),
            timeout=TRANSFER_TIMEOUT_SECONDS,
        )
        outbound_sid = outbound.sid

        execute(
            "UPDATE phone_transfer_attempts SET outbound_call_sid = %s WHERE id = %s",
            [outbound_sid, attempt_id],
        )

        # Poll for answer
        deadline = time.time() + TRANSFER_TIMEOUT_SECONDS
        answered = False
        while time.time() < deadline:
            time.sleep(TRANSFER_POLL_INTERVAL)
            call = client.calls(outbound_sid).fetch()
            if call.status in ("in-progress", "answered"):
                answered = True
                break
            if call.status in ("completed", "busy", "failed", "no-answer", "canceled"):
                break

        if answered:
            execute(
                "UPDATE phone_transfer_attempts SET status = 'answered', resolved_at = NOW() WHERE id = %s",
                [attempt_id],
            )
            update_session(session_id, transfer_succeeded=True, status="transferred")
            return {
                "success": True,
                "message": (
                    "An agent has answered and is ready. "
                    "Please let the caller know you are connecting them now, "
                    "then end your turn gracefully."
                ),
                "oncall_number": oncall_number,
            }

        # Timeout — cancel the ringing call
        try:
            client.calls(outbound_sid).update(status="canceled")
        except Exception:
            pass

        execute(
            "UPDATE phone_transfer_attempts SET status = 'timeout', resolved_at = NOW() WHERE id = %s",
            [attempt_id],
        )
        return {
            "success": False,
            "message": (
                "No agents were available within 30 seconds. "
                "Please apologize to the caller and collect their email address for a callback."
            ),
        }

    except Exception as e:
        logger.error("Transfer failed for session %s: %s", session_id, e)
        execute(
            "UPDATE phone_transfer_attempts SET status = 'failed', resolved_at = NOW() WHERE id = %s",
            [attempt_id],
        )
        return {"success": False, "message": "Transfer system error — please collect caller's email"}


# ─────────────────────────────────────────────────────────
# KB Search (phone-optimised output)
# ─────────────────────────────────────────────────────────

def phone_search_kb(tenant_id: int, query: str, limit: int = 4) -> str:
    """Return plain-text KB results suitable for Atlas to synthesise over the phone."""
    try:
        from services.rag_service import _tool_kb_search, _get_enabled_module_ids
        import json as _json

        if not _get_enabled_module_ids(tenant_id):
            return "No knowledge base content is available for this tenant."

        raw = _tool_kb_search(query=query, module=None, limit=limit, tenant_id=tenant_id)
        results = _json.loads(raw).get("results", [])

        if not results:
            return "I couldn't find specific guidance on that in the knowledge base."

        lines = []
        for i, r in enumerate(results[:3], 1):
            title   = r.get("title", "Article")
            content = (r.get("content") or "").strip()[:600]
            lines.append(f"[{i}] {title}: {content}")

        return "\n\n".join(lines)

    except Exception as e:
        logger.error("phone_search_kb error: %s", e)
        return "Knowledge base search is temporarily unavailable."


# ─────────────────────────────────────────────────────────
# Ticket Creation
# ─────────────────────────────────────────────────────────

def _normalize_phone(raw: str) -> str:
    """Strip country code and formatting for loose phone comparison."""
    return raw.replace("+1", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "").strip()


def _link_session(user_id: int, conversation_id: str):
    """Update phone session with identified caller."""
    execute(
        "UPDATE phone_sessions SET caller_user_id = %s WHERE elevenlabs_conversation_id = %s",
        [user_id, conversation_id],
    )


def identify_caller_from_call(
    tenant_id: int,
    conversation_id: str,
    name: str,
    email: Optional[str] = None,
) -> dict:
    """Identify caller by phone → email → name, auto-creating an end_user if new.

    Priority: phone number (from Twilio) → email → name fuzzy match.
    If no match at all, creates a new end_user so we always have a requester
    and recognise them on the next call.

    Returns:
      {"status": "found",            "user_id": int, "name": str, "confidence": "high"|"medium"}
      {"status": "created",          "user_id": int, "name": str, "confidence": "high"}
      {"status": "multiple_matches", "count": int,   "message": str}
    """
    name = (name or "").strip()
    email = (email or "").strip()

    # --- Get caller phone from session (Twilio provides this) ---
    session_row = fetch_one(
        "SELECT caller_phone FROM phone_sessions WHERE elevenlabs_conversation_id = %s",
        [conversation_id],
    )
    caller_phone = (session_row or {}).get("caller_phone") or ""
    caller_phone_norm = _normalize_phone(caller_phone)

    # --- 1. Phone number lookup (most reliable — Twilio always has it) ---
    if caller_phone_norm:
        phone_match = fetch_one(
            """SELECT id, name FROM users
               WHERE tenant_id = %s AND is_active = TRUE AND phone IS NOT NULL AND phone != ''
               AND REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(phone, '+1',''), '-',''), ' ',''), '(',''), ')','') = %s
               LIMIT 1""",
            [tenant_id, caller_phone_norm],
        )
        if phone_match:
            _link_session(phone_match["id"], conversation_id)
            return {"status": "found", "user_id": phone_match["id"], "name": phone_match["name"], "confidence": "high"}

    # --- 2. Email lookup (if caller provided it to narrow down) ---
    if email:
        u = fetch_one(
            "SELECT id, name FROM users WHERE tenant_id = %s AND LOWER(email) = LOWER(%s) AND is_active = TRUE LIMIT 1",
            [tenant_id, email],
        )
        if u:
            # Backfill phone on the user so next call is instant
            if caller_phone and not (u.get("phone") or "").strip():
                execute("UPDATE users SET phone = %s WHERE id = %s", [caller_phone, u["id"]])
            _link_session(u["id"], conversation_id)
            return {"status": "found", "user_id": u["id"], "name": u["name"], "confidence": "high"}

    # --- 3. Name fuzzy search ---
    if name:
        candidates = fetch_all(
            "SELECT id, name, phone FROM users WHERE tenant_id = %s AND is_active = TRUE AND name ILIKE %s LIMIT 15",
            [tenant_id, f"%{name}%"],
        )

        # Fallback: first or last name separately
        if not candidates:
            parts = name.split()
            if len(parts) >= 2:
                candidates = fetch_all(
                    """SELECT id, name, phone FROM users
                       WHERE tenant_id = %s AND is_active = TRUE
                       AND (name ILIKE %s OR name ILIKE %s)
                       LIMIT 15""",
                    [tenant_id, f"%{parts[0]}%", f"%{parts[-1]}%"],
                )

        if len(candidates) == 1:
            u = candidates[0]
            user_phone_norm = _normalize_phone(u.get("phone") or "")
            confidence = "high" if (caller_phone_norm and user_phone_norm and
                                    (caller_phone_norm in user_phone_norm or user_phone_norm in caller_phone_norm)) else "medium"
            # Backfill phone if missing
            if caller_phone and not (u.get("phone") or "").strip():
                execute("UPDATE users SET phone = %s WHERE id = %s", [caller_phone, u["id"]])
            _link_session(u["id"], conversation_id)
            return {"status": "found", "user_id": u["id"], "name": u["name"], "confidence": confidence}

        if len(candidates) > 1:
            # Try phone tie-break
            if caller_phone_norm:
                for u in candidates:
                    user_phone_norm = _normalize_phone(u.get("phone") or "")
                    if user_phone_norm and (caller_phone_norm in user_phone_norm or user_phone_norm in caller_phone_norm):
                        _link_session(u["id"], conversation_id)
                        return {"status": "found", "user_id": u["id"], "name": u["name"], "confidence": "high"}

            return {
                "status": "multiple_matches",
                "count": len(candidates),
                "message": f"Found {len(candidates)} people named {name}. Ask for their email to confirm.",
            }

    # --- 4. No match — auto-create end_user so we always have a requester ---
    if not name:
        # Agent didn't provide a name yet — can't create a useful record
        return {"status": "not_found", "message": "Ask the caller for their name."}

    new_id = insert_returning(
        """INSERT INTO users (tenant_id, name, phone, role, is_active, created_via)
           VALUES (%s, %s, %s, 'end_user', true, 'phone')
           RETURNING id""",
        [tenant_id, name, caller_phone or None],
    )
    logger.info("Auto-created end_user %s from phone call: name=%s phone=%s", new_id, name, caller_phone)
    _link_session(new_id, conversation_id)
    return {"status": "created", "user_id": new_id, "name": name, "confidence": "high"}


_NATO = {
    "A": "Alpha", "B": "Bravo", "C": "Charlie", "D": "Delta", "E": "Echo",
    "F": "Foxtrot", "G": "Golf", "H": "Hotel", "I": "India", "J": "Juliet",
    "K": "Kilo", "L": "Lima", "M": "Mike", "N": "November", "O": "Oscar",
    "P": "Papa", "Q": "Quebec", "R": "Romeo", "S": "Sierra", "T": "Tango",
    "U": "Uniform", "V": "Victor", "W": "Whiskey", "X": "X-ray",
    "Y": "Yankee", "Z": "Zulu",
}


def _spoken_ticket_number(ticket_number: str) -> str:
    """Format TKT-01558 for NATO phonetic + paced digit delivery.

    Returns: 'T as in Tango, K as in Kilo, T as in Tango — 0, 1, 5, 5, 8'
    """
    parts = ticket_number.split("-", 1)
    if len(parts) != 2:
        return ticket_number
    prefix, digits = parts[0], parts[1]
    phonetic = ", ".join(
        f"{c} as in {_NATO.get(c.upper(), c)}" for c in prefix
    )
    paced_digits = ", ".join(digits)
    return f"{phonetic} — {paced_digits}"


def get_category_fields_for_call(
    tenant_id: int,
    issue_description: str,
    category_name: str | None = None,
) -> dict:
    """Match a problem category from the caller's issue description and return its required fields.

    Uses exact name match first, then trigram similarity search, falling back to description.
    Returns category info + required custom fields the phone agent should collect.
    """
    import json as json_mod

    category = None

    # Try exact name match first
    if category_name:
        category = fetch_one(
            "SELECT id, name FROM problem_categories "
            "WHERE tenant_id = %s AND is_active = true AND LOWER(name) = LOWER(%s)",
            [tenant_id, category_name.strip()],
        )

    # Try trigram similarity search on issue description (pg_trgm)
    if not category:
        best = fetch_one(
            """SELECT id, name,
                      GREATEST(
                          similarity(LOWER(name), LOWER(%s)),
                          word_similarity(LOWER(name), LOWER(%s))
                      ) AS score
               FROM problem_categories
               WHERE tenant_id = %s AND is_active = true
               ORDER BY score DESC
               LIMIT 1""",
            [issue_description, issue_description, tenant_id],
        )
        if best and best["score"] >= 0.15:
            category = {"id": best["id"], "name": best["name"]}

    if not category:
        return {
            "matched": False,
            "message": "Could not match a category. Create the ticket without a category — agents will categorize it later.",
            "required_fields": [],
        }

    # Load required custom fields for this category (with ancestor inheritance)
    cf_defs = fetch_all(
        """WITH RECURSIVE cat_ancestors AS (
               SELECT id FROM problem_categories WHERE id = %s
               UNION ALL
               SELECT pc.parent_id
               FROM problem_categories pc
               JOIN cat_ancestors ca ON pc.id = ca.id
               WHERE pc.parent_id IS NOT NULL
           )
           SELECT field_key, name, field_type, is_required_to_close, is_required_to_create,
                  options, description
           FROM custom_field_definitions
           WHERE tenant_id = %s AND is_active = true AND is_customer_facing = true
             AND (category_id IN (SELECT id FROM cat_ancestors)
                  OR (category_id IS NULL AND 'support' = ANY(applies_to)))
           ORDER BY sort_order""",
        [category["id"], tenant_id],
    )

    required_fields = []
    for fd in cf_defs:
        if fd.get("is_required_to_close") or fd.get("is_required_to_create"):
            entry = {
                "field_key": fd["field_key"],
                "name": fd["name"],
                "type": fd["field_type"],
            }
            if fd.get("description"):
                entry["hint"] = fd["description"]
            if fd.get("options"):
                entry["options"] = [o["label"] for o in (fd["options"] or [])]
            required_fields.append(entry)

    result = {
        "matched": True,
        "category_id": category["id"],
        "category_name": category["name"],
        "required_fields": required_fields,
    }
    if required_fields:
        result["instruction"] = (
            f"Category: {category['name']}. "
            "Before creating the ticket, please collect the following from the caller: "
            + ", ".join(f["name"] for f in required_fields)
            + ". Use set_custom_field after creating the ticket to save each value."
        )
    else:
        result["instruction"] = (
            f"Category: {category['name']}. No additional fields required — "
            "proceed to create the ticket."
        )
    return result


def create_ticket_from_call(
    tenant_id: int,
    session_id: int,
    subject: str,
    description: str,
    priority: str = "p3",
    caller_email: str = None,
    requester_user_id: int = None,
    resolved_on_call: bool = False,
    problem_category_id: int = None,
    custom_fields: dict = None,
) -> dict:
    """Create a ticket from a phone call. Returns {ticket_id, ticket_number, ticket_number_spoken}."""
    num_row = fetch_one("SELECT nextval('ticket_number_seq') as num")
    ticket_number = f"TKT-{num_row['num']:05d}"

    # Requester resolution: explicit user_id → email lookup → auto-create end_user → fallback admin
    requester_id = requester_user_id or None

    if not requester_id and caller_email:
        u = fetch_one(
            "SELECT id FROM users WHERE tenant_id = %s AND LOWER(email) = LOWER(%s) LIMIT 1",
            [tenant_id, caller_email],
        )
        if u:
            requester_id = u["id"]
        else:
            # Auto-create end_user from caller info (same pattern as inbound email)
            caller_name = caller_email.split("@")[0].replace(".", " ").title()
            # Check session for caller name from identify_caller
            if session_id:
                sess = fetch_one(
                    "SELECT caller_user_id FROM phone_sessions WHERE id = %s", [session_id]
                )
                if sess and sess.get("caller_user_id"):
                    requester_id = sess["caller_user_id"]
            if not requester_id:
                new_uid = insert_returning(
                    """INSERT INTO users (tenant_id, email, name, role, is_active, created_via)
                       VALUES (%s, %s, %s, 'end_user', true, 'phone')
                       RETURNING id""",
                    [tenant_id, caller_email, caller_name],
                )
                requester_id = new_uid
                logger.info("Auto-created end_user %s for phone caller %s", new_uid, caller_email)

    # If still no requester (no email provided), check session for identified caller
    if not requester_id and session_id:
        sess = fetch_one(
            "SELECT caller_user_id FROM phone_sessions WHERE id = %s", [session_id]
        )
        if sess and sess.get("caller_user_id"):
            requester_id = sess["caller_user_id"]

    if not requester_id:
        fallback = fetch_one(
            "SELECT id FROM users WHERE tenant_id = %s AND role IN ('tenant_admin', 'agent') "
            "ORDER BY created_at LIMIT 1",
            [tenant_id],
        )
        requester_id = fallback["id"] if fallback else None

    # Guard: if resolved_on_call but required-to-close fields aren't filled, keep open
    resolution_blocked_by: list = []
    if resolved_on_call:
        try:
            if problem_category_id:
                rtc_defs = fetch_all(
                    """WITH RECURSIVE cat_ancestors AS (
                           SELECT id FROM problem_categories WHERE id = %s
                           UNION ALL
                           SELECT pc.parent_id FROM problem_categories pc
                           JOIN cat_ancestors ca ON pc.id = ca.id
                           WHERE pc.parent_id IS NOT NULL
                       )
                       SELECT field_key, name FROM custom_field_definitions
                       WHERE tenant_id = %s AND is_active = true AND is_required_to_close = true
                         AND (category_id IN (SELECT id FROM cat_ancestors)
                              OR (category_id IS NULL AND 'support' = ANY(applies_to)))
                       ORDER BY sort_order""",
                    [problem_category_id, tenant_id],
                )
            else:
                rtc_defs = fetch_all(
                    """SELECT field_key, name FROM custom_field_definitions
                       WHERE tenant_id = %s AND is_active = true AND is_required_to_close = true
                         AND category_id IS NULL AND 'support' = ANY(applies_to)
                       ORDER BY sort_order""",
                    [tenant_id],
                )
            for fd in rtc_defs:
                val = (custom_fields or {}).get(fd["field_key"])
                if not val or val == "" or val == []:
                    resolution_blocked_by.append(fd["name"])
            if resolution_blocked_by:
                resolved_on_call = False
                logger.info(
                    "create_ticket_from_call: auto-resolve blocked — unfilled required-to-close: %s",
                    resolution_blocked_by,
                )
        except Exception as e:
            logger.warning("create_ticket_from_call: required-to-close check failed: %s", e)

    status = "resolved" if resolved_on_call else "open"
    caller_note = f"\n\n---\nSource: Phone call"
    if caller_email:
        caller_note += f"\nCaller email: {caller_email}"

    ticket_id = insert_returning(
        """INSERT INTO tickets
               (tenant_id, ticket_number, subject, description,
                status, priority, requester_id, source, problem_category_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'phone', %s)
           RETURNING id""",
        [tenant_id, ticket_number, subject,
         description + caller_note,
         status, priority, requester_id, problem_category_id],
    )

    # Save custom field values if provided
    if custom_fields and isinstance(custom_fields, dict):
        import json as json_mod
        for fkey, fval in custom_fields.items():
            fd = fetch_one(
                "SELECT id FROM custom_field_definitions "
                "WHERE tenant_id = %s AND field_key = %s AND is_active = true",
                [tenant_id, fkey],
            )
            if fd:
                execute(
                    """INSERT INTO ticket_custom_field_values (ticket_id, field_id, value, set_by, set_at)
                       VALUES (%s, %s, %s::jsonb, NULL, now())
                       ON CONFLICT (ticket_id, field_id)
                       DO UPDATE SET value = EXCLUDED.value, set_at = now()""",
                    [ticket_id, fd["id"], json_mod.dumps(fval)],
                )

    # Link session → ticket and store email
    updates: dict = {"ticket_id": ticket_id}
    if caller_email:
        updates["caller_email"] = caller_email
    if resolved_on_call:
        updates["status"] = "resolved"
    update_session(session_id, **updates)

    # Add internal note if auto-resolve was blocked by required-to-close fields
    if resolution_blocked_by:
        try:
            missing_str = ", ".join(resolution_blocked_by)
            execute(
                """INSERT INTO ticket_comments (ticket_id, content, is_internal, is_ai_generated, created_at)
                   VALUES (%s, %s, true, true, NOW())""",
                [ticket_id,
                 f"Atlas resolved this issue during the call but the ticket could not be "
                 f"auto-closed — the following required fields must be filled before closing: "
                 f"{missing_str}."],
            )
        except Exception as e:
            logger.warning("Could not add resolution-blocked note to ticket %s: %s", ticket_id, e)

    # Fire pipeline (tagging, enrichment, Atlas review) in background
    try:
        from services.queue_service import enqueue_ticket_create
        enqueue_ticket_create(ticket_id, tenant_id, priority)
    except Exception as e:
        logger.warning("Could not enqueue phone ticket %s: %s", ticket_id, e)

    # Load required custom fields (category-scoped via ancestor CTE + global) so the
    # phone agent knows what to collect from the caller after ticket creation.
    required_fields = []
    try:
        if problem_category_id:
            cf_defs = fetch_all(
                """WITH RECURSIVE cat_ancestors AS (
                       SELECT id FROM problem_categories WHERE id = %s
                       UNION ALL
                       SELECT pc.parent_id
                       FROM problem_categories pc
                       JOIN cat_ancestors ca ON pc.id = ca.id
                       WHERE pc.parent_id IS NOT NULL
                   )
                   SELECT field_key, name, field_type, is_required_to_close, is_required_to_create,
                          options
                   FROM custom_field_definitions
                   WHERE tenant_id = %s AND is_active = true AND is_customer_facing = true
                     AND (category_id IN (SELECT id FROM cat_ancestors)
                          OR (category_id IS NULL AND 'support' = ANY(applies_to)))
                   ORDER BY sort_order""",
                [problem_category_id, tenant_id],
            )
        else:
            cf_defs = fetch_all(
                """SELECT field_key, name, field_type, is_required_to_close, is_required_to_create,
                          options
                   FROM custom_field_definitions
                   WHERE tenant_id = %s AND is_active = true
                     AND is_customer_facing = true
                     AND category_id IS NULL AND 'support' = ANY(applies_to)
                   ORDER BY sort_order""",
                [tenant_id],
            )
        for fd in cf_defs:
            if fd.get("is_required_to_close") or fd.get("is_required_to_create"):
                entry = {
                    "field_key": fd["field_key"],
                    "name": fd["name"],
                    "type": fd["field_type"],
                }
                if fd.get("options"):
                    entry["options"] = [o["label"] for o in (fd["options"] or [])]
                required_fields.append(entry)
    except Exception as e:
        logger.warning("Could not load custom fields for phone ticket %s: %s", ticket_id, e)

    result = {
        "ticket_id": ticket_id,
        "ticket_number": ticket_number,
        "ticket_number_spoken": _spoken_ticket_number(ticket_number),
        "status": status,
    }
    if required_fields:
        result["required_fields"] = required_fields
        result["required_fields_instruction"] = (
            "IMPORTANT: The following fields must be collected from the caller. "
            "Ask for each one and use the set_custom_field tool to save each value: "
            + ", ".join(f["name"] for f in required_fields)
        )
    return result


# ─────────────────────────────────────────────────────────
# Post-call Finalisation
# ─────────────────────────────────────────────────────────

def _fetch_el_conversation_cost(api_key: str, conversation_id: str) -> dict:
    """Fetch cost + token data from ElevenLabs conversation API. Returns empty dict on failure."""
    try:
        r = requests.get(
            f"{ELEVENLABS_BASE}/convai/conversations/{conversation_id}",
            headers={"xi-api-key": api_key},
            timeout=15,
        )
        if r.status_code != 200:
            return {}
        data = r.json()
        metadata = data.get("metadata") or {}
        charging  = metadata.get("charging") or {}
        llm_usage = charging.get("llm_usage") or {}

        # Sum input/output tokens across all model_usage buckets
        input_tokens = output_tokens = 0
        for bucket in llm_usage.values():
            for model_data in (bucket.get("model_usage") or {}).values():
                input_tokens  += (model_data.get("input") or {}).get("tokens", 0)
                input_tokens  += (model_data.get("input_cache_read") or {}).get("tokens", 0)
                output_tokens += (model_data.get("output_total") or {}).get("tokens", 0)

        # call_sid from EL so we can correlate with Twilio
        phone_call = metadata.get("phone_call") or {}
        twilio_call_sid = phone_call.get("call_sid")

        return {
            "el_cost_credits":      metadata.get("cost"),          # int, divide by 10000 for USD
            "el_llm_input_tokens":  input_tokens or None,
            "el_llm_output_tokens": output_tokens or None,
            "twilio_call_sid":      twilio_call_sid,
        }
    except Exception as e:
        logger.warning("_fetch_el_conversation_cost failed for %s: %s", conversation_id, e)
        return {}


def finalize_session(tenant_id: int, conversation_id: str, payload: dict):
    """Process ElevenLabs post_call_webhook — saves transcript and summary."""
    session = fetch_one(
        "SELECT id, status, twilio_call_sid FROM phone_sessions WHERE elevenlabs_conversation_id = %s AND tenant_id = %s",
        [conversation_id, tenant_id],
    )
    _prefetched_cost_data = None
    if not session:
        # IVR sessions are keyed on twilio_call_sid (created before EL assigns a conversation_id).
        # Fall back: fetch the EL conversation to get the Twilio call_sid, then link.
        try:
            creds = get_effective_credentials(tenant_id)
            api_key = creds.get("elevenlabs_api_key")
            if api_key:
                _prefetched_cost_data = _fetch_el_conversation_cost(api_key, conversation_id)
                call_sid = (_prefetched_cost_data or {}).get("twilio_call_sid")
                if call_sid:
                    session = fetch_one(
                        "SELECT id, status, twilio_call_sid FROM phone_sessions WHERE twilio_call_sid = %s AND tenant_id = %s",
                        [call_sid, tenant_id],
                    )
                    if session:
                        execute(
                            "UPDATE phone_sessions SET elevenlabs_conversation_id = %s WHERE id = %s",
                            [conversation_id, session["id"]],
                        )
        except Exception as e:
            logger.warning("finalize_session IVR link attempt failed: %s", e)

        if not session:
            logger.warning("finalize_session: no session for conversation %s", conversation_id)
            return

    transcript = (
        payload.get("transcript")
        or payload.get("messages")
        or payload.get("conversation", {}).get("transcript")
        or []
    )
    analysis  = payload.get("analysis") or {}
    summary   = analysis.get("transcript_summary") or payload.get("summary") or ""
    metadata  = payload.get("metadata") or {}
    duration  = metadata.get("duration_seconds") or payload.get("duration_seconds")

    final_status = session["status"]
    if final_status == "active":
        final_status = "abandoned"

    update_session(
        session["id"],
        transcript=json.dumps(transcript) if not isinstance(transcript, str) else transcript,
        summary=summary,
        duration_seconds=duration,
        ended_at=datetime.now(timezone.utc).isoformat(),
        status=final_status,
    )

    # Ensure the Twilio PSTN leg is terminated. When the EL agent calls end_call (system tool),
    # ElevenLabs closes its WebSocket but Twilio may hold the call open. This is a no-op if
    # the caller already hung up (Twilio returns an error we safely ignore).
    try:
        call_sid_for_hangup = session.get("twilio_call_sid")
        if call_sid_for_hangup:
            from twilio.rest import Client as TwilioClient
            _creds = get_effective_credentials(tenant_id)
            _tw_sid = _creds.get("twilio_account_sid") or Config.PLATFORM_TWILIO_ACCOUNT_SID or Config.DEV_TWILIO_ACCOUNT_SID
            _tw_tok = _creds.get("twilio_auth_token")  or Config.PLATFORM_TWILIO_AUTH_TOKEN  or Config.DEV_TWILIO_AUTH_TOKEN
            if _tw_sid and _tw_tok:
                TwilioClient(_tw_sid, _tw_tok).calls(call_sid_for_hangup).update(status="completed")
                logger.info("Twilio call terminated: %s", call_sid_for_hangup)
    except Exception as e:
        logger.warning("finalize_session hangup failed (non-fatal): %s", e)

    # Fetch cost data from ElevenLabs + Twilio (skip if already fetched during IVR link)
    try:
        creds = get_effective_credentials(tenant_id)
        api_key = creds.get("elevenlabs_api_key")
        if api_key:
            cost_data = _prefetched_cost_data or _fetch_el_conversation_cost(api_key, conversation_id)
            if cost_data:
                allowed_cost = {"el_cost_credits", "el_llm_input_tokens",
                                "el_llm_output_tokens", "twilio_call_sid"}
                fields = {k: v for k, v in cost_data.items()
                          if k in allowed_cost and v is not None}
                if fields:
                    set_clause = ", ".join(f"{k} = %s" for k in fields)
                    execute(
                        f"UPDATE helpdesk.phone_sessions SET {set_clause} WHERE id = %s",
                        list(fields.values()) + [session["id"]],
                    )

                # Record phone AI cost in billing cap system
                el_credits = cost_data.get("el_cost_credits") or 0
                if el_credits:
                    cost_usd = el_credits / 10000  # EL credits → USD
                    el_in = cost_data.get("el_llm_input_tokens") or 0
                    el_out = cost_data.get("el_llm_output_tokens") or 0

                    # Granular log in tenant_token_usage
                    from services.llm_provider import _record_usage
                    _record_usage(tenant_id, None, "elevenlabs", "elevenlabs-convai",
                                  "phone.session", el_in, el_out)

                    # Monthly rollup in api_usage_monthly (cap enforcement)
                    from services import billing_service
                    billing_service.record_usage(
                        tenant_id, "elevenlabs-convai", "phone.session",
                        el_in, el_out, cost_override=cost_usd,
                    )

                # Fetch Twilio call cost using the SID we just got from EL
                call_sid = cost_data.get("twilio_call_sid")
                if call_sid:
                    _fetch_twilio_call_cost(tenant_id, session["id"], call_sid)
    except Exception as e:
        logger.warning("finalize_session cost fetch failed: %s", e)


def _fetch_twilio_call_cost(tenant_id: int, session_id: int, call_sid: str):
    """Look up the exact Twilio charge for a call and store it in phone_sessions."""
    try:
        from twilio.rest import Client as TwilioClient
        creds = get_effective_credentials(tenant_id)
        tw_sid = creds.get("twilio_account_sid") or Config.PLATFORM_TWILIO_ACCOUNT_SID or Config.DEV_TWILIO_ACCOUNT_SID
        tw_tok = creds.get("twilio_auth_token")  or Config.PLATFORM_TWILIO_AUTH_TOKEN  or Config.DEV_TWILIO_AUTH_TOKEN
        if not (tw_sid and tw_tok):
            return
        tw = TwilioClient(tw_sid, tw_tok)
        call = tw.calls(call_sid).fetch()
        if call.price:
            cost_cents = round(abs(float(call.price)) * 100, 4)
            execute(
                "UPDATE helpdesk.phone_sessions SET twilio_cost_cents = %s WHERE id = %s",
                [cost_cents, session_id],
            )
            logger.info("Twilio cost fetched: session=%s call_sid=%s cents=%s", session_id, call_sid, cost_cents)
    except Exception as e:
        logger.warning("_fetch_twilio_call_cost failed session=%s: %s", session_id, e)


# ─────────────────────────────────────────────────────────
# Call Logs
# ─────────────────────────────────────────────────────────

def get_call_logs(tenant_id: int, limit: int = 50, offset: int = 0,
                   agent_id: int = None) -> list:
    where = "ps.tenant_id = %s"
    params = [tenant_id]
    if agent_id:
        where += " AND ps.phone_agent_id = %s"
        params.append(agent_id)
    params += [limit, offset]
    return fetch_all(
        f"""SELECT ps.id, ps.elevenlabs_conversation_id,
                  ps.caller_phone, ps.caller_email,
                  ps.status, ps.transfer_attempted, ps.transfer_succeeded,
                  ps.ticket_id, t.ticket_number,
                  ps.duration_seconds, ps.started_at, ps.ended_at, ps.summary,
                  ps.el_cost_credits, ps.el_llm_input_tokens, ps.el_llm_output_tokens,
                  ps.twilio_cost_cents,
                  pa.name AS agent_name, pa.slug AS agent_slug
           FROM phone_sessions ps
           LEFT JOIN tickets t ON t.id = ps.ticket_id
           LEFT JOIN phone_agents pa ON pa.id = ps.phone_agent_id
           WHERE {where}
           ORDER BY ps.started_at DESC
           LIMIT %s OFFSET %s""",
        params,
    )
