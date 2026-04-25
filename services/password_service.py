"""Password reset token generation, validation, and consumption."""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from models.db import fetch_one, execute, insert_returning

logger = logging.getLogger(__name__)


def generate_reset_token(conn, user_id: int) -> str:
    """Insert a new password reset token for user_id and return the token string.

    Token is a URL-safe random string (secrets.token_urlsafe(32)).
    Expires 1 hour from now.  Any previously unused tokens for this user
    are left intact — callers may choose to invalidate old ones separately.
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    insert_returning(
        """INSERT INTO password_reset_tokens (user_id, token, expires_at)
           VALUES (%s, %s, %s)
           RETURNING id""",
        [user_id, token, expires_at],
    )

    logger.info("Generated password reset token for user_id=%s", user_id)
    return token


def validate_reset_token(conn, token: str) -> dict | None:
    """Return the user row if the token is valid (not expired, not used), else None."""
    row = fetch_one(
        """SELECT prt.id AS token_id, u.id, u.email, u.name, u.role, u.tenant_id
           FROM password_reset_tokens prt
           JOIN users u ON u.id = prt.user_id
           WHERE prt.token = %s
             AND prt.is_used = false
             AND prt.expires_at > now()""",
        [token],
    )
    return row or None


def consume_reset_token(conn, token: str) -> None:
    """Mark a password reset token as used."""
    execute(
        "UPDATE password_reset_tokens SET is_used = true WHERE token = %s",
        [token],
    )
    logger.info("Consumed password reset token")
