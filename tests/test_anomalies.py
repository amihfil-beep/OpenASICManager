import os
import tempfile
import unittest

import config as app_config
from db import db, init_db
from anomalies import policy
from anomalies import repository
from anomalies import service


class FakeRuntime:
    def __init__(self):
        self.events = []
        self.desired_state = lambda when: None
        self.stop_event = None

    def log_event(self, **kwargs):
        self.events.append(kwargs)


class AnomalyPolicyTests(unittest.TestCase):
    def test_offline_ignores_intentional_reboot(self):
        self.assertTrue(
            policy.offline_observed("OFFLINE", None)
        )
        self.assertFalse(
            policy.offline_observed("OFFLINE", "reboot")
        )

    def test_temperature_hysteresis(self):
        self.assertFalse(
            policy.overheat_observed(84.9, False)
        )
        self.assertTrue(
            policy.overheat_observed(85.0, False)
        )
        self.assertTrue(
            policy.overheat_observed(82.0, True)
        )
        self.assertFalse(
            policy.overheat_observed(81.9, True)
        )

    def test_schedule_applicability(self):
        self.assertTrue(
            policy.schedule_applicable(
                True,
                "MINING",
                1,
                False,
                0,
            )
        )
        self.assertFalse(
            policy.schedule_applicable(
                True,
                "MINING",
                1,
                True,
                0,
            )
        )


class AnomalyRepositoryTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-anomaly-",
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
                last_state,
                temp
            )
            VALUES
            (
                'TEST-ASIC',
                '192.0.2.50',
                'bitmain_stock',
                1,
                'OFFLINE',
                70
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

    def test_candidate_open_and_resolve_transition(self):
        miner = self.miner()

        opened, resolved = (
            repository.transition_anomaly_condition(
                miner=miner,
                code="OFFLINE",
                severity="CRITICAL",
                observed=True,
                grace_seconds=0,
                message="ASIC is offline",
                now=100,
            )
        )
        self.assertFalse(opened)
        self.assertFalse(resolved)

        opened, resolved = (
            repository.transition_anomaly_condition(
                miner=miner,
                code="OFFLINE",
                severity="CRITICAL",
                observed=True,
                grace_seconds=0,
                message="ASIC is offline",
                now=101,
            )
        )
        self.assertTrue(opened)
        self.assertFalse(resolved)
        self.assertTrue(
            repository.active_issue_exists(
                self.miner_id,
                "OFFLINE",
            )
        )

        opened, resolved = (
            repository.transition_anomaly_condition(
                miner=miner,
                code="OFFLINE",
                severity="CRITICAL",
                observed=False,
                grace_seconds=0,
                message="ASIC reachable; state=MINING",
                now=102,
            )
        )
        self.assertFalse(opened)
        self.assertTrue(resolved)
        self.assertFalse(
            repository.active_issue_exists(
                self.miner_id,
                "OFFLINE",
            )
        )

    def test_service_emits_open_and_resolved_events(self):
        runtime = FakeRuntime()

        old_grace = service.ANOMALY_OFFLINE_GRACE
        service.ANOMALY_OFFLINE_GRACE = 0
        try:
            service.anomaly_scan(runtime)
            service.anomaly_scan(runtime)

            self.assertEqual(
                runtime.events[-1]["action"],
                "ISSUE_OPEN",
            )

            conn = db()
            conn.execute(
                "UPDATE miners SET last_state='MINING' WHERE id=?",
                (self.miner_id,),
            )
            conn.commit()
            conn.close()

            service.anomaly_scan(runtime)

            self.assertEqual(
                runtime.events[-1]["action"],
                "ISSUE_RESOLVED",
            )
        finally:
            service.ANOMALY_OFFLINE_GRACE = old_grace


if __name__ == "__main__":
    unittest.main()
