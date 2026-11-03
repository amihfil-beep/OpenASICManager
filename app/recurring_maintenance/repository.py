"""SQLite persistence for recurring maintenance."""

from db import db


class RecurringTargetNotFoundError(LookupError):
    pass


RULE_SELECT = """
    SELECT
        r.*,
        m.name AS miner_name,
        m.ip AS miner_ip,
        g.name AS group_name
    FROM recurring_maintenance_rules r
    LEFT JOIN miners m
        ON m.id=r.miner_id
    LEFT JOIN miner_groups g
        ON g.id=r.group_id
"""


def ensure_recurring_schema():
    conn = db()

    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recurring_maintenance_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                enabled INTEGER NOT NULL DEFAULT 1,

                scope TEXT NOT NULL,
                miner_id INTEGER,
                group_id INTEGER,

                days_mask INTEGER NOT NULL,
                start_minutes INTEGER NOT NULL,
                duration_minutes INTEGER NOT NULL,

                note TEXT,

                created_by TEXT NOT NULL,
                created_at INTEGER NOT NULL,

                updated_by TEXT,
                updated_at INTEGER,

                deleted_by TEXT,
                deleted_at INTEGER,

                CHECK (enabled IN (0, 1)),
                CHECK (scope IN ('FARM', 'MINER', 'GROUP')),
                CHECK (days_mask BETWEEN 1 AND 127),
                CHECK (start_minutes BETWEEN 0 AND 1439),
                CHECK (duration_minutes BETWEEN 1 AND 10080),
                CHECK (
                    (scope='FARM' AND miner_id IS NULL AND group_id IS NULL)
                    OR
                    (scope='MINER' AND miner_id IS NOT NULL AND group_id IS NULL)
                    OR
                    (scope='GROUP' AND miner_id IS NULL AND group_id IS NOT NULL)
                )
            );

            CREATE INDEX IF NOT EXISTS idx_recurring_maintenance_enabled
            ON recurring_maintenance_rules(enabled, deleted_at, id);

            CREATE TABLE IF NOT EXISTS recurring_maintenance_occurrences (
                rule_id INTEGER NOT NULL,
                occurrence_key TEXT NOT NULL,
                window_id INTEGER NOT NULL,
                starts_at INTEGER NOT NULL,
                ends_at INTEGER NOT NULL,
                materialized_at INTEGER NOT NULL,

                PRIMARY KEY (rule_id, occurrence_key)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_recurring_occurrence_window
            ON recurring_maintenance_occurrences(window_id);
        """)
        conn.commit()
    finally:
        conn.close()


def _validate_target(conn, normalized):
    scope = normalized["scope"]

    if scope == "MINER":
        row = conn.execute(
            "SELECT id FROM miners WHERE id=?",
            (int(normalized["miner_id"]),),
        ).fetchone()

        if row is None:
            raise RecurringTargetNotFoundError("Miner not found")

    elif scope == "GROUP":
        row = conn.execute(
            "SELECT id FROM miner_groups WHERE id=?",
            (int(normalized["group_id"]),),
        ).fetchone()

        if row is None:
            raise RecurringTargetNotFoundError("Group not found")


def get_recurring_rule(rule_id, include_deleted=False):
    ensure_recurring_schema()
    conn = db()

    where = "WHERE r.id=?"
    if not include_deleted:
        where += " AND r.deleted_at IS NULL"

    try:
        return conn.execute(
            RULE_SELECT + where,
            (int(rule_id),),
        ).fetchone()
    finally:
        conn.close()


def list_recurring_rules(include_deleted=False):
    ensure_recurring_schema()
    conn = db()

    where = ""
    if not include_deleted:
        where = "WHERE r.deleted_at IS NULL"

    try:
        return list(
            conn.execute(
                RULE_SELECT
                + where
                + " ORDER BY r.id"
            ).fetchall()
        )
    finally:
        conn.close()


def list_enabled_recurring_rules():
    ensure_recurring_schema()
    conn = db()

    try:
        return list(
            conn.execute(
                RULE_SELECT
                + """
                WHERE
                    r.enabled=1
                    AND r.deleted_at IS NULL
                ORDER BY r.id
                """
            ).fetchall()
        )
    finally:
        conn.close()


def create_recurring_rule(normalized, actor, now):
    ensure_recurring_schema()
    conn = db()

    try:
        conn.execute("BEGIN IMMEDIATE")
        _validate_target(conn, normalized)

        cursor = conn.execute("""
            INSERT INTO recurring_maintenance_rules
            (
                enabled,
                scope,
                miner_id,
                group_id,
                days_mask,
                start_minutes,
                duration_minutes,
                note,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            1 if normalized["enabled"] else 0,
            normalized["scope"],
            normalized["miner_id"],
            normalized["group_id"],
            normalized["days_mask"],
            normalized["start_minutes"],
            normalized["duration_minutes"],
            normalized["note"],
            actor,
            int(now),
        ))

        rule_id = cursor.lastrowid

        row = conn.execute(
            RULE_SELECT + " WHERE r.id=?",
            (int(rule_id),),
        ).fetchone()

        conn.commit()
        return row

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_recurring_rule(rule_id, normalized, actor, now):
    ensure_recurring_schema()
    conn = db()

    try:
        conn.execute("BEGIN IMMEDIATE")
        _validate_target(conn, normalized)

        cursor = conn.execute("""
            UPDATE recurring_maintenance_rules
            SET
                enabled=?,
                scope=?,
                miner_id=?,
                group_id=?,
                days_mask=?,
                start_minutes=?,
                duration_minutes=?,
                note=?,
                updated_by=?,
                updated_at=?
            WHERE
                id=?
                AND deleted_at IS NULL
        """, (
            1 if normalized["enabled"] else 0,
            normalized["scope"],
            normalized["miner_id"],
            normalized["group_id"],
            normalized["days_mask"],
            normalized["start_minutes"],
            normalized["duration_minutes"],
            normalized["note"],
            actor,
            int(now),
            int(rule_id),
        ))

        if cursor.rowcount == 0:
            conn.rollback()
            return None

        row = conn.execute(
            RULE_SELECT + " WHERE r.id=?",
            (int(rule_id),),
        ).fetchone()

        conn.commit()
        return row

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_recurring_rule(rule_id, actor, now):
    ensure_recurring_schema()
    conn = db()

    try:
        cursor = conn.execute("""
            UPDATE recurring_maintenance_rules
            SET
                enabled=0,
                deleted_by=?,
                deleted_at=?,
                updated_by=?,
                updated_at=?
            WHERE
                id=?
                AND deleted_at IS NULL
        """, (
            actor,
            int(now),
            actor,
            int(now),
            int(rule_id),
        ))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def _current_rule_for_materialization(conn, rule_id):
    return conn.execute(
        RULE_SELECT
        + """
        WHERE
            r.id=?
            AND r.enabled=1
            AND r.deleted_at IS NULL
        """,
        (int(rule_id),),
    ).fetchone()


