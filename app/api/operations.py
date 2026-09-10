"""Read-only operational HTTP routes."""

from zoneinfo import ZoneInfo

from fastapi import APIRouter

import config as app_config
from audit.service import action_log_entries
from control.repository import list_control_jobs
from control.analytics import control_job_items
from anomalies.analytics import issue_report


MOSCOW = ZoneInfo(app_config.TIMEZONE)


def create_operations_router():
    router = APIRouter()

    @router.get(
        "/api/logs"
    )
    def api_logs(
        limit: int = 100,
    ):
        return {
            "logs": action_log_entries(
                limit,
                MOSCOW,
            )
        }


    @router.get(
        "/api/control/jobs"
    )
    def api_control_jobs(
        limit: int = 100,
    ):
        limit = max(
            1,
            min(
                int(limit),
                500,
            ),
        )

        rows = list_control_jobs(
            limit
        )

        return {
            "jobs": control_job_items(
                rows
            )
        }


    @router.get(
        "/api/issues"
    )
    def api_issues(
        limit: int = 100,
    ):
        limit = max(
            1,
            min(int(limit), 500),
        )
        return issue_report(limit)


    return router


__all__ = (
    "create_operations_router",
)
