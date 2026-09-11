import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from maintenance.doctor import DoctorConfig, exit_code, run_diagnostics


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install = (
            self.root
            / "opt"
            / "openasicmanager"
        )
        self.data = (
            self.root
            / "var"
            / "lib"
            / "openasicmanager"
        )
        self.config = (
            self.root
            / "etc"
            / "openasicmanager"
        )
        self.backups = (
            self.root
            / "var"
            / "backups"
            / "openasicmanager"
        )
        self.remote_nginx = (
            self.root
            / "etc"
            / "nginx"
            / "sites-available"
            / "openasicmanager-remote"
        )

        for path in (
            self.install,
            self.data,
            self.config,
            self.backups,
            self.remote_nginx.parent,
        ):
            path.mkdir(
                parents=True,
                exist_ok=True,
            )

        (
            self.install
            / "VERSION"
        ).write_text(
            "0.2.0\n",
            encoding="utf-8",
        )

        venv_python = (
            self.install
            / "venv"
            / "bin"
            / "python"
        )
        venv_python.parent.mkdir(
            parents=True
        )
        venv_python.write_text(
            "",
            encoding="utf-8",
        )

        self.database = (
            self.data
            / "openasicmanager.db"
        )
        self._create_database(
            self.database
        )

        self.env = (
            self.config
            / "openasicmanager.env"
        )
        self.env.write_text(
            "REMOTE_WEB_ENABLED=false\n"
            "BITMAIN_PASSWORD=do-not-print-this\n"
            "TELEGRAM_BOT_TOKEN=also-secret\n",
            encoding="utf-8",
        )
        os.chmod(
            self.env,
            0o640,
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _create_database(path):
        conn = sqlite3.connect(
            path
        )
        conn.executescript("""
            CREATE TABLE settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE miners(
                id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE schedule_rules(
                id INTEGER PRIMARY KEY
            );

            INSERT INTO settings(key, value)
            VALUES ('scheduler_enabled', '0');

            INSERT INTO miners(id, enabled)
            VALUES (1, 1);

            INSERT INTO miners(id, enabled)
            VALUES (2, 0);

            INSERT INTO schedule_rules(id)
            VALUES (1);
        """)
        conn.commit()
        conn.close()

    def config_obj(self, warning=1):
        return DoctorConfig(
            version="0.2.0",
            install_root=self.install,
            database_path=self.database,
            env_file=self.env,
            backup_root=self.backups,
            remote_nginx=self.remote_nginx,
            health_url=(
                "http://127.0.0.1:8088/health"
            ),
            free_bytes_warning=warning,
        )

    @staticmethod
    def _completed(
        stdout="",
        stderr="",
        returncode=0,
    ):
        return subprocess.CompletedProcess(
            [],
            returncode,
            stdout,
            stderr,
        )

    def command_ok(
        self,
        command,
        timeout=10,
    ):
        if command[:2] == [
            "systemctl",
            "is-active",
        ]:
            return (
                self._completed(
                    "active\n"
                ),
                None,
            )

        if command[:2] == [
            "systemctl",
            "is-enabled",
        ]:
            return (
                self._completed(
                    "enabled\n"
                ),
                None,
            )

        if command[-1:] == ["-t"]:
            return (
                self._completed(),
                None,
            )

        return (
            self._completed(
                "ok\n"
            ),
            None,
        )

    def run_healthy(
        self,
        command=None,
        health=None,
        nginx="/usr/sbin/nginx",
        warning=1,
    ):
        command = (
            command
            or self.command_ok
        )
        health = (
            health
            or {
                "status": "ok",
                "version": "0.2.0",
            }
        )

        with mock.patch(
            "maintenance.doctor._command",
            side_effect=command,
        ), mock.patch(
            "maintenance.doctor._health",
            return_value=health,
        ), mock.patch(
            "maintenance.doctor.shutil.which",
            return_value=nginx,
        ):
            return run_diagnostics(
                self.config_obj(
                    warning=warning
                )
            )

    def test_healthy_installation_passes_and_reports_read_only_state(self):
        report = self.run_healthy()

        self.assertEqual(
            report["status"],
            "pass",
        )
        self.assertEqual(
            exit_code(report),
            0,
        )

        database = next(
            item
            for item in report["checks"]
            if item["code"] == "database"
        )

        self.assertEqual(
            database["data"]["miners"],
            2,
        )
        self.assertEqual(
            database["data"][
                "enabled_miners"
            ],
            1,
        )
        self.assertEqual(
            database["data"][
                "schedule_rules"
            ],
            1,
        )
        self.assertFalse(
            database["data"][
                "scheduler_enabled"
            ]
        )

    def test_stopped_service_fails(self):
        def command(
            command,
            timeout=10,
        ):
            if command == [
                "systemctl",
                "is-active",
                "openasicmanager.service",
            ]:
                return (
                    self._completed(
                        "inactive\n",
                        returncode=3,
                    ),
                    None,
                )

            return self.command_ok(
                command,
                timeout,
            )

        report = self.run_healthy(
            command=command
        )

        self.assertEqual(
            report["status"],
            "fail",
        )
        self.assertEqual(
            exit_code(report),
            1,
        )

    def test_corrupt_database_fails(self):
        self.database.write_bytes(
            b"not a sqlite database"
        )

        report = self.run_healthy()

        item = next(
            item
            for item in report["checks"]
            if item["code"] == "database"
        )

        self.assertEqual(
            item["status"],
            "fail",
        )
        self.assertEqual(
            report["status"],
            "fail",
        )

    def test_missing_environment_file_fails(self):
        self.env.unlink()

        report = self.run_healthy()

        item = next(
            item
            for item in report["checks"]
            if item["code"] == "environment"
        )

        self.assertEqual(
            item["status"],
            "fail",
        )

    def test_invalid_nginx_config_fails(self):
        def command(
            command,
            timeout=10,
        ):
            if command[-1:] == ["-t"]:
                return (
                    self._completed(
                        stderr="bad nginx",
                        returncode=1,
                    ),
                    None,
                )

            return self.command_ok(
                command,
                timeout,
            )

        report = self.run_healthy(
            command=command
        )

        item = next(
            item
            for item in report["checks"]
            if item["code"] == "nginx"
        )

        self.assertEqual(
            item["status"],
            "fail",
        )

    def test_low_disk_space_is_warning_not_failure(self):
        disk = mock.Mock(
            total=10_000_000,
            used=9_900_000,
            free=100_000,
        )

        with mock.patch(
            "maintenance.doctor._command",
            side_effect=self.command_ok,
        ), mock.patch(
            "maintenance.doctor._health",
            return_value={
                "status": "ok",
                "version": "0.2.0",
            },
        ), mock.patch(
            "maintenance.doctor.shutil.which",
            return_value="/usr/sbin/nginx",
        ), mock.patch(
            "maintenance.doctor.shutil.disk_usage",
            return_value=disk,
        ):
            report = run_diagnostics(
                self.config_obj(
                    warning=1_000_000
                )
            )

        self.assertEqual(
            report["status"],
            "warn",
        )
        self.assertEqual(
            exit_code(report),
            0,
        )

    def test_json_report_never_contains_environment_secrets(self):
        report = self.run_healthy()
        payload = json.dumps(
            report,
            sort_keys=True,
        )

        self.assertNotIn(
            "do-not-print-this",
            payload,
        )
        self.assertNotIn(
            "also-secret",
            payload,
        )
        self.assertIn(
            "remote_web_enabled",
            payload,
        )

    def test_remote_web_enabled_requires_generated_nginx_config(self):
        self.env.write_text(
            "REMOTE_WEB_ENABLED=true\n"
            "BITMAIN_PASSWORD=hidden\n",
            encoding="utf-8",
        )
        os.chmod(
            self.env,
            0o640,
        )

        report = self.run_healthy()

        item = next(
            item
            for item in report["checks"]
            if item["code"] == "nginx"
        )

        self.assertEqual(
            item["status"],
            "fail",
        )
        self.assertIn(
            "generated nginx",
            item["message"],
        )


if __name__ == "__main__":
    unittest.main()
