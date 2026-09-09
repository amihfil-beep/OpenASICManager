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
    "farm_history_points",
    "farm_problem_miners",
    "farm_current_summary",
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


def farm_history_points(
    rows,
):

    points = []

    for row in rows:

        points.append({
            "time":
                datetime.fromtimestamp(
                    row["bucket_ts"],
                    MOSCOW,
                ).isoformat(),

            "mining":
                row["mining_count"],

            "paused":
                row["paused_count"],

            "starting":
                row["starting_count"],

            "offline":
                row["offline_count"],

            "total":
                row["total_count"],

            "hashrate":
                row["total_hashrate"],

            "power":
                row["known_power"],

            "max_temp":
                row["max_temp"],

            "avg_temp":
                row["avg_temp"],
        })

    return points


def farm_problem_miners(
    rows,
):

    problems = []

    for row in rows:

        problems.append({
            "miner_id":
                row["miner_id"],

            "ip":
                row["ip"],

            "name":
                row["name"],

            "driver":
                row["driver"],

            "samples":
                row["samples"],

            "offline_samples":
                row["offline_samples"],

            "critical_temp_samples":
                row["critical_temp_samples"],

            "max_temp":
                row["max_temp"],

            "avg_mining_hashrate":
                row["avg_mining_hashrate"],
        })

    return problems


def farm_current_summary(
    rows,
):

    return {
        "total":
            len(rows),

        "mining":
            sum(
                1
                for row in rows
                if row["state"] == "MINING"
            ),

        "paused":
            sum(
                1
                for row in rows
                if row["state"] == "PAUSED"
            ),

        "starting":
            sum(
                1
                for row in rows
                if row["state"] == "STARTING"
            ),

        "offline":
            sum(
                1
                for row in rows
                if row["state"] == "OFFLINE"
            ),

        "hashrate":
            sum(
                float(
                    row["hashrate"]
                    or 0
                )
                for row in rows
            ),

        "power":
            sum(
                float(
                    row["power"]
                    or 0
                )
                for row in rows
            ),

        "max_temp":
            max(
                [
                    float(row["temp"])
                    for row in rows
                    if row["temp"] is not None
                ],
                default=None,
            ),
    }

