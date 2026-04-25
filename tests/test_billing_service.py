"""Tests for services/billing_service.py.

All DB calls are mocked at the models.db module level because billing_service
uses lazy imports (from models.db import fetch_one inside function bodies).
No live database or running server required.
"""

import threading
from unittest.mock import patch, MagicMock

import pytest

from services.billing_service import (
    ApiCapError,
    check_ai_gate,
    get_cap_for_tenant,
    is_over_cap,
    record_usage,
)


# ---------------------------------------------------------------------------
# ApiCapError — exception contract
# ---------------------------------------------------------------------------

class TestApiCapError:
    def test_api_cap_error_attributes(self):
        """ApiCapError must expose .reason and .tier as set attributes."""
        err = ApiCapError("api_cap_reached", "starter")
        assert err.reason == "api_cap_reached"
        assert err.tier == "starter"


# ---------------------------------------------------------------------------
# check_ai_gate
# ---------------------------------------------------------------------------

class TestCheckAiGate:
    def test_gate_free_tier_raises_ai_not_included(self):
        """Free-tier tenant must be refused with reason='ai_not_included'."""
        with patch("models.db.fetch_one", return_value={"plan_tier": "free"}):
            with pytest.raises(ApiCapError) as exc_info:
                check_ai_gate(tenant_id=1)
        assert exc_info.value.reason == "ai_not_included"
        assert exc_info.value.tier == "free"

    def test_gate_enterprise_passes(self):
        """Enterprise tenant must pass unconditionally (unlimited tier)."""
        with patch("models.db.fetch_one", return_value={"plan_tier": "enterprise"}):
            # Must not raise
            check_ai_gate(tenant_id=1)

    def test_gate_over_cap_raises(self):
        """Starter tenant that is over their monthly cap must be refused with reason='api_cap_reached'."""
        with patch("models.db.fetch_one", return_value={"plan_tier": "starter"}):
            with patch("services.billing_service.is_over_cap", return_value=True):
                with pytest.raises(ApiCapError) as exc_info:
                    check_ai_gate(tenant_id=1)
        assert exc_info.value.reason == "api_cap_reached"
        assert exc_info.value.tier == "starter"

    def test_gate_under_cap_passes(self):
        """Starter tenant under their monthly cap must be allowed through."""
        with patch("models.db.fetch_one", return_value={"plan_tier": "starter"}):
            with patch("services.billing_service.is_over_cap", return_value=False):
                # Must not raise
                check_ai_gate(tenant_id=1)


# ---------------------------------------------------------------------------
# get_cap_for_tenant
# ---------------------------------------------------------------------------

class TestGetCapForTenant:
    def test_cap_starter_two_users(self):
        """Starter tier with 2 active users → cap = $15 * 2 = $30.00."""
        with patch("models.db.fetch_one", return_value={"plan_tier": "starter", "user_count": 2}):
            result = get_cap_for_tenant(tenant_id=1)
        assert result == 30.0

    def test_cap_enterprise_returns_none(self):
        """Enterprise tier must return None (unlimited — BYOK, no cap)."""
        with patch("models.db.fetch_one", return_value={"plan_tier": "enterprise", "user_count": 5}):
            result = get_cap_for_tenant(tenant_id=1)
        assert result is None

    def test_cap_free_returns_zero(self):
        """Free tier must return 0.0 (AI usage not included, cap is zero)."""
        with patch("models.db.fetch_one", return_value={"plan_tier": "free", "user_count": 3}):
            result = get_cap_for_tenant(tenant_id=1)
        assert result == 0.0

    # -- zero-billable-user edge cases (super_admin-only tenants) -----------

    def test_cap_business_zero_billable_users_gets_floor(self):
        """Business tier with 0 billable users must still get 1 × $45 = $45.00.

        This covers tenants where the only active users are super_admins,
        who are excluded from the billable count query.  The max(1, ...)
        floor ensures they are never locked out of AI on a paid plan.
        """
        with patch("models.db.fetch_one", return_value={"plan_tier": "business", "user_count": 0}):
            result = get_cap_for_tenant(tenant_id=1)
        assert result == 45.0

    def test_cap_free_zero_users_still_zero(self):
        """Free tier with 0 users must still return 0.0.

        The max(1, ...) floor applies but free per_user is $0, so 1 × $0 = $0.
        """
        with patch("models.db.fetch_one", return_value={"plan_tier": "free", "user_count": 0}):
            result = get_cap_for_tenant(tenant_id=1)
        assert result == 0.0

    def test_cap_enterprise_zero_users_still_unlimited(self):
        """Enterprise tier with 0 users must still return None (unlimited).

        Enterprise exits early before the user_count path, so max(1, ...)
        is irrelevant — the result is always None regardless of count.
        """
        with patch("models.db.fetch_one", return_value={"plan_tier": "enterprise", "user_count": 0}):
            result = get_cap_for_tenant(tenant_id=1)
        assert result is None


