from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from shared.models import Signal
from shared.signal.types import RejectedSignal, SanitizedSignal


def decimal_to_string(value: object) -> str:
    """Return stable string representation for Decimal-like values."""
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"value is not Decimal-like: {value!r}") from error

    rendered = format(decimal_value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def build_signal_created_payload(signal: Signal) -> dict[str, Any]:
    """Build the canonical normalized signal.created payload from a DB Signal row."""
    sanitizer = signal.sanitizer_notes or {
        "dropped": [],
        "corrected": [],
        "alert": False,
    }
    return {
        "signal_id": signal.id,
        "symbol": signal.symbol,
        "side": signal.side,
        "entry": decimal_to_string(signal.entry),
        "stop_loss": decimal_to_string(signal.stop_loss),
        "leverage": signal.leverage,
        "targets": [decimal_to_string(target) for target in signal.targets_clean],
        "sanitizer": _json_safe(sanitizer),
    }


def build_signal_rejected_payload(
    *,
    source_msg_id: int | None,
    reason: str,
    signal_id: int | None = None,
    symbol: str | None = None,
    raw_excerpt: str | None = None,
) -> dict[str, Any]:
    """Build a signal.rejected payload."""
    payload: dict[str, Any] = {
        "source_msg_id": source_msg_id,
        "reason": reason,
    }
    if signal_id is not None:
        payload["signal_id"] = signal_id
    if symbol is not None:
        payload["symbol"] = symbol
    if raw_excerpt is not None:
        payload["raw_excerpt"] = raw_excerpt
    return payload


def sanitizer_notes_from_result(
    result: SanitizedSignal | RejectedSignal,
) -> dict[str, Any]:
    """Build JSON-safe sanitizer notes."""
    if isinstance(result, RejectedSignal):
        return {
            "dropped": [],
            "corrected": [],
            "alert": result.alert,
        }

    return {
        "dropped": [decimal_to_string(value) for value in result.dropped],
        "corrected": [
            {
                "original": decimal_to_string(correction.original),
                "corrected": decimal_to_string(correction.corrected),
                "reason": correction.reason,
            }
            for correction in result.corrected
        ],
        "alert": result.alert,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_to_string(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
