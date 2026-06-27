from __future__ import annotations

import pytest

from services.admin_panel.auth import (
    AdminAuthError,
    authenticate_admin_request,
    parse_admin_telegram_ids,
)


def test_parse_admin_telegram_ids() -> None:
    assert parse_admin_telegram_ids(None) == ()
    assert parse_admin_telegram_ids(" 123,456, 123 ") == (123, 456)

    with pytest.raises(ValueError, match="integers"):
        parse_admin_telegram_ids("123,invalid")


def test_rejects_missing_admin_header() -> None:
    with pytest.raises(AdminAuthError, match="required"):
        _authenticate(headers={})


def test_rejects_non_admin_id() -> None:
    with pytest.raises(AdminAuthError, match="not authorized"):
        _authenticate(headers={"X-Admin-Telegram-Id": "999"})


def test_rejects_disallowed_ip() -> None:
    with pytest.raises(AdminAuthError, match="IP"):
        _authenticate(
            headers={"X-Admin-Telegram-Id": "123"},
            scope={"client": ("10.0.0.2", 12345)},
        )


def test_accepts_valid_admin_and_allowed_ip() -> None:
    identity = _authenticate(headers={"x-admin-telegram-id": "123"})

    assert identity.telegram_id == 123
    assert identity.client_ip == "127.0.0.1"


def _authenticate(
    *,
    headers: dict[str, str],
    scope: dict[str, object] | None = None,
):
    return authenticate_admin_request(
        headers=headers,
        scope=scope or {"client": ("127.0.0.1", 12345)},
        admin_telegram_ids=(123, 456),
        ip_allowlist=["127.0.0.1", "::1"],
    )
