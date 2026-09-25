"""
Scheduler state repository.

Contains database-backed schedule state queries.

This module does not expose HTTP endpoints, start scheduler
threads, write audit events or queue ASIC control actions.
"""

from datetime import (
    datetime,
    timedelta,
)

from db import (
    db,
    ensure_schedule_rules_schema,
)

from scheduler.policy import (
    MOSCOW,
    schedule_action_state,
    schedule_rule_next_run,
)


__all__ = (
    "schedule_state_details",
    "desired_state",
    "effective_schedule_states",
    "next_transition_for_miner",
    "schedule_conflicting_rule",
    "next_transition",
    "list_schedule_rules",
    "get_schedule_rule",
    "create_schedule_rule",
    "update_schedule_rule",
    "set_schedule_rule_enabled",
    "delete_schedule_rule",
    "mark_schedule_rule_seen",
    "list_schedulable_miners",
    "count_group_schedule_rules",
)


def _rule_last_occurrence(
    rule,
    now,
):

    hour = (
        int(
            rule["time_minutes"]
        )
        // 60
    )

    minute = (
        int(
            rule["time_minutes"]
        )
        % 60
    )

    mask = int(
        rule["days_mask"]
    )

    effective_from = int(
        rule["effective_from"]
        or 0
    )


    # Seven days cover a weekly schedule.
    # The eighth day gives one safe boundary day.
    for offset in range(
        0,
        8,
    ):

        day = (
            now
            -
            timedelta(
                days=offset
            )
        ).date()


        if not (
            mask
            &
            (
                1
                <<
                day.weekday()
            )
        ):
            continue


        occurrence = datetime(
            day.year,
            day.month,
            day.day,
            hour,
            minute,
            0,
            tzinfo=MOSCOW,
        )


        if occurrence > now:
            continue


        # Never apply a newly-created/enabled rule
        # retroactively to an occurrence that happened
        # before that rule became effective.
        if (
            effective_from
            and
            int(
                occurrence.timestamp()
            )
            <
            effective_from
        ):
            continue


        return occurrence


    return None


def _scope_state_details(
    rules,
    now,
):

    winner_rule = None
    winner_occurrence = None


    for rule in rules:

        occurrence = (
            _rule_last_occurrence(
                rule,
                now,
            )
        )


        if occurrence is None:
            continue


        if (
            winner_occurrence is None
            or
            occurrence
            >
            winner_occurrence
        ):

            winner_occurrence = (
                occurrence
            )

            winner_rule = rule


    if winner_rule is None:

        return (
            None,
            None,
            None,
        )


    return (
        schedule_action_state(
            winner_rule["action"]
        ),
        winner_rule,
        winner_occurrence,
    )


def _scope_next_transition(
    rules,
    now,
):

    candidates = []


    for rule in rules:

        candidate = (
            schedule_rule_next_run(
                rule,
                now,
            )
        )


        if candidate is not None:

            candidates.append(
                candidate
            )


    if not candidates:
        return None


    return min(
        candidates
    )


def _enabled_schedule_rules():

    ensure_schedule_rules_schema()


    conn = db()

    try:
        return list(
            conn.execute("""
                SELECT *

                FROM schedule_rules

                WHERE enabled=1

                ORDER BY id
            """).fetchall()
        )

    finally:
        conn.close()


def _rules_for_scope(
    rules,
    scope,
    group_id=None,
):

    result = []


    for rule in rules:

        if str(
            rule["scope"]
        ) != scope:
            continue


        if scope == "GROUP":

            if (
                group_id is None
                or
                rule["group_id"] is None
                or
                int(
                    rule["group_id"]
                )
                !=
                int(
                    group_id
                )
            ):
                continue


        result.append(
            rule
        )


    return result


def _effective_schedule_details(
    miner,
    rules,
    now,
):

    farm_rules = (
        _rules_for_scope(
            rules,
            "FARM",
        )
    )


    group_id = (
        miner["group_id"]
        if (
            "group_id"
            in miner.keys()
        )
        else None
    )


    group_rules = (
        _rules_for_scope(
            rules,
            "GROUP",
            group_id,
        )
        if group_id is not None
        else []
    )


    (
        farm_state,
        farm_rule,
        farm_occurrence,
    ) = _scope_state_details(
        farm_rules,
        now,
    )


    farm_next = (
        _scope_next_transition(
            farm_rules,
            now,
        )
    )


    (
        group_state,
        group_rule,
        group_occurrence,
    ) = _scope_state_details(
        group_rules,
        now,
    )


    group_next = (
        _scope_next_transition(
            group_rules,
            now,
        )
    )


    # GROUP is the more specific layer.
    #
    # Once a GROUP rule has an effective past occurrence,
    # FARM transitions no longer change the effective state
    # for that miner until membership or GROUP schedule
    # applicability changes.
    if group_rule is not None:

        return {
            "desired_state":
                group_state,

            "source_scope":
                "GROUP",

            "rule":
                group_rule,

            "occurrence":
                group_occurrence,

            "next_transition":
                group_next,
        }


    # No GROUP occurrence is active yet.
    #
    # FARM remains the current baseline, but the first future
    # GROUP occurrence can become the miner's next effective
    # transition before the next FARM event.
    future_candidates = [
        candidate
        for candidate
        in (
            farm_next,
            group_next,
        )
        if candidate is not None
    ]


    return {
        "desired_state":
            farm_state,

        "source_scope":
            (
                "FARM"
                if farm_rule is not None
                else None
            ),

        "rule":
            farm_rule,

        "occurrence":
            farm_occurrence,

        "next_transition":
            (
                min(
                    future_candidates
                )
                if future_candidates
                else None
            ),
    }


