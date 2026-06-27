from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from uuid import uuid4

import pytest
import redis.asyncio as redis

from shared.bus import (
    RedisStreamPublisher,
    event_from_json,
    event_to_json,
    event_to_stream_fields,
    make_event,
)


def test_make_event_uses_utc_timestamp_and_preserves_payload() -> None:
    payload = {"signal_id": 123}

    event = make_event("signal.created", payload)

    timestamp = datetime.fromisoformat(event.ts_utc)
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset() == UTC.utcoffset(timestamp)
    assert event.payload == payload


def test_event_json_round_trip() -> None:
    event = make_event("signal.created", {"symbol": "HBARUSDT"})

    restored = event_from_json(event_to_json(event))

    assert restored == event


def test_decimal_payload_serializes_as_string() -> None:
    event = make_event("signal.created", {"entry": Decimal("0.07145")})

    decoded = json.loads(event_to_json(event))

    assert decoded["payload"]["entry"] == "0.07145"


def test_stream_fields_contain_exactly_one_event_field() -> None:
    event = make_event("signal.created", {"signal_id": 123})

    fields = event_to_stream_fields(event)

    assert fields == {"event": event_to_json(event)}


@pytest.mark.skipif(
    os.getenv("RUN_REDIS_TESTS") != "1",
    reason="Redis integration test requires RUN_REDIS_TESTS=1",
)
def test_redis_stream_publisher_adds_event() -> None:
    asyncio.run(_assert_redis_stream_publish())


async def _assert_redis_stream_publish() -> None:
    client = redis.from_url(os.environ["REDIS_URL"])
    stream = f"test:signals:{uuid4()}"
    try:
        before = await client.xlen(stream)
        event = await RedisStreamPublisher(client).publish(
            stream=stream,
            event_type="signal.created",
            payload={"entry": Decimal("0.07145")},
        )
        after = await client.xlen(stream)

        assert event.type == "signal.created"
        assert after == before + 1
    finally:
        await client.delete(stream)
        await client.aclose()
