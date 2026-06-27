from __future__ import annotations

from decimal import Decimal

from shared.rate_limit import TokenBucket


def test_token_bucket_allows_initial_capacity_then_denies() -> None:
    bucket = TokenBucket.create(
        rate_per_second=Decimal("2"), capacity=Decimal("2"), now=Decimal("0")
    )

    assert bucket.try_acquire(now=Decimal("0")) is True
    assert bucket.try_acquire(now=Decimal("0")) is True
    assert bucket.try_acquire(now=Decimal("0")) is False


def test_refill_restores_tokens_without_exceeding_capacity() -> None:
    bucket = TokenBucket.create(
        rate_per_second=Decimal("2"), capacity=Decimal("2"), now=Decimal("0")
    )
    assert bucket.try_acquire(tokens=Decimal("2"), now=Decimal("0")) is True

    bucket.refill(now=Decimal("0.5"))

    assert bucket.tokens == Decimal("1.0")
    assert bucket.try_acquire(now=Decimal("0.5")) is True


def test_seconds_until_available_is_exact_decimal() -> None:
    bucket = TokenBucket.create(
        rate_per_second=Decimal("4"), capacity=Decimal("1"), now=Decimal("0")
    )
    bucket.try_acquire(now=Decimal("0"))

    delay = bucket.seconds_until_available(now=Decimal("0.125"))

    assert isinstance(delay, Decimal)
    assert delay == Decimal("0.125")