def effective_schedule_states(
    miners,
    now=None,
):

    if now is None:

        now = datetime.now(
            MOSCOW
        )


    rules = (
        _enabled_schedule_rules()
    )


    return {
        int(
            miner["id"]
        ):
            _effective_schedule_details(
                miner,
                rules,
                now,
            )

        for miner
        in miners
    }


def next_transition_for_miner(
    miner_id,
    now=None,
):

    if now is None:

        now = datetime.now(
            MOSCOW
        )


    ensure_schedule_rules_schema()


    conn = db()

    try:

        miner = conn.execute("""
            SELECT
                id,
                group_id

            FROM miners

            WHERE id=?
        """, (
            int(
                miner_id
            ),
        )).fetchone()

    finally:
        conn.close()


    if miner is None:
        return None


    rules = (
        _enabled_schedule_rules()
    )


    details = (
        _effective_schedule_details(
            miner,
            rules,
            now,
        )
    )


    return details[
        "next_transition"
    ]


def schedule_state_details(
    now=None,
):

    if now is None:

        now = datetime.now(
            MOSCOW
        )


    rules = [
        rule
        for rule
        in _enabled_schedule_rules()
        if str(
            rule["scope"]
        )
        ==
        "FARM"
    ]


    # Keep the historical farm-wide API/read model intact.
    return _scope_state_details(
        rules,
        now,
    )


def desired_state(
    now=None,
):

    state, _, _ = (
        schedule_state_details(
            now
        )
    )

    return state


def next_transition(
    now=None,
):

    if now is None:

        now = datetime.now(
            MOSCOW
        )


    rules = [
        rule
        for rule
        in _enabled_schedule_rules()
        if str(
            rule["scope"]
        )
        ==
        "FARM"
    ]


    # Keep the historical FARM summary intact.
    return _scope_next_transition(
        rules,
        now,
    )


def schedule_conflicting_rule(
    normalized,
    exclude_id=None,
):

    if not normalized[
        "enabled"
    ]:

        return None


    ensure_schedule_rules_schema()


    conn = db()

    sql = """
        SELECT *

        FROM schedule_rules

        WHERE
            enabled=1
            AND time_minutes=?
            AND (
                days_mask & ?
            ) != 0
            AND scope=?
            AND (
                (
                    ? IS NULL
                    AND group_id IS NULL
                )
                OR group_id=?
            )
    """

    group_id = normalized.get(
        "group_id"
    )

    params = [
        normalized[
            "time_minutes"
        ],
        normalized[
            "days_mask"
        ],
        normalized[
            "scope"
        ],
        group_id,
        group_id,
    ]


    if exclude_id is not None:

        sql += """
            AND id != ?
        """

        params.append(
            int(
                exclude_id
            )
        )


    sql += """
        ORDER BY id
        LIMIT 1
    """


    row = conn.execute(
        sql,
        tuple(
            params
        ),
    ).fetchone()

    conn.close()


    return row


def next_transition(
    now=None,
):

    ensure_schedule_rules_schema()


    if now is None:

        now = datetime.now(
            MOSCOW
        )


    conn = db()

    rules = conn.execute("""
        SELECT *

        FROM schedule_rules

        WHERE
            enabled=1
            AND scope='FARM'
    """).fetchall()

    conn.close()


    candidates = []


    for rule in rules:

        candidate = (
            schedule_rule_next_run(
                rule,
                now
            )
        )


        if candidate:

            candidates.append(
                candidate
            )


    if not candidates:

        return None


    return min(
        candidates
    )

def _schedule_rule_read_query(
    where_sql="",
):

    return f"""
        SELECT
            r.*,

            g.name
                AS group_name,

            CASE
                WHEN r.scope='GROUP'
                THEN (
                    SELECT COUNT(*)
                    FROM miners m
                    WHERE m.group_id=r.group_id
                )
                ELSE NULL
            END
                AS group_member_count

        FROM schedule_rules r

        LEFT JOIN miner_groups g
            ON g.id=r.group_id

        {where_sql}
    """


def list_schedule_rules():
    ensure_schedule_rules_schema()

    conn = db()
    try:
        return conn.execute(
            _schedule_rule_read_query(
                "ORDER BY r.time_minutes, r.id"
            )
        ).fetchall()
    finally:
        conn.close()


