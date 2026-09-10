import os
import tempfile
import unittest

import config as app_config
from db import db, init_db
from anomalies.analytics import issue_report
from anomalies.repository import issue_rows


class AnomalyIssueReadModelTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-issues-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        init_db()

        conn = db()
        rows = (
            (1, "192.0.2.11", "ASIC-11", "OVERHEAT", "CRITICAL", "ACTIVE", 100, 120, None, "hot"),
            (2, "192.0.2.12", "ASIC-12", "SCHEDULE_MISMATCH", "WARNING", "ACTIVE", 90, 121, None, "mismatch"),
            (3, "192.0.2.13", "ASIC-13", "OFFLINE", "INFO", "ACTIVE", 80, 122, None, "offline"),
            (4, "192.0.2.14", "ASIC-14", "OFFLINE", "CRITICAL", "RESOLVED", 60, 70, 200, "resolved old"),
            (5, "192.0.2.15", "ASIC-15", "OVERHEAT", "CRITICAL", "RESOLVED", 61, 71, 300, "resolved new"),
        )
        conn.executemany("""
            INSERT INTO issues
            (
                miner_id,
                ip,
                name,
                code,
                severity,
                status,
                first_seen,
                last_seen,
                resolved_at,
                message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
        conn.close()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_active_order_matches_existing_api_contract(self):
        active, _ = issue_rows(10)
        self.assertEqual(
            [row["severity"] for row in active],
            ["CRITICAL", "WARNING", "INFO"],
        )

    def test_resolved_limit_and_order(self):
        _, resolved = issue_rows(1)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(
            resolved[0]["message"],
            "resolved new",
        )

    def test_report_preserves_shape_and_thresholds(self):
        report = issue_report(10)

        self.assertEqual(report["active_count"], 3)
        self.assertEqual(len(report["active"]), 3)
        self.assertEqual(len(report["recent_resolved"]), 2)
        self.assertEqual(
            set(report["thresholds"]),
            {
                "offline_grace_seconds",
                "hot_open_c",
                "hot_clear_c",
                "hot_grace_seconds",
                "schedule_grace_seconds",
            },
        )

        active = report["active"][0]
        self.assertIn("T", active["first_seen"])
        self.assertIn("T", active["last_seen"])
        self.assertIsNone(active["resolved_at"])

        resolved = report["recent_resolved"][0]
        self.assertIsNotNone(resolved["resolved_at"])


if __name__ == "__main__":
    unittest.main()
