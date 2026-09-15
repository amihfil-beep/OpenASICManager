"""Background anomaly detection service."""

import time
from datetime import datetime

from scheduler.policy import MOSCOW

from anomalies.policy import (
    normalize_temperature,
    offline_observed,
    overheat_observed,
    schedule_applicable,
)
from anomalies.repository import (
    active_issue_exists,
    anomaly_scan_snapshot,
    load_anomaly_policy,
    transition_anomaly_condition,
)


class AnomalyRuntime:
    def __init__(
        self,
        log_event,
        stop_event,
        desired_state,
    ):
        self.log_event = log_event
        self.stop_event = stop_event
        self.desired_state = desired_state

    def run(self):
        return anomaly_loop(self)


def set_anomaly_condition(
    runtime,
    miner,
    code,
    severity,
    observed,
    grace_seconds,
    message,
):
    opened, resolved = transition_anomaly_condition(
        miner=miner,
        code=code,
        severity=severity,
        observed=observed,
        grace_seconds=grace_seconds,
        message=message,
    )

    if opened:
        runtime.log_event(
            source="SYSTEM",
            action="ISSUE_OPEN",
            miner=miner,
            success=False,
            message=(
                f"{severity} {code}: "
                f"{message}"
            ),
        )

    if resolved:
        runtime.log_event(
            source="SYSTEM",
            action="ISSUE_RESOLVED",
            miner=miner,
            success=True,
            message=(
                f"{code}: {message}"
            ),
        )


def anomaly_desired_state(runtime):
    return runtime.desired_state(
        datetime.now(MOSCOW)
    )


def anomaly_scan(
    runtime,
    anomaly_policy=None,
):
    now = int(time.time())

    if anomaly_policy is None:
        anomaly_policy = load_anomaly_policy()

    scheduler_enabled, miners = (
        anomaly_scan_snapshot()
    )

    desired = anomaly_desired_state(
        runtime
    )

    for miner in miners:
        enabled = bool(
            miner["enabled"]
        )

        if not enabled:
            for code in (
                "OFFLINE",
                "OVERHEAT",
                "SCHEDULE_MISMATCH",
            ):
                set_anomaly_condition(
                    runtime=runtime,
                    miner=miner,
                    code=code,
                    severity="INFO",
                    observed=False,
                    grace_seconds=0,
                    message="ASIC disabled",
                )
            continue

        state = (
            miner["last_state"]
            or "UNKNOWN"
        )

        offline = offline_observed(
            state,
            miner["active_control_action"],
        )

        set_anomaly_condition(
            runtime=runtime,
            miner=miner,
            code="OFFLINE",
            severity="CRITICAL",
            observed=offline,
            grace_seconds=(
                anomaly_policy[
                    "offline_grace_seconds"
                ]
            ),
            message=(
                "ASIC is offline"
                if offline
                else f"ASIC reachable; state={state}"
            ),
        )

        temp_value = normalize_temperature(
            miner["temp"]
        )

        hot_active = active_issue_exists(
            miner["id"],
            "OVERHEAT",
        )

        hot = overheat_observed(
            temp_value,
            hot_active,
            hot_temp_c=(
                anomaly_policy[
                    "hot_temp_c"
                ]
            ),
            hot_clear_c=(
                anomaly_policy[
                    "hot_clear_c"
                ]
            ),
        )

        set_anomaly_condition(
            runtime=runtime,
            miner=miner,
            code="OVERHEAT",
            severity="CRITICAL",
            observed=hot,
            grace_seconds=(
                anomaly_policy[
                    "hot_grace_seconds"
                ]
            ),
            message=(
                f"Temperature {temp_value:.1f} C"
                if temp_value is not None
                else "Temperature unavailable"
            ),
        )

        override_active = bool(
            miner["manual_override_until"]
            and miner["manual_override_until"] > now
        )

        applicable = schedule_applicable(
            scheduler_enabled=scheduler_enabled,
            desired=desired,
            schedule_enabled=miner["schedule_enabled"],
            override_active=override_active,
            has_control_job=miner["has_control_job"],
        )

        mismatch = (
            state != desired
            if applicable
            else False
        )

        set_anomaly_condition(
            runtime=runtime,
            miner=miner,
            code="SCHEDULE_MISMATCH",
            severity="WARNING",
            observed=mismatch,
            grace_seconds=(
                anomaly_policy[
                    "schedule_grace_seconds"
                ]
            ),
            message=(
                f"Expected {desired}; actual {state}"
                if applicable
                else "Schedule condition not applicable"
            ),
        )


def anomaly_loop(runtime):
    anomaly_policy = load_anomaly_policy()

    if runtime.stop_event.wait(
        anomaly_policy["interval_seconds"]
    ):
        return

    while not runtime.stop_event.is_set():
        try:
            anomaly_policy = load_anomaly_policy()
            anomaly_scan(
                runtime,
                anomaly_policy=anomaly_policy,
            )
        except Exception as exc:
            runtime.log_event(
                source="SYSTEM",
                action="ANOMALY_ERROR",
                success=False,
                message=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )

        anomaly_policy = load_anomaly_policy()
        if runtime.stop_event.wait(
            anomaly_policy["interval_seconds"]
        ):
            return


__all__ = (
    "AnomalyRuntime",
    "set_anomaly_condition",
    "anomaly_desired_state",
    "anomaly_scan",
    "anomaly_loop",
)
