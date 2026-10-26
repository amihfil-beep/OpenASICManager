"""Scheduler supervision and health heartbeat."""

import logging
import threading
import time

from scheduler.service import (
    SCHEDULER_INTERVAL,
    scheduler_iteration,
)


LOGGER = logging.getLogger(__name__)
SCHEDULER_HEALTH_MAX_AGE = max(
    60,
    SCHEDULER_INTERVAL * 3,
)


class SchedulerHealthState:
    def __init__(self):
        self._lock = threading.Lock()
        self._started_at = None
        self._last_success_at = None
        self._last_error_at = None
        self._last_error = None

    def reset(self):
        with self._lock:
            self._started_at = None
            self._last_success_at = None
            self._last_error_at = None
            self._last_error = None

    def mark_started(self, now=None):
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._started_at = timestamp

    def mark_success(self, now=None):
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._last_success_at = timestamp

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

        age = (
            max(0.0, timestamp - last_success_at)
            if last_success_at is not None
            else None
        )

        healthy = bool(
            last_success_at is not None
            and age is not None
            and age <= SCHEDULER_HEALTH_MAX_AGE
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
            "max_age_seconds": SCHEDULER_HEALTH_MAX_AGE,
            "started_at": started_at,
            "last_success_at": last_success_at,
            "last_success_age_seconds": age,
            "last_error_at": last_error_at,
            "last_error": last_error,
        }


SCHEDULER_HEALTH = SchedulerHealthState()


def scheduler_health_snapshot(now=None):
    return SCHEDULER_HEALTH.snapshot(now)


def scheduler_loop(runtime):
    """Run scheduler forever, retrying after iteration failures."""

    SCHEDULER_HEALTH.mark_started()

    while not runtime.stop_event.is_set():
        try:
            scheduler_iteration(runtime)
        except Exception as exc:
            SCHEDULER_HEALTH.mark_error(exc)
            LOGGER.exception(
                "Scheduler iteration failed; retrying in %s seconds",
                SCHEDULER_INTERVAL,
            )
        else:
            SCHEDULER_HEALTH.mark_success()

        runtime.stop_event.wait(
            SCHEDULER_INTERVAL
        )


__all__ = (
    "SCHEDULER_HEALTH",
    "SCHEDULER_HEALTH_MAX_AGE",
    "SchedulerHealthState",
    "scheduler_health_snapshot",
    "scheduler_loop",
)
