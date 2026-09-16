"""Telemetry and farm-history HTTP routes."""

import time

from fastapi import APIRouter, HTTPException

from db import get_miner
from telemetry.repository import (
    telemetry_history_rows,
    farm_history_rows,
    farm_problem_rows,
    farm_current_rows,
)
from telemetry.analytics import (
    history_stats,
    history_metadata,
    miner_history_points,
    farm_history_points,
    farm_problem_miners,
    farm_current_summary,
)
from telemetry.history import (
    SUPPORTED_HISTORY_HOURS,
    aligned_history_window,
    history_range,
)


def _selected_history_range(hours):
    selected_range = history_range(hours)

    if selected_range is None:
        allowed = ",".join(
            str(value)
            for value in SUPPORTED_HISTORY_HOURS
        )

        raise HTTPException(
            status_code=400,
            detail=f"Allowed hours: {allowed}",
        )

    return selected_range


def create_telemetry_router():
    router = APIRouter()

    @router.get("/api/history/stats/summary")
    def api_history_stats_summary():
        return history_stats()

    @router.get(
        "/api/history/{miner_id}"
    )
    def api_history(
        miner_id: int,
        hours: int = 24,
    ):

        miner = get_miner(
            miner_id
        )

        if not miner:

            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )


        selected_range = _selected_history_range(
            hours
        )

        since, until = aligned_history_window(
            time.time(),
            selected_range,
        )

        rows = telemetry_history_rows(
            miner_id,
            since,
            selected_range.bucket_seconds,
            until,
        )

        points = miner_history_points(
            rows
        )

        metadata = history_metadata(
            selected_range,
            since,
            until,
            len(points),
        )


        return {
            "miner": {
                "id":
                    miner["id"],

                "ip":
                    miner["ip"],

                "name":
                    miner["name"],

                "driver":
                    miner["driver"],
            },

            "hours":
                hours,

            "bucket_seconds":
                selected_range.bucket_seconds,

            "metadata":
                metadata,

            "points":
                points,
        }


    @router.get(
        "/api/farm/history"
    )
    def api_farm_history(
        hours: int = 24,
    ):

        selected_range = _selected_history_range(
            hours
        )

        since, until = aligned_history_window(
            time.time(),
            selected_range,
        )


        rows = farm_history_rows(
            since,
            selected_range.bucket_seconds,
            until,
        )

        problem_rows = farm_problem_rows(
            since
        )

        current_rows = farm_current_rows()


        points = farm_history_points(
            rows
        )

        metadata = history_metadata(
            selected_range,
            since,
            until,
            len(points),
        )

        problems = farm_problem_miners(
            problem_rows
        )

        current = farm_current_summary(
            current_rows
        )


        return {
            "hours":
                hours,

            "bucket_seconds":
                selected_range.bucket_seconds,

            "metadata":
                metadata,

            "current":
                current,

            "points":
                points,

            "problems":
                problems,
        }


    return router


__all__ = (
    "create_telemetry_router",
)
