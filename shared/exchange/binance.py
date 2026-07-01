from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from decimal import Decimal
import hashlib
import hmac
import json
import time
from typing import Any, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from shared.exchange.types import (
    ExchangeOrder,
    MarkPrice,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSnapshot,
    SymbolFilters,
    UserStreamEvent,
)


_PRODUCTION_REST_URL = "https://fapi.binance.com"
_TESTNET_REST_URL = "https://testnet.binancefuture.com"
_PRODUCTION_WS_URL = "wss://fstream.binance.com"
_PRODUCTION_MARKET_WS_URL = f"{_PRODUCTION_WS_URL}/market"
_TESTNET_WS_URL = "wss://stream.binancefuture.com"
_API_RESTRICTIONS_URL = "https://api.binance.com/sapi/v1/account/apiRestrictions"


class BinanceFuturesClient:
    """Async facade over the official synchronous USD-M Futures connector."""

    def __init__(self, *, api_key: str, api_secret: str, testnet: bool = False) -> None:
        from binance.um_futures import UMFutures

        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._rest = UMFutures(
            key=api_key,
            secret=api_secret,
            base_url=_TESTNET_REST_URL if testnet else _PRODUCTION_REST_URL,
        )
        self._ws_url = _TESTNET_WS_URL if testnet else _PRODUCTION_WS_URL
        self._market_ws_url = (
            _TESTNET_WS_URL if testnet else _PRODUCTION_MARKET_WS_URL
        )

    async def verify_credentials(self) -> bool:
        try:
            await self._call("account")
        except Exception:
            return False
        return True

    async def verify_withdrawals_disabled(self) -> bool:
        """Verify Binance's key restriction flag; unknown is always unsafe."""
        if self._testnet:
            return False
        try:
            restrictions = await asyncio.to_thread(self._fetch_api_restrictions)
        except Exception:
            return False
        value = restrictions.get("enableWithdrawals")
        return value is False

    async def get_usdt_balance(self) -> Decimal:
        balances = await self._call("balance")
        for balance in balances:
            if balance.get("asset") == "USDT":
                value = balance.get("availableBalance", balance.get("withdrawAvailable", "0"))
                return Decimal(str(value))
        return Decimal("0")

    async def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        exchange_info = await self._call("exchange_info")
        wanted = symbol.upper()
        for item in exchange_info.get("symbols", []):
            if item.get("symbol") != wanted:
                continue
            filters = {entry["filterType"]: entry for entry in item.get("filters", [])}
            lot_size = filters.get("LOT_SIZE", {})
            price_filter = filters.get("PRICE_FILTER", {})
            notional_filter = filters.get("MIN_NOTIONAL", filters.get("NOTIONAL", {}))
            min_notional = notional_filter.get(
                "notional", notional_filter.get("minNotional", "0")
            )
            return SymbolFilters(
                symbol=wanted,
                step_size=Decimal(str(lot_size["stepSize"])),
                tick_size=Decimal(str(price_filter["tickSize"])),
                min_qty=Decimal(str(lot_size["minQty"])),
                min_notional=Decimal(str(min_notional)),
            )
        raise ValueError(f"symbol is not available on Binance Futures: {wanted}")

    async def set_leverage(self, *, symbol: str, leverage: int) -> None:
        await self._call("change_leverage", symbol=symbol, leverage=leverage)

    async def set_margin_type_isolated(self, *, symbol: str) -> None:
        try:
            await self._call("change_margin_type", symbol=symbol, marginType="ISOLATED")
        except Exception as error:
            error_code = getattr(error, "error_code", None)
            if error_code != -4046 and "No need to change margin type" not in str(error):
                raise

    async def place_entry_limit(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        client_order_id: str,
    ) -> ExchangeOrder:
        raw = await self._call(
            "new_order",
            symbol=symbol,
            side=side,
            type="LIMIT",
            timeInForce="GTC",
            quantity=_decimal_string(qty),
            price=_decimal_string(price),
            newClientOrderId=client_order_id,
        )
        return parse_exchange_order(raw)

    async def place_entry_market(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        client_order_id: str,
    ) -> ExchangeOrder:
        raw = await self._call(
            "new_order",
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=_decimal_string(qty),
            newClientOrderId=client_order_id,
        )
        return parse_exchange_order(raw)

    async def place_stop_market(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        stop_price: Decimal,
        client_order_id: str,
    ) -> ExchangeOrder:
        raw = await self._signed_request(
            "POST",
            "/fapi/v1/algoOrder",
            {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "triggerPrice": _decimal_string(stop_price),
                "quantity": _decimal_string(qty),
                "reduceOnly": "true",
                "workingType": "MARK_PRICE",
                "clientAlgoId": client_order_id,
            },
        )
        return parse_exchange_order(raw)

    async def place_take_profit_market(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        stop_price: Decimal,
        client_order_id: str,
        reduce_only: bool,
    ) -> ExchangeOrder:
        raw = await self._signed_request(
            "POST",
            "/fapi/v1/algoOrder",
            {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "triggerPrice": _decimal_string(stop_price),
                "quantity": _decimal_string(qty),
                "reduceOnly": "true",
                "workingType": "MARK_PRICE",
                "clientAlgoId": client_order_id,
            },
        )
        return parse_exchange_order(raw)

    async def cancel_order(self, *, symbol: str, client_order_id: str) -> None:
        await self._call(
            "cancel_order", symbol=symbol, origClientOrderId=client_order_id
        )

    async def cancel_open_orders(self, *, symbol: str) -> None:
        await self._call("cancel_open_orders", symbol=symbol)

    async def cancel_algo_order(self, *, client_order_id: str) -> None:
        await self._signed_request(
            "DELETE",
            "/fapi/v1/algoOrder",
            {"clientAlgoId": client_order_id},
        )

    async def cancel_all_algo_orders(self, *, symbol: str) -> None:
        await self._signed_request(
            "DELETE",
            "/fapi/v1/algoOpenOrders",
            {"symbol": symbol},
        )

    async def close_position_market(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal | None,
        client_order_id: str,
    ) -> ExchangeOrder:
        close_qty = qty
        if close_qty is None:
            position = await self.get_position(symbol=symbol)
            if position is None:
                raise ValueError("cannot close a missing position")
            close_qty = abs(position.qty)
        raw = await self._call(
            "new_order",
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=_decimal_string(close_qty),
            reduceOnly=True,
            newClientOrderId=client_order_id,
        )
        return parse_exchange_order(raw)

    async def get_position(self, *, symbol: str) -> PositionSnapshot | None:
        positions = await self._call("get_position_risk", symbol=symbol)
        for raw in positions:
            if raw.get("symbol") != symbol.upper():
                continue
            if raw.get("positionSide", "BOTH") != "BOTH":
                continue
            qty = Decimal(str(raw.get("positionAmt", "0")))
            if qty == 0:
                return None
            return PositionSnapshot(
                symbol=symbol.upper(),
                qty=qty,
                entry_price=_optional_decimal(raw.get("entryPrice")),
                mark_price=_optional_decimal(raw.get("markPrice")),
                liquidation_price=_optional_decimal(raw.get("liquidationPrice")),
                unrealized_pnl=_optional_decimal(raw.get("unRealizedProfit")),
            )
        return None

    async def get_open_orders(
        self, *, symbol: str | None = None
    ) -> list[ExchangeOrder]:
        values = await self._call(
            "get_orders", **({"symbol": symbol} if symbol is not None else {})
        )
        return [parse_exchange_order(raw) for raw in values]

    async def get_open_algo_orders(
        self, *, symbol: str | None = None
    ) -> list[ExchangeOrder]:
        values = await self._signed_request(
            "GET",
            "/fapi/v1/openAlgoOrders",
            {"symbol": symbol},
        )
        return [parse_exchange_order(raw) for raw in values]

    async def user_stream(self) -> AsyncIterator[UserStreamEvent]:
        import websockets

        listen_key_raw = await self._call("new_listen_key")
        listen_key = str(listen_key_raw["listenKey"])
        renew_task = asyncio.create_task(self._renew_listen_key(listen_key))
        try:
            async with websockets.connect(f"{self._ws_url}/ws/{listen_key}") as socket:
                async for message in socket:
                    raw = json.loads(message)
                    event = parse_user_stream_event(raw)
                    if event is not None:
                        yield event
        finally:
            renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await renew_task
            with suppress(Exception):
                await self._call("close_listen_key", listenKey=listen_key)

    async def mark_price_stream(self, symbols: list[str]) -> AsyncIterator[MarkPrice]:
        import websockets

        normalized = sorted({symbol.lower() for symbol in symbols if symbol})
        if not normalized:
            return
        streams = "/".join(f"{symbol}@markPrice@1s" for symbol in normalized)
        async with websockets.connect(
            f"{self._market_ws_url}/stream?streams={streams}"
        ) as socket:
            async for message in socket:
                raw = json.loads(message)
                data = raw.get("data", raw)
                symbol = data.get("s")
                mark_price = data.get("p")
                if symbol is not None and mark_price is not None:
                    yield MarkPrice(symbol=str(symbol), price=Decimal(str(mark_price)))

    async def _call(self, method: str, **kwargs: Any) -> Any:
        function = getattr(self._rest, method)
        return await asyncio.to_thread(function, **kwargs)

    async def _signed_request(
        self, http_method: str, url_path: str, params: dict[str, Any]
    ) -> Any:
        payload = {key: value for key, value in params.items() if value is not None}
        return await asyncio.to_thread(
            self._rest.sign_request, http_method, url_path, payload
        )

    async def _renew_listen_key(self, listen_key: str) -> None:
        while True:
            await asyncio.sleep(30 * 60)
            await self._call("renew_listen_key", listenKey=listen_key)

    def _fetch_api_restrictions(self) -> dict[str, Any]:
        timestamp = int(time.time() * 1000)
        query = urlencode({"timestamp": timestamp, "recvWindow": 5000})
        signature = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        request = Request(
            f"{_API_RESTRICTIONS_URL}?{query}&signature={signature}",
            headers={"X-MBX-APIKEY": self._api_key},
        )
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed HTTPS URL
            payload = json.loads(response.read())
        return payload if isinstance(payload, dict) else {}


