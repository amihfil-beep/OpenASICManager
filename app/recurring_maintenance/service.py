"""Background materializer for recurring maintenance windows."""

from datetime import datetime, timedelta
import logging
import threading
import time

from db import get_miner
from recurring_maintenance.lifecycle_repository import (
    claim_lifecycle_events,
    ensure_lifecycle_schema,
)
from recurring_maintenance.policy import (
    TIMEZONE,
    occurrence_key,
    occurrences_between,
)
from recurring_maintenance.repository import (
    list_enabled_recurring_rules,
    materialize_occurrence,
)


LOGGER = logging.getLogger(__name__)
MATERIALIZER_INTERVAL = 30
MATERIALIZER_CATCHUP_DAYS = 7
MATERIALIZER_HEALTH_MAX_AGE = max(
    90,
    MATERIALIZER_INTERVAL * 3,
)


class MaterializerHealthState:
    def __init__(self):
        self._lock = threading.Lock()
        self._started_at = None
        self._last_success_at = None
        self._last_error_at = None
        self._last_error = None
        self._last_materialized_count = 0
        self._last_lifecycle_event_count = 0

    def reset(self):
        with self._lock:
            self._started_at = None
            self._last_success_at = None
            self._last_error_at = None
            self._last_error = None
            self._last_materialized_count = 0
            self._last_lifecycle_event_count = 0

    def mark_started(self, now=None):
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._started_at = timestamp

    def mark_success(
        self,
        materialized_count,
        lifecycle_event_count,
        now=None,
    ):
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._last_success_at = timestamp
            self._last_materialized_count = int(materialized_count)
            self._last_lifecycle_event_count = int(lifecycle_event_count)

    def mark_error(self, error, now=None):
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._last_error_at = timestamp
            self._last_error = str(error)

    def snapshot(self, now=None):
        timestamp = time.time() if now is None else float(now)

        with self._lock:
            started_at = self._started_at
            last_success_at = self._last_success_at
            last_error_at = self._last_error_at
            last_error = self._last_error
            last_materialized_count = self._last_materialized_count
            last_lifecycle_event_count = self._last_lifecycle_event_count

        age = (
            max(0.0, timestamp - last_success_at)
            if last_success_at is not None
            else None
        )

        healthy = bool(
            last_success_at is not None
            and age is not None
            and age <= MATERIALIZER_HEALTH_MAX_AGE
        )

        if healthy:
            status = "ok"
        elif started_at is None:
            status = "not_started"
        elif last_success_at is None:
            status = "error" if last_error else "starting"
        else:
            status = "stale"

        return {
            "status": status,
            "healthy": healthy,
            "max_age_seconds": MATERIALIZER_HEALTH_MAX_AGE,
            "started_at": started_at,
            "last_success_at": last_success_at,
            "last_success_age_seconds": age,
            "last_error_at": last_error_at,
            "last_error": last_error,
            "last_materialized_count": last_materialized_count,
            "last_lifecycle_event_count": last_lifecycle_event_count,
            "catchup_days": MATERIALIZER_CATCHUP_DAYS,
        }


MATERIALIZER_HEALTH = MaterializerHealthState()


def materializer_health_snapshot(now=None):
    return MATERIALIZER_HEALTH.snapshot(now)


