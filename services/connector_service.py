"""Webhook connector dispatch service.

Fires active connectors of type 'webhook' on ticket lifecycle events.
Each connector's config (Fernet-encrypted) must contain a 'url' key.
Optional 'headers' dict and 'secret' string (sent as X-Webhook-Secret header).

SSRF protection: validate_url() blocks all RFC-1918 + link-local + loopback addresses.
"""

import json
import logging
import threading

import requests as http_requests

from config import Config
from models.db import fetch_all, fetch_one
from services.url_validator import validate_url

logger = logging.getLogger(__name__)


def dispatch_webhook_connectors(
    tenant_id: int,
    ticket_id: int,
    event: str,
    ticket_data: dict | None = None,
):
    """Enqueue webhook connector dispatch in a daemon thread (non-blocking).

    Called from ticket lifecycle hooks.  Failures are logged but never
    propagated — connectors must not interrupt ticket mutations.
    """
    t = threading.Thread(
        target=_fire_connectors,
        args=(tenant_id, ticket_id, event, dict(ticket_data or {})),
        daemon=True,
    )
    t.start()


def _fire_connectors(tenant_id: int, ticket_id: int, event: str, ticket_data: dict):
    """Load and fire all active webhook connectors for this tenant."""
    if not Config.FERNET_KEY:
        logger.error("dispatch_webhook_connectors: FERNET_KEY not configured — skipping")
        return

    connectors = fetch_all(
        """SELECT id, name, config_encrypted
           FROM connectors
           WHERE tenant_id = %s AND connector_type = 'webhook' AND is_active = true""",
        [tenant_id],
    )
    if not connectors:
        return

    # Supplement ticket_data with core fields if caller didn't provide them
    if not ticket_data.get("ticket_number"):
        row = fetch_one(
            "SELECT ticket_number, subject, status, priority FROM tickets WHERE id = %s",
            [ticket_id],
        )
        if row:
            ticket_data.update(row)

    payload = {
        "event": event,
        "tenant_id": tenant_id,
        "ticket_id": ticket_id,
        **ticket_data,
    }

    from cryptography.fernet import Fernet
    f = Fernet(Config.FERNET_KEY.encode())

    for conn in connectors:
        try:
            config = json.loads(f.decrypt(conn["config_encrypted"].encode()).decode())
            url = config.get("url", "").strip()
            if not url:
                logger.warning("Webhook connector '%s' has no URL — skipping", conn["name"])
                continue

            validate_url(url)  # SSRF protection — raises ValueError for internal addresses

            headers = dict(config.get("headers") or {})
            secret = config.get("secret", "").strip()
            if secret:
                headers["X-Webhook-Secret"] = secret

            resp = http_requests.post(url, json=payload, headers=headers, timeout=10)
            logger.info(
                "Webhook connector '%s' fired event=%s ticket=%s → HTTP %s",
                conn["name"], event, ticket_id, resp.status_code,
            )
        except ValueError as ve:
            # SSRF block or bad URL
            logger.error(
                "Webhook connector '%s' blocked (SSRF/invalid URL): %s",
                conn.get("name", "?"), ve,
            )
        except Exception as exc:
            logger.error(
                "Webhook connector '%s' failed event=%s ticket=%s: %s",
                conn.get("name", "?"), event, ticket_id, exc,
            )


def test_webhook_connector(url: str, headers: dict | None = None, secret: str = "") -> dict:
    """Send a test ping to a webhook connector URL.

    Returns {"ok": True/False, "status_code": int, "error": str|None}.
    Used by the connectors blueprint test endpoint.
    """
    try:
        validate_url(url)
    except ValueError as ve:
        return {"ok": False, "error": str(ve)}

    req_headers = dict(headers or {})
    if secret:
        req_headers["X-Webhook-Secret"] = secret

    test_payload = {"event": "test_ping", "message": "BITSM webhook connector test"}

    try:
        resp = http_requests.post(url, json=test_payload, headers=req_headers, timeout=10)
        ok = resp.status_code < 400
        return {"ok": ok, "status_code": resp.status_code}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
