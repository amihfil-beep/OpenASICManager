"""
Telemetry repository.

Stores periodic miner telemetry snapshots and applies
telemetry retention policy.

This module does not start background threads and does not
expose HTTP endpoints.
"""

import time

from db import db


TELEMETRY_RETENTION_DAYS = 90


__all__ = (
    "TELEMETRY_RETENTION_DAYS",
    "save_telemetry_snapshot",
    "telemetry_stats_row",
    "telemetry_history_rows",
)


def save_telemetry_snapshot():

    now = int(
        time.time()
    )

    conn = db()

    miners = conn.execute("""
        SELECT
            id,
            ip,
            name,
            driver,
            enabled,
            last_state,
            hashrate,
            avg_hashrate,
            temp,
            power

        FROM miners

        WHERE
            enabled=1
            AND driver IN (
                'awesome',
                'bitmain_stock'
            )
    """).fetchall()


    rows = []

    for miner in miners:

        rows.append((
            now,
            miner["id"],
            miner["ip"],
            miner["name"],
            miner["driver"],
            miner["last_state"],
            miner["hashrate"],
            miner["avg_hashrate"],
            miner["temp"],
            miner["power"],
        ))


    if rows:

        conn.executemany("""
            INSERT INTO telemetry
            (
                ts,
                miner_id,
                ip,
                name,
                driver,
                state,
                hashrate,
                avg_hashrate,
                temp,
                power
            )

            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, rows)


    retention_limit = (
        now
        -
        TELEMETRY_RETENTION_DAYS
        * 86400
    )


    conn.execute("""
        DELETE FROM telemetry
        WHERE ts < ?
    """, (
        retention_limit,
    ))


    conn.commit()
    conn.close()


def telemetry_stats_row():

    conn = db()

    row = conn.execute("""
        SELECT
            COUNT(*) AS rows,
            MIN(ts) AS oldest,
            MAX(ts) AS newest

        FROM telemetry
    """).fetchone()

    conn.close()

    return row


def telemetry_history_rows(
    miner_id,
    since,
):

    conn = db()

    rows = conn.execute("""
        SELECT
            ts,
            state,
            hashrate,
            avg_hashrate,
            temp,
            power

        FROM telemetry

        WHERE
            miner_id=?
            AND ts>=?

        ORDER BY ts ASC
    """, (
        miner_id,
        since,
    )).fetchall()

    conn.close()

    return rows
