"""Lifecycle helpers for recurring maintenance occurrences."""

from datetime import datetime
from zoneinfo import ZoneInfo

import config as app_config


TIMEZONE_NAME = app_config.TIMEZONE
TIMEZONE = ZoneInfo(TIMEZONE_NAME)


def lifecycle_state(row, now):
    now = int(now)
    starts_at = int(row["starts_at"])
    ends_at = int(row["ends_at"])
    ended_at = row["ended_at"]

    if ended_at is not None and int(ended_at) < ends_at:
        return "CANCELLED"

    if ends_at <= now:
        return "COMPLETED"

    if starts_at <= now:
        return "ACTIVE"

    return "SCHEDULED"


def _iso(value):
    if value is None:
        return None

    return datetime.fromtimestamp(
        int(value),
        TIMEZONE,
    ).isoformat()


def occurrence_history_item(row, now):
    scope = str(row["scope"])

    if scope == "FARM":
        target = "FARM"
    elif scope == "MINER":
        target = f"MINER #{int(row['miner_id'])}"
        if row["miner_name"]:
            target += f" {row['miner_name']}"
    else:
        target = f"GROUP #{int(row['group_id'])}"
        if row["group_name"]:
            target += f" {row['group_name']}"

    return {
        "rule_id": int(row["rule_id"]),
        "occurrence_key": str(row["occurrence_key"]),
        "window_id": int(row["window_id"]),
        "scope": scope,
        "miner_id": row["miner_id"],
        "miner_name": row["miner_name"],
        "miner_ip": row["miner_ip"],
        "group_id": row["group_id"],
        "group_name": row["group_name"],
        "target": target,
        "starts_at": int(row["starts_at"]),
        "starts_at_iso": _iso(row["starts_at"]),
        "ends_at": int(row["ends_at"]),
        "ends_at_iso": _iso(row["ends_at"]),
        "materialized_at": int(row["materialized_at"]),
        "materialized_at_iso": _iso(row["materialized_at"]),
        "ended_at": row["ended_at"],
        "ended_at_iso": _iso(row["ended_at"]),
        "ended_by": row["ended_by"],
        "note": row["note"],
        "state": lifecycle_state(row, now),
        "started_event_at": row["started_event_at"],
        "started_event_at_iso": _iso(row["started_event_at"]),
        "completed_event_at": row["completed_event_at"],
        "completed_event_at_iso": _iso(row["completed_event_at"]),
        "cancelled_event_at": row["cancelled_event_at"],
        "cancelled_event_at_iso": _iso(row["cancelled_event_at"]),
        "rule_deleted": row["rule_deleted_at"] is not None,
    }


__all__ = (
    "TIMEZONE_NAME",
    "TIMEZONE",
    "lifecycle_state",
    "occurrence_history_item",
)
