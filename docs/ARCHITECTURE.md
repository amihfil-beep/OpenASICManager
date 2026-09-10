# OpenASICManager Architecture

## Overview

OpenASICManager is a self-hosted ASIC management application.

The standard deployment model is:

    Browser
       |
      HTTPS
       |
    nginx + Basic Auth
       |
       v
    FastAPI application
       |
       +---- repositories ---- SQLite
       |
       +---- firmware drivers ---- ASIC management network
       |
       +---- Telegram API (optional)
       |
       +---- Remote ASIC Web (optional)

The FastAPI application itself listens only on:

    127.0.0.1:8088

nginx is responsible for external HTTPS access.

## Application structure

OpenASICManager 0.2.0 separates application composition, HTTP routing, domain services, firmware drivers and persistence.

The main application packages are:

    app/
    ├── app.py
    ├── app_version.py
    ├── config.py
    ├── db.py
    ├── api/
    ├── audit/
    ├── control/
    ├── drivers/
    ├── inventory/
    ├── monitoring/
    ├── scheduler/
    ├── telemetry/
    ├── anomalies/
    ├── notifications/
    └── ui/

### app/app.py

`app.py` is the application composition root.

Its responsibilities are intentionally limited to:

- creating application-owned runtime objects;
- sharing the application shutdown signal between background services;
- wiring callbacks between subsystems;
- FastAPI lifespan startup/shutdown;
- request-scoped audit middleware;
- registering API routers;
- starting the Telegram farm-summary background loop;
- serving the dashboard entry point.

Business logic, SQL queries and firmware-specific network operations do not belong in the composition root.

### app/api/

The API package contains FastAPI routers grouped by responsibility.

Current routers include:

- `audit_auth.py` — audit identity test/whoami and authenticated user switching;
- `control.py` — manual pause/resume and reboot operations;
- `discovery.py` — network discovery and discovery-result enrollment;
- `inventory.py` — miner configuration, firmware metadata and scheduling flags;
- `notifications.py` — Telegram configuration/test/summary endpoints;
- `operations.py` — audit log, control-job and issue read APIs;
- `remote_web.py` — Remote ASIC Web redirect and authorization endpoints;
- `scheduler.py` — scheduler state and editable rule APIs;
- `system.py` — health and farm-status endpoints;
- `telemetry.py` — miner and farm history endpoints.

Routers may call domain services, analytics functions and repositories, but they do not import the application composition root.

### app/drivers/

Firmware-specific ASIC communication is isolated in driver modules.

Current drivers include:

- `bitmain.py` — Bitmain Stock status, control and reboot behavior;
- `awesome.py` — Awesome Miner / AnthillOS status, authorization, control and reboot behavior.

The control dispatch layer selects the correct driver without placing firmware-specific request logic in API endpoints.

### app/control/

The control subsystem contains:

- `dispatch.py` — driver dispatch for status/control/reboot operations;
- `policy.py` — control target, timeout and retry policy;
- `repository.py` — control-job and related persistence;
- `analytics.py` — control-job API read models;
- `worker.py` — verified pause/resume/reboot execution;
- `queue.py` — asynchronous job queueing and manual override behavior.

Control is intentionally asynchronous and verified against observed ASIC state.

### app/scheduler/

The scheduler subsystem contains:

- `policy.py` — schedule calculations and formatting;
- `validation.py` — scheduler API input normalization and validation;
- `repository.py` — schedule-rule persistence, desired-state queries and schedulable-miner reads;
- `service.py` — background scheduler runtime.

The scheduler service receives application-owned logging, control queueing and shutdown dependencies through `SchedulerRuntime`.

### app/telemetry/

The telemetry subsystem contains:

- `repository.py` — telemetry snapshots, retention and history queries;
- `analytics.py` — miner/farm history read models and summary calculations;
- `service.py` — periodic telemetry snapshot collection.

### app/anomalies/

The anomaly subsystem contains:

- `policy.py` — anomaly thresholds and pure applicability helpers;
- `repository.py` — anomaly candidates, issues and persistence transitions;
- `analytics.py` — issue API read model;
- `service.py` — periodic anomaly evaluation runtime.

Current operational conditions include ASIC offline, excessive temperature and scheduler mismatch.

### app/notifications/

The notification subsystem contains:

- `telegram.py` — Telegram formatting, delivery, command parsing and farm-summary runtime;
- `repository.py` — issue-context and farm-summary persistence required by Telegram formatting.

Telegram integration is optional and disabled by default.

### app/monitoring/

The monitoring subsystem owns regular ASIC polling and delayed refresh operations.

`MonitoringRuntime` exposes polling callbacks used by control and API composition without requiring those modules to own polling threads themselves.

### app/inventory/

The inventory subsystem owns managed-miner persistence and read models.

It includes repository helpers for miner configuration, firmware metadata, discovery enrollment, enable/schedule flags and manual override changes.

### app/audit/

The audit subsystem contains:

- `identity.py` — request-scoped actor identity and source attribution;
- `repository.py` — audit-event persistence;
- `service.py` — application-facing audit runtime and read model.

Requests arriving through nginx may provide the authenticated username through `X-Remote-User`. Automatic actions retain system identities such as `SYSTEM` and `SCHEDULER`.

### app/remote_web.py

