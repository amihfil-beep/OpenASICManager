"""Persistence helpers for ASIC physical service history."""

from db import db


SERVICE_WORK_TYPES = (
    "THERMAL_SERVICE",
    "CLEANING",
    "FAN_REPLACEMENT",
    "HASHBOARD_REPAIR",
    "HASHBOARD_REPLACEMENT",
    "CONTROL_BOARD_REPLACEMENT",
    "PSU_REPLACEMENT",
    "OTHER",
)

MAX_SERVICE_HISTORY_LIMIT = 200


__all__ = (
    "SERVICE_WORK_TYPES",
    "create_service_record",
    "list_service_records",
    "latest_service_record",
    "latest_service_records_by_miner",
)


def create_service_record(
    miner_id,
    performed_at,
    work_type,
    description,
    part_serial,
    performed_by,
    created_by,
    created_at,
):
    conn = db()

    try:
        cursor = conn.execute(
            """
            INSERT INTO miner_service_records
            (
                miner_id,
                performed_at,
                work_type,
                description,
                part_serial,
                performed_by,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(miner_id),
                int(performed_at),
                str(work_type),
                str(description or ""),
                (
                    str(part_serial).strip()
                    if part_serial
                    else None
                ),
                str(performed_by or ""),
                str(created_by),
                int(created_at),
            ),
        )

        record_id = cursor.lastrowid

        conn.commit()

        return conn.execute(
            """
            SELECT *
            FROM miner_service_records
            WHERE id=?
            """,
            (
                record_id,
            ),
        ).fetchone()

    finally:
        conn.close()


def list_service_records(
    miner_id,
    limit=100,
):
    limit = max(
        1,
        min(
            int(limit),
            MAX_SERVICE_HISTORY_LIMIT,
        ),
    )

    conn = db()

    try:
        return list(
            conn.execute(
                """
                SELECT *
                FROM miner_service_records

                WHERE miner_id=?

                ORDER BY
                    performed_at DESC,
                    id DESC

                LIMIT ?
                """,
                (
                    int(miner_id),
                    limit,
                ),
            ).fetchall()
        )

    finally:
        conn.close()




def latest_service_records_by_miner():
    """Return newest service record for every miner."""

    conn = db()

    try:
        rows = conn.execute(
            """
            SELECT r.*

            FROM miner_service_records r

            WHERE NOT EXISTS (
                SELECT 1

                FROM miner_service_records newer

                WHERE
                    newer.miner_id=r.miner_id
                    AND
                    (
                        newer.performed_at
                            >
                        r.performed_at

                        OR

                        (
                            newer.performed_at
                                =
                            r.performed_at
                            AND
                            newer.id
                                >
                            r.id
                        )
                    )
            )
            """
        ).fetchall()

        return {
            int(row["miner_id"]): row
            for row in rows
        }

    finally:
        conn.close()


def latest_service_record(miner_id):
    conn = db()

    try:
        return conn.execute(
            """
            SELECT *
            FROM miner_service_records

            WHERE miner_id=?

            ORDER BY
                performed_at DESC,
                id DESC

            LIMIT 1
            """,
            (
                int(miner_id),
            ),
        ).fetchone()

    finally:
        conn.close()
