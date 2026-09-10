# Changelog

All notable changes to OpenASICManager will be documented in this file.

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
