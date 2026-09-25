"""Persistence helpers for verified ASIC control jobs."""

import time

from db import db


__all__ = (
    "get_control_job",
    "get_active_control_job",
    "list_active_control_jobs",
    "get_bulk_control_group",
    "list_bulk_control_miners_by_ids",
    "list_bulk_control_miners_by_group",
    "list_control_jobs",
    "update_control_job",
    "set_last_command",
    "set_manual_override",
    "create_control_job",
)


def get_control_job(job_id):
    conn = db()
    try:
        return conn.execute("""
            SELECT *
            FROM control_jobs
            WHERE id=?
        """, (
            job_id,
        )).fetchone()
    finally:
        conn.close()


def list_active_control_jobs():
    conn = db()
    try:
        return list(
            conn.execute("""
                SELECT *
                FROM control_jobs
                WHERE status IN (
                    'QUEUED',
                    'RUNNING'
                )
                ORDER BY id DESC
            """).fetchall()
        )
    finally:
        conn.close()


def get_active_control_job(miner_id):
    conn = db()
    try:
        return conn.execute("""
            SELECT *
            FROM control_jobs
            WHERE
                miner_id=?
                AND status IN (
                    'QUEUED',
                    'RUNNING'
                )
            ORDER BY id DESC
            LIMIT 1
        """, (
            miner_id,
        )).fetchone()
    finally:
        conn.close()


def _bulk_control_selection_query(
    where_sql,
):
    return f"""
        SELECT
            m.*,
            g.name AS group_name,

            cj.id AS active_job_id,
            cj.status AS active_job_status,
            cj.action AS active_job_action,
            cj.target_state AS active_job_target_state

        FROM miners m

        LEFT JOIN miner_groups g
            ON g.id=m.group_id

        LEFT JOIN control_jobs cj
            ON cj.id=(
                SELECT active.id

                FROM control_jobs active

                WHERE
                    active.miner_id=m.id
                    AND active.status IN (
                        'QUEUED',
                        'RUNNING'
                    )

                ORDER BY active.id DESC

                LIMIT 1
            )

        WHERE {where_sql}

        ORDER BY m.id
    """


def get_bulk_control_group(
    group_id,
):
    conn = db()

    try:
        return conn.execute("""
            SELECT
                id,
                name

            FROM miner_groups

            WHERE id=?
        """, (
            int(group_id),
        )).fetchone()

    finally:
        conn.close()


def list_bulk_control_miners_by_ids(
    miner_ids,
):
    miner_ids = [
        int(miner_id)
        for miner_id
        in miner_ids
    ]

    if not miner_ids:
        return []

    placeholders = ",".join(
        "?"
        for _
        in miner_ids
    )

    conn = db()

    try:
        return list(
            conn.execute(
                _bulk_control_selection_query(
                    f"m.id IN ({placeholders})"
                ),
                miner_ids,
            ).fetchall()
        )

    finally:
        conn.close()


def list_bulk_control_miners_by_group(
    group_id,
):
    conn = db()

    try:
        return list(
            conn.execute(
                _bulk_control_selection_query(
                    "m.group_id=?"
                ),
                (
                    int(group_id),
                ),
            ).fetchall()
        )

    finally:
        conn.close()


def update_control_job(
    job_id,
    **fields,
):
    if not fields:
        return

    allowed = {
        "started_at",
        "completed_at",
        "status",
        "attempts",
        "final_state",
        "message",
    }

    unknown = set(fields) - allowed
    if unknown:
        raise RuntimeError(
            "Invalid control job field: "
            + ", ".join(sorted(unknown))
        )

    columns = []
    values = []

    for key, value in fields.items():
        columns.append(f"{key}=?")
        values.append(value)

    values.append(job_id)

    conn = db()
    try:
        conn.execute(
            """
            UPDATE control_jobs
            SET %s
            WHERE id=?
            """
            % ", ".join(columns),
            values,
        )
        conn.commit()
    finally:
        conn.close()


def set_last_command(
    miner_id,
    action,
):
    conn = db()
    try:
        conn.execute("""
            UPDATE miners
            SET
                last_action=?,
                last_action_at=?
            WHERE id=?
        """, (
            action.upper(),
            int(time.time()),
            miner_id,
        ))
        conn.commit()
    finally:
        conn.close()


def set_manual_override(
    miner_id,
    override_until,
):
    conn = db()
    try:
        conn.execute("""
            UPDATE miners
            SET manual_override_until=?
            WHERE id=?
        """, (
            int(override_until),
            miner_id,
        ))
        conn.commit()
    finally:
        conn.close()


def create_control_job(
    miner,
    source,
    action,
    target_state,
    max_attempts,
    now=None,
):
    if now is None:
        now = int(time.time())

    conn = db()
    try:
        cur = conn.execute("""
            INSERT INTO control_jobs
            (
                created_at,
                miner_id,
                ip,
                name,
                source,
                action,
                target_state,
                status,
                attempts,
                max_attempts
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                'QUEUED',
                0,
                ?
            )
        """, (
            int(now),
            miner["id"],
            miner["ip"],
            miner["name"],
            source,
            action,
            target_state,
            int(max_attempts),
        ))
        job_id = cur.lastrowid
        conn.commit()
        return job_id
    finally:
        conn.close()



def list_control_jobs(limit):
    limit = max(1, int(limit))

    conn = db()
    try:
        return list(
            conn.execute("""
                SELECT *
                FROM control_jobs
                ORDER BY id DESC
                LIMIT ?
            """, (
                limit,
            )).fetchall()
        )
    finally:
        conn.close()
