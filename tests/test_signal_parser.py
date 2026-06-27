from decimal import Decimal

import pytest

from shared.signal import (
    MessageKind,
    ParsedEntrySignal,
    ParsedResultMessage,
    ParsedStopMessage,
    SignalParseError,
    SignalSide,
    parse_entry_signal,
    parse_message,
)


HBAR_SIGNAL = """VIP CRYPTO JEMAL
#HBAR/USDT - Long🟢
Entry: 0.07145
Stop Loss: 0.07077

Target 1: 0.072
Target 2: 0.07186
Target 3: 0.07238
Target 4: 0.07296
Target 5: 0.07309

Leverage: x42
"""

ETH_SIGNAL = (
    "#ETH/USDT - Long🟢 Entry: 1581.72 Stop Loss: 1572.68914 "
    "Target 1: 1584.4697 Target 2: 1589.97695 Target 3: 1593.8519 "
    "Target 4: 1608.13271 Target 5: 1604.9578 Leverage: x54"
)

AVAX_SIGNAL = """#AVAX/USDT - Long🟢

Entry: 6.65
Stop Loss: 6.545

Target 1: 6.79114
Target 2: 7.08196
Target 3: 7.38137

Leverage: x19
"""

ETH_RESULT = """#ETH/USDT

Target Tuch 1 ✅

Profit: 9.3544% 📈
Period: 14 Minutes ⏰
"""

AGLD_STOP = """#AGLD/USDT

Stop Target Hit ⛔

Loss: 243.4286% 📉
"""


def test_hbar_multiline_entry_parses() -> None:
    parsed = parse_message(HBAR_SIGNAL)

    assert isinstance(parsed, ParsedEntrySignal)
    assert parsed.kind is MessageKind.ENTRY
    assert parsed.symbol == "HBARUSDT"
    assert parsed.side is SignalSide.LONG
    assert parsed.entry == Decimal("0.07145")
    assert parsed.stop_loss == Decimal("0.07077")
    assert parsed.leverage == 42
    assert parsed.targets == (
        Decimal("0.072"),
        Decimal("0.07186"),
        Decimal("0.07238"),
        Decimal("0.07296"),
        Decimal("0.07309"),
    )


def test_eth_single_line_entry_parses() -> None:
    parsed = parse_message(ETH_SIGNAL)

    assert isinstance(parsed, ParsedEntrySignal)
    assert parsed.symbol == "ETHUSDT"
    assert parsed.side is SignalSide.LONG
    assert parsed.entry == Decimal("1581.72")
    assert parsed.stop_loss == Decimal("1572.68914")
    assert parsed.leverage == 54
    assert parsed.targets == (
        Decimal("1584.4697"),
        Decimal("1589.97695"),
        Decimal("1593.8519"),
        Decimal("1608.13271"),
        Decimal("1604.9578"),
    )


def test_avax_multiline_entry_parses() -> None:
    parsed = parse_message(AVAX_SIGNAL)

    assert isinstance(parsed, ParsedEntrySignal)
    assert parsed.symbol == "AVAXUSDT"
    assert parsed.side is SignalSide.LONG
    assert parsed.entry == Decimal("6.65")
    assert parsed.stop_loss == Decimal("6.545")
    assert parsed.leverage == 19
    assert parsed.targets == (
        Decimal("6.79114"),
        Decimal("7.08196"),
        Decimal("7.38137"),
    )


def test_eth_result_message_parses_as_logging_data() -> None:
    parsed = parse_message(ETH_RESULT)

    assert isinstance(parsed, ParsedResultMessage)
    assert parsed.kind is MessageKind.RESULT
    assert parsed.symbol == "ETHUSDT"
    assert parsed.target_index == 1
    assert parsed.profit_pct == Decimal("9.3544")
    assert parsed.period_minutes == 14


def test_agld_stop_message_parses_as_logging_data() -> None:
    parsed = parse_message(AGLD_STOP)

    assert isinstance(parsed, ParsedStopMessage)
    assert parsed.kind is MessageKind.STOP
    assert parsed.symbol == "AGLDUSDT"
    assert parsed.loss_pct == Decimal("243.4286")


def test_mismatched_word_and_emoji_side_rejects() -> None:
    with pytest.raises(SignalParseError, match="disagree"):
        parse_entry_signal(HBAR_SIGNAL.replace("Long🟢", "Long🔴"))


def test_missing_leverage_rejects_entry_signal() -> None:
    without_leverage = HBAR_SIGNAL.replace("Leverage: x42", "")

    with pytest.raises(SignalParseError, match="missing leverage"):
        parse_message(without_leverage)


def test_valid_symbol_collection_rejects_unknown_symbol() -> None:
    with pytest.raises(SignalParseError, match="unknown symbol"):
        parse_entry_signal(HBAR_SIGNAL, valid_symbols={"ETHUSDT", "AVAXUSDT"})


def test_valid_symbol_collection_accepts_known_symbol() -> None:
    parsed = parse_entry_signal(HBAR_SIGNAL, valid_symbols={"HBARUSDT"})

    assert parsed.symbol == "HBARUSDT"


def test_unrelated_text_returns_none() -> None:
    assert parse_message("Good morning, traders!") is None


def test_internal_spaces_in_decimal_tokens_are_tolerated() -> None:
    parsed = parse_entry_signal(HBAR_SIGNAL.replace("0.07145", "0 .07145"))

    assert parsed.entry == Decimal("0.07145")
