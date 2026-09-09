"""Pure anomaly detection policy and thresholds."""

ANOMALY_INTERVAL = 30
ANOMALY_OFFLINE_GRACE = 180
ANOMALY_HOT_TEMP = 85
ANOMALY_HOT_CLEAR = 82
ANOMALY_HOT_GRACE = 180
ANOMALY_SCHEDULE_GRACE = 600


def normalize_temperature(value):
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def offline_observed(state, active_control_action):
    return (
        state == "OFFLINE"
        and active_control_action != "reboot"
    )


def overheat_observed(temp_value, issue_active):
    threshold = (
        ANOMALY_HOT_CLEAR
        if issue_active
        else ANOMALY_HOT_TEMP
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
    "normalize_temperature",
    "offline_observed",
    "overheat_observed",
    "schedule_applicable",
)
