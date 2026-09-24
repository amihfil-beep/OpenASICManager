"""SQLite persistence for anomaly policy, candidates and issues."""

import time

from anomalies.policy import (
    ANOMALY_POLICY_FIELDS,
    DEFAULT_ANOMALY_POLICY,
    MINER_ANOMALY_OVERRIDE_FIELDS,
    normalize_anomaly_overrides,
    normalize_anomaly_policy,
    normalize_group_anomaly_overrides,
    resolve_anomaly_policy,
    resolve_layered_anomaly_policy,
)
from db import db


ANOMALY_POLICY_SETTING_KEYS = {
    field: f"anomaly.{field}"
    for field in ANOMALY_POLICY_FIELDS
}


def load_anomaly_policy():
    conn = db()

    try:
        rows = conn.execute("""
            SELECT key, value
            FROM settings
            WHERE key LIKE 'anomaly.%'
        """).fetchall()
    finally:
        conn.close()

    values = dict(DEFAULT_ANOMALY_POLICY)
    reverse_keys = {
        setting_key: field
        for field, setting_key
        in ANOMALY_POLICY_SETTING_KEYS.items()
    }

    for row in rows:
        field = reverse_keys.get(
            row["key"]
        )
        if field:
            values[field] = row["value"]

    try:
        return normalize_anomaly_policy(
            values
        )
    except ValueError:
        # Invalid values can only appear after manual DB editing or
        # corruption because normal writes are validated. Falling back
        # to the documented defaults keeps anomaly detection available.
        return dict(DEFAULT_ANOMALY_POLICY)


def save_anomaly_policy(policy):
    normalized = normalize_anomaly_policy(
        policy
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
                ANOMALY_POLICY_SETTING_KEYS[field],
                str(normalized[field]),
            )
            for field in ANOMALY_POLICY_FIELDS
        ])
        conn.commit()
    finally:
        conn.close()

    return normalized


def _override_values_from_row(row):
    if row is None:
        return {}

    return {
        field: row[field]
        for field
        in MINER_ANOMALY_OVERRIDE_FIELDS
        if row[field] is not None
    }


def _miner_group_id(miner_id):
    conn = db()

    try:
        row = conn.execute("""
            SELECT group_id
            FROM miners
            WHERE id=?
        """, (
            int(miner_id),
        )).fetchone()

    finally:
        conn.close()

    if row is None:
        return None

    return row["group_id"]


def load_group_anomaly_overrides(
    group_id,
    global_policy=None,
):
    if group_id is None:
        return {}

    if global_policy is None:
        global_policy = (
            load_anomaly_policy()
        )

    conn = db()

    try:
        row = conn.execute("""
            SELECT *
            FROM group_anomaly_policy_overrides
            WHERE group_id=?
        """, (
            int(group_id),
        )).fetchone()

    finally:
        conn.close()

    raw = _override_values_from_row(
        row
    )

    try:
        overrides = (
            normalize_group_anomaly_overrides(
                raw
            )
        )

        resolve_layered_anomaly_policy(
            global_policy,
            group_overrides=overrides,
            miner_overrides={},
        )

        return overrides

    except ValueError:
        # Corrupted/manual rows fall back to
        # GLOBAL inheritance.
        return {}


def load_group_anomaly_override_snapshot(
    global_policy=None,
):
    if global_policy is None:
        global_policy = (
            load_anomaly_policy()
        )

    conn = db()

    try:
        rows = conn.execute("""
            SELECT *
            FROM group_anomaly_policy_overrides
        """).fetchall()

    finally:
        conn.close()

    result = {}

    for row in rows:

        raw = _override_values_from_row(
            row
        )

        try:
            overrides = (
                normalize_group_anomaly_overrides(
                    raw
                )
            )

            resolve_layered_anomaly_policy(
                global_policy,
                group_overrides=overrides,
                miner_overrides={},
            )

        except ValueError:
            continue

        if overrides:
            result[
                int(row["group_id"])
            ] = overrides

    return result


def load_miner_anomaly_overrides(
    miner_id,
    global_policy=None,
    group_overrides=None,
):
    if global_policy is None:
        global_policy = (
            load_anomaly_policy()
        )

    if group_overrides is None:

        group_id = _miner_group_id(
            miner_id
        )

        group_overrides = (
            load_group_anomaly_overrides(
                group_id,
                global_policy=global_policy,
            )
            if group_id is not None
            else {}
        )

    conn = db()

    try:
        row = conn.execute("""
            SELECT *
            FROM anomaly_policy_overrides
            WHERE miner_id=?
        """, (
            int(miner_id),
        )).fetchone()

    finally:
        conn.close()

    raw = _override_values_from_row(
        row
    )

    try:
        overrides = (
            normalize_anomaly_overrides(
                raw
            )
        )

        resolve_layered_anomaly_policy(
            global_policy,
            group_overrides=group_overrides,
            miner_overrides=overrides,
        )

        return overrides

    except ValueError:
        # Corrupted/manual rows fall back to
        # inherited GLOBAL/GROUP policy.
        return {}


