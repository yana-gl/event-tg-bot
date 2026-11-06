from __future__ import annotations

import grp
import os
import secrets
from pathlib import Path


def main() -> None:
    path = Path("/etc/tg-event.env")
    if path.exists():
        print("Existing server configuration preserved")
        return
    password = secrets.token_urlsafe(24)
    values = {
        "TELEGRAM_API_ID": "1",
        "TELEGRAM_API_HASH": "NOT_CONFIGURED",
        "TELEGRAM_PHONE": "NOT_CONFIGURED",
        "OPENROUTER_API_KEY": "NOT_CONFIGURED",
        "BOT_TOKEN": "",
        "CITY": "Владивосток",
        "EVENT_TIMEZONE": "Asia/Vladivostok",
        "DATABASE_PATH": "/var/lib/tg-event/tg_event.sqlite3",
        "RAW_DIR": "/var/lib/tg-event/raw",
        "PARSED_DIR": "/var/lib/tg-event/parsed",
        "TELEGRAM_SESSION": "/var/lib/tg-event/sessions/collector",
        "BOT_SESSION_NAME": "/var/lib/tg-event/sessions/bot",
        "SOURCE_RULES_PATH": "/opt/tg-event/source_rules.json",
        "ADMIN_STATIC_DIR": "/opt/tg-event/admin-ui/dist",
        "ADMIN_HOST": "127.0.0.1",
        "ADMIN_PORT": "8080",
        "ADMIN_USER": "admin",
        "ADMIN_PASSWORD": password,
        "ADMIN_SECRET": secrets.token_urlsafe(48),
    }
    os.umask(0o077)
    with path.open("x", encoding="utf-8") as output:
        output.write("".join(f"{key}={value}\n" for key, value in values.items()))
    os.chown(path, 0, grp.getgrnam("tg-event").gr_gid)
    path.chmod(0o640)
    login = Path("/var/lib/tg-event/admin-login.txt")
    with login.open("x", encoding="utf-8") as output:
        output.write(f"Логин: admin\nПароль: {password}\n")
    print("Server configuration created; credentials saved in /var/lib/tg-event/admin-login.txt")


if __name__ == "__main__":
    main()
