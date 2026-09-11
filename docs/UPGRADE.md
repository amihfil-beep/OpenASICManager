# OpenASICManager Upgrade

OpenASICManager includes a transactional upgrade command for standard public installations.

The supported public installation layout is:

```text
/opt/openasicmanager
/etc/openasicmanager/openasicmanager.env
/var/lib/openasicmanager/openasicmanager.db
/var/backups/openasicmanager
```

Public upgrades are supported starting with installed version `0.2.0`.

Private or legacy installations, including the historical private `1.5.2` deployment, are deliberately outside this upgrade contract and require a separately validated migration procedure.

## Upgrade source

The upgrader is run from the **target release tree**, not from the active production directory.

Use an extracted OpenASICManager release/source archive containing at least:

```text
app/
scripts/
deploy/systemd/
requirements.txt
VERSION
```

Production does not need to be a Git checkout and the upgrader never runs `git pull`.

Example from an extracted target release:

```bash
sudo python3 scripts/openasicmanager-upgrade --yes
```

The source tree must not overlap `/opt/openasicmanager`.

## Safety sequence

The upgrade operation performs these phases in order:

1. Read and validate the installed and target versions.
2. Run read-only `doctor` preflight checks against the currently running version.
3. Create and verify a consistent backup of the database and environment configuration.
4. Stage the complete target application in a temporary sibling directory under `/opt`.
5. Create a fresh target virtualenv and install target dependencies **before stopping production**.
6. Validate that the staged runtime imports required dependencies and reports the same version as the target `VERSION` file.
7. Snapshot the currently installed OpenASICManager systemd unit files.
8. Stop the firmware-detection timer/service and the application service.
9. Install target systemd units and run `systemctl daemon-reload`.
10. Atomically move the previous `/opt/openasicmanager` tree aside and move the staged target tree into its place.
11. Start the target application. Application startup performs the normal schema initialization after the verified backup already exists.
12. Require `/health` to report `status=ok` and the exact target version.
13. Start the firmware-detection timer and run a full doctor postflight check.
14. Only after successful validation remove the previous application tree.

The environment file and database remain outside the application tree and are never replaced as part of a successful code deployment.

## Automatic rollback

If a failure occurs after the active application has been switched, the upgrader automatically attempts to:

1. stop the target timer/service;
2. restore the previous `/opt/openasicmanager` tree including its previous virtualenv;
3. restore the previous systemd unit files;
4. restore the verified database/environment backup;
5. run `systemctl daemon-reload`;
6. start the previous application;
7. require the previous version to pass `/health` again;
8. restart the firmware-detection timer.

This database restore is important if target startup already performed a schema migration before health verification failed.

If a failure happens after production was stopped but **before** the application-tree switch, the upgrader restores the previous systemd units and starts the existing application/timer again without replacing the database.

## Interrupted upgrades

Upgrade transaction metadata is written under the protected backup area and the previous application tree is kept as a sibling rollback directory until postflight succeeds.

A hard interruption therefore leaves recovery material outside the active application tree. The verified backup remains under:

```text
/var/backups/openasicmanager
```

Do not manually delete hidden `.upgrade-transaction-*` or `.openasicmanager-rollback-*` paths after an interrupted upgrade until the installation state has been inspected.

## Output and secrets

A successful human-readable run prints only the source version, target version, backup path and preflight/postflight status.

For machine-readable success output:

```bash
sudo python3 scripts/openasicmanager-upgrade \
    --yes \
    --json
```

The upgrader does not print the contents of `openasicmanager.env`, ASIC credentials, Telegram tokens or Remote Web secrets.

## Health timeout

The default health timeout is 45 seconds.

Override it when necessary:

```bash
sudo python3 scripts/openasicmanager-upgrade \
    --yes \
    --health-timeout 90
```

## Important operational notes

- Do not run two upgrades concurrently.
- Do not use the active `/opt/openasicmanager` directory as the target source tree.
- Do not delete the verified backup immediately after an upgrade.
- A warning-only doctor result does not block an upgrade; any doctor `FAIL` does.
- Dependency installation happens before service downtime, so a package-resolution failure leaves the running application untouched.
- The upgrader does not download releases itself and does not implement unattended Internet self-update.
