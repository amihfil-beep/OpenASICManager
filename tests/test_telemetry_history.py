import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telemetry.history import (
    HISTORY_RANGES,
    aligned_history_window,
)
from telemetry.repository import telemetry_history_rows


class TelemetryHistoryPolicyTests(unittest.TestCase):
    def test_supported_ranges_are_predictable_and_bounded(self):
        expected = {
            24: (300, 288),
            168: (900, 672),
            720: (3600, 720),
            2160: (10800, 720),
        }

        self.assertEqual(set(HISTORY_RANGES), set(expected))

        for hours, values in expected.items():
            with self.subTest(hours=hours):
                selected = HISTORY_RANGES[hours]
                self.assertEqual(
                    selected.bucket_seconds,
                    values[0],
                )
                self.assertEqual(
                    selected.expected_points,
                    values[1],
                )

    def test_window_aligns_to_bucket_boundaries(self):
        selected = HISTORY_RANGES[24]
        since, until = aligned_history_window(
            2_000_000_123,
            selected,
        )

        self.assertEqual(since % 300, 0)
        self.assertEqual(until, 2_000_000_123)
        self.assertEqual(
            (until // 300) - (since // 300) + 1,
            selected.expected_points,
        )


class TelemetryHistoryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "history.sqlite3"

        conn = self.connect()
        conn.execute("""
            CREATE TABLE telemetry (
                ts INTEGER NOT NULL,
                miner_id INTEGER NOT NULL,
                state TEXT,
                hashrate REAL,
                avg_hashrate REAL,
                temp REAL,
                power REAL
            )
        """)
        conn.commit()
        conn.close()

        self.db_patch = patch(
            "telemetry.repository.db",
            side_effect=self.connect,
        )
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def insert_samples(self, rows):
        conn = self.connect()
        conn.executemany("""
            INSERT INTO telemetry (
                ts,
                miner_id,
                state,
                hashrate,
                avg_hashrate,
                temp,
                power
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
        conn.close()

    def test_downsampling_aggregates_buckets_and_keeps_gaps(self):
        self.insert_samples([
            (10, 1, "PAUSED", 10, 11, 60, 1000),
            (290, 1, "MINING", 30, 31, 80, 1200),
            (610, 1, "OFFLINE", None, None, None, None),
        ])

        rows = telemetry_history_rows(
            1,
            0,
            300,
            899,
        )

        self.assertEqual(
            [row["bucket_ts"] for row in rows],
            [0, 600],
        )
        self.assertEqual(rows[0]["state"], "MINING")
        self.assertEqual(rows[0]["sample_count"], 2)
        self.assertEqual(rows[0]["hashrate"], 20)
        self.assertEqual(rows[0]["temp"], 70)
        self.assertEqual(rows[1]["state"], "OFFLINE")
        self.assertIsNone(rows[1]["hashrate"])

    def test_empty_period_returns_no_fabricated_points(self):
        rows = telemetry_history_rows(
            1,
            0,
            300,
            899,
        )

        self.assertEqual(rows, [])

    def test_90_day_query_is_bounded_to_720_buckets(self):
        interval = 300
        sample_count = 2160 * 3600 // interval
        self.insert_samples([
            (
                index * interval,
                1,
                "MINING",
                100,
                100,
                70,
                3000,
            )
            for index in range(sample_count)
        ])

        rows = telemetry_history_rows(
            1,
            0,
            10800,
            2160 * 3600 - 1,
        )

        self.assertEqual(len(rows), 720)
        self.assertTrue(
            all(row["sample_count"] == 36 for row in rows)
        )


if __name__ == "__main__":
    unittest.main()
