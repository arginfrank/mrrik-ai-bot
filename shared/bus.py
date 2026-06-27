from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    type: str
    ts_utc: str
    payload: dict[str, Any]


def make_event(event_type: str, payload: dict[str, Any]) -> EventEnvelope:
    """Create a canonical event envelope for Redis Streams publishing."""
    return EventEnvelope(
        event_id=str(uuid4()),
        type=event_type,
        ts_utc=datetime.now(UTC).isoformat(),
        payload=payload,
    )


def json_default(value: Any) -> str:
    """JSON serializer for safe event payloads."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("naive datetimes are not valid event values")
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def event_to_json(envelope: EventEnvelope) -> str:
    """Serialize an event envelope as JSON."""
    return json.dumps(
        asdict(envelope),
        default=json_default,
        separators=(",", ":"),
    )


def event_from_json(raw: str) -> EventEnvelope:
    """Deserialize an event envelope from JSON."""
    decoded = json.loads(raw)
    if not isinstance(decoded, Mapping):
        raise ValueError("event envelope must be a JSON object")

    payload = decoded.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("event payload must be a JSON object")

    try:
        return EventEnvelope(
            event_id=str(decoded["event_id"]),
            type=str(decoded["type"]),
            ts_utc=str(decoded["ts_utc"]),
            payload=dict(payload),
        )
    except KeyError as error:
        raise ValueError(f"missing event field: {error.args[0]}") from error


def event_to_stream_fields(envelope: EventEnvelope) -> dict[str, str]:
    """Encode an event as Redis Stream fields."""
    return {"event": event_to_json(envelope)}


class RedisStreamPublisher:
    """Small Redis Streams publisher wrapper."""

    def __init__(self, redis_client: Any) -> None:
        self._redis_client = redis_client

    async def publish(
        self,
        *,
        stream: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> EventEnvelope:
        """Create and publish an event to a Redis Stream using XADD."""
        envelope = make_event(event_type, payload)
        await self._redis_client.xadd(stream, event_to_stream_fields(envelope))
        return envelope