def load_anomaly_override_snapshot(
    global_policy=None,
    group_snapshot=None,
):
    if global_policy is None:
        global_policy = (
            load_anomaly_policy()
        )

    if group_snapshot is None:
        group_snapshot = (
            load_group_anomaly_override_snapshot(
                global_policy
            )
        )

    conn = db()

    try:
        rows = conn.execute("""
            SELECT
                apo.*,
                m.group_id AS miner_group_id

            FROM anomaly_policy_overrides apo

            JOIN miners m
                ON m.id=apo.miner_id
        """).fetchall()

    finally:
        conn.close()

    result = {}

    for row in rows:

        raw = _override_values_from_row(
            row
        )

        group_overrides = (
            group_snapshot.get(
                int(row["miner_group_id"]),
                {},
            )
            if row["miner_group_id"]
            is not None
            else {}
        )

        try:
            overrides = (
                normalize_anomaly_overrides(
                    raw
                )
            )

            resolve_layered_anomaly_policy(
                global_policy,
                group_overrides=group_overrides,
                miner_overrides=overrides,
            )

        except ValueError:
            continue

        if overrides:
            result[
                int(row["miner_id"])
            ] = overrides

    return result


def validate_global_policy_against_overrides(
    global_policy,
):
    normalized_global = (
        normalize_anomaly_policy(
            global_policy
        )
    )

    current_global = (
        load_anomaly_policy()
    )

    current_groups = (
        load_group_anomaly_override_snapshot(
            current_global
        )
    )

    # A global update must keep every currently
    # valid GROUP policy valid.
    for group_id, group_overrides in (
        current_groups.items()
    ):

        try:
            resolve_layered_anomaly_policy(
                normalized_global,
                group_overrides=group_overrides,
                miner_overrides={},
            )

        except ValueError as exc:
            raise ValueError(
                "Global anomaly policy conflicts "
                "with overrides for group "
                f"{group_id}: {exc}"
            )

    conn = db()

    try:
        rows = conn.execute("""
            SELECT
                apo.*,
                m.group_id AS miner_group_id

            FROM anomaly_policy_overrides apo

            JOIN miners m
                ON m.id=apo.miner_id
        """).fetchall()

    finally:
        conn.close()

    for row in rows:

        try:
            miner_overrides = (
                normalize_anomaly_overrides(
                    _override_values_from_row(
                        row
                    )
                )
            )

        except ValueError:
            continue

        current_group = (
            current_groups.get(
                int(row["miner_group_id"]),
                {},
            )
            if row["miner_group_id"]
            is not None
            else {}
        )

        try:
            resolve_layered_anomaly_policy(
                current_global,
                group_overrides=current_group,
                miner_overrides=miner_overrides,
            )

        except ValueError:
            # Already-corrupted/manual rows are
            # ignored by normal resolution.
            continue

        try:
            resolve_layered_anomaly_policy(
                normalized_global,
                group_overrides=current_group,
                miner_overrides=miner_overrides,
            )

        except ValueError as exc:
            raise ValueError(
                "Global anomaly policy conflicts "
                "with overrides for miner "
                f"{int(row['miner_id'])}: {exc}"
            )

    return normalized_global


def validate_group_policy_against_miner_overrides(
    group_id,
    group_overrides,
):
    global_policy = (
        load_anomaly_policy()
    )

    normalized_group = (
        normalize_group_anomaly_overrides(
            group_overrides or {}
        )
    )

    resolve_layered_anomaly_policy(
        global_policy,
        group_overrides=normalized_group,
        miner_overrides={},
    )

    current_group = (
        load_group_anomaly_overrides(
            group_id,
            global_policy=global_policy,
        )
    )

    conn = db()

    try:
        rows = conn.execute("""
            SELECT apo.*

            FROM anomaly_policy_overrides apo

            JOIN miners m
                ON m.id=apo.miner_id

            WHERE m.group_id=?
        """, (
            int(group_id),
        )).fetchall()

    finally:
        conn.close()

    for row in rows:

        try:
            miner_overrides = (
                normalize_anomaly_overrides(
                    _override_values_from_row(
                        row
                    )
                )
            )

        except ValueError:
            continue

        try:
            resolve_layered_anomaly_policy(
                global_policy,
                group_overrides=current_group,
                miner_overrides=miner_overrides,
            )

        except ValueError:
            # Existing manual corruption should not
            # prevent recovery.
            continue

        try:
            resolve_layered_anomaly_policy(
                global_policy,
                group_overrides=normalized_group,
                miner_overrides=miner_overrides,
            )

        except ValueError as exc:
            raise ValueError(
                "Group anomaly policy conflicts "
                "with overrides for miner "
                f"{int(row['miner_id'])}: {exc}"
            )

    return normalized_group