# ---------------------------------------------------------------------------
# is_over_cap
# ---------------------------------------------------------------------------

class TestIsOverCap:
    def test_over_cap_true_when_at_limit(self):
        """Spend equal to cap (>= semantics) must return True."""
        with patch("services.billing_service.get_cap_for_tenant", return_value=20.0):
            with patch("models.db.fetch_one", return_value={"total_cost_usd": 20.0}):
                result = is_over_cap(tenant_id=1)
        assert result is True

    def test_over_cap_false_when_under_limit(self):
        """Spend below cap must return False."""
        with patch("services.billing_service.get_cap_for_tenant", return_value=20.0):
            with patch("models.db.fetch_one", return_value={"total_cost_usd": 10.0}):
                result = is_over_cap(tenant_id=1)
        assert result is False

    def test_over_cap_false_for_enterprise(self):
        """Enterprise tenant (None cap) must return False regardless of spend."""
        with patch("services.billing_service.get_cap_for_tenant", return_value=None):
            result = is_over_cap(tenant_id=1)
        assert result is False

    def test_over_cap_fails_open_on_db_error(self):
        """DB failure in is_over_cap must return False (fail-open) and never raise.

        Billing glitches must not block production AI workloads.
        """
        with patch("services.billing_service.get_cap_for_tenant", side_effect=Exception("db down")):
            result = is_over_cap(tenant_id=1)
        assert result is False


# ---------------------------------------------------------------------------
# record_usage
# ---------------------------------------------------------------------------

class TestRecordUsage:
    def test_record_usage_does_not_raise(self):
        """record_usage must never propagate exceptions even when the DB call fails."""
        with patch("models.db.execute", side_effect=Exception("db unreachable")):
            # Start the thread and give it time to run the inner _upsert
            record_usage(
                tenant_id=1,
                model="claude-haiku-4-5",
                call_type="chat",
                input_tokens=100,
                output_tokens=50,
            )
        # If we reach here, no exception was raised by record_usage itself.
        # The daemon thread swallows the exception internally.

    def test_record_usage_fires_daemon_thread(self):
        """record_usage must dispatch work via a daemon thread (fire-and-forget pattern)."""
        threads_started = []
        original_thread_init = threading.Thread.__init__

        def capturing_init(self, *args, **kwargs):
            original_thread_init(self, *args, **kwargs)
            threads_started.append(self)

        with patch.object(threading.Thread, "__init__", capturing_init):
            with patch("models.db.execute"):
                record_usage(
                    tenant_id=1,
                    model="claude-haiku-4-5",
                    call_type="chat",
                    input_tokens=100,
                    output_tokens=50,
                )

        assert len(threads_started) == 1, "Expected exactly one thread to be started"
        assert threads_started[0].daemon is True, "Thread must be a daemon thread"

    def test_record_usage_skips_when_tenant_id_none(self):
        """record_usage with tenant_id=None must return immediately without starting a thread."""
        with patch("models.db.execute") as mock_execute:
            record_usage(
                tenant_id=None,
                model="claude-haiku-4-5",
                call_type="chat",
                input_tokens=100,
                output_tokens=50,
            )
        mock_execute.assert_not_called()
