"""Read-only operational diagnostics for OpenASICManager."""

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import stat
import subprocess
from urllib import error as urlerror
from urllib import request as urlrequest

from maintenance.repository import diagnostic_state, sqlite_quick_check

DEFAULT_INSTALL_ROOT = Path("/opt/openasicmanager")
DEFAULT_DATABASE_PATH = Path("/var/lib/openasicmanager/openasicmanager.db")
DEFAULT_ENV_FILE = Path("/etc/openasicmanager/openasicmanager.env")
DEFAULT_BACKUP_ROOT = Path("/var/backups/openasicmanager")
DEFAULT_REMOTE_NGINX = Path("/etc/nginx/sites-available/openasicmanager-remote")
DEFAULT_HEALTH_URL = "http://127.0.0.1:8088/health"
DEFAULT_SERVICE = "openasicmanager.service"
DEFAULT_TIMER = "openasicmanager-firmware-detect.timer"
DEFAULT_FREE_BYTES_WARNING = 1024 * 1024 * 1024


@dataclass(frozen=True)
class DoctorConfig:
    version: str
    install_root: Path = DEFAULT_INSTALL_ROOT
    database_path: Path = DEFAULT_DATABASE_PATH
    env_file: Path = DEFAULT_ENV_FILE
    backup_root: Path = DEFAULT_BACKUP_ROOT
    remote_nginx: Path = DEFAULT_REMOTE_NGINX
    health_url: str = DEFAULT_HEALTH_URL
    service_name: str = DEFAULT_SERVICE
    timer_name: str = DEFAULT_TIMER
    free_bytes_warning: int = DEFAULT_FREE_BYTES_WARNING


def _check(code, status, message, **data):
    return {
        "code": code,
        "status": status,
        "message": message,
        "data": data,
    }


def _command(command, timeout=10):
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result, None
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)


def _systemctl_state(name):
    active, active_error = _command([
        "systemctl",
        "is-active",
        name,
    ])
    enabled, enabled_error = _command([
        "systemctl",
        "is-enabled",
        name,
    ])

    return {
        "active": active.stdout.strip() if active else "unknown",
        "active_rc": active.returncode if active else None,
        "enabled": enabled.stdout.strip() if enabled else "unknown",
        "enabled_rc": enabled.returncode if enabled else None,
        "error": active_error or enabled_error,
    }


def _health(url, timeout=5):
    try:
        with urlrequest.urlopen(url, timeout=timeout) as response:
            payload = response.read()
    except (OSError, urlerror.URLError, urlerror.HTTPError) as exc:
        raise RuntimeError(
            "health request failed: "
            + str(exc)
        ) from exc

    try:
        data = json.loads(
            payload.decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "health response is not valid JSON"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "health response must be a JSON object"
        )

    return data


def _parse_remote_web_enabled(path):
    path = Path(path)

    if not path.is_file():
        return False

    for raw in path.read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, value = line.split("=", 1)

        if key.strip() != "REMOTE_WEB_ENABLED":
            continue

        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ('"', "'")
        ):
            value = value[1:-1]

        return value.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    return False


def _disk_check(path, label, warning_bytes):
    path = Path(path)
    probe = path

    while (
        not probe.exists()
        and probe.parent != probe
    ):
        probe = probe.parent

    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return _check(
            "disk_" + label,
            "fail",
            "unable to inspect free space",
            path=str(path),
            error=str(exc),
        )

    status = (
        "warn"
        if usage.free < warning_bytes
        else "pass"
    )

    return _check(
        "disk_" + label,
        status,
        (
            "low free space"
            if status == "warn"
            else "free space is sufficient"
        ),
        path=str(path),
        probe=str(probe),
        free_bytes=usage.free,
        total_bytes=usage.total,
        warning_bytes=warning_bytes,
    )


def _venv_check(install_root):
    python = (
        Path(install_root)
        / "venv"
        / "bin"
        / "python"
    )

    if not python.is_file():
        return _check(
            "python_venv",
            "fail",
            "virtualenv Python is missing",
            path=str(python),
        )

    result, error = _command(
        [
            str(python),
            "-c",
            (
                "import fastapi, uvicorn, requests, socks; "
                "print('ok')"
            ),
        ],
        timeout=20,
    )

    if (
        result is None
        or result.returncode != 0
    ):
        return _check(
            "python_venv",
            "fail",
            "required Python imports failed",
            path=str(python),
            error=(
                error
                or (
                    result.stderr.strip()
                    if result
                    else "unknown"
                )
            ),
        )

    return _check(
        "python_venv",
        "pass",
        "virtualenv and required imports are healthy",
        path=str(python),
    )


def _nginx_check(remote_enabled, remote_nginx):
    binary = shutil.which("nginx")

    if binary is None:
        return _check(
            "nginx",
            (
                "fail"
                if remote_enabled
                else "pass"
            ),
            (
                "nginx is required because Remote Web is enabled"
                if remote_enabled
                else "nginx is not installed; optional web publishing is not required"
            ),
            remote_web_enabled=remote_enabled,
        )

    result, error = _command(
        [binary, "-t"],
        timeout=15,
    )

    if (
        result is None
        or result.returncode != 0
    ):
        return _check(
            "nginx",
            "fail",
            "nginx configuration validation failed",
            error=(
                error
                or (
                    result.stderr.strip()
                    if result
                    else "unknown"
                )
            ),
        )

    generated = Path(
        remote_nginx
    ).is_file()

    if remote_enabled and not generated:
        return _check(
            "nginx",
            "fail",
            "Remote Web is enabled but generated nginx config is missing",
            remote_web_enabled=True,
            remote_config=str(remote_nginx),
            remote_config_present=False,
        )

    return _check(
        "nginx",
        "pass",
        "nginx configuration is valid",
        remote_web_enabled=remote_enabled,
        remote_config=str(remote_nginx),
        remote_config_present=generated,
    )


