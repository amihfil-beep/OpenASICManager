import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.inventory import create_inventory_router


class InventoryRoutesTests(unittest.TestCase):
    def setUp(self):
        self.polled = []
        self.events = []

    def make_router(self):
        return create_inventory_router(
            self.polled.append,
            lambda **kwargs: self.events.append(kwargs),
        )

    def endpoint(self, path, method="POST"):
        return next(
            route.endpoint
            for route in self.make_router().routes
            if route.path == path
            and method in route.methods
        )

    def test_router_exposes_inventory_paths(self):
        paths = {
            route.path
            for route in self.make_router().routes
        }
        self.assertEqual(paths, {
            "/api/miners/{miner_id}/driver",
            "/api/miners/{miner_id}/firmware-settings",
            "/api/miners/{miner_id}/firmware-detect",
            "/api/miners/{miner_id}/enabled",
            "/api/miners/{miner_id}/schedule",
            "/api/bulk/unconfigured-stock",
            "/api/schedule/all/{state}",
            "/api/overrides/clear",
        })

    def test_driver_rejects_invalid_value(self):
        endpoint = self.endpoint(
            "/api/miners/{miner_id}/driver"
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint(1, {"driver": "unsupported"})
        self.assertEqual(caught.exception.status_code, 400)

    @patch("api.inventory.get_miner", return_value=None)
    def test_toggle_enabled_requires_miner(self, get_miner):
        endpoint = self.endpoint(
            "/api/miners/{miner_id}/enabled"
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint(999)
        self.assertEqual(caught.exception.status_code, 404)

    def test_schedule_all_rejects_invalid_state(self):
        endpoint = self.endpoint(
            "/api/schedule/all/{state}"
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint("maybe")
        self.assertEqual(caught.exception.status_code, 400)

    @patch("api.inventory.set_miner_enabled")
    @patch("api.inventory.get_miner")
    @patch("api.inventory.threading.Thread")
    def test_enabling_miner_starts_immediate_poll(
        self,
        thread_cls,
        get_miner,
        set_enabled,
    ):
        get_miner.return_value = {
            "id": 7,
            "enabled": 0,
        }
        endpoint = self.endpoint(
            "/api/miners/{miner_id}/enabled"
        )
        result = endpoint(7)
        self.assertTrue(result["enabled"])
        set_enabled.assert_called_once_with(7, True)
        thread_cls.assert_called_once()
        thread_cls.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
