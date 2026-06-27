from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal


Side = Literal["LONG", "SHORT"]
OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"]
OrderStatus = Literal[
    "NEW",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "EXPIRED",
    "REJECTED",
]
MarginType = Literal["ISOLATED"]
PositionSide = Literal["BOTH"]


@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    step_size: Decimal
    tick_size: Decimal
    min_qty: Decimal
    min_notional: Decimal


@dataclass(frozen=True)
class BalanceSnapshot:
    asset: str
    free: Decimal
    total: Decimal


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: Decimal
    entry_price: Decimal | None
    mark_price: Decimal | None
    liquidation_price: Decimal | None
    unrealized_pnl: Decimal | None


@dataclass(frozen=True)
class ExchangeOrder:
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    price: Decimal | None = None
    stop_price: Decimal | None = None
    qty: Decimal | None = None
    reduce_only: bool = False
    close_position: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UserStreamEvent:
    event_type: str
    symbol: str | None = None
    client_order_id: str | None = None
    exchange_order_id: str | None = None
    order_status: str | None = None
    execution_type: str | None = None
    last_filled_qty: Decimal | None = None
    cumulative_filled_qty: Decimal | None = None
    last_filled_price: Decimal | None = None
    realized_pnl: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarkPrice:
    symbol: str
    price: Decimal
