"""SQLite persistence for maintenance windows."""

from db import db


WINDOW_SELECT = """
    SELECT
        mw.*,
        m.name AS miner_name,
        m.ip AS miner_ip
    FROM maintenance_windows mw
    LEFT JOIN miners m
        ON m.id = mw.miner_id
"""


def get_maintenance_window(
    window_id,
):
    conn = db()

    try:
        return conn.execute(
            WINDOW_SELECT
            + """
            WHERE mw.id=?
            """,
            (
                int(window_id),
            ),
        ).fetchone()

    finally:
        conn.close()


def list_maintenance_windows(
    limit=100,
):
    limit = max(
        1,
        min(
            int(limit),
            500,
        ),
    )

    conn = db()

    try:
        rows = conn.execute(
            WINDOW_SELECT
            + """
            ORDER BY
                mw.starts_at DESC,
                mw.id DESC
            LIMIT ?
            """,
            (
                limit,
            ),
        ).fetchall()

        return list(rows)

    finally:
        conn.close()


def find_maintenance_conflict(
    scope,
    miner_id,
    starts_at,
    ends_at,
    exclude_id=None,
):
    conn = db()

    where = [
        "ended_at IS NULL",
        "starts_at < ?",
        "ends_at > ?",
        "scope = ?",
    ]

    params = [
        int(ends_at),
        int(starts_at),
        str(scope),
    ]

    if scope == "MINER":
        where.append(
            "miner_id = ?"
        )
        params.append(
            int(miner_id)
        )
    else:
        where.append(
            "miner_id IS NULL"
        )

    if exclude_id is not None:
        where.append(
            "id != ?"
        )
        params.append(
            int(exclude_id)
        )

    sql = """
        SELECT *
        FROM maintenance_windows
        WHERE
    """ + " AND ".join(where) + """
        ORDER BY id
        LIMIT 1
    """

    try:
        return conn.execute(
            sql,
            params,
        ).fetchone()

    finally:
        conn.close()


def create_maintenance_window(
    normalized,
    actor,
    now,
):
    conn = db()

    try:
        cursor = conn.execute("""
            INSERT INTO maintenance_windows
            (
                scope,
                miner_id,
                starts_at,
                ends_at,
                note,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            normalized["scope"],
            normalized["miner_id"],
            normalized["starts_at"],
            normalized["ends_at"],
            normalized["note"],
            actor,
            int(now),
        ))

        window_id = cursor.lastrowid

        row = conn.execute(
            WINDOW_SELECT
            + """
            WHERE mw.id=?
            """,
            (
                window_id,
            ),
        ).fetchone()

        conn.commit()
        return row

    finally:
        conn.close()


def extend_maintenance_window(
    window_id,
    normalized,
    actor,
    now,
):
    conn = db()

    try:
        cursor = conn.execute("""
            UPDATE maintenance_windows
            SET
                ends_at=?,
                note=?,
                updated_by=?,
                updated_at=?
            WHERE
                id=?
                AND ended_at IS NULL
        """, (
            normalized["ends_at"],
            normalized["note"],
            actor,
            int(now),
            int(window_id),
        ))

        if cursor.rowcount == 0:
            conn.rollback()
            return None

        row = conn.execute(
            WINDOW_SELECT
            + """
            WHERE mw.id=?
            """,
            (
                int(window_id),
            ),
        ).fetchone()

        conn.commit()
        return row

    finally:
        conn.close()


def end_maintenance_window(
    window_id,
    actor,
    now,
):
    conn = db()

    try:
        cursor = conn.execute("""
            UPDATE maintenance_windows
            SET
                ended_at=?,
                ended_by=?,
                updated_by=?,
                updated_at=?
            WHERE
                id=?
                AND ended_at IS NULL
        """, (
            int(now),
            actor,
            actor,
            int(now),
            int(window_id),
        ))

        if cursor.rowcount == 0:
            conn.rollback()
            return None

        row = conn.execute(
            WINDOW_SELECT
            + """
            WHERE mw.id=?
            """,
            (
                int(window_id),
            ),
        ).fetchone()

        conn.commit()
        return row

    finally:
        conn.close()


def active_maintenance_snapshot(
    now,
):
    conn = db()

    try:
        rows = conn.execute("""
            SELECT
                scope,
                miner_id
            FROM maintenance_windows
            WHERE
                ended_at IS NULL
                AND starts_at <= ?
                AND ends_at > ?
        """, (
            int(now),
            int(now),
        )).fetchall()

    finally:
        conn.close()

    farm_active = any(
        row["scope"] == "FARM"
        for row in rows
    )

    miner_ids = {
        int(row["miner_id"])
        for row in rows
        if (
            row["scope"] == "MINER"
            and row["miner_id"] is not None
        )
    }

    return {
        "farm_active": farm_active,
        "miner_ids": miner_ids,
    }


__all__ = (
    "get_maintenance_window",
    "list_maintenance_windows",
    "find_maintenance_conflict",
    "create_maintenance_window",
    "extend_maintenance_window",
    "end_maintenance_window",
    "active_maintenance_snapshot",
)
