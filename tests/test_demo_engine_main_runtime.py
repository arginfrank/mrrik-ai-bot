from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
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
        self.group_create_calls: list[dict[str, Any]] = []
        self.read_group_calls: list[dict[str, Any]] = []
        self.acked: list[tuple[str, str, str]] = []
        self.pending: dict[str, dict[str, str]] = {}

    async def xgroup_create(self, **kwargs: Any) -> bool:
        self.group_create_calls.append(kwargs)
        return True

    async def xreadgroup(self, **kwargs: Any) -> Any:
        self.read_group_calls.append(kwargs)
        stream_id = kwargs["streams"][demo_main.SIGNALS_STREAM]
        if stream_id == "0":
            if not self.pending:
                return []
            return [(demo_main.SIGNALS_STREAM, list(self.pending.items()))]
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        for _stream, messages in response:
            self.pending.update(dict(messages))
        return response

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        self.pending.pop(message_id, None)
        return 1


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
        return SimpleNamespace(
            status="opened",
            opened_count=1,
            ignored_reason=None,
        )

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
    assert [
        call["streams"][demo_main.SIGNALS_STREAM]
        for call in redis_client.read_group_calls
    ] == ["0", ">", "0", ">", "0", ">"]
    assert redis_client.acked == [
        (
            demo_main.SIGNALS_STREAM,
            demo_main.SIGNALS_GROUP_NAME,
            "1-0",
        )
    ]
    assert "reason=redis_read_timeout" in caplog.text


def test_consume_signals_logs_ignored_status_and_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_event = event_to_json(make_event("signal.created", {"signal_id": 8}))
    redis_client = FakeRedisClient(
        [
            [("signals", [("1-0", {"event": raw_event})])],
            asyncio.CancelledError(),
        ]
    )

    async def fake_handle_signal_created(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            status="ignored",
            opened_count=0,
            ignored_reason="demo_open_plan_rejected",
        )

    monkeypatch.setattr(demo_main, "session_scope", fake_session_scope)
    monkeypatch.setattr(
        demo_main,
        "handle_signal_created",
        fake_handle_signal_created,
    )

    with caplog.at_level(logging.INFO, logger=demo_main.__name__):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                demo_main._consume_signals(
                    redis_client=redis_client,
                    session_factory=object(),
                    publisher=object(),  # type: ignore[arg-type]
                    config=object(),
                )
            )

    message = next(
        record.message
        for record in caplog.records
        if "event_type=signal.created signal_id=8" in record.message
    )
    assert "status=ignored" in message
    assert "opened_count=0" in message
    assert "ignored_reason=demo_open_plan_rejected" in message
    assert redis_client.acked == [
        (
            demo_main.SIGNALS_STREAM,
            demo_main.SIGNALS_GROUP_NAME,
            "1-0",
        )
    ]


def test_transient_signal_miss_is_reprocessed_then_acked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_event = event_to_json(make_event("signal.created", {"signal_id": 9}))
    redis_client = FakeRedisClient(
        [
            [("signals", [("1-0", {"event": raw_event})])],
            asyncio.CancelledError(),
        ]
    )
    calls = 0

    async def retry_then_open(**_kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(
                status="retry",
                opened_count=0,
                ignored_reason="signal_not_found",
            )
        return SimpleNamespace(status="opened", opened_count=1, ignored_reason=None)

    monkeypatch.setattr(demo_main, "session_scope", fake_session_scope)
    monkeypatch.setattr(demo_main, "handle_signal_created", retry_then_open)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            demo_main._consume_signals(
                redis_client=redis_client,
                session_factory=object(),
                publisher=object(),  # type: ignore[arg-type]
                config=object(),
            )
        )

    assert calls == 2
    assert redis_client.acked == [
        (
            demo_main.SIGNALS_STREAM,
            demo_main.SIGNALS_GROUP_NAME,
            "1-0",
        )
    ]


def test_persistent_signal_miss_is_warned_and_left_unacked(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_event = event_to_json(make_event("signal.created", {"signal_id": 10}))
    redis_client = FakeRedisClient(
        [[("signals", [("1-0", {"event": raw_event})])]]
    )
    calls = 0

    async def always_retry(**_kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError
        return SimpleNamespace(
            status="retry",
            opened_count=0,
            ignored_reason="signal_not_found",
        )

    monkeypatch.setattr(demo_main, "session_scope", fake_session_scope)
    monkeypatch.setattr(demo_main, "handle_signal_created", always_retry)

    with caplog.at_level(logging.WARNING, logger=demo_main.__name__):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                demo_main._consume_signals(
                    redis_client=redis_client,
                    session_factory=object(),
                    publisher=object(),  # type: ignore[arg-type]
                    config=object(),
                )
            )

    assert calls == 2
    assert redis_client.acked == []
    assert "reason=signal_not_found acknowledged=false" in caplog.text


def test_track_prices_logs_status_and_ignored_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_stream_mark_prices(_symbols: tuple[str, ...]):
        yield SimpleNamespace(symbol="ETHUSDT", price=Decimal("100"))
        raise asyncio.CancelledError

    async def fake_handle_mark_price(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            status="ignored",
            closed_count=0,
            ignored_reason="no_open_trades",
        )

    monkeypatch.setattr(demo_main, "_open_symbols", lambda _factory: ["ETHUSDT"])
    monkeypatch.setattr(demo_main, "session_scope", fake_session_scope)
    monkeypatch.setattr(demo_main, "stream_mark_prices", fake_stream_mark_prices)
    monkeypatch.setattr(demo_main, "handle_mark_price", fake_handle_mark_price)

    with caplog.at_level(logging.INFO, logger=demo_main.__name__):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                demo_main._track_prices(
                    session_factory=object(),
                    publisher=object(),  # type: ignore[arg-type]
                    config=object(),
                )
            )

    message = next(
        record.message
        for record in caplog.records
        if "event_type=mark_price symbol=ETHUSDT" in record.message
    )
    assert "status=ignored" in message
    assert "closed_count=0" in message
    assert "ignored_reason=no_open_trades" in message


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
