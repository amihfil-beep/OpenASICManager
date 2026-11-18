"""Background threads must survive coincident SQLite and audit failures."""

import sqlite3
import unittest
from unittest.mock import Mock, patch

from anomalies import service as anomaly_service
from telemetry import service as telemetry_service


class StopAfterWaits:
    def __init__(self, limit):
        self.calls = []
        self.limit = limit

    def is_set(self):
        return len(self.calls) >= self.limit

    def wait(self, seconds):
        self.calls.append(seconds)
        return self.is_set()


class BackgroundRecoveryTests(unittest.TestCase):
    def test_anomaly_loop_recovers_after_initial_db_failure(self):
        stop = StopAfterWaits(3)
        runtime = Mock()
        runtime.stop_event = stop
        runtime.log_event.side_effect = sqlite3.OperationalError(
            "attempt to write a readonly database"
        )

        with patch.object(
            anomaly_service,
            "load_anomaly_policy",
            side_effect=[
                sqlite3.OperationalError("unable to open database file"),
                {"interval_seconds": 1},
            ],
        ) as load, patch.object(
            anomaly_service, "anomaly_scan"
        ) as scan, patch.object(
            anomaly_service.LOGGER, "exception"
        ):
            anomaly_service.anomaly_loop(runtime)

        self.assertEqual(load.call_count, 2)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(stop.calls, [30, 1, 1])

    def test_telemetry_loop_survives_failed_audit_logging(self):
        stop = StopAfterWaits(2)
        runtime = Mock()
        runtime.stop_event = stop
        runtime.log_event.side_effect = sqlite3.OperationalError(
            "database is readonly"
        )
        with patch.object(
            telemetry_service,
            "save_telemetry_snapshot",
            side_effect=sqlite3.OperationalError("database is readonly"),
        ), patch.object(
            telemetry_service.LOGGER, "exception"
        ):
            telemetry_service.telemetry_loop(runtime)

        self.assertEqual(runtime.log_event.call_count, 1)
        self.assertEqual(
            stop.calls, [30, telemetry_service.TELEMETRY_INTERVAL]
        )


if __name__ == "__main__":
    unittest.main()
