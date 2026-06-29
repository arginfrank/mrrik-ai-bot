from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from services.core_engine import main as core_main
from shared.bus import event_to_json, make_event


class FakeRedisClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.pending: dict[str, dict[str, str]] = {}
        self.acked: list[tuple[str, str, str]] = []

    async def xgroup_create(self, **_kwargs: Any) -> bool:
        return True

    async def xreadgroup(self, **kwargs: Any) -> Any:
        stream_id = kwargs["streams"][core_main.SIGNALS_STREAM]
        if stream_id == "0":
            if not self.pending:
                return []
            return [(core_main.SIGNALS_STREAM, list(self.pending.items()))]
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


def test_transient_signal_miss_is_reprocessed_then_acked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_event = event_to_json(make_event("signal.created", {"signal_id": 11}))
    redis_client = FakeRedisClient(
        [
            [("signals", [("1-0", {"event": raw_event})])],
            asyncio.CancelledError(),
        ]
    )
    calls = 0

    async def retry_then_process(**_kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(
                status="retry",
                opened_count=0,
                skipped_count=0,
                error_count=0,
                ignored_reason="signal_not_found",
            )
        return SimpleNamespace(
            status="processed",
            opened_count=1,
            skipped_count=0,
            error_count=0,
            ignored_reason=None,
        )

    monkeypatch.setattr(core_main, "session_scope", fake_session_scope)
    monkeypatch.setattr(core_main, "handle_signal_created", retry_then_process)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            core_main._consume_signals(
                redis_client=redis_client,
                session_factory=object(),
                publisher=object(),  # type: ignore[arg-type]
                config=object(),
                exchange_factory=object(),
                fernet_key="unused",
            )
        )

    assert calls == 2
    assert redis_client.acked == [
        (
            core_main.SIGNALS_STREAM,
            core_main.SIGNALS_GROUP_NAME,
            "1-0",
        )
    ]


def test_persistent_signal_miss_is_warned_and_left_unacked(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_event = event_to_json(make_event("signal.created", {"signal_id": 12}))
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
            skipped_count=0,
            error_count=0,
            ignored_reason="signal_not_found",
        )

    monkeypatch.setattr(core_main, "session_scope", fake_session_scope)
    monkeypatch.setattr(core_main, "handle_signal_created", always_retry)

    with caplog.at_level(logging.WARNING, logger=core_main.__name__):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                core_main._consume_signals(
                    redis_client=redis_client,
                    session_factory=object(),
                    publisher=object(),  # type: ignore[arg-type]
                    config=object(),
                    exchange_factory=object(),
                    fernet_key="unused",
                )
            )

    assert calls == 2
    assert redis_client.acked == []
    assert "reason=signal_not_found acknowledged=false" in caplog.text
