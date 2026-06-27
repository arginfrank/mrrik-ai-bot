from __future__ import annotations

from decimal import Decimal
from typing import Any

from shared.models import DemoAccount, DemoTrade, User


DEMO_STREAM = "demo"
NOTIFY_STREAM = "notify"


def decimal_to_string(value: Decimal | None) -> str | None:
    """Return a stable non-float string for Decimal values."""
    if value is None:
        return None
    return format(value, "f")


def build_demo_opened_payload(demo_trade: DemoTrade) -> dict[str, Any]:
    """Build a demo.opened payload."""
    return {
        "demo_trade_id": demo_trade.id,
        "user_id": demo_trade.user_id,
        "signal_id": demo_trade.signal_id,
        "symbol": demo_trade.symbol,
        "side": demo_trade.side,
        "status": demo_trade.status,
    }


def build_demo_closed_payload(
    *,
    demo_trade: DemoTrade,
    demo_account: DemoAccount,
) -> dict[str, Any]:
    """Build a demo.closed payload."""
    return {
        "demo_trade_id": demo_trade.id,
        "user_id": demo_trade.user_id,
        "signal_id": demo_trade.signal_id,
        "symbol": demo_trade.symbol,
        "side": demo_trade.side,
        "closed_reason": demo_trade.closed_reason,
        "realized_roi_pct": decimal_to_string(demo_trade.realized_roi_pct),
        "realized_pnl_usdt": decimal_to_string(demo_trade.realized_pnl_usdt),
        "touched_tps": list(demo_trade.touched_tps or []),
        "balance_usdt": decimal_to_string(demo_account.balance_usdt),
    }


def format_demo_open_message(demo_trade: DemoTrade) -> str:
    """Return the open notification text. No result numbers."""
    return f"Demo: {demo_trade.side} {demo_trade.symbol} opened."


def format_demo_close_message(demo_trade: DemoTrade) -> str:
    """Return the close notification text. Only for fully closed trades."""
    if demo_trade.status != "closed":
        raise ValueError("demo close messages require a fully closed trade")
    if demo_trade.realized_roi_pct is None or demo_trade.realized_pnl_usdt is None:
        raise ValueError("closed demo trade is missing its realized result")

    reason = _format_close_reason(demo_trade)
    roi = _format_signed(demo_trade.realized_roi_pct, places=1)
    pnl = _format_signed(demo_trade.realized_pnl_usdt, places=2)
    return (
        f"{demo_trade.side} {demo_trade.symbol} — {reason} "
        f"Result: {roi}% ({pnl} USDT)."
    )


def build_notify_user_payload(
    *,
    user: User,
    text: str,
    lang: str | None = None,
) -> dict[str, Any]:
    """Build a notify.user payload for the telegram_bot service to deliver later."""
    return {
        "telegram_id": user.telegram_id,
        "text": text,
        "lang": lang or user.language or "en",
    }


def _format_close_reason(demo_trade: DemoTrade) -> str:
    if demo_trade.closed_reason == "all_tp":
        targets = ", ".join(f"TP{index}" for index in demo_trade.touched_tps or [])
        return f"{targets} hit." if targets else "Targets hit."
    if demo_trade.closed_reason == "sl":
        return "Stopped."
    if demo_trade.closed_reason == "be":
        return "Break-even."
    if demo_trade.closed_reason == "liquidation":
        return "Liquidated."
    if demo_trade.closed_reason == "model3_exit":
        return "Model 3 exit."
    raise ValueError(f"unsupported demo close reason: {demo_trade.closed_reason!r}")


def _format_signed(value: Decimal, *, places: int) -> str:
    rendered = f"{value:.{places}f}"
    if value >= 0:
        return f"+{rendered}"
    return rendered
