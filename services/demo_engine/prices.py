from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import Any

import websockets


BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/stream?streams="


@dataclass(frozen=True)
class MarkPrice:
    symbol: str
    price: Decimal


def normalize_stream_symbol(symbol: str) -> str:
    """Return lowercase stream symbol, e.g. ETHUSDT -> ethusdt."""
    normalized = symbol.strip().lower()
    if not normalized:
        raise ValueError("symbol must not be empty")
    return normalized


def build_mark_price_stream_url(symbols: Iterable[str]) -> str:
    """Build Binance USDⓈ-M Futures combined mark-price stream URL."""
    normalized = sorted({normalize_stream_symbol(symbol) for symbol in symbols})
    if not normalized:
        raise ValueError("at least one symbol is required")
    streams = "/".join(f"{symbol}@markPrice@1s" for symbol in normalized)
    return f"{BINANCE_FUTURES_WS_URL}{streams}"


def parse_mark_price_message(raw: str) -> MarkPrice | None:
    """Parse Binance mark-price websocket JSON into MarkPrice."""
    try:
        decoded: Any = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    data = decoded.get("data", decoded)
    if not isinstance(data, dict):
        return None
    if data.get("e") not in {None, "markPriceUpdate"}:
        return None
    symbol = data.get("s")
    price = data.get("p")
    if not isinstance(symbol, str) or not symbol or not isinstance(price, str):
        return None
    try:
        decimal_price = Decimal(price)
    except (InvalidOperation, ValueError):
        return None
    if not decimal_price.is_finite() or decimal_price <= 0:
        return None
    return MarkPrice(symbol=symbol.upper(), price=decimal_price)


async def stream_mark_prices(symbols: Iterable[str]) -> AsyncIterator[MarkPrice]:
    """Yield public Binance mark prices from websocket.

    Use `<symbol>@markPrice@1s`.
    Do not require API keys.
    """
    url = build_mark_price_stream_url(symbols)
    async with websockets.connect(url) as websocket:
        async for raw in websocket:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            mark_price = parse_mark_price_message(raw)
            if mark_price is not None:
                yield mark_price
