import unittest
from datetime import datetime
from unittest.mock import patch

from api.system import create_system_router
from app_version import APP_VERSION


class SystemRoutesTests(unittest.TestCase):
    def make_router(self):
        return create_system_router()

    def endpoint(self, path):
        router = self.make_router()
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and "GET" in route.methods
        )

    def test_router_exposes_health_and_status(self):
        paths = {
            route.path
            for route in self.make_router().routes
        }
        self.assertEqual(paths, {
            "/health",
            "/api/status",
        })

    def test_health_preserves_version_and_timestamp(self):
        scheduler = {
            "status": "ok",
            "healthy": True,
            "max_age_seconds": 60,
            "started_at": 100.0,
            "last_success_at": 120.0,
            "last_success_age_seconds": 1.0,
            "last_error_at": None,
            "last_error": None,
        }

        with patch(
            "api.system.scheduler_health_snapshot",
            return_value=scheduler,
        ):
            result = self.endpoint("/health")()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], APP_VERSION)
        self.assertEqual(result["scheduler"], scheduler)
        parsed = datetime.fromisoformat(result["time"])
        self.assertIsNotNone(parsed.tzinfo)

    def test_health_degrades_when_scheduler_heartbeat_is_unhealthy(self):
        scheduler = {
            "status": "stale",
            "healthy": False,
            "max_age_seconds": 60,
            "started_at": 100.0,
            "last_success_at": 120.0,
            "last_success_age_seconds": 90.0,
            "last_error_at": 130.0,
            "last_error": "unable to open database file",
        }

        with patch(
            "api.system.scheduler_health_snapshot",
            return_value=scheduler,
        ):
            result = self.endpoint("/health")()

        self.assertEqual(result["status"], "degraded")
        self.assertFalse(result["scheduler"]["healthy"])
        self.assertEqual(
            result["scheduler"]["last_error"],
            "unable to open database file",
        )

    def test_status_delegates_to_existing_read_models(self):
        rows = [{"id": 1, "ip": "192.0.2.10"}]
        jobs = [{"miner_id": 1, "action": "pause"}]
        miners = [{
            "id": 1,
            "state": "MINING",
            "enabled": True,
            "schedule_enabled": True,
            "driver": "awesome",
            "group_id": 7,
        }]
        upcoming = datetime.fromisoformat(
            "2026-09-10T21:00:00+03:00"
        )

        schedule_states = {
            1: {
                "desired_state":
                    "PAUSED",

                "source_scope":
                    "GROUP",

                "rule": {
                    "id":
                        17,

                    "action":
                        "PAUSE",
                },

                "next_transition":
                    upcoming,
            },
        }

        with patch("api.system.list_miners", return_value=rows), \
             patch("api.system.list_active_control_jobs", return_value=jobs), \
             patch("api.system.get_setting", return_value="1"), \
             patch("api.system.next_transition", return_value=upcoming) as next_run, \
             patch("api.system.desired_state", return_value="MINING") as desired, \
             patch(
                 "api.system.effective_schedule_states",
                 return_value=schedule_states,
             ) as effective_states, \
             patch(
                 "api.system.latest_service_records_by_miner",
                 return_value={
                     1: {
                         "id": 41,
                         "miner_id": 1,
                         "performed_at": 1700000000,
                         "work_type": "FAN_REPLACEMENT",
                         "description": "Fan 2 replaced",
                         "part_serial": "FAN-001",
                         "performed_by": "Technician A",
                     },
                 },
             ) as latest_service, \
             patch("api.system.miner_status_items", return_value=miners) as status_items:

            result = self.endpoint("/api/status")()

        self.assertEqual(result["version"], APP_VERSION)
        self.assertTrue(result["scheduler_enabled"])
        self.assertEqual(result["desired_state"], "MINING")
        self.assertEqual(
            result["next_transition"],
            "2026-09-10T21:00:00+03:00",
        )
        self.assertEqual(
            result[
                "miners"
            ][
                0
            ][
                "schedule_context"
            ][
                "source_scope"
            ],
            "GROUP",
        )
        self.assertEqual(
            result[
                "miners"
            ][
                0
            ][
                "schedule_context"
            ][
                "desired_state"
            ],
            "PAUSED",
        )
        self.assertEqual(
            result[
                "miners"
            ][
                0
            ][
                "schedule_context"
            ][
                "rule_id"
            ],
            17,
        )
        self.assertTrue(
            result[
                "miners"
            ][
                0
            ][
                "schedule_context"
            ][
                "applicable"
            ]
        )

        self.assertEqual(
            result[
                "miners"
            ][
                0
            ][
                "last_service"
            ],
            {
                "id": 41,
                "performed_at": 1700000000,
                "work_type": "FAN_REPLACEMENT",
                "description": "Fan 2 replaced",
                "part_serial": "FAN-001",
                "performed_by": "Technician A",
            },
        )

        status_items.assert_called_once_with(rows, jobs)
        latest_service.assert_called_once_with()
        effective_states.assert_called_once()
        self.assertEqual(
            effective_states.call_args.args[0],
            rows,
        )
        self.assertEqual(next_run.call_count, 1)
        self.assertEqual(desired.call_count, 1)
        self.assertIs(next_run.call_args.args[0], desired.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
