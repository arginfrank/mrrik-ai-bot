from __future__ import annotations

from decimal import Decimal

from shared.exchange.types import (
    BalanceSnapshot,
    ExchangeOrder,
    MarkPrice,
    PositionSnapshot,
    SymbolFilters,
)


def test_exchange_dtos_keep_decimal_values() -> None:
    filters = SymbolFilters(
        symbol="HBARUSDT",
        step_size=Decimal("1"),
        tick_size=Decimal("0.00001"),
        min_qty=Decimal("1"),
        min_notional=Decimal("5"),
    )
    balance = BalanceSnapshot(
        asset="USDT", free=Decimal("12.34"), total=Decimal("20")
    )
    position = PositionSnapshot(
        symbol="HBARUSDT",
        qty=Decimal("100"),
        entry_price=Decimal("0.07145"),
        mark_price=Decimal("0.072"),
        liquidation_price=Decimal("0.0701"),
        unrealized_pnl=Decimal("0.55"),
    )
    order = ExchangeOrder(
        exchange_order_id="1",
        client_order_id="mrrik-1-entry",
        symbol="HBARUSDT",
        side="BUY",
        order_type="LIMIT",
        status="NEW",
        price=Decimal("0.07145"),
        qty=Decimal("100"),
    )
    mark = MarkPrice(symbol="HBARUSDT", price=Decimal("0.072"))

    values = (
        filters.step_size,
        balance.free,
        position.entry_price,
        order.price,
        mark.price,
    )
    assert all(isinstance(value, Decimal) for value in values)
    assert not any(isinstance(value, float) for value in values)
