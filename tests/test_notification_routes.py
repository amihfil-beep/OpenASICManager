import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.notifications import create_notifications_router


class FakeRuntime:
    def __init__(self, result=None):
        self.result = result or {
            "success": True,
            "message": "sent",
        }
        self.calls = []

    def send_farm_summary(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)


class NotificationRoutesTests(unittest.TestCase):
    def endpoint(self, path, method, runtime=None):
        router = create_notifications_router(
            runtime or FakeRuntime()
        )
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    def test_summary_test_uses_runtime(self):
        runtime = FakeRuntime()
        endpoint = self.endpoint(
            "/api/notifications/summary/test",
            "POST",
            runtime,
        )
        response = endpoint()
        self.assertTrue(response["success"])
        self.assertEqual(
            runtime.calls,
            [{"force": True, "source": "MANUAL_TEST"}],
        )

    def test_summary_failure_maps_to_502(self):
        runtime = FakeRuntime({
            "success": False,
            "message": "no transport",
        })
        endpoint = self.endpoint(
            "/api/notifications/summary/test",
            "POST",
            runtime,
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint()
        self.assertEqual(
            caught.exception.status_code,
            502,
        )
        self.assertEqual(
            caught.exception.detail,
            "no transport",
        )

    @patch("api.notifications.telegram_transport_health")
    @patch("api.notifications.telegram_configured")
    def test_health_shape(self, configured, transport):
        configured.return_value = True
        transport.return_value = {
            "ok": True,
            "mode": "direct",
        }
        endpoint = self.endpoint(
            "/api/notifications/health",
            "GET",
        )
        data = endpoint()
        self.assertEqual(data["provider"], "telegram")
        self.assertTrue(data["configured"])
        self.assertTrue(data["transport_ok"])
        self.assertEqual(
            data["transport"]["mode"],
            "direct",
        )

    def test_router_exposes_all_notification_paths(self):
        router = create_notifications_router(
            FakeRuntime()
        )
        paths = {
            route.path
            for route in router.routes
        }
        self.assertEqual(paths, {
            "/api/notifications/summary/status",
            "/api/notifications/summary/test",
            "/api/notifications/health",
            "/api/notifications/status",
            "/api/notifications/test",
        })


if __name__ == "__main__":
    unittest.main()
