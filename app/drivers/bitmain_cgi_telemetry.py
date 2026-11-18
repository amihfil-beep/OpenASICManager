"""Pure, offline parsing of documented BITMAIN CGI telemetry.

This module performs NO network or database operations and does not
determine miner state. Live S21/S21 Pro control and state verification
remain gated on real hardware tests.
"""

import math


RATE_TO_TH = {
    "GH/s": 0.001,
    "TH/s": 1.0,
    "MH/s": 0.000001,
    "PH/s": 1000.0,
}


def _first_record(payload, key):
    if not isinstance(payload, dict):
        return {}
    records = payload.get(key)
    if not isinstance(records, list):
        return {}
    for record in records:
        if isinstance(record, dict):
            return record
    return {}


def _number(value, *, positive=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if number <= 0 if positive else number < 0:
        return None
    return number


def _hashrate(value, unit):
    factor = RATE_TO_TH.get(unit)
    measured = _number(value)
    if factor is None or measured is None:
        return None
    return measured * factor


def _chip_temperature(stats):
    chains = stats.get("chain")
    if not isinstance(chains, list):
        return None
    readings = []
    for chain in chains:
        if not isinstance(chain, dict):
            continue
        sensors = chain.get("temp_chip")
        if not isinstance(sensors, list):
            continue
        for reading in sensors:
            value = _number(reading, positive=True)
            # Reject common missing/sentinel readings such as 0 or 255.
            # No fallback to ambient temperature (different meaning).
            if value is not None and value < 125.0:
                readings.append(value)
    return max(readings) if readings else None


def _work_mode(stats):
    value = stats.get("miner-mode")
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in (0, 1, 2, 3):
        return value
    if isinstance(value, str) and value.strip() in {"0", "1", "2", "3"}:
        return int(value.strip())
    return None


def _active_pool(payload):
    if not isinstance(payload, dict):
        return None
    pools = payload.get("POOLS")
    if not isinstance(pools, list):
        return None
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        if str(pool.get("status", "")).casefold() != "alive":
            continue
        url = pool.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def normalize_cgi_telemetry(
    summary_payload=None,
    stats_payload=None,
    pools_payload=None,
):
    """Normalize read-only CGI numeric data without guessing ASIC state.

    Hashrate -> TH/s; power -> W; max chip temperature -> degrees C.
    Invalid or absent fields return None. An observed miner-mode is
    metadata, NOT permission to infer PAUSED/MINING for control.
    """
    summary = _first_record(summary_payload, "SUMMARY")
    stats = _first_record(stats_payload, "STATS")

    unit = summary.get("rate_unit") or stats.get("rate_unit")
    if not isinstance(unit, str):
        unit = None

    return {
        "hashrate": _hashrate(
            summary.get("rate_5s", stats.get("rate_5s")),
            unit,
        ),
        "avg_hashrate": _hashrate(
            summary.get("rate_avg", stats.get("rate_avg")),
            unit,
        ),
        "temp": _chip_temperature(stats),
        "power": _number(stats.get("watt")),
        "pool": _active_pool(pools_payload),
        "observed_work_mode": _work_mode(stats),
    }


__all__ = ("normalize_cgi_telemetry",)
