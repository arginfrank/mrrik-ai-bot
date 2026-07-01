from __future__ import annotations

import asyncio
from decimal import Decimal

import websockets

from shared.exchange.binance import (
    BinanceFuturesClient,
    parse_exchange_order,
    parse_user_stream_event,
    to_binance_order_side,
    to_binance_position_side,
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


class EmptyWebSocket:
    async def __aenter__(self) -> EmptyWebSocket:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def __aiter__(self) -> EmptyWebSocket:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


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


def test_parse_exchange_order_supports_algo_response_fields() -> None:
    order = parse_exchange_order(
        {
            "algoId": 314,
            "clientAlgoId": "m7-9-sl",
            "symbol": "HBARUSDT",
            "side": "SELL",
            "orderType": "STOP_MARKET",
            "algoStatus": "NEW",
            "triggerPrice": "0.07077",
            "quantity": "100",
            "reduceOnly": "true",
        }
    )

    assert order.exchange_order_id == "314"
    assert order.client_order_id == "m7-9-sl"
    assert order.order_type == "STOP_MARKET"
    assert order.status == "NEW"
    assert order.stop_price == Decimal("0.07077")
    assert order.qty == Decimal("100")
    assert order.reduce_only is True
    assert order.close_position is False


def test_stop_market_uses_algo_endpoint_with_position_side(
    monkeypatch,
) -> None:
    client = BinanceFuturesClient(api_key="", api_secret="")
    calls: list[tuple[str, str, dict[str, object]]] = []

    async def signed_request(
        http_method: str, url_path: str, params: dict[str, object]
    ) -> dict[str, object]:
        calls.append((http_method, url_path, params))
        return dict(params, algoId=1, algoStatus="NEW")

    monkeypatch.setattr(client, "_signed_request", signed_request)
    order = asyncio.run(
        client.place_stop_market(
            symbol="HBARUSDT",
            side="SELL",
            position_side="LONG",
            qty=Decimal("100"),
            stop_price=Decimal("0.07077"),
            client_order_id="m7-9-sl",
        )
    )

    assert calls == [
        (
            "POST",
            "/fapi/v1/algoOrder",
            {
                "algoType": "CONDITIONAL",
                "symbol": "HBARUSDT",
                "side": "SELL",
                "positionSide": "LONG",
                "type": "STOP_MARKET",
                "triggerPrice": "0.07077",
                "quantity": "100",
                "workingType": "MARK_PRICE",
                "clientAlgoId": "m7-9-sl",
            },
        )
    ]
    assert "closePosition" not in calls[0][2]
    assert "reduceOnly" not in calls[0][2]
    assert order.client_order_id == "m7-9-sl"


def test_take_profit_market_uses_algo_endpoint_without_close_position(
    monkeypatch,
) -> None:
    client = BinanceFuturesClient(api_key="", api_secret="")
    calls: list[tuple[str, str, dict[str, object]]] = []

    async def signed_request(
        http_method: str, url_path: str, params: dict[str, object]
    ) -> dict[str, object]:
        calls.append((http_method, url_path, params))
        return dict(params, algoId=2, algoStatus="NEW")

    monkeypatch.setattr(client, "_signed_request", signed_request)
    asyncio.run(
        client.place_take_profit_market(
            symbol="HBARUSDT",
            side="SELL",
            position_side="LONG",
            qty=Decimal("50"),
            stop_price=Decimal("0.07186"),
            client_order_id="m7-9-tp-1",
        )
    )

    assert calls[0] == (
        "POST",
        "/fapi/v1/algoOrder",
        {
            "algoType": "CONDITIONAL",
            "symbol": "HBARUSDT",
            "side": "SELL",
            "positionSide": "LONG",
            "type": "TAKE_PROFIT_MARKET",
            "triggerPrice": "0.07186",
            "quantity": "50",
            "workingType": "MARK_PRICE",
            "clientAlgoId": "m7-9-tp-1",
        },
    )
    assert "closePosition" not in calls[0][2]
    assert "reduceOnly" not in calls[0][2]


def test_entry_orders_include_position_side(monkeypatch) -> None:
    client = BinanceFuturesClient(api_key="", api_secret="")
    calls: list[tuple[str, dict[str, object]]] = []

    async def call(method: str, **kwargs: object) -> dict[str, object]:
        calls.append((method, kwargs))
        return dict(kwargs, orderId=1, status="NEW")

    monkeypatch.setattr(client, "_call", call)
    asyncio.run(
        client.place_entry_market(
            symbol="HBARUSDT",
            side="BUY",
            position_side="LONG",
            qty=Decimal("100"),
            client_order_id="m7-9-entry-market",
        )
    )
    asyncio.run(
        client.place_entry_limit(
            symbol="HBARUSDT",
            side="SELL",
            position_side="SHORT",
            qty=Decimal("100"),
            price=Decimal("0.07145"),
            client_order_id="m7-9-entry-limit",
        )
    )

    assert calls[0][1]["positionSide"] == "LONG"
    assert calls[1][1]["positionSide"] == "SHORT"
    assert all("reduceOnly" not in params for _, params in calls)


def test_get_position_selects_requested_hedge_side(monkeypatch) -> None:
    client = BinanceFuturesClient(api_key="", api_secret="")

    async def call(method: str, **kwargs: object) -> list[dict[str, str]]:
        assert method == "get_position_risk"
        assert kwargs == {"symbol": "HBARUSDT"}
        return [
            {
                "symbol": "HBARUSDT",
                "positionSide": "SHORT",
                "positionAmt": "-25",
                "entryPrice": "0.072",
            },
            {
                "symbol": "HBARUSDT",
                "positionSide": "LONG",
                "positionAmt": "100",
                "entryPrice": "0.07145",
            },
        ]

    monkeypatch.setattr(client, "_call", call)

    long_position = asyncio.run(
        client.get_position(symbol="HBARUSDT", position_side="LONG")
    )
    short_position = asyncio.run(
        client.get_position(symbol="HBARUSDT", position_side="SHORT")
    )

    assert long_position is not None and long_position.qty == Decimal("100")
    assert short_position is not None and short_position.qty == Decimal("-25")


def test_close_position_looks_up_requested_side_and_omits_reduce_only(
    monkeypatch,
) -> None:
    client = BinanceFuturesClient(api_key="", api_secret="")
    calls: list[tuple[str, dict[str, object]]] = []

    async def call(method: str, **kwargs: object) -> object:
        calls.append((method, kwargs))
        if method == "get_position_risk":
            return [
                {
                    "symbol": "HBARUSDT",
                    "positionSide": "LONG",
                    "positionAmt": "100",
                },
                {
                    "symbol": "HBARUSDT",
                    "positionSide": "SHORT",
                    "positionAmt": "-25",
                },
            ]
        return dict(kwargs, orderId=1, status="NEW")

    monkeypatch.setattr(client, "_call", call)
    asyncio.run(
        client.close_position_market(
            symbol="HBARUSDT",
            side="BUY",
            position_side="SHORT",
            qty=None,
            client_order_id="m7-9-close",
        )
    )

    assert calls[0] == ("get_position_risk", {"symbol": "HBARUSDT"})
    assert calls[1][0] == "new_order"
    assert calls[1][1]["positionSide"] == "SHORT"
    assert calls[1][1]["quantity"] == "25"
    assert "reduceOnly" not in calls[1][1]


def test_open_and_cancel_algo_orders_use_signed_algo_endpoints(monkeypatch) -> None:
    client = BinanceFuturesClient(api_key="", api_secret="")
    calls: list[tuple[str, str, dict[str, object]]] = []

    async def signed_request(
        http_method: str, url_path: str, params: dict[str, object]
    ) -> object:
        calls.append((http_method, url_path, params))
        if http_method == "GET":
            return [
                {
                    "algoId": 314,
                    "clientAlgoId": "m7-9-sl",
                    "algoStatus": "NEW",
                    "triggerPrice": "0.07077",
                }
            ]
        return {}

    monkeypatch.setattr(client, "_signed_request", signed_request)
    orders = asyncio.run(client.get_open_algo_orders(symbol="HBARUSDT"))
    asyncio.run(client.cancel_algo_order(client_order_id="m7-9-sl"))

    assert orders[0].client_order_id == "m7-9-sl"
    assert orders[0].exchange_order_id == "314"
    assert orders[0].status == "NEW"
    assert orders[0].stop_price == Decimal("0.07077")
    assert calls == [
        ("GET", "/fapi/v1/openAlgoOrders", {"symbol": "HBARUSDT"}),
        ("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": "m7-9-sl"}),
    ]


