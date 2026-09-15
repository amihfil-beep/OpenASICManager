"""Pure anomaly detection policy, defaults and validation."""

import math


ANOMALY_INTERVAL = 30
ANOMALY_OFFLINE_GRACE = 180
ANOMALY_HOT_TEMP = 85.0
ANOMALY_HOT_CLEAR = 82.0
ANOMALY_HOT_GRACE = 180
ANOMALY_SCHEDULE_GRACE = 600


DEFAULT_ANOMALY_POLICY = {
    "interval_seconds": ANOMALY_INTERVAL,
    "offline_grace_seconds": ANOMALY_OFFLINE_GRACE,
    "hot_temp_c": ANOMALY_HOT_TEMP,
    "hot_clear_c": ANOMALY_HOT_CLEAR,
    "hot_grace_seconds": ANOMALY_HOT_GRACE,
    "schedule_grace_seconds": ANOMALY_SCHEDULE_GRACE,
}


ANOMALY_POLICY_FIELDS = tuple(
    DEFAULT_ANOMALY_POLICY.keys()
)


ANOMALY_POLICY_LIMITS = {
    "interval_seconds": (5, 3600),
    "offline_grace_seconds": (0, 86400),
    "hot_temp_c": (40.0, 120.0),
    "hot_clear_c": (30.0, 119.9),
    "hot_grace_seconds": (0, 86400),
    "schedule_grace_seconds": (0, 86400),
}


INTEGER_POLICY_FIELDS = {
    "interval_seconds",
    "offline_grace_seconds",
    "hot_grace_seconds",
    "schedule_grace_seconds",
}


def normalize_temperature(value):
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _normalize_policy_value(field, value):
    if isinstance(value, bool):
        raise ValueError(
            f"{field} must be numeric"
        )

    try:
        if field in INTEGER_POLICY_FIELDS:
            normalized = int(value)
            if float(value) != normalized:
                raise ValueError
        else:
            normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(
            f"{field} must be numeric"
        )

    if isinstance(normalized, float) and not math.isfinite(normalized):
        raise ValueError(
            f"{field} must be finite"
        )

    minimum, maximum = ANOMALY_POLICY_LIMITS[field]
    if normalized < minimum or normalized > maximum:
        raise ValueError(
            f"{field} must be between {minimum} and {maximum}"
        )

    return normalized


def normalize_anomaly_policy(data):
    if not isinstance(data, dict):
        raise ValueError(
            "Anomaly policy must be an object"
        )

    unknown = sorted(
        set(data) - set(ANOMALY_POLICY_FIELDS)
    )
    if unknown:
        raise ValueError(
            "Unknown anomaly policy fields: "
            + ", ".join(unknown)
        )

    missing = [
        field
        for field in ANOMALY_POLICY_FIELDS
        if field not in data
    ]
    if missing:
        raise ValueError(
            "Missing anomaly policy fields: "
            + ", ".join(missing)
        )

    normalized = {
        field: _normalize_policy_value(
            field,
            data[field],
        )
        for field in ANOMALY_POLICY_FIELDS
    }

    if normalized["hot_clear_c"] >= normalized["hot_temp_c"]:
        raise ValueError(
            "hot_clear_c must be lower than hot_temp_c"
        )

    return normalized


def offline_observed(state, active_control_action):
    return (
        state == "OFFLINE"
        and active_control_action != "reboot"
    )


def overheat_observed(
    temp_value,
    issue_active,
    hot_temp_c=ANOMALY_HOT_TEMP,
    hot_clear_c=ANOMALY_HOT_CLEAR,
):
    threshold = (
        hot_clear_c
        if issue_active
        else hot_temp_c
    )

    return (
        temp_value is not None
        and temp_value >= threshold
    )


def schedule_applicable(
    scheduler_enabled,
    desired,
    schedule_enabled,
    override_active,
    has_control_job,
):
    return (
        scheduler_enabled
        and desired is not None
        and bool(schedule_enabled)
        and not override_active
        and not bool(has_control_job)
    )


__all__ = (
    "ANOMALY_INTERVAL",
    "ANOMALY_OFFLINE_GRACE",
    "ANOMALY_HOT_TEMP",
    "ANOMALY_HOT_CLEAR",
    "ANOMALY_HOT_GRACE",
    "ANOMALY_SCHEDULE_GRACE",
    "DEFAULT_ANOMALY_POLICY",
    "ANOMALY_POLICY_FIELDS",
    "ANOMALY_POLICY_LIMITS",
    "normalize_anomaly_policy",
    "normalize_temperature",
    "offline_observed",
    "overheat_observed",
    "schedule_applicable",
)
