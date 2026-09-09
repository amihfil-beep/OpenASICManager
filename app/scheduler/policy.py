"""
Scheduler rule policy.

Contains deterministic schedule formatting and rule-time
calculations. This module does not access the database and
does not start scheduler threads.
"""

from datetime import (
    datetime,
    timedelta,
)

from zoneinfo import ZoneInfo

import config as app_config


TIMEZONE_NAME = (
    app_config.TIMEZONE
)

MOSCOW = ZoneInfo(
    TIMEZONE_NAME
)


SCHEDULE_DAY_NAMES = (
    "Mon",
    "Tue",
    "Wed",
    "Thu",
    "Fri",
    "Sat",
    "Sun",
)


__all__ = (
    "schedule_time_string",
    "schedule_days_string",
    "schedule_action_state",
    "schedule_rule_next_run",
    "schedule_rule_dict",
)


def schedule_time_string(
    time_minutes,
):

    value = int(
        time_minutes
    )

    hour = (
        value
        // 60
    )

    minute = (
        value
        % 60
    )

    return (
        f"{hour:02d}:"
        f"{minute:02d}"
    )


def schedule_days_string(
    days_mask,
):

    mask = int(
        days_mask
    )


    if mask == 127:
        return "Daily"

    if mask == 31:
        return "Mon-Fri"

    if mask == 96:
        return "Sat-Sun"


    result = []

    for index, name in enumerate(
        SCHEDULE_DAY_NAMES
    ):

        if mask & (
            1 << index
        ):

            result.append(
                name
            )


    return ",".join(
        result
    )


def schedule_action_state(
    action,
):

    action = str(
        action
    ).upper()


    if action == "RESUME":
        return "MINING"

    if action == "PAUSE":
        return "PAUSED"


    return None


def schedule_rule_next_run(
    rule,
    now=None,
):

    if now is None:

        now = datetime.now(
            MOSCOW
        )


    if not bool(
        rule["enabled"]
    ):
        return None


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


    for offset in range(
        0,
        9,
    ):

        day = (
            now
            +
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


        candidate = datetime(
            day.year,
            day.month,
            day.day,
            hour,
            minute,
            0,
            tzinfo=MOSCOW,
        )


        if candidate > now:

            return candidate


    return None


def schedule_rule_dict(
    rule,
    now=None,
):

    if now is None:

        now = datetime.now(
            MOSCOW
        )


    next_run = (
        schedule_rule_next_run(
            rule,
            now
        )
    )


    return {
        "id":
            int(
                rule["id"]
            ),

        "enabled":
            bool(
                rule["enabled"]
            ),

        "action":
            str(
                rule["action"]
            ),

        "time_minutes":
            int(
                rule["time_minutes"]
            ),

        "time":
            schedule_time_string(
                rule["time_minutes"]
            ),

        "days_mask":
            int(
                rule["days_mask"]
            ),

        "days":
            schedule_days_string(
                rule["days_mask"]
            ),

        "scope":
            str(
                rule["scope"]
            ),

        "comment":
            str(
                rule["comment"]
                or ""
            ),

        "effective_from":
            int(
                rule["effective_from"]
                or 0
            ),

        "next_run":
            (
                next_run.isoformat()
                if next_run
                else None
            ),

        "next_run_label":
            (
                (
                    next_run.strftime(
                        "%a %d.%m %H:%M"
                    )
                    +
                    " "
                    +
                    TIMEZONE_NAME
                )
                if next_run
                else None
            ),
    }
