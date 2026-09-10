import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.telemetry import create_telemetry_router


class TelemetryRoutesTests(unittest.TestCase):
    def endpoint(self, path, method):
        router = create_telemetry_router()
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    def test_router_exposes_history_paths(self):
        router = create_telemetry_router()
        paths = [route.path for route in router.routes]
        self.assertIn(
            "/api/history/stats/summary",
            paths,
        )
        self.assertIn(
            "/api/history/{miner_id}",
            paths,
        )
        self.assertIn(
            "/api/farm/history",
            paths,
        )
        self.assertLess(
            paths.index("/api/history/stats/summary"),
            paths.index("/api/history/{miner_id}"),
        )

    @patch("api.telemetry.history_stats")
    def test_history_stats_endpoint_uses_analytics(self, stats):
        expected = {
            "rows": 10,
            "oldest": None,
            "newest": None,
            "interval_seconds": 300,
            "retention_days": 90,
        }
        stats.return_value = expected
        endpoint = self.endpoint(
            "/api/history/stats/summary",
            "GET",
        )
        self.assertEqual(endpoint(), expected)
        stats.assert_called_once_with()

    def test_history_rejects_invalid_hours(self):
        endpoint = self.endpoint(
            "/api/history/{miner_id}",
            "GET",
        )
        with patch("api.telemetry.get_miner") as get_miner:
            get_miner.return_value = {
                "id": 1,
                "ip": "192.0.2.1",
                "name": "test",
                "driver": "bitmain_stock",
            }
            with self.assertRaises(HTTPException) as caught:
                endpoint(1, 2)
        self.assertEqual(caught.exception.status_code, 400)

    def test_farm_history_rejects_invalid_hours(self):
        endpoint = self.endpoint(
            "/api/farm/history",
            "GET",
        )
        with self.assertRaises(HTTPException) as caught:
            endpoint(1)
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
