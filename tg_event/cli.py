from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import closing
from pathlib import Path

from tg_event.cleanup import run_cleanup
from tg_event.config import Settings, load_dotenv
from tg_event.database import connect_database, init_database
from tg_event.pipeline import collect_to_file, parse_file
from tg_event.watcher import run_watch, today_string


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram event parser POC")
    parser.add_argument(
        "command",
        choices=["collect", "parse", "run", "watch", "serve", "cleanup"],
        help="collect, parse, watch, serve admin UI, or preview/apply data cleanup",
    )
    parser.add_argument("--input", type=Path, help="Raw JSONL file for parse command")
    parser.add_argument("--apply", action="store_true", help="Apply cleanup after backup; default is preview only")
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of raw posts to parse in this run",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=1800,
        help="Seconds between watch cycles",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one watch cycle and exit",
    )
    parser.add_argument(
        "--since",
        default=today_string(),
        help="Initial watch cutoff, YYYY-MM-DD. Saved channel progress resumes across restarts.",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("data/state/channels.json"),
        help="Path to watch state JSON",
    )
    parser.add_argument(
        "--max-posts-per-cycle",
        type=int,
        default=None,
        help="Optional total maximum new posts to parse in one watch cycle",
    )
    parser.add_argument(
        "--max-posts-per-channel",
        type=int,
        default=10,
        help="Maximum new posts to parse per channel in one watch cycle",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Admin server host (default: 127.0.0.1 or ADMIN_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Admin server port (default: 8080 or ADMIN_PORT)",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Serve admin API only (use Vite dev server for the UI)",
    )
    args = parser.parse_args()

    if args.command == "cleanup":
        load_dotenv()
        result = run_cleanup(
            Path(os.getenv("DATABASE_PATH", "data/tg_event.sqlite3")),
            Path(os.getenv("RAW_DIR", "data/raw")), Path(os.getenv("PARSED_DIR", "data/parsed")),
            os.getenv("EVENT_TIMEZONE", "Asia/Vladivostok").strip(), apply=args.apply,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    settings = Settings.from_env()
    if args.command == "collect":
        raw_path = asyncio.run(collect_to_file(settings))
        print(raw_path)
    elif args.command == "parse":
        if not args.input:
            raise SystemExit("--input is required for parse")
        parsed_path = parse_file(settings, args.input, limit=args.limit)
        print(parsed_path)
    elif args.command == "run":
        raw_path = asyncio.run(collect_to_file(settings))
        parsed_path = parse_file(settings, raw_path, limit=args.limit)
        print(parsed_path)
    elif args.command == "watch":
        asyncio.run(
            run_watch(
                settings,
                interval=args.interval,
                once=args.once,
                since_date=args.since,
                state_path=args.state,
                max_posts_per_cycle=args.max_posts_per_cycle,
                max_posts_per_channel=args.max_posts_per_channel,
            )
        )
    elif args.command == "serve":
        _serve(
            settings,
            host=args.host or settings.admin_host,
            port=args.port or settings.admin_port,
            dev=args.dev,
        )


def _serve(settings: Settings, host: str, port: int, dev: bool) -> None:
    if not settings.admin_user or not settings.admin_password:
        raise SystemExit("ADMIN_USER and ADMIN_PASSWORD are required for serve")
    if not settings.admin_secret.strip():
        raise SystemExit("ADMIN_SECRET is required for serve")

    from tg_event.admin import serve as serve_admin

    with closing(connect_database(settings.database_path)) as connection:
        init_database(connection)
    if not dev and not (settings.admin_static_dir / "index.html").exists():
        print(
            "warning: admin-ui/dist not found; run `cd admin-ui && npm install && npm run build`",
            flush=True,
        )
    serve_admin(settings, host=host, port=port, dev=dev)


if __name__ == "__main__":
    main()
