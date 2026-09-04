import sqlite3

import config as app_config


def db():
    conn = sqlite3.connect(
        app_config.DATABASE_PATH,
        timeout=10,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    return conn


def get_setting(
    key,
    default=None,
):
    conn = db()

    row = conn.execute(
        """
        SELECT value
        FROM settings
        WHERE key=?
        """,
        (key,),
    ).fetchone()

    conn.close()

    if not row:
        return default

    return row["value"]


def set_setting(
    key,
    value,
):
    conn = db()

    conn.execute("""
        INSERT INTO settings(
            key,
            value
        )
        VALUES (?, ?)

        ON CONFLICT(key)
        DO UPDATE SET
            value=excluded.value
    """, (
        key,
        str(value),
    ))

    conn.commit()
    conn.close()


def get_miner(
    miner_id,
):
    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM miners
        WHERE id=?
        """,
        (miner_id,),
    ).fetchone()

    conn.close()

    return row


def get_poll_miners():
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM miners
        WHERE
            enabled=1
            AND driver IN (
                'awesome',
                'bitmain_stock'
            )
    """).fetchall()

    conn.close()

    return list(rows)


def get_control_miners():
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM miners
        WHERE
            enabled=1
            AND driver IN (
                'awesome',
                'bitmain_stock'
            )
    """).fetchall()

    conn.close()

    return list(rows)
