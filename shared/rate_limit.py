from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from time import monotonic


@dataclass
class TokenBucket:
    rate_per_second: Decimal
    capacity: Decimal
    tokens: Decimal
    updated_at: Decimal

    @classmethod
    def create(
        cls,
        *,
        rate_per_second: Decimal,
        capacity: Decimal,
        now: Decimal | None = None,
    ) -> "TokenBucket":
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        return cls(
            rate_per_second=rate_per_second,
            capacity=capacity,
            tokens=capacity,
            updated_at=_now_decimal() if now is None else now,
        )

    def refill(self, *, now: Decimal | None = None) -> None:
        current = _now_decimal() if now is None else now
        if current <= self.updated_at:
            return
        elapsed = current - self.updated_at
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_second)
        self.updated_at = current

    def try_acquire(
        self,
        *,
        tokens: Decimal = Decimal("1"),
        now: Decimal | None = None,
    ) -> bool:
        _validate_requested_tokens(tokens)
        self.refill(now=now)
        if tokens > self.tokens:
            return False
        self.tokens -= tokens
        return True

    def seconds_until_available(
        self,
        *,
        tokens: Decimal = Decimal("1"),
        now: Decimal | None = None,
    ) -> Decimal:
        _validate_requested_tokens(tokens)
        self.refill(now=now)
        if tokens <= self.tokens:
            return Decimal(0)
        return (tokens - self.tokens) / self.rate_per_second


def _now_decimal() -> Decimal:
    return Decimal(str(monotonic()))


def _validate_requested_tokens(tokens: Decimal) -> None:
    if tokens <= 0:
        raise ValueError("tokens must be positive")
