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
        result = self.endpoint("/health")()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], APP_VERSION)
        parsed = datetime.fromisoformat(result["time"])
        self.assertIsNotNone(parsed.tzinfo)

    def test_status_delegates_to_existing_read_models(self):
        rows = [{"id": 1, "ip": "192.0.2.10"}]
        jobs = [{"miner_id": 1, "action": "pause"}]
        miners = [{"id": 1, "state": "MINING"}]
        upcoming = datetime.fromisoformat(
            "2026-09-10T21:00:00+03:00"
        )

        with patch("api.system.list_miners", return_value=rows), \
             patch("api.system.list_active_control_jobs", return_value=jobs), \
             patch("api.system.get_setting", return_value="1"), \
             patch("api.system.next_transition", return_value=upcoming) as next_run, \
             patch("api.system.desired_state", return_value="MINING") as desired, \
             patch("api.system.miner_status_items", return_value=miners) as status_items:

            result = self.endpoint("/api/status")()

        self.assertEqual(result["version"], APP_VERSION)
        self.assertTrue(result["scheduler_enabled"])
        self.assertEqual(result["desired_state"], "MINING")
        self.assertEqual(
            result["next_transition"],
            "2026-09-10T21:00:00+03:00",
        )
        self.assertEqual(result["miners"], miners)
        status_items.assert_called_once_with(rows, jobs)
        self.assertEqual(next_run.call_count, 1)
        self.assertEqual(desired.call_count, 1)
        self.assertIs(next_run.call_args.args[0], desired.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
