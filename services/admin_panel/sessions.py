from __future__ import annotations

import hashlib
import hmac
import inspect
import secrets
from typing import Any


SESSION_COOKIE_NAME = "mrrik_admin_session"
SESSION_KEY_PREFIX = "admin_session:"


class AdminSessionStore:
    """Create and resolve signed, opaque admin sessions stored in Redis."""

    def __init__(
        self,
        redis_client: Any,
        *,
        signing_secret: str,
        ttl_sec: int = 43_200,
        cookie_name: str = SESSION_COOKIE_NAME,
    ) -> None:
        if ttl_sec <= 0:
            raise ValueError("session TTL must be positive")
        self._redis = redis_client
        self._signing_key = signing_secret.encode("utf-8")
        self.ttl_sec = ttl_sec
        self.cookie_name = cookie_name

    async def create(self, telegram_id: int) -> str:
        if not self._signing_key:
            raise RuntimeError("admin session signing secret is not configured")
        session_id = secrets.token_urlsafe(32)
        result = self._redis.set(
            self._key(session_id),
            str(telegram_id),
            ex=self.ttl_sec,
        )
        await _await_if_needed(result)
        return f"{session_id}.{self._signature(session_id)}"

    async def resolve(self, cookie_value: str | None) -> int | None:
        session_id = self._verified_session_id(cookie_value)
        if session_id is None:
            return None
        raw_telegram_id = await _await_if_needed(self._redis.get(self._key(session_id)))
        if raw_telegram_id is None:
            return None
        if isinstance(raw_telegram_id, bytes):
            raw_telegram_id = raw_telegram_id.decode("utf-8")
        try:
            return int(raw_telegram_id)
        except (TypeError, ValueError):
            return None

    async def delete(self, cookie_value: str | None) -> None:
        session_id = self._verified_session_id(cookie_value)
        if session_id is None:
            return
        result = self._redis.delete(self._key(session_id))
        await _await_if_needed(result)

    def _verified_session_id(self, cookie_value: str | None) -> str | None:
        if not cookie_value or not self._signing_key:
            return None
        try:
            session_id, signature = cookie_value.rsplit(".", 1)
        except ValueError:
            return None
        if not session_id or not hmac.compare_digest(
            signature,
            self._signature(session_id),
        ):
            return None
        return session_id

    def _signature(self, session_id: str) -> str:
        return hmac.new(
            self._signing_key,
            session_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _key(session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}{session_id}"


async def _await_if_needed(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value
