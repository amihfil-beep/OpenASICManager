import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import config
from db import db, init_db
from recurring_maintenance import policy
from recurring_maintenance.policy import (
    active_occurrences,
    next_occurrence,
    normalize_recurring_rule,
)
from recurring_maintenance.repository import (
    create_recurring_rule,
    ensure_recurring_schema,
    get_recurring_rule,
    update_recurring_rule,
)
from recurring_maintenance.service import materialize_due_occurrences


MOSCOW = ZoneInfo("Europe/Moscow")
MONDAY = 1 << 0


class RecurringMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="openasic-recurring-maintenance-"
        )
        self.database_path = str(
            Path(self.temp.name) / "test.db"
        )

        self.db_patch = patch.object(
            config,
            "DATABASE_PATH",
            self.database_path,
        )
        self.db_patch.start()

        self.policy_timezone_patch = patch.object(
            policy,
            "TIMEZONE",
            MOSCOW,
        )
        self.policy_timezone_patch.start()

        self.service_timezone_patch = patch(
            "recurring_maintenance.service.TIMEZONE",
            MOSCOW,
        )
        self.service_timezone_patch.start()

        init_db()
        ensure_recurring_schema()

    def tearDown(self):
        self.service_timezone_patch.stop()
        self.policy_timezone_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def _farm_rule(self, **changes):
        payload = {
            "enabled": True,
            "scope": "FARM",
            "miner_id": None,
            "group_id": None,
            "days_mask": MONDAY,
            "start_minutes": 7 * 60,
            "duration_minutes": 60,
            "note": "Weekly maintenance",
        }
        payload.update(changes)

        normalized = normalize_recurring_rule(payload)
        return create_recurring_rule(
            normalized=normalized,
            actor="test-operator",
            now=1_700_000_000,
        )

    def _create_group_with_miner(self):
        conn = db()
        try:
            cursor = conn.execute("""
                INSERT INTO miner_groups
                (
                    name,
                    normalized_name,
                    created_by,
                    created_at
                )
                VALUES ('Rack A', 'rack a', 'test', 1)
            """)
            group_id = int(cursor.lastrowid)

            cursor = conn.execute("""
                INSERT INTO miners
                (
                    name,
                    ip,
                    driver,
                    group_id
                )
                VALUES ('miner-one', '192.168.1.10', 'bitmain_stock', ?)
            """, (group_id,))
            miner_id = int(cursor.lastrowid)
            conn.commit()
            return group_id, miner_id
        finally:
            conn.close()

    def test_weekly_occurrence_uses_configured_timezone(self):
        rule = {
            "days_mask": MONDAY,
            "start_minutes": 7 * 60,
            "duration_minutes": 60,
        }

        during = datetime(
            2026,
            11,
            2,
            7,
            5,
            tzinfo=MOSCOW,
        )
        active = active_occurrences(rule, during)

        self.assertEqual(len(active), 1)
        self.assertEqual(
            active[0][0].isoformat(),
            "2026-11-02T07:00:00+03:00",
        )
        self.assertEqual(
            active[0][1].isoformat(),
            "2026-11-02T08:00:00+03:00",
        )

        before = datetime(
            2026,
            11,
            2,
            6,
            0,
            tzinfo=MOSCOW,
        )
        self.assertEqual(
            next_occurrence(rule, before).isoformat(),
            "2026-11-02T07:00:00+03:00",
        )

        after = datetime(
            2026,
            11,
            2,
            8,
            0,
            tzinfo=MOSCOW,
        )
        self.assertEqual(
            next_occurrence(rule, after).isoformat(),
            "2026-11-09T07:00:00+03:00",
        )

    def test_materialization_is_idempotent_and_never_queues_control(self):
        self._farm_rule()
        now = datetime(
            2026,
            11,
            2,
            7,
            5,
            tzinfo=MOSCOW,
        )

        first = materialize_due_occurrences(now)
        second = materialize_due_occurrences(now)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

        conn = db()
        try:
            window_count = conn.execute(
                "SELECT COUNT(*) AS n FROM maintenance_windows"
            ).fetchone()["n"]
            occurrence_count = conn.execute(
                "SELECT COUNT(*) AS n FROM recurring_maintenance_occurrences"
            ).fetchone()["n"]
            control_count = conn.execute(
                "SELECT COUNT(*) AS n FROM control_jobs"
            ).fetchone()["n"]
        finally:
            conn.close()

        self.assertEqual(window_count, 1)
        self.assertEqual(occurrence_count, 1)
        self.assertEqual(control_count, 0)

    def test_group_occurrence_snapshots_membership(self):
        group_id, original_miner_id = self._create_group_with_miner()

        normalized = normalize_recurring_rule({
            "enabled": True,
            "scope": "GROUP",
            "group_id": group_id,
            "miner_id": None,
            "days_mask": MONDAY,
            "start_minutes": 7 * 60,
            "duration_minutes": 60,
            "note": "Rack service",
        })
        create_recurring_rule(
            normalized=normalized,
            actor="test-operator",
            now=1_700_000_000,
        )

        now = datetime(
            2026,
            11,
            2,
            7,
            5,
            tzinfo=MOSCOW,
        )
        created = materialize_due_occurrences(now)
        self.assertEqual(len(created), 1)

        conn = db()
        try:
            conn.execute(
                "UPDATE miners SET group_id=NULL WHERE id=?",
                (original_miner_id,),
            )
            conn.execute("""
                INSERT INTO miners
                (
                    name,
                    ip,
                    driver,
                    group_id
                )
                VALUES ('miner-two', '192.168.1.11', 'bitmain_stock', ?)
            """, (group_id,))
            conn.commit()

            members = conn.execute("""
                SELECT miner_id
                FROM maintenance_window_members
                WHERE window_id=?
                ORDER BY miner_id
            """, (created[0]["window_id"],)).fetchall()
        finally:
            conn.close()

        self.assertEqual(
            [int(row["miner_id"]) for row in members],
            [original_miner_id],
        )

    def test_rule_edit_does_not_rewrite_materialized_window(self):
        row = self._farm_rule()
        now = datetime(
            2026,
            11,
            2,
            7,
            5,
            tzinfo=MOSCOW,
        )
        created = materialize_due_occurrences(now)
        self.assertEqual(len(created), 1)

        current = get_recurring_rule(row["id"])
        updated = normalize_recurring_rule(
            {
                "start_minutes": 8 * 60,
                "note": "Changed future rule",
            },
            current=current,
        )
        update_recurring_rule(
            rule_id=row["id"],
            normalized=updated,
            actor="test-editor",
            now=1_700_000_100,
        )

        conn = db()
        try:
            window = conn.execute("""
                SELECT starts_at, ends_at, note
                FROM maintenance_windows
                WHERE id=?
            """, (created[0]["window_id"],)).fetchone()
        finally:
            conn.close()

        self.assertEqual(
            int(window["starts_at"]),
            int(datetime(2026, 11, 2, 7, 0, tzinfo=MOSCOW).timestamp()),
        )
        self.assertEqual(
            window["note"],
            "Weekly maintenance",
        )


if __name__ == "__main__":
    unittest.main()
