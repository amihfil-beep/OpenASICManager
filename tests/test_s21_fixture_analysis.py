"""Offline S21 fixture analysis: synthetic data, no hardware required."""

import importlib.util
import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from io import StringIO


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze-stock-hardware-fixture.py"
spec = importlib.util.spec_from_file_location("fixture_analysis", SCRIPT)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def sample_fixture(expected="S21-Pro"):
    return {
        "schema_version": 1,
        "capture_mode": "read_only",
        "expected_model": expected,
        "captured_at_utc": "2026-11-18T12:00:00+00:00",
        "host_redacted": True,
        "cgminer": {
            "summary": {
                "available": True,
                "data": {"SUMMARY": [
                    {"GHS 5s": 231000, "GHS av": 230000}
                ]},
            },
            "stats": {
                "available": True,
                "data": {"STATS": [{"ID": "BTM_SOC0"}]},
            },
        },
        "http_system_info": {
            "authenticated_status": 200,
            "sanitized_json": {
                "system_filesystem_version": "SENSITIVE-VERSION-STRING"
            },
        },
        "http_cgi_diagnostics": {
            "/cgi-bin/miner_type.cgi": {
                "authenticated_status": 200,
                "sanitized_json": {
                    "miner_type": "Antminer S21 Pro",
                    "fw_version": "SENSITIVE-VERSION-STRING",
                    "subtype": "SENSITIVE-SERIAL-STRING",
                },
            },
            "/cgi-bin/summary.cgi": {
                "authenticated_status": 200,
                "sanitized_json": {
                    "SUMMARY": [{
                        "rate_5s": 232500,
                        "rate_avg": 230500,
                        "rate_unit": "GH/s",
                    }]
                },
            },
            "/cgi-bin/stats.cgi": {
                "authenticated_status": 200,
                "sanitized_json": {
                    "STATS": [{
                        "watt": 3510,
                        "miner-mode": 0,
                        "chain": [{"temp_chip": [60, 72, 255]}],
                    }]
                },
            },
        },
        "ip": "10.1.2.3",
        "worker": "SENSITIVE-WORKER-STRING",
    }


