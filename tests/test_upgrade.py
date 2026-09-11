import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from maintenance.upgrade import (
    UpgradeConfig,
    perform_upgrade,
    validate_source_tree,
    validate_upgrade_path,
)


class FakeCommandRunner:
    def __init__(
        self,
        runtime_version="0.3.0",
        fail_dependency=False,
        fail_start_once=False,
    ):
        self.runtime_version = runtime_version
        self.fail_dependency = fail_dependency
        self.fail_start_once = fail_start_once
        self.start_failures = 0
        self.calls = []

    def __call__(self, command, timeout=None, check=True, cwd=None):
        command = [str(item) for item in command]
        self.calls.append(command)

        if self.fail_dependency and "pip" in command and "-r" in command:
            raise RuntimeError("dependency installation failed")

        if (
            self.fail_start_once
            and command[:2] == ["systemctl", "start"]
            and command[2] == "openasicmanager.service"
            and self.start_failures == 0
        ):
            self.start_failures += 1
            raise RuntimeError("service startup failed")

        if "-c" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=self.runtime_version + "\n",
                stderr="",
            )

        return SimpleNamespace(returncode=0, stdout="", stderr="")


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(
            tempfile.mkdtemp(
                prefix="openasic-upgrade-test-"
            )
        )
        self.install = self.temp / "opt" / "openasicmanager"
        self.source = self.temp / "release"
        self.data = self.temp / "var" / "lib" / "openasicmanager"
        self.config_dir = self.temp / "etc" / "openasicmanager"
        self.backups = self.temp / "backups"
        self.systemd = self.temp / "systemd"
        self.database = self.data / "openasicmanager.db"
        self.env_file = self.config_dir / "openasicmanager.env"

        for path in (
            self.install,
            self.data,
            self.config_dir,
            self.backups,
            self.systemd,
        ):
            path.mkdir(parents=True, exist_ok=True)

        (self.install / "VERSION").write_text(
            "0.2.0\n",
            encoding="utf-8",
        )
        (self.install / "old-marker").write_text(
            "old\n",
            encoding="utf-8",
        )
        self.database.write_text(
            "original-db\n",
            encoding="utf-8",
        )
        self.env_file.write_text(
            "SECRET=preserved\n",
            encoding="utf-8",
        )

        for name in (
            "openasicmanager.service",
            "openasicmanager-firmware-detect.service",
            "openasicmanager-firmware-detect.timer",
        ):
            (self.systemd / name).write_text(
                "old-" + name + "\n",
                encoding="utf-8",
            )

        self._make_source("0.3.0")

    def tearDown(self):
        shutil.rmtree(
            self.temp,
            ignore_errors=True,
        )

    def _make_source(self, version):
        if self.source.exists():
            shutil.rmtree(self.source)

        (self.source / "app").mkdir(parents=True)
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "deploy" / "systemd").mkdir(parents=True)

        (self.source / "VERSION").write_text(
            version + "\n",
            encoding="utf-8",
        )
        (self.source / "requirements.txt").write_text(
            "fastapi\n",
            encoding="utf-8",
        )
        (self.source / "app" / "app_version.py").write_text(
            'APP_VERSION = "' + version + '"\n',
            encoding="utf-8",
        )
        (self.source / "app" / "new-marker").write_text(
            "new\n",
            encoding="utf-8",
        )
        (self.source / "scripts" / "tool").write_text(
            "#!/bin/sh\n",
            encoding="utf-8",
        )

        for name in (
            "openasicmanager.service",
            "openasicmanager-firmware-detect.service",
            "openasicmanager-firmware-detect.timer",
        ):
            (
                self.source
                / "deploy"
                / "systemd"
                / name
            ).write_text(
                "new-" + name + "\n",
                encoding="utf-8",
            )

    def _config(self):
        return UpgradeConfig(
            source_root=self.source,
            install_root=self.install,
            database_path=self.database,
            env_file=self.env_file,
            backup_root=self.backups,
            systemd_root=self.systemd,
            health_url="http://127.0.0.1:18089/health",
            health_timeout=1,
        )

    def _doctor(self, seen=None, fail_version=None):
        def runner(config):
            if seen is not None:
                seen.append(config.version)

            if config.version == fail_version:
                return {
                    "status": "fail",
                    "checks": [
                        {
                            "code": "health",
                            "status": "fail",
                        }
                    ],
                }

            return {
                "status": "pass",
                "checks": [],
            }

        return runner

    def _backup_functions(self, calls=None):
        def create_backup(**kwargs):
            if calls is not None:
                calls.append("create")

            backup = self.backups / "verified-backup"
            backup.mkdir(
                parents=True,
                exist_ok=True,
            )
            shutil.copy2(
                self.database,
                backup / "database",
            )
            shutil.copy2(
                self.env_file,
                backup / "environment",
            )
            return backup

        def restore_backup(
            backup_dir,
            database_path,
            env_file,
        ):
            if calls is not None:
                calls.append("restore")

            shutil.copy2(
                Path(backup_dir) / "database",
                database_path,
            )
            shutil.copy2(
                Path(backup_dir) / "environment",
                env_file,
            )
            return {
                "source_version": "0.2.0"
            }

        return create_backup, restore_backup

    def test_version_contract(self):
        validate_upgrade_path(
            "0.2.0",
            "0.3.0",
        )
        validate_upgrade_path(
            "0.2.9",
            "1.0.0",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "public upgrades start",
        ):
            validate_upgrade_path(
                "0.1.2",
                "0.3.0",
            )

        with self.assertRaisesRegex(
            RuntimeError,
            "newer",
        ):
            validate_upgrade_path(
                "0.2.0",
                "0.2.0",
            )

        with self.assertRaisesRegex(
            RuntimeError,
            "version format",
        ):
            validate_upgrade_path(
                "legacy-1.5.2",
                "0.3.0",
            )

    def test_source_tree_must_not_overlap_installation(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "must not overlap",
        ):
            validate_source_tree(
                self.install,
                self.install,
            )

    def test_preflight_failure_happens_before_backup(self):
        calls = []
        create_backup, restore_backup = (
            self._backup_functions(calls)
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "preflight failed",
        ):
            perform_upgrade(
                self._config(),
                doctor_runner=self._doctor(
                    fail_version="0.2.0"
                ),
                backup_creator=create_backup,
                backup_restorer=restore_backup,
                command_runner=FakeCommandRunner(),
            )

        self.assertEqual(calls, [])
        self.assertTrue(
            (self.install / "old-marker").is_file()
        )

    def test_dependency_failure_does_not_stop_running_service(self):
        backup_calls = []
        create_backup, restore_backup = (
            self._backup_functions(
                backup_calls
            )
        )
        commands = FakeCommandRunner(
            fail_dependency=True
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "dependency installation failed",
        ):
            perform_upgrade(
                self._config(),
                doctor_runner=self._doctor(),
                backup_creator=create_backup,
                backup_restorer=restore_backup,
                command_runner=commands,
            )

        self.assertEqual(
            backup_calls,
            ["create"],
        )
        self.assertFalse(
            any(
                call
                and call[0] == "systemctl"
                for call in commands.calls
            )
        )
        self.assertEqual(
            (self.install / "VERSION").read_text().strip(),
            "0.2.0",
        )

    def test_successful_upgrade_preserves_database_and_configuration(self):
        seen = []
        create_backup, restore_backup = (
            self._backup_functions()
        )
        commands = FakeCommandRunner(
            runtime_version="0.3.0"
        )

        def health_waiter(
            url,
            version,
            timeout,
            **kwargs,
        ):
            return {
                "status": "ok",
                "version": version,
            }

        report = perform_upgrade(
            self._config(),
            doctor_runner=self._doctor(
                seen=seen
            ),
            backup_creator=create_backup,
            backup_restorer=restore_backup,
            command_runner=commands,
            health_waiter=health_waiter,
        )

        self.assertEqual(
            report["status"],
            "success",
        )
        self.assertEqual(
            report["from_version"],
            "0.2.0",
        )
        self.assertEqual(
            report["to_version"],
            "0.3.0",
        )
        self.assertEqual(
            seen,
            ["0.2.0", "0.3.0"],
        )
        self.assertEqual(
            (self.install / "VERSION").read_text().strip(),
            "0.3.0",
        )
        self.assertTrue(
            (
                self.install
                / "app"
                / "new-marker"
            ).is_file()
        )
        self.assertFalse(
            (self.install / "old-marker").exists()
        )
        self.assertEqual(
            self.database.read_text(),
            "original-db\n",
        )
        self.assertEqual(
            self.env_file.read_text(),
            "SECRET=preserved\n",
        )
        self.assertEqual(
            (
                self.systemd
                / "openasicmanager.service"
            ).read_text(),
            "new-openasicmanager.service\n",
        )
        self.assertTrue(
            Path(report["backup_dir"]).is_dir()
        )

    def test_health_failure_rolls_back_code_units_and_database(self):
        backup_calls = []
        create_backup, restore_backup = (
            self._backup_functions(
                backup_calls
            )
        )
        commands = FakeCommandRunner(
            runtime_version="0.3.0"
        )

        def health_waiter(
            url,
            version,
            timeout,
            **kwargs,
        ):
            if version == "0.3.0":
                self.database.write_text(
                    "migrated-db\n",
                    encoding="utf-8",
                )
                raise RuntimeError(
                    "target health failed"
                )

            return {
                "status": "ok",
                "version": version,
            }

        with self.assertRaisesRegex(
            RuntimeError,
            "rolled back",
        ):
            perform_upgrade(
                self._config(),
                doctor_runner=self._doctor(),
                backup_creator=create_backup,
                backup_restorer=restore_backup,
                command_runner=commands,
                health_waiter=health_waiter,
            )

        self.assertEqual(
            backup_calls,
            ["create", "restore"],
        )
        self.assertEqual(
            (self.install / "VERSION").read_text().strip(),
            "0.2.0",
        )
        self.assertTrue(
            (self.install / "old-marker").is_file()
        )
        self.assertEqual(
            self.database.read_text(),
            "original-db\n",
        )
        self.assertEqual(
            self.env_file.read_text(),
            "SECRET=preserved\n",
        )
        self.assertEqual(
            (
                self.systemd
                / "openasicmanager.service"
            ).read_text(),
            "old-openasicmanager.service\n",
        )

    def test_service_start_failure_rolls_back(self):
        create_backup, restore_backup = (
            self._backup_functions()
        )
        commands = FakeCommandRunner(
            runtime_version="0.3.0",
            fail_start_once=True,
        )

        def health_waiter(
            url,
            version,
            timeout,
            **kwargs,
        ):
            return {
                "status": "ok",
                "version": version,
            }

        with self.assertRaisesRegex(
            RuntimeError,
            "rolled back",
        ):
            perform_upgrade(
                self._config(),
                doctor_runner=self._doctor(),
                backup_creator=create_backup,
                backup_restorer=restore_backup,
                command_runner=commands,
                health_waiter=health_waiter,
            )

        self.assertEqual(
            (self.install / "VERSION").read_text().strip(),
            "0.2.0",
        )
        self.assertGreaterEqual(
            commands.start_failures,
            1,
        )

    def test_pre_switch_failure_restores_old_units_and_service(self):
        create_backup, restore_backup = (
            self._backup_functions()
        )
        commands = FakeCommandRunner(
            runtime_version="0.3.0"
        )

        def health_waiter(
            url,
            version,
            timeout,
            **kwargs,
        ):
            return {
                "status": "ok",
                "version": version,
            }

        with mock.patch(
            "maintenance.upgrade._install_target_units",
            side_effect=RuntimeError(
                "unit install failed"
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "previous service was restored",
            ):
                perform_upgrade(
                    self._config(),
                    doctor_runner=self._doctor(),
                    backup_creator=create_backup,
                    backup_restorer=restore_backup,
                    command_runner=commands,
                    health_waiter=health_waiter,
                )

        self.assertEqual(
            (self.install / "VERSION").read_text().strip(),
            "0.2.0",
        )
        self.assertEqual(
            (
                self.systemd
                / "openasicmanager.service"
            ).read_text(),
            "old-openasicmanager.service\n",
        )
        self.assertIn(
            [
                "systemctl",
                "start",
                "openasicmanager.service",
            ],
            commands.calls,
        )
        self.assertIn(
            [
                "systemctl",
                "start",
                "openasicmanager-firmware-detect.timer",
            ],
            commands.calls,
        )

    def test_postflight_failure_rolls_back(self):
        create_backup, restore_backup = (
            self._backup_functions()
        )
        commands = FakeCommandRunner(
            runtime_version="0.3.0"
        )

        def health_waiter(
            url,
            version,
            timeout,
            **kwargs,
        ):
            return {
                "status": "ok",
                "version": version,
            }

        with self.assertRaisesRegex(
            RuntimeError,
            "rolled back",
        ):
            perform_upgrade(
                self._config(),
                doctor_runner=self._doctor(
                    fail_version="0.3.0"
                ),
                backup_creator=create_backup,
                backup_restorer=restore_backup,
                command_runner=commands,
                health_waiter=health_waiter,
            )

        self.assertEqual(
            (self.install / "VERSION").read_text().strip(),
            "0.2.0",
        )


if __name__ == "__main__":
    unittest.main()
