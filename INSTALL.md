# OpenASICManager Installation

## Supported platform

The initial public release is designed for Ubuntu/Debian systems with systemd.

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

## 13. Backup

At minimum, back up:

    /var/lib/openasicmanager/openasicmanager.db
    /etc/openasicmanager/openasicmanager.env

The environment file may contain credentials, Telegram tokens and Remote Web
secrets. Store its backup securely.

For a simple offline database backup:

    sudo systemctl stop openasicmanager

    sudo cp \
        /var/lib/openasicmanager/openasicmanager.db \
        /safe/backup/location/openasicmanager.db

    sudo systemctl start openasicmanager

SQLite's online backup API may also be used when service downtime is not
acceptable.

## 14. Uninstall

The initial 0.1.0 release does not yet include an automatic uninstall script.

To remove OpenASICManager manually, first stop and disable its services:

    sudo systemctl disable --now \
        openasicmanager.service

    sudo systemctl disable --now \
        openasicmanager-firmware-detect.timer

Then remove installed files only after backing up any data you want to keep.

Do not delete:

    /var/lib/openasicmanager

until you have verified that the database is no longer required.
