"""Tests for _validate_url() SSRF protection in routes.connectors.

Each test exercises a distinct class of blocked or allowed address.
All tests are purely unit-level — no live server, no database.

The test_allows_public_url test performs a real DNS lookup. If running in an
environment without DNS resolution, skip it with: pytest -m "not network"
"""

import pytest

from routes.connectors import _validate_url


def test_blocks_rfc1918_192():
    """RFC 1918 192.168.x.x range must be blocked (covers internal server addresses)."""
    with pytest.raises(ValueError):
        _validate_url("http://192.168.2.221:5432")


def test_blocks_rfc1918_10():
    """RFC 1918 10.x.x.x range must be blocked."""
    with pytest.raises(ValueError):
        _validate_url("http://10.0.0.1")


def test_blocks_rfc1918_172():
    """RFC 1918 172.16.x.x–172.31.x.x range must be blocked."""
    with pytest.raises(ValueError):
        _validate_url("http://172.16.0.1")


def test_blocks_loopback_ip():
    """IPv4 loopback 127.0.0.1 must be blocked."""
    with pytest.raises(ValueError):
        _validate_url("http://127.0.0.1")


def test_blocks_loopback_localhost():
    """'localhost' hostname (resolves to loopback) must be blocked."""
    with pytest.raises(ValueError):
        _validate_url("http://localhost")


def test_blocks_link_local_metadata():
    """169.254.169.254 (AWS/GCP/Azure instance metadata service) must be blocked."""
    with pytest.raises(ValueError):
        _validate_url("http://169.254.169.254/latest/meta-data/")


def test_blocks_zero_address():
    """0.0.0.0 (non-routable catch-all) must be blocked."""
    with pytest.raises(ValueError):
        _validate_url("http://0.0.0.0")


@pytest.mark.network
def test_allows_public_url():
    """A legitimate public HTTPS URL must pass validation without raising.

    Marked @pytest.mark.network — requires DNS resolution. Skip with:
        pytest -m "not network"
    """
    # Must not raise — example.com is a globally routable public address
    _validate_url("https://example.com")
