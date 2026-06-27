from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SECRET_FIELD_HINTS = (
    "token",
    "secret",
    "api_key",
    "api_secret",
    "fernet",
    "password",
    "session",
    "database_url",
    "redis_url",
)

_REDACTED = "[REDACTED]"


def redact_value(value: Any) -> str:
    """Return a safe redacted representation."""
    del value
    return _REDACTED


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively redact secret-looking keys."""
    redacted: dict[str, Any] = {}
    for key, value in values.items():
        key_text = str(key)
        if _is_secret_field(key_text):
            redacted[key_text] = redact_value(value)
        else:
            redacted[key_text] = _redact_nested(value)
    return redacted


def safe_log_context(**kwargs: Any) -> dict[str, Any]:
    """Return a safe dict for structured logs."""
    return redact_mapping(kwargs)


def _is_secret_field(key: str) -> bool:
    normalized = key.casefold()
    return any(hint in normalized for hint in SECRET_FIELD_HINTS)


def _redact_nested(value: Any) -> Any:
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [_redact_nested(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_nested(item) for item in value)
    if isinstance(value, set):
        return {_redact_nested(item) for item in value}
    return value
