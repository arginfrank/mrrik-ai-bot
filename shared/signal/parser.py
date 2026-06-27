from __future__ import annotations

import re
from collections.abc import Collection
from decimal import Decimal, InvalidOperation

from shared.signal.types import (
    MessageKind,
    ParsedEntrySignal,
    ParsedMessage,
    ParsedResultMessage,
    ParsedStopMessage,
    SignalSide,
)


class SignalParseError(ValueError):
    """Raised when a message looks like a signal but is invalid."""


_DECIMAL_PATTERN = (
    r"[+-]?(?:\d(?:[ \t]*\d)*[ \t]*(?:\.[ \t]*\d(?:[ \t]*\d)*)?"
    r"|\.[ \t]*\d(?:[ \t]*\d)*)"
)
_SYMBOL_RE = re.compile(r"#\s*([A-Z0-9]+(?:\s*/\s*[A-Z0-9]+)?)", re.IGNORECASE)
_ENTRY_RE = re.compile(rf"\bEntry\s*:\s*({_DECIMAL_PATTERN})", re.IGNORECASE)
_STOP_LOSS_RE = re.compile(rf"\bStop\s*Loss\s*:\s*({_DECIMAL_PATTERN})", re.IGNORECASE)
_TARGET_RE = re.compile(
    rf"\bTarget\s*(\d+)\s*:\s*({_DECIMAL_PATTERN})",
    re.IGNORECASE,
)
_LEVERAGE_RE = re.compile(r"\bLeverage\s*:\s*[x×]?\s*(\d+)", re.IGNORECASE)
_SIDE_WORD_RE = re.compile(r"\b(Long|Short)\b", re.IGNORECASE)
_RESULT_TARGET_RE = re.compile(r"\bTarget\s+(?:Tuch|Touch)\s*(\d+)", re.IGNORECASE)
_PROFIT_RE = re.compile(rf"\bProfit\s*:\s*({_DECIMAL_PATTERN})\s*%", re.IGNORECASE)
_PERIOD_RE = re.compile(r"\bPeriod\s*:\s*(\d+)\s*Minutes?\b", re.IGNORECASE)
_CHANNEL_LOSS_RE = re.compile(
    rf"(?<!Stop )\bLoss\s*:\s*({_DECIMAL_PATTERN})\s*%",
    re.IGNORECASE,
)
_RESULT_HINT_RE = re.compile(r"\b(?:Profit\s*:|Target\s+(?:Tuch|Touch)\b)", re.IGNORECASE)
_STOP_HINT_RE = re.compile(r"\bStop\s+Target\s+Hit\b", re.IGNORECASE)
_ENTRY_HINT_RE = re.compile(
    r"\b(?:Entry\s*:|Stop\s*Loss\s*:|Target\s*\d+\s*:|Leverage\s*:)",
    re.IGNORECASE,
)


def normalize_symbol(raw_symbol: str) -> str:
    """Convert '#HBAR/USDT' or 'HBAR/USDT' to 'HBARUSDT'."""

    normalized = re.sub(r"[\s/#]", "", raw_symbol).upper()
    if not normalized or not normalized.isalnum():
        raise SignalParseError("invalid symbol")
    return normalized


def parse_decimal_token(raw: str) -> Decimal:
    """Parse decimal tokens, tolerating internal spaces such as '0 .07145'."""

    collapsed = re.sub(r"\s+", "", raw)
    try:
        value = Decimal(collapsed)
    except InvalidOperation as error:
        raise SignalParseError(f"invalid decimal token: {raw!r}") from error
    if not value.is_finite():
        raise SignalParseError(f"invalid decimal token: {raw!r}")
    return value


def parse_message(
    text: str,
    *,
    valid_symbols: Collection[str] | None = None,
) -> ParsedMessage | None:
    """Parse an entry, result, or stop message. Return None if unrelated."""

    if not text or not text.strip():
        return None
    if _RESULT_HINT_RE.search(text):
        return parse_result_message(text)
    if _STOP_HINT_RE.search(text) or (
        _CHANNEL_LOSS_RE.search(text) and not _STOP_LOSS_RE.search(text)
    ):
        return parse_stop_message(text)
    if _ENTRY_HINT_RE.search(text) or (_SYMBOL_RE.search(text) and _SIDE_WORD_RE.search(text)):
        return parse_entry_signal(text, valid_symbols=valid_symbols)
    return None


