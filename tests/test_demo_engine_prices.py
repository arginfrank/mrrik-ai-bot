from __future__ import annotations

from decimal import Decimal

from services.demo_engine.prices import (
    build_mark_price_stream_url,
    normalize_stream_symbol,
    parse_mark_price_message,
)


def test_normalize_and_build_single_stream_url() -> None:
    assert normalize_stream_symbol("ETHUSDT") == "ethusdt"
    assert "ethusdt@markPrice@1s" in build_mark_price_stream_url(["ETHUSDT"])


def test_combined_stream_url_is_sorted_and_deduplicated() -> None:
    url = build_mark_price_stream_url(["ethusdt", "BTCUSDT", "ETHUSDT"])

    assert url.endswith("btcusdt@markPrice@1s/ethusdt@markPrice@1s")


def test_parse_raw_mark_price_message_uses_decimal() -> None:
    parsed = parse_mark_price_message(
        '{"e":"markPriceUpdate","s":"ETHUSDT","p":"1581.72000000"}'
    )

    assert parsed is not None
    assert parsed.symbol == "ETHUSDT"
    assert parsed.price == Decimal("1581.72000000")
    assert isinstance(parsed.price, Decimal)


def test_parse_combined_mark_price_message() -> None:
    parsed = parse_mark_price_message(
        '{"stream":"ethusdt@markPrice@1s","data":'
        '{"e":"markPriceUpdate","s":"ETHUSDT","p":"2000.01"}}'
    )

    assert parsed is not None
    assert parsed.price == Decimal("2000.01")


def test_invalid_and_unrelated_messages_are_ignored() -> None:
    assert parse_mark_price_message("not json") is None
    assert parse_mark_price_message('{"e":"aggTrade","s":"ETHUSDT","p":"1"}') is None
    assert parse_mark_price_message('{"e":"markPriceUpdate","s":"ETHUSDT"}') is None
