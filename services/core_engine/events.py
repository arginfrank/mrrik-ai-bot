from __future__ import annotations

from decimal import Decimal
from typing import Any

from shared.models import Trade, TradeLeg, User


ORDERS_STREAM = "orders"
NOTIFY_STREAM = "notify"


def decimal_to_string(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def build_trade_opened_payload(trade: Trade) -> dict[str, Any]:
    return {
        "trade_id": trade.id,
        "signal_id": trade.signal_id,
        "user_id": trade.user_id,
        "symbol": trade.symbol,
        "side": trade.side,
        "leverage": trade.leverage,
        "margin_usdt": decimal_to_string(trade.margin_usdt),
        "notional_usdt": decimal_to_string(trade.notional_usdt),
        "qty": decimal_to_string(trade.qty),
        "status": trade.status,
    }


def build_trade_leg_filled_payload(*, trade: Trade, leg: TradeLeg) -> dict[str, Any]:
    return {
        "trade_id": trade.id,
        "signal_id": trade.signal_id,
        "user_id": trade.user_id,
        "symbol": trade.symbol,
        "leg_index": leg.leg_index,
        "target_price": decimal_to_string(leg.target_price),
        "qty": decimal_to_string(leg.qty),
        "status": leg.status,
    }


def build_trade_closed_payload(trade: Trade) -> dict[str, Any]:
    return {
        "trade_id": trade.id,
        "signal_id": trade.signal_id,
        "user_id": trade.user_id,
        "symbol": trade.symbol,
        "side": trade.side,
        "status": trade.status,
        "closed_reason": trade.closed_reason,
        "realized_pnl_usdt": decimal_to_string(trade.realized_pnl_usdt),
        "realized_roi_pct": decimal_to_string(trade.realized_roi_pct),
        "touched_tps": list(trade.touched_tps or []),
    }


def build_trade_error_payload(
    *,
    user_id: int,
    signal_id: int | None,
    symbol: str | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "signal_id": signal_id,
        "symbol": symbol,
        "reason": reason,
    }


def build_notify_user_payload(
    *,
    user: User,
    text: str,
    lang: str | None = None,
) -> dict[str, Any]:
    return {
        "telegram_id": user.telegram_id,
        "text": text,
        "lang": lang or user.language,
    }


def format_trade_open_message(trade: Trade) -> str:
    return f"{trade.side} {trade.symbol} opened."


def format_trade_closed_message(trade: Trade) -> str:
    pnl = decimal_to_string(trade.realized_pnl_usdt) or "0"
    roi = decimal_to_string(trade.realized_roi_pct) or "0"
    return f"{trade.side} {trade.symbol} closed. Result: {roi}% ({pnl} USDT)."


def format_trade_skipped_message(*, symbol: str, reason: str) -> str:
    return f"{symbol} skipped: {reason}."