def get_schedule_rule(rule_id):
    ensure_schedule_rules_schema()

    conn = db()
    try:
        return conn.execute(
            _schedule_rule_read_query(
                "WHERE r.id=?"
            ),
            (
                int(rule_id),
            ),
        ).fetchone()
    finally:
        conn.close()


def create_schedule_rule(
    normalized,
    now_epoch,
):
    ensure_schedule_rules_schema()

    conn = db()
    try:
        cur = conn.execute("""
            INSERT INTO schedule_rules
            (
                enabled,
                action,
                time_minutes,
                days_mask,
                scope,
                group_id,
                comment,
                effective_from,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            1 if normalized["enabled"] else 0,
            normalized["action"],
            normalized["time_minutes"],
            normalized["days_mask"],
            normalized["scope"],
            normalized.get(
                "group_id"
            ),
            normalized["comment"],
            int(now_epoch),
            int(now_epoch),
            int(now_epoch),
        ))

        rule_id = cur.lastrowid
        conn.commit()

        return conn.execute("""
            SELECT *
            FROM schedule_rules
            WHERE id=?
        """, (
            rule_id,
        )).fetchone()
    finally:
        conn.close()


def update_schedule_rule(
    rule_id,
    normalized,
    effective_from,
    now_epoch,
):
    ensure_schedule_rules_schema()

    conn = db()
    try:
        conn.execute("""
            UPDATE schedule_rules
            SET
                enabled=?,
                action=?,
                time_minutes=?,
                days_mask=?,
                scope=?,
                group_id=?,
                comment=?,
                effective_from=?,
                last_run_key=NULL,
                updated_at=?
            WHERE id=?
        """, (
            1 if normalized["enabled"] else 0,
            normalized["action"],
            normalized["time_minutes"],
            normalized["days_mask"],
            normalized["scope"],
            normalized.get(
                "group_id"
            ),
            normalized["comment"],
            int(effective_from),
            int(now_epoch),
            int(rule_id),
        ))
        conn.commit()

        return conn.execute("""
            SELECT *
            FROM schedule_rules
            WHERE id=?
        """, (
            int(rule_id),
        )).fetchone()
    finally:
        conn.close()


def set_schedule_rule_enabled(
    rule_id,
    enabled,
    effective_from,
    now_epoch,
):
    ensure_schedule_rules_schema()

    conn = db()
    try:
        conn.execute("""
            UPDATE schedule_rules
            SET
                enabled=?,
                effective_from=?,
                last_run_key=NULL,
                updated_at=?
            WHERE id=?
        """, (
            1 if enabled else 0,
            int(effective_from),
            int(now_epoch),
            int(rule_id),
        ))
        conn.commit()

        return conn.execute("""
            SELECT *
            FROM schedule_rules
            WHERE id=?
        """, (
            int(rule_id),
        )).fetchone()
    finally:
        conn.close()


def delete_schedule_rule(rule_id):
    ensure_schedule_rules_schema()

    conn = db()
    try:
        current = conn.execute("""
            SELECT *
            FROM schedule_rules
            WHERE id=?
        """, (
            int(rule_id),
        )).fetchone()

        if current is None:
            return None

        conn.execute("""
            DELETE FROM schedule_rules
            WHERE id=?
        """, (
            int(rule_id),
        ))
        conn.commit()
        return current
    finally:
        conn.close()



def mark_schedule_rule_seen(
    rule_id,
    run_key,
):
    ensure_schedule_rules_schema()

    conn = db()
    try:
        cur = conn.execute("""
            UPDATE schedule_rules

            SET last_run_key=?

            WHERE
                id=?
                AND COALESCE(
                    last_run_key,
                    ''
                ) <> ?
        """, (
            run_key,
            int(rule_id),
            run_key,
        ))

        changed = (
            cur.rowcount
            > 0
        )

        conn.commit()
        return changed
    finally:
        conn.close()


def count_group_schedule_rules(
    group_id,
):

    conn = db()

    try:

        table = conn.execute("""
            SELECT 1

            FROM sqlite_master

            WHERE
                type='table'
                AND name='schedule_rules'

            LIMIT 1
        """).fetchone()


        # Scheduler schema is created lazily.
        #
        # If this database has never initialized it,
        # there cannot be any GROUP schedule rules
        # blocking miner-group deletion.
        if table is None:
            return 0


        return int(
            conn.execute("""
                SELECT COUNT(*) AS count

                FROM schedule_rules

                WHERE
                    scope='GROUP'
                    AND group_id=?
            """, (
                int(group_id),
            )).fetchone()[
                "count"
            ]
        )

    finally:
        conn.close()


def list_schedulable_miners():
    conn = db()
    try:
        return list(
            conn.execute("""
                SELECT *
                FROM miners
                WHERE
                    enabled=1
                    AND schedule_enabled=1
                    AND driver IN (
                        'awesome',
                        'bitmain_stock'
                    )
            """).fetchall()
        )
    finally:
        conn.close()
