"""Scheduler HTTP routes."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request

import config as app_config
from db import (
    get_setting,
    set_setting,
    ensure_schedule_rules_schema,
)
from scheduler.policy import (
    schedule_time_string,
    schedule_days_string,
    schedule_rule_dict,
)
from scheduler.repository import (
    schedule_state_details,
    schedule_conflicting_rule,
    next_transition,
    list_schedule_rules,
    get_schedule_rule,
    create_schedule_rule,
    update_schedule_rule,
    set_schedule_rule_enabled,
    delete_schedule_rule,
)
from scheduler.validation import (
    schedule_normalize_input,
)


TIMEZONE_NAME = app_config.TIMEZONE
MOSCOW = ZoneInfo(TIMEZONE_NAME)


def create_scheduler_router(log_event):
    router = APIRouter()

    @router.get(
        "/api/schedule/rules"
    )
    def api_schedule_rules():

        ensure_schedule_rules_schema()

        now = datetime.now(
            MOSCOW
        )

        rules = list_schedule_rules()

        desired, active_rule, _ = (
            schedule_state_details(
                now
            )
        )

        upcoming = next_transition(
            now
        )

        return {
            "timezone":
                TIMEZONE_NAME,

            "scheduler_enabled":
                (
                    get_setting(
                        "scheduler_enabled",
                        "0",
                    )
                    ==
                    "1"
                ),

            "desired_state":
                desired,

            "active_rule_id":
                (
                    int(
                        active_rule[
                            "id"
                        ]
                    )
                    if active_rule
                    else None
                ),

            "next_transition":
                (
                    upcoming.isoformat()
                    if upcoming
                    else None
                ),

            "next_transition_label":
                (
                    (
                        upcoming.strftime(
                            "%a %d.%m %H:%M"
                        )
                        +
                        " "
                        +
                        TIMEZONE_NAME
                    )
                    if upcoming
                    else None
                ),

            "rules": [
                schedule_rule_dict(
                    rule,
                    now
                )
                for rule
                in rules
            ],
        }


    @router.post(
        "/api/schedule/rules"
    )
    async def api_schedule_rule_create(
        request: Request,
    ):

        ensure_schedule_rules_schema()

        try:
            data = await request.json()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid JSON body",
            )

        normalized = schedule_normalize_input(
            data
        )

        conflict = schedule_conflicting_rule(
            normalized
        )

        if conflict:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Schedule conflict with "
                    f"Rule #{conflict['id']} "
                    f"at "
                    f"{schedule_time_string(conflict['time_minutes'])}"
                ),
            )

        now_epoch = int(
            time.time()
        )

        rule = create_schedule_rule(
            normalized,
            now_epoch,
        )
        rule_id = rule["id"]

        log_event(
            source="SYSTEM",
            action="SCHEDULE_RULE_CREATE",
            success=True,
            message=(
                f"Rule #{rule_id}: "
                f"{normalized['action']} "
                f"{schedule_time_string(normalized['time_minutes'])} "
                f"{schedule_days_string(normalized['days_mask'])}"
                +
                (
                    f" - {normalized['comment']}"
                    if normalized["comment"]
                    else ""
                )
            ),
        )

        return {
            "success": True,
            "rule": schedule_rule_dict(
                rule
            ),
        }


    @router.put(
        "/api/schedule/rules/{rule_id}"
    )
    async def api_schedule_rule_update(
        rule_id: int,
        request: Request,
    ):

        ensure_schedule_rules_schema()

        current = get_schedule_rule(
            rule_id
        )

        if not current:
            raise HTTPException(
                status_code=404,
                detail="Schedule rule not found",
            )

        try:
            data = await request.json()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid JSON body",
            )

        normalized = schedule_normalize_input(
            data,
            current=current,
        )

        conflict = schedule_conflicting_rule(
            normalized,
            exclude_id=rule_id,
        )

        if conflict:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Schedule conflict with "
                    f"Rule #{conflict['id']} "
                    f"at "
                    f"{schedule_time_string(conflict['time_minutes'])}"
                ),
            )

        schedule_changed = (
            str(current["action"]) != normalized["action"]
            or int(current["time_minutes"]) != normalized["time_minutes"]
            or int(current["days_mask"]) != normalized["days_mask"]
            or (
                not bool(current["enabled"])
                and normalized["enabled"]
            )
        )

        now_epoch = int(
            time.time()
        )

        effective_from = int(
            current["effective_from"]
            or 0
        )

        if normalized["enabled"] and schedule_changed:
            effective_from = now_epoch

        rule = update_schedule_rule(
            rule_id,
            normalized,
            effective_from,
            now_epoch,
        )

        log_event(
            source="SYSTEM",
            action="SCHEDULE_RULE_UPDATE",
            success=True,
            message=(
                f"Rule #{rule_id}: "
                f"{normalized['action']} "
                f"{schedule_time_string(normalized['time_minutes'])} "
                f"{schedule_days_string(normalized['days_mask'])}"
            ),
        )

        return {
            "success": True,
            "rule": schedule_rule_dict(
                rule
            ),
        }


    @router.post(
        "/api/schedule/rules/{rule_id}/toggle"
    )
    def api_schedule_rule_toggle(
        rule_id: int,
    ):

        ensure_schedule_rules_schema()

        current = get_schedule_rule(
            rule_id
        )

        if not current:
            raise HTTPException(
                status_code=404,
                detail="Schedule rule not found",
            )

        new_enabled = not bool(
            current["enabled"]
        )

        normalized = {
            "enabled": new_enabled,
            "action": current["action"],
            "time_minutes": current["time_minutes"],
            "days_mask": current["days_mask"],
            "scope": current["scope"],
            "comment": current["comment"],
        }

        if new_enabled:
            conflict = schedule_conflicting_rule(
                normalized,
                exclude_id=rule_id,
            )

            if conflict:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Schedule conflict with "
                        f"Rule #{conflict['id']}"
                    ),
                )

        now_epoch = int(
            time.time()
        )

        effective_from = (
            now_epoch
            if new_enabled
            else int(
                current["effective_from"]
                or 0
            )
        )

        rule = set_schedule_rule_enabled(
            rule_id,
            new_enabled,
            effective_from,
            now_epoch,
        )

        log_event(
            source="SYSTEM",
            action=(
                "SCHEDULE_RULE_ENABLE"
                if new_enabled
                else "SCHEDULE_RULE_DISABLE"
            ),
            success=True,
            message=f"Rule #{rule_id}",
        )

        return {
            "success": True,
            "rule": schedule_rule_dict(
                rule
            ),
        }


    @router.delete(
        "/api/schedule/rules/{rule_id}"
    )
    def api_schedule_rule_delete(
        rule_id: int,
    ):

        ensure_schedule_rules_schema()

        current = delete_schedule_rule(
            rule_id
        )

        if not current:
            raise HTTPException(
                status_code=404,
                detail="Schedule rule not found",
            )

        log_event(
            source="SYSTEM",
            action="SCHEDULE_RULE_DELETE",
            success=True,
            message=(
                f"Rule #{rule_id}: "
                f"{current['action']} "
                f"{schedule_time_string(current['time_minutes'])} "
                f"{schedule_days_string(current['days_mask'])}"
            ),
        )

        return {
            "success": True,
            "deleted": rule_id,
        }


    @router.post(
        "/api/scheduler/toggle"
    )
    def scheduler_toggle():

        current = (
            get_setting(
                "scheduler_enabled",
                "0",
            )
            == "1"
        )

        new_value = not current

        set_setting(
            "scheduler_enabled",
            (
                "1"
                if new_value
                else "0"
            ),
        )

        log_event(
            source="SYSTEM",
            action=(
                "SCHEDULER_ON"
                if new_value
                else "SCHEDULER_OFF"
            ),
            success=True,
            message="Global scheduler changed",
        )

        return {
            "scheduler_enabled":
                new_value
        }

    return router


__all__ = (
    "create_scheduler_router",
)
