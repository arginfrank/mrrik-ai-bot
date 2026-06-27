from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from secrets import randbelow
from typing import TypeVar


T = TypeVar("T")
_JITTER_SCALE = 10**18


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_delay_sec: Decimal
    max_delay_sec: Decimal
    jitter_pct: Decimal = Decimal("0")


class RetryExhaustedError(RuntimeError):
    """Raised when retry attempts are exhausted."""


def compute_backoff_delay(
    *,
    attempt_index: int,
    policy: RetryPolicy,
    jitter_seed: int | None = None,
) -> Decimal:
    """Compute capped exponential backoff delay."""
    _validate_policy(policy)
    if attempt_index < 0:
        raise ValueError("attempt_index must be non-negative")

    delay = min(
        policy.base_delay_sec * (Decimal(2) ** attempt_index),
        policy.max_delay_sec,
    )
    if delay == 0 or policy.jitter_pct == 0:
        return delay

    jitter_unit = _jitter_unit(attempt_index=attempt_index, seed=jitter_seed)
    jittered = delay * (Decimal(1) + policy.jitter_pct * jitter_unit)
    return min(max(jittered, Decimal(0)), policy.max_delay_sec)


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    retry_on: tuple[type[BaseException], ...],
    operation_name: str,
) -> T:
    """Retry an async operation with capped backoff."""
    _validate_policy(policy)
    if not retry_on:
        raise ValueError("retry_on must contain at least one exception type")

    for attempt_index in range(policy.max_attempts):
        try:
            return await operation()
        except retry_on as error:
            if attempt_index + 1 >= policy.max_attempts:
                raise RetryExhaustedError(
                    f"{operation_name} failed after {policy.max_attempts} attempts"
                ) from error
            delay = compute_backoff_delay(
                attempt_index=attempt_index,
                policy=policy,
            )
            await asyncio.sleep(float(delay))

    raise AssertionError("retry loop terminated unexpectedly")


def _validate_policy(policy: RetryPolicy) -> None:
    if policy.max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if policy.base_delay_sec < 0 or policy.max_delay_sec < 0:
        raise ValueError("retry delays must be non-negative")
    if policy.base_delay_sec > policy.max_delay_sec:
        raise ValueError("base_delay_sec must not exceed max_delay_sec")
    if policy.jitter_pct < 0 or policy.jitter_pct > 1:
        raise ValueError("jitter_pct must be between 0 and 1")


def _jitter_unit(*, attempt_index: int, seed: int | None) -> Decimal:
    if seed is None:
        raw = randbelow((_JITTER_SCALE * 2) + 1) - _JITTER_SCALE
    else:
        digest = sha256(f"{seed}:{attempt_index}".encode()).digest()
        raw = int.from_bytes(digest[:8], "big") % ((_JITTER_SCALE * 2) + 1)
        raw -= _JITTER_SCALE
    return Decimal(raw) / Decimal(_JITTER_SCALE)