def validate_miner_group_policy_membership(
    miner_id,
    group_id,
):
    global_policy = (
        load_anomaly_policy()
    )

    group_overrides = (
        load_group_anomaly_overrides(
            group_id,
            global_policy=global_policy,
        )
        if group_id is not None
        else {}
    )

    conn = db()

    try:
        row = conn.execute("""
            SELECT *
            FROM anomaly_policy_overrides
            WHERE miner_id=?
        """, (
            int(miner_id),
        )).fetchone()

    finally:
        conn.close()

    try:
        miner_overrides = (
            normalize_anomaly_overrides(
                _override_values_from_row(
                    row
                )
            )
        )

    except ValueError:
        # Corrupted miner overrides are already
        # ignored by normal resolution.
        miner_overrides = {}

    try:
        return resolve_layered_anomaly_policy(
            global_policy,
            group_overrides=group_overrides,
            miner_overrides=miner_overrides,
        )

    except ValueError as exc:
        target = (
            f"group {int(group_id)}"
            if group_id is not None
            else "GLOBAL inheritance"
        )

        raise ValueError(
            "Miner anomaly policy would become "
            f"invalid under {target}: {exc}"
        )


def validate_group_policy_removal(
    group_id,
):
    conn = db()

    try:
        rows = conn.execute("""
            SELECT id
            FROM miners
            WHERE group_id=?
            ORDER BY id
        """, (
            int(group_id),
        )).fetchall()

    finally:
        conn.close()

    for row in rows:

        validate_miner_group_policy_membership(
            int(row["id"]),
            None,
        )

    return True


def save_group_anomaly_overrides(
    group_id,
    overrides,
    actor,
    now=None,
):
    if now is None:
        now = int(time.time())

    normalized = (
        validate_group_policy_against_miner_overrides(
            group_id,
            overrides,
        )
    )

    conn = db()

    try:

        if not normalized:

            conn.execute("""
                DELETE FROM
                    group_anomaly_policy_overrides
                WHERE group_id=?
            """, (
                int(group_id),
            ))

        else:

            conn.execute("""
                INSERT INTO
                    group_anomaly_policy_overrides
                (
                    group_id,
                    offline_grace_seconds,
                    hot_temp_c,
                    hot_clear_c,
                    hot_grace_seconds,
                    schedule_grace_seconds,
                    updated_by,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?
                )

                ON CONFLICT(group_id)
                DO UPDATE SET
                    offline_grace_seconds=
                        excluded.offline_grace_seconds,
                    hot_temp_c=
                        excluded.hot_temp_c,
                    hot_clear_c=
                        excluded.hot_clear_c,
                    hot_grace_seconds=
                        excluded.hot_grace_seconds,
                    schedule_grace_seconds=
                        excluded.schedule_grace_seconds,
                    updated_by=
                        excluded.updated_by,
                    updated_at=
                        excluded.updated_at
            """, (
                int(group_id),

                normalized.get(
                    "offline_grace_seconds"
                ),

                normalized.get(
                    "hot_temp_c"
                ),

                normalized.get(
                    "hot_clear_c"
                ),

                normalized.get(
                    "hot_grace_seconds"
                ),

                normalized.get(
                    "schedule_grace_seconds"
                ),

                str(actor),
                int(now),
            ))

        conn.commit()

    finally:
        conn.close()

    return normalized


def clear_group_anomaly_overrides(
    group_id,
):
    validate_group_policy_against_miner_overrides(
        group_id,
        {},
    )

    conn = db()

    try:
        cursor = conn.execute("""
            DELETE FROM
                group_anomaly_policy_overrides
            WHERE group_id=?
        """, (
            int(group_id),
        ))

        conn.commit()

        return cursor.rowcount > 0

    finally:
        conn.close()


