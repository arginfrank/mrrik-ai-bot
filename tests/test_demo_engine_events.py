from __future__ import annotations

from decimal import Decimal

import pytest

from services.demo_engine.events import (
    build_demo_closed_payload,
    build_demo_opened_payload,
    build_notify_user_payload,
    format_demo_close_message,
    format_demo_open_message,
)
from shared.models import DemoAccount, DemoTrade, User


def test_open_payload_and_message_have_no_result_numbers() -> None:
    trade = _trade(status="open")

    assert build_demo_opened_payload(trade) == {
        "demo_trade_id": 10,
        "user_id": 2,
        "signal_id": 3,
        "symbol": "ETHUSDT",
        "side": "LONG",
        "status": "open",
    }
    assert format_demo_open_message(trade) == "Demo: LONG ETHUSDT opened."
    assert "Result" not in format_demo_open_message(trade)


def test_closed_payload_stringifies_decimals() -> None:
    trade = _trade(
        status="closed",
        closed_reason="all_tp",
        roi=Decimal("42.0000"),
        pnl=Decimal("4.200000"),
        touched=[1, 2],
    )
    account = DemoAccount(
        user_id=2,
        start_balance_usdt=Decimal("1000"),
        balance_usdt=Decimal("1004.200000"),
    )

    payload = build_demo_closed_payload(demo_trade=trade, demo_account=account)

    assert payload["realized_roi_pct"] == "42.0000"
    assert payload["realized_pnl_usdt"] == "4.200000"
    assert payload["balance_usdt"] == "1004.200000"
    assert payload["touched_tps"] == [1, 2]


@pytest.mark.parametrize(
    ("reason", "roi", "pnl", "touched", "expected"),
    [
        ("all_tp", "42", "4.2", [1, 2], "TP1, TP2 hit. Result: +42.0% (+4.20 USDT)."),
        ("sl", "-1", "-0.1", [], "Stopped. Result: -1.0% (-0.10 USDT)."),
        ("be", "5", "0.5", [1], "Break-even. Result: +5.0% (+0.50 USDT)."),
        (
            "liquidation",
            "-100",
            "-10",
            [],
            "Liquidated. Result: -100.0% (-10.00 USDT).",
        ),
        (
            "model3_exit",
            "20",
            "2",
            [],
            "Model 3 exit. Result: +20.0% (+2.00 USDT).",
        ),
    ],
)
def test_close_message_formats_every_reason(
    reason: str,
    roi: str,
    pnl: str,
    touched: list[int],
    expected: str,
) -> None:
    message = format_demo_close_message(
        _trade(
            status="closed",
            closed_reason=reason,
            roi=Decimal(roi),
            pnl=Decimal(pnl),
            touched=touched,
        )
    )

    assert message == f"LONG ETHUSDT — {expected}"


def test_notify_user_payload_uses_telegram_id_text_and_language() -> None:
    user = User(id=2, telegram_id=123456, language="en")

    assert build_notify_user_payload(user=user, text="hello") == {
        "telegram_id": 123456,
        "text": "hello",
        "lang": "en",
    }


def _trade(
    *,
    status: str,
    closed_reason: str | None = None,
    roi: Decimal | None = None,
    pnl: Decimal | None = None,
    touched: list[int] | None = None,
) -> DemoTrade:
    return DemoTrade(
        id=10,
        signal_id=3,
        user_id=2,
        symbol="ETHUSDT",
        side="LONG",
        leverage=10,
        margin_usdt=Decimal("10"),
        notional_usdt=Decimal("100"),
        qty=Decimal("0.1"),
        liq_price=Decimal("90.5"),
        status=status,
        closed_reason=closed_reason,
        realized_roi_pct=roi,
        realized_pnl_usdt=pnl,
        touched_tps=touched or [],
    )
