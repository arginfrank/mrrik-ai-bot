from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    type: str
    ts_utc: str
    payload: dict[str, Any]


def make_event(event_type: str, payload: dict[str, Any]) -> EventEnvelope:
    """Create a canonical event envelope for future Redis Streams publishing."""
    return EventEnvelope(
        event_id=str(uuid4()),
        type=event_type,
        ts_utc=datetime.now(UTC).isoformat(),
        payload=payload,
    )