def parse_entry_signal(
    text: str,
    *,
    valid_symbols: Collection[str] | None = None,
) -> ParsedEntrySignal:
    """Parse a multiline or single-line entry signal."""

    symbol = _parse_symbol(text)
    if valid_symbols is not None and symbol not in valid_symbols:
        raise SignalParseError(f"unknown symbol: {symbol}")

    side = _parse_side(text)
    entry = _required_decimal(_ENTRY_RE, text, "entry")
    stop_loss = _required_decimal(_STOP_LOSS_RE, text, "stop loss")

    leverage_match = _LEVERAGE_RE.search(text)
    if leverage_match is None:
        raise SignalParseError("missing leverage")
    leverage = int(leverage_match.group(1))
    if leverage <= 0:
        raise SignalParseError("leverage must be positive")

    target_matches = list(_TARGET_RE.finditer(text))
    if not target_matches:
        raise SignalParseError("missing targets")
    numbered_targets = [
        (int(match.group(1)), match.start(), parse_decimal_token(match.group(2)))
        for match in target_matches
    ]
    target_numbers = [number for number, _, _ in numbered_targets]
    if len(target_numbers) != len(set(target_numbers)):
        raise SignalParseError("duplicate target number")
    numbered_targets.sort(key=lambda item: (item[0], item[1]))

    return ParsedEntrySignal(
        kind=MessageKind.ENTRY,
        symbol=symbol,
        side=side,
        entry=entry,
        stop_loss=stop_loss,
        leverage=leverage,
        targets=tuple(value for _, _, value in numbered_targets),
        raw_text=text,
    )


def parse_result_message(text: str) -> ParsedResultMessage:
    """Parse a result message used only for logging/analytics."""

    symbol = _parse_symbol(text)
    profit_pct = _required_decimal(_PROFIT_RE, text, "profit percentage")
    target_match = _RESULT_TARGET_RE.search(text)
    period_match = _PERIOD_RE.search(text)

    return ParsedResultMessage(
        kind=MessageKind.RESULT,
        symbol=symbol,
        target_index=int(target_match.group(1)) if target_match else None,
        profit_pct=profit_pct,
        period_minutes=int(period_match.group(1)) if period_match else None,
        raw_text=text,
    )


def parse_stop_message(text: str) -> ParsedStopMessage:
    """Parse a stop message used only for logging/analytics."""

    return ParsedStopMessage(
        kind=MessageKind.STOP,
        symbol=_parse_symbol(text),
        loss_pct=_required_decimal(_CHANNEL_LOSS_RE, text, "loss percentage"),
        raw_text=text,
    )


def _parse_symbol(text: str) -> str:
    match = _SYMBOL_RE.search(text)
    if match is None:
        raise SignalParseError("missing symbol")
    return normalize_symbol(match.group(1))


def _parse_side(text: str) -> SignalSide:
    word_sides = {
        SignalSide.LONG if match.group(1).lower() == "long" else SignalSide.SHORT
        for match in _SIDE_WORD_RE.finditer(text)
    }
    emoji_sides: set[SignalSide] = set()
    if "🟢" in text:
        emoji_sides.add(SignalSide.LONG)
    if "🔴" in text:
        emoji_sides.add(SignalSide.SHORT)

    if len(word_sides) > 1 or len(emoji_sides) > 1:
        raise SignalParseError("conflicting side indicators")
    if word_sides and emoji_sides and word_sides != emoji_sides:
        raise SignalParseError("word and emoji side indicators disagree")
    combined = word_sides | emoji_sides
    if not combined:
        raise SignalParseError("missing side")
    return next(iter(combined))


def _required_decimal(pattern: re.Pattern[str], text: str, field_name: str) -> Decimal:
    match = pattern.search(text)
    if match is None:
        raise SignalParseError(f"missing {field_name}")
    return parse_decimal_token(match.group(1))
