"""Durable lifecycle state for recurring maintenance occurrences."""

from db import db
from recurring_maintenance.repository import ensure_recurring_schema


LIFECYCLE_SELECT = """
    SELECT
        o.rule_id,
        o.occurrence_key,
        o.window_id,
        o.starts_at,
        o.ends_at,
        o.materialized_at,

        mw.scope,
        mw.miner_id,
        m.name AS miner_name,
        m.ip AS miner_ip,
        mw.group_id,
        mw.group_name,
        mw.note,
        mw.ended_at,
        mw.ended_by,

        l.started_event_at,
        l.completed_event_at,
        l.cancelled_event_at,

        r.deleted_at AS rule_deleted_at

    FROM recurring_maintenance_occurrences o

    JOIN maintenance_windows mw
        ON mw.id=o.window_id

    LEFT JOIN miners m
        ON m.id=mw.miner_id

    LEFT JOIN recurring_maintenance_lifecycle l
        ON
            l.rule_id=o.rule_id
            AND l.occurrence_key=o.occurrence_key

    LEFT JOIN recurring_maintenance_rules r
        ON r.id=o.rule_id
"""


def ensure_lifecycle_schema():
    ensure_recurring_schema()
    conn = db()

    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recurring_maintenance_lifecycle (
                rule_id INTEGER NOT NULL,
                occurrence_key TEXT NOT NULL,

                started_event_at INTEGER,
                completed_event_at INTEGER,
                cancelled_event_at INTEGER,

                PRIMARY KEY (
                    rule_id,
                    occurrence_key
                )
            );

            CREATE INDEX IF NOT EXISTS idx_recurring_lifecycle_events
            ON recurring_maintenance_lifecycle(
                started_event_at,
                completed_event_at,
                cancelled_event_at
            );
        """)

        conn.execute("""
            INSERT OR IGNORE INTO recurring_maintenance_lifecycle
            (
                rule_id,
                occurrence_key
            )
            SELECT
                rule_id,
                occurrence_key
            FROM recurring_maintenance_occurrences
        """)

        conn.commit()

    finally:
        conn.close()


def _event_item(row, action):
    return {
        "action": action,
        "rule_id": int(row["rule_id"]),
        "occurrence_key": str(row["occurrence_key"]),
        "window_id": int(row["window_id"]),
        "scope": str(row["scope"]),
        "miner_id": (
            int(row["miner_id"])
            if row["miner_id"] is not None
            else None
        ),
        "miner_name": row["miner_name"],
        "miner_ip": row["miner_ip"],
        "group_id": (
            int(row["group_id"])
            if row["group_id"] is not None
            else None
        ),
        "group_name": row["group_name"],
        "starts_at": int(row["starts_at"]),
        "ends_at": int(row["ends_at"]),
        "ended_at": row["ended_at"],
        "note": row["note"],
    }


def claim_lifecycle_events(now):
    """Atomically claim lifecycle transitions so repeated scans are idempotent."""

    ensure_lifecycle_schema()
    conn = db()
    now = int(now)
    events = []

    try:
        conn.execute("BEGIN IMMEDIATE")

        conn.execute("""
            INSERT OR IGNORE INTO recurring_maintenance_lifecycle
            (
                rule_id,
                occurrence_key
            )
            SELECT
                rule_id,
                occurrence_key
            FROM recurring_maintenance_occurrences
        """)

        rows = conn.execute(
            LIFECYCLE_SELECT
            + " ORDER BY o.starts_at, o.rule_id"
        ).fetchall()

        for row in rows:
            starts_at = int(row["starts_at"])
            ends_at = int(row["ends_at"])
            ended_at = row["ended_at"]

            cancelled = (
                ended_at is not None
                and int(ended_at) < ends_at
            )

            completed = (
                not cancelled
                and ends_at <= now
            )

            active = (
                not cancelled
                and ended_at is None
                and starts_at <= now < ends_at
            )

            if cancelled:
                if row["cancelled_event_at"] is None:
                    conn.execute("""
                        UPDATE recurring_maintenance_lifecycle
                        SET
                            started_event_at=COALESCE(
                                started_event_at,
                                ?
                            ),
                            cancelled_event_at=?
                        WHERE
                            rule_id=?
                            AND occurrence_key=?
                            AND cancelled_event_at IS NULL
                    """, (
                        starts_at,
                        now,
                        int(row["rule_id"]),
                        str(row["occurrence_key"]),
                    ))
                    events.append(
                        _event_item(
                            row,
                            "MAINTENANCE_RECURRING_CANCEL",
                        )
                    )
                continue

            if completed:
                if row["completed_event_at"] is None:
                    conn.execute("""
                        UPDATE recurring_maintenance_lifecycle
                        SET
                            started_event_at=COALESCE(
                                started_event_at,
                                ?
                            ),
                            completed_event_at=?
                        WHERE
                            rule_id=?
                            AND occurrence_key=?
                            AND completed_event_at IS NULL
                    """, (
                        starts_at,
                        now,
                        int(row["rule_id"]),
                        str(row["occurrence_key"]),
                    ))
                    events.append(
                        _event_item(
                            row,
                            "MAINTENANCE_RECURRING_COMPLETE",
                        )
                    )
                continue

            if active and row["started_event_at"] is None:
                conn.execute("""
                    UPDATE recurring_maintenance_lifecycle
                    SET started_event_at=?
                    WHERE
                        rule_id=?
                        AND occurrence_key=?
                        AND started_event_at IS NULL
                """, (
                    now,
                    int(row["rule_id"]),
                    str(row["occurrence_key"]),
                ))
                events.append(
                    _event_item(
                        row,
                        "MAINTENANCE_RECURRING_START",
                    )
                )

        conn.commit()
        return events

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def list_occurrence_history(limit=200):
    ensure_lifecycle_schema()

    limit = max(
        1,
        min(int(limit), 500),
    )

    conn = db()

    try:
        return list(
            conn.execute(
                LIFECYCLE_SELECT
                + """
                ORDER BY
                    o.starts_at DESC,
                    o.rule_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )

    finally:
        conn.close()


__all__ = (
    "ensure_lifecycle_schema",
    "claim_lifecycle_events",
    "list_occurrence_history",
)
