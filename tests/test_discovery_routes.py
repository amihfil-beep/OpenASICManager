import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.discovery import create_discovery_router


class DiscoveryRoutesTests(unittest.TestCase):
    def make_router(self):
        return create_discovery_router(
            lambda miner_id: None,
            lambda **kwargs: None,
        )

    def endpoint(self, path, method):
        router = self.make_router()
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    def test_router_exposes_discovery_paths(self):
        paths = {
            route.path
            for route in self.make_router().routes
        }
        self.assertEqual(paths, {
            "/api/discovery/scan",
            "/api/discovery/add",
        })

    def test_scan_requires_network(self):
        endpoint = self.endpoint(
            "/api/discovery/scan",
            "POST",
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint({})
        self.assertEqual(caught.exception.status_code, 400)

    def test_add_requires_ip_list(self):
        endpoint = self.endpoint(
            "/api/discovery/add",
            "POST",
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint({"ips": "192.0.2.1"})
        self.assertEqual(caught.exception.status_code, 400)

    def test_add_rejects_public_ip(self):
        endpoint = self.endpoint(
            "/api/discovery/add",
            "POST",
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint({"ips": ["203.0.113.10"]})
        self.assertEqual(caught.exception.status_code, 400)

    @patch("api.discovery.list_discovery_miners", return_value=[])
    @patch("api.discovery.scan_network")
    def test_scan_preserves_discovery_summary(self, scan, list_miners):
        scan.return_value = {
            "devices": [{
                "ip": "192.168.1.10",
                "driver": "bitmain_stock",
            }]
        }
        endpoint = self.endpoint(
            "/api/discovery/scan",
            "POST",
        )
        result = endpoint({"network": "192.168.1.0/24"})
        self.assertEqual(result["managed_count"], 0)
        self.assertEqual(result["new_known"], 1)
        self.assertEqual(result["new_unknown"], 0)
        self.assertFalse(result["devices"][0]["managed"])


if __name__ == "__main__":
    unittest.main()
