from __future__ import annotations

from decimal import Decimal

from services.signal_ingestor.events import (
    build_signal_created_payload,
    build_signal_rejected_payload,
    sanitizer_notes_from_result,
)
from shared.models import Signal
from shared.signal.types import SanitizedSignal, SignalSide, TargetCorrection


def test_build_signal_created_payload_matches_canonical_shape() -> None:
    signal = Signal(
        id=123,
        source_msg_id=456,
        symbol="HBARUSDT",
        side="LONG",
        entry=Decimal("0.071450000000000000"),
        stop_loss=Decimal("0.070770000000000000"),
        leverage=42,
        targets_raw=["0.072", "0.07186", "0.07238", "0.07296", "0.07309"],
        targets_clean=["0.07186", "0.07238", "0.07296", "0.07309"],
        sanitizer_notes={
            "dropped": ["0.072"],
            "corrected": [],
            "alert": True,
        },
        status="accepted",
        reject_reason=None,
    )

    payload = build_signal_created_payload(signal)

    assert payload == {
        "signal_id": 123,
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
    }


def test_rejected_payload_includes_provided_identifiers() -> None:
    payload = build_signal_rejected_payload(
        source_msg_id=456,
        reason="wrong_side_stop_loss",
        signal_id=123,
    )

    assert payload == {
        "source_msg_id": 456,
        "reason": "wrong_side_stop_loss",
        "signal_id": 123,
    }


def test_sanitizer_corrections_are_json_safe_strings() -> None:
    sanitized = SanitizedSignal(
        symbol="HBARUSDT",
        side=SignalSide.LONG,
        entry=Decimal("0.07145"),
        stop_loss=Decimal("0.07077"),
        leverage=42,
        targets_raw=(Decimal("0.7186"),),
        targets_clean=(Decimal("0.07186"),),
        corrected=(
            TargetCorrection(
                original=Decimal("0.7186"),
                corrected=Decimal("0.07186"),
                reason="decimal_shift",
            ),
        ),
        alert=True,
    )

    notes = sanitizer_notes_from_result(sanitized)

    assert notes == {
        "dropped": [],
        "corrected": [
            {
                "original": "0.7186",
                "corrected": "0.07186",
                "reason": "decimal_shift",
            }
        ],
        "alert": True,
    }
