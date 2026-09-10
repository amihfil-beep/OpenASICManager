import os
import tempfile
import unittest

import config as app_config
from db import db, init_db
from control.repository import list_active_control_jobs
from inventory.analytics import miner_status_items
from inventory.repository import (
    clear_manual_overrides,
    list_miners,
    set_all_schedule_enabled,
    set_miner_enabled,
    set_miner_schedule_enabled,
)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-inventory-",
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
                name, ip, driver, enabled,
                schedule_enabled, last_state,
                manual_override_until
            )
            VALUES (
                'ASIC-20', '192.0.2.20',
                'bitmain_stock', 1, 1,
                'MINING', 12345
            )
        """)
        self.miner20 = cur.lastrowid

        cur = conn.execute("""
            INSERT INTO miners
            (
                name, ip, driver, enabled,
                schedule_enabled, last_state
            )
            VALUES (
                'ASIC-10', '192.0.2.10',
                'unset', 1, 0,
                'UNKNOWN'
            )
        """)
        self.miner10 = cur.lastrowid

        cur = conn.execute("""
            INSERT INTO miners
            (
                name, ip, driver, enabled,
                schedule_enabled, last_state
            )
            VALUES (
                'ASIC-30', '192.0.2.30',
                'awesome', 0, 1,
                'PAUSED'
            )
        """)
        self.miner30 = cur.lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_list_and_simple_mutations(self):
        self.assertEqual(len(list_miners()), 3)

        set_miner_enabled(self.miner20, False)
        set_miner_schedule_enabled(self.miner20, False)

        conn = db()
        row = conn.execute(
            "SELECT * FROM miners WHERE id=?",
            (self.miner20,),
        ).fetchone()
        conn.close()

        self.assertEqual(row["enabled"], 0)
        self.assertEqual(row["schedule_enabled"], 0)

    def test_bulk_schedule_only_updates_enabled_supported_miners(self):
        changed = set_all_schedule_enabled(False)
        self.assertEqual(changed, 1)

        conn = db()
        rows = {
            row["id"]: row
            for row in conn.execute(
                "SELECT * FROM miners"
            ).fetchall()
        }
        conn.close()

        self.assertEqual(
            rows[self.miner20]["schedule_enabled"],
            0,
        )
        self.assertEqual(
            rows[self.miner10]["schedule_enabled"],
            0,
        )
        self.assertEqual(
            rows[self.miner30]["schedule_enabled"],
            1,
        )

    def test_clear_manual_overrides(self):
        changed = clear_manual_overrides()
        self.assertEqual(changed, 1)

        conn = db()
        row = conn.execute(
            "SELECT manual_override_until FROM miners WHERE id=?",
            (self.miner20,),
        ).fetchone()
        conn.close()

        self.assertIsNone(row["manual_override_until"])

    def test_status_read_model_sorts_and_attaches_latest_job(self):
        conn = db()
        conn.execute("""
            INSERT INTO control_jobs
            (
                created_at, miner_id, ip, name,
                source, action, target_state,
                status, attempts, max_attempts,
                message
            )
            VALUES (
                100, ?, '192.0.2.20', 'ASIC-20',
                'WEB:test', 'pause', 'PAUSED',
                'QUEUED', 0, 3, 'queued'
            )
        """, (self.miner20,))
        newest = conn.execute("""
            INSERT INTO control_jobs
            (
                created_at, miner_id, ip, name,
                source, action, target_state,
                status, attempts, max_attempts,
                message
            )
            VALUES (
                101, ?, '192.0.2.20', 'ASIC-20',
                'WEB:test', 'pause', 'PAUSED',
                'RUNNING', 1, 3, 'running'
            )
        """, (self.miner20,)).lastrowid
        conn.commit()
        conn.close()

        items = miner_status_items(
            list_miners(),
            list_active_control_jobs(),
        )

        self.assertEqual(
            [item["ip"] for item in items],
            ["192.0.2.10", "192.0.2.20", "192.0.2.30"],
        )
        self.assertEqual(items[0]["state"], "CONFIG_REQUIRED")
        self.assertEqual(
            items[1]["control_job"]["id"],
            newest,
        )
        self.assertEqual(
            items[1]["control_job"]["status"],
            "RUNNING",
        )


if __name__ == "__main__":
    unittest.main()