This module contains Remote ASIC Web token, cookie and hostname policy.

Miner inventory lookup is delegated to the inventory repository rather than performed through direct SQL.

The full IPv4 address is encoded into the DNS name.

Example:

    192.168.50.81

becomes:

    m192-168-50-81.manager.example.com

Using the complete IPv4 address avoids hostname collisions when more than one ASIC subnet is managed.

### app/ui/

The dashboard HTML template is stored under `app/ui/` instead of being embedded inside `app.py`.

The root route renders the dashboard through `ui.dashboard`.

### app/config.py

Central environment-based configuration.

Deployment-specific values are loaded from environment variables instead of being stored in source code.

Default installed configuration file:

    /etc/openasicmanager/openasicmanager.env

Passwords and cryptographic secrets intentionally have no source-code defaults.

### app/app_version.py

Contains the runtime application version used by health/status APIs.

CI tests ensure that this value matches the repository-level `VERSION` file.

## Persistence boundary

SQLite access follows an explicit persistence boundary.

Direct database connections are allowed only in:

- `app/db.py` for shared schema/settings helpers;
- modules named `repository.py` for subsystem-specific persistence.

API routers, services, policies, analytics and firmware drivers do not open SQLite connections directly.

Permanent architecture tests enforce this rule and also prevent API routers from importing the application composition root.

## Background service lifecycle

Application-owned background services share a single shutdown signal created in `app/app.py`.

The FastAPI lifespan starts the primary background threads for:

- miner polling;
- scheduler processing;
- telemetry collection;
- anomaly evaluation.

Telegram farm-summary scheduling is also started by the application layer.

On shutdown, the shared stop event is set so background runtimes can terminate cleanly.

## Discovery

ASIC discovery scans RFC1918 IPv4 networks and attempts to identify supported firmware.

Current detection methods include:

    Awesome / AnthillOS:
        HTTP API /api/v1/summary

    Bitmain Stock:
        Bitmain HTTP/Digest endpoints and ASIC API signatures

Discovery-result persistence is handled through the inventory repository.

## Helper scripts

### scripts/asic-discover

Portable wrapper around the discovery module.

It can operate either from a Git checkout or from:

    /opt/openasicmanager

### scripts/asic-firmware-detect

Periodic metadata detector.

It detects:

- firmware driver;
- ASIC model;
- firmware version.

AUTO devices may be updated.

MANUAL devices are protected against periodic metadata replacement.

### scripts/generate-remote-nginx

Generates nginx configuration for Remote ASIC Web from the managed ASIC inventory stored in SQLite.

It validates generated configuration with:

    nginx -t

before nginx is reloaded.

## SQLite

Default database:

    /var/lib/openasicmanager/openasicmanager.db

SQLite stores:

- managed ASIC inventory;
- configuration state;
- scheduler rules;
- scheduler overrides;
- telemetry;
- telemetry history;
- control jobs;
- anomaly state;
- audit events.

## ASIC control model

Control is intentionally asynchronous.

A successful HTTP response does not automatically mean that an ASIC has actually changed state.

The control flow is:

    User or Scheduler
           |
           v
      Control Job
           |
           v
    Firmware Driver
           |
           v
    Verification polling
           |
           +---- VERIFIED
           |
           +---- FAILED

Typical states include:

    MINING
    PAUSED
    STARTING
    RESTARTING
    OFFLINE
    UNKNOWN

Firmware-specific verification logic is used because different firmware families expose different APIs and transition behavior.

## Scheduler

The scheduler is rule based.

A rule contains:

- action: PAUSE or RESUME;
- time;
- weekdays;
- enabled state;
- comment;
- scope.

Each ASIC independently controls whether scheduling is enabled.

Manual operations can create a temporary override until the next scheduled transition.

A clean installation contains no scheduler rules and the global scheduler is disabled.

## Firmware metadata modes

Each ASIC has a firmware detection mode.

AUTO:

    Periodic firmware detection may update driver, model and firmware.

MANUAL:

    Automatic detection does not overwrite administrator-supplied values.

A manual immediate auto-detect operation can restore an ASIC to AUTO mode.

## Audit model

Requests arriving through nginx include the authenticated username in:

    X-Remote-User

OpenASICManager records Web actions with the authenticated actor.

Automatic actions retain system identities such as:

    SYSTEM
    SCHEDULER

This keeps manual and automated operations distinguishable in the journal.

## Telegram

Telegram integration is optional.

It can send selected operational notifications and farm summaries.

A SOCKS proxy may be configured through environment variables when direct Telegram connectivity is unavailable.

## Security model

The default installation runs OpenASICManager using the dedicated account:

    openasicmanager

The systemd service uses restrictions including:

    NoNewPrivileges=true
    PrivateTmp=true
    ProtectSystem=strict
    ProtectHome=true
    UMask=0077

The application database remains writable under:

    /var/lib/openasicmanager

Public HTTPS access is intended to terminate at nginx.

ASIC web interfaces do not need to be exposed directly to the Internet.

## Network assumptions

The OpenASICManager server must have IP connectivity to managed ASIC management addresses.

Discovery is restricted to RFC1918 IPv4 networks.

The server does not need to use the ASIC network as its default route. Routed, VPN or other private connectivity can be used.
