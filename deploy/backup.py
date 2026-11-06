from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def backup_database(database: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".sqlite-backup-", dir=directory)
    os.close(descriptor)
    temporary = Path(name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    destination = directory / f"tg-event-{stamp}.sqlite3.gz"
    compressed = destination.with_suffix(".tmp")
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
        with temporary.open("rb") as source, gzip.open(compressed, "wb") as target:
            shutil.copyfileobj(source, target)
        compressed.chmod(0o600)
        compressed.replace(destination)
        backups = sorted(directory.glob("tg-event-????????-??????-??????.sqlite3.gz"), reverse=True)
        for old in backups[7:]:
            old.unlink()
        return destination
    finally:
        temporary.unlink(missing_ok=True)
        compressed.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("/var/lib/tg-event/tg_event.sqlite3"))
    parser.add_argument("--directory", type=Path, default=Path("/var/backups/tg-event"))
    args = parser.parse_args()
    path = backup_database(args.database, args.directory)
    print(f"SQLite backup created: {path.name}")


if __name__ == "__main__":
    main()
