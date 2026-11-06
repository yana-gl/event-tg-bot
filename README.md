# tg-event POC

Инструкция по размещению текущей версии на сервере: [deploy/README.md](deploy/README.md).

Админка: https://event-admin.localplayer.dev — HTTPS, вход с данными администратора, SSH-туннель не требуется.

Telegram event aggregator with a bot and a web administration panel.

Module boundaries, transactions and recovery: [ARCHITECTURE.md](ARCHITECTURE.md). Current requirements and acceptance status: [PLAN.md](PLAN.md).

The POC does three things:

1. Reads posts from enabled public Telegram channels managed in the admin panel through Telethon.
2. Saves raw posts to JSONL files under `data/raw/`.
3. Sends each post to OpenRouter, validates the strict event JSON, writes parsed events to `data/parsed/`, and saves them to SQLite.

One Telegram post can produce zero, one, or many events. This matters for digest posts like "5 events for the weekend".

SQLite is the primary storage. The watcher collects new posts and tracked edits; the Telegram bot displays published scheduled events. The Vue/Vuestic admin panel manages channels, manual edits and conflicts. Deployment uses systemd and Caddy; see the deployment guide above.

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

Add channels on `/channels` in the authenticated admin panel. A channel can be paused without losing collected posts or progress. The first load defaults to the latest 20 posts (configurable from 1 to 1000 before loading starts). `CHANNELS` in env is not used.

The default city is Владивосток and the event timezone is `Asia/Vladivostok`. `CITY` and `EVENT_TIMEZONE` can be configured independently.

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
- uses the saved first-load window for new recent channels; legacy profiles keep their initial date cutoff;
- parses at most 10 new posts per channel in one cycle;
- parses up to three posts concurrently, preserving result order;
- continues an unfinished initial channel load immediately while progress advances, without waiting 1800 seconds between successful batches;
- commits results and channel progress together in SQLite; `data/state/channels.json` is an auxiliary copy.

Run one real collection cycle and exit (contacts Telegram/OpenRouter; this is not an isolated test):

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
  "city": "Владивосток",
  "message_id": 123,
  "published_at": "2026-07-04T10:00:00+00:00",
  "source": "example_events",
  "text": "post text",
  "url": "https://t.me/example_events/123"
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
      "status": "published"
    }
  ],
  "links": [
    "https://example.com"
  ],
  "post_url": "https://t.me/example_events/123"
}
```

If the post has no events, `events` is an empty list:

```json
{
  "events": []
}
```

Statuses:

- `draft`: insufficient or ambiguous event details, or an unmatched change requiring review.
- `published`: new events with a title and valid date/date range are published automatically; manually published cards are also supported.
- `rejected`: declined.
- `repeat`: legacy duplicate cards, hidden from the bot. New matches attach sources to an existing card instead of creating a duplicate.
- `not_event`: post contains no event.

Publication status is separate from `event_state`: scheduled, postponed or cancelled. The bot shows only published scheduled events intersecting the selected period. New events that have already ended are filtered during persistence; existing cards keep their update rules.

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
- `events`: event cards; `raw_post_id` points to the primary source.
- `event_sources`: all linked posts and their separate extracted details.
- `event_field_locks`, `event_conflict_resolutions`: protected manual fields and reviewed conflict versions.
- `channel_settings`, `watch_progress`: channel configuration and progress per state profile.
- `maintenance_state`: persistent cleanup schedule.

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

The Python tests use only the standard library, temporary databases and mocked Telegram/LLM calls:

```bash
python3 -m unittest discover -s tests
```

Frontend tests and production build: from `admin-ui`, run `npm test` (Node 22+) and `npm run build`. See [admin-ui/README.md](admin-ui/README.md).
