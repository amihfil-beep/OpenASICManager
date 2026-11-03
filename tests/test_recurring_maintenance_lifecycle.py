import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import config
from db import db, init_db
from maintenance_windows.repository import end_maintenance_window
from notifications.policy import notification_delivery_allowed
from recurring_maintenance import lifecycle, policy, service
from recurring_maintenance.lifecycle import occurrence_history_item
from recurring_maintenance.lifecycle_repository import (
    claim_lifecycle_events,
    list_occurrence_history,
)
from recurring_maintenance.policy import normalize_recurring_rule
from recurring_maintenance.repository import (
    create_recurring_rule,
    ensure_recurring_schema,
)
from recurring_maintenance.service import (
    MaterializerHealthState,
    materialize_due_occurrences,
    recurring_maintenance_iteration,
)


MOSCOW = ZoneInfo("Europe/Moscow")
MONDAY = 1 << 0


class RecurringMaintenanceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="openasic-recurring-lifecycle-"
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

        self.service_timezone_patch = patch.object(
            service,
            "TIMEZONE",
            MOSCOW,
        )
        self.service_timezone_patch.start()

        self.lifecycle_timezone_patch = patch.object(
            lifecycle,
            "TIMEZONE",
            MOSCOW,
        )
        self.lifecycle_timezone_patch.start()

        init_db()
        ensure_recurring_schema()

    def tearDown(self):
        self.lifecycle_timezone_patch.stop()
        self.service_timezone_patch.stop()
        self.policy_timezone_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def _create_farm_rule(
        self,
        created_at,
        duration_minutes=60,
    ):
        normalized = normalize_recurring_rule({
            "enabled": True,
            "scope": "FARM",
            "miner_id": None,
            "group_id": None,
            "days_mask": MONDAY,
            "start_minutes": 7 * 60,
            "duration_minutes": duration_minutes,
            "note": "Weekly maintenance",
        })

        return create_recurring_rule(
            normalized=normalized,
            actor="test-operator",
            now=int(created_at.timestamp()),
        )

    def test_restart_catchup_materializes_recent_completed_occurrence_once(self):
        created_at = datetime(
            2026, 11, 1, 12, 0,
            tzinfo=MOSCOW,
        )
        self._create_farm_rule(created_at)

        now = datetime(
            2026, 11, 4, 12, 0,
            tzinfo=MOSCOW,
        )

        first = materialize_due_occurrences(now)
        second = materialize_due_occurrences(now)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

        events = claim_lifecycle_events(
            int(now.timestamp())
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["action"],
            "MAINTENANCE_RECURRING_COMPLETE",
        )
        self.assertEqual(
            claim_lifecycle_events(
                int(now.timestamp())
            ),
            [],
        )

        rows = list_occurrence_history()
        self.assertEqual(len(rows), 1)
        item = occurrence_history_item(
            rows[0],
            int(now.timestamp()),
        )

        self.assertEqual(item["state"], "COMPLETED")
        self.assertEqual(
            item["starts_at_iso"],
            "2026-11-02T07:00:00+03:00",
        )
        self.assertIsNotNone(
            item["completed_event_at"]
        )

    def test_active_occurrence_emits_start_then_early_end_emits_cancel_once(self):
        created_at = datetime(
            2026, 11, 1, 12, 0,
            tzinfo=MOSCOW,
        )
        self._create_farm_rule(
            created_at,
            duration_minutes=120,
        )

        active_now = datetime(
            2026, 11, 2, 7, 5,
            tzinfo=MOSCOW,
        )
        created = materialize_due_occurrences(
            active_now
        )
        self.assertEqual(len(created), 1)

        start_events = claim_lifecycle_events(
            int(active_now.timestamp())
        )
        self.assertEqual(
            [item["action"] for item in start_events],
            ["MAINTENANCE_RECURRING_START"],
        )

        cancelled_at = datetime(
            2026, 11, 2, 7, 10,
            tzinfo=MOSCOW,
        )
        ended = end_maintenance_window(
            window_id=created[0]["window_id"],
            actor="test-operator",
            now=int(cancelled_at.timestamp()),
        )
        self.assertIsNotNone(ended)

        cancel_events = claim_lifecycle_events(
            int(cancelled_at.timestamp())
        )
        self.assertEqual(
            [item["action"] for item in cancel_events],
            ["MAINTENANCE_RECURRING_CANCEL"],
        )
        self.assertEqual(
            claim_lifecycle_events(
                int(cancelled_at.timestamp())
            ),
            [],
        )

        item = occurrence_history_item(
            list_occurrence_history()[0],
            int(cancelled_at.timestamp()),
        )
        self.assertEqual(item["state"], "CANCELLED")
        self.assertIsNotNone(
            item["cancelled_event_at"]
        )

    def test_iteration_uses_existing_maintenance_notification_policy_class(self):
        created_at = datetime(
            2026, 11, 1, 12, 0,
            tzinfo=MOSCOW,
        )
        self._create_farm_rule(created_at)

        log_event = Mock()
        runtime = service.RecurringMaintenanceRuntime(
            stop_event=threading.Event(),
            log_event=log_event,
        )
        now = datetime(
            2026, 11, 2, 7, 5,
            tzinfo=MOSCOW,
        )

        result = recurring_maintenance_iteration(
            runtime,
            now=now,
        )

        self.assertEqual(
            result["materialized_count"],
            1,
        )
        self.assertEqual(
            result["lifecycle_event_count"],
            1,
        )

        actions = [
            call.kwargs["action"]
            for call in log_event.call_args_list
        ]
        self.assertIn(
            "MAINTENANCE_RECURRING_MATERIALIZE",
            actions,
        )
        self.assertIn(
            "MAINTENANCE_CREATE",
            actions,
        )

        policy_data = {
            "issue_open": True,
            "issue_resolved": True,
            "acknowledgement": False,
            "control_failure": True,
            "maintenance": True,
        }
        self.assertTrue(
            notification_delivery_allowed(
                "MAINTENANCE_CREATE",
                policy_data,
            )
        )

        conn = db()
        try:
            control_count = conn.execute(
                "SELECT COUNT(*) AS c FROM control_jobs"
            ).fetchone()["c"]
        finally:
            conn.close()

        self.assertEqual(control_count, 0)

    def test_materializer_health_marks_stale_heartbeat_unhealthy(self):
        state = MaterializerHealthState()
        state.mark_started(now=100.0)
        state.mark_success(
            materialized_count=2,
            lifecycle_event_count=1,
            now=120.0,
        )

        healthy = state.snapshot(now=121.0)
        self.assertTrue(healthy["healthy"])
        self.assertEqual(
            healthy["last_materialized_count"],
            2,
        )

        stale = state.snapshot(now=1000.0)
        self.assertFalse(stale["healthy"])
        self.assertEqual(stale["status"], "stale")


if __name__ == "__main__":
    unittest.main()