def parse_exchange_order(raw: dict[str, Any]) -> ExchangeOrder:
    """Parse a Binance order response into ExchangeOrder."""
    return ExchangeOrder(
        exchange_order_id=str(
            raw.get("orderId", raw.get("algoId", raw.get("i", "")))
        ),
        client_order_id=str(
            raw.get("clientOrderId", raw.get("clientAlgoId", raw.get("c", "")))
        ),
        symbol=str(raw.get("symbol", raw.get("s", ""))),
        side=cast(OrderSide, str(raw.get("side", raw.get("S", "BUY")))),
        order_type=cast(
            OrderType, str(raw.get("type", raw.get("orderType", raw.get("o", "MARKET"))))
        ),
        status=cast(
            OrderStatus, str(raw.get("status", raw.get("algoStatus", raw.get("X", "NEW"))))
        ),
        price=_optional_decimal(raw.get("price", raw.get("p"))),
        stop_price=_optional_decimal(
            raw.get("stopPrice", raw.get("triggerPrice", raw.get("sp")))
        ),
        qty=_optional_decimal(raw.get("origQty", raw.get("quantity", raw.get("q")))),
        reduce_only=_as_bool(raw.get("reduceOnly", raw.get("R", False))),
        close_position=_as_bool(raw.get("closePosition", raw.get("cp", False))),
        raw=dict(raw),
    )


