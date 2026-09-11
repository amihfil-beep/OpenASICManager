"""SQLite persistence helpers for maintenance operations."""

from pathlib import Path
from urllib.parse import quote
import sqlite3


def _readonly_uri(path):
    path = Path(path).resolve()
    return "file:" + quote(path.as_posix(), safe="/") + "?mode=ro"


def _readonly_connection(path):
    return sqlite3.connect(
        _readonly_uri(path),
        uri=True,
        timeout=20,
    )


def sqlite_quick_check(path):
    path = Path(path)

    if not path.is_file():
        raise RuntimeError(
            "SQLite database does not exist: "
            + str(path)
        )

    try:
        conn = _readonly_connection(path)
        try:
            rows = conn.execute(
                "PRAGMA quick_check"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(
            "SQLite quick_check failed for "
            + str(path)
            + ": "
            + str(exc)
        ) from exc

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


def diagnostic_state(path):
    """Return the minimal read-only state required by doctor/preflight."""

    try:
        conn = _readonly_connection(path)
        try:
            scheduler = conn.execute(
                "SELECT value FROM settings "
                "WHERE key='scheduler_enabled'"
            ).fetchone()

            schedule_rules = conn.execute(
                "SELECT COUNT(*) FROM schedule_rules"
            ).fetchone()[0]

            miners = conn.execute(
                "SELECT COUNT(*) FROM miners"
            ).fetchone()[0]

            enabled_miners = conn.execute(
                "SELECT COUNT(*) FROM miners "
                "WHERE enabled=1"
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(
            "Unable to read diagnostic database state: "
            + str(exc)
        ) from exc

    scheduler_enabled = bool(
        scheduler
        and str(scheduler[0]).strip().lower()
        in {"1", "true", "yes", "on"}
    )

    return {
        "scheduler_enabled": scheduler_enabled,
        "schedule_rules": int(schedule_rules),
        "miners": int(miners),
        "enabled_miners": int(enabled_miners),
    }


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

    source_conn = _readonly_connection(source)
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
    "diagnostic_state",
    "sqlite_quick_check",
)