def due_occurrences(rule, now):
    """Return active or recently missed occurrences valid for this rule version."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    local_now = now.astimezone(TIMEZONE)

    effective_epoch = int(
        rule["updated_at"]
        or rule["created_at"]
    )
    effective_since = datetime.fromtimestamp(
        effective_epoch,
        TIMEZONE,
    )

    catchup_since = (
        local_now
        - timedelta(days=MATERIALIZER_CATCHUP_DAYS)
    )

    starts_at = max(
        effective_since,
        catchup_since,
    )

    candidates = occurrences_between(
        rule,
        starts_at,
        local_now + timedelta(seconds=1),
    )

    return [
        (start, end)
        for start, end in candidates
        if start <= local_now
    ]


def materialize_due_occurrences(now=None, errors=None):
    if now is None:
        now = datetime.now(TIMEZONE)

    if errors is None:
        errors = []

    now_epoch = int(now.timestamp())
    created = []

    for rule in list_enabled_recurring_rules():
        for start, end in due_occurrences(rule, now):
            try:
                item = materialize_occurrence(
                    rule_id=rule["id"],
                    occurrence_key=occurrence_key(start),
                    starts_at=int(start.timestamp()),
                    ends_at=int(end.timestamp()),
                    now=now_epoch,
                )
            except Exception as exc:
                message = (
                    "Recurring maintenance rule "
                    f"#{rule['id']} materialization failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                errors.append(message)
                LOGGER.exception(message)
                continue

            if item is not None:
                created.append(item)

    return created


def _event_message(item):
    labels = {
        "MAINTENANCE_RECURRING_START": "started",
        "MAINTENANCE_RECURRING_COMPLETE": "completed",
        "MAINTENANCE_RECURRING_CANCEL": "cancelled",
    }

    return (
        "Recurring maintenance rule "
        f"#{item['rule_id']} window "
        f"#{item['window_id']} "
        f"{labels[item['action']]}"
    )


def _notification_action(item):
    if item["action"] == "MAINTENANCE_RECURRING_START":
        return "MAINTENANCE_CREATE"

    return "MAINTENANCE_END"


def recurring_maintenance_iteration(runtime, now=None):
    if now is None:
        now = datetime.now(TIMEZONE)

    ensure_lifecycle_schema()

    errors = []
    created = materialize_due_occurrences(
        now=now,
        errors=errors,
    )

    lifecycle_events = claim_lifecycle_events(
        int(now.timestamp())
    )

    if runtime.log_event is not None:
        for item in created:
            runtime.log_event(
                source="SCHEDULER",
                action="MAINTENANCE_RECURRING_MATERIALIZE",
                success=True,
                message=(
                    "Recurring maintenance rule "
                    f"#{item['rule_id']} created "
                    f"window #{item['window_id']}"
                ),
            )

        for item in lifecycle_events:
            miner = (
                get_miner(item["miner_id"])
                if item["miner_id"] is not None
                else None
            )

            runtime.log_event(
                source="SCHEDULER",
                action=_notification_action(item),
                miner=miner,
                success=True,
                message=_event_message(item),
            )

    if errors:
        raise RuntimeError(
            "; ".join(errors)
        )

    return {
        "materialized_count": len(created),
        "lifecycle_event_count": len(lifecycle_events),
    }


class RecurringMaintenanceRuntime:
    def __init__(self, stop_event, log_event=None):
        self.stop_event = stop_event
        self.log_event = log_event

    def run(self):
        MATERIALIZER_HEALTH.mark_started()

        while not self.stop_event.is_set():
            try:
                result = recurring_maintenance_iteration(
                    self
                )
            except Exception as exc:
                MATERIALIZER_HEALTH.mark_error(exc)
                LOGGER.exception(
                    "Recurring maintenance iteration failed; retrying in %s seconds",
                    MATERIALIZER_INTERVAL,
                )
            else:
                MATERIALIZER_HEALTH.mark_success(
                    materialized_count=(
                        result["materialized_count"]
                    ),
                    lifecycle_event_count=(
                        result["lifecycle_event_count"]
                    ),
                )

            self.stop_event.wait(
                MATERIALIZER_INTERVAL
            )


__all__ = (
    "MATERIALIZER_INTERVAL",
    "MATERIALIZER_CATCHUP_DAYS",
    "MATERIALIZER_HEALTH_MAX_AGE",
    "MATERIALIZER_HEALTH",
    "MaterializerHealthState",
    "materializer_health_snapshot",
    "due_occurrences",
    "materialize_due_occurrences",
    "recurring_maintenance_iteration",
    "RecurringMaintenanceRuntime",
)
