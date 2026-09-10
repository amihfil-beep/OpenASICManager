import unittest
from unittest.mock import patch

from api.audit_auth import create_audit_auth_router


class AuditAuthRoutesTests(unittest.TestCase):
    def make_router(self, log_event=None):
        if log_event is None:
            log_event = lambda **kwargs: None
        return create_audit_auth_router(log_event)

    def endpoint(self, path, method="GET", log_event=None):
        router = self.make_router(log_event)
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    def test_router_exposes_audit_auth_paths(self):
        paths = {
            route.path
            for route in self.make_router().routes
        }
        self.assertEqual(paths, {
            "/api/audit/whoami",
            "/api/audit/test",
            "/api/auth/relogin",
        })

    @patch("api.audit_auth.current_audit_actor", return_value="WEB:alice")
    def test_whoami_returns_current_actor(self, current_actor):
        endpoint = self.endpoint(
            "/api/audit/whoami"
        )
        self.assertEqual(
            endpoint(),
            {"actor": "WEB:alice"},
        )

    @patch("api.audit_auth.current_audit_actor", return_value="LOCAL")
    def test_audit_test_preserves_event_and_actor(self, current_actor):
        events = []
        endpoint = self.endpoint(
            "/api/audit/test",
            method="POST",
            log_event=lambda **kwargs: events.append(kwargs),
        )

        result = endpoint()

        self.assertEqual(result, {
            "success": True,
            "actor": "LOCAL",
        })
        self.assertEqual(events, [{
            "source": "MANUAL",
            "action": "AUDIT_TEST",
            "success": True,
            "message": "Audit identity test",
        }])

    @patch("api.audit_auth.current_audit_actor", return_value="LOCAL")
    def test_relogin_rejects_local_access(self, current_actor):
        endpoint = self.endpoint(
            "/api/auth/relogin"
        )

        response = endpoint("alice")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.headers["cache-control"],
            "no-store",
        )

    @patch("api.audit_auth.remote_web_clear_cookie")
    @patch("api.audit_auth.current_audit_actor", return_value="WEB:alice")
    def test_relogin_same_user_returns_basic_challenge(
        self,
        current_actor,
        clear_cookie,
    ):
        endpoint = self.endpoint(
            "/api/auth/relogin"
        )

        response = endpoint("alice")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.headers["www-authenticate"],
            'Basic realm="OpenASICManager"',
        )
        clear_cookie.assert_called_once_with(response)

    @patch("api.audit_auth.remote_web_clear_cookie")
    @patch("api.audit_auth.current_audit_actor", return_value="WEB:bob")
    def test_relogin_new_user_redirects_and_audits_switch(
        self,
        current_actor,
        clear_cookie,
    ):
        events = []
        endpoint = self.endpoint(
            "/api/auth/relogin",
            log_event=lambda **kwargs: events.append(kwargs),
        )

        response = endpoint(" alice ")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/")
        self.assertEqual(events[0]["action"], "USER_SWITCH")
        self.assertEqual(
            events[0]["message"],
            "Previous user: WEB:alice",
        )
        clear_cookie.assert_called_once_with(response)


if __name__ == "__main__":
    unittest.main()
