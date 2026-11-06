from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tg_event.config import load_dotenv


def main() -> None:
    load_dotenv("/etc/tg-event.env")
    required = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE", "OPENROUTER_API_KEY", "BOT_TOKEN")
    missing = [
        name for name in required
        if not os.getenv(name, "").strip() or os.getenv(name) == "NOT_CONFIGURED"
    ]
    if os.getenv("TELEGRAM_API_ID") == "1":
        missing.append("TELEGRAM_API_ID")
    if missing:
        raise SystemExit("Заполните /etc/tg-event.env: " + ", ".join(missing))
    print("Обязательные реквизиты заполнены")


if __name__ == "__main__":
    main()
