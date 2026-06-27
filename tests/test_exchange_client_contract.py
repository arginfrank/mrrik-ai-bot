from __future__ import annotations

from decimal import Decimal

from shared.exchange.binance import (
    parse_exchange_order,
    parse_user_stream_event,
    to_binance_order_side,
)
from shared.exchange.types import ExchangeOrder, SymbolFilters


class FakeExchangeClient:
    async def verify_credentials(self) -> bool:
        return True

    async def verify_withdrawals_disabled(self) -> bool:
        return True

    async def get_usdt_balance(self) -> Decimal:
        return Decimal("100")

    async def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        return SymbolFilters(
            symbol=symbol,
            step_size=Decimal("1"),
            tick_size=Decimal("0.00001"),
            min_qty=Decimal("1"),
            min_notional=Decimal("5"),
        )


def test_fake_client_exposes_exchange_contract_methods() -> None:
    fake = FakeExchangeClient()
    for name in (
        "verify_credentials",
        "verify_withdrawals_disabled",
        "get_usdt_balance",
        "get_symbol_filters",
    ):
        assert callable(getattr(fake, name))


def test_parse_exchange_order_uses_decimal_strings() -> None:
    order = parse_exchange_order(
        {
            "orderId": 42,
            "clientOrderId": "mrrik-1-tp-1",
            "symbol": "HBARUSDT",
            "side": "SELL",
            "type": "TAKE_PROFIT_MARKET",
            "status": "NEW",
            "stopPrice": "0.07186",
            "origQty": "50",
            "reduceOnly": True,
        }
    )

    assert isinstance(order, ExchangeOrder)
    assert order.exchange_order_id == "42"
    assert order.stop_price == Decimal("0.07186")
    assert order.qty == Decimal("50")
    assert order.reduce_only is True


def test_parse_user_stream_order_fill() -> None:
    event = parse_user_stream_event(
        {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "s": "HBARUSDT",
                "c": "mrrik-1-tp-1",
                "i": 99,
                "X": "FILLED",
                "x": "TRADE",
                "l": "50",
                "z": "50",
                "L": "0.07186",
                "rp": "0.25",
            },
        }
    )

    assert event is not None
    assert event.order_status == "FILLED"
    assert event.last_filled_qty == Decimal("50")
    assert event.last_filled_price == Decimal("0.07186")


def test_order_side_mapping() -> None:
    assert to_binance_order_side(trade_side="LONG", action="open") == "BUY"
    assert to_binance_order_side(trade_side="LONG", action="close") == "SELL"
    assert to_binance_order_side(trade_side="SHORT", action="open") == "SELL"
    assert to_binance_order_side(trade_side="SHORT", action="close") == "BUY"
