import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

from maintenance.remote_web_sync import (
    SYNC_SERVICE,
    SYNC_TIMER,
    reconcile_config,
    register_upgrade_units,
)


class FakeRunner:
    def __init__(
        self,
        *,
        invalid_once=False,
        reload_failure=False,
    ):
        self.invalid_once = invalid_once
        self.reload_failure = reload_failure
        self.validation_calls = 0
        self.calls = []

    def __call__(self, command):
        command = [str(item) for item in command]
        self.calls.append(command)

        if command == ["nginx", "-t"]:
            self.validation_calls += 1

            if (
                self.invalid_once
                and self.validation_calls == 1
            ):
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="invalid nginx config",
                )

        if (
            command
            == ["systemctl", "reload", "nginx"]
            and self.reload_failure
        ):
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="reload failed",
            )

        return SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        )


class RemoteWebSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(
            tempfile.mkdtemp(
                prefix="openasic-remote-sync-test-"
            )
        )
        self.available = self.temp / "sites-available"
        self.enabled = self.temp / "sites-enabled"
        self.output = (
            self.available
            / "openasicmanager-remote"
        )
        self.available.mkdir(parents=True)
        self.enabled.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(
            self.temp,
            ignore_errors=True,
        )

    def _activate(self, content):
        self.output.write_text(
            content,
            encoding="utf-8",
        )
        (
            self.enabled
            / self.output.name
        ).symlink_to(self.output)

    def test_unchanged_configuration_is_noop(self):
        self._activate("same\n")
        runner = FakeRunner()

        report = reconcile_config(
            "same\n",
            output_path=self.output,
            enabled_dir=self.enabled,
            command_runner=runner,
        )

        self.assertEqual(
            report["status"],
            "unchanged",
        )
        self.assertFalse(
            report["reloaded"]
        )
        self.assertEqual(
            runner.calls,
            [],
        )

    def test_changed_configuration_validates_and_reloads(self):
        self._activate("old\n")
        runner = FakeRunner()

        report = reconcile_config(
            "new\n",
            output_path=self.output,
            enabled_dir=self.enabled,
            command_runner=runner,
        )

        self.assertEqual(
            report["status"],
            "updated",
        )
        self.assertEqual(
            self.output.read_text(
                encoding="utf-8"
            ),
            "new\n",
        )
        self.assertEqual(
            runner.calls,
            [
                ["nginx", "-t"],
                ["systemctl", "reload", "nginx"],
            ],
        )

    def test_invalid_candidate_restores_previous_configuration(self):
        self._activate("old\n")
        runner = FakeRunner(
            invalid_once=True
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "nginx -t failed",
        ):
            reconcile_config(
                "invalid\n",
                output_path=self.output,
                enabled_dir=self.enabled,
                command_runner=runner,
            )

        self.assertEqual(
            self.output.read_text(
                encoding="utf-8"
            ),
            "old\n",
        )
        self.assertEqual(
            (
                self.enabled
                / self.output.name
            ).resolve(),
            self.output.resolve(),
        )
        self.assertNotIn(
            ["systemctl", "reload", "nginx"],
            runner.calls,
        )
        self.assertEqual(
            runner.validation_calls,
            2,
        )

    def test_reload_failure_restores_previous_configuration(self):
        self._activate("old\n")
        runner = FakeRunner(
            reload_failure=True
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "nginx reload failed",
        ):
            reconcile_config(
                "new\n",
                output_path=self.output,
                enabled_dir=self.enabled,
                command_runner=runner,
            )

        self.assertEqual(
            self.output.read_text(
                encoding="utf-8"
            ),
            "old\n",
        )
        self.assertEqual(
            (
                self.enabled
                / self.output.name
            ).resolve(),
            self.output.resolve(),
        )
        self.assertEqual(
            runner.validation_calls,
            2,
        )

    def test_missing_link_is_reconciled_even_when_content_matches(self):
        self.output.write_text(
            "same\n",
            encoding="utf-8",
        )
        runner = FakeRunner()

        report = reconcile_config(
            "same\n",
            output_path=self.output,
            enabled_dir=self.enabled,
            command_runner=runner,
        )

        self.assertTrue(
            report["link_changed"]
        )
        self.assertTrue(
            (
                self.enabled
                / self.output.name
            ).is_symlink()
        )

    def test_non_symlink_enabled_entry_is_not_overwritten(self):
        self.output.write_text(
            "old\n",
            encoding="utf-8",
        )
        enabled_entry = (
            self.enabled
            / self.output.name
        )
        enabled_entry.write_text(
            "administrator file\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Refusing to replace non-symlink",
        ):
            reconcile_config(
                "new\n",
                output_path=self.output,
                enabled_dir=self.enabled,
                command_runner=FakeRunner(),
            )

        self.assertEqual(
            enabled_entry.read_text(
                encoding="utf-8"
            ),
            "administrator file\n",
        )

    def test_upgrade_unit_registration(self):
        source = self.temp / "release"
        unit_root = source / "deploy/systemd"
        unit_root.mkdir(parents=True)

        for name in (
            SYNC_SERVICE,
            SYNC_TIMER,
        ):
            (unit_root / name).write_text(
                "unit\n",
                encoding="utf-8",
            )

        module = SimpleNamespace(
            UNIT_NAMES=("existing.service",)
        )

        names = register_upgrade_units(
            module,
            source,
        )

        self.assertEqual(
            names,
            (
                "existing.service",
                SYNC_SERVICE,
                SYNC_TIMER,
            ),
        )

    def _generator_output(
        self,
        miners,
        *,
        enabled=True,
        allowed_cidr="192.168.1.0/24",
    ):
        db_path = self.temp / (
            "render-"
            + str(abs(hash(tuple(miners))))
            + ".db"
        )

        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE miners (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                ip TEXT NOT NULL,
                driver TEXT NOT NULL,
                enabled INTEGER NOT NULL
            )
        """)

        for index, (name, ip) in enumerate(
            miners,
            start=1,
        ):
            conn.execute(
                """
                INSERT INTO miners(
                    id,
                    name,
                    ip,
                    driver,
                    enabled
                )
                VALUES (?, ?, ?, 'bitmain_stock', 1)
                """,
                (index, name, ip),
            )

        conn.commit()
        conn.close()

        cert = self.temp / "cert.pem"
        key = self.temp / "key.pem"
        cert.write_text("cert\n", encoding="utf-8")
        key.write_text("key\n", encoding="utf-8")

        project_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment.update({
            "OPENASICMANAGER_ENV": str(
                self.temp / "missing.env"
            ),
            "ASIC_MANAGER_DB": str(db_path),
            "REMOTE_WEB_ENABLED": (
                "true" if enabled else "false"
            ),
            "REMOTE_WEB_SECRET": "x" * 40,
            "PUBLIC_DOMAIN": "manager.example.com",
            "REMOTE_WEB_BASE_DOMAIN": "miners.example.com",
            "REMOTE_WEB_ALLOWED_CIDR": allowed_cidr,
            "REMOTE_WEB_CERT": str(cert),
            "REMOTE_WEB_KEY": str(key),
        })

        result = subprocess.run(
            [
                sys.executable,
                str(
                    project_root
                    / "scripts"
                    / "generate-remote-nginx"
                ),
                "--stdout",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            cwd=project_root,
        )

        if result.returncode != 0:
            self.fail(
                "generator failed: "
                + result.stderr
            )

        return result.stdout

    def test_inventory_add_remove_and_ip_change_change_rendered_hosts(self):
        first = self._generator_output([
            ("one", "192.168.1.10"),
        ])
        added = self._generator_output([
            ("one", "192.168.1.10"),
            ("two", "192.168.1.11"),
        ])
        removed = self._generator_output([
            ("two", "192.168.1.11"),
        ])
        changed = self._generator_output([
            ("two", "192.168.1.12"),
        ])

        self.assertIn(
            "m192-168-1-10.miners.example.com",
            first,
        )
        self.assertIn(
            "m192-168-1-11.miners.example.com",
            added,
        )
        self.assertNotIn(
            "m192-168-1-10.miners.example.com",
            removed,
        )
        self.assertNotIn(
            "m192-168-1-11.miners.example.com",
            changed,
        )
        self.assertIn(
            "m192-168-1-12.miners.example.com",
            changed,
        )

    def test_miner_outside_allowed_cidr_is_omitted(self):
        output = self._generator_output(
            [("outside", "192.168.1.10")],
            allowed_cidr="10.0.0.0/24",
        )

        self.assertIn(
            "no managed ASICs are inside",
            output,
        )
        self.assertNotIn(
            "192.168.1.10",
            output,
        )

    def test_remote_web_disabled_renders_disabled_state(self):
        output = self._generator_output(
            [("one", "192.168.1.10")],
            enabled=False,
        )

        self.assertEqual(
            output,
            "# OpenASICManager Remote Web is disabled.\n",
        )


if __name__ == "__main__":
    unittest.main()
