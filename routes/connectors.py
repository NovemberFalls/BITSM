"""Connectors blueprint: manage external system integrations."""

import json
import logging

from flask import Blueprint, jsonify, request

from routes.auth import require_role, require_permission, get_current_user
from models.db import fetch_all, fetch_one, insert_returning, execute
from services.url_validator import validate_url as _validate_url

logger = logging.getLogger(__name__)
connectors_bp = Blueprint("connectors", __name__)


def _encrypt_config(config: dict) -> str:
    """Encrypt connector config with Fernet.

    Raises RuntimeError if FERNET_KEY is not configured — connector secrets
    must never be stored as plaintext (defense-in-depth alongside startup validation).
    """
    from config import Config
    if not Config.FERNET_KEY:
        raise RuntimeError("FERNET_KEY is not configured — cannot encrypt connector config")
    from cryptography.fernet import Fernet
    f = Fernet(Config.FERNET_KEY.encode())
    return f.encrypt(json.dumps(config).encode()).decode()


def _decrypt_config(encrypted: str) -> dict:
    """Decrypt connector config.

    Raises RuntimeError if FERNET_KEY is not configured — ensures we never
    silently return unencrypted data (defense-in-depth alongside startup validation).
    """
    from config import Config
    if not Config.FERNET_KEY:
        raise RuntimeError("FERNET_KEY is not configured — cannot decrypt connector config")
    from cryptography.fernet import Fernet
    f = Fernet(Config.FERNET_KEY.encode())
    return json.loads(f.decrypt(encrypted.encode()).decode())


@connectors_bp.route("", methods=["GET"])
@require_permission("connectors.manage")
def list_connectors():
    user = get_current_user()
    if user["role"] == "super_admin":
        connectors = fetch_all(
            """SELECT id, tenant_id, connector_type, name, is_active, last_sync_at, last_error, created_at
               FROM connectors ORDER BY name"""
        )
    else:
        connectors = fetch_all(
            """SELECT id, tenant_id, connector_type, name, is_active, last_sync_at, last_error, created_at
               FROM connectors WHERE tenant_id = %s ORDER BY name""",
            [user["tenant_id"]],
        )
    return jsonify(connectors)


@connectors_bp.route("", methods=["POST"])
@require_permission("connectors.manage")
def create_connector():
    data = request.json or {}
    user = get_current_user()
    # Only super_admin may specify a different tenant_id — prevents IDOR
    # where a non-admin user supplies an arbitrary tenant_id in the POST body
    # to create connectors in another tenant's scope.
    if user["role"] == "super_admin":
        tenant_id = data.get("tenant_id") or user.get("tenant_id")
    else:
        tenant_id = user.get("tenant_id")

    config = data.get("config", {})
    encrypted = _encrypt_config(config)

    connector_id = insert_returning(
        """INSERT INTO connectors (tenant_id, connector_type, name, config_encrypted)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        [tenant_id, data.get("connector_type"), data.get("name"), encrypted],
    )
    return jsonify({"id": connector_id}), 201


@connectors_bp.route("/<int:connector_id>/test", methods=["POST"])
@require_permission("connectors.manage")
def test_connector(connector_id: int):
    user = get_current_user()
    if user["role"] == "super_admin":
        conn = fetch_one("SELECT * FROM connectors WHERE id = %s", [connector_id])
    else:
        conn = fetch_one("SELECT * FROM connectors WHERE id = %s AND tenant_id = %s", [connector_id, user.get("tenant_id")])
    if not conn:
        return jsonify({"error": "Not found"}), 404

    config = _decrypt_config(conn["config_encrypted"])
    connector_type = conn["connector_type"]

    # Test connection based on type
    try:
        if connector_type == "http_api":
            # Validate URL before making request — blocks SSRF to internal networks
            try:
                _validate_url(config["base_url"])
            except ValueError as ve:
                return jsonify({"ok": False, "error": str(ve)}), 400
            import requests as http_requests
            resp = http_requests.get(f"{config['base_url']}/api/health", timeout=10)
            resp.raise_for_status()
        elif connector_type == "webhook":
            url = config.get("url", "").strip()
            if not url:
                return jsonify({"ok": False, "error": "No URL configured"}), 400
            from services.connector_service import test_webhook_connector
            result = test_webhook_connector(
                url,
                headers=config.get("headers"),
                secret=config.get("secret", ""),
            )
            if not result["ok"]:
                return jsonify(result), 502
            return jsonify(result)
        else:
            return jsonify({"ok": True, "message": "No test available for this connector type"})

        execute(
            "UPDATE connectors SET last_sync_at = now(), last_error = NULL WHERE id = %s",
            [connector_id],
        )
        return jsonify({"ok": True})
    except Exception as e:
        execute(
            "UPDATE connectors SET last_error = %s WHERE id = %s",
            [str(e), connector_id],
        )
        return jsonify({"ok": False, "error": str(e)}), 502
