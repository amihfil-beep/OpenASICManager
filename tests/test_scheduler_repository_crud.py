import os
import tempfile
import unittest

import config as app_config
import db as db_module
from db import init_db
from scheduler.repository import (
    create_schedule_rule,
    delete_schedule_rule,
    get_schedule_rule,
    list_schedule_rules,
    set_schedule_rule_enabled,
    update_schedule_rule,
)


class SchedulerRepositoryCrudTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-scheduler-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        db_module.SCHEDULE_RULES_SCHEMA_READY = False
        init_db()

        self.normalized = {
            "enabled": True,
            "action": "PAUSE",
            "time_minutes": 7 * 60,
            "days_mask": 0b00011111,
            "scope": "ALL",
            "comment": "Morning pause",
        }

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_create_get_and_list_rule(self):
        rule = create_schedule_rule(
            self.normalized,
            1000,
        )

        fetched = get_schedule_rule(
            rule["id"]
        )
        listed = list_schedule_rules()

        self.assertEqual(fetched["action"], "PAUSE")
        self.assertEqual(fetched["effective_from"], 1000)
        self.assertEqual(fetched["created_at"], 1000)
        self.assertEqual(fetched["updated_at"], 1000)
        self.assertEqual(
            [item["id"] for item in listed],
            [rule["id"]],
        )

    def test_update_rule_and_clear_last_run_key(self):
        rule = create_schedule_rule(
            self.normalized,
            1000,
        )
        changed = dict(self.normalized)
        changed.update({
            "enabled": False,
            "action": "RESUME",
            "time_minutes": 13 * 60,
            "comment": "Lunch resume",
        })

        updated = update_schedule_rule(
            rule["id"],
            changed,
            1200,
            1300,
        )

        self.assertFalse(bool(updated["enabled"]))
        self.assertEqual(updated["action"], "RESUME")
        self.assertEqual(updated["time_minutes"], 13 * 60)
        self.assertEqual(updated["effective_from"], 1200)
        self.assertEqual(updated["updated_at"], 1300)
        self.assertIsNone(updated["last_run_key"])

    def test_toggle_persistence(self):
        rule = create_schedule_rule(
            self.normalized,
            1000,
        )

        disabled = set_schedule_rule_enabled(
            rule["id"],
            False,
            1000,
            1400,
        )
        enabled = set_schedule_rule_enabled(
            rule["id"],
            True,
            1500,
            1500,
        )

        self.assertFalse(bool(disabled["enabled"]))
        self.assertTrue(bool(enabled["enabled"]))
        self.assertEqual(enabled["effective_from"], 1500)
        self.assertEqual(enabled["updated_at"], 1500)

    def test_delete_returns_deleted_rule(self):
        rule = create_schedule_rule(
            self.normalized,
            1000,
        )

        deleted = delete_schedule_rule(
            rule["id"]
        )

        self.assertEqual(deleted["id"], rule["id"])
        self.assertIsNone(
            get_schedule_rule(rule["id"])
        )
        self.assertIsNone(
            delete_schedule_rule(rule["id"])
        )


if __name__ == "__main__":
    unittest.main()
