from __future__ import annotations

from decimal import Decimal

from shared.signal.types import (
    ParsedEntrySignal,
    RejectedSignal,
    SanitizedSignal,
    SignalSide,
    TargetCorrection,
)


def sanitize_signal(
    signal: ParsedEntrySignal,
    *,
    decimal_shift_lo: Decimal = Decimal("5"),
    decimal_shift_hi: Decimal = Decimal("20"),
) -> SanitizedSignal | RejectedSignal:
    """Sanitize an entry signal according to architecture section 8."""

    if decimal_shift_lo <= 0 or decimal_shift_hi < decimal_shift_lo:
        raise ValueError("invalid decimal-shift range")
    if not is_stop_loss_on_correct_side(signal.side, signal.entry, signal.stop_loss):
        return RejectedSignal(reason="wrong_side_stop_loss", alert=True)
    if not signal.targets:
        return RejectedSignal(reason="no_viable_targets", alert=True)

    median = _median(signal.targets)
    corrected_targets: list[Decimal] = []
    corrections: list[TargetCorrection] = []
    for target in signal.targets:
        corrected = _decimal_shift_candidate(
            target=target,
            median=median,
            side=signal.side,
            entry=signal.entry,
            decimal_shift_lo=decimal_shift_lo,
            decimal_shift_hi=decimal_shift_hi,
        )
        if corrected is None:
            corrected_targets.append(target)
        else:
            corrected_targets.append(corrected)
            corrections.append(
                TargetCorrection(
                    original=target,
                    corrected=corrected,
                    reason="decimal_shift",
                )
            )

    surviving: list[tuple[int, Decimal]] = []
    dropped: list[tuple[int, Decimal]] = []
    for index, target in enumerate(corrected_targets):
        if is_target_on_profitable_side(signal.side, signal.entry, target):
            surviving.append((index, target))
        else:
            dropped.append((index, target))

    if not surviving:
        return RejectedSignal(reason="no_viable_targets", alert=True)

    surviving_values = tuple(target for _, target in surviving)
    kept_indices = longest_monotonic_subsequence_indices(surviving_values, side=signal.side)
    kept_index_set = set(kept_indices)
    clean = tuple(surviving_values[index] for index in kept_indices)
    dropped.extend(
        surviving[index] for index in range(len(surviving)) if index not in kept_index_set
    )

    if not clean:
        return RejectedSignal(reason="no_viable_targets", alert=True)

    return SanitizedSignal(
        symbol=signal.symbol,
        side=signal.side,
        entry=signal.entry,
        stop_loss=signal.stop_loss,
        leverage=signal.leverage,
        targets_raw=signal.targets,
        targets_clean=clean,
        dropped=tuple(target for _, target in sorted(dropped)),
        corrected=tuple(corrections),
        alert=bool(dropped or corrections),
    )


def is_target_on_profitable_side(
    side: SignalSide,
    entry: Decimal,
    target: Decimal,
) -> bool:
    """Return whether target is on the profitable side of entry."""

    if side is SignalSide.LONG:
        return target > entry
    return target < entry


def is_stop_loss_on_correct_side(
    side: SignalSide,
    entry: Decimal,
    stop_loss: Decimal,
) -> bool:
    """LONG requires SL < entry; SHORT requires SL > entry."""

    if side is SignalSide.LONG:
        return stop_loss < entry
    return stop_loss > entry


def longest_monotonic_subsequence_indices(
    values: tuple[Decimal, ...],
    *,
    side: SignalSide,
) -> tuple[int, ...]:
    """Return kept indices for strict ascending LONG or strict descending SHORT.

    Equal-length paths prefer the nearer profitable target sequence. If values
    are also equal, the lexicographically smallest kept index tuple wins.
    """

    if not values:
        return ()

    paths: list[tuple[int, ...]] = []
    for index, value in enumerate(values):
        best = (index,)
        for previous_index in range(index):
            previous = values[previous_index]
            monotonic = previous < value if side is SignalSide.LONG else previous > value
            if not monotonic:
                continue
            candidate = paths[previous_index] + (index,)
            if _prefer_path(candidate, best, values=values, side=side):
                best = candidate
        paths.append(best)

    result = paths[0]
    for candidate in paths[1:]:
        if _prefer_path(candidate, result, values=values, side=side):
            result = candidate
    return result


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")


def _decimal_shift_candidate(
    *,
    target: Decimal,
    median: Decimal,
    side: SignalSide,
    entry: Decimal,
    decimal_shift_lo: Decimal,
    decimal_shift_hi: Decimal,
) -> Decimal | None:
    if target <= 0 or median <= 0:
        return None
    ratio = max(target / median, median / target)
    if not decimal_shift_lo <= ratio <= decimal_shift_hi:
        return None

    candidates = (target * Decimal("10"), target / Decimal("10"))
    valid = [
        candidate
        for candidate in candidates
        if is_target_on_profitable_side(side, entry, candidate)
    ]
    if not valid:
        return None
    return min(valid, key=lambda candidate: (abs(candidate - median), candidates.index(candidate)))


def _prefer_path(
    candidate: tuple[int, ...],
    current: tuple[int, ...],
    *,
    values: tuple[Decimal, ...],
    side: SignalSide,
) -> bool:
    if len(candidate) != len(current):
        return len(candidate) > len(current)

    candidate_values = tuple(values[index] for index in candidate)
    current_values = tuple(values[index] for index in current)
    if candidate_values != current_values:
        if side is SignalSide.LONG:
            return candidate_values < current_values
        return candidate_values > current_values
    return candidate < current
