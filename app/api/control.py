"""ASIC control HTTP routes."""

from fastapi import APIRouter, HTTPException

from db import get_control_miners


def create_control_router(
    queue_control,
    queue_reboot,
):
    router = APIRouter()

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

    return router
