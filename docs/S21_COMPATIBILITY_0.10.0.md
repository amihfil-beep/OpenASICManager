# OpenASICManager 0.10.0 — ANTMINER S21 read-path compatibility

Status: **development / not released**. Production stays on v0.9.1.

## Support evidence as of 2026-11-18

| Model | Firmware | Read-only parser | Actual live polling | Verified control | Remote Web |
| --- | --- | --- | --- | --- | --- |
| T21 | Bitmain stock | Production baseline | 18 devices | PAUSE/RESUME exercised in production | Existing functionality |
| T21 | Awesome / AnthillOS | Production baseline | 12 devices | PAUSE/RESUME exercised in production | Existing functionality |
| S21 (air) | Bitmain stock | Synthetic tests only | Not yet validated | Not tested | Not tested |
| S21 Pro (air) | Bitmain stock | Synthetic tests only | Not yet validated | Not tested | Not tested |
| S21 / S21 Pro | Awesome / AnthillOS | Generic driver, vendor support documented | Not yet validated | Not tested | Not tested |

**Do not equate a passing unit test with hardware compatibility.**
Power supply phase count is not an API compatibility guarantee.

## Vendor-defined read-only CGI fields

BITMAIN's CGI documentation describes:

- `GET /cgi-bin/miner_type.cgi` — explicit `miner_type` and
  `fw_version` model/build identity;
- `GET /cgi-bin/summary.cgi` — `SUMMARY[].rate_5s`,
  `rate_avg`, `rate_unit`;
- `GET /cgi-bin/stats.cgi` — `STATS[].watt`,
  `miner-mode` and nested `chain[].temp_chip`;
- `GET /cgi-bin/pools.cgi` — pool URL and active/dead status.

Source: https://docs.bitmain.com/en/antminer/CONTENTS.html

The documentation includes an **S21+** example. It does not establish
the exact responses or control compatibility of every S21 or S21 Pro
stock firmware version.

The pure parser in `app/drivers/bitmain_cgi_telemetry.py` returns
hashrate in TH/s, maximum valid chip temperature in degrees Celsius,
power in W, a living pool URL (if present), and an observed work-mode
integer. Missing/invalid numbers remain `None`. It does not create
MINING/PAUSED state; a mode value alone is not verified control state.

The parser **does not perform HTTP requests**, create files, read
SQLite, or change running polling/control dispatch. Integration into
`bitmain_stock` is postponed until actual S21 and S21 Pro fixtures
are collected and examined.

## Release acceptance

1. Real read-only fixture from one S21 and one S21 Pro on installed
   Bitmain stock firmware, including model and exact firmware build;
   manually review and sanitize before sharing.
2. Compare actual API fields and units against the parser. Add
   fixture-driven regression tests, including absent fields.
3. Confirm model identification and telemetry on live hardware.
4. In an operator-approved one-device test window, validate
   PAUSE/RESUME/REBOOT, verified state transitions, scheduling,
   manual overrides, bulk controls and Remote Web. Do not assume
   newer `bitmain-work-mode` matches the legacy T21
   `miner-mode` command contract without testing.
5. Confirm existing T21 regressions, diagnostics, CI, validated
   backup, transactional upgrade and postflight.

## Test the read-only groundwork

Run from the Git clone, not the live `/opt/openasicmanager` tree:

    PYTHONPATH=app python3 -m unittest discover -s tests -p 'test_bitmain_cgi_telemetry.py' -v
    PYTHONPATH=app python3 -m unittest discover -s tests -p 'test_s21_miner_type_cgi.py' -v

Do not change production or schedule/control endpoints during
read-only compatibility research.
