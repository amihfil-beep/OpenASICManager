# OpenASICManager 0.9.1 - monitoring reliability hotfix

## Why this release is needed

On 2026-11-17 the production OpenASICManager 0.9.0 process exhausted
its soft limit of 1024 open file descriptors. The service logged
Errno 24 (Too many open files), then intermittent SQLite
"unable to open database file" and "attempt to write a readonly
database". The asic-poller and asic-anomaly threads terminated while
the main web process and scheduler stayed alive. Scheduler decisions
then skipped devices with stale monitoring timestamps.

A controlled restart on 2026-11-18 restored fresh status for all
30 existing T21 miners. The exact source of descriptor exhaustion
is **not yet established**; 45 seconds of post-restart sampling showed
7 stable descriptors. Increasing the limit does not prove the
underlying source of pressure has been eliminated.

## Changes

- Monitor polling continues after failed database reads and worker
  failures, retrying the next polling interval instead of permanently
  losing the worker thread.
- Monitoring heartbeat and last error are visible under /health.
  A failed or stale monitoring cycle sets the overall health to
  degraded even if scheduler and recurring maintenance are healthy.
- The /health endpoint reports current open file descriptors and
  soft/hard RLIMIT_NOFILE, and degrades on critical FD pressure.
- Critical SQLite paths close connections when operations fail:
  connection initialization, core read functions, monitoring status
  updates, and telemetry snapshot writes.
- Anomaly scanning and telemetry history workers no longer terminate
  if both a database operation and the audit-log write fail.
- Versioned systemd unit specifies LimitNOFILE=65535:65535.
- Added regression tests for failures/recovery, health, SQLite
  cleanup, and systemd resource limit.

## Behavior deliberately unchanged

- No changes to ANTMINER drivers, CGMiner endpoints, PAUSE, RESUME,
  REBOOT, scheduler rules, manual-override semantics, or firmware.
- No database schema migration.
- No changes to miner inventories or existing task history.
- S21 and S21 Pro support remains in the separate 0.10.0 roadmap.

## Production release gate

1. Validate CI/unit tests and release version consistency.
2. Confirm currently active control jobs = 0 and existing
   schedule/override state before the planned restart.
3. Create and verify a full application backup through
   scripts/openasicmanager-backup.
4. Deploy the exact immutable v0.9.1 tag using the transactional
   scripts/openasicmanager-upgrade command with postflight validation.
5. Verify /health returns 0.9.1, status ok, monitoring healthy,
   file_descriptors.soft_limit = 65535, and fresh telemetry for
   the expected 30 T21 devices.
6. Continue monitoring FD consumption and scheduled control results
   over several days. A healthy snapshot immediately after a restart
   does not rule out a slow descriptor leak.

Do not manually replace files in /opt/openasicmanager or bypass the
verified upgrade and rollback process.
