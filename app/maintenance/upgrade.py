"""Transactional public-release upgrades for OpenASICManager."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib import error as urlerror
from urllib import request as urlrequest
import uuid

from maintenance.backup import create_backup, restore_backup
from maintenance.doctor import DoctorConfig, run_diagnostics

MIN_SUPPORTED_VERSION = (0, 2, 0)
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
DEFAULT_INSTALL_ROOT = Path("/opt/openasicmanager")
DEFAULT_DATABASE_PATH = Path("/var/lib/openasicmanager/openasicmanager.db")
DEFAULT_ENV_FILE = Path("/etc/openasicmanager/openasicmanager.env")
DEFAULT_BACKUP_ROOT = Path("/var/backups/openasicmanager")
DEFAULT_SYSTEMD_ROOT = Path("/etc/systemd/system")
DEFAULT_HEALTH_URL = "http://127.0.0.1:8088/health"
DEFAULT_SERVICE = "openasicmanager.service"
DEFAULT_TIMER = "openasicmanager-firmware-detect.timer"
DEFAULT_FIRMWARE_SERVICE = "openasicmanager-firmware-detect.service"
UNIT_NAMES = (DEFAULT_SERVICE, DEFAULT_FIRMWARE_SERVICE, DEFAULT_TIMER)


@dataclass(frozen=True)
class UpgradeConfig:
    source_root: Path
    install_root: Path = DEFAULT_INSTALL_ROOT
    database_path: Path = DEFAULT_DATABASE_PATH
    env_file: Path = DEFAULT_ENV_FILE
    backup_root: Path = DEFAULT_BACKUP_ROOT
    systemd_root: Path = DEFAULT_SYSTEMD_ROOT
    health_url: str = DEFAULT_HEALTH_URL
    service_name: str = DEFAULT_SERVICE
    timer_name: str = DEFAULT_TIMER
    firmware_service_name: str = DEFAULT_FIRMWARE_SERVICE
    health_timeout: int = 45


def _version_tuple(value):
    match = VERSION_RE.fullmatch(str(value).strip())
    if not match:
        raise RuntimeError("Unsupported version format: " + repr(value))
    return tuple(int(part) for part in match.groups())


def _read_version(root):
    path = Path(root) / "VERSION"
    if not path.is_file():
        raise RuntimeError("VERSION file is missing: " + str(path))
    value = path.read_text(encoding="utf-8").strip()
    _version_tuple(value)
    return value


def _within(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()
    return path == root or root in path.parents


def validate_upgrade_path(current_version, target_version):
    current = _version_tuple(current_version)
    target = _version_tuple(target_version)
    if current < MIN_SUPPORTED_VERSION:
        raise RuntimeError(
            "Unsupported source version: " + current_version
            + "; public upgrades start at 0.2.0"
        )
    if target <= current:
        raise RuntimeError(
            "Target version must be newer than the installed version: "
            + current_version + " -> " + target_version
        )


def _required_source_paths(source_root):
    source_root = Path(source_root)
    return (
        source_root / "app",
        source_root / "scripts",
        source_root / "requirements.txt",
        source_root / "VERSION",
        source_root / "deploy/systemd" / DEFAULT_SERVICE,
        source_root / "deploy/systemd" / DEFAULT_FIRMWARE_SERVICE,
        source_root / "deploy/systemd" / DEFAULT_TIMER,
    )


def validate_source_tree(source_root, install_root=DEFAULT_INSTALL_ROOT):
    source_root = Path(source_root).resolve()
    install_root = Path(install_root).resolve()
    if _within(source_root, install_root) or _within(install_root, source_root):
        raise RuntimeError("Upgrade source and active installation must not overlap")
    missing = [
        str(path)
        for path in _required_source_paths(source_root)
        if not path.exists()
    ]
    if missing:
        raise RuntimeError("Upgrade source tree is incomplete: " + ", ".join(missing))
    return _read_version(source_root)


def _run(command, *, timeout=None, check=True, cwd=None):
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RuntimeError(
            "Command failed: "
            + " ".join(str(part) for part in command)
            + ": " + detail
        )
    return result


def _normalize_tree_permissions(root):
    root = Path(root)
    os.chmod(root, 0o755)
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("Upgrade source contains unsupported symlink: " + str(path))
        if path.is_dir():
            os.chmod(path, 0o755)
        elif path.is_file():
            os.chmod(path, 0o644)
    scripts = root / "scripts"
    if scripts.is_dir():
        for path in scripts.iterdir():
            if path.is_file():
                os.chmod(path, 0o755)


def _copy_release_payload(source_root, staging_root):
    source_root = Path(source_root)
    staging_root = Path(staging_root)
    shutil.copytree(source_root / "app", staging_root / "app")
    shutil.copytree(source_root / "scripts", staging_root / "scripts")
    shutil.copy2(source_root / "requirements.txt", staging_root / "requirements.txt")
    shutil.copy2(source_root / "VERSION", staging_root / "VERSION")
    _normalize_tree_permissions(staging_root)


def _prepare_venv(staging_root, *, command_runner=_run):
    staging_root = Path(staging_root)
    python = staging_root / "venv/bin/python"
    command_runner(["python3", "-m", "venv", str(staging_root / "venv")])
    command_runner([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    command_runner([
        str(python), "-m", "pip", "install", "-r",
        str(staging_root / "requirements.txt"),
    ])
    return python


def _validate_staged_runtime(staging_root, expected_version, *, command_runner=_run):
    staging_root = Path(staging_root)
    python = staging_root / "venv/bin/python"
    script = (
        "import sys;"
        f"sys.path.insert(0,{str(staging_root / 'app')!r});"
        "import fastapi,uvicorn,requests,socks;"
        "from app_version import APP_VERSION;"
        "print(APP_VERSION)"
    )
    result = command_runner([str(python), "-c", script], timeout=30)
    runtime_version = result.stdout.strip()
    if runtime_version != expected_version:
        raise RuntimeError(
            "Staged runtime version mismatch: "
            + runtime_version + " != " + expected_version
        )


def _doctor_config(config, version):
    return DoctorConfig(
        version=version,
        install_root=Path(config.install_root),
        database_path=Path(config.database_path),
        env_file=Path(config.env_file),
        backup_root=Path(config.backup_root),
        health_url=config.health_url,
        service_name=config.service_name,
        timer_name=config.timer_name,
    )


def _require_preflight(config, version, *, doctor_runner=run_diagnostics):
    report = doctor_runner(_doctor_config(config, version))
    if report.get("status") == "fail":
        failed = [
            item.get("code", "unknown")
            for item in report.get("checks", [])
            if item.get("status") == "fail"
        ]
        raise RuntimeError("Upgrade preflight failed: " + ", ".join(failed))
    return report


def _health_payload(url, *, timeout=5):
    try:
        with urlrequest.urlopen(url, timeout=timeout) as response:
            raw = response.read()
    except (OSError, urlerror.URLError, urlerror.HTTPError) as exc:
        raise RuntimeError("health request failed: " + str(exc)) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("health response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("health response must be a JSON object")
    return payload


def _wait_for_health(url, version, timeout, *, health_reader=_health_payload, sleep=time.sleep):
    deadline = time.monotonic() + timeout
    last_error = "not checked"
    while time.monotonic() < deadline:
        try:
            payload = health_reader(url)
        except RuntimeError as exc:
            last_error = str(exc)
        else:
            if payload.get("status") == "ok" and payload.get("version") == version:
                return payload
            last_error = "unexpected health payload: " + repr(payload)
        sleep(1)
    raise RuntimeError("Timed out waiting for healthy " + version + ": " + last_error)


def _transaction_dir(backup_root):
    root = Path(backup_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / (".upgrade-transaction-" + uuid.uuid4().hex)
    path.mkdir(mode=0o700)
    return path


def _write_state(transaction, **state):
    path = Path(transaction) / "state.json"
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _snapshot_units(config, transaction):
    snapshot_root = Path(transaction) / "systemd"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    state = {}
    for name in UNIT_NAMES:
        source = Path(config.systemd_root) / name
        destination = snapshot_root / name
        present = source.is_file()
        state[name] = {"present": present}
        if present:
            shutil.copy2(source, destination)
            os.chmod(destination, 0o600)
    state_path = snapshot_root / "manifest.json"
    state_path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(state_path, 0o600)


def _restore_units(config, transaction):
    snapshot_root = Path(transaction) / "systemd"
    state = json.loads(
        (snapshot_root / "manifest.json").read_text(encoding="utf-8")
    )
    for name in UNIT_NAMES:
        target = Path(config.systemd_root) / name
        item = state.get(name, {})
        if item.get("present"):
            temporary = target.with_name(
                "." + target.name + ".rollback-" + uuid.uuid4().hex
            )
            shutil.copy2(snapshot_root / name, temporary)
            os.chmod(temporary, 0o644)
            os.replace(temporary, target)
        else:
            target.unlink(missing_ok=True)


def _install_target_units(config, source_root):
    source_root = Path(source_root)
    systemd_root = Path(config.systemd_root)
    systemd_root.mkdir(parents=True, exist_ok=True)
    for name in UNIT_NAMES:
        source = source_root / "deploy/systemd" / name
        target = systemd_root / name
        temporary = target.with_name(
            "." + target.name + ".upgrade-" + uuid.uuid4().hex
        )
        shutil.copy2(source, temporary)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)


def _systemctl(command_runner, *args):
    return command_runner(["systemctl", *args])


def _rollback(
    *, config, backup_dir, rollback_root, transaction, current_version,
    command_runner, backup_restorer, health_reader, health_waiter, sleep,
):
    errors = []
    for unit in (config.timer_name, config.firmware_service_name, config.service_name):
        try:
            _systemctl(command_runner, "stop", unit)
        except Exception as exc:
            errors.append("stop " + unit + ": " + str(exc))

    install_root = Path(config.install_root)
    failed_root = install_root.parent / (
        ".openasicmanager-failed-" + uuid.uuid4().hex
    )

    try:
        if install_root.exists():
            os.replace(install_root, failed_root)
        os.replace(rollback_root, install_root)
    except Exception as exc:
        errors.append("restore application tree: " + str(exc))

    try:
        _restore_units(config, transaction)
        _systemctl(command_runner, "daemon-reload")
    except Exception as exc:
        errors.append("restore systemd units: " + str(exc))

    try:
        backup_restorer(
            backup_dir,
            database_path=config.database_path,
            env_file=config.env_file,
        )
    except Exception as exc:
        errors.append("restore backup: " + str(exc))

    try:
        _systemctl(command_runner, "start", config.service_name)
        health_waiter(
            config.health_url,
            current_version,
            config.health_timeout,
            health_reader=health_reader,
            sleep=sleep,
        )
    except Exception as exc:
        errors.append("restart previous service: " + str(exc))

    try:
        _systemctl(command_runner, "start", config.timer_name)
    except Exception as exc:
        errors.append("restart timer: " + str(exc))

    if failed_root.exists():
        shutil.rmtree(failed_root, ignore_errors=True)

    if errors:
        raise RuntimeError("Automatic rollback was incomplete: " + " | ".join(errors))


def _recover_before_switch(
    *, config, transaction, current_version, command_runner,
    health_reader, health_waiter, sleep,
):
    errors = []
    try:
        _restore_units(config, transaction)
        _systemctl(command_runner, "daemon-reload")
    except Exception as exc:
        errors.append("restore systemd units: " + str(exc))
    try:
        _systemctl(command_runner, "start", config.service_name)
        health_waiter(
            config.health_url,
            current_version,
            config.health_timeout,
            health_reader=health_reader,
            sleep=sleep,
        )
    except Exception as exc:
        errors.append("restart previous service: " + str(exc))
    try:
        _systemctl(command_runner, "start", config.timer_name)
    except Exception as exc:
        errors.append("restart timer: " + str(exc))
    if errors:
        raise RuntimeError(
            "Pre-switch recovery was incomplete: " + " | ".join(errors)
        )


def perform_upgrade(
    config,
    *,
    doctor_runner=run_diagnostics,
    backup_creator=create_backup,
    backup_restorer=restore_backup,
    command_runner=_run,
    health_reader=_health_payload,
    health_waiter=_wait_for_health,
    sleep=time.sleep,
):
    source_root = Path(config.source_root).resolve()
    install_root = Path(config.install_root).resolve()
    current_version = _read_version(install_root)
    target_version = validate_source_tree(source_root, install_root)
    validate_upgrade_path(current_version, target_version)

    preflight = _require_preflight(
        config,
        current_version,
        doctor_runner=doctor_runner,
    )

    backup_dir = backup_creator(
        database_path=config.database_path,
        env_file=config.env_file,
        backup_root=config.backup_root,
        source_version=current_version,
        install_root=install_root,
    )

    transaction = _transaction_dir(config.backup_root)
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=".openasicmanager-stage-",
            dir=str(install_root.parent),
        )
    )
    rollback_root = install_root.parent / (
        ".openasicmanager-rollback-" + uuid.uuid4().hex
    )

    deployment_started = False
    switched = False
    success = False

    _write_state(
        transaction,
        phase="staging",
        current_version=current_version,
        target_version=target_version,
        backup_dir=str(backup_dir),
        staging_root=str(staging_root),
        rollback_root=str(rollback_root),
    )

    try:
        _copy_release_payload(source_root, staging_root)
        _prepare_venv(staging_root, command_runner=command_runner)
        _validate_staged_runtime(
            staging_root,
            target_version,
            command_runner=command_runner,
        )
        _snapshot_units(config, transaction)

        _write_state(
            transaction,
            phase="switching",
            current_version=current_version,
            target_version=target_version,
            backup_dir=str(backup_dir),
            staging_root=str(staging_root),
            rollback_root=str(rollback_root),
        )

        deployment_started = True
        _systemctl(command_runner, "stop", config.timer_name)
        _systemctl(command_runner, "stop", config.firmware_service_name)
        _systemctl(command_runner, "stop", config.service_name)

        _install_target_units(config, source_root)
        _systemctl(command_runner, "daemon-reload")

        os.replace(install_root, rollback_root)
        try:
            os.replace(staging_root, install_root)
        except Exception:
            os.replace(rollback_root, install_root)
            raise

        switched = True

        _write_state(
            transaction,
            phase="validating",
            current_version=current_version,
            target_version=target_version,
            backup_dir=str(backup_dir),
            rollback_root=str(rollback_root),
        )

        _systemctl(command_runner, "start", config.service_name)
        health = health_waiter(
            config.health_url,
            target_version,
            config.health_timeout,
            health_reader=health_reader,
            sleep=sleep,
        )
        _systemctl(command_runner, "start", config.timer_name)

        postflight = _require_preflight(
            config,
            target_version,
            doctor_runner=doctor_runner,
        )

        success = True
        return {
            "status": "success",
            "from_version": current_version,
            "to_version": target_version,
            "backup_dir": str(backup_dir),
            "health": health,
            "preflight_status": preflight.get("status"),
            "postflight_status": postflight.get("status"),
        }

    except Exception as upgrade_error:
        if switched:
            try:
                _write_state(
                    transaction,
                    phase="rollback",
                    current_version=current_version,
                    target_version=target_version,
                    backup_dir=str(backup_dir),
                    rollback_root=str(rollback_root),
                    error=str(upgrade_error),
                )
                _rollback(
                    config=config,
                    backup_dir=backup_dir,
                    rollback_root=rollback_root,
                    transaction=transaction,
                    current_version=current_version,
                    command_runner=command_runner,
                    backup_restorer=backup_restorer,
                    health_reader=health_reader,
                    health_waiter=health_waiter,
                    sleep=sleep,
                )
            except Exception as rollback_error:
                raise RuntimeError(
                    "Upgrade failed: " + str(upgrade_error)
                    + "; " + str(rollback_error)
                    + "; recovery metadata retained at " + str(transaction)
                ) from rollback_error
            raise RuntimeError(
                "Upgrade failed and was rolled back: " + str(upgrade_error)
            ) from upgrade_error

        if deployment_started:
            try:
                _recover_before_switch(
                    config=config,
                    transaction=transaction,
                    current_version=current_version,
                    command_runner=command_runner,
                    health_reader=health_reader,
                    health_waiter=health_waiter,
                    sleep=sleep,
                )
            except Exception as recovery_error:
                raise RuntimeError(
                    "Upgrade failed before application switch: "
                    + str(upgrade_error)
                    + "; " + str(recovery_error)
                    + "; recovery metadata retained at " + str(transaction)
                ) from recovery_error
            raise RuntimeError(
                "Upgrade failed before application switch and previous service was restored: "
                + str(upgrade_error)
            ) from upgrade_error

        raise

    finally:
        if success:
            if rollback_root.exists():
                shutil.rmtree(rollback_root, ignore_errors=True)
            shutil.rmtree(transaction, ignore_errors=True)
        else:
            if not switched and staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)
            if not switched and rollback_root.exists():
                shutil.rmtree(rollback_root, ignore_errors=True)


__all__ = (
    "MIN_SUPPORTED_VERSION",
    "UpgradeConfig",
    "perform_upgrade",
    "validate_source_tree",
    "validate_upgrade_path",
)
