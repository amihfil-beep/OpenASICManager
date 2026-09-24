"""Persistence helpers used by Telegram notification formatting."""

from db import db

from notifications.policy import (
    DEFAULT_NOTIFICATION_POLICY,
    NOTIFICATION_POLICY_FIELDS,
    normalize_notification_policy,
)


NOTIFICATION_POLICY_SETTING_KEYS = {
    field:
        f"telegram.policy.{field}"

    for field
    in NOTIFICATION_POLICY_FIELDS
}


__all__ = (
    "load_notification_policy",
    "save_notification_policy",
    "find_issue_context",
    "list_farm_summary_rows",
)


def _stored_bool(value):
    value = str(
        value
    ).strip().lower()

    if value in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True

    if value in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False

    raise ValueError(
        "Invalid stored boolean"
    )


def load_notification_policy():
    conn = db()

    try:
        rows = conn.execute("""
            SELECT
                key,
                value
            FROM settings
            WHERE key LIKE 'telegram.policy.%'
        """).fetchall()

    finally:
        conn.close()

    values = dict(
        DEFAULT_NOTIFICATION_POLICY
    )

    reverse = {
        setting_key:
            field

        for field, setting_key
        in NOTIFICATION_POLICY_SETTING_KEYS.items()
    }

    for row in rows:

        field = reverse.get(
            row["key"]
        )

        if field is None:
            continue

        try:
            values[field] = (
                _stored_bool(
                    row["value"]
                )
            )

        except ValueError:
            # Manual DB corruption should not make
            # the application unusable. Keep this
            # field on its documented default.
            values[field] = (
                DEFAULT_NOTIFICATION_POLICY[
                    field
                ]
            )

    return (
        normalize_notification_policy(
            values
        )
    )


def save_notification_policy(
    policy,
):
    normalized = (
        normalize_notification_policy(
            policy
        )
    )

    conn = db()

    try:
        conn.executemany("""
            INSERT INTO settings(
                key,
                value
            )
            VALUES (?, ?)

            ON CONFLICT(key)
            DO UPDATE SET
                value=excluded.value
        """, [
            (
                NOTIFICATION_POLICY_SETTING_KEYS[
                    field
                ],
                (
                    "1"
                    if normalized[field]
                    else "0"
                ),
            )

            for field
            in NOTIFICATION_POLICY_FIELDS
        ])

        conn.commit()

    finally:
        conn.close()

    return normalized


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
