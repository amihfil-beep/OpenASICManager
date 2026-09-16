import os
import tempfile
import unittest

import config as app_config
from db import init_db
from discovery_profiles.service import (
    create_saved_profile,
    delete_saved_profile,
    list_saved_profiles,
    scan_saved_profiles,
    update_saved_profile,
)


class DiscoveryProfileTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-discovery-profiles-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        init_db()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_profile_crud_and_canonical_network(self):
        profile = create_saved_profile({
            "name": "Primary LAN",
            "network": "192.168.10.25/24",
        })

        self.assertEqual(profile["network"], "192.168.10.0/24")
        self.assertTrue(profile["enabled"])
        self.assertEqual(len(list_saved_profiles()), 1)

        updated = update_saved_profile(
            profile["id"],
            {
                "name": "Primary ASIC LAN",
                "enabled": False,
            },
        )

        self.assertEqual(updated["name"], "Primary ASIC LAN")
        self.assertFalse(updated["enabled"])
        self.assertEqual(list_saved_profiles(enabled_only=True), [])

        deleted = delete_saved_profile(profile["id"])
        self.assertEqual(deleted["id"], profile["id"])
        self.assertEqual(list_saved_profiles(), [])

    def test_rejects_public_and_overlapping_networks(self):
        with self.assertRaisesRegex(ValueError, "RFC1918"):
            create_saved_profile({
                "name": "Public",
                "network": "203.0.113.0/24",
            })

        create_saved_profile({
            "name": "LAN A",
            "network": "192.168.20.0/24",
        })

        with self.assertRaisesRegex(ValueError, "overlaps"):
            create_saved_profile({
                "name": "LAN B",
                "network": "192.168.20.128/25",
            })

    def test_rejects_oversized_network(self):
        with self.assertRaisesRegex(ValueError, "too large"):
            create_saved_profile({
                "name": "Too large",
                "network": "10.10.0.0/19",
            })

    def test_all_scan_uses_only_enabled_profiles(self):
        enabled = create_saved_profile({
            "name": "Enabled",
            "network": "192.168.30.0/24",
        })
        disabled = create_saved_profile({
            "name": "Disabled",
            "network": "10.30.0.0/24",
            "enabled": False,
        })

        calls = []

        def scanner(network):
            calls.append(network)
            return {
                "network": network,
                "hosts_scanned": 254,
                "duration_seconds": 0.1,
                "total": 0,
                "awesome": 0,
                "bitmain_stock": 0,
                "unknown": 0,
                "devices": [],
            }

        result = scan_saved_profiles(scanner=scanner)

        self.assertEqual(calls, [enabled["network"]])
        self.assertEqual(result["profiles_scanned"], 1)

        calls.clear()
        result = scan_saved_profiles(
            profile_ids=[disabled["id"]],
            scanner=scanner,
        )
        self.assertEqual(calls, [disabled["network"]])
        self.assertEqual(result["profiles_scanned"], 1)

    def test_multi_scan_deduplicates_ip_and_tracks_sources(self):
        first = create_saved_profile({
            "name": "LAN A",
            "network": "192.168.40.0/24",
        })
        second = create_saved_profile({
            "name": "LAN B",
            "network": "10.40.0.0/24",
        })

        def scanner(network):
            return {
                "network": network,
                "hosts_scanned": 254,
                "duration_seconds": 0.2,
                "total": 1,
                "awesome": 0,
                "bitmain_stock": 1,
                "unknown": 0,
                "devices": [{
                    "ip": "192.168.40.10",
                    "driver": "bitmain_stock",
                    "model": "T21",
                }],
            }

        result = scan_saved_profiles(
            profile_ids=[second["id"], first["id"]],
            scanner=scanner,
        )

        self.assertEqual(result["profiles_scanned"], 2)
        self.assertEqual(result["successful_networks"], 2)
        self.assertEqual(result["failed_networks"], 0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["devices"][0]["sources"]), 2)
        self.assertEqual(
            [row["profile_id"] for row in result["networks"]],
            [first["id"], second["id"]],
        )

    def test_network_failure_does_not_hide_other_results(self):
        first = create_saved_profile({
            "name": "LAN A",
            "network": "192.168.50.0/24",
        })
        second = create_saved_profile({
            "name": "LAN B",
            "network": "10.50.0.0/24",
        })

        def scanner(network):
            if network == first["network"]:
                raise RuntimeError("route unavailable")

            return {
                "network": network,
                "hosts_scanned": 254,
                "duration_seconds": 0.1,
                "total": 1,
                "awesome": 1,
                "bitmain_stock": 0,
                "unknown": 0,
                "devices": [{
                    "ip": "10.50.0.10",
                    "driver": "awesome",
                }],
            }

        result = scan_saved_profiles(
            profile_ids=[first["id"], second["id"]],
            scanner=scanner,
        )

        self.assertEqual(result["successful_networks"], 1)
        self.assertEqual(result["failed_networks"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["networks"][0]["status"], "failed")
        self.assertEqual(result["networks"][1]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
