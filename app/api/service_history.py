"""ASIC physical service-history HTTP routes."""

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
from service_history.policy import (
    normalize_service_record,
    service_record_dict,
)
from service_history.repository import (
    SERVICE_WORK_TYPES,
    create_service_record,
    list_service_records,
)


async def _json_body(request):
    try:
        return await request.json()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON body",
        )


def _require_miner(miner_id):
    miner = get_miner(
        miner_id
    )

    if miner is None:
        raise HTTPException(
            status_code=404,
            detail="Miner not found",
        )

    return miner


def create_service_history_router(
    log_event,
):
    router = APIRouter()


    @router.get(
        "/api/miners/{miner_id}/service-history"
    )
    def api_service_history_list(
        miner_id: int,
        limit: int = 100,
    ):
        miner = _require_miner(
            miner_id
        )

        records = [
            service_record_dict(row)
            for row
            in list_service_records(
                miner_id,
                limit,
            )
        ]

        return {
            "miner": {
                "id": miner["id"],
                "name": miner["name"],
                "ip": miner["ip"],
            },
            "work_types": list(
                SERVICE_WORK_TYPES
            ),
            "records": records,
        }


    @router.post(
        "/api/miners/{miner_id}/service-history"
    )
    async def api_service_history_create(
        miner_id: int,
        request: Request,
    ):
        miner = _require_miner(
            miner_id
        )

        payload = await _json_body(
            request
        )

        now = int(
            time.time()
        )

        try:
            normalized = (
                normalize_service_record(
                    payload,
                    now,
                )
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )


        actor = (
            current_audit_actor()
        )


        row = create_service_record(
            miner_id=miner_id,
            performed_at=(
                normalized[
                    "performed_at"
                ]
            ),
            work_type=(
                normalized[
                    "work_type"
                ]
            ),
            description=(
                normalized[
                    "description"
                ]
            ),
            part_serial=(
                normalized[
                    "part_serial"
                ]
            ),
            performed_by=(
                normalized[
                    "performed_by"
                ]
            ),
            created_by=actor,
            created_at=now,
        )


        item = service_record_dict(
            row
        )


        message = (
            f"Service record #{item['id']} "
            f"{item['work_type']} "
            f"performed_at="
            f"{item['performed_at']} "
            f"performed_by="
            f"{item['performed_by']}"
        )

        if item["part_serial"]:
            message += (
                " part="
                + item["part_serial"]
            )


        log_event(
            source="MANUAL",
            action="SERVICE_RECORD_CREATE",
            miner=miner,
            success=True,
            message=message,
        )


        return {
            "success": True,
            "record": item,
        }


    return router


__all__ = (
    "create_service_history_router",
)
