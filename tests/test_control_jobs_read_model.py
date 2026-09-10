import os
import tempfile
import unittest

import config as app_config
from db import init_db
from control.analytics import (
    CONTROL_JOB_FIELDS,
    control_job_items,
)
from control.repository import (
    create_control_job,
    list_control_jobs,
)


class ControlJobsReadModelTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-control-jobs-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        init_db()

        self.miner = {
            "id": 42,
            "ip": "192.0.2.42",
            "name": "TEST-ASIC-042",
        }

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def create_job(self, action, now):
        target = "MINING" if action == "resume" else "PAUSED"
        return create_control_job(
            self.miner,
            "MANUAL",
            action,
            target,
            3,
            now=now,
        )

    def test_repository_orders_newest_first_and_limits(self):
        first = self.create_job("pause", 100)
        second = self.create_job("resume", 101)

        rows = list_control_jobs(1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], second)
        self.assertNotEqual(rows[0]["id"], first)

    def test_read_model_preserves_public_fields(self):
        job_id = self.create_job("pause", 100)
        rows = list_control_jobs(10)
        items = control_job_items(rows)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], job_id)
        self.assertEqual(items[0]["ip"], "192.0.2.42")
        self.assertEqual(items[0]["action"], "pause")
        self.assertEqual(items[0]["status"], "QUEUED")
        self.assertEqual(
            tuple(items[0].keys()),
            CONTROL_JOB_FIELDS,
        )

    def test_repository_coerces_non_positive_limit(self):
        self.create_job("pause", 100)
        self.create_job("resume", 101)

        rows = list_control_jobs(0)
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
