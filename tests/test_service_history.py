import os
import sqlite3
import tempfile
import unittest

import config as app_config
import db as db_module

from db import init_db
from service_history.repository import (
    create_service_record,
    latest_service_record,
    list_service_records,
)


class ServiceHistoryRepositoryTests(
    unittest.TestCase
):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-service-history-",
            suffix=".db",
        )

        os.close(fd)
        os.unlink(path)

        self.path = path

        self.original_db = (
            app_config.DATABASE_PATH
        )

        app_config.DATABASE_PATH = path

        init_db()

        conn = db_module.db()

        self.miner_id = conn.execute(
            """
            INSERT INTO miners
            (
                name,
                ip,
                driver
            )
            VALUES (
                'T21-01',
                '192.168.10.21',
                'bitmain_stock'
            )
            """
        ).lastrowid

        conn.commit()
        conn.close()

    def tearDown(self):
        app_config.DATABASE_PATH = (
            self.original_db
        )

        for suffix in (
            "",
            "-shm",
            "-wal",
        ):
            try:
                os.unlink(
                    self.path + suffix
                )
            except FileNotFoundError:
                pass

    def test_service_history_stays_with_miner(
        self,
    ):
        first = create_service_record(
            miner_id=self.miner_id,
            performed_at=1000,
            work_type="CLEANING",
            description="Dust removed",
            part_serial=None,
            performed_by="Technician A",
            created_by="TEST",
            created_at=1100,
        )

        second = create_service_record(
            miner_id=self.miner_id,
            performed_at=2000,
            work_type="FAN_REPLACEMENT",
            description="Fan 2 replaced",
            part_serial="FAN-SN-001",
            performed_by="Technician B",
            created_by="TEST",
            created_at=2100,
        )

        self.assertEqual(
            first["miner_id"],
            self.miner_id,
        )

        self.assertEqual(
            second["work_type"],
            "FAN_REPLACEMENT",
        )

        conn = db_module.db()

        conn.execute(
            """
            UPDATE miners
            SET ip='192.168.10.99'
            WHERE id=?
            """,
            (
                self.miner_id,
            ),
        )

        conn.commit()
        conn.close()

        rows = list_service_records(
            self.miner_id
        )

        self.assertEqual(
            [
                row["id"]
                for row in rows
            ],
            [
                second["id"],
                first["id"],
            ],
        )

        self.assertEqual(
            rows[0]["part_serial"],
            "FAN-SN-001",
        )

        latest = latest_service_record(
            self.miner_id
        )

        self.assertEqual(
            latest["id"],
            second["id"],
        )

    def test_invalid_work_type_is_rejected(
        self,
    ):
        with self.assertRaises(
            sqlite3.IntegrityError
        ):
            create_service_record(
                miner_id=self.miner_id,
                performed_at=1000,
                work_type="UNSUPPORTED",
                description="Invalid",
                part_serial=None,
                performed_by="TEST",
                created_by="TEST",
                created_at=1000,
            )


if __name__ == "__main__":
    unittest.main()
