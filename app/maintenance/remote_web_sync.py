"""Privileged Remote Web nginx reconciliation helpers."""

import os
from pathlib import Path
import subprocess
import tempfile
import uuid


DEFAULT_OUTPUT_PATH = Path(
    "/etc/nginx/sites-available/openasicmanager-remote"
)
DEFAULT_ENABLED_DIR = Path(
    "/etc/nginx/sites-enabled"
)
SYNC_SERVICE = "openasicmanager-remote-web-sync.service"
SYNC_TIMER = "openasicmanager-remote-web-sync.timer"
SYNC_UNIT_NAMES = (
    SYNC_SERVICE,
    SYNC_TIMER,
)


def _run(command):
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )


def _command_error(result):
    return (
        result.stderr.strip()
        or result.stdout.strip()
        or "command failed"
    )


def _link_target(link):
    link = Path(link)

    if not link.is_symlink():
        return None

    raw = os.readlink(link)
    target = Path(raw)

    if not target.is_absolute():
        target = link.parent / target

    return target.resolve()


def _write_atomic(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temporary_name = tempfile.mkstemp(
        prefix="." + path.name + ".sync-",
        dir=str(path.parent),
    )

    temporary = Path(temporary_name)

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)

        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(
            missing_ok=True
        )


def _replace_symlink(link, target):
    link = Path(link)
    target = Path(target)
    link.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = link.parent / (
        "."
        + link.name
        + ".sync-"
        + uuid.uuid4().hex
    )

    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        temporary.unlink(
            missing_ok=True
        )


def _restore_file(path, previous):
    path = Path(path)

    if previous is None:
        path.unlink(
            missing_ok=True
        )
        return

    _write_atomic(
        path,
        previous,
        mode=0o644,
    )


def _restore_link(link, previous_target):
    link = Path(link)

    if link.exists() or link.is_symlink():
        link.unlink()

    if previous_target is not None:
        _replace_symlink(
            link,
            previous_target,
        )


def reconcile_config(
    content,
    output_path=DEFAULT_OUTPUT_PATH,
    enabled_dir=DEFAULT_ENABLED_DIR,
    *,
    command_runner=_run,
):
    """Reconcile generated nginx content and reload only on change."""

    output_path = Path(output_path).resolve()
    enabled_dir = Path(enabled_dir).resolve()
    enabled_link = enabled_dir / output_path.name
    desired = str(content).encode("utf-8")

    if (
        enabled_link.exists()
        and not enabled_link.is_symlink()
    ):
        raise RuntimeError(
            "Refusing to replace non-symlink nginx site entry: "
            + str(enabled_link)
        )

    previous = (
        output_path.read_bytes()
        if output_path.is_file()
        else None
    )

    previous_link_target = _link_target(
        enabled_link
    )

    content_changed = (
        previous != desired
    )
    link_changed = (
        previous_link_target
        != output_path
    )

    if (
        not content_changed
        and not link_changed
    ):
        return {
            "status": "unchanged",
            "content_changed": False,
            "link_changed": False,
            "reloaded": False,
        }

    try:
        if content_changed:
            _write_atomic(
                output_path,
                desired,
                mode=0o644,
            )

        if link_changed:
            _replace_symlink(
                enabled_link,
                output_path,
            )

        check = command_runner([
            "nginx",
            "-t",
        ])

        if check.returncode != 0:
            raise RuntimeError(
                "nginx -t failed: "
                + _command_error(check)
            )

        reload_result = command_runner([
            "systemctl",
            "reload",
            "nginx",
        ])

        if reload_result.returncode != 0:
            raise RuntimeError(
                "nginx reload failed: "
                + _command_error(
                    reload_result
                )
            )

    except Exception as exc:
        rollback_errors = []

        try:
            _restore_file(
                output_path,
                previous,
            )
        except Exception as rollback_exc:
            rollback_errors.append(
                "file rollback: "
                + str(rollback_exc)
            )

        try:
            _restore_link(
                enabled_link,
                previous_link_target,
            )
        except Exception as rollback_exc:
            rollback_errors.append(
                "link rollback: "
                + str(rollback_exc)
            )

        try:
            restored_check = command_runner([
                "nginx",
                "-t",
            ])

            if restored_check.returncode != 0:
                rollback_errors.append(
                    "restored nginx config failed validation: "
                    + _command_error(
                        restored_check
                    )
                )
        except Exception as rollback_exc:
            rollback_errors.append(
                "restored nginx validation: "
                + str(rollback_exc)
            )

        message = str(exc)

        if rollback_errors:
            message += "; rollback problems: " + " | ".join(
                rollback_errors
            )

        raise RuntimeError(message) from exc

    return {
        "status": "updated",
        "content_changed": content_changed,
        "link_changed": link_changed,
        "reloaded": True,
    }


def register_upgrade_units(
    upgrade_module,
    source_root,
):
    """Register Remote Web sync units with the transactional upgrader."""

    source_root = Path(source_root).resolve()
    unit_root = source_root / "deploy/systemd"

    missing = [
        str(unit_root / name)
        for name in SYNC_UNIT_NAMES
        if not (
            unit_root / name
        ).is_file()
    ]

    if missing:
        raise RuntimeError(
            "Remote Web sync systemd units are missing: "
            + ", ".join(missing)
        )

    names = list(
        upgrade_module.UNIT_NAMES
    )

    for name in SYNC_UNIT_NAMES:
        if name not in names:
            names.append(name)

    upgrade_module.UNIT_NAMES = tuple(
        names
    )

    return upgrade_module.UNIT_NAMES


__all__ = (
    "DEFAULT_ENABLED_DIR",
    "DEFAULT_OUTPUT_PATH",
    "SYNC_SERVICE",
    "SYNC_TIMER",
    "SYNC_UNIT_NAMES",
    "reconcile_config",
    "register_upgrade_units",
)