def run_diagnostics(config):
    checks = []
    install_root = Path(config.install_root)
    database_path = Path(config.database_path)
    env_file = Path(config.env_file)
    backup_root = Path(config.backup_root)

    version_file = install_root / "VERSION"

    if not version_file.is_file():
        checks.append(
            _check(
                "version",
                "fail",
                "installed VERSION file is missing",
                path=str(version_file),
            )
        )
    else:
        try:
            installed_version = version_file.read_text(
                encoding="utf-8"
            ).strip()
        except OSError as exc:
            checks.append(
                _check(
                    "version",
                    "fail",
                    "unable to read installed VERSION",
                    path=str(version_file),
                    error=str(exc),
                )
            )
        else:
            status = (
                "pass"
                if installed_version == config.version
                else "warn"
            )
            checks.append(
                _check(
                    "version",
                    status,
                    (
                        "installed version matches runtime"
                        if status == "pass"
                        else "installed VERSION differs from runtime"
                    ),
                    installed=installed_version,
                    runtime=config.version,
                    path=str(version_file),
                )
            )

    for code, name, label in (
        (
            "service",
            config.service_name,
            "application service",
        ),
        (
            "firmware_timer",
            config.timer_name,
            "firmware detection timer",
        ),
    ):
        state = _systemctl_state(name)
        ok = (
            state["active_rc"] == 0
            and state["active"] == "active"
            and state["enabled_rc"] == 0
        )
        checks.append(
            _check(
                code,
                "pass" if ok else "fail",
                (
                    f"{label} is active and enabled"
                    if ok
                    else f"{label} is not active/enabled"
                ),
                name=name,
                **state,
            )
        )

    try:
        health = _health(config.health_url)
    except RuntimeError as exc:
        checks.append(
            _check(
                "health",
                "fail",
                str(exc),
                url=config.health_url,
            )
        )
    else:
        ok = (
            health.get("status") == "ok"
            and health.get("version") == config.version
        )
        checks.append(
            _check(
                "health",
                "pass" if ok else "fail",
                (
                    "health endpoint is healthy"
                    if ok
                    else "health endpoint returned unexpected status/version"
                ),
                url=config.health_url,
                status_value=health.get("status"),
                version=health.get("version"),
            )
        )

    if not database_path.is_file():
        checks.append(
            _check(
                "database",
                "fail",
                "database file is missing",
                path=str(database_path),
            )
        )
    else:
        try:
            sqlite_quick_check(database_path)
            state = diagnostic_state(database_path)
        except (OSError, RuntimeError) as exc:
            checks.append(
                _check(
                    "database",
                    "fail",
                    "database check failed",
                    path=str(database_path),
                    error=str(exc),
                )
            )
        else:
            checks.append(
                _check(
                    "database",
                    "pass",
                    "database is readable and passes SQLite quick_check",
                    path=str(database_path),
                    **state,
                )
            )

    remote_enabled = False

    if not env_file.is_file():
        checks.append(
            _check(
                "environment",
                "fail",
                "environment file is missing",
                path=str(env_file),
            )
        )
    else:
        try:
            mode = stat.S_IMODE(
                env_file.stat().st_mode
            )
            remote_enabled = (
                _parse_remote_web_enabled(
                    env_file
                )
            )
        except (OSError, UnicodeError) as exc:
            checks.append(
                _check(
                    "environment",
                    "fail",
                    "unable to inspect environment file",
                    path=str(env_file),
                    error=str(exc),
                )
            )
        else:
            unsafe = bool(mode & 0o027)
            checks.append(
                _check(
                    "environment",
                    "warn" if unsafe else "pass",
                    (
                        "environment permissions are broader than recommended"
                        if unsafe
                        else "environment file permissions are restrictive"
                    ),
                    path=str(env_file),
                    mode=oct(mode),
                    remote_web_enabled=remote_enabled,
                )
            )

    checks.append(
        _nginx_check(
            remote_enabled,
            config.remote_nginx,
        )
    )

    checks.append(
        _venv_check(install_root)
    )

    checks.extend([
        _disk_check(
            install_root,
            "application",
            config.free_bytes_warning,
        ),
        _disk_check(
            database_path.parent,
            "database",
            config.free_bytes_warning,
        ),
        _disk_check(
            backup_root,
            "backup",
            config.free_bytes_warning,
        ),
    ])

    statuses = {
        item["status"]
        for item in checks
    }

    if "fail" in statuses:
        overall = "fail"
    elif "warn" in statuses:
        overall = "warn"
    else:
        overall = "pass"

    return {
        "status": overall,
        "version": config.version,
        "paths": {
            "install_root": str(install_root),
            "database": str(database_path),
            "environment": str(env_file),
            "backup_root": str(backup_root),
            "remote_nginx": str(config.remote_nginx),
        },
        "checks": checks,
    }


def exit_code(report):
    return (
        1
        if report.get("status") == "fail"
        else 0
    )


__all__ = (
    "DoctorConfig",
    "exit_code",
    "run_diagnostics",
)
