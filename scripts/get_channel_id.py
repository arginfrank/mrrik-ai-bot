from __future__ import annotations

import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from shared.config import load_config


async def run() -> None:
    """List Telegram dialogs so the source channel ID can be identified."""
    settings = load_config().env
    if settings.tg_api_id is None:
        raise RuntimeError("Missing required setting: TG_API_ID")
    if settings.tg_api_hash is None:
        raise RuntimeError("Missing required setting: TG_API_HASH")
    if settings.tg_userbot_session is None:
        raise RuntimeError("Missing required setting: TG_USERBOT_SESSION")

    client = TelegramClient(
        StringSession(settings.tg_userbot_session.get_secret_value()),
        settings.tg_api_id,
        settings.tg_api_hash.get_secret_value(),
    )
    try:
        await client.start()
        print("dialog_id\ttitle\tis_channel\tis_group")
        async for dialog in client.iter_dialogs():
            title = (dialog.name or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")
            print(
                f"{dialog.id}\t{title}\t"
                f"{bool(getattr(dialog, 'is_channel', False))}\t"
                f"{bool(getattr(dialog, 'is_group', False))}"
            )
    finally:
        await client.disconnect()


def main() -> None:
    """Run the Telegram dialog listing tool."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
