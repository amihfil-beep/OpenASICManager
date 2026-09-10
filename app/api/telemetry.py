"""Telemetry and farm-history HTTP routes."""

import time

from fastapi import APIRouter, HTTPException

from db import get_miner
from telemetry.repository import (
    farm_history_rows,
    farm_problem_rows,
    farm_current_rows,
)
from telemetry.analytics import (
    history_stats,
    miner_history_points,
    farm_history_points,
    farm_problem_miners,
    farm_current_summary,
)


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


        allowed_hours = {
            1,
            6,
            12,
            24,
            72,
            168,
            720,
            2160,
        }


        if hours not in allowed_hours:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Allowed hours: "
                    "1,6,12,24,72,168,720,2160"
                ),
            )


        since = (
            int(time.time())
            -
            hours * 3600
        )


        points = miner_history_points(
            miner_id,
            since,
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

            "points":
                points,
        }


    @router.get(
        "/api/farm/history"
    )
    def api_farm_history(
        hours: int = 24,
    ):

        allowed_hours = {
            24,
            168,
            720,
            2160,
        }


        if hours not in allowed_hours:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Allowed hours: "
                    "24,168,720,2160"
                ),
            )


        # --------------------------------------------------------
        # Downsampling
        #
        # 24h  -> 5 min
        # 7d   -> 15 min
        # 30d  -> 1 hour
        # 90d  -> 3 hours
        # --------------------------------------------------------

        if hours <= 24:
            bucket_seconds = 300

        elif hours <= 168:
            bucket_seconds = 900

        elif hours <= 720:
            bucket_seconds = 3600

        else:
            bucket_seconds = 10800


        since = (
            int(time.time())
            -
            hours * 3600
        )


        rows = farm_history_rows(
            since,
            bucket_seconds,
        )

        problem_rows = farm_problem_rows(
            since
        )

        current_rows = farm_current_rows()


        points = farm_history_points(
            rows
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
                bucket_seconds,

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
