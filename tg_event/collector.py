from __future__ import annotations

from tg_event.config import Settings
from tg_event.storage import RawPost


async def collect_posts(settings: Settings) -> list[RawPost]:
    from telethon import TelegramClient

    posts: list[RawPost] = []
    async with TelegramClient(
        settings.session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    ) as client:
        await client.start(phone=settings.telegram_phone)
        for channel in settings.channels:
            async for message in client.iter_messages(channel, limit=settings.posts_per_channel):
                text = (message.message or "").strip()
                if not text:
                    continue
                published_at = message.date.isoformat()
                posts.append(
                    RawPost(
                        source=channel,
                        message_id=message.id,
                        published_at=published_at,
                        text=text,
                        url=f"https://t.me/{channel}/{message.id}",
                        links=_message_links(message),
                        city=settings.city,
                    )
                )
    return posts


def _message_links(message) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    text = message.message or ""

    for entity in message.entities or []:
        url = getattr(entity, "url", None)
        if not url:
            offset = getattr(entity, "offset", None)
            length = getattr(entity, "length", None)
            if offset is not None and length is not None:
                candidate = text[offset : offset + length]
                if candidate.startswith(("http://", "https://")):
                    url = candidate
        if url and url not in seen:
            links.append(url)
            seen.add(url)

    return links
