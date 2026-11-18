# OpenASICManager 0.10.0 — independent connectivity watchdog

**Status: development only; do not deploy to the live 0.9.1 service
until operator approval, backups, full regression testing and a release.**

## One shared Bitmain stock API family

The existing `bitmain_stock` driver and CGI/CGMiner parsing code are
shared for air-cooled ANTMINER T21, S21 and S21 Pro; there are no separate
T21/S21 branches or duplicated control implementations. Synthetic
regression tests exercise this shared software. The 2026-11-18 physical
PAUSED and MINING readings were obtained on an actual stock T21, so
firmware-specific behavior and control transitions remain to be checked
on actual S21 and S21 Pro equipment before claiming hardware validation.
This is a test-evidence distinction, **not** a code split.

## What the new diagnostics detect

The former `/health.monitoring.last_polled_miners=30` indicated an
executed polling cycle, even when all 30 miners were OFFLINE.

The new `/health.fleet` snapshot reads SQLite with `mode=ro` and counts
enabled miners with configured drivers:

- `total`, `fresh`, `offline`, `stale`, `unavailable`,
  `paused`, `mining`.
- Fresh `PAUSED` miners are **healthy**; zero hashrate is irrelevant.
- Freshness threshold is 120 seconds, not the hashrate or schedule.
- Mass outage means at least five eligible miners and at least
  80 percent unavailable (24 of 30 in the current farm).
- When this threshold is crossed, top-level `/health.status` becomes
  `degraded` even if the monitoring loop itself is `ok`.
- A database read error returns a safe `fleet.status=unavailable`
  without logging database paths or credentials in the API response.

An independent systemd-timer watcher also evaluates:

1. `openconnect-asic.service` active state;
2. `ip -4 route get 192.168.1.81` must select `ocasic0`;
3. local OpenASICManager `/health` HTTP availability;
4. read-only SQLite fleet freshness / availability.

These checks do not open connections to ASICs or send control commands.
The watchdog is an independent process: if the web process stops, the
systemd timer still executes.

## Telegram state machine

- **Disabled by default** (`OAM_WATCHDOG_ENABLED=0`).
- Outage grace: 90 seconds; avoids alerts on brief VPN renegotiations.
- Recovery grace: 60 seconds; avoids false recovery on flapping.
- One alert after sustained failure, one recovery after sustained health.
- Persistent, mode-0600 JSON state at
  `/var/lib/openasicmanager/connectivity-watchdog.json` survives
  timer runs / process restarts.
- Failed Telegram delivery is **not** acknowledged: it is retried at
  the next invocation while the problem persists.
- Existing `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, and `TELEGRAM_PROXY` are reused; no second
  credential file is created.
- Once a fleet alert was **successfully delivered**, the main anomaly
  scanner suppresses *new* individual OFFLINE issues during a mass
  outage (not temperature or schedule alerts). If the watchdog is off,
  the state is missing, or delivery fails, pre-existing per-miner
  anomaly behavior remains active.

**Limitations:** Delivery is best-effort/at-least-once: a lost Telegram
acknowledgement could produce a repeat. The watchdog is on the same
machine and depends on the host, local database and a working outbound
Telegram path. Separate external monitoring is needed to cover a full
host or network outage. It does not bypass pinned OpenConnect TLS
validation or auto-trust a changed Keenetic server certificate. It
does not determine miner control state or initiate PAUSE/RESUME.

## Configuration for the verified OpenASICManager host

The intended values for this farm, in the *existing* secured
`/etc/openasicmanager/openasicmanager.env`, are:

```dotenv
OAM_WATCHDOG_ENABLED=1
OAM_WATCHDOG_VPN_SERVICE=openconnect-asic.service
OAM_WATCHDOG_VPN_INTERFACE=ocasic0
OAM_WATCHDOG_ASIC_TARGET=192.168.1.81
```

This is **future deployment configuration only**. The code has a
disabled default; existing installations do not begin sending alerts
by merely pulling a Git branch.

The timer/service source files are:

```
deploy/systemd/openasicmanager-connectivity-watchdog.service
deploy/systemd/openasicmanager-connectivity-watchdog.timer
```

Manual operator-controlled release deployment will install these
units, reload systemd, restart the application after backups, and only
then enable the watchdog timer. Do not install systemd units directly
from a development branch into the production host.

## Development checks (safe, no changes)

```bash
cd /root/projects/openasicmanager
PYTHONPATH=app /opt/openasicmanager/venv/bin/python -m unittest discover -s tests -p 'test_connectivity_watchdog.py' -v
PYTHONPATH=app /opt/openasicmanager/venv/bin/python -m unittest discover -s tests -p 'test_system_routes.py' -v
/opt/openasicmanager/venv/bin/python scripts/openasicmanager-connectivity-watchdog --dry-run
curl -fsS http://127.0.0.1:8088/health
```

The `--dry-run` probe reads only: it neither writes alert state nor
sends Telegram messages, including when the VPN is down. Only the
future deployed `/health` endpoint includes `fleet` counters.
The already running production 0.9.1 endpoint remains unchanged.

## Acceptance before deployment

1. All current CI and unit tests pass, including PAUSED, single ASIC
   OFFLINE, mass OFFLINE, VPN service down, wrong route, app down,
   short flap, failed send retry, and recovery.
2. Read-only dry run on the real host reports VPN service and route OK,
   app reachable, fleet 30/30 fresh (or the actual current count).
3. Verify that a synthetic offline scenario in tests emits exactly
   one Telegram alert and one recovery; **do not disconnect the live
   VPN or stop miners** to test it.
4. Review actual Telegram destination/settings with operator and
   confirm that per-device offline notifications are not accidentally
   suppressed when aggregate delivery fails.
5. Complete a validated backup, staged install, postflight and
   rollback procedure before enabling the timer on production.
