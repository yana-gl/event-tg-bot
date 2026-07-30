# tg-event POC

Proof of concept for a Telegram event-board bot pipeline.

The POC does three things:

1. Reads recent posts from a manual list of public Telegram channels through Telethon.
2. Saves raw posts to JSONL files under `data/raw/`.
3. Sends each post to OpenRouter, validates the strict event JSON, writes parsed events to `data/parsed/`, and saves them to SQLite.

One Telegram post can produce zero, one, or many events. This matters for digest posts like "5 events for the weekend".

There is no bot, database, moderation UI, Docker, or scheduler yet. This step is only for checking whether real channel posts can be collected and parsed well enough.

## Setup

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Create a local `.env`:

```bash
cp .env.example .env
```

Fill these values in `.env`:

```env
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_PHONE=
OPENROUTER_API_KEY=
```

Do not commit `.env`. It is ignored by git.

The SQLite database is stored at:

```text
data/tg_event.sqlite3
```

## Channels

The current starting list is:

```text
events_vrn,avanturacoffee,zateya_company,vrnfoodblog,vrn_guide,arikasarai,meetbowling_club
```

The city is set to `Воронеж`.

## Run

Collect raw Telegram posts:

```bash
python -m tg_event.cli collect
```

On the first Telethon run, Telegram may ask for a login code and possibly a 2FA password. Telethon will create a local `tg_event.session` file. This file is ignored by git.

Parse a raw JSONL file:

```bash
python -m tg_event.cli parse --input data/raw/posts-YYYYMMDD-HHMMSS.jsonl
```

For the first check, parse only a few posts:

```bash
python -m tg_event.cli parse --input data/raw/posts-YYYYMMDD-HHMMSS.jsonl --limit 5
```

Collect and parse in one command:

```bash
python -m tg_event.cli run
```

You can also limit the combined command:

```bash
python -m tg_event.cli run --limit 5
```

Watch for new posts:

```bash
python -m tg_event.cli watch
```

By default, watcher:

- checks channels every 1800 seconds;
- processes only posts from today's date;
- parses at most 10 new posts per channel in one cycle;
- stores channel progress in `data/state/channels.json`.

Run one safe test cycle and exit:

```bash
python -m tg_event.cli watch --once
```

Override the date cutoff or cycle size:

```bash
python -m tg_event.cli watch --once --since 2026-07-06 --max-posts-per-channel 5
```

## Output

Raw rows contain:

```json
{
  "city": "Воронеж",
  "message_id": 123,
  "published_at": "2026-07-04T10:00:00+00:00",
  "source": "events_vrn",
  "text": "post text",
  "url": "https://t.me/events_vrn/123"
}
```

Parsed rows include the raw post metadata plus:

```json
{
  "events": [
    {
      "is_event": true,
      "title": "Название",
      "date": "2026-07-12",
      "end_date": null,
      "time": "19:30",
      "end_time": null,
      "place": "Площадка",
      "address": "Адрес",
      "category": "music",
      "price": "600р",
      "confidence": 0.86,
      "reason": "Почему модель решила, что это событие",
      "status": "draft"
    }
  ],
  "links": [
    "https://example.com"
  ],
  "post_url": "https://t.me/events_vrn/123"
}
```

If the post has no events, `events` is an empty list:

```json
{
  "events": []
}
```

Statuses:

- `draft`: all newly parsed events (awaiting moderation).
- `published`: approved and visible in the bot.
- `rejected`: declined.
- `repeat`: duplicate of an existing event (not shown in the bot).
- `not_event`: post contains no event.

Allowed categories:

```text
music, cinema, lecture, exhibition, market, workshop, food, sport, party, kids, other
```

## Database

Parsed rows are also saved into SQLite:

```text
data/tg_event.sqlite3
```

Tables:

- `sources`: Telegram channels.
- `raw_posts`: raw Telegram posts.
- `post_links`: links extracted from a post.
- `events`: parsed events linked to `raw_posts`.

View the database in VS Code with a SQLite viewer extension, or from the terminal:

```bash
sqlite3 data/tg_event.sqlite3
```

Useful SQLite commands:

```sql
.tables
SELECT id, title, date, time, place, status FROM events ORDER BY id DESC LIMIT 10;
SELECT source_id, message_id, url FROM raw_posts ORDER BY id DESC LIMIT 10;
```

JSONL files remain useful as debug exports, but SQLite is now the main local storage for the MVP path.

## Test

The unit tests use only the Python standard library:

```bash
python3 -m unittest discover -s tests
```