def test_regular_get_open_orders_uses_plural_connector_method(monkeypatch) -> None:
    client = BinanceFuturesClient(api_key="", api_secret="")
    calls: list[tuple[str, dict[str, object]]] = []

    async def call(method: str, **kwargs: object) -> list[object]:
        calls.append((method, kwargs))
        return []

    monkeypatch.setattr(client, "_call", call)

    assert asyncio.run(client.get_open_orders(symbol="HBARUSDT")) == []
    assert calls == [("get_orders", {"symbol": "HBARUSDT"})]


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
    assert to_binance_position_side(trade_side="LONG") == "LONG"
    assert to_binance_position_side(trade_side="short") == "SHORT"


def test_mark_price_stream_uses_production_market_route(monkeypatch) -> None:
    connected_urls: list[str] = []

    def connect(url: str) -> EmptyWebSocket:
        connected_urls.append(url)
        return EmptyWebSocket()

    monkeypatch.setattr(websockets, "connect", connect)
    client = BinanceFuturesClient(api_key="", api_secret="")

    async def consume_stream() -> None:
        async for _ in client.mark_price_stream(["ETHUSDT", "BTCUSDT", "ETHUSDT"]):
            pass

    asyncio.run(consume_stream())

    assert connected_urls == [
        "wss://fstream.binance.com/market/stream?streams="
        "btcusdt@markPrice@1s/ethusdt@markPrice@1s"
    ]
    assert not connected_urls[0].startswith(
        "wss://fstream.binance.com/stream?streams="
    )
