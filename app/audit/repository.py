"""SQLite persistence for the audit event log."""

from db import db


def write_action_log(
    timestamp,
    source,
    action,
    miner_id,
    ip,
    name,
    success,
    message,
):
    conn = db()

    try:
        conn.execute("""
            INSERT INTO action_log
            (
                ts,
                source,
                action,
                miner_id,
                ip,
                name,
                success,
                message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            int(timestamp),
            str(source),
            str(action),
            miner_id,
            ip,
            name,
            1 if success else 0,
            str(message),
        ))

        conn.commit()

    finally:
        conn.close()


def list_action_log(limit):
    conn = db()

    try:
        return conn.execute("""
            SELECT
                id,
                ts,
                source,
                action,
                miner_id,
                ip,
                name,
                success,
                message

            FROM action_log

            ORDER BY id DESC

            LIMIT ?
        """, (
            int(limit),
        )).fetchall()

    finally:
        conn.close()


__all__ = (
    "write_action_log",
    "list_action_log",
)
