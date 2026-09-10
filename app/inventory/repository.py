"""Persistence helpers for ASIC inventory state."""

from db import db


__all__ = (
    "list_miners",
    "set_miner_enabled",
    "set_miner_schedule_enabled",
    "set_all_schedule_enabled",
    "clear_manual_overrides",
)


def list_miners():
    conn = db()
    try:
        return list(
            conn.execute("""
                SELECT *
                FROM miners
            """).fetchall()
        )
    finally:
        conn.close()


def set_miner_enabled(
    miner_id,
    enabled,
):
    conn = db()
    try:
        conn.execute("""
            UPDATE miners
            SET enabled=?
            WHERE id=?
        """, (
            1 if enabled else 0,
            int(miner_id),
        ))
        conn.commit()
    finally:
        conn.close()


def set_miner_schedule_enabled(
    miner_id,
    enabled,
):
    conn = db()
    try:
        conn.execute("""
            UPDATE miners
            SET schedule_enabled=?
            WHERE id=?
        """, (
            1 if enabled else 0,
            int(miner_id),
        ))
        conn.commit()
    finally:
        conn.close()


def set_all_schedule_enabled(enabled):
    conn = db()
    try:
        cur = conn.execute("""
            UPDATE miners
            SET schedule_enabled=?
            WHERE
                enabled=1
                AND driver IN (
                    'awesome',
                    'bitmain_stock'
                )
        """, (
            1 if enabled else 0,
        ))
        changed = cur.rowcount
        conn.commit()
        return changed
    finally:
        conn.close()


def clear_manual_overrides():
    conn = db()
    try:
        cur = conn.execute("""
            UPDATE miners
            SET manual_override_until=NULL
            WHERE manual_override_until IS NOT NULL
        """)
        changed = cur.rowcount
        conn.commit()
        return changed
    finally:
        conn.close()
