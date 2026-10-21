"""Health and farm status HTTP routes."""

from datetime import datetime
from zoneinfo import ZoneInfo

import config as app_config

from app_version import APP_VERSION
from control.repository import list_active_control_jobs
from db import get_setting
from fastapi import APIRouter
from inventory.analytics import miner_status_items
from inventory.repository import list_miners
from scheduler.repository import (
    desired_state,
    next_transition,
    effective_schedule_states,
)
from service_history.repository import (
    latest_service_records_by_miner,
)


TIMEZONE_NAME = app_config.TIMEZONE
MOSCOW = ZoneInfo(TIMEZONE_NAME)


def create_system_router():
    router = APIRouter()

    @router.get("/health")
    def health():
        return {
            "status": "ok",
            "version": APP_VERSION,
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


        schedule_states = (
            effective_schedule_states(
                rows,
                now,
            )
        )


        miners = (
            miner_status_items(
                rows,
                active_job_rows,
            )
        )


        latest_service = (
            latest_service_records_by_miner()
        )


        for miner in miners:

            service_row = (
                latest_service.get(
                    int(
                        miner["id"]
                    )
                )
            )


            miner[
                "last_service"
            ] = (
                {
                    "id":
                        int(
                            service_row["id"]
                        ),

                    "performed_at":
                        int(
                            service_row[
                                "performed_at"
                            ]
                        ),

                    "work_type":
                        str(
                            service_row[
                                "work_type"
                            ]
                        ),

                    "description":
                        (
                            service_row[
                                "description"
                            ]
                            or ""
                        ),

                    "part_serial":
                        service_row[
                            "part_serial"
                        ],

                    "performed_by":
                        (
                            service_row[
                                "performed_by"
                            ]
                            or ""
                        ),
                }
                if service_row is not None
                else None
            )


            details = (
                schedule_states.get(
                    int(
                        miner["id"]
                    ),
                    {},
                )
            )


            rule = details.get(
                "rule"
            )


            transition = details.get(
                "next_transition"
            )


            source_scope = details.get(
                "source_scope"
            )


            miner[
                "schedule_context"
            ] = {
                "applicable":
                    bool(
                        miner[
                            "enabled"
                        ]
                        and
                        miner[
                            "schedule_enabled"
                        ]
                        and
                        miner[
                            "driver"
                        ]
                        in (
                            "awesome",
                            "bitmain_stock",
                        )
                    ),

                "desired_state":
                    details.get(
                        "desired_state"
                    ),

                "source_scope":
                    source_scope,

                "source_group_id":
                    (
                        miner[
                            "group_id"
                        ]
                        if (
                            source_scope
                            ==
                            "GROUP"
                        )
                        else None
                    ),

                "rule_id":
                    (
                        int(
                            rule["id"]
                        )
                        if rule is not None
                        else None
                    ),

                "rule_action":
                    (
                        str(
                            rule["action"]
                        )
                        if rule is not None
                        else None
                    ),

                "next_transition":
                    (
                        transition.isoformat()
                        if transition
                        else None
                    ),

                "next_transition_label":
                    (
                        transition.strftime(
                            "%a %d.%m %H:%M"
                        )
                        +
                        " "
                        +
                        TIMEZONE_NAME
                        if transition
                        else None
                    ),
            }


        return {
            "version": APP_VERSION,
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
            "miners": miners,
        }


    return router
