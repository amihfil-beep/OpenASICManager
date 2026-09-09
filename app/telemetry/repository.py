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
    "farm_history_rows",
    "farm_problem_rows",
    "farm_current_rows",
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


def farm_history_rows(
    since,
    bucket_seconds,
):

    conn = db()

    rows = conn.execute("""
        WITH snapshots AS
        (
            SELECT
                ts,

                SUM(
                    CASE
                        WHEN state='MINING'
                        THEN 1
                        ELSE 0
                    END
                ) AS mining_count,

                SUM(
                    CASE
                        WHEN state='PAUSED'
                        THEN 1
                        ELSE 0
                    END
                ) AS paused_count,

                SUM(
                    CASE
                        WHEN state='STARTING'
                        THEN 1
                        ELSE 0
                    END
                ) AS starting_count,

                SUM(
                    CASE
                        WHEN state='OFFLINE'
                        THEN 1
                        ELSE 0
                    END
                ) AS offline_count,

                COUNT(*) AS total_count,

                SUM(
                    COALESCE(
                        hashrate,
                        0
                    )
                ) AS total_hashrate,

                SUM(
                    COALESCE(
                        power,
                        0
                    )
                ) AS known_power,

                MAX(temp) AS max_temp,

                AVG(
                    CASE
                        WHEN temp IS NOT NULL
                        THEN temp
                    END
                ) AS avg_temp

            FROM telemetry

            WHERE ts >= ?

            GROUP BY ts
        )

        SELECT
            (
                CAST(
                    ts / ?
                    AS INTEGER
                )
                * ?
            ) AS bucket_ts,

            AVG(
                mining_count
            ) AS mining_count,

            AVG(
                paused_count
            ) AS paused_count,

            AVG(
                starting_count
            ) AS starting_count,

            AVG(
                offline_count
            ) AS offline_count,

            AVG(
                total_count
            ) AS total_count,

            AVG(
                total_hashrate
            ) AS total_hashrate,

            AVG(
                known_power
            ) AS known_power,

            MAX(
                max_temp
            ) AS max_temp,

            AVG(
                avg_temp
            ) AS avg_temp

        FROM snapshots

        GROUP BY bucket_ts

        ORDER BY bucket_ts ASC
    """, (
        since,
        bucket_seconds,
        bucket_seconds,
    )).fetchall()

    conn.close()

    return rows


def farm_problem_rows(
    since,
):

    conn = db()

    rows = conn.execute("""
        SELECT
            miner_id,
            ip,
            name,
            driver,

            COUNT(*) AS samples,

            SUM(
                CASE
                    WHEN state='OFFLINE'
                    THEN 1
                    ELSE 0
                END
            ) AS offline_samples,

            SUM(
                CASE
                    WHEN temp >= 85
                    THEN 1
                    ELSE 0
                END
            ) AS critical_temp_samples,

            MAX(temp) AS max_temp,

            AVG(
                CASE
                    WHEN state='MINING'
                    AND hashrate IS NOT NULL

                    THEN hashrate
                END
            ) AS avg_mining_hashrate

        FROM telemetry

        WHERE ts >= ?

        GROUP BY
            miner_id,
            ip,
            name,
            driver

        HAVING
            SUM(
                CASE
                    WHEN state='OFFLINE'
                    THEN 1
                    ELSE 0
                END
            ) > 0

            OR

            SUM(
                CASE
                    WHEN temp >= 85
                    THEN 1
                    ELSE 0
                END
            ) > 0

        ORDER BY
            offline_samples DESC,
            critical_temp_samples DESC,
            max_temp DESC
    """, (
        since,
    )).fetchall()

    conn.close()

    return rows


def farm_current_rows():

    conn = db()

    rows = conn.execute("""
        SELECT
            last_state AS state,
            hashrate,
            power,
            temp

        FROM miners

        WHERE
            enabled=1
            AND driver IN (
                'awesome',
                'bitmain_stock'
            )
    """).fetchall()

    conn.close()

    return rows

