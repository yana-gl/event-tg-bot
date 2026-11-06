from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deploy.check_config import main as check_config
from tg_event.config import Settings


async def main() -> None:
    check_config()
    settings = Settings.from_env("/etc/tg-event.env")
    from telethon import TelegramClient

    client = TelegramClient(settings.session_name, settings.telegram_api_id, settings.telegram_api_hash)
    try:
        await client.start(phone=settings.telegram_phone)
        print("Telegram авторизован. Сессию можно использовать для сборщика.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
