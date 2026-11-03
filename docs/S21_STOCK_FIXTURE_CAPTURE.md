# S21 / S21 Pro stock-firmware fixture capture (issue #101)

This read-only procedure targets real air-cooled ANTMINER S21 and
S21 Pro units running Bitmain stock firmware. Until both models have
been examined, OpenASICManager must not claim full S21 / S21 Pro support.

## Before starting

Identify one S21 and one S21 Pro running Bitmain stock firmware.
Do not substitute T21, Hydro, XP, immersion or third-party firmware.
Run the helper only when the OpenASICManager server can reach the
chosen ASIC on TCP/4028 and TCP/80. Never pass miner passwords as
shell arguments or paste them into a GitHub issue.

## Commands on the administrative server

Use the separate development Git clone, not the production application
tree at /opt/openasicmanager.

For S21:

    cd /root/projects/openasicmanager
    /opt/openasicmanager/venv/bin/python scripts/capture-stock-hardware-fixture.py --model S21

For S21 Pro:

    cd /root/projects/openasicmanager
    /opt/openasicmanager/venv/bin/python scripts/capture-stock-hardware-fixture.py --model S21-Pro

The helper prompts for a private IPv4 address, HTTP username and
hidden HTTP password. If no password is entered, authenticated HTTP
detection is skipped; CGMiner reading is still attempted.

The only network operations are:

- CGMiner TCP/4028 commands: version, summary, stats, pools, devs;
- HTTP GET /cgi-bin/get_system_info.cgi: anonymous status, auth
  challenge scheme and response with explicitly supplied credentials.

The helper never calls set_miner_conf.cgi, reboot.cgi, POST endpoints,
or HEAD on reboot routes. On some servers HEAD might execute the GET
handler, so it is not a safe reboot probe. PAUSE, RESUME, REBOOT,
bulk control, scheduler tests and authenticated Remote Web browser
testing belong in the explicitly approved test window in issue #103.

## Sanitization and file handling

The helper sanitizes responses in memory and writes only the sanitized
fixture to the executing user's home directory under
openasicmanager-hardware-fixtures (normally
/root/openasicmanager-hardware-fixtures). It never writes raw network
responses. Output files are created with filesystem mode 0600 and the
default location is outside the Git repository.

Unknown string values are redacted. Numeric telemetry and JSON types
are retained. Model, firmware versions, pool state and BTM_SOC IDs
are retained only through constrained allowlists. Pool URLs and workers
are replaced with fixture-safe example values. Passwords, MACs, IP
addresses, serials, cookies, tokens, and Digest nonces are not retained.

Manual review is mandatory before sharing any output. Device firmware
can introduce fields that are not covered by automated sanitization.
Inspect captures for credentials, pool accounts, topology, customer
identifiers and unexpected private information before uploading or
committing anything.

## Record of each real-hardware test

Record the observed model and firmware build, current miner state,
4028/80 reachability, any unavailable command/endpoint, and whether
the HTTP challenge was Digest or Basic. An unavailable API is a useful
observation, not proof of compatibility. Do not enable disabled ASIC
APIs or change firmware/security settings just to obtain a capture.

## Completion criteria

Issue #101 is complete only when sanitized, manually reviewed fixture
captures for both real S21 and S21 Pro stock firmware are available,
with the exact observed firmware builds. Issue #102 then adds
fixture-driven telemetry and discovery validation. Full support still
requires controlled action and Remote Web validation under issue #103.
