"""Health and farm status HTTP routes."""

from datetime import datetime
from zoneinfo import ZoneInfo

import config as app_config

from control.repository import list_active_control_jobs
from db import get_setting
from fastapi import APIRouter
from inventory.analytics import miner_status_items
from inventory.repository import list_miners
from scheduler.repository import desired_state, next_transition


TIMEZONE_NAME = app_config.TIMEZONE
MOSCOW = ZoneInfo(TIMEZONE_NAME)


def create_system_router():
    router = APIRouter()

    @router.get("/health")
    def health():
        return {
            "status": "ok",
            "version": "0.1.2",
            "time":
                datetime.now(
                    MOSCOW
                ).isoformat(),
        }


    @router.get("/api/status")
    def api_status():
        rows = list_miners()
        active_job_rows = list_active_control_jobs()

        now = datetime.now(
            MOSCOW
        )
        upcoming = next_transition(
            now
        )

        return {
            "version": "0.1.2",
            "now": now.isoformat(),
            "scheduler_enabled": (
                get_setting(
                    "scheduler_enabled",
                    "0",
                )
                == "1"
            ),
            "desired_state": desired_state(
                now
            ),
            "next_transition": (
                upcoming.isoformat()
                if upcoming
                else None
            ),
            "miners": miner_status_items(
                rows,
                active_job_rows,
            ),
        }


    return router
