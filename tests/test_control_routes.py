import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.control import create_control_router


class ControlRoutesTests(unittest.TestCase):
    def make_router(self, queue_control=None, queue_reboot=None):
        return create_control_router(
            queue_control or (lambda *args, **kwargs: {}),
            queue_reboot or (lambda *args, **kwargs: {}),
        )

    def endpoint(self, path, queue_control=None, queue_reboot=None):
        router = self.make_router(queue_control, queue_reboot)
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path and "POST" in route.methods
        )

    def test_router_exposes_control_paths(self):
        paths = {route.path for route in self.make_router().routes}
        self.assertEqual(paths, {
            "/api/miners/{miner_id}/control/{action}",
            "/api/all/{action}",
            "/api/miners/{miner_id}/reboot",
        })

    def test_miner_action_rejects_invalid_action(self):
        endpoint = self.endpoint(
            "/api/miners/{miner_id}/control/{action}"
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint(1, "invalid")
        self.assertEqual(caught.exception.status_code, 400)

    def test_all_action_rejects_invalid_action(self):
        endpoint = self.endpoint("/api/all/{action}")
        with self.assertRaises(HTTPException) as caught:
            endpoint("invalid")
        self.assertEqual(caught.exception.status_code, 400)

    def test_miner_action_delegates_manual_control(self):
        calls = []

        def queue(miner_id, action, manual=False):
            calls.append((miner_id, action, manual))
            return {"job_id": 9}

        endpoint = self.endpoint(
            "/api/miners/{miner_id}/control/{action}",
            queue_control=queue,
        )
        result = endpoint(7, "pause")
        self.assertEqual(calls, [(7, "pause", True)])
        self.assertEqual(result, {"success": True, "job_id": 9})

    @patch("api.control.get_control_miners")
    def test_all_action_preserves_per_miner_results(self, get_miners):
        get_miners.return_value = [
            {"id": 1, "ip": "192.0.2.1"},
            {"id": 2, "ip": "192.0.2.2"},
        ]

        def queue(miner_id, action, manual=False):
            if miner_id == 2:
                raise RuntimeError("boom")
            return {"job_id": 11}

        endpoint = self.endpoint(
            "/api/all/{action}",
            queue_control=queue,
        )
        result = endpoint("resume")
        self.assertEqual(result["results"][0], {
            "ip": "192.0.2.1",
            "success": True,
            "job_id": 11,
        })
        self.assertEqual(result["results"][1], {
            "ip": "192.0.2.2",
            "success": False,
            "error": "boom",
        })

    def test_reboot_delegates(self):
        calls = []

        def reboot(miner_id):
            calls.append(miner_id)
            return {"job_id": 22}

        endpoint = self.endpoint(
            "/api/miners/{miner_id}/reboot",
            queue_reboot=reboot,
        )
        result = endpoint(3)
        self.assertEqual(calls, [3])
        self.assertEqual(result, {"success": True, "job_id": 22})

    def test_control_exception_is_wrapped_as_http_500(self):
        def queue(*args, **kwargs):
            raise ValueError("bad")

        endpoint = self.endpoint(
            "/api/miners/{miner_id}/control/{action}",
            queue_control=queue,
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint(1, "pause")
        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(caught.exception.detail, "ValueError: bad")


if __name__ == "__main__":
    unittest.main()
