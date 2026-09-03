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
    OpenASICManager
       |
       +---- SQLite
       |
       +---- ASIC management network
       |
       +---- Telegram API (optional)
       |
       +---- Remote ASIC Web (optional)

The FastAPI application itself listens only on:

    127.0.0.1:8088

nginx is responsible for external HTTPS access.

## Application components

### app/app.py

Main FastAPI application.

Responsibilities include:

    - Web UI;
    - REST API;
    - telemetry polling;
    - control jobs;
    - scheduler;
    - anomaly processing;
    - history;
    - audit logging;
    - Telegram notifications;
    - Remote Web authorization.

### app/config.py

Central environment-based configuration.

Deployment-specific values are loaded from environment variables instead of
being stored in source code.

Default installed configuration file:

    /etc/openasicmanager/openasicmanager.env

Passwords and cryptographic secrets intentionally have no source-code
defaults.

### app/discovery.py

ASIC discovery engine.

It scans RFC1918 IPv4 networks and attempts to identify supported firmware.

Current detection methods include:

    Awesome / AnthillOS:
        HTTP API /api/v1/summary

    Bitmain Stock:
        Bitmain HTTP/Digest endpoints and ASIC API signatures

### app/remote_web.py

Contains generic Remote ASIC Web hostname generation.

The full IPv4 address is encoded into the DNS name.

Example:

    192.168.50.81

becomes:

    m192-168-50-81.manager.example.com

Using the complete IPv4 address avoids hostname collisions when more than one
ASIC subnet is managed.

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

Generates nginx configuration for Remote ASIC Web from the managed ASIC
inventory stored in SQLite.

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

A successful HTTP response does not automatically mean that an ASIC has
actually changed state.

The control flow is:

    User or Scheduler
           |
           v
      Control Job
           |
           v
       API request
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

Firmware-specific verification logic is used because different firmware
families expose different APIs and transition behavior.

## Scheduler

The scheduler is rule based.

A rule contains:

    - action: PAUSE or RESUME;
    - time;
    - weekdays;
    - enabled state;
    - comment;
    - scope.

Example:

    Monday-Friday

    07:00 PAUSE
    13:00 RESUME
    16:00 PAUSE
    21:00 RESUME

Each ASIC independently controls whether scheduling is enabled.

Manual operations can create a temporary override until the next scheduled
transition.

## Firmware metadata modes

Each ASIC has a firmware detection mode.

AUTO:

    Periodic firmware detection may update driver, model and firmware.

MANUAL:

    Automatic detection does not overwrite administrator-supplied values.

A manual immediate auto-detect operation can restore an ASIC to AUTO mode.

## Anomaly detection

The current architecture tracks operational conditions such as:

    - ASIC offline;
    - excessive temperature;
    - scheduler mismatch.

Issue state is maintained so that the system can distinguish between a newly
opened problem and a resolved problem.

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

A SOCKS proxy may be configured through environment variables when direct
Telegram connectivity is unavailable.

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

The OpenASICManager server must have IP connectivity to managed ASIC
management addresses.

Discovery is restricted to RFC1918 IPv4 networks.

The server does not need to use the ASIC network as its default route. Routed,
VPN or other private connectivity can be used.
