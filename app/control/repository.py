"""Persistence helpers for verified ASIC control jobs."""

import time

from db import db


__all__ = (
    "get_control_job",
    "get_active_control_job",
    "list_active_control_jobs",
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
