"""Pure and host-side connectivity watchdog regression tests."""

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import config as app_config
from monitoring.connectivity import (
    advance_alarm,
    classify_fleet,
    classify_incident,
    idle_state,
    suppress_individual_offline,
)
from monitoring.fleet import read_fleet_snapshot


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/openasicmanager-connectivity-watchdog"
spec = importlib.util.spec_from_file_location(
    "connectivity_watchdog", SCRIPT
)
# Source without .py suffix cannot be passed to spec_from_file_location
# directly on some Python versions. Compile it with SourceFileLoader.
from importlib.machinery import SourceFileLoader
spec = importlib.util.spec_from_loader(
    "connectivity_watchdog",
    SourceFileLoader("connectivity_watchdog", str(SCRIPT)),
)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


def rows(states, seen=100):
    return [
        {"last_state": state, "last_seen": seen}
        for state in states
    ]


class FleetClassificationTests(unittest.TestCase):
    def test_all_thirty_paused_are_healthy(self):
        snap = classify_fleet(rows(["PAUSED"] * 30), now=115)
        self.assertEqual(snap["fresh"], 30)
        self.assertEqual(snap["paused"], 30)
        self.assertEqual(snap["unavailable"], 0)
        self.assertIsNone(
            classify_incident(
                snap, app_available=True,
                vpn_service_ok=True, vpn_route_ok=True,
            )
        )

    def test_one_mining_and_29_paused_are_healthy(self):
        snap = classify_fleet(
            rows(["MINING"] + ["PAUSED"] * 29), now=105
        )
        self.assertEqual(snap["mining"], 1)
        self.assertEqual(snap["paused"], 29)
        self.assertEqual(snap["status"], "ok")

    def test_mass_offline_threshold_and_individual_failure(self):
        for failed, expected in ((1, None), (23, None), (24, "FARM_MASS_UNAVAILABLE"), (30, "FARM_MASS_UNAVAILABLE")):
            with self.subTest(failed=failed):
                snap = classify_fleet(
                    rows(["OFFLINE"] * failed + ["PAUSED"] * (30-failed)),
                    now=110,
                )
                self.assertEqual(
                    classify_incident(
                        snap, app_available=True,
                        vpn_service_ok=True, vpn_route_ok=True,
                    ),
                    expected,
                )

    def test_stale_telemetry_is_unavailable_but_zero_hashrate_is_not(self):
        fresh = classify_fleet(rows(["PAUSED"] * 30), now=220)
        self.assertEqual(fresh["fresh"], 30)
        stale = classify_fleet(rows(["PAUSED"] * 30), now=221)
        self.assertEqual(stale["stale"], 30)
        self.assertEqual(stale["paused"], 0)

    def test_vpn_and_app_causes_take_precedence(self):
        snap = classify_fleet(rows(["OFFLINE"] * 30), now=200)
        self.assertEqual(
            classify_incident(
                snap, app_available=False,
                vpn_service_ok=False, vpn_route_ok=False,
            ),
            "VPN_SERVICE_UNAVAILABLE",
        )
        self.assertEqual(
            classify_incident(
                snap, app_available=False,
                vpn_service_ok=True, vpn_route_ok=False,
            ),
            "VPN_ROUTE_UNAVAILABLE",
        )
        self.assertEqual(
            classify_incident(
                snap, app_available=False,
                vpn_service_ok=True, vpn_route_ok=True,
            ),
            "APPLICATION_UNAVAILABLE",
        )
        self.assertEqual(
            classify_incident(
                snap, app_available=True,
                database_available=False,
                vpn_service_ok=True, vpn_route_ok=True,
            ),
            "DATABASE_UNAVAILABLE",
        )

    def test_offline_suppression_only_after_confirmed_aggregate_delivery(self):
        records = [
            {
                "enabled": 1,
                "driver": "bitmain_stock",
                "last_state": "OFFLINE",
            }
            for _ in range(30)
        ]
        alert = {
            "phase": "active",
            "alert_sent": True,
            "reason": "VPN_SERVICE_UNAVAILABLE",
        }
        self.assertTrue(suppress_individual_offline(records, alert))
        for changed in (
            {"alert_sent": False},
            {"phase": "idle"},
            {"reason": "DATABASE_UNAVAILABLE"},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(
                    suppress_individual_offline(records, {**alert, **changed})
                )
        records[0]["last_state"] = "PAUSED"
        for miner in records[1:]:
            miner["last_state"] = "PAUSED"
        self.assertFalse(suppress_individual_offline(records, alert))

    def test_empty_farm_not_mass_alarm(self):
        snap = classify_fleet([], now=100)
        self.assertEqual(snap["status"], "empty")
        self.assertIsNone(classify_incident(
            snap, app_available=True,
            vpn_service_ok=True, vpn_route_ok=True,
        ))


class AlarmStateTests(unittest.TestCase):
    def test_grace_dedup_retry_and_recovery(self):
        state, event = advance_alarm(
            idle_state(), "VPN_SERVICE_UNAVAILABLE", 100
        )
        self.assertIsNone(event)
        self.assertEqual(state["phase"], "idle")
        state, event = advance_alarm(
            state, "VPN_SERVICE_UNAVAILABLE", 189
        )
        self.assertIsNone(event)
        state, event = advance_alarm(
            state, "VPN_SERVICE_UNAVAILABLE", 190
        )
        self.assertEqual(event, "ALERT")
        self.assertEqual(state["phase"], "active")

        state, event = advance_alarm(
            state, "VPN_SERVICE_UNAVAILABLE", 220
        )
        self.assertEqual(event, "ALERT")  # Retry a failed send.
        state["alert_sent"] = True
        state, event = advance_alarm(
            state, "VPN_ROUTE_UNAVAILABLE", 240
        )
        self.assertIsNone(event)  # No duplicate alert.

        state, event = advance_alarm(state, None, 250)
        self.assertIsNone(event)
        state, event = advance_alarm(state, None, 309)
        self.assertIsNone(event)
        state, event = advance_alarm(state, None, 310)
        self.assertEqual(event, "RECOVERY")
        state = idle_state()  # Successful delivery in the caller.
        self.assertIsNone(advance_alarm(state, None, 400)[1])

    def test_short_outage_never_generates_message(self):
        state, _ = advance_alarm(
            idle_state(), "FARM_MASS_UNAVAILABLE", 100
        )
        state, event = advance_alarm(state, None, 120)
        self.assertEqual(state, idle_state())
        self.assertIsNone(event)

    def test_failed_initial_delivery_is_not_followed_by_fake_recovery(self):
        state, _ = advance_alarm(idle_state(), "VPN_ROUTE_UNAVAILABLE", 10)
        state, event = advance_alarm(state, "VPN_ROUTE_UNAVAILABLE", 100)
        self.assertEqual(event, "ALERT")
        state, _ = advance_alarm(state, None, 120)
        state, event = advance_alarm(state, None, 180)
        self.assertEqual(state, idle_state())
        self.assertIsNone(event)


class FleetReadOnlyTests(unittest.TestCase):
    def test_sqlite_read_only_snapshot_and_no_mining_false_positive(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "monitoring.sqlite"
            conn = sqlite3.connect(database)
            conn.execute(
                "CREATE TABLE miners (enabled INTEGER, driver TEXT, "
                "last_state TEXT, last_seen INTEGER)"
            )
            conn.executemany(
                "INSERT INTO miners VALUES (1, 'bitmain_stock', 'PAUSED', 100)",
                [()] * 30,
            )
            conn.execute(
                "INSERT INTO miners VALUES (0, 'bitmain_stock', 'OFFLINE', 1)"
            )
            conn.commit()
            conn.close()
            with patch.object(app_config, "DATABASE_PATH", str(database)):
                snap = read_fleet_snapshot(now=110)
            self.assertEqual(snap["total"], 30)
            self.assertEqual(snap["paused"], 30)
            self.assertTrue(snap["healthy"])
            self.assertFalse(snap["mass_outage"])
            conn = sqlite3.connect(database)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM miners").fetchone()[0],
                31,
            )
            conn.close()


class HostWatchdogTests(unittest.TestCase):
    def test_route_requires_the_correct_interface(self):
        with patch.object(
            watchdog, "_command_ok", side_effect=[
                (True, ""), (True, "192.168.1.81 dev ocasic0 src 172.16.5.3"),
            ],
        ):
            self.assertEqual(
                watchdog.vpn_status(
                    "openconnect-asic.service", "ocasic0", "192.168.1.81"
                ),
                (True, True),
            )
        with patch.object(
            watchdog, "_command_ok", side_effect=[
                (True, ""), (True, "192.168.1.81 dev enp3s0 src 77.91.72.25"),
            ],
        ):
            self.assertEqual(
                watchdog.vpn_status(
                    "openconnect-asic.service", "ocasic0", "192.168.1.81"
                ),
                (True, False),
            )

    def test_reject_public_or_command_like_target(self):
        for target in ("8.8.8.8", "127.0.0.1", "192.168.1.81; echo private"):
            with self.subTest(target=target):
                with patch.dict(
                    os.environ, {"OAM_WATCHDOG_ASIC_TARGET": target}
                ), self.assertRaises(ValueError):
                    watchdog._configuration()

    def test_dry_run_does_not_send_or_write_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            sample = {
                "reason": "VPN_ROUTE_UNAVAILABLE",
                "vpn_service_ok": True,
                "vpn_route_ok": False,
                "app_available": True,
                "database_available": True,
                "fleet": classify_fleet(rows(["PAUSED"] * 30), 105),
            }
            with patch.object(
                watchdog, "STATE_PATH", path
            ), patch.object(
                watchdog, "observe", return_value=sample
            ), patch.object(
                watchdog, "_configuration",
                return_value=("openconnect-asic.service", "ocasic0", "192.168.1.81")
            ), patch.object(
                watchdog, "send_telegram"
            ) as send, patch("builtins.print"):
                self.assertEqual(watchdog.main(["--dry-run"]), 0)
            self.assertFalse(path.exists())
            send.assert_not_called()

    def test_state_survives_timer_process_and_sends_one_each(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            bad = {
                "reason": "VPN_SERVICE_UNAVAILABLE",
                "vpn_service_ok": False, "vpn_route_ok": False,
                "app_available": True, "database_available": True,
                "fleet": classify_fleet(rows(["OFFLINE"] * 30), 100),
            }
            good = {
                "reason": None,
                "vpn_service_ok": True, "vpn_route_ok": True,
                "app_available": True, "database_available": True,
                "fleet": classify_fleet(rows(["PAUSED"] * 30), 100),
            }
            with patch.object(
                watchdog, "STATE_PATH", path
            ), patch.object(
                watchdog, "_enabled", return_value=True
            ), patch.object(
                watchdog, "_configuration",
                return_value=("openconnect-asic.service", "ocasic0", "192.168.1.81")
            ), patch.object(
                watchdog, "observe", side_effect=[bad,bad,bad,good,good]
            ), patch.object(
                watchdog.time, "time", side_effect=[100,190,220,250,310]
            ), patch.object(
                watchdog, "send_telegram", return_value=True
            ) as sender, patch("builtins.print"):
                for _ in range(5):
                    self.assertEqual(watchdog.main([]), 0)
            self.assertEqual(sender.call_count, 2)
            self.assertIn("CONNECTIVITY ALERT", sender.call_args_list[0].args[0])
            self.assertIn("RESTORED", sender.call_args_list[1].args[0])
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                idle_state(),
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
