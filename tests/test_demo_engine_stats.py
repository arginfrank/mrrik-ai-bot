from __future__ import annotations

from decimal import Decimal

from services.demo_engine.stats import compute_demo_stats, format_demo_stats


def test_one_open_trade_counts_only_as_open() -> None:
    stats = compute_demo_stats(
        {
            "start_balance_usdt": "1000",
            "current_balance_usdt": "1000",
            "fixed_margin_usdt": "10",
            "trades": [{"status": "open", "realized_pnl_usdt": None}],
        }
    )

    assert stats["signals_traded"] == 1
    assert stats["open_count"] == 1
    assert stats["closed_count"] == 0
    assert stats["closed_win_count"] == 0
    assert stats["closed_loss_count"] == 0
    assert stats["win_rate_pct"] == Decimal("0")


def test_empty_demo_stats_format_without_dividing_by_zero() -> None:
    stats = compute_demo_stats(
        {
            "start_balance_usdt": Decimal("1000"),
            "current_balance_usdt": Decimal("1000"),
            "fixed_margin_usdt": Decimal("10"),
            "trades": [],
        }
    )

    assert stats["signals_traded"] == 0
    assert stats["closed_count"] == 0
    assert stats["win_rate_pct"] == Decimal("0")
    assert "Win rate: 0.0%" in format_demo_stats(stats)


def test_stats_use_closed_trades_only_for_balance_and_win_rate() -> None:
    raw = {
        "start_balance_usdt": Decimal("1000"),
        "current_balance_usdt": Decimal("1006"),
        "fixed_margin_usdt": Decimal("10"),
        "trades": [
            {"status": "open", "realized_pnl_usdt": None},
            {"status": "closed", "realized_pnl_usdt": Decimal("8")},
            {"status": "closed", "realized_pnl_usdt": Decimal("-2")},
            {"status": "closed", "realized_pnl_usdt": Decimal("0")},
        ],
    }

    stats = compute_demo_stats(raw)

    assert stats["signals_traded"] == 4
    assert stats["open_count"] == 1
    assert stats["closed_win_count"] == 1
    assert stats["closed_loss_count"] == 2
    assert stats["closed_count"] == 3
    assert stats["win_rate_pct"] == Decimal("100") / Decimal("3")
    assert stats["current_balance_usdt"] == Decimal("1006")
    assert stats["net_profit_usdt"] == Decimal("6")
    assert stats["net_profit_pct"] == Decimal("0.6")


def test_stats_format_is_english_and_includes_required_fields() -> None:
    text = format_demo_stats(
        compute_demo_stats(
            {
                "start_balance_usdt": "1000",
                "current_balance_usdt": "1010",
                "fixed_margin_usdt": "10",
                "trades": [
                    {"status": "open", "realized_pnl_usdt": None},
                    {"status": "closed", "realized_pnl_usdt": "10"},
                ],
            }
        )
    )

    for phrase in (
        "Start balance",
        "Current balance",
        "Open",
        "Closed",
        "Win rate",
        "Net profit",
    ):
        assert phrase in text
