from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TestnetScenario:
    symbol: str
    side: str
    margin_usdt: Decimal
    leverage: int
    entry_offset_pct: Decimal
    stop_offset_pct: Decimal
    target_offset_pct: Decimal


@dataclass(frozen=True)
class TestnetReadiness:
    ready: bool
    reasons: tuple[str, ...]


def default_testnet_scenario() -> TestnetScenario:
    return TestnetScenario(
        symbol="BTCUSDT",
        side="LONG",
        margin_usdt=Decimal("1"),
        leverage=1,
        entry_offset_pct=Decimal("0.10"),
        stop_offset_pct=Decimal("0.50"),
        target_offset_pct=Decimal("0.50"),
    )


def check_testnet_readiness(
    *,
    enabled: bool,
    api_key_present: bool,
    api_secret_present: bool,
) -> TestnetReadiness:
    """Return readiness for opt-in testnet E2E."""
    reasons = []
    if not enabled:
        reasons.append("testnet E2E is not explicitly enabled")
    if not api_key_present:
        reasons.append("Binance testnet API key is missing")
    if not api_secret_present:
        reasons.append("Binance testnet API secret is missing")
    return TestnetReadiness(ready=not reasons, reasons=tuple(reasons))
