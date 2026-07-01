from __future__ import annotations

import re

from services.core_engine.ids import client_order_id


def test_client_order_ids_are_deterministic_and_descriptive() -> None:
    first = client_order_id(trade_id=123, purpose="entry")
    second = client_order_id(trade_id=123, purpose="entry")

    assert first == second
    assert "123" in first
    assert "entry" in first
    assert len(first) <= 36
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)


def test_tp_id_contains_leg_index() -> None:
    assert client_order_id(trade_id=7, purpose="tp", leg_index=3).endswith("tp-3")


def test_algo_client_ids_fit_binance_pattern_and_length() -> None:
    values = (
        client_order_id(trade_id=9_223_372_036_854_775_807, purpose="sl"),
        client_order_id(
            trade_id=9_223_372_036_854_775_807,
            purpose="tp",
            leg_index=2_147_483_647,
        ),
        client_order_id(trade_id=9_223_372_036_854_775_807, purpose="be_sl"),
        client_order_id(
            trade_id=9_223_372_036_854_775_807, purpose="emergency_close"
        ),
    )

    assert len(set(values)) == len(values)
    assert all(len(value) <= 36 for value in values)
    assert all(re.fullmatch(r"[.A-Z:/a-z0-9_-]{1,36}", value) for value in values)
