"""Maintenance-window HTTP routes."""

import time

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from audit.identity import (
    current_audit_actor,
)
from db import get_miner
from maintenance_windows.policy import (
    TIMEZONE_NAME,
    maintenance_status,
    maintenance_window_dict,
    normalize_maintenance_create,
    normalize_maintenance_extend,
)
from maintenance_windows.repository import (
    create_maintenance_window,
    end_maintenance_window,
    extend_maintenance_window,
    find_maintenance_conflict,
    get_maintenance_window,
    list_maintenance_windows,
)


async def _json_body(request):
    try:
        return await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON body",
        )


def _scope_miner(normalized):
    if normalized["scope"] != "MINER":
        return None

    miner = get_miner(
        normalized["miner_id"]
    )

    if miner is None:
        raise HTTPException(
            status_code=404,
            detail="Miner not found",
        )

    return miner


def create_maintenance_router(
    log_event,
):
    router = APIRouter()

    @router.get(
        "/api/maintenance"
    )
    def api_maintenance_list(
        limit: int = 100,
    ):
        now = int(
            time.time()
        )

        windows = [
            maintenance_window_dict(
                row,
                now,
            )
            for row
            in list_maintenance_windows(
                limit
            )
        ]

        return {
            "timezone": TIMEZONE_NAME,
            "now": now,
            "active": [
                item
                for item in windows
                if item["status"] == "ACTIVE"
            ],
            "scheduled": [
                item
                for item in windows
                if item["status"] == "SCHEDULED"
            ],
            "recent": [
                item
                for item in windows
                if item["status"] in (
                    "ENDED",
                    "EXPIRED",
                )
            ],
        }

    @router.post(
        "/api/maintenance"
    )
    async def api_maintenance_create(
        request: Request,
    ):
        now = int(
            time.time()
        )

        payload = await _json_body(
            request
        )

        try:
            normalized = (
                normalize_maintenance_create(
                    payload,
                    now,
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        miner = _scope_miner(
            normalized
        )

        conflict = (
            find_maintenance_conflict(
                scope=normalized["scope"],
                miner_id=normalized["miner_id"],
                starts_at=normalized["starts_at"],
                ends_at=normalized["ends_at"],
            )
        )

        if conflict is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Maintenance window overlaps "
                    f"window #{conflict['id']}"
                ),
            )

        actor = current_audit_actor()

        row = create_maintenance_window(
            normalized=normalized,
            actor=actor,
            now=now,
        )

        item = maintenance_window_dict(
            row,
            now,
        )

        log_event(
            source="MANUAL",
            action="MAINTENANCE_CREATE",
            miner=miner,
            success=True,
            message=(
                f"Maintenance #{item['id']} "
                f"{item['scope']} "
                f"{item['starts_at_iso']} -> "
                f"{item['ends_at_iso']}"
            ),
        )

        return {
            "success": True,
            "maintenance": item,
        }

    @router.put(
        "/api/maintenance/{window_id}"
    )
    async def api_maintenance_extend(
        window_id: int,
        request: Request,
    ):
        now = int(
            time.time()
        )

        current = get_maintenance_window(
            window_id
        )

        if current is None:
            raise HTTPException(
                status_code=404,
                detail="Maintenance window not found",
            )

        payload = await _json_body(
            request
        )

        try:
            normalized = (
                normalize_maintenance_extend(
                    payload,
                    current,
                    now,
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        conflict = find_maintenance_conflict(
            scope=current["scope"],
            miner_id=current["miner_id"],
            starts_at=current["starts_at"],
            ends_at=normalized["ends_at"],
            exclude_id=current["id"],
        )

        if conflict is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Extended maintenance overlaps "
                    f"window #{conflict['id']}"
                ),
            )

        actor = current_audit_actor()

        row = extend_maintenance_window(
            window_id=window_id,
            normalized=normalized,
            actor=actor,
            now=now,
        )

        if row is None:
            raise HTTPException(
                status_code=409,
                detail="Maintenance window cannot be extended",
            )

        miner = (
            get_miner(row["miner_id"])
            if row["scope"] == "MINER"
            else None
        )

        item = maintenance_window_dict(
            row,
            now,
        )

        log_event(
            source="MANUAL",
            action="MAINTENANCE_EXTEND",
            miner=miner,
            success=True,
            message=(
                f"Maintenance #{item['id']} "
                f"extended to {item['ends_at_iso']}"
            ),
        )

        return {
            "success": True,
            "maintenance": item,
        }

    @router.post(
        "/api/maintenance/{window_id}/end"
    )
    def api_maintenance_end(
        window_id: int,
    ):
        now = int(
            time.time()
        )

        current = get_maintenance_window(
            window_id
        )

        if current is None:
            raise HTTPException(
                status_code=404,
                detail="Maintenance window not found",
            )

        status = maintenance_status(
            current,
            now,
        )

        if status in (
            "ENDED",
            "EXPIRED",
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Maintenance window is already "
                    + status.lower()
                ),
            )

        actor = current_audit_actor()

        row = end_maintenance_window(
            window_id=window_id,
            actor=actor,
            now=now,
        )

        if row is None:
            raise HTTPException(
                status_code=409,
                detail="Maintenance window cannot be ended",
            )

        miner = (
            get_miner(row["miner_id"])
            if row["scope"] == "MINER"
            else None
        )

        item = maintenance_window_dict(
            row,
            now,
        )

        log_event(
            source="MANUAL",
            action="MAINTENANCE_END",
            miner=miner,
            success=True,
            message=(
                f"Maintenance #{item['id']} ended"
            ),
        )

        return {
            "success": True,
            "maintenance": item,
        }

    return router


__all__ = (
    "create_maintenance_router",
)
