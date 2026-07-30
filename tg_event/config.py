from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "qwen/qwen3-next-80b-a3b-instruct:free"
DEFAULT_FALLBACK_MODEL = "qwen/qwen3.5-flash-02-23"


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_channels(raw: str) -> list[str]:
    channels: list[str] = []
    for item in raw.split(","):
        channel = item.strip()
        if channel.startswith("https://t.me/"):
            channel = channel.removeprefix("https://t.me/")
        elif channel.startswith("http://t.me/"):
            channel = channel.removeprefix("http://t.me/")
        elif channel.startswith("t.me/"):
            channel = channel.removeprefix("t.me/")
        channel = channel.strip().removeprefix("@").strip("/")
        if channel:
            channels.append(channel)

    if not channels:
        raise ValueError("CHANNELS must contain at least one Telegram channel")
    return channels


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_phone: str
    openrouter_api_key: str
    openrouter_model: str
    openrouter_fallback_model: str
    bot_token: str
    city: str
    channels: list[str]
    admin_user: str = ""
    admin_password: str = ""
    admin_secret: str = ""
    admin_static_dir: Path = Path("admin-ui/dist")
    admin_host: str = "127.0.0.1"
    admin_port: int = 8080
    posts_per_channel: int = 20
    raw_dir: Path = Path("data/raw")
    parsed_dir: Path = Path("data/parsed")
    database_path: Path = Path("data/tg_event.sqlite3")
    session_name: str = "tg_event"
    bot_session_name: str = "tg_event_bot"

    @classmethod
    def from_env(cls, env_file: str = ".env") -> "Settings":
        load_dotenv(env_file)
        return cls(
            telegram_api_id=int(_required("TELEGRAM_API_ID")),
            telegram_api_hash=_required("TELEGRAM_API_HASH"),
            telegram_phone=_required("TELEGRAM_PHONE"),
            openrouter_api_key=_required("OPENROUTER_API_KEY"),
            bot_token=os.getenv("BOT_TOKEN", "").strip(),
            openrouter_model=os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL).strip(),
            openrouter_fallback_model=os.getenv(
                "OPENROUTER_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL
            ).strip(),
            city=os.getenv("CITY", "Воронеж").strip(),
            channels=parse_channels(_required("CHANNELS")),
            posts_per_channel=int(os.getenv("POSTS_PER_CHANNEL", "20")),
            raw_dir=Path(os.getenv("RAW_DIR", "data/raw")),
            parsed_dir=Path(os.getenv("PARSED_DIR", "data/parsed")),
            database_path=Path(os.getenv("DATABASE_PATH", "data/tg_event.sqlite3")),
            session_name=os.getenv("TELEGRAM_SESSION", "tg_event").strip(),
            bot_session_name=os.getenv("BOT_SESSION_NAME", "tg_event_bot").strip(),
            admin_user=os.getenv("ADMIN_USER", "").strip(),
            admin_password=os.getenv("ADMIN_PASSWORD", "").strip(),
            admin_secret=os.getenv("ADMIN_SECRET", "").strip(),
            admin_static_dir=Path(os.getenv("ADMIN_STATIC_DIR", "admin-ui/dist")),
            admin_host=os.getenv("ADMIN_HOST", "127.0.0.1").strip(),
            admin_port=int(os.getenv("ADMIN_PORT", "8080")),
        )
