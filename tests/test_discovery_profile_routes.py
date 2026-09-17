import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.discovery_profiles import create_discovery_profiles_router


class DiscoveryProfileRoutesTests(unittest.TestCase):
    def make_router(self):
        return create_discovery_profiles_router(
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

    def test_router_exposes_profile_paths(self):
        paths = {
            route.path
            for route in self.make_router().routes
        }
        self.assertEqual(paths, {
            "/api/discovery/profiles",
            "/api/discovery/profiles/{profile_id}",
            "/api/discovery/profiles/scan",
        })

    @patch("api.discovery_profiles.list_saved_profiles", return_value=[])
    def test_list_returns_scan_limit(self, list_profiles):
        endpoint = self.endpoint(
            "/api/discovery/profiles",
            "GET",
        )
        result = endpoint()
        self.assertEqual(result["profiles"], [])
        self.assertEqual(result["max_scan_networks"], 8)

    @patch("api.discovery_profiles.create_saved_profile")
    def test_create_maps_validation_error_to_400(self, create_profile):
        create_profile.side_effect = ValueError("invalid network")
        endpoint = self.endpoint(
            "/api/discovery/profiles",
            "POST",
        )

        with self.assertRaises(HTTPException) as caught:
            endpoint({"name": "LAN", "network": "bad"})

        self.assertEqual(caught.exception.status_code, 400)

    @patch("api.discovery_profiles.list_discovery_miners", return_value=[])
    @patch("api.discovery_profiles.scan_saved_profiles")
    def test_scan_preserves_source_and_adds_managed_summary(
        self,
        scan_profiles,
        list_miners,
    ):
        scan_profiles.return_value = {
            "devices": [{
                "ip": "192.168.1.10",
                "driver": "bitmain_stock",
                "source_network": "192.168.1.0/24",
                "source_profile_id": 1,
                "source_profile_name": "LAN",
                "sources": [{
                    "profile_id": 1,
                    "profile_name": "LAN",
                    "network": "192.168.1.0/24",
                }],
            }],
            "networks": [],
            "total": 1,
        }

        endpoint = self.endpoint(
            "/api/discovery/profiles/scan",
            "POST",
        )
        result = endpoint({"profile_ids": [1]})

        self.assertEqual(result["managed_count"], 0)
        self.assertEqual(result["new_known"], 1)
        self.assertEqual(result["new_unknown"], 0)
        self.assertFalse(result["devices"][0]["managed"])
        self.assertEqual(
            result["devices"][0]["source_network"],
            "192.168.1.0/24",
        )

    @patch("api.discovery_profiles.scan_saved_profiles")
    def test_scan_missing_profile_maps_to_404(self, scan_profiles):
        scan_profiles.side_effect = LookupError(
            "Discovery profile not found: 99"
        )
        endpoint = self.endpoint(
            "/api/discovery/profiles/scan",
            "POST",
        )

        with self.assertRaises(HTTPException) as caught:
            endpoint({"profile_ids": [99]})

        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
