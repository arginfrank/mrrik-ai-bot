from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from io import StringIO
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

from shared.config import load_config
from shared.signal.parser import SignalParseError, parse_message
from shared.signal.types import MessageKind


@dataclass(frozen=True)
class HistoricalMessage:
    text: str
    date_utc: datetime


def count_entry_signals_by_utc_day(
    messages: Iterable[HistoricalMessage],
) -> Counter[date]:
    """Count parseable entry signals per UTC day."""
    counts: Counter[date] = Counter()
    for message in messages:
        try:
            parsed = parse_message(message.text)
        except SignalParseError:
            continue
        if parsed is not None and parsed.kind is MessageKind.ENTRY:
            counts[_as_utc(message.date_utc).date()] += 1
    return counts


def render_counts_csv(counts: Mapping[date, int]) -> str:
    """Render daily signal counts as deterministic CSV."""
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("date", "count"))
    for day in sorted(counts):
        writer.writerow((day.isoformat(), counts[day]))
    return output.getvalue()


def write_counts_csv(path: Path, csv_text: str) -> None:
    """Write rendered signal counts to a UTF-8 CSV file."""
    path.write_text(csv_text, encoding="utf-8")


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the history extractor."""
    parser = argparse.ArgumentParser(
        description="Count parseable Telegram entry signals by UTC day.",
    )
    parser.add_argument("--start", required=True, type=_parse_date, help="Inclusive UTC date")
    parser.add_argument("--end", required=True, type=_parse_date, help="Exclusive UTC date")
    parser.add_argument("--output", type=Path, help="Optional CSV output path")
    return parser


async def run(argv: Sequence[str] | None = None) -> None:
    """Fetch source-channel history and print daily entry counts as CSV."""
    args = build_argument_parser().parse_args(argv)
    if args.start >= args.end:
        raise SystemExit("--start must be earlier than --end")

    settings = load_config().env
    if settings.tg_api_id is None:
        raise RuntimeError("Missing required setting: TG_API_ID")
    if settings.tg_api_hash is None:
        raise RuntimeError("Missing required setting: TG_API_HASH")
    if settings.tg_userbot_session is None:
        raise RuntimeError("Missing required setting: TG_USERBOT_SESSION")
    if settings.source_channel_id is None:
        raise RuntimeError("Missing required setting: SOURCE_CHANNEL_ID")

    start_utc = datetime.combine(args.start, time.min, tzinfo=UTC)
    end_utc = datetime.combine(args.end, time.min, tzinfo=UTC)
    client = TelegramClient(
        StringSession(settings.tg_userbot_session.get_secret_value()),
        settings.tg_api_id,
        settings.tg_api_hash.get_secret_value(),
    )
    messages: list[HistoricalMessage] = []
    try:
        await client.start()
        async for message in client.iter_messages(
            settings.source_channel_id,
            offset_date=end_utc,
        ):
            if message.date is None:
                continue
            message_date = _as_utc(message.date)
            if message_date < start_utc:
                break
            if message_date >= end_utc:
                continue
            text_value = message.raw_text
            if text_value:
                messages.append(HistoricalMessage(text=text_value, date_utc=message_date))
    finally:
        await client.disconnect()

    csv_text = render_counts_csv(count_entry_signals_by_utc_day(messages))
    print(csv_text, end="")
    if args.output is not None:
        write_counts_csv(args.output, csv_text)


def main() -> None:
    """Run the historical signal-count extractor."""
    asyncio.run(run())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from error


if __name__ == "__main__":
    main()
