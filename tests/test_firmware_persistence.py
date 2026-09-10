import os
import tempfile
import unittest

import config as app_config
from db import db, init_db
from inventory.repository import (
    set_miner_detection_auto,
    set_miner_detected_firmware,
    set_miner_manual_driver,
    set_miner_manual_firmware,
)


class FirmwarePersistenceTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-firmware-",
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

    def add_miner(
        self,
        *,
        driver="unset",
        schedule_enabled=1,
        state="MINING",
        model="T21",
        firmware="old-fw",
    ):
        conn = db()
        cur = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                enabled,
                schedule_enabled,
                detection_mode,
                model,
                firmware,
                last_state,
                last_error,
                manual_override_until
            )
            VALUES (
                'ASIC-20',
                '192.0.2.20',
                ?,
                1,
                ?,
                'AUTO',
                ?,
                ?,
                ?,
                'old-error',
                12345
            )
        """, (
            driver,
            schedule_enabled,
            model,
            firmware,
            state,
        ))
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

    def test_manual_driver_change_resets_runtime_state(self):
        miner_id = self.add_miner(driver="unset")

        set_miner_manual_driver(
            miner_id,
            "awesome",
            "test-user",
            "test-password",
        )

        row = self.row(miner_id)
        self.assertEqual(row["detection_mode"], "MANUAL")
        self.assertEqual(row["driver"], "awesome")
        self.assertEqual(row["username"], "test-user")
        self.assertEqual(row["password"], "test-password")
        self.assertEqual(row["last_state"], "UNKNOWN")
        self.assertIsNone(row["model"])
        self.assertIsNone(row["firmware"])
        self.assertIsNone(row["manual_override_until"])
        self.assertIsNone(row["last_error"])

    def test_unset_manual_driver_disables_schedule(self):
        miner_id = self.add_miner(
            driver="awesome",
            schedule_enabled=1,
        )

        set_miner_manual_driver(
            miner_id,
            "unset",
            "",
            "",
        )

        row = self.row(miner_id)
        self.assertEqual(row["driver"], "unset")
        self.assertEqual(row["last_state"], "CONFIG_REQUIRED")
        self.assertEqual(row["schedule_enabled"], 0)

    def test_detection_mode_auto_only_changes_mode(self):
        miner_id = self.add_miner(driver="awesome")

        conn = db()
        conn.execute(
            "UPDATE miners SET detection_mode='MANUAL' WHERE id=?",
            (miner_id,),
        )
        conn.commit()
        conn.close()

        set_miner_detection_auto(miner_id)
        row = self.row(miner_id)
        self.assertEqual(row["detection_mode"], "AUTO")
        self.assertEqual(row["driver"], "awesome")
        self.assertEqual(row["last_state"], "MINING")

    def test_manual_firmware_same_driver_preserves_state(self):
        miner_id = self.add_miner(driver="awesome")

        set_miner_manual_firmware(
            miner_id,
            "awesome",
            "user-a",
            "pass-a",
            "T21-manual",
            "manual-fw",
            False,
        )

        row = self.row(miner_id)
        self.assertEqual(row["detection_mode"], "MANUAL")
        self.assertEqual(row["model"], "T21-manual")
        self.assertEqual(row["firmware"], "manual-fw")
        self.assertEqual(row["last_state"], "MINING")
        self.assertIsNone(row["manual_override_until"])
        self.assertIsNone(row["last_error"])

    def test_manual_firmware_driver_change_sets_unknown(self):
        miner_id = self.add_miner(driver="awesome")

        set_miner_manual_firmware(
            miner_id,
            "bitmain_stock",
            "user-b",
            "pass-b",
            "T21-stock",
            "stock-fw",
            True,
        )

        row = self.row(miner_id)
        self.assertEqual(row["driver"], "bitmain_stock")
        self.assertEqual(row["last_state"], "UNKNOWN")

    def test_detected_firmware_updates_auto_state(self):
        miner_id = self.add_miner(driver="awesome")

        set_miner_detected_firmware(
            miner_id,
            "bitmain_stock",
            "user-c",
            "pass-c",
            "T21-detected",
            "detected-fw",
            True,
        )

        row = self.row(miner_id)
        self.assertEqual(row["detection_mode"], "AUTO")
        self.assertEqual(row["driver"], "bitmain_stock")
        self.assertEqual(row["model"], "T21-detected")
        self.assertEqual(row["firmware"], "detected-fw")
        self.assertEqual(row["last_state"], "UNKNOWN")
        self.assertIsNone(row["last_error"])


if __name__ == "__main__":
    unittest.main()
