from __future__ import annotations

from decimal import Decimal

import pytest

from services.core_engine.safety import (
    assert_withdrawal_disabled,
    enforce_canary_margin_limit,
    require_live_canary_confirmation,
)


def test_withdrawal_unverified_raises() -> None:
    with pytest.raises(ValueError, match="withdrawal-disabled"):
        assert_withdrawal_disabled(verified=False)


def test_canary_margin_above_max_is_denied() -> None:
    result = enforce_canary_margin_limit(
        requested_margin_usdt=Decimal("5.01"), max_margin_usdt=Decimal("5")
    )

    assert result.allowed is False


def test_canary_margin_within_max_is_allowed() -> None:
    result = enforce_canary_margin_limit(
        requested_margin_usdt=Decimal("5"), max_margin_usdt=Decimal("5")
    )

    assert result.allowed is True


def test_live_canary_requires_exact_confirmation() -> None:
    required = "I_ACCEPT_REAL_MONEY_RISK"

    assert require_live_canary_confirmation(provided="wrong", required=required).allowed is False
    assert require_live_canary_confirmation(provided=required, required=required).allowed is True
