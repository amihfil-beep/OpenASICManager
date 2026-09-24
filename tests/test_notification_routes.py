import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.notifications import create_notifications_router


class FakeRequest:

    def __init__(
        self,
        payload,
    ):
        self.payload = payload

    async def json(self):
        return self.payload


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


class NotificationRoutesTests(
    unittest.IsolatedAsyncioTestCase
):
    def endpoint(
        self,
        path,
        method,
        runtime=None,
        log_event=None,
    ):
        router = create_notifications_router(
            runtime or FakeRuntime(),
            log_event or (
                lambda **kwargs: None
            ),
        )
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    @patch(
        "api.notifications.load_notification_policy"
    )
    def test_policy_get_is_secret_free(
        self,
        load_policy,
    ):
        load_policy.return_value = {
            "issue_open": True,
            "issue_resolved": True,
            "acknowledgement": False,
            "control_failure": True,
            "maintenance": False,
        }

        endpoint = self.endpoint(
            "/api/notifications/policy",
            "GET",
        )

        data = endpoint()

        self.assertEqual(
            data["provider"],
            "telegram",
        )

        self.assertTrue(
            data["farm_summary_independent"]
        )

        rendered = repr(
            data
        ).lower()

        self.assertNotIn(
            "token",
            rendered,
        )

        self.assertNotIn(
            "chat_id",
            rendered,
        )

        self.assertNotIn(
            "proxy",
            rendered,
        )


    @patch(
        "api.notifications.current_audit_actor",
        return_value="TEST-OPERATOR",
    )
    @patch(
        "api.notifications.save_notification_policy"
    )
    @patch(
        "api.notifications.load_notification_policy"
    )
    async def test_policy_put_persists_and_audits(
        self,
        load_policy,
        save_policy,
        actor,
    ):
        current = {
            "issue_open": True,
            "issue_resolved": True,
            "acknowledgement": False,
            "control_failure": True,
            "maintenance": False,
        }

        updated = dict(
            current
        )

        updated[
            "acknowledgement"
        ] = True

        load_policy.return_value = current
        save_policy.return_value = updated

        events = []

        endpoint = self.endpoint(
            "/api/notifications/policy",
            "PUT",
            log_event=(
                lambda **kwargs:
                    events.append(kwargs)
            ),
        )

        result = await endpoint(
            FakeRequest({
                "acknowledgement": True,
            })
        )

        self.assertTrue(
            result["success"]
        )

        self.assertTrue(
            result["policy"][
                "acknowledgement"
            ]
        )

        save_policy.assert_called_once_with(
            updated
        )

        self.assertEqual(
            len(events),
            1,
        )

        self.assertEqual(
            events[0]["action"],
            "NOTIFICATION_POLICY_UPDATE",
        )

        self.assertNotIn(
            "token",
            events[0]["message"].lower(),
        )

        self.assertNotIn(
            "chat",
            events[0]["message"].lower(),
        )

        self.assertNotIn(
            "proxy",
            events[0]["message"].lower(),
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
            FakeRuntime(),
            lambda **kwargs: None,
        )
        paths = {
            route.path
            for route in router.routes
        }
        self.assertEqual(paths, {
            "/api/notifications/policy",
            "/api/notifications/summary/status",
            "/api/notifications/summary/test",
            "/api/notifications/health",
            "/api/notifications/status",
            "/api/notifications/test",
        })


if __name__ == "__main__":
    unittest.main()
