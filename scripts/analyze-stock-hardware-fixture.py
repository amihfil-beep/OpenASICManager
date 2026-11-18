#!/usr/bin/env python3
"""Safely inspect an already sanitized S21/S21 Pro fixture offline.

No network, database, miner control, or firmware operations are used.
Only aggregate numeric metrics and fixed-status labels are printed.
"""

import argparse
import json
import math
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app"))

from drivers.bitmain_cgi_telemetry import normalize_cgi_telemetry
from drivers.bitmain_models import stock_model_from_responses


EXPECTED = {
    "S21": "Antminer S21",
    "S21-Pro": "Antminer S21 Pro",
}
MAX_INPUT_BYTES = 5 * 1024 * 1024


def _captured_cgi(fixture, path):
    reports = fixture.get("http_cgi_diagnostics")
    if not isinstance(reports, dict):
        return None
    result = reports.get(path)
    if not isinstance(result, dict):
        return None
    data = result.get("sanitized_json")
    if not isinstance(data, dict):
        return None
    if (
        result.get("authenticated_status") != 200
        and result.get("unauthenticated_status") != 200
    ):
        return None
    return data


def _cgminer_data(fixture, command):
    entries = fixture.get("cgminer")
    if not isinstance(entries, dict):
        return None
    entry = entries.get(command)
    if not isinstance(entry, dict) or entry.get("available") is not True:
        return None
    data = entry.get("data")
    return data if isinstance(data, dict) else None


def _legacy_rate(summary, field):
    if not isinstance(summary, dict):
        return None
    records = summary.get("SUMMARY")
    if not isinstance(records, list) or not records:
        return None
    row = records[0]
    if not isinstance(row, dict):
        return None
    value = row.get(field)
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return round(number / 1000.0, 4)


def analyze_fixture(fixture):
    """Compare read-only responses without treating them as verified control."""
    if not isinstance(fixture, dict):
        raise ValueError("Expected an object")
    if fixture.get("schema_version") != 1:
        raise ValueError("Unsupported fixture schema")
    if fixture.get("capture_mode") != "read_only":
        raise ValueError("Fixture is not marked read_only")

    expected = fixture.get("expected_model")
    if expected not in EXPECTED:
        raise ValueError("Fixture model must be S21 or S21-Pro")

    cgi_summary = _captured_cgi(fixture, "/cgi-bin/summary.cgi")
    cgi_stats = _captured_cgi(fixture, "/cgi-bin/stats.cgi")
    cgi_type = _captured_cgi(fixture, "/cgi-bin/miner_type.cgi")
    system_info_report = fixture.get("http_system_info")
    system_info = (
        system_info_report.get("sanitized_json")
        if isinstance(system_info_report, dict)
        else None
    )

    cg_summary = _cgminer_data(fixture, "summary")
    cg_stats = _cgminer_data(fixture, "stats")
    cg_stats_items = (
        cg_stats.get("STATS", []) if isinstance(cg_stats, dict) else []
    )

    observed_model = (
        stock_model_from_responses(system_info=system_info)
        or stock_model_from_responses(system_info=cgi_type)
        or stock_model_from_responses(stats_items=cg_stats_items)
    )
    if observed_model is None:
        model_status = "unknown"
    elif observed_model == EXPECTED[expected]:
        model_status = "matches_expected"
    else:
        model_status = "different_or_unsupported"

    normalized = normalize_cgi_telemetry(
        summary_payload=cgi_summary,
        stats_payload=cgi_stats,
    )
    legacy_rate = _legacy_rate(cg_summary, "GHS 5s")
    cgi_rate = normalized["hashrate"]
    delta = (
        round(cgi_rate - legacy_rate, 4)
        if legacy_rate is not None and cgi_rate is not None
        else None
    )

    # Never echo IPs, worker names, pool URLs, firmware strings, host
    # metadata, or arbitrary fixture text to stdout.
    return {
        "expected_model": expected,
        "model_evidence": model_status,
        "capture_mode": "read_only",
        "source_available": {
            "cgminer_summary": cg_summary is not None,
            "cgminer_stats": cg_stats is not None,
            "cgi_miner_type": cgi_type is not None,
            "cgi_summary": cgi_summary is not None,
            "cgi_stats": cgi_stats is not None,
        },
        "metrics": {
            "cgminer_hashrate_ths": legacy_rate,
            "cgi_hashrate_ths": (
                round(cgi_rate, 4) if cgi_rate is not None else None
            ),
            "cgi_avg_hashrate_ths": (
                round(normalized["avg_hashrate"], 4)
                if normalized["avg_hashrate"] is not None
                else None
            ),
            "cgi_max_chip_temp_c": normalized["temp"],
            "cgi_power_w": normalized["power"],
            "cgi_observed_work_mode": normalized["observed_work_mode"],
            "cgi_minus_cgminer_hashrate_ths": delta,
        },
        "compatibility_verified": False,
        "next_step": "Review sanitized device fixture and validate real hardware",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", help="Path to sanitized read-only JSON fixture")
    args = parser.parse_args(argv)

    path = Path(args.fixture).expanduser()
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Fixture must be an existing regular file")
        stat = path.stat()
        if stat.st_size > MAX_INPUT_BYTES or stat.st_size == 0:
            raise ValueError("Fixture exceeds safe file size or is empty")
        if stat.st_mode & 0o077:
            raise ValueError("Fixture must have permissions 0600")
        data = json.loads(path.read_text(encoding="utf-8"))
        report = analyze_fixture(data)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        print("ERROR: Invalid, unsafe or unsupported sanitized fixture", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
