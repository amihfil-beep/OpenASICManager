"""
Telemetry history analytics.

Transforms raw telemetry repository data into API-ready
statistics and time-series points.

This module does not expose HTTP endpoints.
"""

from datetime import datetime

from scheduler.policy import MOSCOW

from telemetry.repository import (
    TELEMETRY_RETENTION_DAYS,
    telemetry_stats_row,
    telemetry_history_rows,
)

from telemetry.service import (
    TELEMETRY_INTERVAL,
)


__all__ = (
    "history_stats",
    "miner_history_points",
)


def history_stats():

    row = telemetry_stats_row()

    return {
        "rows":
            row["rows"],

        "oldest":
            (
                datetime.fromtimestamp(
                    row["oldest"],
                    MOSCOW,
                ).isoformat()

                if row["oldest"]
                else None
            ),

        "newest":
            (
                datetime.fromtimestamp(
                    row["newest"],
                    MOSCOW,
                ).isoformat()

                if row["newest"]
                else None
            ),

        "interval_seconds":
            TELEMETRY_INTERVAL,

        "retention_days":
            TELEMETRY_RETENTION_DAYS,
    }


def miner_history_points(
    miner_id,
    since,
):

    rows = telemetry_history_rows(
        miner_id,
        since,
    )

    points = []

    for row in rows:

        points.append({
            "time":
                datetime.fromtimestamp(
                    row["ts"],
                    MOSCOW,
                ).isoformat(),

            "state":
                row["state"],

            "hashrate":
                row["hashrate"],

            "avg_hashrate":
                row["avg_hashrate"],

            "temp":
                row["temp"],

            "power":
                row["power"],
        })

    return points
