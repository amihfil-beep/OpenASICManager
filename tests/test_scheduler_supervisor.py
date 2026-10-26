import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scheduler.supervisor import (
    SCHEDULER_HEALTH,
    scheduler_health_snapshot,
    scheduler_loop,
)


class StopAfterWaits:
    def __init__(self, count):
        self.count = int(count)
        self.wait_calls = 0
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.wait_calls += 1
        if self.wait_calls >= self.count:
            self.stopped = True
        return self.stopped


class SchedulerSupervisorTests(unittest.TestCase):
    def setUp(self):
        SCHEDULER_HEALTH.reset()

    def tearDown(self):
        SCHEDULER_HEALTH.reset()

    def test_iteration_failure_does_not_kill_scheduler_loop(self):
        runtime = SimpleNamespace(
            stop_event=StopAfterWaits(2)
        )

        with patch(
            "scheduler.supervisor.scheduler_iteration",
            side_effect=[
                sqlite3.OperationalError(
                    "unable to open database file"
                ),
                None,
            ],
        ) as iteration, patch(
            "scheduler.supervisor.LOGGER.exception"
        ) as logged:
            scheduler_loop(runtime)

        self.assertEqual(iteration.call_count, 2)
        logged.assert_called_once()

        health = scheduler_health_snapshot()
        self.assertTrue(health["healthy"])
        self.assertEqual(health["status"], "ok")
        self.assertEqual(
            health["last_error"],
            "unable to open database file",
        )
        self.assertIsNotNone(
            health["last_error_at"]
        )

    def test_repeated_failures_become_unhealthy_when_heartbeat_is_stale(self):
        SCHEDULER_HEALTH.mark_started(now=100.0)
        SCHEDULER_HEALTH.mark_success(now=100.0)
        SCHEDULER_HEALTH.mark_error(
            "database unavailable",
            now=120.0,
        )

        health = scheduler_health_snapshot(
            now=200.0
        )

        self.assertFalse(health["healthy"])
        self.assertEqual(health["status"], "stale")
        self.assertEqual(
            health["last_error"],
            "database unavailable",
        )


if __name__ == "__main__":
    unittest.main()
