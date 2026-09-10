"""SQLite persistence for anomaly candidates and issues."""

import time

from db import db


def active_issue_exists(miner_id, code):
    conn = db()

    row = conn.execute("""
        SELECT id
        FROM issues
        WHERE
            miner_id=?
            AND code=?
            AND status='ACTIVE'
        ORDER BY id DESC
        LIMIT 1
    """, (
        miner_id,
        code,
    )).fetchone()

    conn.close()
    return bool(row)


def anomaly_scan_snapshot():
    conn = db()

    scheduler_row = conn.execute("""
        SELECT value
        FROM settings
        WHERE key='scheduler_enabled'
    """).fetchone()

    scheduler_enabled = bool(
        scheduler_row
        and str(scheduler_row["value"]) == "1"
    )

    miners = conn.execute("""
        SELECT
            m.*,

            EXISTS(
                SELECT 1
                FROM control_jobs cj
                WHERE
                    cj.miner_id=m.id
                    AND cj.status IN (
                        'QUEUED',
                        'RUNNING'
                    )
            ) AS has_control_job,

            (
                SELECT cj.action
                FROM control_jobs cj
                WHERE
                    cj.miner_id=m.id
                    AND cj.status IN (
                        'QUEUED',
                        'RUNNING'
                    )
                ORDER BY cj.id DESC
                LIMIT 1
            ) AS active_control_action

        FROM miners m

        WHERE driver IN (
            'awesome',
            'bitmain_stock'
        )
    """).fetchall()

    conn.close()
    return scheduler_enabled, list(miners)


def transition_anomaly_condition(
    miner,
    code,
    severity,
    observed,
    grace_seconds,
    message,
    now=None,
):
    if now is None:
        now = int(time.time())

    opened = False
    resolved = False

    conn = db()

    active = conn.execute("""
        SELECT *
        FROM issues
        WHERE
            miner_id=?
            AND code=?
            AND status='ACTIVE'
        ORDER BY id DESC
        LIMIT 1
    """, (
        miner["id"],
        code,
    )).fetchone()

    candidate = conn.execute("""
        SELECT *
        FROM anomaly_candidates
        WHERE
            miner_id=?
            AND code=?
    """, (
        miner["id"],
        code,
    )).fetchone()

    if observed:
        if active:
            conn.execute("""
                UPDATE issues
                SET
                    last_seen=?,
                    message=?,
                    severity=?
                WHERE id=?
            """, (
                now,
                message,
                severity,
                active["id"],
            ))
        else:
            if not candidate:
                conn.execute("""
                    INSERT INTO anomaly_candidates
                    (
                        miner_id,
                        code,
                        since_ts,
                        last_seen_ts
                    )
                    VALUES (?, ?, ?, ?)
                """, (
                    miner["id"],
                    code,
                    now,
                    now,
                ))
            else:
                conn.execute("""
                    UPDATE anomaly_candidates
                    SET last_seen_ts=?
                    WHERE
                        miner_id=?
                        AND code=?
                """, (
                    now,
                    miner["id"],
                    code,
                ))

                if now - candidate["since_ts"] >= grace_seconds:
                    conn.execute("""
                        INSERT INTO issues
                        (
                            miner_id,
                            ip,
                            name,
                            code,
                            severity,
                            status,
                            first_seen,
                            last_seen,
                            message
                        )
                        VALUES (
                            ?, ?, ?, ?, ?,
                            'ACTIVE',
                            ?, ?, ?
                        )
                    """, (
                        miner["id"],
                        miner["ip"],
                        miner["name"],
                        code,
                        severity,
                        candidate["since_ts"],
                        now,
                        message,
                    ))

                    conn.execute("""
                        DELETE FROM anomaly_candidates
                        WHERE
                            miner_id=?
                            AND code=?
                    """, (
                        miner["id"],
                        code,
                    ))
                    opened = True
    else:
        conn.execute("""
            DELETE FROM anomaly_candidates
            WHERE
                miner_id=?
                AND code=?
        """, (
            miner["id"],
            code,
        ))

        if active:
            conn.execute("""
                UPDATE issues
                SET
                    status='RESOLVED',
                    last_seen=?,
                    resolved_at=?,
                    message=?
                WHERE id=?
            """, (
                now,
                now,
                message,
                active["id"],
            ))
            resolved = True

    conn.commit()
    conn.close()

    return opened, resolved


def issue_rows(limit=100):
    """Return active issues and the most recently resolved issues."""
    limit = max(1, int(limit))
    conn = db()
    try:
        active = conn.execute("""
            SELECT * FROM issues
            WHERE status='ACTIVE'
            ORDER BY
                CASE severity
                    WHEN 'CRITICAL' THEN 1
                    WHEN 'WARNING' THEN 2
                    ELSE 3
                END,
                first_seen ASC
        """).fetchall()
        resolved = conn.execute("""
            SELECT * FROM issues
            WHERE status='RESOLVED'
            ORDER BY resolved_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return list(active), list(resolved)
    finally:
        conn.close()


__all__ = (
    "active_issue_exists",
    "anomaly_scan_snapshot",
    "transition_anomaly_condition",
    "issue_rows",
)
