"""Tests for _validate_secrets() startup security check in app.py.

Config attributes are patched directly to avoid interference from .env files
(load_dotenv(override=True) runs at app.py import time, before any test
can set os.environ). All tests are unit-level — no database, no server.
"""

import pytest

from app import _validate_secrets
from config import Config


def test_dev_mode_skips_checks(monkeypatch):
    """When AUTH_ENABLED is False (dev mode), _validate_secrets must return without raising.

    This covers the branch where missing/default keys are acceptable because
    auth is disabled. Would have been RED if the guard `if not Config.AUTH_ENABLED: return`
    were absent.
    """
    monkeypatch.setattr(Config, "AUTH_ENABLED", False)
    # Must not raise regardless of SECRET_KEY / FERNET_KEY state
    _validate_secrets()


def test_missing_secret_key_raises(monkeypatch):
    """Empty SECRET_KEY in production mode must raise RuntimeError.

    Covers the case where an operator forgets to set SECRET_KEY in .env.
    Would have been RED before the fail-fast guard was added.
    """
    monkeypatch.setattr(Config, "AUTH_ENABLED", True)
    monkeypatch.setattr(Config, "SECRET_KEY", "")
    monkeypatch.setattr(Config, "FERNET_KEY", "some-fernet-key")

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _validate_secrets()


def test_default_secret_key_raises(monkeypatch):
    """The literal default SECRET_KEY value 'change-me-in-production' must raise RuntimeError.

    This is the value baked into config.py as the os.environ fallback. Deploying
    with it is a critical security misconfiguration — the check must catch it.
    Would have been RED before the equality check was added.
    """
    monkeypatch.setattr(Config, "AUTH_ENABLED", True)
    monkeypatch.setattr(Config, "SECRET_KEY", "change-me-in-production")
    monkeypatch.setattr(Config, "FERNET_KEY", "some-fernet-key")

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _validate_secrets()


def test_missing_fernet_key_raises(monkeypatch):
    """Empty FERNET_KEY with a valid SECRET_KEY in production mode must raise RuntimeError.

    Connector configs cannot be encrypted without FERNET_KEY — this is a
    P0 security requirement from the board review.
    Would have been RED before the FERNET_KEY guard was added (original silent plaintext fallback).
    """
    monkeypatch.setattr(Config, "AUTH_ENABLED", True)
    monkeypatch.setattr(Config, "SECRET_KEY", "a-sufficiently-long-secret-key-value")
    monkeypatch.setattr(Config, "FERNET_KEY", "")

    with pytest.raises(RuntimeError, match="FERNET_KEY"):
        _validate_secrets()


def test_valid_secrets_passes(monkeypatch):
    """Valid SECRET_KEY and FERNET_KEY in production mode must pass without raising."""
    monkeypatch.setattr(Config, "AUTH_ENABLED", True)
    monkeypatch.setattr(Config, "SECRET_KEY", "a-sufficiently-long-secret-key-value")
    monkeypatch.setattr(Config, "FERNET_KEY", "dGhpcyBpcyBhIHZhbGlkIGZlcm5ldCBrZXkh")

    # Must not raise
    _validate_secrets()
