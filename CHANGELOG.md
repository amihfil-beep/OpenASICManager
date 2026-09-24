# Changelog

All notable changes to OpenASICManager will be documented in this file.

## [Unreleased]

## [0.5.0] - 2026-09-24

Incident-workflow and fleet-policy release focused on giving operators explicit control over issue acknowledgement, planned maintenance, miner-specific anomaly policy exceptions and Telegram incident delivery while preserving separation between detection, history, notifications and ASIC control.

### Added

- Operator acknowledgement workflow for anomaly issues, including acknowledgement actor, timestamp and optional bounded operator note.
- Dashboard and API support for acknowledging and clearing acknowledgement without changing automatic anomaly open/resolve behavior.
- Time-bounded maintenance windows with farm-wide or per-miner scope.
- Maintenance scheduling, extension and explicit early termination from the dashboard/API.
- Planned-maintenance suppression for new covered anomaly incidents while telemetry and audit history continue normally.
- Per-miner anomaly policy overrides with global inheritance as the default.
- Per-field GLOBAL / OVERRIDE source visibility and effective anomaly policy in the API/dashboard.
- Explicit set, update, clear and inherit-all workflows for per-miner anomaly policy.
- Persistent Telegram incident-delivery policy with independent controls for new issues, recovery/clear, acknowledgement, control failures and maintenance events.
- Telegram Incident Policy dashboard controls and secret-safe read/update API.
- Audit coverage for acknowledgement, maintenance, anomaly-policy override and notification-policy changes.
- Regression coverage for issue lifecycle, maintenance expiry/scope, policy inheritance, notification routing and delivery/history separation.

### Changed

- Anomaly issues now distinguish automatic condition state from operator acknowledgement state.
- Maintenance is evaluated independently from scheduler/control state and never acts as an ASIC control command.
- Still-active anomaly conditions become eligible for normal handling again after maintenance expires.
- Selected miners may use explicit anomaly grace/threshold overrides while all unset fields continue following the global policy.
- Global anomaly scan interval remains global-only because it controls the shared scan loop.
- Global anomaly-policy updates are rejected when they would make an existing valid per-miner effective policy invalid.
- Telegram incident routing is now policy-controlled after audit persistence instead of being tied only to a fixed event list.
- Farm-summary scheduling and delivery remain independent from incident notification policy.

### Safety and compatibility

- Acknowledgement means “seen by an operator”; it does not mark an underlying anomaly as fixed.
- Acknowledgement, maintenance and anomaly-policy configuration never automatically PAUSE, RESUME or REBOOT an ASIC.
- Maintenance windows are explicitly bounded and do not stop telemetry collection or erase issue/audit history.
- Existing miners without per-miner overrides retain the 0.4.0 global anomaly behavior.
- Per-miner overrides affect anomaly evaluation only and do not directly control ASICs.
- Existing Telegram incident behavior remains compatible by default: issue-open, issue-resolved and control-failure delivery remain enabled when Telegram itself is enabled.
- Newly introduced acknowledgement and maintenance Telegram event classes are opt-in by default.
- Telegram remains globally disabled by default on fresh installations.
- Disabling a Telegram delivery class does not prevent issue persistence or audit logging.
- Telegram policy read models and audit events do not expose bot tokens, chat IDs or proxy secrets.
- Existing farm-summary configuration remains independent and unchanged.
- Public transactional upgrades remain supported from OpenASICManager 0.2.0 and newer public releases.
- Historical private/legacy 1.5.2 deployments remain outside the generic public upgrade contract.

## [0.4.0] - 2026-09-17

Farm-observability and policy release focused on making a small real-world ASIC farm easier to observe, diagnose and configure from the dashboard while preserving the operational-safety foundation introduced in 0.3.0.

### Added

- Configurable global anomaly policy for scan interval, offline grace, overheat trigger/clear thresholds, overheat grace and scheduler-mismatch grace.
- Anomaly-policy API and dashboard editor with server-side validation and persistent settings.
- Bounded telemetry-history query model with server-side downsampling for 24-hour, 7-day, 30-day and 90-day ranges.
- Range, bucket, expected-point and missing-point metadata for historical telemetry.
- Richer miner and farm history charts that consume the bounded server-side history model.
- Persistent saved discovery-network profiles with independent enabled/disabled state.
- Multi-network ASIC discovery for one selected saved network or all enabled saved networks.
- Per-network discovery status, duration and result counts.
- Source-network metadata and deterministic IP deduplication for aggregated discovery results.
- Dashboard management for saved discovery networks.

