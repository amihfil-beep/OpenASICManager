"""SQLite persistence helpers for maintenance backups."""

from pathlib import Path
from urllib.parse import quote
import sqlite3


def _readonly_uri(path):
    path = Path(path).resolve()
    return "file:" + quote(path.as_posix(), safe="/") + "?mode=ro"


def sqlite_quick_check(path):
    path = Path(path)

    if not path.is_file():
        raise RuntimeError(
            "SQLite database does not exist: "
            + str(path)
        )

    conn = sqlite3.connect(
        _readonly_uri(path),
        uri=True,
        timeout=20,
    )

    try:
        rows = conn.execute(
            "PRAGMA quick_check"
        ).fetchall()
    finally:
        conn.close()

    messages = [
        str(row[0])
        for row in rows
    ]

    if messages != ["ok"]:
        raise RuntimeError(
            "SQLite quick_check failed for "
            + str(path)
            + ": "
            + "; ".join(messages)
        )

    return "ok"


def backup_sqlite(source, destination):
    source = Path(source)
    destination = Path(destination)

    if not source.is_file():
        raise RuntimeError(
            "SQLite database does not exist: "
            + str(source)
        )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    source_conn = sqlite3.connect(
        _readonly_uri(source),
        uri=True,
        timeout=20,
    )

    destination_conn = sqlite3.connect(
        str(destination),
        timeout=20,
    )

    try:
        source_conn.backup(
            destination_conn
        )

        # Store backup snapshots as standalone rollback-journal
        # databases. The application will switch restored databases
        # back to WAL mode on first normal open.
        destination_conn.execute(
            "PRAGMA journal_mode=DELETE"
        )
        destination_conn.commit()
    finally:
        destination_conn.close()
        source_conn.close()

    sqlite_quick_check(
        destination
    )


__all__ = (
    "backup_sqlite",
    "sqlite_quick_check",
)
