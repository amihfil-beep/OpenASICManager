"""Offline CGI telemetry tests; synthetic Bitmain API response shapes."""

import unittest

from drivers.bitmain_cgi_telemetry import normalize_cgi_telemetry


class CGIReadOnlyParserTests(unittest.TestCase):
    def test_documented_cgi_fields_become_conservative_metrics(self):
        result = normalize_cgi_telemetry(
            summary_payload={
                "SUMMARY": [{
                    "rate_5s": 232436.76,
                    "rate_avg": 231429.72,
                    "rate_unit": "GH/s",
                }]
            },
            stats_payload={
                "STATS": [{
                    "watt": 3510,
                    "miner-mode": 0,
                    "chain": [
                        {"temp_chip": [55, 65, 255]},
                        {"temp_chip": [54, 64]},
                    ],
                }]
            },
            pools_payload={
                "POOLS": [{
                    "status": "Alive",
                    "url": "stratum+tcp://pool.example.invalid:3333",
                }]
            },
        )

        self.assertAlmostEqual(result["hashrate"], 232.43676)
        self.assertAlmostEqual(result["avg_hashrate"], 231.42972)
        self.assertEqual(result["power"], 3510.0)
        self.assertEqual(result["temp"], 65.0)
        self.assertEqual(
            result["pool"],
            "stratum+tcp://pool.example.invalid:3333",
        )
        self.assertEqual(result["observed_work_mode"], 0)
        # The parser is not wired into control verification and must
        # not invent a PAUSED/MINING state from numeric samples.
        self.assertNotIn("state", result)

    def test_explicit_ths_unit_does_not_get_divided_by_1000(self):
        result = normalize_cgi_telemetry(
            {"SUMMARY": [{
                "rate_5s": "234.5",
                "rate_avg": "230",
                "rate_unit": "TH/s",
            }]}
        )
        self.assertEqual(result["hashrate"], 234.5)
        self.assertEqual(result["avg_hashrate"], 230.0)

    def test_missing_or_unknown_unit_does_not_infer_hashrate(self):
        for unit in (None, "", "unknown", "H/s"):
            with self.subTest(unit=unit):
                result = normalize_cgi_telemetry(
                    {"SUMMARY": [{
                        "rate_5s": 230000,
                        "rate_avg": 230000,
                        "rate_unit": unit,
                    }]}
                )
                self.assertIsNone(result["hashrate"])
                self.assertIsNone(result["avg_hashrate"])

    def test_all_missing_fields_remain_unknown(self):
        result = normalize_cgi_telemetry(
            summary_payload={},
            stats_payload=None,
            pools_payload={"POOLS": []},
        )
        self.assertEqual(result, {
            "hashrate": None,
            "avg_hashrate": None,
            "temp": None,
            "power": None,
            "pool": None,
            "observed_work_mode": None,
        })

    def test_zero_hashrate_is_observation_not_paused_state(self):
        result = normalize_cgi_telemetry(
            {"SUMMARY": [{
                "rate_5s": 0,
                "rate_avg": 0,
                "rate_unit": "GH/s",
            }]},
            {"STATS": [{
                "miner-mode": 1,
                "watt": 0,
                "chain": [{"temp_chip": [0, 255]}],
            }]},
        )
        self.assertEqual(result["hashrate"], 0)
        self.assertEqual(result["power"], 0)
        self.assertIsNone(result["temp"])
        self.assertEqual(result["observed_work_mode"], 1)
        self.assertNotIn("state", result)

    def test_unreliable_values_are_rejected(self):
        result = normalize_cgi_telemetry(
            {"SUMMARY": [{
                "rate_5s": float("inf"),
                "rate_avg": -1,
                "rate_unit": "GH/s",
            }]},
            {"STATS": [{
                "watt": "not reported",
                "miner-mode": True,
                "chain": [
                    {"temp_chip": [None, -4, 255, False]},
                    {"temp_chip": [0, 120, 150]},
                ],
            }]},
        )
        self.assertIsNone(result["hashrate"])
        self.assertIsNone(result["avg_hashrate"])
        self.assertIsNone(result["power"])
        self.assertEqual(result["temp"], 120)
        self.assertIsNone(result["observed_work_mode"])

    def test_nested_unexpected_payloads_are_safe(self):
        result = normalize_cgi_telemetry(
            summary_payload={"SUMMARY": ["bad", {"rate_unit": "GH/s", "rate_5s": 1000}]},
            stats_payload={"STATS": ["bad", {"chain": [None, {}, "str"]}]},
            pools_payload={"POOLS": [None, "bad", {"status": "Dead", "url": "secret"}]},
        )
        self.assertEqual(result["hashrate"], 1.0)
        self.assertIsNone(result["pool"])
        self.assertIsNone(result["temp"])

    def test_only_alive_pool_is_reported(self):
        result = normalize_cgi_telemetry(
            pools_payload={"POOLS": [
                {"status": "Dead", "url": "dead.example.invalid"},
                {"status": "Alive", "url": "live.example.invalid"},
            ]}
        )
        self.assertEqual(result["pool"], "live.example.invalid")


if __name__ == "__main__":
    unittest.main()
