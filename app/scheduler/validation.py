"""
Scheduler API input validation.

Contains normalization and validation helpers used by the
HTTP API when schedule rules are created or updated.

No database access or scheduler execution occurs here.
"""

from fastapi import HTTPException


__all__ = (
    "schedule_parse_bool",
    "schedule_normalize_input",
)


def schedule_parse_bool(
    value,
):

    if isinstance(
        value,
        bool
    ):
        return value


    if isinstance(
        value,
        int
    ):
        return bool(
            value
        )


    return str(
        value
    ).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def schedule_normalize_input(
    data,
    current=None,
):

    if not isinstance(
        data,
        dict
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid JSON body",
        )


    def current_value(
        name,
        default=None,
    ):

        if current is None:
            return default

        return current[
            name
        ]


    action = str(
        data.get(
            "action",
            current_value(
                "action",
                "PAUSE",
            ),
        )
    ).strip().upper()


    if action not in (
        "PAUSE",
        "RESUME",
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Action must be "
                "PAUSE or RESUME"
            ),
        )


    if "time_minutes" in data:

        try:

            time_minutes = int(
                data[
                    "time_minutes"
                ]
            )

        except Exception:

            raise HTTPException(
                status_code=400,
                detail="Invalid time",
            )


    elif "time" in data:

        try:

            hour_text, minute_text = (
                str(
                    data["time"]
                )
                .strip()
                .split(
                    ":",
                    1,
                )
            )

            time_minutes = (
                int(
                    hour_text
                )
                * 60
                +
                int(
                    minute_text
                )
            )

        except Exception:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Time must be HH:MM"
                ),
            )


    else:

        time_minutes = int(
            current_value(
                "time_minutes",
                420,
            )
        )


    if not (
        0
        <=
        time_minutes
        <=
        1439
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid time",
        )


    try:

        days_mask = int(
            data.get(
                "days_mask",
                current_value(
                    "days_mask",
                    31,
                ),
            )
        )

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid days",
        )


    if not (
        1
        <=
        days_mask
        <=
        127
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Select at least one day"
            ),
        )


    enabled = schedule_parse_bool(
        data.get(
            "enabled",
            current_value(
                "enabled",
                1,
            ),
        )
    )


    comment = str(
        data.get(
            "comment",
            current_value(
                "comment",
                "",
            ),
        )
        or ""
    ).strip()


    if len(
        comment
    ) > 120:

        raise HTTPException(
            status_code=400,
            detail=(
                "Comment is limited "
                "to 120 characters"
            ),
        )


    return {
        "enabled":
            enabled,

        "action":
            action,

        "time_minutes":
            time_minutes,

        "days_mask":
            days_mask,

        "scope":
            "SCHEDULED",

        "comment":
            comment,
    }
