import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import config
from db import db, init_db
from operations_calendar import read_model
from operations_calendar.read_model import (
    operations_calendar_report,
)
from recurring_maintenance import policy
from recurring_maintenance.policy import (
    normalize_recurring_rule,
    occurrences_between,
)
from recurring_maintenance.repository import (
    create_recurring_rule,
    ensure_recurring_schema,
)
from recurring_maintenance.service import (
    materialize_due_occurrences,
)


MOSCOW = ZoneInfo("Europe/Moscow")
MONDAY = 1 << 0
WEDNESDAY = 1 << 2


class OperationsCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="openasic-operations-calendar-"
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

        self.calendar_timezone_patch = patch.object(
            read_model,
            "TIMEZONE",
            MOSCOW,
        )
        self.calendar_timezone_patch.start()

        init_db()
        ensure_recurring_schema()

    def tearDown(self):
        self.calendar_timezone_patch.stop()
        self.service_timezone_patch.stop()
        self.policy_timezone_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def _create_group_miner(self):
        conn = db()

        try:
            group_cursor = conn.execute("""
                INSERT INTO miner_groups
                (
                    name,
                    normalized_name,
                    created_by,
                    created_at
                )
                VALUES ('Rack A', 'rack a', 'test', 1)
            """)
            group_id = int(group_cursor.lastrowid)

            miner_cursor = conn.execute("""
                INSERT INTO miners
                (
                    name,
                    ip,
                    driver,
                    group_id
                )
                VALUES (
                    'miner-one',
                    '192.168.1.10',
                    'bitmain_stock',
                    ?
                )
            """, (group_id,))
            miner_id = int(miner_cursor.lastrowid)

            conn.commit()
            return group_id, miner_id

        finally:
            conn.close()

    def _create_rule(
        self,
        scope="FARM",
        miner_id=None,
        group_id=None,
        days_mask=WEDNESDAY,
        start_minutes=7 * 60,
        duration_minutes=60,
        note="Recurring work",
    ):
        normalized = normalize_recurring_rule({
            "enabled": True,
            "scope": scope,
            "miner_id": miner_id,
            "group_id": group_id,
            "days_mask": days_mask,
            "start_minutes": start_minutes,
            "duration_minutes": duration_minutes,
            "note": note,
        })

        return create_recurring_rule(
            normalized=normalized,
            actor="test-operator",
            now=1_700_000_000,
        )

    def test_occurrences_between_returns_multiple_weekly_starts(self):
        rule = {
            "days_mask": WEDNESDAY,
            "start_minutes": 7 * 60,
            "duration_minutes": 90,
        }

        starts_at = datetime(
            2026,
            11,
            3,
            12,
            0,
            tzinfo=MOSCOW,
        )
        ends_before = datetime(
            2026,
            11,
            20,
            0,
            0,
            tzinfo=MOSCOW,
        )

        rows = occurrences_between(
            rule,
            starts_at,
            ends_before,
        )

        self.assertEqual(
            [item[0].isoformat() for item in rows],
            [
                "2026-11-04T07:00:00+03:00",
                "2026-11-11T07:00:00+03:00",
                "2026-11-18T07:00:00+03:00",
            ],
        )
        self.assertEqual(
            rows[0][1].isoformat(),
            "2026-11-04T08:30:00+03:00",
        )

    def test_report_combines_one_off_and_recurring_forecast(self):
        now = datetime(
            2026,
            11,
            3,
            12,
            0,
            tzinfo=MOSCOW,
        )

        conn = db()
        try:
            conn.execute("""
                INSERT INTO maintenance_windows
                (
                    scope,
                    miner_id,
                    group_id,
                    group_name,
                    starts_at,
                    ends_at,
                    note,
                    created_by,
                    created_at
                )
                VALUES (
                    'FARM',
                    NULL,
                    NULL,
                    NULL,
                    ?,
                    ?,
                    'One-off work',
                    'test',
                    ?
                )
            """, (
                int(datetime(
                    2026, 11, 5, 10, 0,
                    tzinfo=MOSCOW,
                ).timestamp()),
                int(datetime(
                    2026, 11, 5, 11, 0,
                    tzinfo=MOSCOW,
                ).timestamp()),
                int(now.timestamp()),
            ))
            conn.commit()
        finally:
            conn.close()

        self._create_rule()

        report = operations_calendar_report(
            now=now,
            upcoming_days=14,
            recent_days=7,
        )

        one_off = [
            item
            for item in report["upcoming"]
            if item["source"] == "ONE_OFF"
        ]
        recurring = [
            item
            for item in report["upcoming"]
            if item["source"] == "RECURRING"
        ]

        self.assertEqual(len(one_off), 1)
        self.assertTrue(
            one_off[0]["materialized"]
        )
        self.assertEqual(
            one_off[0]["note"],
            "One-off work",
        )

        self.assertEqual(len(recurring), 2)
        self.assertTrue(all(
            not item["materialized"]
            for item in recurring
        ))
        self.assertEqual(
            recurring[0]["starts_at_iso"],
            "2026-11-04T07:00:00+03:00",
        )

    def test_materialized_recurring_window_is_fact_not_duplicate_forecast(self):
        self._create_rule(
            days_mask=MONDAY,
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

        report = operations_calendar_report(
            now=now,
            upcoming_days=14,
            recent_days=7,
        )

        self.assertEqual(len(report["active"]), 1)
        active = report["active"][0]

        self.assertEqual(
            active["source"],
            "RECURRING",
        )
        self.assertTrue(active["materialized"])
        self.assertIsNotNone(active["rule_id"])
        self.assertIsNotNone(
            active["occurrence_key"]
        )

        same_key = [
            item
            for item in report["upcoming"]
            if (
                item["rule_id"] == active["rule_id"]
                and item["occurrence_key"]
                == active["occurrence_key"]
            )
        ]
        self.assertEqual(same_key, [])

    def test_group_forecast_uses_current_membership_for_preview_and_filter(self):
        group_id, miner_id = self._create_group_miner()
        self._create_rule(
            scope="GROUP",
            group_id=group_id,
        )

        now = datetime(
            2026,
            11,
            3,
            12,
            0,
            tzinfo=MOSCOW,
        )

        report = operations_calendar_report(
            now=now,
            miner_id=miner_id,
            upcoming_days=7,
            recent_days=7,
        )

        self.assertEqual(len(report["upcoming"]), 1)
        item = report["upcoming"][0]

        self.assertEqual(item["scope"], "GROUP")
        self.assertEqual(
            item["membership_mode"],
            "CURRENT_PREVIEW",
        )
        self.assertEqual(
            item["affected_miner_ids"],
            [miner_id],
        )
        self.assertFalse(item["materialized"])


if __name__ == "__main__":
    unittest.main()
