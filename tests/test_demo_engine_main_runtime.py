from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from redis.exceptions import TimeoutError as RedisTimeoutError

from services.demo_engine import main as demo_main
from shared.bus import event_to_json, make_event


class FakeRedisClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.stream_ids: list[dict[str, str]] = []

    async def xread(
        self,
        streams: dict[str, str],
        *,
        count: int,
        block: int,
    ) -> Any:
        self.stream_ids.append(streams)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@contextmanager
def fake_session_scope(_session_factory: Any) -> Iterator[object]:
    yield object()


def test_redis_timeout_continues_and_later_signal_is_handled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_event = event_to_json(
        make_event("signal.created", {"signal_id": 7})
    )
    redis_client = FakeRedisClient(
        [
            RedisTimeoutError("read timed out"),
            [("signals", [("1-0", {"event": raw_event})])],
            asyncio.CancelledError(),
        ]
    )
    handled_payloads: list[dict[str, Any]] = []

    async def fake_handle_signal_created(**kwargs: Any) -> SimpleNamespace:
        handled_payloads.append(kwargs["payload"])
        return SimpleNamespace(opened_count=1)

    monkeypatch.setattr(demo_main, "session_scope", fake_session_scope)
    monkeypatch.setattr(
        demo_main,
        "handle_signal_created",
        fake_handle_signal_created,
    )
    monkeypatch.setattr(demo_main, "REDIS_TIMEOUT_RETRY_DELAY_SEC", 0)

    with caplog.at_level(logging.DEBUG, logger=demo_main.__name__):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                demo_main._consume_signals(
                    redis_client=redis_client,
                    session_factory=object(),
                    publisher=object(),  # type: ignore[arg-type]
                    config=object(),
                )
            )

    assert handled_payloads == [{"signal_id": 7}]
    assert redis_client.stream_ids == [
        {demo_main.SIGNALS_STREAM: "$"},
        {demo_main.SIGNALS_STREAM: "$"},
        {demo_main.SIGNALS_STREAM: "1-0"},
    ]
    assert "reason=redis_read_timeout" in caplog.text


def test_cancelled_error_from_xread_propagates() -> None:
    redis_client = FakeRedisClient([asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            demo_main._consume_signals(
                redis_client=redis_client,
                session_factory=object(),
                publisher=object(),  # type: ignore[arg-type]
                config=object(),
            )
        )


def test_unexpected_xread_error_is_logged_and_propagates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis_client = FakeRedisClient([RuntimeError("socket failed")])

    with caplog.at_level(logging.ERROR, logger=demo_main.__name__):
        with pytest.raises(RuntimeError, match="socket failed"):
            asyncio.run(
                demo_main._consume_signals(
                    redis_client=redis_client,
                    session_factory=object(),
                    publisher=object(),  # type: ignore[arg-type]
                    config=object(),
                )
            )

    assert "status=read_failed stream=signals" in caplog.text
