"""Read-only SQLite fleet snapshot for health and external watchdog.

Never starts ASIC network requests, changes miner state, or writes the
production database. PAUSED counts as available when telemetry is fresh.
"""

 import time
 from monitoring.repository import read_fleet_health_rows
from monitoring.connectivity import (
    MASS_OUTAGE_FRACTION,
    MASS_OUTAGE_MIN_MINERS,
    classify_fleet,
    classify_incident,
)


def read_fleet_snapshot(now=None):
    when = time.time() if now is None else float(now)
    rows = read_fleet_health_rows()
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
