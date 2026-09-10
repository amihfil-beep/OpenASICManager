"""Persistence helpers used by Telegram notification formatting."""

from db import db


__all__ = (
    "find_issue_context",
    "list_farm_summary_rows",
)


def find_issue_context(
    miner_id,
    action,
):
    conn = db()
    try:
        schema = conn.execute(
            "PRAGMA table_info(issues)"
        ).fetchall()

        columns = {
            row["name"]
            for row in schema
        }

        if (
            not columns
            or
            "miner_id" not in columns
        ):
            return {}

        where = [
            "miner_id=?"
        ]
        params = [
            miner_id
        ]

        if "status" in columns:
            if action == "ISSUE_OPEN":
                where.append(
                    "status='ACTIVE'"
                )
            elif action == "ISSUE_RESOLVED":
                where.append(
                    "status='RESOLVED'"
                )

        order_column = "id"
        for candidate in (
            "resolved_at",
            "closed_at",
            "last_seen",
            "opened_at",
            "created_at",
            "id",
        ):
            if candidate in columns:
                order_column = candidate
                break

        sql = (
            "SELECT * "
            "FROM issues "
            "WHERE "
            + " AND ".join(where)
            + f" ORDER BY {order_column} DESC "
            "LIMIT 1"
        )

        row = conn.execute(
            sql,
            params,
        ).fetchone()

        # Some older issue schemas may use another status value.
        # Fall back to the newest issue for the miner.
        if row is None:
            row = conn.execute(
                """
                SELECT *
                FROM issues
                WHERE miner_id=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    miner_id,
                ),
            ).fetchone()

        if row is None:
            return {}

        return dict(row)
    finally:
        conn.close()


def list_farm_summary_rows():
    conn = db()
    try:
        miners = conn.execute(
            """
            SELECT
                id,
                name,
                ip,
                driver,
                enabled,
                last_state,
                hashrate,
                avg_hashrate,
                temp,
                power,
                last_seen
            FROM miners
            WHERE enabled=1
            ORDER BY ip
            """
        ).fetchall()

        issues = conn.execute(
            """
            SELECT
                i.id,
                i.miner_id,
                i.code,
                m.name,
                m.ip
            FROM issues i

            LEFT JOIN miners m
                ON m.id=i.miner_id

            WHERE i.status='ACTIVE'

            ORDER BY i.id DESC
            """
        ).fetchall()

        return miners, issues
    finally:
        conn.close()
