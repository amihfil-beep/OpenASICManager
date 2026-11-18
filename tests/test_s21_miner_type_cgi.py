"""Synthetic tests of BITMAIN's documented read-only miner_type CGI."""

import unittest
from unittest.mock import Mock, patch

import discovery


def response(status, data=None):
    result = Mock()
    result.status_code = status
    result.json.return_value = data
    return result


class MinerTypeCGITests(unittest.TestCase):
    def test_missing_system_info_type_uses_miner_type_cgi(self):
        def get(url, **kwargs):
            self.assertTrue(kwargs["allow_redirects"] is False)
            if url.endswith("/get_system_info.cgi"):
                return response(200, {
                    "system_filesystem_version": "FD-1.5(250418-S21+)"
                })
            if url.endswith("/miner_type.cgi"):
                return response(200, {
                    "miner_type": "Antminer S21 Pro",
                    "fw_version": "Fri Apr 18 16:15:41 CST 2025",
                })
            raise AssertionError("Unexpected request")

        with patch.object(discovery.requests, "get", side_effect=get) as get_mock, \
             patch.object(discovery, "cgminer_query") as cgminer:
            result = discovery.detect_stock("192.0.2.21")

        self.assertEqual(result["model"], "Antminer S21 Pro")
        self.assertEqual(
            result["firmware"], "FD-1.5(250418-S21+)"
        )
        self.assertEqual(get_mock.call_count, 2)
        cgminer.assert_not_called()

    def test_type_endpoint_works_if_system_info_is_unavailable(self):
        def get(url, **kwargs):
            if url.endswith("/get_system_info.cgi"):
                return response(404)
            if url.endswith("/miner_type.cgi"):
                return response(200, {
                    "miner_type": "S21",
                    "fw_version": "Fri Apr 18 16:15:41 CST 2025",
                })
            raise AssertionError("Unexpected request")

        with patch.object(discovery.requests, "get", side_effect=get), \
             patch.object(discovery, "cgminer_query") as cgminer:
            result = discovery.detect_stock("192.0.2.21")

        self.assertEqual(result["model"], "Antminer S21")
        self.assertEqual(
            result["firmware"], "Fri Apr 18 16:15:41 CST 2025"
        )
        cgminer.assert_not_called()

    def test_legacy_t21_discovery_does_not_call_extra_endpoint(self):
        with patch.object(
            discovery.requests, "get",
            return_value=response(200, {
                "minertype": "Antminer T21",
                "system_filesystem_version": "T21-legacy-build",
            }),
        ) as get_mock, patch.object(
            discovery, "cgminer_query"
        ) as cgminer:
            result = discovery.detect_stock("192.0.2.21")

        self.assertEqual(result["model"], "Antminer T21")
        self.assertEqual(result["firmware"], "T21-legacy-build")
        self.assertEqual(get_mock.call_count, 1)
        cgminer.assert_not_called()

    def test_cgminer_fallback_when_both_cgi_endpoints_missing(self):
        with patch.object(
            discovery.requests, "get", return_value=response(404)
        ) as get_mock, patch.object(
            discovery, "cgminer_query",
            return_value={
                "STATS": [{"ID": "BTM_SOC0", "Type": "Antminer S21 Pro"}]
            },
        ) as cgminer:
            result = discovery.detect_stock("192.0.2.21")

        self.assertEqual(result["model"], "Antminer S21 Pro")
        self.assertIsNone(result["firmware"])
        self.assertEqual(get_mock.call_count, 2)
        cgminer.assert_called_once_with("192.0.2.21", "stats")

    def test_unverified_firmware_string_does_not_prove_model(self):
        with patch.object(
            discovery.requests, "get",
            return_value=response(200, {
                "system_filesystem_version": "FD-1.5(250418-S21+)"
            }),
        ), patch.object(
            discovery, "cgminer_query",
            return_value={"STATS": []},
        ):
            self.assertIsNone(discovery.detect_stock("192.0.2.21"))

    def test_http_cgi_read_allowlist_prevents_control_requests(self):
        with patch.object(discovery.requests, "get") as request:
            for path in (
                "/cgi-bin/reboot.cgi",
                "/cgi-bin/set_miner_conf.cgi",
                "https://example.com/",
            ):
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        discovery.read_stock_cgi_info("192.0.2.21", path)
            request.assert_not_called()

    def test_non_dict_payload_is_ignored(self):
        with patch.object(
            discovery.requests, "get",
            return_value=response(200, [1, 2, 3]),
        ):
            self.assertIsNone(
                discovery.read_stock_cgi_info(
                    "192.0.2.21", "/cgi-bin/miner_type.cgi"
                )
            )


if __name__ == "__main__":
    unittest.main()
