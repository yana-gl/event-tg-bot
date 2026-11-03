from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from tg_event.database import connect_database, fetch_published_events
from tg_event.config import Settings

PERIODS = {
    "today": lambda today: (today, today),
    "tomorrow": lambda today: (today + timedelta(days=1), today + timedelta(days=1)),
    "week": lambda today: (today, today + timedelta(days=6)),
    "month": lambda today: (today, today + timedelta(days=29)),
}

PERIOD_TITLES = {
    "today": "Сегодня",
    "tomorrow": "Завтра",
    "week": "Неделя",
    "month": "Месяц",
}

MAX_MESSAGE_LEN = 4000
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря")
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def today_in_city(timezone_name: str = "Asia/Vladivostok") -> date:
    return datetime.now(ZoneInfo(timezone_name)).date()


def register_handlers(client, settings: Settings) -> None:
    ZoneInfo(settings.event_timezone)
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
        today = today_in_city(settings.event_timezone)
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

        text = format_events(rows, date_from=date_from.isoformat())
        for chunk in _split_messages(text):
            await event.respond(chunk, parse_mode="html", link_preview=False)

    @client.on(events.NewMessage(pattern="/help"))
    async def _help(event):
        await event.respond(
            "Бот афиши событий.\n\n"
            "Нажми /start, выбери период кнопками — получишь опубликованные события."
        )


def format_events(rows: list[dict], date_from: str | None = None) -> str:
    parts: list[str] = []
    current_date = None
    for row in rows:
        display_date = max(row["date"], date_from) if date_from else row["date"]
        if display_date != current_date:
            current_date = display_date
            day = date.fromisoformat(current_date)
            parts.append(f"<b>{_date_label(current_date)}, {WEEKDAYS[day.weekday()]}</b>")
        lines: list[str] = []
        lines.append(row["title"] or "Без названия")

        if row["end_date"] and row["end_date"] != row["date"]:
            lines.append(f"{_date_label(row['date'])} — {_date_label(row['end_date'])}")
        if row["time"]:
            lines.append(row["time"])

        where = row["place"] or ""
        if row["address"]:
            where = f"{where}, {row['address']}" if where else row["address"]
        if where:
            lines.append(where)

        if row["price"]:
            lines.append(f"Цена: {row['price']}")
        if row.get("previous_date") and row.get("last_change_at"):
            lines.append(f"Перенесено с {_date_label(row['previous_date'])}")
        if row.get("conflict_url"):
            lines.append("Есть изменение, уточните у организатора")
        lines = [escape(line) for line in lines]
        lines.append(f'<a href="{escape(row["post_url"], quote=True)}">Ссылка</a>')
        update_url = row.get("conflict_url") or row.get("last_change_url")
        if update_url and update_url != row["post_url"]:
            lines.append(f'<a href="{escape(update_url, quote=True)}">Изменение</a>')

        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _date_label(value: str) -> str:
    day = date.fromisoformat(value)
    return f"{day.day} {MONTHS[day.month - 1]}"


def _split_messages(text: str) -> list[str]:
    if len(text) <= MAX_MESSAGE_LEN:
        return [text]

    chunks: list[str] = []
    block = ""
    heading = ""
    pending_heading = False
    for part in text.split("\n\n"):
        if part.startswith("<b>") and part.endswith("</b>"):
            heading = part
            pending_heading = True
            continue
        addition = f"{heading}\n\n{part}" if pending_heading else part
        candidate = addition if not block else f"{block}\n\n{addition}"
        if len(candidate) > MAX_MESSAGE_LEN and block:
            chunks.append(block)
            block = f"{heading}\n\n{part}" if heading else part
        else:
            block = candidate
        pending_heading = False
    if block:
        chunks.append(block)
    return chunks
