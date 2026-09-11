# OpenASICManager Doctor

`openasicmanager-doctor` is a read-only operational and preflight diagnostic command.

Installed path:

```text
/opt/openasicmanager/scripts/openasicmanager-doctor
```

Run the human-readable report:

```bash
sudo /opt/openasicmanager/scripts/openasicmanager-doctor
```

Machine-readable output for automation:

```bash
sudo /opt/openasicmanager/scripts/openasicmanager-doctor --json
```

The command does not restart/reload services, mutate the database, send ASIC control requests or run scheduler actions.

## Checks

Doctor reports:

- installed and runtime application versions;
- `openasicmanager.service` state;
- firmware-detection timer state;
- local `/health` response and reported version;
- SQLite accessibility and `PRAGMA quick_check`;
- global scheduler state and configured rule count;
- inventory count and enabled-miner count;
- environment-file presence and permission mode;
- Remote Web enabled/disabled state without exposing secrets;
- generated Remote Web nginx configuration presence;
- `nginx -t` validation when nginx is installed;
- virtualenv presence and imports for required runtime dependencies;
- free space for application, database and backup paths.

## Result levels

`PASS` means the check is healthy.

`WARN` reports a condition that should be reviewed but does not make the preflight exit non-zero, such as low free space or broader-than-recommended environment-file permissions.

`FAIL` means a required preflight condition failed. Doctor exits with status `1` when any `FAIL` is present.

A warning-only report exits with status `0`, allowing upgrade tooling to distinguish a blocking failure from an advisory condition.

## Security

Doctor intentionally reads only the safe `REMOTE_WEB_ENABLED` configuration key. Passwords, ASIC credentials, Telegram tokens, Remote Web secrets and other environment values are never added to the human-readable or JSON report.

The command may expose paths, service names, counts and validation errors. Treat diagnostic output as operational metadata rather than as a secret-bearing configuration dump.

## Free-space warning threshold

The default warning threshold is 1024 MiB for each checked storage location.

Override it when required:

```bash
sudo /opt/openasicmanager/scripts/openasicmanager-doctor \
    --warn-free-mb 2048
```

## Upgrade integration

The Python diagnostic engine returns a structured report independently of CLI formatting. The 0.3.0 transactional upgrade workflow can reuse this engine for preflight and post-upgrade validation without invoking state-changing operations.
