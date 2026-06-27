from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.signal_ingestor.events import decimal_to_string, sanitizer_notes_from_result
from shared.config import load_config
from shared.models import Signal
from shared.signal.types import ParsedEntrySignal, RejectedSignal, SanitizedSignal


def make_engine_from_config() -> Any:
    """Create a SQLAlchemy engine from configured DATABASE_URL."""
    return create_engine(load_config().env.database_url)


def make_session_factory(engine: Any | None = None) -> sessionmaker[Session]:
    """Create a SQLAlchemy session factory."""
    return sessionmaker(
        bind=engine if engine is not None else make_engine_from_config(),
        class_=Session,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit on success, rollback on exception, close always."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class SignalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_accepted_signal(
        self,
        *,
        parsed: ParsedEntrySignal,
        sanitized: SanitizedSignal,
        source_msg_id: int | None,
    ) -> Signal:
        """Persist an accepted sanitized signal."""
        signal = Signal(
            source_msg_id=source_msg_id,
            symbol=sanitized.symbol,
            side=sanitized.side.value,
            entry=sanitized.entry,
            stop_loss=sanitized.stop_loss,
            leverage=sanitized.leverage,
            targets_raw=[decimal_to_string(target) for target in parsed.targets],
            targets_clean=[decimal_to_string(target) for target in sanitized.targets_clean],
            sanitizer_notes=sanitizer_notes_from_result(sanitized),
            status="accepted",
            reject_reason=None,
        )
        self._session.add(signal)
        self._session.flush()
        return signal

    def create_rejected_signal(
        self,
        *,
        parsed: ParsedEntrySignal,
        rejection: RejectedSignal,
        source_msg_id: int | None,
    ) -> Signal:
        """Persist a parsed entry signal rejected by sanitizer."""
        signal = Signal(
            source_msg_id=source_msg_id,
            symbol=parsed.symbol,
            side=parsed.side.value,
            entry=parsed.entry,
            stop_loss=parsed.stop_loss,
            leverage=parsed.leverage,
            targets_raw=[decimal_to_string(target) for target in parsed.targets],
            targets_clean=[],
            sanitizer_notes=sanitizer_notes_from_result(rejection),
            status="rejected",
            reject_reason=rejection.reason,
        )
        self._session.add(signal)
        self._session.flush()
        return signal
