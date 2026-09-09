import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import config as app_config
from audit.identity import (
    bind_audit_actor,
    reset_audit_actor,
)
from audit import service
from db import db, init_db


class AuditServiceTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-audit-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        init_db()
        self.notify = Mock()
        self.runtime = service.AuditRuntime(
            notify_event=self.notify
        )

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def rows(self):
        conn = db()
        rows = conn.execute(
            "SELECT * FROM action_log ORDER BY id"
        ).fetchall()
        conn.close()
        return rows

    def test_log_event_persists_and_attributes_web_actor(self):
        token = bind_audit_actor("WEB:alice")
        try:
            self.runtime.log_event(
                source="MANUAL",
                action="TEST_ACTION",
                miner={
                    "id": 7,
                    "ip": "192.0.2.7",
                    "name": "TEST-ASIC",
                },
                success=True,
                message="ok",
            )
        finally:
            reset_audit_actor(token)

        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "WEB:alice")
        self.assertEqual(rows[0]["action"], "TEST_ACTION")
        self.assertEqual(rows[0]["miner_id"], 7)
        self.assertEqual(rows[0]["ip"], "192.0.2.7")
        self.assertEqual(rows[0]["name"], "TEST-ASIC")
        self.assertEqual(rows[0]["success"], 1)
        self.assertEqual(rows[0]["message"], "ok")

        self.notify.assert_called_once_with(
            source="WEB:alice",
            action="TEST_ACTION",
            miner={
                "id": 7,
                "ip": "192.0.2.7",
                "name": "TEST-ASIC",
            },
            success=True,
            message="ok",
        )

    def test_message_is_truncated_for_storage_and_notification(self):
        message = "x" * 1200

        self.runtime.log_event(
            source="SYSTEM",
            action="LONG_MESSAGE",
            message=message,
        )

        rows = self.rows()
        self.assertEqual(
            len(rows[0]["message"]),
            service.MAX_AUDIT_MESSAGE_LENGTH,
        )
        self.assertEqual(
            len(self.notify.call_args.kwargs["message"]),
            service.MAX_AUDIT_MESSAGE_LENGTH,
        )

    def test_persistence_failure_does_not_block_notification(self):
        with patch.object(
            service,
            "write_action_log",
            side_effect=RuntimeError("db failure"),
        ):
            self.runtime.log_event(
                source="SYSTEM",
                action="TEST_FAILURE",
                message="still notify",
            )

        self.notify.assert_called_once_with(
            source="SYSTEM",
            action="TEST_FAILURE",
            miner=None,
            success=True,
            message="still notify",
        )

    def test_action_log_entries_are_newest_first(self):
        self.runtime.log_event(
            source="SYSTEM",
            action="FIRST",
        )
        self.runtime.log_event(
            source="SYSTEM",
            action="SECOND",
        )

        entries = service.action_log_entries(
            1000,
            ZoneInfo("UTC"),
        )

        self.assertEqual(
            [entry["action"] for entry in entries],
            ["SECOND", "FIRST"],
        )
        self.assertTrue(
            entries[0]["time"].endswith("+00:00")
        )


if __name__ == "__main__":
    unittest.main()
