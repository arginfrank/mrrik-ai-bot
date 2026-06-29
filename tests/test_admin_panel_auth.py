from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from typing import Any

from fastapi import Request
import pytest

from services.admin_panel.auth import (
    AdminAuthError,
    authenticate_admin_request,
    authenticate_admin_session,
    authorize_bootstrap_login,
    authorize_telegram_login,
    is_ip_allowed,
    parse_admin_telegram_ids,
    verify_telegram_login_payload,
)
from services.admin_panel.sessions import AdminSessionStore


BOT_TOKEN = "123456:telegram-bot-secret"
NOW = datetime(2026, 6, 29, 12, tzinfo=UTC)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        assert ex == 43_200
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


def test_parse_admin_telegram_ids_and_ip_allowlist() -> None:
    assert parse_admin_telegram_ids(None) == ()
    assert parse_admin_telegram_ids(" 123,456, 123 ") == (123, 456)
    assert is_ip_allowed("10.2.3.4", ("10.0.0.0/8",)) is True
    assert is_ip_allowed("192.168.1.1", ("10.0.0.0/8",)) is False

    with pytest.raises(ValueError, match="integers"):
        parse_admin_telegram_ids("123,invalid")


def test_telegram_payload_accepts_correct_hmac() -> None:
    payload = _signed_payload(telegram_id=123, auth_date=NOW)

    assert verify_telegram_login_payload(
        payload,
        bot_token=BOT_TOKEN,
        now_utc=NOW,
    ) == 123


def test_telegram_payload_rejects_tampering_and_expiry() -> None:
    tampered = _signed_payload(telegram_id=123, auth_date=NOW)
    tampered["username"] = "tampered"
    with pytest.raises(AdminAuthError, match="invalid"):
        verify_telegram_login_payload(tampered, bot_token=BOT_TOKEN, now_utc=NOW)

    expired = _signed_payload(
        telegram_id=123,
        auth_date=NOW - timedelta(seconds=86_401),
    )
    with pytest.raises(AdminAuthError, match="expired"):
        verify_telegram_login_payload(expired, bot_token=BOT_TOKEN, now_utc=NOW)


def test_telegram_login_rejects_non_admin() -> None:
    with pytest.raises(AdminAuthError, match="not authorized"):
        authorize_telegram_login(
            _signed_payload(telegram_id=999, auth_date=NOW),
            bot_token=BOT_TOKEN,
            admin_telegram_ids=(123,),
            now_utc=NOW,
        )


@pytest.mark.parametrize("provided", [None, "wrong-token"])
def test_bootstrap_login_rejects_missing_or_wrong_token(provided: str | None) -> None:
    with pytest.raises(AdminAuthError):
        authorize_bootstrap_login(
            provided,
            bootstrap_token="correct-token",
            admin_telegram_ids=(123,),
        )


def test_session_cookie_authorizes_and_missing_or_expired_does_not() -> None:
    redis = FakeRedis()
    store = AdminSessionStore(redis, signing_secret="session-signing-secret")
    cookie = asyncio.run(store.create(123))

    identity = asyncio.run(
        authenticate_admin_session(
            _request(cookie),
            session_store=store,
            admin_telegram_ids=(123,),
            require_ip_allowlist=False,
        )
    )
    assert identity.telegram_id == 123

    with pytest.raises(AdminAuthError, match="missing or expired"):
        asyncio.run(
            authenticate_admin_session(
                _request(None),
                session_store=store,
                admin_telegram_ids=(123,),
            )
        )

    redis.values.clear()
    with pytest.raises(AdminAuthError, match="missing or expired"):
        asyncio.run(
            authenticate_admin_session(
                _request(cookie),
                session_store=store,
                admin_telegram_ids=(123,),
            )
        )


def test_session_auth_enforces_ip_as_second_layer() -> None:
    redis = FakeRedis()
    store = AdminSessionStore(redis, signing_secret="session-signing-secret")
    cookie = asyncio.run(store.create(123))

    with pytest.raises(AdminAuthError, match="IP"):
        asyncio.run(
            authenticate_admin_session(
                _request(cookie, client_ip="10.0.0.2"),
                session_store=store,
                admin_telegram_ids=(123,),
                ip_allowlist=("127.0.0.1",),
                require_ip_allowlist=True,
            )
        )


def test_old_admin_header_alone_never_authorizes() -> None:
    with pytest.raises(AdminAuthError, match="session"):
        authenticate_admin_request(
            headers={"X-Admin-Telegram-Id": "123"},
            scope={"client": ("127.0.0.1", 12345)},
            admin_telegram_ids=(123,),
            ip_allowlist=("127.0.0.1",),
        )


def _signed_payload(*, telegram_id: int, auth_date: datetime) -> dict[str, str]:
    payload = {
        "id": str(telegram_id),
        "first_name": "Admin",
        "username": "admin_user",
        "auth_date": str(int(auth_date.timestamp())),
    }
    data_check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    payload["hash"] = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return payload


def _request(cookie: str | None, *, client_ip: str = "127.0.0.1") -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie:
        headers.append((b"cookie", f"mrrik_admin_session={cookie}".encode()))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/overview",
        "headers": headers,
        "client": (client_ip, 12345),
        "scheme": "https",
        "server": ("testserver", 443),
    }
    return Request(scope)
