# OpenASICManager Installation

## Supported platform

The current public release is designed for Ubuntu/Debian systems with systemd.

Primary development platform:

    Ubuntu 24.04 LTS

## 1. Download OpenASICManager

Example:

    git clone https://github.com/amihfil-beep/OpenASICManager.git
    cd OpenASICManager

## 2. Install

Run:

    sudo ./install.sh

The installer creates:

    /opt/openasicmanager
    /etc/openasicmanager
    /var/lib/openasicmanager
    /var/backups/openasicmanager

and the dedicated system account:

    openasicmanager

The application listens locally on:

    127.0.0.1:8088

## 3. Configure ASIC credentials

Edit:

    sudo nano /etc/openasicmanager/openasicmanager.env

Example for Bitmain Stock firmware:

    BITMAIN_USERNAME=root
    BITMAIN_PASSWORD=CHANGE_ME

Example for Awesome / AnthillOS:

    AWESOME_USERNAME=
    AWESOME_PASSWORD=CHANGE_ME

Passwords intentionally have no default value.

Do not commit the real environment file to Git.

## 4. Restart the application

    sudo systemctl restart openasicmanager

Check service status:

    systemctl status openasicmanager

Health check:

    curl http://127.0.0.1:8088/health

Expected response contains:

    "status": "ok"

## 5. ASIC discovery

Discover supported ASICs in a private IPv4 network:

    /opt/openasicmanager/scripts/asic-discover \
        192.168.1.0/24

JSON output:

    /opt/openasicmanager/scripts/asic-discover \
        --json \
        192.168.1.0/24

Discovery accepts RFC1918 IPv4 networks only.

Supported private address ranges are:

    10.0.0.0/8
    172.16.0.0/12
    192.168.0.0/16

## 6. Automatic firmware detection

Automatic firmware detection is executed by a systemd timer.

Check timer status:

    systemctl status \
        openasicmanager-firmware-detect.timer

List upcoming timer runs:

    systemctl list-timers \
        openasicmanager-firmware-detect.timer

Run detection immediately:

    sudo systemctl start \
        openasicmanager-firmware-detect.service

View the latest detector output:

    journalctl \
        -u openasicmanager-firmware-detect.service \
        -n 100 \
        --no-pager

Firmware detection supports two per-device modes.

AUTO:

    Periodic detection may update driver, model and firmware.

MANUAL:

    Administrator values are preserved and periodic detection does not
    overwrite them.

## 7. Configure HTTPS

Before configuring public HTTPS access:

    1. Create a DNS record for the manager.
    2. Point it to the OpenASICManager server.
    3. Allow inbound TCP/80.
    4. Allow inbound TCP/443.

Then run:

    sudo ./configure-web.sh \
        --domain manager.example.com \
        --email admin@example.com \
        --user admin

The script:

    - installs nginx;
    - installs certbot;
    - creates Basic Authentication;
    - prepares ACME HTTP validation;
    - requests a Let's Encrypt certificate;
    - configures HTTPS;
    - forwards the authenticated username to OpenASICManager;
    - configures nginx reload after certificate renewal.

Open:

    https://manager.example.com

## 8. Remote ASIC Web

Remote ASIC Web is optional and disabled by default.

Read:

    docs/REMOTE-WEB.md

before enabling it.

Remote ASIC Web requires:

    - compatible DNS hierarchy;
    - TLS certificate covering remote ASIC hostnames;
    - REMOTE_WEB_SECRET;
    - REMOTE_WEB_ALLOWED_CIDR;
    - access from the OpenASICManager host to ASIC HTTP interfaces.

## 9. Scheduler migration

If another management system currently controls ASIC start/stop scheduling,
disable its automatic scheduling before enabling OpenASICManager scheduling.

Two independent schedulers controlling the same ASIC can issue conflicting
commands.

It is safe to keep another product for monitoring only.

Before enabling scheduling for the whole farm:

    - test several ASICs first;
    - test both PAUSE and RESUME;
    - verify the actual ASIC state;
    - confirm schedule rules and timezone.

## 10. Application logs

Follow application logs:

    journalctl \
        -u openasicmanager \
        -f

Recent application logs:

    journalctl \
        -u openasicmanager \
        -n 200 \
        --no-pager

Firmware detector:

    journalctl \
        -u openasicmanager-firmware-detect.service \
        -n 100 \
        --no-pager

nginx:

    journalctl \
        -u nginx \
        -n 100 \
        --no-pager

## 11. Configuration changes

After changing:

    /etc/openasicmanager/openasicmanager.env

restart OpenASICManager:

    sudo systemctl restart openasicmanager

## 12. Database

Default SQLite database:

    /var/lib/openasicmanager/openasicmanager.db

The database stores management state, scheduler data, telemetry, history,
control jobs and audit records.

## 13. Backup and restore

OpenASICManager includes a maintenance command for consistent backup sets:

    /opt/openasicmanager/scripts/openasicmanager-backup

Create an online backup:

    sudo /opt/openasicmanager/scripts/openasicmanager-backup \
        create

The application may remain running while the backup is created. The tool uses
SQLite's online backup API, so a live WAL database is copied consistently
without copying `-wal` or `-shm` files directly.

Default backup location:

    /var/backups/openasicmanager

Each backup set contains:

    - a consistent SQLite database snapshot;
    - `/etc/openasicmanager/openasicmanager.env` when present;
    - reference copies of OpenASICManager systemd/nginx configuration when
      present;
    - `manifest.json` with source version, file sizes and SHA-256 checksums.

Backup directories are created with restrictive permissions. The environment
file may contain ASIC credentials, Telegram tokens and Remote Web secrets, so
backup storage must still be protected accordingly.

Verify a backup before restore:

    sudo /opt/openasicmanager/scripts/openasicmanager-backup \
        verify \
        /var/backups/openasicmanager/openasicmanager-YYYYMMDDTHHMMSSZ-xxxxxxxx

Verification checks every recorded file checksum and runs SQLite
`PRAGMA quick_check` against the archived database.

Restore is deliberately a separate state-changing operation. Stop the
application first:

    sudo systemctl stop openasicmanager.service

Then restore a verified backup:

    sudo /opt/openasicmanager/scripts/openasicmanager-backup \
        restore \
        /var/backups/openasicmanager/openasicmanager-YYYYMMDDTHHMMSSZ-xxxxxxxx \
        --yes

By default, restore refuses to run while `openasicmanager.service` is active.
The existing database/configuration are preserved as rollback copies during the
restore operation, and a failed partial restore attempts to return the previous
state.

The database and environment file are the restorable application state.
Captured systemd/nginx files are retained as recovery references and are not
automatically written back by the restore command.

After a successful restore:

    sudo systemctl start openasicmanager.service

Then verify:

    curl http://127.0.0.1:8088/health

## 14. Uninstall

The current release does not yet include an automatic uninstall script.

To remove OpenASICManager manually, first stop and disable its services:

    sudo systemctl disable --now \
        openasicmanager.service

    sudo systemctl disable --now \
        openasicmanager-firmware-detect.timer

Then remove installed files only after backing up any data you want to keep.

Do not delete:

    /var/lib/openasicmanager
    /var/backups/openasicmanager

until you have verified that the database and backup sets are no longer
required.
