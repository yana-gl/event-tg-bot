from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tg_event.config import Settings
from tg_event.storage import RawPost
from tg_event.channels import initialize_recent_channel, record_channel_check
from tg_event.database import connect_database


async def collect_posts(
    settings: Settings, tracked_posts: dict[str, list[int]] | None = None,
    channel_progress: dict[str, tuple[int, str]] | None = None,
    errors: list[str] | None = None,
    managed_channels: dict[str, dict] | None = None,
    state_key: str | None = None,
) -> list[RawPost]:
    channels = list(managed_channels or {})
    if not channels:
        return []
    from telethon import TelegramClient

    def record(channel, **kwargs):
        if managed_channels is not None:
            with connect_database(settings.database_path) as connection:
                record_channel_check(connection, channel, **kwargs)

    for channel in channels:
        record(channel, started=True)
    posts: list[RawPost] = []
    async with TelegramClient(
        settings.session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    ) as client:
        try:
            await client.start(phone=settings.telegram_phone)
        except Exception:
            for channel in channels:
                record(channel, error="Не удалось подключиться к Telegram; проверьте подключение и сессию")
            raise
        for channel in channels:
            try:
                options = {"limit": settings.posts_per_channel}
                start = None
                if channel_progress is not None:
                    last_id, since_date = channel_progress[channel]
                    managed = (managed_channels or {}).get(channel)
                    if managed and managed["mode"] == "recent" and managed["initial_start_id"] is None:
                        initial = [m.id async for m in client.iter_messages(channel, limit=managed["initial_posts"])]
                        with connect_database(settings.database_path) as connection:
                            last_id = initialize_recent_channel(connection, channel, state_key, managed["initial_posts"], initial)
                        since_date = "1970-01-01"
                    options["reverse"] = True
                    if last_id:
                        options["min_id"] = last_id
                    else:
                        start = datetime.fromisoformat(since_date).replace(tzinfo=timezone.utc)
                        options["offset_date"] = start - timedelta(seconds=1)
                messages = []
                async for message in client.iter_messages(channel, **options):
                    if start is None or message.date >= start:
                        messages.append(message)
                seen = {m.id for m in messages}
                missing_ids = [i for i in (tracked_posts or {}).get(channel, []) if i not in seen]
                if missing_ids:
                    messages.extend(m for m in await client.get_messages(channel, ids=missing_ids) if m is not None)
                record(channel)
            except Exception as error:
                record(channel, error="Не удалось прочитать канал; проверьте его имя и доступность")
                if errors is None:
                    raise
                errors.append(channel)
                print(f"Collection failed: {channel} ({type(error).__name__})", flush=True)
                continue
            for message in messages:
                text = (message.message or "").strip()
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
                        edited_at=message.edit_date.isoformat() if message.edit_date else None,
                        tracked_only=message.id not in seen,
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
