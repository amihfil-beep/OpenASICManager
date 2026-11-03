"""Bounded read model for active, upcoming and recent maintenance."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config as app_config
from operations_calendar.repository import (
    current_group_member_ids,
    list_calendar_windows,
)
from recurring_maintenance.policy import (
    occurrence_key,
    occurrences_between,
)
from recurring_maintenance.repository import (
    list_enabled_recurring_rules,
)


TIMEZONE_NAME = app_config.TIMEZONE
TIMEZONE = ZoneInfo(TIMEZONE_NAME)
CALENDAR_SCOPES = {
    "FARM",
    "GROUP",
    "MINER",
}


def _row_value(row, key, default=None):
    try:
        keys = row.keys()
    except AttributeError:
        keys = row

    if key not in keys:
        return default

    return row[key]


def _member_ids(row):
    csv = _row_value(
        row,
        "member_ids_csv",
    )

    if not csv:
        return []

    return [
        int(value)
        for value in str(csv).split(",")
        if value
    ]


def _status(row, now_epoch):
    if row["ended_at"] is not None:
        return "ENDED"

    if int(now_epoch) < int(row["starts_at"]):
        return "SCHEDULED"

    if int(now_epoch) < int(row["ends_at"]):
        return "ACTIVE"

    return "EXPIRED"


def _iso(epoch):
    return datetime.fromtimestamp(
        int(epoch),
        TIMEZONE,
    ).isoformat()


def _target_label(
    scope,
    miner_id=None,
    miner_name=None,
    group_id=None,
    group_name=None,
):
    if scope == "FARM":
        return "FARM"

    if scope == "MINER":
        label = f"MINER #{int(miner_id)}"
        if miner_name:
            label += f" {miner_name}"
        return label

    label = f"GROUP #{int(group_id)}"
    if group_name:
        label += f" {group_name}"
    return label


def _concrete_item(row, now_epoch):
    scope = str(row["scope"])
    group_members = (
        _member_ids(row)
        if scope == "GROUP"
        else []
    )

    affected_miner_ids = []
    if scope == "MINER" and row["miner_id"] is not None:
        affected_miner_ids = [
            int(row["miner_id"])
        ]
    elif scope == "GROUP":
        affected_miner_ids = group_members

    recurring_rule_id = _row_value(
        row,
        "recurring_rule_id",
    )

    source = (
        "RECURRING"
        if recurring_rule_id is not None
        else "ONE_OFF"
    )

    status = _status(
        row,
        now_epoch,
    )

    return {
        "id": f"window:{int(row['id'])}",
        "window_id": int(row["id"]),
        "materialized": True,
        "source": source,
        "rule_id": (
            int(recurring_rule_id)
            if recurring_rule_id is not None
            else None
        ),
        "occurrence_key": _row_value(
            row,
            "recurring_occurrence_key",
        ),
        "scope": scope,
        "miner_id": row["miner_id"],
        "miner_name": _row_value(
            row,
            "miner_name",
        ),
        "group_id": row["group_id"],
        "group_name": row["group_name"],
        "target": _target_label(
            scope=scope,
            miner_id=row["miner_id"],
            miner_name=_row_value(
                row,
                "miner_name",
            ),
            group_id=row["group_id"],
            group_name=row["group_name"],
        ),
        "starts_at": int(row["starts_at"]),
        "starts_at_iso": _iso(row["starts_at"]),
        "ends_at": int(row["ends_at"]),
        "ends_at_iso": _iso(row["ends_at"]),
        "ended_at": row["ended_at"],
        "ended_at_iso": (
            _iso(row["ended_at"])
            if row["ended_at"] is not None
            else None
        ),
        "status": status,
        "note": row["note"],
        "affects_all": scope == "FARM",
        "affected_miner_ids": affected_miner_ids,
        "member_count": (
            int(_row_value(row, "member_count", 0) or 0)
            if scope == "GROUP"
            else len(affected_miner_ids)
        ),
        "membership_mode": (
            "SNAPSHOT"
            if scope == "GROUP"
            else None
        ),
    }


def _rule_applies_to_miner(
    rule,
    miner_id,
    group_members,
):
    if miner_id is None:
        return True

    if rule["scope"] == "FARM":
        return True

    if rule["scope"] == "MINER":
        return int(rule["miner_id"]) == int(miner_id)

    return int(miner_id) in group_members


def _forecast_items(
    now,
    upcoming_until,
    scope=None,
    miner_id=None,
    existing_occurrences=None,
):
    existing_occurrences = (
        existing_occurrences
        or set()
    )

    items = []

    for rule in list_enabled_recurring_rules():
        rule_scope = str(rule["scope"])

        if (
            scope is not None
            and rule_scope != scope
        ):
            continue

        group_members = []
        if rule_scope == "GROUP":
            group_members = current_group_member_ids(
                rule["group_id"]
            )

        if not _rule_applies_to_miner(
            rule,
            miner_id,
            group_members,
        ):
            continue

        for start, end in occurrences_between(
            rule,
            now,
            upcoming_until,
        ):
            key = occurrence_key(start)
            identity = (
                int(rule["id"]),
                key,
            )

            if identity in existing_occurrences:
                continue

            affected_miner_ids = []
            if rule_scope == "MINER":
                affected_miner_ids = [
                    int(rule["miner_id"])
                ]
            elif rule_scope == "GROUP":
                affected_miner_ids = list(
                    group_members
                )

            items.append({
                "id": (
                    "forecast:"
                    f"{int(rule['id'])}:"
                    f"{key}"
                ),
                "window_id": None,
                "materialized": False,
                "source": "RECURRING",
                "rule_id": int(rule["id"]),
                "occurrence_key": key,
                "scope": rule_scope,
                "miner_id": rule["miner_id"],
                "miner_name": rule["miner_name"],
                "group_id": rule["group_id"],
                "group_name": rule["group_name"],
                "target": _target_label(
                    scope=rule_scope,
                    miner_id=rule["miner_id"],
                    miner_name=rule["miner_name"],
                    group_id=rule["group_id"],
                    group_name=rule["group_name"],
                ),
                "starts_at": int(start.timestamp()),
                "starts_at_iso": start.isoformat(),
                "ends_at": int(end.timestamp()),
                "ends_at_iso": end.isoformat(),
                "ended_at": None,
                "ended_at_iso": None,
                "status": "SCHEDULED",
                "note": rule["note"],
                "affects_all": rule_scope == "FARM",
                "affected_miner_ids": affected_miner_ids,
                "member_count": (
                    len(affected_miner_ids)
                    if rule_scope == "GROUP"
                    else len(affected_miner_ids)
                ),
                "membership_mode": (
                    "CURRENT_PREVIEW"
                    if rule_scope == "GROUP"
                    else None
                ),
            })

    return items


def operations_calendar_report(
    now=None,
    upcoming_days=14,
    recent_days=7,
    scope=None,
    miner_id=None,
    limit=200,
):
    if now is None:
        now = datetime.now(TIMEZONE)

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    local_now = now.astimezone(TIMEZONE)
    now_epoch = int(local_now.timestamp())

    upcoming_days = max(
        1,
        min(int(upcoming_days), 90),
    )
    recent_days = max(
        1,
        min(int(recent_days), 90),
    )
    limit = max(
        1,
        min(int(limit), 500),
    )

    normalized_scope = None
    if scope is not None:
        normalized_scope = str(scope).strip().upper()
        if normalized_scope == "ALL":
            normalized_scope = None
        elif normalized_scope not in CALENDAR_SCOPES:
            raise ValueError(
                "scope must be ALL, FARM, GROUP or MINER"
            )

    if miner_id is not None:
        miner_id = int(miner_id)
        if miner_id <= 0:
            raise ValueError("miner_id must be positive")

    upcoming_until = (
        local_now
        + timedelta(days=upcoming_days)
    )
    recent_since = (
        local_now
        - timedelta(days=recent_days)
    )

    rows = list_calendar_windows(
        starts_before=int(
            upcoming_until.timestamp()
        ),
        relevant_after=int(
            recent_since.timestamp()
        ),
        scope=normalized_scope,
        miner_id=miner_id,
        limit=limit,
    )

    concrete = [
        _concrete_item(
            row,
            now_epoch,
        )
        for row in rows
    ]

    existing_occurrences = {
        (
            int(item["rule_id"]),
            str(item["occurrence_key"]),
        )
        for item in concrete
        if (
            item["rule_id"] is not None
            and item["occurrence_key"]
        )
    }

    forecast = _forecast_items(
        now=local_now,
        upcoming_until=upcoming_until,
        scope=normalized_scope,
        miner_id=miner_id,
        existing_occurrences=existing_occurrences,
    )

    active = [
        item
        for item in concrete
        if item["status"] == "ACTIVE"
    ]

    upcoming = [
        item
        for item in concrete
        if item["status"] == "SCHEDULED"
    ]
    upcoming.extend(forecast)

    recent = [
        item
        for item in concrete
        if item["status"] in {
            "ENDED",
            "EXPIRED",
        }
    ]

    active.sort(
        key=lambda item: (
            item["starts_at"],
            item["id"],
        )
    )
    upcoming.sort(
        key=lambda item: (
            item["starts_at"],
            item["id"],
        )
    )
    recent.sort(
        key=lambda item: (
            item["starts_at"],
            item["id"],
        ),
        reverse=True,
    )

    active = active[:limit]
    upcoming = upcoming[:limit]
    recent = recent[:limit]

    return {
        "timezone": TIMEZONE_NAME,
        "now": local_now.isoformat(),
        "filters": {
            "scope": (
                normalized_scope
                or "ALL"
            ),
            "miner_id": miner_id,
            "upcoming_days": upcoming_days,
            "recent_days": recent_days,
            "limit": limit,
        },
        "counts": {
            "active": len(active),
            "upcoming": len(upcoming),
            "recent": len(recent),
        },
        "active": active,
        "upcoming": upcoming,
        "recent": recent,
    }


__all__ = (
    "TIMEZONE_NAME",
    "CALENDAR_SCOPES",
    "operations_calendar_report",
)
