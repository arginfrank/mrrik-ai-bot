from __future__ import annotations

from collections.abc import Iterator
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import Engine

from services.signal_ingestor.repository import (
    SignalRepository,
    make_engine_from_config,
    make_session_factory,
    session_scope,
)
from shared.models import Signal
from shared.signal.parser import parse_message
from shared.signal.sanitizer import sanitize_signal
from shared.signal.types import ParsedEntrySignal, RejectedSignal, SanitizedSignal


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="PostgreSQL repository tests require RUN_DB_TESTS=1",
)

HBAR_SIGNAL = """#HBAR/USDT - Long
Entry: 0.07145
Stop Loss: 0.07077
Target 1: 0.072
Target 2: 0.07186
Target 3: 0.07238
Target 4: 0.07296
Target 5: 0.07309
Leverage: x42
"""


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    database_engine = make_engine_from_config()
    try:
        yield database_engine
    finally:
        database_engine.dispose()


@pytest.fixture
def source_msg_id(engine: Engine) -> Iterator[int]:
    value = uuid4().int % 9_000_000_000_000_000
    try:
        yield value
    finally:
        session_factory = make_session_factory(engine)
        with session_scope(session_factory) as session:
            session.execute(delete(Signal).where(Signal.source_msg_id == value))


def test_create_accepted_hbar_signal(engine: Engine, source_msg_id: int) -> None:
    parsed = parse_message(HBAR_SIGNAL)
    assert isinstance(parsed, ParsedEntrySignal)
    sanitized = sanitize_signal(parsed)
    assert isinstance(sanitized, SanitizedSignal)
    session_factory = make_session_factory(engine)

    with session_scope(session_factory) as session:
        row = SignalRepository(session).create_accepted_signal(
            parsed=parsed,
            sanitized=sanitized,
            source_msg_id=source_msg_id,
        )
        assert row.id is not None
        row_id = row.id

    with session_factory() as session:
        persisted = session.get(Signal, row_id)
        assert persisted is not None
        assert persisted.status == "accepted"
        assert persisted.reject_reason is None
        assert persisted.targets_raw == [
            "0.072",
            "0.07186",
            "0.07238",
            "0.07296",
            "0.07309",
        ]
        assert persisted.targets_clean == ["0.07186", "0.07238", "0.07296", "0.07309"]
        assert persisted.sanitizer_notes["alert"] is True


def test_create_sanitizer_rejected_signal(engine: Engine, source_msg_id: int) -> None:
    parsed = parse_message(HBAR_SIGNAL.replace("Stop Loss: 0.07077", "Stop Loss: 0.08"))
    assert isinstance(parsed, ParsedEntrySignal)
    rejected = sanitize_signal(parsed)
    assert isinstance(rejected, RejectedSignal)
    session_factory = make_session_factory(engine)

    with session_scope(session_factory) as session:
        row = SignalRepository(session).create_rejected_signal(
            parsed=parsed,
            rejection=rejected,
            source_msg_id=source_msg_id,
        )
        assert row.id is not None
        row_id = row.id

    with session_factory() as session:
        persisted = session.get(Signal, row_id)
        assert persisted is not None
        assert persisted.status == "rejected"
        assert persisted.reject_reason == "wrong_side_stop_loss"
        assert persisted.targets_clean == []
