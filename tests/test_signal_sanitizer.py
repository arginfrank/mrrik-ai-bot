from decimal import Decimal

from shared.signal import (
    MessageKind,
    ParsedEntrySignal,
    RejectedSignal,
    SanitizedSignal,
    SignalSide,
    sanitize_signal,
)


def make_signal(
    *,
    symbol: str = "TESTUSDT",
    side: SignalSide = SignalSide.LONG,
    entry: str = "100",
    stop_loss: str = "95",
    leverage: int = 10,
    targets: tuple[str, ...],
) -> ParsedEntrySignal:
    return ParsedEntrySignal(
        kind=MessageKind.ENTRY,
        symbol=symbol,
        side=side,
        entry=Decimal(entry),
        stop_loss=Decimal(stop_loss),
        leverage=leverage,
        targets=tuple(Decimal(target) for target in targets),
        raw_text="fixture",
    )


def test_hbar_drops_out_of_order_first_target() -> None:
    signal = make_signal(
        symbol="HBARUSDT",
        entry="0.07145",
        stop_loss="0.07077",
        leverage=42,
        targets=("0.072", "0.07186", "0.07238", "0.07296", "0.07309"),
    )

    result = sanitize_signal(signal)

    assert isinstance(result, SanitizedSignal)
    assert result.targets_clean == (
        Decimal("0.07186"),
        Decimal("0.07238"),
        Decimal("0.07296"),
        Decimal("0.07309"),
    )
    assert result.dropped == (Decimal("0.072"),)
    assert result.alert is True


def test_eth_drops_out_of_order_fourth_target() -> None:
    signal = make_signal(
        symbol="ETHUSDT",
        entry="1581.72",
        stop_loss="1572.68914",
        leverage=54,
        targets=("1584.4697", "1589.97695", "1593.8519", "1608.13271", "1604.9578"),
    )

    result = sanitize_signal(signal)

    assert isinstance(result, SanitizedSignal)
    assert result.targets_clean == (
        Decimal("1584.4697"),
        Decimal("1589.97695"),
        Decimal("1593.8519"),
        Decimal("1604.9578"),
    )
    assert result.dropped == (Decimal("1608.13271"),)
    assert result.alert is True


def test_avax_is_accepted_unchanged() -> None:
    signal = make_signal(
        symbol="AVAXUSDT",
        entry="6.65",
        stop_loss="6.545",
        leverage=19,
        targets=("6.79114", "7.08196", "7.38137"),
    )

    result = sanitize_signal(signal)

    assert isinstance(result, SanitizedSignal)
    assert result.targets_clean == signal.targets
    assert result.dropped == ()
    assert result.corrected == ()
    assert result.alert is False


def test_decimal_shift_target_is_corrected_without_invention() -> None:
    signal = make_signal(
        entry="0.07145",
        stop_loss="0.07077",
        targets=("0.7186", "0.07238", "0.07296"),
    )

    result = sanitize_signal(signal)

    assert isinstance(result, SanitizedSignal)
    assert result.targets_clean == (
        Decimal("0.07186"),
        Decimal("0.07238"),
        Decimal("0.07296"),
    )
    assert len(result.corrected) == 1
    assert result.corrected[0].original == Decimal("0.7186")
    assert result.corrected[0].corrected == Decimal("0.07186")
    assert result.corrected[0].reason == "decimal_shift"
    assert result.alert is True


def test_wrong_side_stop_loss_rejects_whole_signal() -> None:
    signal = make_signal(stop_loss="101", targets=("102",))

    result = sanitize_signal(signal)

    assert result == RejectedSignal(reason="wrong_side_stop_loss", alert=True)


def test_wrong_side_target_is_dropped() -> None:
    signal = make_signal(targets=("99", "101", "102"))

    result = sanitize_signal(signal)

    assert isinstance(result, SanitizedSignal)
    assert result.targets_clean == (Decimal("101"), Decimal("102"))
    assert result.dropped == (Decimal("99"),)
    assert result.alert is True


def test_short_signal_keeps_strict_descending_targets_deterministically() -> None:
    signal = make_signal(
        side=SignalSide.SHORT,
        entry="100",
        stop_loss="105",
        targets=("98", "99", "97", "96"),
    )

    result = sanitize_signal(signal)

    assert isinstance(result, SanitizedSignal)
    assert result.targets_clean == (Decimal("99"), Decimal("97"), Decimal("96"))
    assert result.dropped == (Decimal("98"),)
    assert result.alert is True


def test_all_wrong_side_targets_reject_signal() -> None:
    signal = make_signal(targets=("99", "98"))

    result = sanitize_signal(signal)

    assert result == RejectedSignal(reason="no_viable_targets", alert=True)
