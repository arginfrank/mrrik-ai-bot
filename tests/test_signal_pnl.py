from decimal import Decimal

from shared.signal import (
    ParsedResultMessage,
    SignalSide,
    approximate_liquidation_price,
    capped_loss_usdt_on_liquidation,
    liquidation_happens_before_stop,
    parse_message,
    pnl_usdt_for_fraction,
    roi_percent_on_margin,
    signed_move_fraction,
)


ETH_RESULT = """#ETH/USDT

Target Tuch 1 ✅

Profit: 9.3544% 📈
Period: 14 Minutes ⏰
"""


def test_eth_result_percentage_is_parsed_for_logging_only() -> None:
    parsed = parse_message(ETH_RESULT)

    assert isinstance(parsed, ParsedResultMessage)
    assert parsed.profit_pct == Decimal("9.3544")


def test_eth_roi_formula_reproduces_logged_percentage() -> None:
    entry = Decimal("1581.72")
    expected_roi = Decimal("9.3544")
    price = (
        entry
        * (
            Decimal("1")
            + expected_roi / Decimal("100") / Decimal("54")
        )
    ).quantize(Decimal("0.0001"))

    roi = roi_percent_on_margin(
        side=SignalSide.LONG,
        entry=entry,
        price=price,
        leverage=54,
    )

    assert roi.quantize(Decimal("0.0001")) == expected_roi


def test_agld_like_loss_liquidates_and_caps_at_one_margin() -> None:
    entry = Decimal("100")
    leverage = 10
    reported_loss_pct = Decimal("243.4286")
    implied_stop = entry * (
        Decimal("1") - reported_loss_pct / Decimal("100") / Decimal(leverage)
    )

    assert liquidation_happens_before_stop(
        side=SignalSide.LONG,
        entry=entry,
        stop_loss=implied_stop,
        leverage=leverage,
        maintenance_margin_rate=Decimal("0.005"),
    )
    assert capped_loss_usdt_on_liquidation(margin_usdt=Decimal("10")) == Decimal("-10")


def test_hbar_normal_stop_has_uncapped_formula_loss() -> None:
    roi = roi_percent_on_margin(
        side=SignalSide.LONG,
        entry=Decimal("0.07145"),
        price=Decimal("0.07077"),
        leverage=42,
    )
    pnl = pnl_usdt_for_fraction(
        margin_usdt=Decimal("10"),
        position_fraction=Decimal("1"),
        side=SignalSide.LONG,
        entry=Decimal("0.07145"),
        price=Decimal("0.07077"),
        leverage=42,
    )

    assert roi.quantize(Decimal("0.0001")) == Decimal("-39.9720")
    assert pnl.quantize(Decimal("0.0001")) == Decimal("-3.9972")
    assert not liquidation_happens_before_stop(
        side=SignalSide.LONG,
        entry=Decimal("0.07145"),
        stop_loss=Decimal("0.07077"),
        leverage=42,
        maintenance_margin_rate=Decimal("0.005"),
    )


def test_long_and_short_signed_moves_have_correct_signs() -> None:
    assert signed_move_fraction(
        side=SignalSide.LONG,
        entry=Decimal("100"),
        price=Decimal("110"),
    ) == Decimal("0.1")
    assert signed_move_fraction(
        side=SignalSide.SHORT,
        entry=Decimal("100"),
        price=Decimal("90"),
    ) == Decimal("0.1")
    assert signed_move_fraction(
        side=SignalSide.SHORT,
        entry=Decimal("100"),
        price=Decimal("110"),
    ) == Decimal("-0.1")


def test_approximate_liquidation_prices_are_on_loss_sides() -> None:
    long_liquidation = approximate_liquidation_price(
        side=SignalSide.LONG,
        entry=Decimal("100"),
        leverage=10,
        maintenance_margin_rate=Decimal("0.005"),
    )
    short_liquidation = approximate_liquidation_price(
        side=SignalSide.SHORT,
        entry=Decimal("100"),
        leverage=10,
        maintenance_margin_rate=Decimal("0.005"),
    )

    assert long_liquidation < Decimal("100")
    assert short_liquidation > Decimal("100")
