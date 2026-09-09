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
    "schedule_conflicting_rule",
    "next_transition",
)


def schedule_state_details(
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

        WHERE enabled=1
    """).fetchall()

    conn.close()


    winner_rule = None
    winner_occurrence = None


    # Seven days are enough for a weekly schedule.
    # Eight gives us one extra safe boundary day.

    for rule in rules:

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


            # A newly-created rule is never applied
            # retroactively to an occurrence that
            # happened before the rule existed.

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


            break


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


def desired_state(
    now=None,
):

    state, _, _ = (
        schedule_state_details(
            now
        )
    )

    return state


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
    """

    params = [
        normalized[
            "time_minutes"
        ],
        normalized[
            "days_mask"
        ],
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

        WHERE enabled=1
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
