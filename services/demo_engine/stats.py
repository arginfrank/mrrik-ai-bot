from __future__ import annotations

from decimal import Decimal
from typing import Any


def compute_demo_stats(raw: dict[str, Any]) -> dict[str, Any]:
    """Compute demo stats from repository raw data."""
    start_balance = _decimal(raw.get("start_balance_usdt", "0"))
    current_balance = _decimal(raw.get("current_balance_usdt", start_balance))
    fixed_margin = _decimal(raw.get("fixed_margin_usdt", "0"))
    trades = list(raw.get("trades", []))
    open_trades = [trade for trade in trades if trade.get("status") == "open"]
    closed_trades = [trade for trade in trades if trade.get("status") == "closed"]
    win_count = sum(
        _decimal(trade.get("realized_pnl_usdt", "0")) > 0
        for trade in closed_trades
    )
    loss_count = len(closed_trades) - win_count
    closed_count = len(closed_trades)
    win_rate = (
        Decimal(win_count) / Decimal(closed_count) * Decimal("100")
        if closed_count
        else Decimal("0")
    )
    net_profit = current_balance - start_balance
    net_profit_pct = (
        net_profit / start_balance * Decimal("100")
        if start_balance
        else Decimal("0")
    )
    return {
        "start_balance_usdt": start_balance,
        "current_balance_usdt": current_balance,
        "fixed_margin_usdt": fixed_margin,
        "signals_traded": len(trades),
        "open_count": len(open_trades),
        "closed_win_count": win_count,
        "closed_loss_count": loss_count,
        "closed_count": closed_count,
        "win_rate_pct": win_rate,
        "net_profit_usdt": net_profit,
        "net_profit_pct": net_profit_pct,
    }


def format_demo_stats(stats: dict[str, Any]) -> str:
    """Return English demo stats text for later telegram_bot use."""
    return "\n".join(
        (
            "Demo stats",
            f"Start balance: {_money(stats['start_balance_usdt'])} USDT",
            f"Current balance: {_money(stats['current_balance_usdt'])} USDT",
            f"Fixed margin: {_money(stats['fixed_margin_usdt'])} USDT",
            f"Signals traded: {stats['signals_traded']}",
            f"Open: {stats['open_count']}",
            (
                f"Closed: {stats['closed_count']} "
                f"({stats['closed_win_count']} wins, {stats['closed_loss_count']} losses)"
            ),
            f"Win rate: {_percent(stats['win_rate_pct'])}%",
            (
                f"Net profit: {_signed_money(stats['net_profit_usdt'])} USDT "
                f"({_signed_percent(stats['net_profit_pct'])}%)"
            ),
        )
    )


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value: Any) -> str:
    return f"{_decimal(value):.2f}"


def _percent(value: Any) -> str:
    return f"{_decimal(value):.1f}"


def _signed_money(value: Any) -> str:
    decimal_value = _decimal(value)
    return f"{decimal_value:+.2f}"


def _signed_percent(value: Any) -> str:
    decimal_value = _decimal(value)
    return f"{decimal_value:+.1f}"
