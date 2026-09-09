"""SQLite persistence for miner monitoring state."""

from db import db


def save_miner_status(
    miner_id,
    status,
    seen_at,
):
    conn = db()

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
    conn.close()


def mark_miner_offline(
    miner_id,
    error,
):
    conn = db()

    conn.execute("""
        UPDATE miners

        SET
            last_state='OFFLINE',
            last_error=?

        WHERE id=?
    """, (
        str(error),
        miner_id,
    ))

    conn.commit()
    conn.close()


__all__ = (
    "save_miner_status",
    "mark_miner_offline",
)
