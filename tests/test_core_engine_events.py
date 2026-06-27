from __future__ import annotations

from decimal import Decimal

from services.core_engine.events import (
    build_notify_user_payload,
    build_trade_closed_payload,
    build_trade_error_payload,
    build_trade_leg_filled_payload,
    build_trade_opened_payload,
)
from shared.models import Trade, TradeLeg, User


def _trade() -> Trade:
    return Trade(
        id=10,
        signal_id=2,
        user_id=3,
        symbol="HBARUSDT",
        side="LONG",
        leverage=42,
        margin_usdt=Decimal("10"),
        notional_usdt=Decimal("420"),
        qty=Decimal("5878"),
        status="closed",
        realized_pnl_usdt=Decimal("4.20"),
        realized_roi_pct=Decimal("42.0"),
        touched_tps=[1, 2],
        closed_reason="all_tp",
    )


def test_trade_event_payloads_stringify_decimals() -> None:
    trade = _trade()
    opened = build_trade_opened_payload(trade)
    closed = build_trade_closed_payload(trade)
    leg = TradeLeg(
        leg_index=1,
        target_price=Decimal("0.07186"),
        qty=Decimal("1000"),
        status="filled",
    )
    leg_payload = build_trade_leg_filled_payload(trade=trade, leg=leg)

    assert opened["margin_usdt"] == "10"
    assert not isinstance(opened["margin_usdt"], float)
    assert leg_payload["leg_index"] == 1
    assert closed["realized_pnl_usdt"] == "4.20"
    assert closed["realized_roi_pct"] == "42.0"
    assert closed["touched_tps"] == [1, 2]


def test_error_and_notify_payloads_are_safe() -> None:
    error = build_trade_error_payload(
        user_id=3,
        signal_id=2,
        symbol="HBARUSDT",
        reason="insufficient free margin",
    )
    user = User(id=3, telegram_id=999, language="en", is_blocked=False)
    notify = build_notify_user_payload(user=user, text="Trade skipped.")

    assert error["reason"] == "insufficient free margin"
    assert notify == {"telegram_id": 999, "text": "Trade skipped.", "lang": "en"}
    assert "secret" not in repr((error, notify)).lower()
