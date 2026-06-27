from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Any


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


def authenticate_admin_request(
    *,
    headers: dict[str, str],
    scope: dict[str, Any],
    admin_telegram_ids: tuple[int, ...],
    ip_allowlist: list[str] | tuple[str, ...],
) -> AdminIdentity:
    """Authenticate admin request using X-Admin-Telegram-Id plus IP allowlist."""
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    raw_telegram_id = normalized_headers.get("x-admin-telegram-id")
    if raw_telegram_id is None:
        raise AdminAuthError("admin identity is required")
    try:
        telegram_id = int(raw_telegram_id.strip())
    except ValueError as error:
        raise AdminAuthError("admin identity is invalid") from error
    if telegram_id not in admin_telegram_ids:
        raise AdminAuthError("admin identity is not authorized")

    client_ip = get_client_ip_from_scope(scope)
    if not is_ip_allowed(client_ip, ip_allowlist):
        raise AdminAuthError("client IP is not allowed")
    return AdminIdentity(telegram_id=telegram_id, client_ip=client_ip)
