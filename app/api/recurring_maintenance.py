"""Recurring maintenance rule HTTP routes."""

from datetime import datetime
import time

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from audit.identity import current_audit_actor
from recurring_maintenance.lifecycle import (
    occurrence_history_item,
)
from recurring_maintenance.lifecycle_repository import (
    list_occurrence_history,
)
from recurring_maintenance.policy import (
    TIMEZONE,
    TIMEZONE_NAME,
    next_occurrence,
    normalize_recurring_rule,
)
from recurring_maintenance.repository import (
    RecurringTargetNotFoundError,
    create_recurring_rule,
    delete_recurring_rule,
    get_recurring_rule,
    list_recurring_rules,
    update_recurring_rule,
)
from recurring_maintenance.service import (
    materializer_health_snapshot,
)


async def _json_body(request):
    try:
        return await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON body",
        )


def _rule_dict(row, now):
    next_run = next_occurrence(row, now)

    return {
        "id": int(row["id"]),
        "enabled": bool(row["enabled"]),
        "scope": row["scope"],
        "miner_id": row["miner_id"],
        "miner_name": row["miner_name"],
        "miner_ip": row["miner_ip"],
        "group_id": row["group_id"],
        "group_name": row["group_name"],
        "days_mask": int(row["days_mask"]),
        "start_minutes": int(row["start_minutes"]),
        "duration_minutes": int(row["duration_minutes"]),
        "note": row["note"],
        "created_by": row["created_by"],
        "created_at": int(row["created_at"]),
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
        "next_occurrence": (
            next_run.isoformat()
            if next_run is not None
            else None
        ),
    }


def _target_text(item):
    if item["scope"] == "MINER":
        return f"MINER #{item['miner_id']}"

    if item["scope"] == "GROUP":
        return f"GROUP #{item['group_id']}"

    return "FARM"


def create_recurring_maintenance_router(log_event):
    router = APIRouter()

    @router.get("/api/maintenance/recurring")
    def api_recurring_list():
        now = datetime.now(TIMEZONE)

        return {
            "timezone": TIMEZONE_NAME,
            "now": now.isoformat(),
            "rules": [
                _rule_dict(row, now)
                for row in list_recurring_rules()
            ],
            "materializer": materializer_health_snapshot(),
        }

    @router.get("/api/maintenance/recurring/history")
    def api_recurring_history(
        limit: int = 200,
    ):
        limit = max(
            1,
            min(int(limit), 500),
        )
        now = int(time.time())

        return {
            "timezone": TIMEZONE_NAME,
            "now": datetime.fromtimestamp(
                now,
                TIMEZONE,
            ).isoformat(),
            "materializer": materializer_health_snapshot(),
            "occurrences": [
                occurrence_history_item(
                    row,
                    now,
                )
                for row in list_occurrence_history(
                    limit
                )
            ],
        }

    @router.post("/api/maintenance/recurring")
    async def api_recurring_create(request: Request):
        payload = await _json_body(request)
        now_epoch = int(time.time())

        try:
            normalized = normalize_recurring_rule(payload)
            row = create_recurring_rule(
                normalized=normalized,
                actor=current_audit_actor(),
                now=now_epoch,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )
        except RecurringTargetNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            )

        item = _rule_dict(
            row,
            datetime.now(TIMEZONE),
        )

        log_event(
            source="MANUAL",
            action="MAINTENANCE_RECURRING_CREATE",
            success=True,
            message=(
                f"Recurring maintenance rule #{item['id']} "
                f"{_target_text(item)} created"
            ),
        )

        return {
            "success": True,
            "rule": item,
        }

    @router.put("/api/maintenance/recurring/{rule_id}")
    async def api_recurring_update(
        rule_id: int,
        request: Request,
    ):
        current = get_recurring_rule(rule_id)
        if current is None:
            raise HTTPException(
                status_code=404,
                detail="Recurring maintenance rule not found",
            )

        payload = await _json_body(request)
        now_epoch = int(time.time())

        try:
            normalized = normalize_recurring_rule(
                payload,
                current=current,
            )
            row = update_recurring_rule(
                rule_id=rule_id,
                normalized=normalized,
                actor=current_audit_actor(),
                now=now_epoch,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )
        except RecurringTargetNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            )

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Recurring maintenance rule not found",
            )

        item = _rule_dict(
            row,
            datetime.now(TIMEZONE),
        )

        log_event(
            source="MANUAL",
            action="MAINTENANCE_RECURRING_UPDATE",
            success=True,
            message=(
                f"Recurring maintenance rule #{item['id']} "
                f"{_target_text(item)} updated"
            ),
        )

        return {
            "success": True,
            "rule": item,
        }

    @router.delete("/api/maintenance/recurring/{rule_id}")
    def api_recurring_delete(rule_id: int):
        current = get_recurring_rule(rule_id)
        if current is None:
            raise HTTPException(
                status_code=404,
                detail="Recurring maintenance rule not found",
            )

        actor = current_audit_actor()
        deleted = delete_recurring_rule(
            rule_id=rule_id,
            actor=actor,
            now=int(time.time()),
        )

        if not deleted:
            raise HTTPException(
                status_code=404,
                detail="Recurring maintenance rule not found",
            )

        log_event(
            source="MANUAL",
            action="MAINTENANCE_RECURRING_DELETE",
            success=True,
            message=(
                f"Recurring maintenance rule #{rule_id} deleted"
            ),
        )

        return {
            "success": True,
            "rule_id": int(rule_id),
        }

    return router


__all__ = (
    "create_recurring_maintenance_router",
)