def materialize_occurrence(
    rule_id,
    occurrence_key,
    starts_at,
    ends_at,
    now,
):
    """Atomically create one concrete maintenance window if absent."""

    ensure_recurring_schema()
    conn = db()

    try:
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute("""
            SELECT window_id
            FROM recurring_maintenance_occurrences
            WHERE rule_id=? AND occurrence_key=?
        """, (
            int(rule_id),
            str(occurrence_key),
        )).fetchone()

        if existing is not None:
            conn.commit()
            return None

        rule = _current_rule_for_materialization(
            conn,
            rule_id,
        )

        if rule is None:
            conn.commit()
            return None

        scope = rule["scope"]
        miner_id = rule["miner_id"]
        group_id = rule["group_id"]
        group_name = None
        member_rows = []

        if scope == "MINER":
            miner = conn.execute(
                "SELECT id FROM miners WHERE id=?",
                (int(miner_id),),
            ).fetchone()

            if miner is None:
                raise RecurringTargetNotFoundError("Miner not found")

        elif scope == "GROUP":
            group = conn.execute("""
                SELECT id, name
                FROM miner_groups
                WHERE id=?
            """, (
                int(group_id),
            )).fetchone()

            if group is None:
                raise RecurringTargetNotFoundError("Group not found")

            group_name = group["name"]
            member_rows = list(
                conn.execute("""
                    SELECT id, name, ip
                    FROM miners
                    WHERE group_id=?
                    ORDER BY id
                """, (
                    int(group_id),
                )).fetchall()
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
            miner_id,
            group_id,
            group_name,
            int(starts_at),
            int(ends_at),
            rule["note"],
            "RECURRING",
            int(now),
        ))

        window_id = int(cursor.lastrowid)

        if scope == "GROUP" and member_rows:
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
                    window_id,
                    int(row["id"]),
                    row["name"],
                    row["ip"],
                )
                for row in member_rows
            ])

        conn.execute("""
            INSERT INTO recurring_maintenance_occurrences
            (
                rule_id,
                occurrence_key,
                window_id,
                starts_at,
                ends_at,
                materialized_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            int(rule_id),
            str(occurrence_key),
            window_id,
            int(starts_at),
            int(ends_at),
            int(now),
        ))

        conn.commit()

        return {
            "rule_id": int(rule_id),
            "occurrence_key": str(occurrence_key),
            "window_id": window_id,
            "starts_at": int(starts_at),
            "ends_at": int(ends_at),
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = (
    "RecurringTargetNotFoundError",
    "ensure_recurring_schema",
    "get_recurring_rule",
    "list_recurring_rules",
    "list_enabled_recurring_rules",
    "create_recurring_rule",
    "update_recurring_rule",
    "delete_recurring_rule",
    "materialize_occurrence",
)
