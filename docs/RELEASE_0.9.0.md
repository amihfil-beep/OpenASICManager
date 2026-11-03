# OpenASICManager 0.9.0

OpenASICManager 0.9.0 adds recurring Planned Maintenance and an operator-facing operations calendar while preserving the existing separation between maintenance, anomaly detection and ASIC control.

## Highlights

- Persistent recurring maintenance rules for FARM, GROUP and MINER scopes.
- Weekly recurrence with selectable weekdays, local start time, bounded duration and enable/disable state.
- Deterministic occurrence generation in the configured application timezone.
- Idempotent occurrence identity so retries and delayed background iterations do not create duplicate windows.
- GROUP recurring rules use current membership for future planning and snapshot membership when the concrete occurrence is materialized.
- Operations Calendar with Active now, Upcoming and Recent sections.
- Explicit ONE-OFF versus RECURRING source labels and bounded scope/horizon filters.
- Future recurring entries are forecasts; materialized maintenance windows remain the factual occurrence record.
- Compact per-ASIC upcoming-maintenance indication without widening the main inventory table.
- Durable recurring occurrence history with ACTIVE, COMPLETED and CANCELLED lifecycle semantics.
- Bounded seven-day restart catch-up for missed occurrences, limited to the currently effective rule version.
- Telegram lifecycle delivery reuses the existing maintenance notification policy.
- Recurring-maintenance materializer heartbeat participates in `/health`, so a stale background worker degrades application health.

## Safety boundaries

Recurring maintenance does not automatically PAUSE, RESUME or REBOOT ASICs. It does not create or modify scheduler rules and does not delete anomaly, telemetry, service-history or audit data.

Maintenance continues to suppress expected anomaly notification noise only for the bounded concrete maintenance window. The existing verified per-miner ASIC-control pipeline remains independent.

## Upgrade behavior

The release uses the existing transactional public upgrade path. Existing one-off maintenance windows remain compatible. Recurring-maintenance tables are created idempotently when the application starts.

The transactional upgrader still requires `/health` to report `status=ok`. In 0.9.0 this means both the ASIC scheduler heartbeat and recurring-maintenance materializer heartbeat must be healthy before an upgrade is accepted.

## Development history

- #97 / PR #104 - recurring maintenance rules and deterministic occurrence generation.
- #98 / PR #105 - operations calendar and upcoming-maintenance dashboard.
- #99 / PR #106 - occurrence lifecycle, restart catch-up, notification integration and materializer health.

## Deferred

- Automatic PAUSE/RESUME tied to maintenance.
- Automatic anomaly remediation.
- Escalation/on-call scheduling.
- Firmware upgrade orchestration.
- PDU/power control.

The next planned compatibility milestone is 0.10.0 for real-hardware validation of ANTMINER S21 and S21 Pro.
