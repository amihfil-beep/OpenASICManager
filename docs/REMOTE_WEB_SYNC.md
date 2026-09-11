# Remote Web nginx synchronization

OpenASICManager automatically reconciles the generated Remote ASIC Web nginx configuration on standard public installations.

The synchronization mechanism is deliberately separate from the unprivileged web application.

## Components

The installed units are:

```text
openasicmanager-remote-web-sync.service
openasicmanager-remote-web-sync.timer
```

The timer runs the root-owned oneshot service approximately once per minute while the main OpenASICManager service is active.

The main application service only declares a `Wants=` dependency on the timer. The web application itself is never granted permission to modify nginx configuration or reload nginx.

## Reconciliation sequence

Each synchronization run:

1. executes the existing `generate-remote-nginx --stdout` renderer;
2. compares the generated content with `/etc/nginx/sites-available/openasicmanager-remote`;
3. verifies that the `sites-enabled` entry is a symlink to that generated file;
4. exits without `nginx -t` or reload when both content and link are already correct;
5. writes changed content atomically;
6. updates the generated symlink atomically when required;
7. runs `nginx -t`;
8. reloads nginx only after successful validation.

A regular file in `sites-enabled` with the generated site name is treated as administrator-owned and is not overwritten automatically.

## Failure safety

Before making a change, the reconciler records the previous generated file contents and symlink target.

If validation or reload fails, it restores the previous file and symlink state and validates the restored nginx configuration again.

A synchronization failure makes only the oneshot service fail. It does not stop or restart the main OpenASICManager application.

## Remote Web disabled

When `REMOTE_WEB_ENABLED=false`, the existing renderer produces a disabled generated state:

```text
# OpenASICManager Remote Web is disabled.
```

The synchronizer reconciles the active generated file to that state. No Remote ASIC Web server blocks remain in the generated site.

## Inventory convergence

The renderer reads the current managed inventory each time. Therefore the generated hosts converge automatically after:

- adding a miner;
- removing a miner;
- changing a miner IP address;
- changing `REMOTE_WEB_ALLOWED_CIDR`;
- enabling or disabling Remote Web.

Only miners whose IP address is inside `REMOTE_WEB_ALLOWED_CIDR` are included.

## Manual troubleshooting

Render without changing nginx:

```bash
sudo /opt/openasicmanager/venv/bin/python \
    /opt/openasicmanager/scripts/generate-remote-nginx \
    --stdout
```

Run one reconciliation immediately:

```bash
sudo /opt/openasicmanager/venv/bin/python \
    /opt/openasicmanager/scripts/openasicmanager-remote-web-sync
```

Inspect timer state:

```bash
systemctl status openasicmanager-remote-web-sync.timer
```

Inspect the most recent reconciliation logs:

```bash
journalctl \
    -u openasicmanager-remote-web-sync.service \
    -n 50 \
    --no-pager
```

The journal output contains only concise reconciliation status or errors. The synchronizer does not print `REMOTE_WEB_SECRET`, ASIC credentials, Telegram tokens, or environment-file contents.

## Upgrade behavior

The public `openasicmanager-upgrade` command registers the two synchronization units as part of the same transactional systemd-unit deployment.

The timer is tied to the main application with `PartOf=openasicmanager.service`. If a target upgrade fails and the main service is stopped for rollback, the synchronization timer is stopped with it before previous unit files are restored.

Migration of the private legacy `1.5.2` deployment remains outside the public upgrade contract.
