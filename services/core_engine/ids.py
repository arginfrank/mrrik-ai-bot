from __future__ import annotations

import re


_PURPOSES = {
    "entry",
    "sl",
    "tp",
    "be_sl",
    "model3_exit",
    "close",
    "emergency_close",
}
_BINANCE_CLIENT_ID_MAX_LENGTH = 36
_PURPOSE_TOKENS = {"emergency_close": "emergency"}


def client_order_id(
    *,
    trade_id: int,
    purpose: str,
    leg_index: int | None = None,
) -> str:
    """Return a deterministic Binance-safe client order id."""
    if trade_id <= 0:
        raise ValueError("trade_id must be positive")
    if purpose not in _PURPOSES:
        raise ValueError(f"unsupported order purpose: {purpose}")
    if purpose == "tp" and leg_index is None:
        raise ValueError("TP order ids require leg_index")
    if leg_index is not None and leg_index <= 0:
        raise ValueError("leg_index must be positive")

    purpose_token = _PURPOSE_TOKENS.get(purpose, purpose)
    value = f"m7-{trade_id}-{purpose_token}"
    if leg_index is not None:
        value = f"{value}-{leg_index}"
    if len(value) > _BINANCE_CLIENT_ID_MAX_LENGTH:
        raise ValueError("trade id is too long for a Binance client order id")
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("client order id contains an unsafe character")
    return value
