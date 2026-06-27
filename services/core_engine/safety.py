from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LiveCanaryDecision:
    allowed: bool
    reason: str


def assert_withdrawal_disabled(*, verified: bool) -> None:
    """Raise ValueError if withdrawal-disabled status is not verified."""
    if not verified:
        raise ValueError("withdrawal-disabled API access must be verified")


def enforce_canary_margin_limit(
    *,
    requested_margin_usdt: Decimal,
    max_margin_usdt: Decimal,
) -> LiveCanaryDecision:
    """Allow only tiny-size live canary margin."""
    if requested_margin_usdt <= 0:
        return LiveCanaryDecision(False, "requested margin must be positive")
    if max_margin_usdt <= 0:
        return LiveCanaryDecision(False, "configured margin cap must be positive")
    if requested_margin_usdt > max_margin_usdt:
        return LiveCanaryDecision(False, "requested margin exceeds the live canary cap")
    return LiveCanaryDecision(True, "requested margin is within the live canary cap")


def require_live_canary_confirmation(
    *,
    provided: str | None,
    required: str,
) -> LiveCanaryDecision:
    """Require exact confirmation text before live canary."""
    if not required:
        return LiveCanaryDecision(False, "required confirmation text is not configured")
    if provided != required:
        return LiveCanaryDecision(False, "live canary confirmation does not match")
    return LiveCanaryDecision(True, "live canary confirmation matches exactly")
