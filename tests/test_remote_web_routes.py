import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from api.remote_web import create_remote_web_router


class RemoteWebRoutesTests(unittest.TestCase):
    def make_router(self, log_event=None):
        if log_event is None:
            log_event = lambda **kwargs: None
        return create_remote_web_router(log_event)

    def endpoint(self, path, method="GET", log_event=None):
        router = self.make_router(log_event)
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    def test_router_exposes_remote_web_paths(self):
        paths = {
            route.path
            for route in self.make_router().routes
        }
        self.assertEqual(paths, {
            "/remote/{miner_id}",
            "/api/remote/authorize",
        })

    @patch("api.remote_web.app_config.REMOTE_WEB_ENABLED", False)
    def test_open_rejects_disabled_remote_web(self):
        endpoint = self.endpoint(
            "/remote/{miner_id}"
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint(1)
        self.assertEqual(caught.exception.status_code, 503)

    @patch("api.remote_web.app_config.REMOTE_WEB_ENABLED", True)
    @patch("api.remote_web.remote_web_cookie_scope_valid", return_value=True)
    @patch("api.remote_web.current_audit_actor", return_value="LOCAL")
    def test_open_requires_authenticated_web_actor(
        self,
        actor,
        scope_valid,
    ):
        endpoint = self.endpoint(
            "/remote/{miner_id}"
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint(1)
        self.assertEqual(caught.exception.status_code, 403)

    @patch("api.remote_web.app_config.REMOTE_WEB_ENABLED", True)
    @patch("api.remote_web.remote_web_cookie_scope_valid", return_value=True)
    @patch("api.remote_web.current_audit_actor", return_value="WEB:alice")
    @patch("api.remote_web.get_miner")
    @patch("api.remote_web.remote_web_host_for_ip")
    @patch("api.remote_web.remote_web_make_token", return_value="signed-token")
    def test_open_preserves_redirect_cookie_and_audit(
        self,
        make_token,
        host_for_ip,
        get_miner,
        actor,
        scope_valid,
    ):
        miner = {
            "id": 7,
            "name": "TEST-ASIC",
            "ip": "192.0.2.7",
        }
        get_miner.return_value = miner
        host_for_ip.return_value = "m192-0-2-7.remote.example.com"
        events = []
        endpoint = self.endpoint(
            "/remote/{miner_id}",
            log_event=lambda **kwargs: events.append(kwargs),
        )

        response = endpoint(7)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["location"],
            "https://m192-0-2-7.remote.example.com/",
        )
        self.assertIn("asic_remote_session=signed-token", response.headers["set-cookie"])
        self.assertEqual(events[0]["action"], "REMOTE_WEB_OPEN")
        self.assertEqual(events[0]["miner"], miner)

    @patch("api.remote_web.app_config.REMOTE_WEB_ENABLED", True)
    @patch("api.remote_web.remote_web_verify_token", return_value=None)
    def test_authorize_rejects_invalid_token(self, verify_token):
        endpoint = self.endpoint(
            "/api/remote/authorize"
        )
        request = SimpleNamespace(
            cookies={"asic_remote_session": "bad"},
            headers={"x-remote-host": "m192-0-2-7.remote.example.com"},
        )
        response = endpoint(request)
        self.assertEqual(response.status_code, 401)

    @patch("api.remote_web.app_config.REMOTE_WEB_ENABLED", True)
    @patch(
        "api.remote_web.remote_web_verify_token",
        return_value={"actor": "WEB:alice"},
    )
    @patch("api.remote_web.remote_web_miner_for_host", return_value={"id": 7})
    def test_authorize_preserves_actor_header(
        self,
        miner_for_host,
        verify_token,
    ):
        endpoint = self.endpoint(
            "/api/remote/authorize"
        )
        request = SimpleNamespace(
            cookies={"asic_remote_session": "good"},
            headers={"x-remote-host": "m192-0-2-7.remote.example.com"},
        )
        response = endpoint(request)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            response.headers["x-remote-actor"],
            "WEB:alice",
        )


if __name__ == "__main__":
    unittest.main()
