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


MINER_ANOMALY_OVERRIDE_FIELDS = (
    "offline_grace_seconds",
    "hot_temp_c",
    "hot_clear_c",
    "hot_grace_seconds",
    "schedule_grace_seconds",
)


GROUP_ANOMALY_OVERRIDE_FIELDS = (
    MINER_ANOMALY_OVERRIDE_FIELDS
)


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


def normalize_anomaly_overrides(data):
    if not isinstance(data, dict):
        raise ValueError(
            "Per-miner anomaly overrides must be an object"
        )

    unknown = sorted(
        set(data)
        -
        set(MINER_ANOMALY_OVERRIDE_FIELDS)
    )

    if unknown:
        raise ValueError(
            "Unknown per-miner anomaly policy fields: "
            + ", ".join(unknown)
        )

    return {
        field: _normalize_policy_value(
            field,
            value,
        )
        for field, value in data.items()
        if value is not None
    }


def normalize_group_anomaly_overrides(
    data,
):
    if not isinstance(data, dict):
        raise ValueError(
            "Group anomaly overrides must be an object"
        )

    unknown = sorted(
        set(data)
        -
        set(GROUP_ANOMALY_OVERRIDE_FIELDS)
    )

    if unknown:
        raise ValueError(
            "Unknown group anomaly policy fields: "
            + ", ".join(unknown)
        )

    return {
        field: _normalize_policy_value(
            field,
            value,
        )
        for field, value in data.items()
        if value is not None
    }


def resolve_anomaly_policy(
    global_policy,
    overrides=None,
):
    effective = normalize_anomaly_policy(
        dict(global_policy)
    )

    normalized_overrides = (
        normalize_anomaly_overrides(
            overrides or {}
        )
    )

    effective.update(
        normalized_overrides
    )

    return normalize_anomaly_policy(
        effective
    )


def resolve_layered_anomaly_policy(
    global_policy,
    group_overrides=None,
    miner_overrides=None,
):
    effective = normalize_anomaly_policy(
        dict(global_policy)
    )

    normalized_group = (
        normalize_group_anomaly_overrides(
            group_overrides or {}
        )
    )

    effective.update(
        normalized_group
    )

    # GROUP policy must itself remain a valid
    # effective policy for members that inherit it.
    effective = normalize_anomaly_policy(
        effective
    )

    normalized_miner = (
        normalize_anomaly_overrides(
            miner_overrides or {}
        )
    )

    effective.update(
        normalized_miner
    )

    return normalize_anomaly_policy(
        effective
    )


def apply_anomaly_override_patch(
    global_policy,
    current_overrides,
    patch,
):
    if not isinstance(patch, dict):
        raise ValueError(
            "Per-miner anomaly overrides must be an object"
        )

    if not patch:
        raise ValueError(
            "At least one anomaly override field is required"
        )

    unknown = sorted(
        set(patch)
        -
        set(MINER_ANOMALY_OVERRIDE_FIELDS)
    )

    if unknown:
        raise ValueError(
            "Unknown per-miner anomaly policy fields: "
            + ", ".join(unknown)
        )

    merged = dict(
        normalize_anomaly_overrides(
            current_overrides or {}
        )
    )

    for field, value in patch.items():

        if value is None:
            merged.pop(
                field,
                None,
            )

        else:
            merged[field] = (
                _normalize_policy_value(
                    field,
                    value,
                )
            )

    effective = resolve_anomaly_policy(
        global_policy,
        merged,
    )

    return merged, effective


def apply_group_anomaly_override_patch(
    global_policy,
    current_overrides,
    patch,
):
    if not isinstance(patch, dict):
        raise ValueError(
            "Group anomaly overrides must be an object"
        )

    if not patch:
        raise ValueError(
            "At least one group anomaly override "
            "field is required"
        )

    unknown = sorted(
        set(patch)
        -
        set(GROUP_ANOMALY_OVERRIDE_FIELDS)
    )

    if unknown:
        raise ValueError(
            "Unknown group anomaly policy fields: "
            + ", ".join(unknown)
        )

    merged = dict(
        normalize_group_anomaly_overrides(
            current_overrides or {}
        )
    )

    for field, value in patch.items():

        if value is None:
            merged.pop(
                field,
                None,
            )

        else:
            merged[field] = (
                _normalize_policy_value(
                    field,
                    value,
                )
            )

    effective = (
        resolve_layered_anomaly_policy(
            global_policy,
            group_overrides=merged,
            miner_overrides={},
        )
    )

    return merged, effective


def anomaly_policy_sources(
    overrides,
):
    normalized = (
        normalize_anomaly_overrides(
            overrides or {}
        )
    )

    return {
        field: (
            "OVERRIDE"
            if field in normalized
            else "GLOBAL"
        )
        for field
        in ANOMALY_POLICY_FIELDS
    }


def layered_anomaly_policy_sources(
    group_overrides,
    miner_overrides,
):
    normalized_group = (
        normalize_group_anomaly_overrides(
            group_overrides or {}
        )
    )

    normalized_miner = (
        normalize_anomaly_overrides(
            miner_overrides or {}
        )
    )

    result = {}

    for field in ANOMALY_POLICY_FIELDS:

        if field in normalized_miner:
            result[field] = "MINER"

        elif field in normalized_group:
            result[field] = "GROUP"

        else:
            result[field] = "GLOBAL"

    return result


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
    "MINER_ANOMALY_OVERRIDE_FIELDS",
    "GROUP_ANOMALY_OVERRIDE_FIELDS",
    "normalize_anomaly_policy",
    "normalize_anomaly_overrides",
    "normalize_group_anomaly_overrides",
    "resolve_anomaly_policy",
    "resolve_layered_anomaly_policy",
    "apply_anomaly_override_patch",
    "apply_group_anomaly_override_patch",
    "anomaly_policy_sources",
    "layered_anomaly_policy_sources",
    "normalize_temperature",
    "offline_observed",
    "overheat_observed",
    "schedule_applicable",
)
