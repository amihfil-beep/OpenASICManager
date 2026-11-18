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
            {"expected_model": "T21"},
        ):
            fixture = sample_fixture()
            fixture.update(changed)
            with self.assertRaises(ValueError):
                analysis.analyze_fixture(fixture)

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
