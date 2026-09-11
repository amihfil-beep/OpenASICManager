"""Safe OpenASICManager backup and restore operations."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid

from maintenance.repository import (
    backup_sqlite,
    sqlite_quick_check,
)


BACKUP_FORMAT = 1

DEFAULT_BACKUP_ROOT = Path(
    "/var/backups/openasicmanager"
)

DEFAULT_DATABASE_PATH = Path(
    "/var/lib/openasicmanager/openasicmanager.db"
)

DEFAULT_ENV_FILE = Path(
    "/etc/openasicmanager/openasicmanager.env"
)

DEFAULT_INSTALL_ROOT = Path(
    "/opt/openasicmanager"
)

DEFAULT_REFERENCE_FILES = (
    Path(
        "/etc/systemd/system/"
        "openasicmanager.service"
    ),
    Path(
        "/etc/systemd/system/"
        "openasicmanager-firmware-detect.service"
    ),
    Path(
        "/etc/systemd/system/"
        "openasicmanager-firmware-detect.timer"
    ),
    Path(
        "/etc/nginx/sites-available/"
        "openasicmanager"
    ),
    Path(
        "/etc/nginx/sites-available/"
        "openasicmanager-remote"
    ),
)


def _sha256(path):
    digest = hashlib.sha256()

    with Path(path).open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _within(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()

    return (
        path == root
        or root in path.parents
    )


def _copy_file(
    source,
    destination,
    mode=0o600,
):
    source = Path(source)
    destination = Path(destination)

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copyfile(
        source,
        destination,
    )

    os.chmod(
        destination,
        mode,
    )


def _manifest_entry(
    root,
    path,
    role,
    source,
    restore,
):
    root = Path(root).resolve()
    path = Path(path).resolve()

    relative = path.relative_to(
        root
    ).as_posix()

    return {
        "path": relative,
        "role": role,
        "source": str(source),
        "restore": bool(restore),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _reference_archive_path(
    staging,
    source,
    index,
):
    source = Path(source)
    systemd_root = Path(
        "/etc/systemd/system"
    )
    nginx_root = Path(
        "/etc/nginx/sites-available"
    )

    try:
        source.relative_to(
            systemd_root
        )
        category = "systemd"
    except ValueError:
        try:
            source.relative_to(
                nginx_root
            )
            category = "nginx"
        except ValueError:
            category = "reference"

    name = source.name

    if category == "reference":
        name = (
            f"{index:02d}-"
            + name
        )

    return (
        Path(staging)
        / "deployment"
        / category
        / name
    )


def _load_manifest(backup_dir):
    backup_dir = Path(
        backup_dir
    ).resolve()

    manifest_path = (
        backup_dir
        / "manifest.json"
    )

    if not manifest_path.is_file():
        raise RuntimeError(
            "Backup manifest is missing: "
            + str(manifest_path)
        )

    try:
        manifest = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise RuntimeError(
            "Backup manifest is invalid: "
            + str(exc)
        ) from exc

    if not isinstance(
        manifest,
        dict,
    ):
        raise RuntimeError(
            "Backup manifest must be an object"
        )

    if manifest.get(
        "format"
    ) != BACKUP_FORMAT:
        raise RuntimeError(
            "Unsupported backup format: "
            + repr(
                manifest.get(
                    "format"
                )
            )
        )

    files = manifest.get(
        "files"
    )

    if not isinstance(
        files,
        list,
    ):
        raise RuntimeError(
            "Backup manifest files must be a list"
        )

    return manifest


def _safe_archive_file(
    backup_dir,
    relative,
):
    backup_dir = Path(
        backup_dir
    ).resolve()

    if (
        not isinstance(
            relative,
            str,
        )
        or
        not relative
    ):
        raise RuntimeError(
            "Invalid archive path in manifest"
        )

    candidate = (
        backup_dir
        / relative
    ).resolve()

    if not _within(
        candidate,
        backup_dir,
    ):
        raise RuntimeError(
            "Archive path escapes backup directory: "
            + relative
        )

    return candidate


def verify_backup(
    backup_dir,
):
    backup_dir = Path(
        backup_dir
    ).resolve()

    manifest = _load_manifest(
        backup_dir
    )

    seen = set()
    database_entries = []

    for entry in manifest[
        "files"
    ]:
        if not isinstance(
            entry,
            dict,
        ):
            raise RuntimeError(
                "Backup manifest contains "
                "an invalid file entry"
            )

        relative = entry.get(
            "path"
        )

        if relative in seen:
            raise RuntimeError(
                "Duplicate archive path: "
                + str(relative)
            )

        seen.add(relative)

        path = _safe_archive_file(
            backup_dir,
            relative,
        )

        if not path.is_file():
            raise RuntimeError(
                "Backup file is missing: "
                + str(relative)
            )

        expected_size = entry.get(
            "size"
        )

        if path.stat().st_size != expected_size:
            raise RuntimeError(
                "Backup file size mismatch: "
                + str(relative)
            )

        expected_hash = entry.get(
            "sha256"
        )

        if (
            not isinstance(
                expected_hash,
                str,
            )
            or
            _sha256(path)
            != expected_hash
        ):
            raise RuntimeError(
                "Backup checksum mismatch: "
                + str(relative)
            )

        if entry.get(
            "role"
        ) == "database":
            database_entries.append(
                entry
            )

    if len(
        database_entries
    ) != 1:
        raise RuntimeError(
            "Backup must contain exactly "
            "one database"
        )

    database_path = (
        _safe_archive_file(
            backup_dir,
            database_entries[0][
                "path"
            ],
        )
    )

    sqlite_quick_check(
        database_path
    )

    return manifest


def create_backup(
    database_path=DEFAULT_DATABASE_PATH,
    env_file=DEFAULT_ENV_FILE,
    backup_root=DEFAULT_BACKUP_ROOT,
    source_version="unknown",
    reference_files=None,
    install_root=DEFAULT_INSTALL_ROOT,
):
    database_path = Path(
        database_path
    ).resolve()

    env_file = Path(
        env_file
    ).resolve()

    backup_root = Path(
        backup_root
    ).resolve()

    install_root = Path(
        install_root
    ).resolve()

    if _within(
        backup_root,
        install_root,
    ):
        raise RuntimeError(
            "Backup root must be outside "
            "the application install tree: "
            + str(install_root)
        )

    if not database_path.is_file():
        raise RuntimeError(
            "Database does not exist: "
            + str(database_path)
        )

    root_existed = (
        backup_root.exists()
    )

    backup_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not root_existed:
        os.chmod(
            backup_root,
            0o700,
        )

    staging = Path(
        tempfile.mkdtemp(
            prefix=".openasicmanager-",
            dir=str(
                backup_root
            ),
        )
    )

    os.chmod(
        staging,
        0o700,
    )

    created_at = (
        datetime.now(
            timezone.utc
        )
        .replace(
            microsecond=0
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )

    backup_id = (
        "openasicmanager-"
        + datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        + "-"
        + uuid.uuid4().hex[:8]
    )

    final_path = (
        backup_root
        / backup_id
    )

    files = []

    try:
        database_archive = (
            staging
            / "database"
            / "openasicmanager.db"
        )

        database_archive.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        backup_sqlite(
            database_path,
            database_archive,
        )

        os.chmod(
            database_archive,
            0o600,
        )

        files.append(
            _manifest_entry(
                staging,
                database_archive,
                "database",
                database_path,
                True,
            )
        )

        environment_present = (
            env_file.is_file()
        )

        if environment_present:
            environment_archive = (
                staging
                / "config"
                / "openasicmanager.env"
            )

            _copy_file(
                env_file,
                environment_archive,
                mode=0o600,
            )

            files.append(
                _manifest_entry(
                    staging,
                    environment_archive,
                    "environment",
                    env_file,
                    True,
                )
            )

        if reference_files is None:
            reference_files = (
                DEFAULT_REFERENCE_FILES
            )

        for index, source in enumerate(
            reference_files,
            start=1,
        ):
            source = Path(source)

            if not source.is_file():
                continue

            archive = (
                _reference_archive_path(
                    staging,
                    source,
                    index,
                )
            )

            _copy_file(
                source,
                archive,
                mode=0o600,
            )

            files.append(
                _manifest_entry(
                    staging,
                    archive,
                    "deployment_reference",
                    source,
                    False,
                )
            )

        manifest = {
            "format": BACKUP_FORMAT,
            "backup_id": backup_id,
            "created_at": created_at,
            "source_version": str(
                source_version
            ),
            "database_source": str(
                database_path
            ),
            "environment_source": str(
                env_file
            ),
            "environment_present": (
                environment_present
            ),
            "files": files,
        }

        manifest_path = (
            staging
            / "manifest.json"
        )

        manifest_path.write_text(
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        os.chmod(
            manifest_path,
            0o600,
        )

        verify_backup(
            staging
        )

        os.replace(
            staging,
            final_path,
        )

        os.chmod(
            final_path,
            0o700,
        )

        return final_path

    except Exception:
        shutil.rmtree(
            staging,
            ignore_errors=True,
        )
        raise


def _target_metadata(
    target,
    default_mode,
):
    target = Path(target)

    if target.exists():
        current = target.stat()

        return (
            current.st_uid,
            current.st_gid,
            stat.S_IMODE(
                current.st_mode
            ),
        )

    parent = target.parent
    parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    parent_stat = parent.stat()

    return (
        parent_stat.st_uid,
        parent_stat.st_gid,
        default_mode,
    )


def _apply_metadata(
    path,
    metadata,
):
    path = Path(path)
    uid, gid, mode = metadata

    os.chmod(
        path,
        mode,
    )

    current = path.stat()

    if (
        current.st_uid,
        current.st_gid,
    ) != (
        uid,
        gid,
    ):
        try:
            os.chown(
                path,
                uid,
                gid,
            )
        except PermissionError as exc:
            raise RuntimeError(
                "Unable to preserve ownership for "
                + str(path)
            ) from exc


def _stage_file(
    source,
    target,
    metadata,
):
    source = Path(source)
    target = Path(target)

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    handle, temporary = tempfile.mkstemp(
        prefix=(
            "."
            + target.name
            + ".restore-"
        ),
        dir=str(
            target.parent
        ),
    )

    os.close(handle)

    temporary = Path(
        temporary
    )

    try:
        shutil.copyfile(
            source,
            temporary,
        )

        _apply_metadata(
            temporary,
            metadata,
        )

        return temporary
    except Exception:
        temporary.unlink(
            missing_ok=True
        )
        raise


def _replace_file(
    source,
    destination,
):
    os.replace(
        source,
        destination,
    )


def _database_sidecars(
    database_path,
):
    database_path = Path(
        database_path
    )

    return tuple(
        Path(
            str(database_path)
            + suffix
        )
        for suffix in (
            "-wal",
            "-shm",
        )
    )


def _park_database_sidecars(
    database_path,
):
    parked = []

    try:
        for sidecar in _database_sidecars(
            database_path
        ):
            if not sidecar.exists():
                continue

            parked_path = (
                sidecar.parent
                / (
                    "."
                    + sidecar.name
                    + ".rollback-"
                    + uuid.uuid4().hex
                )
            )

            os.replace(
                sidecar,
                parked_path,
            )

            parked.append(
                (
                    sidecar,
                    parked_path,
                )
            )

        return parked

    except Exception:
        for original, parked_path in reversed(
            parked
        ):
            if parked_path.exists():
                os.replace(
                    parked_path,
                    original,
                )

        raise


def _restore_database_sidecars(
    parked,
):
    for original, parked_path in parked:
        if parked_path.exists():
            os.replace(
                parked_path,
                original,
            )


def _discard_database_sidecars(
    parked,
):
    for _, parked_path in parked:
        parked_path.unlink(
            missing_ok=True
        )


def _restore_entry(
    manifest,
    role,
):
    entries = [
        entry
        for entry in manifest[
            "files"
        ]
        if entry.get(
            "role"
        ) == role
        and entry.get(
            "restore"
        )
    ]

    if len(entries) > 1:
        raise RuntimeError(
            "Backup contains multiple "
            f"restorable {role} files"
        )

    return (
        entries[0]
        if entries
        else None
    )


def restore_backup(
    backup_dir,
    database_path=DEFAULT_DATABASE_PATH,
    env_file=DEFAULT_ENV_FILE,
):
    backup_dir = Path(
        backup_dir
    ).resolve()

    database_path = Path(
        database_path
    ).resolve()

    env_file = Path(
        env_file
    ).resolve()

    manifest = verify_backup(
        backup_dir
    )

    database_entry = _restore_entry(
        manifest,
        "database",
    )

    if database_entry is None:
        raise RuntimeError(
            "Backup database is not restorable"
        )

    environment_entry = _restore_entry(
        manifest,
        "environment",
    )

    database_archive = (
        _safe_archive_file(
            backup_dir,
            database_entry[
                "path"
            ],
        )
    )

    environment_archive = (
        _safe_archive_file(
            backup_dir,
            environment_entry[
                "path"
            ],
        )
        if environment_entry
        else None
    )

    database_metadata = (
        _target_metadata(
            database_path,
            0o600,
        )
    )

    environment_metadata = (
        _target_metadata(
            env_file,
            0o640,
        )
        if environment_archive
        else None
    )

    database_existed = (
        database_path.is_file()
    )

    environment_existed = (
        env_file.is_file()
    )

    database_rollback = None
    environment_rollback = None
    database_stage = None
    environment_stage = None

    database_replaced = False
    environment_replaced = False
    sidecar_rollbacks = []
    restore_succeeded = False

    try:
        if database_existed:
            rollback_handle, rollback_name = (
                tempfile.mkstemp(
                    prefix=(
                        "."
                        + database_path.name
                        + ".rollback-"
                    ),
                    dir=str(
                        database_path.parent
                    ),
                )
            )
            os.close(
                rollback_handle
            )
            Path(
                rollback_name
            ).unlink(
                missing_ok=True
            )

            database_rollback = Path(
                rollback_name
            )

            backup_sqlite(
                database_path,
                database_rollback,
            )

            _apply_metadata(
                database_rollback,
                database_metadata,
            )

        if (
            environment_archive
            and
            environment_existed
        ):
            rollback_handle, rollback_name = (
                tempfile.mkstemp(
                    prefix=(
                        "."
                        + env_file.name
                        + ".rollback-"
                    ),
                    dir=str(
                        env_file.parent
                    ),
                )
            )
            os.close(
                rollback_handle
            )

            environment_rollback = Path(
                rollback_name
            )

            shutil.copyfile(
                env_file,
                environment_rollback,
            )

            _apply_metadata(
                environment_rollback,
                environment_metadata,
            )

        database_stage = _stage_file(
            database_archive,
            database_path,
            database_metadata,
        )

        sqlite_quick_check(
            database_stage
        )

        if environment_archive:
            environment_stage = (
                _stage_file(
                    environment_archive,
                    env_file,
                    environment_metadata,
                )
            )

        sidecar_rollbacks = (
            _park_database_sidecars(
                database_path
            )
        )

        _replace_file(
            database_stage,
            database_path,
        )
        database_stage = None
        database_replaced = True

        if environment_stage:
            _replace_file(
                environment_stage,
                env_file,
            )
            environment_stage = None
            environment_replaced = True

        sqlite_quick_check(
            database_path
        )

        restore_succeeded = True

        return {
            "source_version": manifest.get(
                "source_version",
                "unknown",
            ),
            "database": str(
                database_path
            ),
            "environment_restored": bool(
                environment_archive
            ),
        }

    except Exception:
        if database_replaced:
            if (
                database_existed
                and
                database_rollback
                and
                database_rollback.is_file()
            ):
                _replace_file(
                    database_rollback,
                    database_path,
                )
                database_rollback = None
            elif not database_existed:
                database_path.unlink(
                    missing_ok=True
                )

        if environment_replaced:
            if (
                environment_existed
                and
                environment_rollback
                and
                environment_rollback.is_file()
            ):
                _replace_file(
                    environment_rollback,
                    env_file,
                )
                environment_rollback = None
            elif not environment_existed:
                env_file.unlink(
                    missing_ok=True
                )

        _restore_database_sidecars(
            sidecar_rollbacks
        )
        sidecar_rollbacks = []

        raise

    finally:
        if restore_succeeded:
            _discard_database_sidecars(
                sidecar_rollbacks
            )
            sidecar_rollbacks = []

        for temporary in (
            database_stage,
            environment_stage,
            database_rollback,
            environment_rollback,
        ):
            if temporary is not None:
                Path(
                    temporary
                ).unlink(
                    missing_ok=True
                )


__all__ = (
    "BACKUP_FORMAT",
    "DEFAULT_BACKUP_ROOT",
    "DEFAULT_DATABASE_PATH",
    "DEFAULT_ENV_FILE",
    "create_backup",
    "restore_backup",
    "verify_backup",
)
