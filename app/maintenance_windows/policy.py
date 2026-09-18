"""Validation and read-model helpers for maintenance windows."""

from datetime import datetime
from zoneinfo import ZoneInfo

import config as app_config


TIMEZONE_NAME = app_config.TIMEZONE
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

MAINTENANCE_SCOPES = {
    "FARM",
    "MINER",
}

MAX_MAINTENANCE_DURATION_SECONDS = (
    7 * 24 * 60 * 60
)

MAX_MAINTENANCE_NOTE_LENGTH = 500


def normalize_maintenance_note(value):
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError(
            "note must be a string or null"
        )

    note = value.strip()

    if len(note) > MAX_MAINTENANCE_NOTE_LENGTH:
        raise ValueError(
            "note must be at most "
            f"{MAX_MAINTENANCE_NOTE_LENGTH} characters"
        )

    return note or None


def _integer(value, field):
    try:
        return int(value)
    except Exception:
        raise ValueError(
            f"{field} must be an integer epoch timestamp"
        )


def normalize_maintenance_create(
    payload,
    now,
):
    if not isinstance(payload, dict):
        raise ValueError(
            "JSON body must be an object"
        )

    allowed = {
        "scope",
        "miner_id",
        "starts_at",
        "ends_at",
        "note",
    }

    unknown = sorted(
        set(payload) - allowed
    )

    if unknown:
        raise ValueError(
            "Unknown maintenance fields: "
            + ", ".join(unknown)
        )

    scope = str(
        payload.get(
            "scope",
            "",
        )
    ).strip().upper()

    if scope not in MAINTENANCE_SCOPES:
        raise ValueError(
            "scope must be FARM or MINER"
        )

    if "ends_at" not in payload:
        raise ValueError(
            "ends_at is required"
        )

    starts_at = _integer(
        payload.get(
            "starts_at",
            now,
        ),
        "starts_at",
    )

    ends_at = _integer(
        payload["ends_at"],
        "ends_at",
    )

    if ends_at <= starts_at:
        raise ValueError(
            "ends_at must be later than starts_at"
        )

    if ends_at <= int(now):
        raise ValueError(
            "ends_at must be in the future"
        )

    duration = (
        ends_at
        - starts_at
    )

    if duration > MAX_MAINTENANCE_DURATION_SECONDS:
        raise ValueError(
            "maintenance duration must not exceed 7 days"
        )

    miner_id = payload.get(
        "miner_id"
    )

    if scope == "FARM":
        if miner_id is not None:
            raise ValueError(
                "miner_id must be null for FARM scope"
            )

        miner_id = None

    else:
        try:
            miner_id = int(
                miner_id
            )
        except Exception:
            raise ValueError(
                "miner_id is required for MINER scope"
            )

        if miner_id <= 0:
            raise ValueError(
                "miner_id must be positive"
            )

    return {
        "scope": scope,
        "miner_id": miner_id,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "note": normalize_maintenance_note(
            payload.get("note")
        ),
    }


def normalize_maintenance_extend(
    payload,
    current,
    now,
):
    if not isinstance(payload, dict):
        raise ValueError(
            "JSON body must be an object"
        )

    allowed = {
        "ends_at",
        "note",
    }

    unknown = sorted(
        set(payload) - allowed
    )

    if unknown:
        raise ValueError(
            "Unknown maintenance fields: "
            + ", ".join(unknown)
        )

    if current["ended_at"] is not None:
        raise ValueError(
            "maintenance window is already ended"
        )

    if int(current["ends_at"]) <= int(now):
        raise ValueError(
            "expired maintenance window cannot be extended"
        )

    if "ends_at" not in payload:
        raise ValueError(
            "ends_at is required"
        )

    ends_at = _integer(
        payload["ends_at"],
        "ends_at",
    )

    if ends_at <= int(current["ends_at"]):
        raise ValueError(
            "ends_at must extend the current window"
        )

    if (
        ends_at
        - int(current["starts_at"])
        >
        MAX_MAINTENANCE_DURATION_SECONDS
    ):
        raise ValueError(
            "maintenance duration must not exceed 7 days"
        )

    note = (
        normalize_maintenance_note(
            payload["note"]
        )
        if "note" in payload
        else current["note"]
    )

    return {
        "ends_at": ends_at,
        "note": note,
    }


def maintenance_status(
    row,
    now,
):
    if row["ended_at"] is not None:
        return "ENDED"

    if int(now) < int(row["starts_at"]):
        return "SCHEDULED"

    if int(now) < int(row["ends_at"]):
        return "ACTIVE"

    return "EXPIRED"


def maintenance_time_iso(value):
    if value is None:
        return None

    return datetime.fromtimestamp(
        int(value),
        TIMEZONE,
    ).isoformat()


def maintenance_window_dict(
    row,
    now,
):
    status = maintenance_status(
        row,
        now,
    )

    return {
        "id": int(row["id"]),
        "scope": row["scope"],
        "miner_id": row["miner_id"],
        "miner_name": row["miner_name"],
        "miner_ip": row["miner_ip"],
        "starts_at": int(row["starts_at"]),
        "starts_at_iso": maintenance_time_iso(
            row["starts_at"]
        ),
        "ends_at": int(row["ends_at"]),
        "ends_at_iso": maintenance_time_iso(
            row["ends_at"]
        ),
        "ended_at": row["ended_at"],
        "ended_at_iso": maintenance_time_iso(
            row["ended_at"]
        ),
        "note": row["note"],
        "created_by": row["created_by"],
        "created_at": int(row["created_at"]),
        "created_at_iso": maintenance_time_iso(
            row["created_at"]
        ),
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
        "ended_by": row["ended_by"],
        "status": status,
        "active": status == "ACTIVE",
    }


__all__ = (
    "TIMEZONE_NAME",
    "MAX_MAINTENANCE_DURATION_SECONDS",
    "MAX_MAINTENANCE_NOTE_LENGTH",
    "normalize_maintenance_note",
    "normalize_maintenance_create",
    "normalize_maintenance_extend",
    "maintenance_status",
    "maintenance_window_dict",
)
