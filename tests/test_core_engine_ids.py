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
