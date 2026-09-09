import os
import tempfile
import unittest
from unittest.mock import patch

import config as app_config
from db import db, init_db
from monitoring import service


class MonitoringRepositoryTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-monitoring-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        init_db()

        conn = db()
        cursor = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                enabled,
                last_state
            )
            VALUES
            (
                'TEST-ASIC',
                '192.0.2.90',
                'bitmain_stock',
                1,
                'UNKNOWN'
            )
        """)
        self.miner_id = cursor.lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def miner(self):
        conn = db()
        row = conn.execute(
            "SELECT * FROM miners WHERE id=?",
            (self.miner_id,),
        ).fetchone()
        conn.close()
        return row

    def test_successful_poll_persists_status(self):
        status = {
            "model": "Antminer T21",
            "firmware": "stock",
            "state": "MINING",
            "hashrate": 173.0,
            "avg_hashrate": 171.0,
            "temp": 71.0,
            "power": 3500.0,
            "pool": "pool.example.net",
        }

        with patch.object(
            service,
            "read_status",
            return_value=status,
        ):
            service.poll_miner(
                self.miner_id
            )

        miner = self.miner()
        self.assertEqual(miner["last_state"], "MINING")
        self.assertEqual(miner["hashrate"], 173.0)
        self.assertEqual(miner["avg_hashrate"], 171.0)
        self.assertEqual(miner["temp"], 71.0)
        self.assertEqual(miner["power"], 3500.0)
        self.assertEqual(miner["pool"], "pool.example.net")
        self.assertIsNone(miner["last_error"])
        self.assertIsNotNone(miner["last_seen"])

    def test_failed_poll_marks_miner_offline(self):
        with patch.object(
            service,
            "read_status",
            side_effect=RuntimeError("test failure"),
        ):
            service.poll_miner(
                self.miner_id
            )

        miner = self.miner()
        self.assertEqual(miner["last_state"], "OFFLINE")
        self.assertEqual(
            miner["last_error"],
            "RuntimeError: test failure",
        )

    def test_disabled_miner_is_not_polled(self):
        conn = db()
        conn.execute(
            "UPDATE miners SET enabled=0 WHERE id=?",
            (self.miner_id,),
        )
        conn.commit()
        conn.close()

        with patch.object(
            service,
            "read_status",
        ) as read_status:
            service.poll_miner(
                self.miner_id
            )
            read_status.assert_not_called()


class StopAfterWait:
    def __init__(self):
        self.stopped = False
        self.wait_calls = []

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.wait_calls.append(timeout)
        self.stopped = True
        return True


class MonitoringRuntimeTests(unittest.TestCase):
    def test_empty_polling_cycle_waits_for_interval(self):
        stop_event = StopAfterWait()
        runtime = service.MonitoringRuntime(
            stop_event=stop_event
        )

        with patch.object(
            service,
            "get_poll_miners",
            return_value=[],
        ):
            runtime.run()

        self.assertEqual(
            stop_event.wait_calls,
            [service.POLL_INTERVAL],
        )

    def test_delayed_poll_uses_runtime_poller(self):
        stop_event = StopAfterWait()
        runtime = service.MonitoringRuntime(
            stop_event=stop_event
        )

        with patch.object(
            service.time,
            "sleep",
        ) as sleep, patch.object(
            service,
            "poll_miner",
        ) as poll:
            runtime.delayed_poll(42)

        sleep.assert_called_once_with(3)
        poll.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
