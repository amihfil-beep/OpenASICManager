"""SQLite reads for the maintenance operations calendar."""

from db import db
from recurring_maintenance.repository import ensure_recurring_schema


CALENDAR_SELECT = """
    SELECT
        mw.*,
        m.name AS miner_name,
        m.ip AS miner_ip,

        rmo.rule_id AS recurring_rule_id,
        rmo.occurrence_key AS recurring_occurrence_key,

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

    LEFT JOIN recurring_maintenance_occurrences rmo
        ON rmo.window_id=mw.id
"""


def list_calendar_windows(
    starts_before,
    relevant_after,
    scope=None,
    miner_id=None,
    limit=200,
):
    ensure_recurring_schema()

    limit = max(
        1,
        min(int(limit), 500),
    )

    where = [
        "mw.starts_at < ?",
        "(mw.ends_at >= ? OR mw.ended_at >= ?)",
    ]

    params = [
        int(starts_before),
        int(relevant_after),
        int(relevant_after),
    ]

    if scope is not None:
        where.append("mw.scope = ?")
        params.append(str(scope))

    if miner_id is not None:
        where.append("""
            (
                mw.scope='FARM'
                OR
                (
                    mw.scope='MINER'
                    AND mw.miner_id=?
                )
                OR
                (
                    mw.scope='GROUP'
                    AND EXISTS (
                        SELECT 1
                        FROM maintenance_window_members mwm_filter
                        WHERE
                            mwm_filter.window_id=mw.id
                            AND mwm_filter.miner_id=?
                    )
                )
            )
        """)
        params.extend([
            int(miner_id),
            int(miner_id),
        ])

    params.append(limit)

    conn = db()

    try:
        return list(
            conn.execute(
                CALENDAR_SELECT
                + " WHERE "
                + " AND ".join(where)
                + """
                ORDER BY
                    mw.starts_at ASC,
                    mw.id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        )
    finally:
        conn.close()


def current_group_member_ids(group_id):
    conn = db()

    try:
        return [
            int(row["id"])
            for row in conn.execute("""
                SELECT id
                FROM miners
                WHERE group_id=?
                ORDER BY id
            """, (
                int(group_id),
            )).fetchall()
        ]
    finally:
        conn.close()


__all__ = (
    "list_calendar_windows",
    "current_group_member_ids",
)
