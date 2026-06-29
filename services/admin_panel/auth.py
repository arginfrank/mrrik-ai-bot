from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import ipaddress
from typing import Any

from fastapi import Request

from services.admin_panel.sessions import AdminSessionStore


@dataclass(frozen=True)
class AdminIdentity:
    telegram_id: int
    client_ip: str


class AdminAuthError(PermissionError):
    """Raised when an admin request is not authorized."""


def parse_admin_telegram_ids(raw: str | None) -> tuple[int, ...]:
    """Parse comma-separated admin Telegram ids."""
    if raw is None or not raw.strip():
        return ()
    parsed: list[int] = []
    for value in raw.split(","):
        clean_value = value.strip()
        if not clean_value:
            continue
        try:
            telegram_id = int(clean_value)
        except ValueError as error:
            raise ValueError("admin Telegram ids must be integers") from error
        if telegram_id not in parsed:
            parsed.append(telegram_id)
    return tuple(parsed)


def is_ip_allowed(client_ip: str, allowlist: list[str] | tuple[str, ...]) -> bool:
    """Return true when client IP is allowed."""
    if client_ip in allowlist:
        return True
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for allowed in allowlist:
        try:
            if "/" in allowed:
                if address in ipaddress.ip_network(allowed, strict=False):
                    return True
            elif address == ipaddress.ip_address(allowed):
                return True
        except ValueError:
            continue
    return False


def get_client_ip_from_scope(scope: dict[str, Any]) -> str:
    """Extract client IP from ASGI request scope."""
    client = scope.get("client")
    if not isinstance(client, (tuple, list)) or not client:
        return ""
    return str(client[0])


def verify_telegram_login_payload(
    payload: Mapping[str, Any],
    *,
    bot_token: str,
    max_age_sec: int = 86_400,
    now_utc: datetime | None = None,
) -> int:
    """Verify a Telegram Login Widget payload and return its Telegram id."""
    if not bot_token:
        raise AdminAuthError("Telegram login is not configured")
    received_hash = str(payload.get("hash", "")).strip().lower()
    if len(received_hash) != 64:
        raise AdminAuthError("Telegram login payload is invalid")

    check_values = {
        str(key): str(value)
        for key, value in payload.items()
        if key != "hash" and value is not None
    }
    data_check_string = "\n".join(
        f"{key}={check_values[key]}" for key in sorted(check_values)
    )
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise AdminAuthError("Telegram login payload is invalid")

    try:
        telegram_id = int(check_values["id"])
        auth_timestamp = int(check_values["auth_date"])
    except (KeyError, TypeError, ValueError) as error:
        raise AdminAuthError("Telegram login payload is invalid") from error
    now = _utc_datetime(now_utc)
    age_sec = int(now.timestamp()) - auth_timestamp
    if age_sec < -60 or age_sec > max_age_sec:
        raise AdminAuthError("Telegram login payload has expired")
    if telegram_id <= 0:
        raise AdminAuthError("Telegram login payload is invalid")
    return telegram_id


def authorize_telegram_login(
    payload: Mapping[str, Any],
    *,
    bot_token: str,
    admin_telegram_ids: tuple[int, ...],
    max_age_sec: int = 86_400,
    now_utc: datetime | None = None,
) -> int:
    """Verify Telegram proof and enforce configured admin membership."""
    telegram_id = verify_telegram_login_payload(
        payload,
        bot_token=bot_token,
        max_age_sec=max_age_sec,
        now_utc=now_utc,
    )
    if telegram_id not in admin_telegram_ids:
        raise AdminAuthError("admin identity is not authorized")
    return telegram_id


def authorize_bootstrap_login(
    provided_token: str | None,
    *,
    bootstrap_token: str,
    admin_telegram_ids: tuple[int, ...],
    requested_telegram_id: int | None = None,
) -> int:
    """Verify the development bootstrap secret and select an admin identity."""
    if not bootstrap_token or not provided_token:
        raise AdminAuthError("bootstrap login is not configured or token is missing")
    if not hmac.compare_digest(provided_token, bootstrap_token):
        raise AdminAuthError("bootstrap token is invalid")
    if not admin_telegram_ids:
        raise AdminAuthError("no admin identities are configured")
    telegram_id = requested_telegram_id or admin_telegram_ids[0]
    if telegram_id not in admin_telegram_ids:
        raise AdminAuthError("admin identity is not authorized")
    return telegram_id


async def authenticate_admin_session(
    request: Request,
    *,
    session_store: AdminSessionStore,
    admin_telegram_ids: tuple[int, ...],
    ip_allowlist: tuple[str, ...] = (),
    require_ip_allowlist: bool = False,
) -> AdminIdentity:
    """Authenticate a Redis-backed session, then optionally enforce source IP."""
    session_cookie = request.cookies.get(session_store.cookie_name)
    telegram_id = await session_store.resolve(session_cookie)
    if telegram_id is None:
        raise AdminAuthError("admin session is missing or expired")
    if telegram_id not in admin_telegram_ids:
        raise AdminAuthError("admin identity is not authorized")

    client_ip = get_client_ip_from_scope(request.scope)
    if require_ip_allowlist and not is_ip_allowed(client_ip, ip_allowlist):
        raise AdminAuthError("client IP is not allowed")
    return AdminIdentity(telegram_id=telegram_id, client_ip=client_ip)


def authenticate_admin_request(**_: Any) -> AdminIdentity:
    """Reject the retired header-based authentication mechanism."""
    raise AdminAuthError("a valid admin session is required")


def _utc_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
