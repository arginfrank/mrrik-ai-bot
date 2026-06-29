from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import logging
from typing import Protocol

from services.signal_ingestor.events import (
    build_signal_created_payload,
    build_signal_rejected_payload,
)
from shared.signal.parser import SignalParseError, parse_message
from shared.signal.sanitizer import sanitize_signal
from shared.signal.types import MessageKind, ParsedEntrySignal, RejectedSignal


SIGNALS_STREAM = "signals"
LOGGER = logging.getLogger(__name__)


class SignalEventPublisher(Protocol):
    async def publish(
        self,
        *,
        stream: str,
        event_type: str,
        payload: dict,
    ) -> object: ...


@dataclass(frozen=True)
class IngestResult:
    status: str
    event_type: str | None = None
    signal_id: int | None = None
    reason: str | None = None


async def process_source_message(
    *,
    text: str,
    source_msg_id: int | None,
    repository: object,
    publisher: SignalEventPublisher,
    valid_symbols: Collection[str] | None = None,
) -> IngestResult:
    """Parse, sanitize, persist, and publish events for one source message."""
    try:
        parsed = parse_message(text, valid_symbols=valid_symbols)
    except SignalParseError as error:
        reason = f"parse_error:{error}"
        await publisher.publish(
            stream=SIGNALS_STREAM,
            event_type="signal.rejected",
            payload=build_signal_rejected_payload(
                source_msg_id=source_msg_id,
                reason=reason,
                raw_excerpt=text[:500],
            ),
        )
        return IngestResult(
            status="rejected",
            event_type="signal.rejected",
            reason=reason,
        )

    if parsed is None:
        return IngestResult(status="ignored", reason="unrelated")
    if parsed.kind in {MessageKind.RESULT, MessageKind.STOP}:
        return IngestResult(status="ignored", reason="logging_only")
    if not isinstance(parsed, ParsedEntrySignal):
        return IngestResult(status="ignored", reason="logging_only")

    sanitized = sanitize_signal(parsed)
    if isinstance(sanitized, RejectedSignal):
        signal = repository.create_rejected_signal(
            parsed=parsed,
            rejection=sanitized,
            source_msg_id=source_msg_id,
        )
        payload = build_signal_rejected_payload(
            source_msg_id=source_msg_id,
            signal_id=signal.id,
            symbol=parsed.symbol,
            reason=sanitized.reason,
        )
        repository.commit()  # type: ignore[attr-defined]
        await _publish_persisted_event(
            publisher=publisher,
            stream=SIGNALS_STREAM,
            event_type="signal.rejected",
            payload=payload,
            signal_id=signal.id,
        )
        return IngestResult(
            status="rejected",
            event_type="signal.rejected",
            signal_id=signal.id,
            reason=sanitized.reason,
        )

    signal = repository.create_accepted_signal(
        parsed=parsed,
        sanitized=sanitized,
        source_msg_id=source_msg_id,
    )
    payload = build_signal_created_payload(signal)
    repository.commit()  # type: ignore[attr-defined]
    await _publish_persisted_event(
        publisher=publisher,
        stream=SIGNALS_STREAM,
        event_type="signal.created",
        payload=payload,
        signal_id=signal.id,
    )
    return IngestResult(
        status="created",
        event_type="signal.created",
        signal_id=signal.id,
    )


async def _publish_persisted_event(
    *,
    publisher: SignalEventPublisher,
    stream: str,
    event_type: str,
    payload: dict,
    signal_id: int,
) -> None:
    try:
        await publisher.publish(
            stream=stream,
            event_type=event_type,
            payload=payload,
        )
    except Exception:
        LOGGER.error(
            "event_type=%s signal_id=%s status=publish_failed persisted=true",
            event_type,
            signal_id,
        )
        raise
