from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Protocol, runtime_checkable

from shared.exchange.types import (
    ExchangeOrder,
    MarkPrice,
    PositionSnapshot,
    SymbolFilters,
    UserStreamEvent,
)


@runtime_checkable
class ExchangeClient(Protocol):
    async def verify_credentials(self) -> bool: ...

    async def verify_withdrawals_disabled(self) -> bool:
        """Return True only when withdrawals are disabled or not permitted."""

        ...

    async def get_usdt_balance(self) -> Decimal: ...

    async def get_symbol_filters(self, symbol: str) -> SymbolFilters: ...

    async def set_leverage(self, *, symbol: str, leverage: int) -> None: ...

    async def set_margin_type_isolated(self, *, symbol: str) -> None: ...

    async def place_entry_limit(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        client_order_id: str,
    ) -> ExchangeOrder: ...

    async def place_entry_market(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        client_order_id: str,
    ) -> ExchangeOrder: ...

    async def place_stop_market(
        self,
        *,
        symbol: str,
        side: str,
        stop_price: Decimal,
        client_order_id: str,
        close_position: bool,
    ) -> ExchangeOrder: ...

    async def place_take_profit_market(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        stop_price: Decimal,
        client_order_id: str,
        reduce_only: bool,
    ) -> ExchangeOrder: ...

    async def cancel_order(self, *, symbol: str, client_order_id: str) -> None: ...

    async def cancel_open_orders(self, *, symbol: str) -> None: ...

    async def cancel_algo_order(self, *, client_order_id: str) -> None: ...

    async def cancel_all_algo_orders(self, *, symbol: str) -> None: ...

    async def close_position_market(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal | None,
        client_order_id: str,
    ) -> ExchangeOrder: ...

    async def get_position(self, *, symbol: str) -> PositionSnapshot | None: ...

    async def get_open_orders(
        self, *, symbol: str | None = None
    ) -> list[ExchangeOrder]: ...

    async def get_open_algo_orders(
        self, *, symbol: str | None = None
    ) -> list[ExchangeOrder]: ...

    async def user_stream(self) -> AsyncIterator[UserStreamEvent]: ...

    async def mark_price_stream(self, symbols: list[str]) -> AsyncIterator[MarkPrice]: ...