def save_miner_anomaly_overrides(
    miner_id,
    overrides,
    actor,
    now=None,
):
    if now is None:
        now = int(time.time())

    global_policy = (
        load_anomaly_policy()
    )

    group_id = _miner_group_id(
        miner_id
    )

    group_overrides = (
        load_group_anomaly_overrides(
            group_id,
            global_policy=global_policy,
        )
        if group_id is not None
        else {}
    )

    normalized = (
        normalize_anomaly_overrides(
            overrides
        )
    )

    resolve_layered_anomaly_policy(
        global_policy,
        group_overrides=group_overrides,
        miner_overrides=normalized,
    )

    conn = db()

    try:

        if not normalized:

            conn.execute("""
                DELETE FROM anomaly_policy_overrides
                WHERE miner_id=?
            """, (
                int(miner_id),
            ))

        else:

            conn.execute("""
                INSERT INTO anomaly_policy_overrides
                (
                    miner_id,
                    offline_grace_seconds,
                    hot_temp_c,
                    hot_clear_c,
                    hot_grace_seconds,
                    schedule_grace_seconds,
                    updated_by,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?
                )

                ON CONFLICT(miner_id)
                DO UPDATE SET
                    offline_grace_seconds=
                        excluded.offline_grace_seconds,
                    hot_temp_c=
                        excluded.hot_temp_c,
                    hot_clear_c=
                        excluded.hot_clear_c,
                    hot_grace_seconds=
                        excluded.hot_grace_seconds,
                    schedule_grace_seconds=
                        excluded.schedule_grace_seconds,
                    updated_by=
                        excluded.updated_by,
                    updated_at=
                        excluded.updated_at
            """, (
                int(miner_id),

                normalized.get(
                    "offline_grace_seconds"
                ),

                normalized.get(
                    "hot_temp_c"
                ),

                normalized.get(
                    "hot_clear_c"
                ),

                normalized.get(
                    "hot_grace_seconds"
                ),

                normalized.get(
                    "schedule_grace_seconds"
                ),

                str(actor),
                int(now),
            ))

        conn.commit()

    finally:
        conn.close()

    return normalized


def clear_miner_anomaly_overrides(
    miner_id,
):
    conn = db()

    try:

        cursor = conn.execute("""
            DELETE FROM anomaly_policy_overrides
            WHERE miner_id=?
        """, (
            int(miner_id),
        ))

        conn.commit()

        return cursor.rowcount > 0

    finally:
        conn.close()


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
    suppress_new=False,
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
        elif suppress_new:
            conn.execute("""
                DELETE FROM anomaly_candidates
                WHERE
                    miner_id=?
                    AND code=?
            """, (
                miner["id"],
                code,
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


def set_issue_acknowledgement(
    issue_id,
    actor,
    note,
    now=None,
):
    if now is None:
        now = int(time.time())

    conn = db()

    try:
        cursor = conn.execute("""
            UPDATE issues
            SET
                acknowledged_at=?,
                acknowledged_by=?,
                acknowledgement_note=?
            WHERE id=?
        """, (
            int(now),
            str(actor),
            note,
            int(issue_id),
        ))

        if cursor.rowcount == 0:
            conn.rollback()
            return None

        row = conn.execute("""
            SELECT *
            FROM issues
            WHERE id=?
        """, (
            int(issue_id),
        )).fetchone()

        conn.commit()
        return row

    finally:
        conn.close()


def clear_issue_acknowledgement(
    issue_id,
):
    conn = db()

    try:
        cursor = conn.execute("""
            UPDATE issues
            SET
                acknowledged_at=NULL,
                acknowledged_by=NULL,
                acknowledgement_note=NULL
            WHERE id=?
        """, (
            int(issue_id),
        ))

        if cursor.rowcount == 0:
            conn.rollback()
            return None

        row = conn.execute("""
            SELECT *
            FROM issues
            WHERE id=?
        """, (
            int(issue_id),
        )).fetchone()

        conn.commit()
        return row

    finally:
        conn.close()


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
    "ANOMALY_POLICY_SETTING_KEYS",
    "load_anomaly_policy",
    "save_anomaly_policy",
    "load_miner_anomaly_overrides",
    "load_anomaly_override_snapshot",
    "validate_global_policy_against_overrides",
    "save_miner_anomaly_overrides",
    "clear_miner_anomaly_overrides",
    "active_issue_exists",
    "anomaly_scan_snapshot",
    "transition_anomaly_condition",
    "set_issue_acknowledgement",
    "clear_issue_acknowledgement",
    "issue_rows",
)
