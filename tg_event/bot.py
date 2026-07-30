from __future__ import annotations

from datetime import date, timedelta

from tg_event.database import connect_database, fetch_published_events
from tg_event.config import Settings

PERIODS = {
    "today": lambda today: (today, today),
    "tomorrow": lambda today: (today + timedelta(days=1), today + timedelta(days=1)),
    "week": lambda today: (today, today + timedelta(days=7)),
    "month": lambda today: (today, today + timedelta(days=30)),
}

PERIOD_TITLES = {
    "today": "Сегодня",
    "tomorrow": "Завтра",
    "week": "Неделя",
    "month": "Месяц",
}

MAX_MESSAGE_LEN = 4000


def register_handlers(client, settings: Settings) -> None:
    from telethon import Button
    from telethon import events

    @client.on(events.NewMessage(pattern="/start"))
    async def _start(event):
        keyboard = [
            [
                Button.inline("Сегодня", b"today"),
                Button.inline("Завтра", b"tomorrow"),
            ],
            [
                Button.inline("Неделя", b"week"),
                Button.inline("Месяц", b"month"),
            ],
        ]
        await event.respond("Выбери период:", buttons=keyboard)

    @client.on(events.CallbackQuery)
    async def _callback(event):
        data = event.data.decode("utf-8")
        today = date.today()
        if data not in PERIODS:
            await event.answer()
            return

        date_from, date_to = PERIODS[data](today)
        with connect_database(settings.database_path) as connection:
            rows = fetch_published_events(
                connection,
                date_from.isoformat(),
                date_to.isoformat(),
            )
        await event.answer()

        if not rows:
            await event.respond(
                f"За период «{PERIOD_TITLES[data]}» нет опубликованных событий."
            )
            return

        text = format_events(rows)
        for chunk in _split_messages(text):
            await event.respond(chunk)

    @client.on(events.NewMessage(pattern="/help"))
    async def _help(event):
        await event.respond(
            "Бот афиши событий.\n\n"
            "Нажми /start, выбери период кнопками — получишь опубликованные события."
        )


def format_events(rows: list[dict]) -> str:
    parts: list[str] = []
    for row in rows:
        lines: list[str] = []
        lines.append(row["title"] or "Без названия")

        when = row["date"] or ""
        if row["time"]:
            when = f"{when}, {row['time']}" if when else row["time"]
        if row["end_date"]:
            when += f" — {row['end_date']}"
        if when:
            lines.append(f"Когда: {when}")

        where = row["place"] or ""
        if row["address"]:
            where = f"{where}, {row['address']}" if where else row["address"]
        if where:
            lines.append(f"Где: {where}")

        if row["price"]:
            lines.append(f"Цена: {row['price']}")
        if row["category"]:
            lines.append(f"Категория: {row['category']}")

        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _split_messages(text: str) -> list[str]:
    if len(text) <= MAX_MESSAGE_LEN:
        return [text]

    chunks: list[str] = []
    block = ""
    for part in text.split("\n\n"):
        candidate = part if not block else f"{block}\n\n{part}"
        if len(candidate) > MAX_MESSAGE_LEN and block:
            chunks.append(block)
            block = part
        else:
            block = candidate
    if block:
        chunks.append(block)
    return chunks