from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from shared.retry import (
    RetryExhaustedError,
    RetryPolicy,
    compute_backoff_delay,
    retry_async,
)


def test_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy(5, Decimal("1"), Decimal("4"))

    assert [
        compute_backoff_delay(attempt_index=index, policy=policy)
        for index in range(5)
    ] == [Decimal("1"), Decimal("2"), Decimal("4"), Decimal("4"), Decimal("4")]


def test_jitter_is_deterministic_when_seeded() -> None:
    policy = RetryPolicy(3, Decimal("1"), Decimal("10"), Decimal("0.10"))

    first = compute_backoff_delay(attempt_index=1, policy=policy, jitter_seed=99)
    second = compute_backoff_delay(attempt_index=1, policy=policy, jitter_seed=99)

    assert first == second
    assert Decimal("1.8") <= first <= Decimal("2.2")


def test_retry_succeeds_after_transient_failures() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError
        return "ok"

    result = asyncio.run(
        retry_async(
            operation,
            policy=RetryPolicy(3, Decimal("0"), Decimal("0")),
            retry_on=(TimeoutError,),
            operation_name="transient operation",
        )
    )

    assert result == "ok"
    assert attempts == 3


def test_retry_raises_when_attempts_are_exhausted() -> None:
    async def operation() -> None:
        raise ConnectionError

    with pytest.raises(RetryExhaustedError, match="safe operation"):
        asyncio.run(
            retry_async(
                operation,
                policy=RetryPolicy(2, Decimal("0"), Decimal("0")),
                retry_on=(ConnectionError,),
                operation_name="safe operation",
            )
        )
