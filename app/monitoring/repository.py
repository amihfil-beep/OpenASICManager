"""SQLite persistence for miner monitoring state."""

import sqlite3
from pathlib import Path

import config as app_config
from db import db


def read_fleet_health_rows():
    """Read-only snapshot; do not switch SQLite journaling mode."""
    path = Path(app_config.DATABASE_PATH).resolve()
    conn = sqlite3.connect(
        path.as_uri() + "?mode=ro", uri=True, timeout=3,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn.execute(
            """
            SELECT last_state, last_seen
            FROM miners
            WHERE enabled=1
              AND driver!=\'unset\'
            """
        ).fetchall()
    finally:
        conn.close()


def save_miner_status(
    miner_id,
    status,
    seen_at,
):
    conn = db()
    try:
        conn.execute("""
            UPDATE miners
            SET
                model=?,
                firmware=?,
                last_state=?,
                hashrate=?,
                avg_hashrate=?,
                temp=?,
                power=?,
                pool=?,
                last_seen=?,
                last_error=NULL
            WHERE id=?
        """, (
            status.get("model"),
            status.get("firmware"),
            status.get("state"),
            status.get("hashrate"),
            status.get("avg_hashrate"),
            status.get("temp"),
            status.get("power"),
            status.get("pool"),
            seen_at,
            miner_id,
        ))
        conn.commit()
    finally:
        conn.close()


def mark_miner_offline(
    miner_id,
    error,
):
    conn = db()
    try:
        conn.execute("""
            UPDATE miners
            SET last_state='OFFLINE', last_error=?
            WHERE id=?
        """, (
            str(error),
            miner_id,
        ))
        conn.commit()
    finally:
        conn.close()


__all__ = (
    "save_miner_status",
    "mark_miner_offline",
    "read_fleet_health_rows",
)