class FixtureAnalysisTests(unittest.TestCase):
    def test_compare_numeric_telemetry_without_claiming_support(self):
        result = analysis.analyze_fixture(sample_fixture())
        self.assertEqual(result["model_evidence"], "matches_expected")
        self.assertTrue(result["source_available"]["cgi_summary"])
        self.assertEqual(result["metrics"]["cgminer_hashrate_ths"], 231.0)
        self.assertEqual(result["metrics"]["cgi_hashrate_ths"], 232.5)
        self.assertEqual(
            result["metrics"]["cgi_minus_cgminer_hashrate_ths"], 1.5
        )
        self.assertEqual(result["metrics"]["cgi_avg_hashrate_ths"], 230.5)
        self.assertEqual(result["metrics"]["cgi_rate_unit_status"], "explicit_scale")
        self.assertEqual(result["metrics"]["cgi_stats_entries"], 1)
        self.assertEqual(result["metrics"]["cgi_power_w"], 3510)
        self.assertEqual(result["metrics"]["cgi_max_chip_temp_c"], 72)
        self.assertFalse(result["compatibility_verified"])
        self.assertNotIn("state", result)

    def test_no_identifying_data_is_reported(self):
        report = json.dumps(analysis.analyze_fixture(sample_fixture()))
        for value in (
            "SENSITIVE-VERSION-STRING",
            "SENSITIVE-SERIAL-STRING",
            "SENSITIVE-WORKER-STRING",
            "10.1.2.3",
        ):
            self.assertNotIn(value, report)

    def test_unknown_or_mismatched_model_cannot_claim_match(self):
        unknown = sample_fixture("S21")
        unknown["http_cgi_diagnostics"]["/cgi-bin/miner_type.cgi"] = {
            "authenticated_status": 401,
            "sanitized_json": {
                "miner_type": "Antminer S21",
            },
        }
        self.assertEqual(
            analysis.analyze_fixture(unknown)["model_evidence"], "unknown"
        )
        wrong = sample_fixture("S21")
        self.assertEqual(
            analysis.analyze_fixture(wrong)["model_evidence"],
            "different_or_unsupported",
        )

    def test_missing_cgi_data_remains_unknown(self):
        fixture = sample_fixture()
        fixture["http_cgi_diagnostics"] = {}
        result = analysis.analyze_fixture(fixture)
        self.assertIsNone(result["metrics"]["cgi_hashrate_ths"])
        self.assertIsNone(result["metrics"]["cgi_power_w"])
        self.assertIsNone(result["metrics"]["cgi_minus_cgminer_hashrate_ths"])
        self.assertFalse(result["compatibility_verified"])

    def test_unsupported_or_unmarked_fixture_rejected(self):
        for changed in (
            {"schema_version": 2},
            {"capture_mode": "active_test"},
            {"expected_model": "S19"},
        ):
            fixture = sample_fixture()
            fixture.update(changed)
            with self.assertRaises(ValueError):
                analysis.analyze_fixture(fixture)

    def test_realistic_t21_legacy_baseline_offline(self):
        fixture = sample_fixture("T21")
        # A legacy stock T21 can expose the model in get_system_info
        # while lacking the newer read-only CGI endpoint.
        fixture["http_system_info"]["sanitized_json"] = {
            "minertype": "Antminer T21",
            "system_filesystem_version": "LEGACY-PRIVATE-VERSION",
        }
        fixture["http_cgi_diagnostics"] = {}

        report = analysis.analyze_fixture(fixture)
        self.assertEqual(report["expected_model"], "T21")
        self.assertEqual(report["model_evidence"], "matches_expected")
        self.assertEqual(report["metrics"]["cgminer_hashrate_ths"], 231.0)
        self.assertIsNone(report["metrics"]["cgi_hashrate_ths"])
        self.assertFalse(report["source_available"]["cgi_stats"])
        self.assertFalse(report["compatibility_verified"])
        self.assertNotIn("LEGACY-PRIVATE-VERSION", json.dumps(report))

    def test_mislabeled_t21_fixture_cannot_claim_s21_compatibility(self):
        fixture = sample_fixture("T21")
        result = analysis.analyze_fixture(fixture)
        self.assertEqual(result["model_evidence"], "different_or_unsupported")
        self.assertFalse(result["compatibility_verified"])

    def test_real_t21_paused_and_mining_fixtures_are_distinct(self):
        # Physical-device observed response shapes, recreated without
        # exporting actual snapshots or customer/account information.
        paused = sample_fixture("T21")
        paused["http_system_info"]["sanitized_json"] = {
            "minertype": "Antminer T21",
        }
        paused["http_cgi_diagnostics"]["/cgi-bin/miner_type.cgi"][
            "sanitized_json"
        ] = {"miner_type": "Antminer T21"}
        paused["cgminer"]["summary"]["data"]["SUMMARY"][0].update({
            "GHS 5s": 0.0, "GHS av": 0.0,
        })
        paused["http_cgi_diagnostics"]["/cgi-bin/summary.cgi"][
            "sanitized_json"
        ] = {"SUMMARY": [{
            "rate_5s": 0.0,
            "rate_avg": 0.0,
            "rate_unit": "/s",
        }]}
        paused["http_cgi_diagnostics"]["/cgi-bin/stats.cgi"][
            "sanitized_json"
        ] = {"STATS": []}

        paused_result = analysis.analyze_fixture(paused)
        self.assertEqual(paused_result["model_evidence"], "matches_expected")
        self.assertEqual(
            paused_result["metrics"]["cgi_rate_unit_status"],
            "suffix_only_scale_unknown",
        )
        self.assertEqual(paused_result["metrics"]["cgi_stats_entries"], 0)
        self.assertEqual(paused_result["metrics"]["cgminer_hashrate_ths"], 0)
        self.assertIsNone(paused_result["metrics"]["cgi_hashrate_ths"])
        self.assertIsNone(paused_result["metrics"]["cgi_minus_cgminer_hashrate_ths"])
        self.assertFalse(paused_result["compatibility_verified"])
        self.assertNotIn("state", paused_result)

        mining = sample_fixture("T21")
        mining["http_system_info"]["sanitized_json"] = {
            "minertype": "Antminer T21",
        }
        mining["http_cgi_diagnostics"]["/cgi-bin/miner_type.cgi"][
            "sanitized_json"
        ] = {"miner_type": "Antminer T21"}
        mining["cgminer"]["summary"]["data"]["SUMMARY"][0].update({
            "GHS 5s": 188903.39, "GHS av": 174066.79,
        })
        mining["http_cgi_diagnostics"]["/cgi-bin/summary.cgi"][
            "sanitized_json"
        ] = {"SUMMARY": [{
            "rate_5s": 186433.27,
            "rate_avg": 175819.69,
            "rate_unit": "GH/s",
        }]}
        mining["http_cgi_diagnostics"]["/cgi-bin/stats.cgi"][
            "sanitized_json"
        ] = {"STATS": [{
            "watt": 3647,
            "miner-mode": 0,
            "chain": [{"temp_chip": [46, 48, 255]}],
        }]}

        active_result = analysis.analyze_fixture(mining)
        self.assertEqual(active_result["model_evidence"], "matches_expected")
        self.assertEqual(
            active_result["metrics"]["cgi_rate_unit_status"],
            "explicit_scale",
        )
        self.assertEqual(active_result["metrics"]["cgi_stats_entries"], 1)
        self.assertAlmostEqual(
            active_result["metrics"]["cgi_hashrate_ths"], 186.4333, places=4
        )
        self.assertEqual(active_result["metrics"]["cgi_max_chip_temp_c"], 48)
        self.assertEqual(active_result["metrics"]["cgi_power_w"], 3647)
        self.assertFalse(active_result["compatibility_verified"])
        self.assertNotIn("state", active_result)

    def test_cli_accepts_private_fixture_and_prints_safe_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s21-pro.json"
            path.write_text(json.dumps(sample_fixture()), encoding="utf-8")
            os.chmod(path, 0o600)
            with patch("sys.stdout", new_callable=StringIO) as output:
                status = analysis.main([str(path)])

        self.assertEqual(status, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data["model_evidence"], "matches_expected")
        self.assertNotIn("SENSITIVE", output.getvalue())

    def test_cli_rejects_readable_by_others_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s21.json"
            path.write_text(json.dumps(sample_fixture()), encoding="utf-8")
            os.chmod(path, 0o644)
            with patch("sys.stderr", new_callable=StringIO):
                status = analysis.main([str(path)])
            self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
