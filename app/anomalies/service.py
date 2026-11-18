"""Background anomaly detection service."""

import json
import logging
from pathlib import Path
import time
from datetime import datetime

import config as app_config
from monitoring.connectivity import suppress_individual_offline

LOGGER = logging.getLogger(__name__)

from scheduler.policy import MOSCOW

from maintenance_windows.repository import (
    active_maintenance_snapshot,
)

from anomalies.policy import (
    normalize_temperature,
    offline_observed,
    overheat_observed,
    resolve_layered_anomaly_policy,
    schedule_applicable,
)
from anomalies.repository import (
    active_issue_exists,
    anomaly_scan_snapshot,
    load_anomaly_override_snapshot,
    load_anomaly_policy,
    load_group_anomaly_override_snapshot,
    transition_anomaly_condition,
)


def aggregated_offline_alert_sent(miners):
    """Fail open to legacy per-device alerts when aggregate delivery fails."""
    if not app_config.env_bool("OAM_WATCHDOG_ENABLED", False):
        return False

    path = Path("/var/lib/openasicmanager/connectivity-watchdog.json")
    try:
        if path.is_symlink() or path.stat().st_size > 8192:
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return False

    return suppress_individual_offline(miners, data)


class AnomalyRuntime:
    def __init__(
        self,
        log_event,
        stop_event,
        effective_schedule_states,
    ):
        self.log_event = log_event
        self.stop_event = stop_event
        self.effective_schedule_states = (
            effective_schedule_states
        )

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
    suppress_new=False,
):
    opened, resolved = transition_anomaly_condition(
        miner=miner,
        code=code,
        severity=severity,
        observed=observed,
        grace_seconds=grace_seconds,
        message=message,
        suppress_new=suppress_new,
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


def anomaly_schedule_states(
    runtime,
    miners,
):

    return (
        runtime.effective_schedule_states(
            miners,
            datetime.now(
                MOSCOW
            ),
        )
    )


def anomaly_scan(
    runtime,
    anomaly_policy=None,
):
    now = int(time.time())

    if anomaly_policy is None:
        anomaly_policy = load_anomaly_policy()

    group_override_snapshot = (
        load_group_anomaly_override_snapshot(
            anomaly_policy
        )
    )

    override_snapshot = (
        load_anomaly_override_snapshot(
            anomaly_policy,
            group_snapshot=(
                group_override_snapshot
            ),
        )
    )

    scheduler_enabled, miners = (
        anomaly_scan_snapshot()
    )

    # Only an already-delivered fleet alarm may suppress new
    # per-device OFFLINE issues. Existing issues still resolve.
    # Missing state, failed Telegram or disabled watchdog preserve
    # the original issue notification path.
    mass_offline_alert_sent = aggregated_offline_alert_sent(miners)

    maintenance = (
        active_maintenance_snapshot(
            now
        )
    )

    schedule_states = (
        anomaly_schedule_states(
            runtime,
            miners,
        )
    )

    for miner in miners:

        schedule_details = (
            schedule_states.get(
                int(
                    miner["id"]
                ),
                {},
            )
        )

        desired = (
            schedule_details.get(
                "desired_state"
            )
        )

        group_overrides = (
            group_override_snapshot.get(
                int(miner["group_id"]),
                {},
            )
            if miner["group_id"]
            is not None
            else {}
        )

        effective_policy = (
            resolve_layered_anomaly_policy(
                anomaly_policy,
                group_overrides=(
                    group_overrides
                ),
                miner_overrides=(
                    override_snapshot.get(
                        int(miner["id"]),
                        {},
                    )
                ),
            )
        )

        maintenance_active = (
            maintenance["farm_active"]
            or
            miner["id"]
            in maintenance["miner_ids"]
        )

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
                    suppress_new=maintenance_active,
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
                effective_policy[
                    "offline_grace_seconds"
                ]
            ),
            message=(
                "ASIC is offline"
                if offline
                else f"ASIC reachable; state={state}"
            ),
            suppress_new=(
                maintenance_active
                or mass_offline_alert_sent
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
                effective_policy[
                    "hot_temp_c"
                ]
            ),
            hot_clear_c=(
                effective_policy[
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
                effective_policy[
                    "hot_grace_seconds"
                ]
            ),
            message=(
                f"Temperature {temp_value:.1f} C"
                if temp_value is not None
                else "Temperature unavailable"
            ),
            suppress_new=maintenance_active,
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
                effective_policy[
                    "schedule_grace_seconds"
                ]
            ),
            message=(
                f"Expected {desired}; actual {state}"
                if applicable
                else "Schedule condition not applicable"
            ),
            suppress_new=maintenance_active,
        )


def anomaly_loop(runtime):
    """Retry database failures without losing anomaly monitoring."""
    initial_delay_pending = True

    while not runtime.stop_event.is_set():
        retry_seconds = 30
        try:
            anomaly_policy = load_anomaly_policy()
            retry_seconds = anomaly_policy["interval_seconds"]

            if initial_delay_pending:
                initial_delay_pending = False
                if runtime.stop_event.wait(retry_seconds):
                    return

            anomaly_scan(
                runtime,
                anomaly_policy=anomaly_policy,
            )
        except Exception as exc:
            LOGGER.exception(
                "Anomaly iteration failed; retrying in %s seconds",
                retry_seconds,
            )
            try:
                runtime.log_event(
                    source="SYSTEM",
                    action="ANOMALY_ERROR",
                    success=False,
                    message=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                # The audit log also depends on SQLite. Its failure
                # must not kill the anomaly monitoring thread.
                LOGGER.exception("Unable to persist anomaly error")

        if runtime.stop_event.wait(retry_seconds):
            return


__all__ = (
    "AnomalyRuntime",
    "set_anomaly_condition",
    "anomaly_schedule_states",
    "anomaly_scan",
    "anomaly_loop",
)
