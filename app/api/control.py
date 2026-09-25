"""ASIC control HTTP routes."""

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from control.bulk import (
    BulkControlValidationError,
    bulk_control_preview,
    execute_bulk_control,
)
from db import get_control_miners


async def _json_body(request):
    try:
        return await request.json()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON body",
        )


def create_control_router(
    queue_control,
    queue_reboot,
    log_event=None,
):
    router = APIRouter()

    if log_event is None:
        log_event = (
            lambda **kwargs:
                None
        )


    @router.post(
        "/api/miners/{miner_id}/control/{action}"
    )
    def miner_action(
        miner_id: int,
        action: str,
    ):

        if action not in (
            "pause",
            "resume",
        ):

            raise HTTPException(
                status_code=400,
                detail="Invalid action",
            )


        try:

            result = queue_control(
                miner_id,
                action,
                manual=True,
            )


            return {
                "success": True,
                **result,
            }


        except Exception as exc:

            raise HTTPException(
                status_code=500,
                detail=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )


    @router.post(
        "/api/all/{action}"
    )
    def all_action(action: str):

        # Legacy farm-wide route retained for compatibility.
        #
        # The new bounded /api/control/bulk API deliberately
        # does not provide an ALL selector.
        if action not in (
            "pause",
            "resume",
        ):

            raise HTTPException(
                status_code=400,
                detail="Invalid action",
            )


        miners = (
            get_control_miners()
        )


        results = []


        for miner in miners:

            try:

                result = queue_control(
                    miner["id"],
                    action,
                    manual=True,
                )

                results.append({
                    "ip":
                        miner["ip"],

                    "success":
                        True,

                    **result,
                })


            except Exception as exc:

                results.append({
                    "ip":
                        miner["ip"],

                    "success":
                        False,

                    "error":
                        str(exc),
                })


        return {
            "results": results
        }


    @router.post(
        "/api/miners/{miner_id}/reboot"
    )
    def api_miner_reboot(
        miner_id: int,
    ):

        try:

            result = queue_reboot(
                miner_id
            )


            return {
                "success":
                    True,

                **result,
            }


        except Exception as exc:

            raise HTTPException(
                status_code=500,
                detail=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )


    @router.post(
        "/api/control/bulk/preview/{action}"
    )
    async def api_bulk_control_preview(
        action: str,
        request: Request,
    ):

        payload = await _json_body(
            request
        )


        try:

            return {
                "success":
                    True,

                **bulk_control_preview(
                    action,
                    payload,
                ),
            }


        except BulkControlValidationError as exc:

            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )


    @router.post(
        "/api/control/bulk/{action}"
    )
    async def api_bulk_control_execute(
        action: str,
        request: Request,
    ):

        payload = await _json_body(
            request
        )


        try:

            result = execute_bulk_control(
                action=action,
                payload=payload,
                queue_control=queue_control,
                queue_reboot=queue_reboot,
                log_event=log_event,
            )


        except BulkControlValidationError as exc:

            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )


        return {
            "success":
                True,

            **result,
        }


    return router
