import os
import tempfile
import unittest

import config as app_config
from db import db, init_db
from inventory.repository import (
    convert_unconfigured_to_stock,
    create_discovered_miner,
    get_miner_by_ip,
    list_discovery_miners,
    miner_name_exists,
)


class DiscoveryPersistenceTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-discovery-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        init_db()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def add_miner(self, name, ip, driver="unset"):
        conn = db()
        cur = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                enabled,
                schedule_enabled,
                last_state,
                last_error
            )
            VALUES (?, ?, ?, 1, 1, 'CONFIG_REQUIRED', 'old-error')
        """, (name, ip, driver))
        miner_id = cur.lastrowid
        conn.commit()
        conn.close()
        return miner_id

    def row(self, miner_id):
        conn = db()
        try:
            return conn.execute(
                "SELECT * FROM miners WHERE id=?",
                (miner_id,),
            ).fetchone()
        finally:
            conn.close()

    def test_convert_unconfigured_to_stock(self):
        first = self.add_miner("ASIC-001", "192.0.2.1")
        second = self.add_miner("ASIC-002", "192.0.2.2")
        untouched = self.add_miner(
            "ASIC-003",
            "192.0.2.3",
            driver="awesome",
        )

        ids = convert_unconfigured_to_stock(
            "stock-user",
            "stock-pass",
        )

        self.assertEqual(set(ids), {first, second})
        for miner_id in (first, second):
            row = self.row(miner_id)
            self.assertEqual(row["driver"], "bitmain_stock")
            self.assertEqual(row["username"], "stock-user")
            self.assertEqual(row["password"], "stock-pass")
            self.assertEqual(row["schedule_enabled"], 0)
            self.assertEqual(row["last_state"], "UNKNOWN")
            self.assertIsNone(row["last_error"])

        self.assertEqual(self.row(untouched)["driver"], "awesome")

    def test_discovery_inventory_lookup_helpers(self):
        miner_id = self.add_miner(
            "ASIC-020",
            "192.0.2.20",
            driver="awesome",
        )

        rows = list_discovery_miners()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], miner_id)

        by_ip = get_miner_by_ip("192.0.2.20")
        self.assertEqual(by_ip["name"], "ASIC-020")
        self.assertTrue(miner_name_exists("ASIC-020"))
        self.assertFalse(miner_name_exists("ASIC-999"))

    def test_create_discovered_miner(self):
        miner_id, created = create_discovered_miner(
            "ASIC-021",
            "192.0.2.21",
            "bitmain_stock",
            "user",
            "pass",
            "T21",
            "stock-fw",
        )

        self.assertTrue(created)
        row = self.row(miner_id)
        self.assertEqual(row["name"], "ASIC-021")
        self.assertEqual(row["driver"], "bitmain_stock")
        self.assertEqual(row["enabled"], 1)
        self.assertEqual(row["schedule_enabled"], 0)
        self.assertEqual(row["last_state"], "UNKNOWN")

    def test_create_discovered_miner_duplicate_ip_is_idempotent(self):
        first_id, created = create_discovered_miner(
            "ASIC-022",
            "192.0.2.22",
            "awesome",
            "user-a",
            "pass-a",
            "T21",
            "fw-a",
        )
        self.assertTrue(created)

        second_id, created = create_discovered_miner(
            "ASIC-023",
            "192.0.2.22",
            "bitmain_stock",
            "user-b",
            "pass-b",
            "T21",
            "fw-b",
        )

        self.assertFalse(created)
        self.assertEqual(second_id, first_id)


if __name__ == "__main__":
    unittest.main()
