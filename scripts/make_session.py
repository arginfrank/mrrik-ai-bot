from __future__ import annotations

import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from shared.config import load_config


async def run() -> None:
    """Interactively create a Telethon StringSession for the source userbot."""
    settings = load_config().env
    if settings.tg_api_id is None:
        raise RuntimeError("Missing required setting: TG_API_ID")
    if settings.tg_api_hash is None:
        raise RuntimeError("Missing required setting: TG_API_HASH")

    client = TelegramClient(
        StringSession(),
        settings.tg_api_id,
        settings.tg_api_hash.get_secret_value(),
    )
    try:
        await client.start()
        print("WARNING: The StringSession below is a secret. Store it securely.")
        print("Paste this value into TG_USERBOT_SESSION in your .env file:")
        print(client.session.save())
    finally:
        await client.disconnect()


def main() -> None:
    """Run the interactive StringSession generator."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
