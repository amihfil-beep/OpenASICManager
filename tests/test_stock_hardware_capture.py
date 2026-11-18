import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "capture-stock-hardware-fixture.py"
)
spec = importlib.util.spec_from_file_location("stock_capture", SCRIPT)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class StockHardwareCaptureTests(unittest.TestCase):
    def test_only_explicit_rfc1918_ipv4_allowed(self):
        self.assertEqual(capture.validate_target("10.1.2.3"), "10.1.2.3")
        self.assertEqual(
            capture.validate_target("192.168.1.21"), "192.168.1.21"
        )
        for value in (
            "8.8.8.8", "127.0.0.1", "169.254.1.2",
            "::1", "example.com", "198.51.100.4",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    capture.validate_target(value)

    def test_sanitizer_preserves_numeric_and_parser_semantics(self):
        raw = {
            "STATS": [
                {"ID": "BTM_SOC0", "Type": "Antminer S21 Pro",
                 "temp2": 72, "power_consumption": "3550",
                 "mac": "AA:BB:CC:DD:EE:FF",
                 "serial": 12345678}
            ],
            "POOLS": [
                {"Status": "Alive", "URL": "stratum+tcp://secret.pool:3333",
                 "User": "wallet.worker", "Pass": "private-password",
                 "Last Share Time": 1000}
            ],
            "STATUS": [{"STATUS": "S", "Description": "miner 10.1.2.3"}],
        }
        sanitized = capture.sanitize_value(raw)
        self.assertEqual(sanitized["STATS"][0]["Type"], "Antminer S21 Pro")
        self.assertEqual(sanitized["STATS"][0]["ID"], "BTM_SOC0")
        self.assertEqual(sanitized["STATS"][0]["temp2"], 72)
        self.assertEqual(sanitized["STATS"][0]["power_consumption"], "3550")
        self.assertEqual(sanitized["POOLS"][0]["Status"], "Alive")
        self.assertEqual(sanitized["STATUS"][0]["STATUS"], "S")
        flat = json.dumps(sanitized)
        for forbidden in (
            "AA:BB:CC", "12345678", "secret.pool",
            "wallet.worker", "private-password", "10.1.2.3",
        ):
            self.assertNotIn(forbidden, flat)

    def test_unknown_strings_redacted(self):
        result = capture.sanitize_value({
            "UnknownMessage": "access_token=verysecret",
            "host": "10.1.2.3",
            "system_filesystem_version": "2026-10-01",
            "User": "account.worker",
        })
        self.assertEqual(result["UnknownMessage"], "REDACTED")
        self.assertEqual(result["host"], "REDACTED")
        self.assertEqual(
            result["system_filesystem_version"], "2026-10-01"
        )
        self.assertEqual(result["User"], "fixture.worker")

    def test_embedded_ip_and_device_identifiers_are_redacted(self):
        result = capture.sanitize_value({
            "minertype": "Antminer S21 10.1.2.3",
            "system_filesystem_version": "2026-11-03 10.1.2.3",
            "chip_id": 481927362,
            "10.1.2.3": "internal",
        })
        flat = json.dumps(result)
        self.assertNotIn("10.1.2.3", flat)
        self.assertNotIn("481927362", flat)
        self.assertEqual(result["minertype"], "REDACTED")
        self.assertEqual(result["chip_id"], 0)

    def test_collect_calls_only_allowlisted_read_commands(self):
        with patch.object(
            capture, "read_cgminer", return_value={"SUMMARY": []}
        ) as cg, patch.object(
            capture, "capture_system_info",
            return_value={"authenticated_status": 200},
        ) as http:
            report = capture.collect_fixture(
                "10.2.3.4", "S21", "root", "a-private-password"
            )
        self.assertEqual(
            [call.args[1] for call in cg.call_args_list],
            list(capture.CGMINER_COMMANDS),
        )
        self.assertEqual(len(http.call_args_list), 4)
        self.assertEqual(
            [call.kwargs.get("path", "/cgi-bin/get_system_info.cgi")
             for call in http.call_args_list],
            list(capture.HTTP_READ_PATHS),
        )
        self.assertEqual(report["capture_mode"], "read_only")
        self.assertNotIn("10.2.3.4", json.dumps(report))
        self.assertNotIn("a-private-password", json.dumps(report))

    def test_new_cgi_temperature_fields_and_serial_are_sanitized(self):
        raw = {
            "STATS": [{
                "rate_5s": 231000.4,
                "watt": 3480,
                "miner-mode": 0,
                "chain": [
                    {"temp_chip": [55, 65], "temp_pcb": [44, 51],
                     "temp_pic": [42, 48], "sn": "SECRET-DEVICE-123"}
                ],
            }]
        }
        sanitized = capture.sanitize_value(raw)
        stats = sanitized["STATS"][0]
        self.assertEqual(stats["rate_5s"], 231000.4)
        self.assertEqual(stats["watt"], 3480)
        self.assertEqual(stats["miner-mode"], 0)
        self.assertEqual(
            stats["chain"][0]["temp_chip"], [55, 65]
        )
        self.assertEqual(stats["chain"][0]["temp_pcb"], [44, 51])
        self.assertEqual(stats["chain"][0]["temp_pic"], [42, 48])
        self.assertEqual(stats["chain"][0]["sn"], "REDACTED")
        self.assertNotIn("SECRET-DEVICE", json.dumps(sanitized))

    def test_t21_baseline_capture_uses_same_read_only_allowlist(self):
        with patch.object(
            capture,
            "read_cgminer",
            return_value={"SUMMARY": [{"GHS 5s": 190000}]},
        ) as cgminer, patch.object(
            capture,
            "capture_system_info",
            return_value={"authenticated_status": 200},
        ) as http:
            fixture = capture.collect_fixture(
                "10.1.2.3", "T21", "root", "secret-not-saved"
            )
        self.assertEqual(fixture["expected_model"], "T21")
        self.assertEqual(fixture["capture_mode"], "read_only")
        self.assertEqual(
            [call.args[1] for call in cgminer.call_args_list],
            list(capture.CGMINER_COMMANDS),
        )
        self.assertEqual(http.call_count, len(capture.HTTP_READ_PATHS))
        self.assertNotIn("secret-not-saved", json.dumps(fixture))
        self.assertNotIn("10.1.2.3", json.dumps(fixture))

    def test_rate_unit_keeps_only_safe_known_measurements(self):
        for unit in ("GH/s", "TH/s", "MH/s", "PH/s", "/s"):
            with self.subTest(unit=unit):
                self.assertEqual(
                    capture.sanitize_value(unit, "rate_unit"),
                    unit,
                )

        for untrusted in (
            "redacted-secret",
            "TH/s; token=private",
            "https://internal.example",
            "H/s",
        ):
            with self.subTest(untrusted=untrusted):
                self.assertEqual(
                    capture.sanitize_value(untrusted, "rate_unit"),
                    "REDACTED",
                )

    def test_real_t21_paused_cgi_suffix_retained_without_secrets(self):
        raw = {
            "SUMMARY": [{
                "rate_5s": 0.0,
                "rate_avg": 0.0,
                "rate_unit": "/s",
                "worker": "PRIVATE-WORKER",
                "serial": "PRIVATE-SERIAL",
            }]
        }
        result = capture.sanitize_value(raw)
        self.assertEqual(result["SUMMARY"][0]["rate_unit"], "/s")
        self.assertEqual(result["SUMMARY"][0]["rate_5s"], 0.0)
        self.assertNotIn("PRIVATE-WORKER", json.dumps(result))
        self.assertNotIn("PRIVATE-SERIAL", json.dumps(result))

    def test_miner_type_cgi_preserves_explicit_model_and_firmware(self):
        payload = {
            "miner_type": "Antminer S21 Pro",
            "fw_version": "Fri Apr 18 16:15:41 CST 2025",
            "subtype": "AMLCtrl_DEVICE_SECRET",
            "serinum": "PRIVATE_SERIAL_1234",
        }
        sanitized = capture.sanitize_value(payload)
        self.assertEqual(sanitized["miner_type"], "Antminer S21 Pro")
        self.assertEqual(
            sanitized["fw_version"], "Fri Apr 18 16:15:41 CST 2025"
        )
        flat = json.dumps(sanitized)
        self.assertNotIn("PRIVATE_SERIAL_1234", flat)
        self.assertNotIn("AMLCtrl_DEVICE_SECRET", flat)

    def test_http_probe_rejects_write_routes_and_external_hosts(self):
        class FakeSession:
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return object()

        session = FakeSession()
        with self.assertRaises(ValueError):
            capture._http_get(
                session, "10.1.2.3",
                path="/cgi-bin/set_miner_conf.cgi"
            )
        with self.assertRaises(ValueError):
            capture._http_get(
                session, "10.1.2.3",
                path="/cgi-bin/reboot.cgi"
            )
        self.assertEqual(session.calls, [])
        capture._http_get(
            session, "10.1.2.3",
            path="/cgi-bin/stats.cgi"
        )
        self.assertEqual(
            session.calls[0][0],
            "http://10.1.2.3/cgi-bin/stats.cgi",
        )
        self.assertFalse(
            session.calls[0][1]["allow_redirects"]
        )

    def test_file_created_private_outside_repository(self):
        fixture = {
            "expected_model": "S21-Pro",
            "capture_mode": "read_only",
            "cgminer": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = capture.save_fixture(fixture, Path(tmp) / "captures")
            self.assertTrue(path.is_file())
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), fixture)

    def test_reject_non_allowlisted_cgminer_commands(self):
        with self.assertRaises(ValueError):
            capture.read_cgminer("10.1.1.1", "reboot")


if __name__ == "__main__":
    unittest.main()
