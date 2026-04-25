"""Email verification token generation, validation, and consumption."""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from models.db import fetch_one, execute, insert_returning

logger = logging.getLogger(__name__)


def generate_verification_token(conn, user_id: int, email: str) -> str:
    """Insert a new email verification token and return the token string.

    Token is a URL-safe random string (secrets.token_urlsafe(32)).
    Expires 24 hours from now.
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    insert_returning(
        """INSERT INTO email_verification_tokens (user_id, email, token, expires_at)
           VALUES (%s, %s, %s, %s)
           RETURNING id""",
        [user_id, email, token, expires_at],
    )

    logger.info("Generated email verification token for user_id=%s email=%s", user_id, email)
    return token


def validate_verification_token(conn, token: str) -> dict | None:
    """Return the user row if the token is valid (not expired, not already verified), else None."""
    row = fetch_one(
        """SELECT evt.id AS token_id, evt.email AS token_email,
                  u.id, u.email, u.name, u.role, u.tenant_id
           FROM email_verification_tokens evt
           JOIN users u ON u.id = evt.user_id
           WHERE evt.token = %s
             AND evt.is_verified = false
             AND evt.expires_at > now()""",
        [token],
    )
    return row or None


def consume_verification_token(conn, token: str) -> None:
    """Mark the token as verified and set users.email_verified = true."""
    row = fetch_one(
        "SELECT user_id FROM email_verification_tokens WHERE token = %s",
        [token],
    )
    if not row:
        logger.warning("consume_verification_token: token not found")
        return

    user_id = row["user_id"]

    execute(
        "UPDATE email_verification_tokens SET is_verified = true WHERE token = %s",
        [token],
    )
    execute(
        "UPDATE users SET email_verified = true, email_verified_at = now() WHERE id = %s",
        [user_id],
    )

    logger.info("Email verified for user_id=%s", user_id)