### Changed

- Miner and farm history charts now preserve measured zero readings and break lines across missing telemetry instead of presenting missing samples as real zero values.
- History loading, empty-data and API-error states clear stale chart data and expose missing-bucket information with explicit local timestamps.
- Anomaly scanning reads the current persisted policy dynamically, so policy changes do not require an application restart.
- Discovery can now cover multiple saved RFC1918 networks while retaining the existing manual single-network scan flow.

### Safety and compatibility

- Default anomaly thresholds preserve the previous 0.3.0 behavior until an operator explicitly changes them.
- Anomaly policy changes affect detection only and do not automatically control ASICs.
- Historical APIs remain bounded for long time ranges and do not fabricate or interpolate missing telemetry measurements.
- Discovery remains restricted to RFC1918 IPv4 networks and preserves the existing per-network host-count limit.
- Saved discovery profiles reject invalid, public, oversized and overlapping CIDRs.
- Multi-network discovery is bounded to a maximum of eight selected profiles and scans networks sequentially.
- Discovery remains read-only until an operator explicitly imports a discovered ASIC.
- Existing single-network discovery remains available.
- Scheduler, control, maintenance, backup, doctor, transactional-upgrade and Remote Web behavior from 0.3.0 remain part of the supported public application.

## [0.3.0] - 2026-09-11

Operational-safety release focused on making public OpenASICManager installations easier to back up, diagnose, upgrade and maintain without weakening the application's privilege boundaries.

### Added

- Safe backup and restore tooling for the standard public installation layout.
- Consistent online SQLite backups using the SQLite backup API instead of copying a live database file directly.
- Backup manifests with SHA-256 checksums, file sizes and source-version metadata.
- Backup verification including checksum validation and SQLite `PRAGMA quick_check`.
- Restore safeguards including explicit confirmation, service-state checks, ownership/mode preservation and rollback on partial restore failure.
- Read-only `openasicmanager-doctor` diagnostics for release metadata, systemd state, `/health`, SQLite integrity, inventory/scheduler summary, environment-file permissions, Remote Web/nginx state, virtualenv dependencies and free disk space.
- Human-readable and JSON doctor output suitable for operator use and automated preflight checks.
- Transactional public-release upgrade tooling for supported installations starting at version 0.2.0.
- Upgrade preflight, verified backup, target staging, fresh virtualenv preparation and dependency validation before production downtime.
- Atomic application-tree switching with exact target-version health verification and doctor postflight validation.
- Automatic rollback of the previous application tree, virtualenv, systemd units and verified database/environment backup when a target upgrade fails after switch-over.
- Recovery handling for failures after service shutdown but before the application-tree switch.
- Automatic Remote ASIC Web nginx reconciliation through a dedicated root-owned systemd oneshot service and timer.
- Idempotent Remote Web reconciliation: unchanged generated configuration causes no nginx validation or reload.
- Automatic rollback of generated nginx content and the managed `sites-enabled` symlink when `nginx -t` or nginx reload fails.
- Regression tests covering maintenance tooling, rollback paths, Remote Web inventory convergence and systemd wiring.

### Changed

- Fresh installs now install the backup, doctor, upgrade and Remote Web synchronization maintenance commands with executable permissions.
- Fresh installs include the Remote Web synchronization service/timer while keeping the main OpenASICManager application process unprivileged.
- The public upgrader installs and rolls back newly introduced maintenance systemd units transactionally.
- Remote Web configuration can now converge automatically after miner additions, removals and IP changes instead of requiring an administrator to rerun the nginx generator manually.
- Remote Web synchronization is skipped cleanly on systems where nginx is not installed.

### Safety and compatibility

