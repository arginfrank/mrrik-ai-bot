from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta, timezone

from scripts.count_signals import (
    HistoricalMessage,
    count_entry_signals_by_utc_day,
    render_counts_csv,
    write_counts_csv,
)


HBAR_SIGNAL = """#HBAR/USDT - Long
Entry: 0.07145
Stop Loss: 0.07077
Target 1: 0.07186
Leverage: x42
"""

AVAX_SIGNAL = """#AVAX/USDT - Long
Entry: 6.65
Stop Loss: 6.545
Target 1: 6.79114
Leverage: x19
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


def test_count_entry_signals_by_utc_day_ignores_non_entries() -> None:
    messages = [
        HistoricalMessage(HBAR_SIGNAL, datetime(2026, 6, 27, 1, tzinfo=UTC)),
        HistoricalMessage(AVAX_SIGNAL, datetime(2026, 6, 27, 23, tzinfo=UTC)),
        HistoricalMessage(ETH_RESULT, datetime(2026, 6, 27, 12, tzinfo=UTC)),
        HistoricalMessage(AGLD_STOP, datetime(2026, 6, 27, 13, tzinfo=UTC)),
        HistoricalMessage("Good morning!", datetime(2026, 6, 27, 14, tzinfo=UTC)),
        HistoricalMessage(HBAR_SIGNAL, datetime(2026, 6, 28, 2, tzinfo=UTC)),
    ]

    counts = count_entry_signals_by_utc_day(messages)

    assert counts == Counter({date(2026, 6, 27): 2, date(2026, 6, 28): 1})


def test_counting_normalizes_message_time_to_utc() -> None:
    utc_plus_four = timezone(timedelta(hours=4))
    messages = [
        HistoricalMessage(HBAR_SIGNAL, datetime(2026, 6, 28, 1, tzinfo=utc_plus_four)),
    ]

    counts = count_entry_signals_by_utc_day(messages)

    assert counts == Counter({date(2026, 6, 27): 1})


def test_csv_rendering_and_writing_are_deterministic(tmp_path) -> None:
    counts = Counter({date(2026, 6, 28): 1, date(2026, 6, 27): 2})
    expected = "date,count\n2026-06-27,2\n2026-06-28,1\n"

    rendered = render_counts_csv(counts)
    output = tmp_path / "signals.csv"
    write_counts_csv(output, rendered)

    assert rendered == expected
    assert output.read_text(encoding="utf-8") == expected
