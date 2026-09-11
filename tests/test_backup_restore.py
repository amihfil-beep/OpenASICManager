import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from maintenance import backup as backup_service
from maintenance.backup import (
    create_backup,
    restore_backup,
    verify_backup,
)


def create_test_database(path):
    conn = sqlite3.connect(
        str(path)
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE miners (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            ip TEXT NOT NULL UNIQUE
        )
    """)

    conn.execute("""
        CREATE TABLE schedule_rules (
            id INTEGER PRIMARY KEY,
            action TEXT NOT NULL,
            time_minutes INTEGER NOT NULL
        )
    """)

    conn.execute(
        "INSERT INTO settings(key, value) "
        "VALUES ('scheduler_enabled', '1')"
    )

    conn.execute(
        "INSERT INTO miners(id, name, ip) "
        "VALUES (1, 'T21-01', '192.168.1.108')"
    )

    conn.execute(
        "INSERT INTO schedule_rules("
        "id, action, time_minutes"
        ") VALUES (1, 'RESUME', 1260)"
    )

    conn.commit()

    return conn


def read_state(path):
    conn = sqlite3.connect(
        str(path)
    )

    try:
        setting = conn.execute(
            "SELECT value FROM settings "
            "WHERE key='scheduler_enabled'"
        ).fetchone()[0]

        miner = conn.execute(
            "SELECT name, ip FROM miners "
            "WHERE id=1"
        ).fetchone()

        rule = conn.execute(
            "SELECT action, time_minutes "
            "FROM schedule_rules WHERE id=1"
        ).fetchone()

        return (
            setting,
            miner,
            rule,
        )
    finally:
        conn.close()


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temporary.name
        )

        self.database = (
            self.root
            / "data"
            / "openasicmanager.db"
        )

        self.database.parent.mkdir()

        self.live_connection = (
            create_test_database(
                self.database
            )
        )

        self.env_file = (
            self.root
            / "etc"
            / "openasicmanager.env"
        )

        self.env_file.parent.mkdir()

        self.env_file.write_text(
            "BITMAIN_PASSWORD=supersecret\n"
            "TELEGRAM_BOT_TOKEN=token-value\n",
            encoding="utf-8",
        )

        os.chmod(
            self.env_file,
            0o640,
        )

        self.reference = (
            self.root
            / "deployment"
            / "openasicmanager.service"
        )

        self.reference.parent.mkdir()

        self.reference.write_text(
            "[Service]\n"
            "ExecStart=/opt/openasicmanager/"
            "venv/bin/uvicorn\n",
            encoding="utf-8",
        )

        self.backup_root = (
            self.root
            / "backups"
        )

    def tearDown(self):
        self.live_connection.close()
        self.temporary.cleanup()

    def create_backup(self, env_file=None):
        return create_backup(
            database_path=self.database,
            env_file=(
                self.env_file
                if env_file is None
                else env_file
            ),
            backup_root=self.backup_root,
            source_version="0.2.0",
            reference_files=[
                self.reference
            ],
        )

    def test_online_backup_round_trip_preserves_state(self):
        # Add committed data while the WAL connection
        # remains open. sqlite3.Connection.backup()
        # must capture it without service downtime.
        self.live_connection.execute(
            "INSERT INTO miners(id, name, ip) "
            "VALUES (2, 'T21-02', '192.168.1.109')"
        )
        self.live_connection.commit()

        backup_dir = (
            self.create_backup()
        )

        manifest = verify_backup(
            backup_dir
        )

        self.assertEqual(
            manifest[
                "source_version"
            ],
            "0.2.0",
        )

        self.assertTrue(
            manifest[
                "environment_present"
            ]
        )

        self.assertEqual(
            stat_mode(
                backup_dir
            ),
            0o700,
        )

        environment_entry = next(
            item
            for item in manifest[
                "files"
            ]
            if item[
                "role"
            ] == "environment"
        )

        environment_archive = (
            backup_dir
            / environment_entry[
                "path"
            ]
        )

        self.assertEqual(
            stat_mode(
                environment_archive
            ),
            0o600,
        )

        manifest_text = (
            backup_dir
            / "manifest.json"
        ).read_text(
            encoding="utf-8"
        )

        self.assertNotIn(
            "supersecret",
            manifest_text,
        )
        self.assertNotIn(
            "token-value",
            manifest_text,
        )

        self.live_connection.execute(
            "UPDATE settings "
            "SET value='0' "
            "WHERE key='scheduler_enabled'"
        )
        self.live_connection.execute(
            "UPDATE miners "
            "SET name='BROKEN' "
            "WHERE id=1"
        )
        self.live_connection.execute(
            "UPDATE schedule_rules "
            "SET action='PAUSE', "
            "time_minutes=420 "
            "WHERE id=1"
        )
        self.live_connection.commit()
        self.live_connection.close()

        self.env_file.write_text(
            "BITMAIN_PASSWORD=changed\n",
            encoding="utf-8",
        )

        result = restore_backup(
            backup_dir,
            database_path=self.database,
            env_file=self.env_file,
        )

        self.assertEqual(
            result[
                "source_version"
            ],
            "0.2.0",
        )

        self.assertTrue(
            result[
                "environment_restored"
            ]
        )

        self.assertEqual(
            read_state(
                self.database
            ),
            (
                "1",
                (
                    "T21-01",
                    "192.168.1.108",
                ),
                (
                    "RESUME",
                    1260,
                ),
            ),
        )

        self.assertEqual(
            self.env_file.read_text(
                encoding="utf-8"
            ),
            (
                "BITMAIN_PASSWORD=supersecret\n"
                "TELEGRAM_BOT_TOKEN=token-value\n"
            ),
        )

        # Reopen for tearDown.
        self.live_connection = (
            sqlite3.connect(
                str(
                    self.database
                )
            )
        )

    def test_missing_optional_environment_is_not_restored(self):
        missing_env = (
            self.root
            / "missing.env"
        )

        backup_dir = (
            self.create_backup(
                env_file=missing_env
            )
        )

        manifest = verify_backup(
            backup_dir
        )

        self.assertFalse(
            manifest[
                "environment_present"
            ]
        )

        self.live_connection.close()

        current = (
            "BITMAIN_PASSWORD="
            "keep-current\n"
        )

        self.env_file.write_text(
            current,
            encoding="utf-8",
        )

        result = restore_backup(
            backup_dir,
            database_path=self.database,
            env_file=self.env_file,
        )

        self.assertFalse(
            result[
                "environment_restored"
            ]
        )

        self.assertEqual(
            self.env_file.read_text(
                encoding="utf-8"
            ),
            current,
        )

        self.live_connection = (
            sqlite3.connect(
                str(
                    self.database
                )
            )
        )

    def test_corrupted_backup_is_rejected_before_restore(self):
        backup_dir = (
            self.create_backup()
        )

        manifest = json.loads(
            (
                backup_dir
                / "manifest.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        environment_entry = next(
            item
            for item in manifest[
                "files"
            ]
            if item[
                "role"
            ] == "environment"
        )

        environment_archive = (
            backup_dir
            / environment_entry[
                "path"
            ]
        )

        environment_archive.write_text(
            "corrupted\n",
            encoding="utf-8",
        )

        original_state = read_state(
            self.database
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "(size|checksum) mismatch",
        ):
            restore_backup(
                backup_dir,
                database_path=self.database,
                env_file=self.env_file,
            )

        self.assertEqual(
            read_state(
                self.database
            ),
            original_state,
        )

    def test_restore_rolls_back_database_if_environment_replace_fails(self):
        backup_dir = (
            self.create_backup()
        )

        self.live_connection.execute(
            "UPDATE settings "
            "SET value='0' "
            "WHERE key='scheduler_enabled'"
        )
        self.live_connection.commit()
        self.live_connection.close()

        self.env_file.write_text(
            "BITMAIN_PASSWORD=current\n",
            encoding="utf-8",
        )

        pre_restore_state = read_state(
            self.database
        )
        pre_restore_env = (
            self.env_file.read_text(
                encoding="utf-8"
            )
        )

        real_replace = (
            backup_service.os.replace
        )

        calls = {
            "count": 0,
        }

        def fail_second_replace(
            source,
            destination,
        ):
            calls[
                "count"
            ] += 1

            if calls[
                "count"
            ] == 2:
                raise OSError(
                    "simulated environment "
                    "replace failure"
                )

            return real_replace(
                source,
                destination,
            )

        with mock.patch(
            "maintenance.backup._replace_file",
            side_effect=(
                fail_second_replace
            ),
        ):
            with self.assertRaisesRegex(
                OSError,
                "simulated environment",
            ):
                restore_backup(
                    backup_dir,
                    database_path=(
                        self.database
                    ),
                    env_file=(
                        self.env_file
                    ),
                )

        self.assertEqual(
            read_state(
                self.database
            ),
            pre_restore_state,
        )

        self.assertEqual(
            self.env_file.read_text(
                encoding="utf-8"
            ),
            pre_restore_env,
        )

        self.live_connection = (
            sqlite3.connect(
                str(
                    self.database
                )
            )
        )

    def test_backup_root_inside_install_tree_is_rejected(self):
        install_root = (
            self.root
            / "opt"
            / "openasicmanager"
        )

        backup_root = (
            install_root
            / "backups"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "outside the application",
        ):
            create_backup(
                database_path=(
                    self.database
                ),
                env_file=(
                    self.env_file
                ),
                backup_root=(
                    backup_root
                ),
                source_version="0.2.0",
                reference_files=[],
                install_root=(
                    install_root
                ),
            )


def stat_mode(path):
    return (
        os.stat(
            path
        ).st_mode
        & 0o777
    )


if __name__ == "__main__":
    unittest.main()
