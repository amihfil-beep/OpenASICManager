"""Read-only SQLite fleet snapshot for health and external watchdog.

Never starts ASIC network requests, changes miner state, or writes the
production database. PAUSED counts as available when telemetry is fresh.
"""

import sqlite3
import time
from pathlib import Path

import config as app_config
from monitoring.connectivity import (
    MASS_OUTAGE_FRACTION,
    MASS_OUTAGE_MIN_MINERS,
    classify_fleet,
    classify_incident,
)


def read_fleet_snapshot(now=None):
    when = time.time() if now is None else float(now)
    path = Path(app_config.DATABASE_PATH).resolve()
    conn = sqlite3.connect(
        path.as_uri() + "?mode=ro",
        uri=True,
        timeout=3,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            """
            SELECT last_state, last_seen
            FROM miners
            WHERE enabled=1
              AND driver!='unset'
            """
        ).fetchall()
    finally:
        conn.close()

    result = classify_fleet(rows, when)
    result["mass_outage"] = bool(
        classify_incident(
            result,
            app_available=True,
            vpn_service_ok=True,
            vpn_route_ok=True,
        ) == "FARM_MASS_UNAVAILABLE"
    )
    result["healthy"] = not result["mass_outage"]
    result["outage_threshold_fraction"] = MASS_OUTAGE_FRACTION
    result["outage_min_miners"] = MASS_OUTAGE_MIN_MINERS
    return result


__all__ = ("read_fleet_snapshot",)