def parse_user_stream_event(raw: dict[str, Any]) -> UserStreamEvent | None:
    """Parse ORDER_TRADE_UPDATE / account events into a normalized user event."""
    event_type = raw.get("e")
    if event_type == "ORDER_TRADE_UPDATE":
        order = raw.get("o")
        if not isinstance(order, dict):
            return None
        return UserStreamEvent(
            event_type="ORDER_TRADE_UPDATE",
            symbol=_optional_string(order.get("s")),
            client_order_id=_optional_string(order.get("c")),
            exchange_order_id=_optional_string(order.get("i")),
            order_status=_optional_string(order.get("X")),
            execution_type=_optional_string(order.get("x")),
            last_filled_qty=_optional_decimal(order.get("l")),
            cumulative_filled_qty=_optional_decimal(order.get("z")),
            last_filled_price=_optional_decimal(order.get("L")),
            realized_pnl=_optional_decimal(order.get("rp")),
            raw=dict(raw),
        )
    if event_type in {"ACCOUNT_UPDATE", "MARGIN_CALL", "ACCOUNT_CONFIG_UPDATE"}:
        return UserStreamEvent(event_type=str(event_type), raw=dict(raw))
    return None


def to_binance_order_side(*, trade_side: str, action: str) -> str:
    """Map LONG/SHORT + open/close to BUY/SELL."""
    normalized_side = trade_side.upper()
    normalized_action = action.lower()
    if normalized_side not in {"LONG", "SHORT"}:
        raise ValueError("trade_side must be LONG or SHORT")
    if normalized_action not in {"open", "close"}:
        raise ValueError("action must be open or close")
    if normalized_side == "LONG":
        return "BUY" if normalized_action == "open" else "SELL"
    return "SELL" if normalized_action == "open" else "BUY"


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _decimal_string(value: Decimal) -> str:
    return format(value, "f")
