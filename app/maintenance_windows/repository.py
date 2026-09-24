"""SQLite persistence for maintenance windows."""

from db import db


class MaintenanceGroupNotFoundError(
    LookupError
):
    pass


class MaintenanceEmptyGroupError(
    ValueError
):
    pass


WINDOW_SELECT = """
    SELECT
        mw.*,

        m.name AS miner_name,
        m.ip AS miner_ip,

        m.group_id AS miner_group_id,
        mg.name AS miner_group_name,

        (
            SELECT COUNT(*)
            FROM maintenance_window_members mwm
            WHERE mwm.window_id=mw.id
        ) AS member_count,

        (
            SELECT GROUP_CONCAT(
                snapshot.miner_id,
                ','
            )

            FROM (
                SELECT miner_id
                FROM maintenance_window_members

                WHERE window_id=mw.id

                ORDER BY miner_id
            ) snapshot
        ) AS member_ids_csv

    FROM maintenance_windows mw

    LEFT JOIN miners m
        ON m.id=mw.miner_id

    LEFT JOIN miner_groups mg
        ON mg.id=m.group_id
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


def maintenance_window_member_rows(
    window_id,
):
    conn = db()

    try:
        return list(
            conn.execute("""
                SELECT
                    window_id,
                    miner_id,
                    miner_name,
                    miner_ip

                FROM maintenance_window_members

                WHERE window_id=?

                ORDER BY miner_id
            """, (
                int(window_id),
            )).fetchall()
        )

    finally:
        conn.close()


def find_maintenance_conflict(
    scope,
    miner_id,
    starts_at,
    ends_at,
    exclude_id=None,
    group_id=None,
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


    elif scope == "GROUP":

        where.append(
            "group_id = ?"
        )

        params.append(
            int(group_id)
        )


    else:

        where.append(
            "miner_id IS NULL"
        )

        where.append(
            "group_id IS NULL"
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
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        scope = (
            normalized["scope"]
        )

        group_id = (
            normalized.get(
                "group_id"
            )
        )

        group_name = None
        member_rows = []


        if scope == "GROUP":

            group = conn.execute("""
                SELECT
                    id,
                    name

                FROM miner_groups

                WHERE id=?
            """, (
                int(group_id),
            )).fetchone()


            if group is None:
                raise MaintenanceGroupNotFoundError(
                    "Group not found"
                )


            member_rows = list(
                conn.execute("""
                    SELECT
                        id,
                        name,
                        ip

                    FROM miners

                    WHERE group_id=?

                    ORDER BY id
                """, (
                    int(group_id),
                )).fetchall()
            )


            if not member_rows:
                raise MaintenanceEmptyGroupError(
                    "GROUP maintenance requires "
                    "at least one current member"
                )


            group_name = (
                group["name"]
            )


        cursor = conn.execute("""
            INSERT INTO maintenance_windows
            (
                scope,
                miner_id,
                group_id,
                group_name,
                starts_at,
                ends_at,
                note,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scope,
            normalized["miner_id"],
            (
                int(group_id)
                if group_id is not None
                else None
            ),
            group_name,
            normalized["starts_at"],
            normalized["ends_at"],
            normalized["note"],
            actor,
            int(now),
        ))

        window_id = (
            cursor.lastrowid
        )


        if scope == "GROUP":

            conn.executemany("""
                INSERT INTO maintenance_window_members
                (
                    window_id,
                    miner_id,
                    miner_name,
                    miner_ip
                )
                VALUES (?, ?, ?, ?)
            """, [
                (
                    int(window_id),
                    int(row["id"]),
                    row["name"],
                    row["ip"],
                )
                for row in member_rows
            ])


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


    except Exception:

        conn.rollback()
        raise


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
                id,
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


        group_member_rows = conn.execute("""
            SELECT DISTINCT
                mwm.miner_id

            FROM maintenance_window_members mwm

            JOIN maintenance_windows mw
                ON mw.id=mwm.window_id

            WHERE
                mw.scope='GROUP'
                AND mw.ended_at IS NULL
                AND mw.starts_at <= ?
                AND mw.ends_at > ?
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
            and
            row["miner_id"] is not None
        )
    }


    miner_ids.update(
        int(row["miner_id"])
        for row in group_member_rows
    )


    return {
        "farm_active":
            farm_active,

        "miner_ids":
            miner_ids,
    }


__all__ = (
    "MaintenanceGroupNotFoundError",
    "MaintenanceEmptyGroupError",
    "get_maintenance_window",
    "list_maintenance_windows",
    "maintenance_window_member_rows",
    "find_maintenance_conflict",
    "create_maintenance_window",
    "extend_maintenance_window",
    "end_maintenance_window",
    "active_maintenance_snapshot",
)
