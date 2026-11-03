"""Validation and time calculations for recurring maintenance."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config as app_config
from maintenance_windows.policy import normalize_maintenance_note


TIMEZONE_NAME = app_config.TIMEZONE
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

RECURRING_SCOPES = {
    "FARM",
    "MINER",
    "GROUP",
}

MAX_DURATION_MINUTES = 7 * 24 * 60
ALL_WEEKDAYS_MASK = 0b1111111


def _positive_id(value, field):
    try:
        result = int(value)
    except Exception:
        raise ValueError(f"{field} is required")

    if result <= 0:
        raise ValueError(f"{field} must be positive")

    return result


def _integer(value, field):
    try:
        return int(value)
    except Exception:
        raise ValueError(f"{field} must be an integer")


def _boolean(value, field):
    if isinstance(value, bool):
        return value

    if value in (0, 1):
        return bool(value)

    raise ValueError(f"{field} must be boolean")


def normalize_recurring_rule(payload, current=None):
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")

    allowed = {
        "enabled",
        "scope",
        "miner_id",
        "group_id",
        "days_mask",
        "start_minutes",
        "duration_minutes",
        "note",
    }

    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(
            "Unknown recurring maintenance fields: "
            + ", ".join(unknown)
        )

    base = {}
    if current is not None:
        for field in allowed:
            if field in current.keys():
                base[field] = current[field]

    values = {**base, **payload}

    scope = str(values.get("scope", "")).strip().upper()
    if scope not in RECURRING_SCOPES:
        raise ValueError("scope must be FARM, MINER or GROUP")

    days_mask = _integer(values.get("days_mask"), "days_mask")
    if days_mask <= 0 or days_mask > ALL_WEEKDAYS_MASK:
        raise ValueError("days_mask must select at least one weekday")

    start_minutes = _integer(
        values.get("start_minutes"),
        "start_minutes",
    )
    if start_minutes < 0 or start_minutes >= 24 * 60:
        raise ValueError("start_minutes must be between 0 and 1439")

    duration_minutes = _integer(
        values.get("duration_minutes"),
        "duration_minutes",
    )
    if duration_minutes <= 0 or duration_minutes > MAX_DURATION_MINUTES:
        raise ValueError(
            "duration_minutes must be between 1 and 10080"
        )

    enabled = _boolean(values.get("enabled", True), "enabled")

    miner_id = values.get("miner_id")
    group_id = values.get("group_id")

    if scope == "FARM":
        if miner_id is not None:
            raise ValueError("miner_id must be null for FARM scope")
        if group_id is not None:
            raise ValueError("group_id must be null for FARM scope")
        miner_id = None
        group_id = None

    elif scope == "MINER":
        if group_id is not None:
            raise ValueError("group_id must be null for MINER scope")
        miner_id = _positive_id(miner_id, "miner_id")
        group_id = None

    else:
        if miner_id is not None:
            raise ValueError("miner_id must be null for GROUP scope")
        group_id = _positive_id(group_id, "group_id")
        miner_id = None

    return {
        "enabled": enabled,
        "scope": scope,
        "miner_id": miner_id,
        "group_id": group_id,
        "days_mask": days_mask,
        "start_minutes": start_minutes,
        "duration_minutes": duration_minutes,
        "note": normalize_maintenance_note(values.get("note")),
    }


def _local_start(rule, local_date):
    midnight = datetime(
        local_date.year,
        local_date.month,
        local_date.day,
        tzinfo=TIMEZONE,
    )

    return midnight + timedelta(
        minutes=int(rule["start_minutes"])
    )


def _weekday_selected(rule, weekday):
    return bool(
        int(rule["days_mask"])
        & (1 << int(weekday))
    )


def occurrence_key(start):
    return start.strftime("%Y-%m-%dT%H:%M%z")


def active_occurrences(rule, now):
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    local_now = now.astimezone(TIMEZONE)
    duration = timedelta(
        minutes=int(rule["duration_minutes"])
    )

    results = []

    for days_back in range(0, 8):
        local_date = (
            local_now.date()
            - timedelta(days=days_back)
        )

        if not _weekday_selected(
            rule,
            local_date.weekday(),
        ):
            continue

        start = _local_start(rule, local_date)
        end = start + duration

        if start <= local_now < end:
            results.append((start, end))

    results.sort(key=lambda item: item[0])
    return results


def next_occurrence(rule, now):
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    local_now = now.astimezone(TIMEZONE)

    for days_forward in range(0, 8):
        local_date = (
            local_now.date()
            + timedelta(days=days_forward)
        )

        if not _weekday_selected(
            rule,
            local_date.weekday(),
        ):
            continue

        start = _local_start(rule, local_date)
        if start >= local_now:
            return start

    return None


def occurrences_between(rule, starts_at, ends_before):
    """Return recurring occurrences whose local start is in a bounded range."""

    if starts_at.tzinfo is None or ends_before.tzinfo is None:
        raise ValueError("occurrence range must be timezone-aware")

    local_start = starts_at.astimezone(TIMEZONE)
    local_end = ends_before.astimezone(TIMEZONE)

    if local_end <= local_start:
        return []

    duration = timedelta(
        minutes=int(rule["duration_minutes"])
    )

    day_count = (
        local_end.date()
        - local_start.date()
    ).days + 1

    results = []

    for day_offset in range(day_count):
        local_date = (
            local_start.date()
            + timedelta(days=day_offset)
        )

        if not _weekday_selected(
            rule,
            local_date.weekday(),
        ):
            continue

        occurrence_start = _local_start(
            rule,
            local_date,
        )

        if not (
            local_start
            <= occurrence_start
            < local_end
        ):
            continue

        results.append((
            occurrence_start,
            occurrence_start + duration,
        ))

    results.sort(key=lambda item: item[0])
    return results


__all__ = (
    "TIMEZONE_NAME",
    "TIMEZONE",
    "RECURRING_SCOPES",
    "MAX_DURATION_MINUTES",
    "normalize_recurring_rule",
    "active_occurrences",
    "next_occurrence",
    "occurrences_between",
    "occurrence_key",
)
