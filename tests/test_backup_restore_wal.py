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


def _create_database(path):
    conn = sqlite3.connect(
        str(path)
    )
    conn.execute(
        "PRAGMA journal_mode=WAL"
    )
    conn.execute(
        "CREATE TABLE state (value TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO state(value) VALUES ('backup')"
    )
    conn.commit()
    return conn


def _state(path):
    conn = sqlite3.connect(
        str(path)
    )
    try:
        return conn.execute(
            "SELECT value FROM state"
        ).fetchone()[0]
    finally:
        conn.close()


class BackupWalSafetyTests(unittest.TestCase):
    def test_backup_database_is_standalone_without_wal_sidecars(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "openasicmanager.db"
            database.parent.mkdir()

            live = _create_database(
                database
            )

            try:
                backup_dir = create_backup(
                    database_path=database,
                    env_file=root / "missing.env",
                    backup_root=root / "backups",
                    source_version="0.2.0",
                    reference_files=[],
                )
            finally:
                live.close()

            manifest = verify_backup(
                backup_dir
            )

            database_entry = next(
                entry
                for entry in manifest["files"]
                if entry["role"] == "database"
            )

            archived = (
                backup_dir
                / database_entry["path"]
            )

            self.assertTrue(
                archived.is_file()
            )
            self.assertFalse(
                Path(
                    str(archived) + "-wal"
                ).exists()
            )
            self.assertFalse(
                Path(
                    str(archived) + "-shm"
                ).exists()
            )

            conn = sqlite3.connect(
                str(archived)
            )
            try:
                journal_mode = conn.execute(
                    "PRAGMA journal_mode"
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(
                journal_mode.lower(),
                "delete",
            )

    def test_failed_restore_restores_existing_sidecars(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "openasicmanager.db"
            database.parent.mkdir()

            live = _create_database(
                database
            )

            env_file = root / "etc" / "openasicmanager.env"
            env_file.parent.mkdir()
            env_file.write_text(
                "BITMAIN_PASSWORD=backup-secret\n",
                encoding="utf-8",
            )
            os.chmod(
                env_file,
                0o640,
            )

            backup_dir = create_backup(
                database_path=database,
                env_file=env_file,
                backup_root=root / "backups",
                source_version="0.2.0",
                reference_files=[],
            )

            live.execute(
                "UPDATE state SET value='current'"
            )
            live.commit()
            live.close()

            env_file.write_text(
                "BITMAIN_PASSWORD=current-secret\n",
                encoding="utf-8",
            )

            pre_restore_state = _state(
                database
            )
            pre_restore_env = env_file.read_text(
                encoding="utf-8"
            )

            wal_path = Path(
                str(database) + "-wal"
            )
            shm_path = Path(
                str(database) + "-shm"
            )
            wal_path.write_bytes(b"")
            shm_path.write_bytes(b"")

            real_replace = backup_service.os.replace
            calls = {"count": 0}

            def fail_environment_replace(
                source,
                destination,
            ):
                calls["count"] += 1

                if calls["count"] == 2:
                    raise OSError(
                        "simulated environment replace failure"
                    )

                return real_replace(
                    source,
                    destination,
                )

            with mock.patch(
                "maintenance.backup._replace_file",
                side_effect=fail_environment_replace,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "simulated environment",
                ):
                    restore_backup(
                        backup_dir,
                        database_path=database,
                        env_file=env_file,
                    )

            self.assertTrue(
                wal_path.exists()
            )
            self.assertTrue(
                shm_path.exists()
            )

            self.assertEqual(
                list(
                    database.parent.glob(
                        ".*rollback-*"
                    )
                ),
                [],
            )

            # Remove the artificial empty sidecars before opening the
            # database again. The restore test above verifies that they
            # were restored to their original paths on rollback.
            wal_path.unlink()
            shm_path.unlink()

            self.assertEqual(
                _state(database),
                pre_restore_state,
            )
            self.assertEqual(
                env_file.read_text(
                    encoding="utf-8"
                ),
                pre_restore_env,
            )


if __name__ == "__main__":
    unittest.main()
