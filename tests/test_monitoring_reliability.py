"""Regression tests for the 0.9.1 SQLite/EMFILE incident."""

import sqlite3
import unittest
from unittest.mock import Mock, patch

import db as db_module
from monitoring import repository as monitoring_repository
from monitoring import resources
from monitoring import service


class StopAfterWaits:
    def __init__(self, max_waits):
        self.calls = 0
        self.max_waits = max_waits

    def is_set(self):
        return self.calls >= self.max_waits

    def wait(self, seconds):
        self.calls += 1
        return self.is_set()


class MonitorRecoveryTests(unittest.TestCase):
    def test_fatal_list_error_retries_next_iteration(self):
        stop = StopAfterWaits(2)
        runtime = service.MonitoringRuntime(stop_event=stop)

        with patch.object(
            service,
            "get_poll_miners",
            side_effect=[
                sqlite3.OperationalError("unable to open database file"),
                [],
            ],
        ) as listing, patch.object(
            service.LOGGER,
            "exception",
        ):
            runtime.run()

        self.assertEqual(listing.call_count, 2)
        self.assertEqual(stop.calls, 2)
        status = service.monitoring_health_snapshot()
        self.assertTrue(status["healthy"])
        self.assertEqual(status["last_polled_miners"], 0)
        self.assertIn("unable to open database", status["last_error"])

    def test_failed_worker_is_reported_without_ending_poller(self):
        stop = StopAfterWaits(1)
        runtime = service.MonitoringRuntime(stop_event=stop)

        with patch.object(
            service,
            "get_poll_miners",
            return_value=[{"id": 31}],
        ), patch.object(
            runtime,
            "poll_miner",
            side_effect=sqlite3.OperationalError("readonly database"),
        ), patch.object(
            service.LOGGER,
            "exception",
        ):
            runtime.run()

        status = service.monitoring_health_snapshot()
        self.assertEqual(status["status"], "error")
        self.assertFalse(status["healthy"])
        self.assertIn("1/1 miner polls failed", status["last_error"])

    def test_failed_status_write_does_not_mark_miner_offline(self):
        miner = {"id": 10, "enabled": 1, "driver": "bitmain_stock"}
        with patch.object(
            service, "get_miner", return_value=miner
        ), patch.object(
            service, "read_status", return_value={"state": "MINING"}
        ), patch.object(
            service,
            "save_miner_status",
            side_effect=sqlite3.OperationalError("readonly database"),
        ), patch.object(
            service, "mark_miner_offline"
        ) as offline:
            with self.assertRaises(sqlite3.OperationalError):
                service.poll_miner(10)
        offline.assert_not_called()

    def test_communication_failure_still_marks_miner_offline(self):
        miner = {"id": 10, "enabled": 1, "driver": "bitmain_stock"}
        with patch.object(
            service, "get_miner", return_value=miner
        ), patch.object(
            service,
            "read_status",
            side_effect=TimeoutError("ASIC timeout"),
        ), patch.object(
            service, "mark_miner_offline"
        ) as offline:
            service.poll_miner(10)
        offline.assert_called_once_with(10, "TimeoutError: ASIC timeout")

    def test_heartbeat_detects_error_and_recovers(self):
        health = service.MonitoringHealthState()
        self.assertEqual(health.snapshot(now=100)["status"], "not_started")
        health.mark_started(now=100)
        health.mark_success(30, now=105)
        self.assertTrue(health.snapshot(now=106)["healthy"])
        health.mark_error(RuntimeError("db down"), now=107)
        self.assertFalse(health.snapshot(now=108)["healthy"])
        health.mark_success(30, now=120)
        self.assertTrue(health.snapshot(now=121)["healthy"])
        self.assertEqual(health.snapshot(now=200)["status"], "stale")


class SQLiteCleanupTests(unittest.TestCase):
    def test_connect_closes_when_wal_initialization_fails(self):
        connection = Mock()
        connection.execute.side_effect = sqlite3.OperationalError(
            "unable to open database file"
        )
        with patch.object(
            db_module.sqlite3, "connect", return_value=connection
        ):
            with self.assertRaises(sqlite3.OperationalError):
                db_module.db()
        connection.close.assert_called_once_with()

    def test_monitoring_writes_close_even_on_failure(self):
        for operation in ("status", "offline"):
            with self.subTest(operation=operation):
                conn = Mock()
                conn.execute.side_effect = sqlite3.OperationalError(
                    "attempt to write a readonly database"
                )
                with patch.object(
                    monitoring_repository, "db", return_value=conn
                ):
                    with self.assertRaises(sqlite3.OperationalError):
                        if operation == "status":
                            monitoring_repository.save_miner_status(
                                1, {"state": "MINING"}, 100
                            )
                        else:
                            monitoring_repository.mark_miner_offline(
                                1, "test"
                            )
                conn.close.assert_called_once_with()


class FDHealthTests(unittest.TestCase):
    def test_normal_and_critical_fd_pressure(self):
        with patch.object(
            resources.resource, "getrlimit", return_value=(1024, 524288)
        ), patch.object(
            resources.os, "listdir", return_value=["fd"] * 7
        ):
            good = resources.file_descriptor_health_snapshot()

        self.assertTrue(good["healthy"])
        self.assertEqual(good["soft_limit"], 1024)
        self.assertEqual(good["open_fds"], 7)

        with patch.object(
            resources.resource, "getrlimit", return_value=(1024, 524288)
        ), patch.object(
            resources.os, "listdir", return_value=["fd"] * 950
        ):
            bad = resources.file_descriptor_health_snapshot()

        self.assertFalse(bad["healthy"])
        self.assertEqual(bad["status"], "critical")


if __name__ == "__main__":
    unittest.main()
