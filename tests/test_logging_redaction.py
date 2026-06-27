from __future__ import annotations

import json

from shared.logging import redact_mapping, redact_value, safe_log_context


def test_secret_looking_keys_are_redacted() -> None:
    result = safe_log_context(api_key="raw-key", bot_token="raw-token", status="ok")

    assert result["api_key"] == "[REDACTED]"
    assert result["bot_token"] == "[REDACTED]"
    assert result["status"] == "ok"


def test_nested_secret_values_are_redacted() -> None:
    result = redact_mapping(
        {
            "request": {
                "items": [{"api_secret": "nested-secret", "attempt": 2}],
                "database_url": "postgresql://user:password@db/mrrik",
            }
        }
    )

    encoded = json.dumps(result)
    assert "nested-secret" not in encoded
    assert "user:password" not in encoded
    assert result["request"]["items"][0]["attempt"] == 2


def test_redact_value_never_preserves_raw_value() -> None:
    assert "very-private" not in redact_value("very-private")
