from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class SignalSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class MessageKind(StrEnum):
    ENTRY = "entry"
    RESULT = "result"
    STOP = "stop"


@dataclass(frozen=True)
class ParsedEntrySignal:
    kind: MessageKind
    symbol: str
    side: SignalSide
    entry: Decimal
    stop_loss: Decimal
    leverage: int
    targets: tuple[Decimal, ...]
    raw_text: str


@dataclass(frozen=True)
class ParsedResultMessage:
    kind: MessageKind
    symbol: str
    target_index: int | None
    profit_pct: Decimal
    period_minutes: int | None
    raw_text: str


@dataclass(frozen=True)
class ParsedStopMessage:
    kind: MessageKind
    symbol: str
    loss_pct: Decimal
    raw_text: str


ParsedMessage = ParsedEntrySignal | ParsedResultMessage | ParsedStopMessage


@dataclass(frozen=True)
class TargetCorrection:
    original: Decimal
    corrected: Decimal
    reason: str


@dataclass(frozen=True)
class SanitizedSignal:
    symbol: str
    side: SignalSide
    entry: Decimal
    stop_loss: Decimal
    leverage: int
    targets_raw: tuple[Decimal, ...]
    targets_clean: tuple[Decimal, ...]
    dropped: tuple[Decimal, ...] = field(default_factory=tuple)
    corrected: tuple[TargetCorrection, ...] = field(default_factory=tuple)
    alert: bool = False


@dataclass(frozen=True)
class RejectedSignal:
    reason: str
    alert: bool = True
