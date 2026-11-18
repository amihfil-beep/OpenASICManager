"""Synthetic T21/S21/S21 Pro stock API contract regression tests.

These fixtures are intentionally synthetic, not physical-device evidence.
"""

import unittest
from unittest.mock import Mock, patch

from drivers.bitmain import stock_status
from drivers.bitmain_models import (
    normalize_stock_model,
    stock_model_from_responses,
)
from discovery import detect_stock


class StockModelTests(unittest.TestCase):
    def test_exact_air_models_are_normalized(self):
        pairs = {
            "T21": "Antminer T21",
            "antminer t21": "Antminer T21",
            "ANTMINER S21": "Antminer S21",
            "s21 pro": "Antminer S21 Pro",
            "Antminer   S21   Pro": "Antminer S21 Pro",
        }
        for raw, expected in pairs.items():
            with self.subTest(model=raw):
                self.assertEqual(normalize_stock_model(raw), expected)

    def test_out_of_scope_variants_are_never_collapsed(self):
        for model in (
            "Antminer S21 XP",
            "Antminer S21 Hydro",
            "Antminer S21 Imm",
            "Antminer S21+",
        ):
            with self.subTest(model=model):
                self.assertEqual(normalize_stock_model(model), model)
        self.assertIsNone(normalize_stock_model("S21 XP"))
        self.assertIsNone(normalize_stock_model(""))

    def test_model_can_be_in_later_stats_entry(self):
        model = stock_model_from_responses(
            stats_items=[
                {"ID": "STATS", "watt": 3520},
                {"ID": "BTM_SOC0", "Type": "Antminer S21 Pro"},
            ]
        )
        self.assertEqual(model, "Antminer S21 Pro")

    def test_no_model_is_inferred_from_firmware_build(self):
        self.assertIsNone(
            stock_model_from_responses(
                system_info={
                    "system_filesystem_version": "FD-1.5(250418-S21+)"
                }
            )
        )

    def test_explicit_system_info_takes_precedence(self):
        model = stock_model_from_responses(
            system_info={"miner_type": "S21 Pro"},
            stats_items=[{"Type": "Antminer T21"}],
        )
        self.assertEqual(model, "Antminer S21 Pro")


class StockDetectionTests(unittest.TestCase):
    def test_cgi_without_minertype_falls_back_to_readonly_cgminer(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "system_filesystem_version": "FD-1.5(250418-S21+)"
        }
        with patch(
            "discovery.requests.get", return_value=response
        ), patch(
            "discovery.cgminer_query",
            return_value={"STATS": [
                {"ID": "STATS"},
                {"ID": "BTM_SOC0", "Type": "Antminer S21 Pro"},
            ]},
        ) as query:
            device = detect_stock("192.0.2.21")

        self.assertEqual(device["driver"], "bitmain_stock")
        self.assertEqual(device["model"], "Antminer S21 Pro")
        query.assert_called_once_with("192.0.2.21", "stats")

    def test_unknown_model_does_not_become_stock_device(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "system_filesystem_version": "FD-1.5(250418-S21+)"
        }
        with patch(
            "discovery.requests.get", return_value=response
        ), patch(
            "discovery.cgminer_query",
            return_value={"STATS": [{"ID": "STATS"}]},
        ):
            self.assertIsNone(detect_stock("192.0.2.21"))


class StockMonitorTests(unittest.TestCase):
    def test_explicit_models_are_reported_from_stats(self):
        cases = (
            "Antminer T21",
            "Antminer S21",
            "Antminer S21 Pro",
        )
        for model in cases:
            with self.subTest(model=model):
                responses = {
                    "summary": {"SUMMARY": [
                        {"GHS 5s": 210000.0, "GHS av": 205000.0}
                    ]},
                    "stats": {"STATS": [
                        {"ID": "STATS", "watt": 3500},
                        {"ID": "BTM_SOC0", "Type": model, "temp2": 65},
                    ]},
                    "pools": {"POOLS": [{"Status": "Alive", "URL": "pool.example.invalid"}]},
                    "devs": {"DEVS": [{"ASC": 0}]},
                }
                with patch(
                    "drivers.bitmain.cgminer_query",
                    side_effect=lambda ip, command: responses[command],
                ):
                    result = stock_status({
                        "ip": "192.0.2.21",
                        "model": "Antminer",
                        "firmware": "Bitmain Stock",
                    })

                self.assertEqual(result["model"], model)
                self.assertEqual(result["state"], "MINING")
                self.assertEqual(result["hashrate"], 210.0)
                self.assertEqual(result["temp"], 65.0)
                self.assertIsNone(result["power"])

    def test_cached_model_survives_missing_stats_type(self):
        responses = {
            "summary": {"SUMMARY": [{"GHS 5s": 0, "GHS av": 0}]},
            "stats": {"STATS": [{"ID": "BTM_SOC0", "temp2": 52}]},
            "pools": {"POOLS": [{"Status": "Disabled"}]},
            "devs": {"DEVS": []},
        }
        with patch(
            "drivers.bitmain.cgminer_query",
            side_effect=lambda ip, command: responses[command],
        ):
            result = stock_status({
                "ip": "192.0.2.21",
                "model": "Antminer S21",
                "firmware": "Bitmain Stock",
            })

        self.assertEqual(result["model"], "Antminer S21")
        self.assertEqual(result["state"], "PAUSED")


if __name__ == "__main__":
    unittest.main()
