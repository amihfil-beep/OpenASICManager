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
  challenge scheme and response with explicitly supplied credentials;
- HTTP GET /cgi-bin/miner_type.cgi: explicit model and firmware
  build fields (when exposed by the installed firmware);
- HTTP GET /cgi-bin/summary.cgi and /cgi-bin/stats.cgi: additional
  read-only CGI telemetry contracts documented by BITMAIN; unsupported
  endpoints are recorded as unavailable instead of guessed.

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

## 0.10.0 read-only compatibility investigation

BITMAIN's public CGI documentation includes a system-info example
without a `minertype` field, a summary example with `rate_5s` and
`rate_avg`, and a stats example with `watt`, `miner-mode`, and nested
`chain[].temp_chip` measurements. These are documented **API format
examples**, not confirmed snapshots of the user's S21 or S21 Pro.

Relevant vendor documentation:
- https://docs.bitmain.com/en/antminer/CONTENTS.html
- https://www.awesomeminer.com/antminerfirmware

The capture tool now records read-only CGI endpoints in
`http_cgi_diagnostics` while preserving existing `cgminer` and
`http_system_info` fields. The sanitized output is **not evidence of
support** until the device's model and firmware build are confirmed.

Before integration, capture one actual S21 and one actual S21 Pro on
their existing firmware, inspect both JSON files for unexpected
sensitive data, and establish the exact parsing and control contract.
No state-changing requests are included in the capture.

Do not infer S21/S21 Pro identity solely from a firmware filename:
`S21`, `S21 Pro`, `S21 XP`, `S21+`, Hydro, and Immersion must not be
silently conflated. The application only recognizes explicit model
fields in this development stage.

## Explicit model identity on newer stock CGI firmware

BITMAIN documents a separate read-only `GET /cgi-bin/miner_type.cgi`
returning `miner_type`, `subtype`, and `fw_version`. When
`get_system_info.cgi` omits the legacy `minertype`, OpenASICManager
tries this explicit model endpoint before falling back to a read-only
CGMiner STATS query. Legacy T21s with the familiar `minertype` do not
incur an additional CGI request.

The capture helper records `miner_type.cgi` alongside previously
allowlisted GET endpoints and retains only sanitized model/build
values. The `subtype` and any device identity/serial fields remain
redacted. As with other captures, manually review JSON files before
sharing.

Documentation shows an S21+ response, not a confirmed S21/S21 Pro
hardware result. This implementation **does not** equate an S21+,
XP, Hydro or Immersion variant with S21 or S21 Pro. Any model-specific
compatibility or control assumptions remain blocked pending actual
device captures and tests.

Reference: https://docs.bitmain.com/en/antminer/CONTENTS.html
