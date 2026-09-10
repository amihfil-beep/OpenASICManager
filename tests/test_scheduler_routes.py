import unittest
from unittest.mock import patch

from api.scheduler import create_scheduler_router


class SchedulerRoutesTests(unittest.TestCase):
    def endpoint(self, path, method, log_event=None):
        router = create_scheduler_router(
            log_event or (lambda **kwargs: None)
        )
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    def test_router_exposes_scheduler_paths(self):
        router = create_scheduler_router(
            lambda **kwargs: None
        )
        routes = {
            (route.path, method)
            for route in router.routes
            for method in route.methods
        }
        expected = {
            ("/api/schedule/rules", "GET"),
            ("/api/schedule/rules", "POST"),
            ("/api/schedule/rules/{rule_id}", "PUT"),
            ("/api/schedule/rules/{rule_id}/toggle", "POST"),
            ("/api/schedule/rules/{rule_id}", "DELETE"),
            ("/api/scheduler/toggle", "POST"),
        }
        self.assertTrue(expected.issubset(routes))

    @patch("api.scheduler.set_setting")
    @patch("api.scheduler.get_setting", return_value="0")
    def test_scheduler_toggle_enables_scheduler(
        self,
        get_setting,
        set_setting,
    ):
        events = []
        endpoint = self.endpoint(
            "/api/scheduler/toggle",
            "POST",
            events.append,
        )

        def log_event(**kwargs):
            events.append(kwargs)

        endpoint = self.endpoint(
            "/api/scheduler/toggle",
            "POST",
            log_event,
        )
        result = endpoint()

        self.assertTrue(result["scheduler_enabled"])
        get_setting.assert_called_once_with(
            "scheduler_enabled",
            "0",
        )
        set_setting.assert_called_once_with(
            "scheduler_enabled",
            "1",
        )
        self.assertEqual(events[0]["action"], "SCHEDULER_ON")
        self.assertTrue(events[0]["success"])


if __name__ == "__main__":
    unittest.main()
