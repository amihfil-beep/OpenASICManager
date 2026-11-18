"""Miner polling, failure recovery and monitoring health."""

import logging
import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)

from control.dispatch import read_status
from db import (
    get_miner,
    get_poll_miners,
)
from monitoring.repository import (
    save_miner_status,
    mark_miner_offline,
)


LOGGER = logging.getLogger(__name__)
POLL_INTERVAL = 15
MONITORING_HEALTH_MAX_AGE = max(60, POLL_INTERVAL * 3)


class MonitoringHealthState:
    """Record completed polling cycles separately from manual status reads."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self._started_at = None
            self._last_success_at = None
            self._last_error_at = None
            self._last_error = None
            self._last_polled_miners = None

    def mark_started(self, now=None):
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._started_at = timestamp

    def mark_success(self, miner_count, now=None):
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._last_success_at = timestamp
            self._last_polled_miners = int(miner_count)

    def mark_error(self, error, now=None):
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._last_error_at = timestamp
            self._last_error = (
                f"{type(error).__name__}: {error}"
            )

    def snapshot(self, now=None):
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            started = self._started_at
            success = self._last_success_at
            error_at = self._last_error_at
            error = self._last_error
            miners = self._last_polled_miners

        age = (
            max(0.0, timestamp - success)
            if success is not None
            else None
        )
        recent_success = bool(
            age is not None
            and age <= MONITORING_HEALTH_MAX_AGE
        )
        unhandled_failure = bool(
            error_at is not None
            and (success is None or error_at > success)
        )
        healthy = recent_success and not unhandled_failure

        if healthy:
            status = "ok"
        elif started is None:
            status = "not_started"
        elif unhandled_failure:
            status = "error"
        elif success is None:
            status = "starting"
        else:
            status = "stale"

        return {
            "status": status,
            "healthy": healthy,
            "max_age_seconds": MONITORING_HEALTH_MAX_AGE,
            "started_at": started,
            "last_success_at": success,
            "last_success_age_seconds": age,
            "last_error_at": error_at,
            "last_error": error,
            "last_polled_miners": miners,
        }


MONITORING_HEALTH = MonitoringHealthState()


def monitoring_health_snapshot(now=None):
    return MONITORING_HEALTH.snapshot(now)


def poll_miner(miner_id):
    miner = get_miner(miner_id)

    if not miner or not miner["enabled"] or miner["driver"] == "unset":
        return

    try:
        status = read_status(miner)
    except Exception as exc:
        # Only an ASIC communication failure means OFFLINE.
        # Database write failures must not masquerade as ASIC outages.
        mark_miner_offline(
            miner_id,
            f"{type(exc).__name__}: {exc}",
        )
        return

    save_miner_status(
        miner_id,
        status,
        int(time.time()),
    )


def polling_iteration(runtime):
    miners = get_poll_miners()

    if miners:
        failures = 0
        first_error = None

        with ThreadPoolExecutor(
            max_workers=min(12, len(miners))
        ) as executor:
            futures = [
                executor.submit(
                    runtime.poll_miner,
                    miner["id"],
                )
                for miner in miners
            ]

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    failures += 1
                    if first_error is None:
                        first_error = exc
                        LOGGER.exception(
                            "Miner polling failed; other failures "
                            "in this cycle will be counted"
                        )

        if failures:
            raise RuntimeError(
                f"{failures}/{len(miners)} miner polls failed; "
                f"first={type(first_error).__name__}: {first_error}"
            )

    return len(miners)


def polling_loop(runtime):
    """Keep retrying failed cycles instead of permanently losing the poller."""
    MONITORING_HEALTH.reset()
    MONITORING_HEALTH.mark_started()

    while not runtime.stop_event.is_set():
        try:
            total = polling_iteration(runtime)
        except Exception as exc:
            MONITORING_HEALTH.mark_error(exc)
            LOGGER.exception(
                "Monitoring iteration failed; retrying in %s seconds",
                POLL_INTERVAL,
            )
        else:
            MONITORING_HEALTH.mark_success(total)

        runtime.stop_event.wait(POLL_INTERVAL)


def delayed_poll(runtime, miner_id):
    time.sleep(3)
    runtime.poll_miner(miner_id)


class MonitoringRuntime:
    """Application-owned shutdown boundary for monitoring."""

    def __init__(self, stop_event):
        self.stop_event = stop_event

    def poll_miner(self, miner_id):
        return poll_miner(miner_id)

    def run(self):
        return polling_loop(self)

    def delayed_poll(self, miner_id):
        return delayed_poll(self, miner_id)


__all__ = (
    "POLL_INTERVAL",
    "MONITORING_HEALTH",
    "monitoring_health_snapshot",
    "MonitoringRuntime",
    "poll_miner",
    "polling_iteration",
    "polling_loop",
    "delayed_poll",
)
