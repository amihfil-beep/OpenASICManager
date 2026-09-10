import os
import tempfile
import unittest

import config as app_config
from db import db, init_db
from control.repository import (
    create_control_job,
    get_active_control_job,
    get_control_job,
    set_last_command,
    set_manual_override,
    update_control_job,
)


class ControlRepositoryTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-control-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        init_db()

        conn = db()
        cur = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                enabled
            )
            VALUES (
                'TEST-ASIC',
                '192.0.2.70',
                'bitmain_stock',
                1
            )
        """)
        self.miner_id = cur.lastrowid
        conn.commit()
        self.miner = conn.execute(
            "SELECT * FROM miners WHERE id=?",
            (self.miner_id,),
        ).fetchone()
        conn.close()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_create_get_and_update_job(self):
        job_id = create_control_job(
            miner=self.miner,
            source="WEB:test",
            action="pause",
            target_state="PAUSED",
            max_attempts=3,
            now=1000,
        )

        job = get_control_job(job_id)
        self.assertEqual(job["status"], "QUEUED")
        self.assertEqual(job["source"], "WEB:test")
        self.assertEqual(job["target_state"], "PAUSED")
        self.assertEqual(job["max_attempts"], 3)

        update_control_job(
            job_id,
            status="RUNNING",
            attempts=1,
            message="verification started",
        )

        job = get_control_job(job_id)
        self.assertEqual(job["status"], "RUNNING")
        self.assertEqual(job["attempts"], 1)
        self.assertEqual(job["message"], "verification started")

    def test_active_job_lookup(self):
        job_id = create_control_job(
            miner=self.miner,
            source="SCHEDULER",
            action="resume",
            target_state="MINING",
            max_attempts=3,
            now=1001,
        )

        active = get_active_control_job(
            self.miner_id
        )
        self.assertEqual(active["id"], job_id)

        update_control_job(
            job_id,
            status="VERIFIED",
        )
        self.assertIsNone(
            get_active_control_job(
                self.miner_id
            )
        )

    def test_manual_override_and_last_command(self):
        set_manual_override(
            self.miner_id,
            123456,
        )
        set_last_command(
            self.miner_id,
            "pause",
        )

        conn = db()
        miner = conn.execute(
            "SELECT * FROM miners WHERE id=?",
            (self.miner_id,),
        ).fetchone()
        conn.close()

        self.assertEqual(
            miner["manual_override_until"],
            123456,
        )
        self.assertEqual(
            miner["last_action"],
            "PAUSE",
        )
        self.assertGreater(
            miner["last_action_at"],
            0,
        )

    def test_update_rejects_unknown_fields(self):
        job_id = create_control_job(
            miner=self.miner,
            source="SCHEDULER",
            action="pause",
            target_state="PAUSED",
            max_attempts=3,
            now=1002,
        )

        with self.assertRaises(RuntimeError):
            update_control_job(
                job_id,
                source="INVALID",
            )


if __name__ == "__main__":
    unittest.main()
