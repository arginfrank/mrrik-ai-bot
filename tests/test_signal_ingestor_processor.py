from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from services.signal_ingestor.events import sanitizer_notes_from_result
from services.signal_ingestor.processor import process_source_message
from shared.models import Signal


HBAR_SIGNAL = """VIP CRYPTO JEMAL
#HBAR/USDT - Long
Entry: 0.07145
Stop Loss: 0.07077
Target 1: 0.072
Target 2: 0.07186
Target 3: 0.07238
Target 4: 0.07296
Target 5: 0.07309
Leverage: x42
"""

ETH_RESULT = """#ETH/USDT
Target Touch 1
Profit: 9.3544%
Period: 14 Minutes
"""

AGLD_STOP = """#AGLD/USDT
Stop Target Hit
Loss: 243.4286%
"""


class FakeRepository:
    def __init__(self) -> None:
        self.accepted: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []
        self.call_order: list[str] = []
        self.committed = False
        self._next_id = 100

    def create_accepted_signal(self, **kwargs: Any) -> Signal:
        self.call_order.append("persist")
        self.accepted.append(kwargs)
        parsed = kwargs["parsed"]
        sanitized = kwargs["sanitized"]
        signal = Signal(
            id=self._next_id,
            source_msg_id=kwargs["source_msg_id"],
            symbol=sanitized.symbol,
            side=sanitized.side.value,
            entry=sanitized.entry,
            stop_loss=sanitized.stop_loss,
            leverage=sanitized.leverage,
            targets_raw=[str(value) for value in parsed.targets],
            targets_clean=[str(value) for value in sanitized.targets_clean],
            sanitizer_notes=sanitizer_notes_from_result(sanitized),
            status="accepted",
            reject_reason=None,
        )
        self._next_id += 1
        return signal

    def create_rejected_signal(self, **kwargs: Any) -> Signal:
        self.call_order.append("persist")
        self.rejected.append(kwargs)
        parsed = kwargs["parsed"]
        rejection = kwargs["rejection"]
        signal = Signal(
            id=self._next_id,
            source_msg_id=kwargs["source_msg_id"],
            symbol=parsed.symbol,
            side=parsed.side.value,
            entry=parsed.entry,
            stop_loss=parsed.stop_loss,
            leverage=parsed.leverage,
            targets_raw=[str(value) for value in parsed.targets],
            targets_clean=[],
            sanitizer_notes=sanitizer_notes_from_result(rejection),
            status="rejected",
            reject_reason=rejection.reason,
        )
        self._next_id += 1
        return signal

    def commit(self) -> None:
        self.call_order.append("commit")
        self.committed = True


class FakePublisher:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.published: list[dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> object:
        if kwargs["payload"].get("signal_id") is not None:
            assert self.repository.committed
        self.repository.call_order.append("publish")
        self.published.append(kwargs)
        return object()


def process(
    text: str,
    *,
    valid_symbols: set[str] | None = None,
) -> tuple[Any, FakeRepository, FakePublisher]:
    repository = FakeRepository()
    publisher = FakePublisher(repository)
    result = asyncio.run(
        process_source_message(
            text=text,
            source_msg_id=456,
            repository=repository,
            publisher=publisher,
            valid_symbols=valid_symbols,
        )
    )
    return result, repository, publisher


def test_hbar_message_persists_and_publishes_created_event() -> None:
    result, repository, publisher = process(HBAR_SIGNAL)

    assert result.status == "created"
    assert result.event_type == "signal.created"
    assert len(repository.accepted) == 1
    assert repository.call_order == ["persist", "commit", "publish"]
    assert publisher.published == [
        {
            "stream": "signals",
            "event_type": "signal.created",
            "payload": {
                "signal_id": 100,
                "symbol": "HBARUSDT",
                "side": "LONG",
                "entry": "0.07145",
                "stop_loss": "0.07077",
                "leverage": 42,
                "targets": ["0.07186", "0.07238", "0.07296", "0.07309"],
                "sanitizer": {
                    "dropped": ["0.072"],
                    "corrected": [],
                    "alert": True,
                },
            },
        }
    ]


def test_wrong_side_stop_loss_persists_and_publishes_rejection() -> None:
    result, repository, publisher = process(
        HBAR_SIGNAL.replace("Stop Loss: 0.07077", "Stop Loss: 0.08000")
    )

    assert result.status == "rejected"
    assert result.reason == "wrong_side_stop_loss"
    assert len(repository.rejected) == 1
    assert repository.call_order == ["persist", "commit", "publish"]
    assert publisher.published[0]["event_type"] == "signal.rejected"
    assert publisher.published[0]["payload"] == {
        "source_msg_id": 456,
        "reason": "wrong_side_stop_loss",
        "signal_id": 100,
        "symbol": "HBARUSDT",
    }


def test_publish_failure_happens_after_commit_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = FakeRepository()

    class FailingPublisher(FakePublisher):
        async def publish(self, **kwargs: Any) -> object:
            assert self.repository.committed
            self.repository.call_order.append("publish")
            raise RuntimeError("redis unavailable")

    with caplog.at_level(
        logging.ERROR,
        logger="services.signal_ingestor.processor",
    ):
        with pytest.raises(RuntimeError, match="redis unavailable"):
            asyncio.run(
                process_source_message(
                    text=HBAR_SIGNAL,
                    source_msg_id=456,
                    repository=repository,
                    publisher=FailingPublisher(repository),
                )
            )

    assert repository.call_order == ["persist", "commit", "publish"]
    assert "status=publish_failed persisted=true" in caplog.text


def test_parser_error_publishes_rejection_without_persisting() -> None:
    invalid = HBAR_SIGNAL.replace("Leverage: x42", "") + ("x" * 600)

    result, repository, publisher = process(invalid)

    assert result.status == "rejected"
    assert result.reason == "parse_error:missing leverage"
    assert repository.accepted == []
    assert repository.rejected == []
    assert publisher.published[0]["event_type"] == "signal.rejected"
    assert len(publisher.published[0]["payload"]["raw_excerpt"]) == 500


def test_eth_result_is_ignored_as_logging_only() -> None:
    result, repository, publisher = process(ETH_RESULT)

    assert result.status == "ignored"
    assert result.reason == "logging_only"
    assert repository.accepted == repository.rejected == []
    assert publisher.published == []


def test_agld_stop_is_ignored_as_logging_only() -> None:
    result, repository, publisher = process(AGLD_STOP)

    assert result.status == "ignored"
    assert result.reason == "logging_only"
    assert repository.accepted == repository.rejected == []
    assert publisher.published == []


def test_unrelated_text_is_ignored() -> None:
    result, repository, publisher = process("Good morning, traders!")

    assert result.status == "ignored"
    assert result.reason == "unrelated"
    assert repository.accepted == repository.rejected == []
    assert publisher.published == []


def test_valid_symbol_collection_rejects_unknown_symbol() -> None:
    result, repository, publisher = process(HBAR_SIGNAL, valid_symbols={"ETHUSDT"})

    assert result.status == "rejected"
    assert result.reason == "parse_error:unknown symbol: HBARUSDT"
    assert repository.accepted == repository.rejected == []
    assert publisher.published[0]["event_type"] == "signal.rejected"