- Production upgrades do not depend on a Git working tree and never require `git pull` in the active installation.
- Configuration and the SQLite database remain outside the application tree during successful upgrades.
- No secret values are intentionally emitted in backup manifests, doctor output or upgrade reports.
- The unprivileged web application is not granted nginx or root privileges; nginx synchronization runs in a separate privileged oneshot service.
- nginx is reloaded only after a real configuration change and a successful `nginx -t` validation.
- Public transactional upgrades are supported from 0.2.0 and newer public releases; migration from historical private/legacy 1.5.2 deployments remains intentionally outside the generic public upgrade contract.
- Existing ASIC-control behavior, scheduler semantics, telemetry, alerts, Telegram behavior and fresh-install conservative defaults are intended to remain compatible with 0.2.0.

## [0.2.0] - 2026-09-10

Architecture-focused release that decomposes the original FastAPI monolith without intentionally changing ASIC-control behavior.

### Changed

- `app/app.py` is now the application composition root: runtime wiring, lifecycle, middleware, router registration and dashboard entry point.
- HTTP endpoints are split into dedicated routers under `app/api/`.
- Firmware-specific Bitmain Stock and Awesome / AnthillOS behavior is isolated in `app/drivers/`.
- Control, scheduler, telemetry, anomaly, monitoring, inventory, notification and audit logic is split into explicit policy, service, analytics and repository layers where appropriate.
- Direct database access is restricted to `app/db.py` and repository modules.
- The dashboard HTML template is stored separately under `app/ui/` instead of being embedded in the Python application module.
- Runtime version reporting now uses shared application version metadata and is checked against the repository `VERSION` file.

### Added

- Focused regression tests for the extracted subsystems and API routers.
- Permanent architecture-boundary tests that prevent direct database access outside persistence modules and prevent API routers from importing the application composition root.
- Dedicated repository helpers for notification context, scheduler persistence, control jobs, inventory state, telemetry, anomalies and audit records.

### Fixed

- Restored the telemetry history-statistics HTTP route to its intended handler during the route extraction pass.
- Removed stale imports, empty monolith section markers and obsolete application-root implementation code left behind by subsystem extraction.

### Compatibility

- Existing public API behavior, scheduler semantics, verified control operations, anomaly thresholds, Telegram behavior and Remote ASIC Web behavior are intended to remain compatible with 0.1.2.
- Fresh installations still start with no configured miners, no scheduler rules, the global scheduler disabled, Telegram disabled and Remote ASIC Web disabled.
- No production deployment migration is performed by this release itself.

## [0.1.2] - 2026-09-03

Public release packaging fixes.

### Fixed

- Remote Web nginx generator is installed with executable permissions.
- Quick Start now includes the complete Git clone command.
- Removed outdated 0.1.0 wording from uninstall documentation.

## [0.1.1] - 2026-09-03

First public release candidate after fresh-install review.

### Fixed

- New installations no longer receive farm-specific scheduler rules.
- Global scheduler remains disabled on a clean installation.
- Fixed first-run nginx Basic Auth password-file creation.
- Replaced placeholder Git clone command with the real repository URL.
- Fixed Remote Web cookie-domain example.
- Removed hardcoded Europe/Moscow and MSK scheduler labels from the public application.
- Scheduler UI and API now use the configured application timezone.

## [0.1.0] - 2026-09-03

Initial public release.

### Added

- FastAPI web management interface.
- RFC1918 IPv4 ASIC discovery.
- Bitmain Stock firmware support.
- Awesome / AnthillOS firmware support.
- Automatic driver, model and firmware detection.
- AUTO and MANUAL firmware metadata modes.
- Centralized ASIC telemetry.
- Telemetry history.
- Search and dashboard filters.
- Editable scheduler rules.
- Per-ASIC scheduling.
- Manual scheduler overrides.
- Verified Pause and Resume jobs.
- Verified ASIC reboot jobs.
- Anomaly detection.
- Audit logging.
- Telegram notifications.
- Farm summary notifications.
- HTTPS nginx deployment helper.
- Optional Remote ASIC Web.
- Generic full-IP Remote Web hostnames.
- Generated Remote Web nginx configuration.
- systemd application service.
- Automatic firmware-detection systemd timer.
- Environment-based configuration.
- Installation script.

### Validated hardware

- Antminer T21.

### Validated firmware

- Bitmain Stock.
- Awesome Miner / AnthillOS based firmware.

### Notes

This is the first public OpenASICManager release.

The internal production development history used a different version sequence.
Public semantic versioning starts at 0.1.0.
