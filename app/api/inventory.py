"""ASIC inventory and firmware HTTP routes."""

import threading
import time

import config as app_config

from fastapi import APIRouter, HTTPException

from config import (
    BITMAIN_USERNAME,
    BITMAIN_PASSWORD,
    AWESOME_USERNAME,
    AWESOME_PASSWORD,
)
from db import get_miner
from discovery import detect_host
from inventory.repository import (
    set_miner_enabled,
    set_miner_schedule_enabled,
    set_all_schedule_enabled,
    clear_manual_overrides,
    set_miner_manual_driver,
    set_miner_detection_auto,
    set_miner_manual_firmware,
    set_miner_detected_firmware,
    convert_unconfigured_to_stock,
)


def create_inventory_router(
    poll_miner,
    log_event,
):
    router = APIRouter()

    @router.post(
        "/api/miners/{miner_id}/driver"
    )
    def set_driver(
        miner_id: int,
        payload: dict,
    ):

        # Legacy compatibility endpoint.
        # Manual driver selection now means MANUAL lock.

        driver = str(
            payload.get(
                "driver",
                "",
            )
        ).strip()


        if driver not in (
            "unset",
            "awesome",
            "bitmain_stock",
        ):

            raise HTTPException(
                status_code=400,
                detail="Invalid driver",
            )


        miner = get_miner(
            miner_id
        )


        if not miner:

            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )


        if driver == "awesome":

            username = AWESOME_USERNAME
            password = AWESOME_PASSWORD

        elif driver == "bitmain_stock":

            username = BITMAIN_USERNAME
            password = BITMAIN_PASSWORD

        else:

            username = ""
            password = ""


        set_miner_manual_driver(
            miner_id,
            driver,
            username,
            password,
        )


        updated = get_miner(
            miner_id
        )


        log_event(
            source="SYSTEM",
            action="FIRMWARE_MANUAL_DRIVER",
            miner=updated,
            success=True,
            message=(
                f"Manual firmware mode; "
                f"driver={driver}"
            ),
        )


        if driver != "unset":

            threading.Thread(
                target=poll_miner,
                args=(
                    miner_id,
                ),
                daemon=True,
            ).start()


        return {
            "success": True,
            "driver": driver,
            "detection_mode": "MANUAL",
        }

    @router.post(
        "/api/miners/{miner_id}/firmware-settings"
    )
    def firmware_settings(
        miner_id: int,
        payload: dict,
    ):

        miner = get_miner(
            miner_id
        )


        if not miner:

            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )


        mode = str(
            payload.get(
                "detection_mode",
                "AUTO",
            )
        ).strip().upper()


        if mode not in (
            "AUTO",
            "MANUAL",
        ):

            raise HTTPException(
                status_code=400,
                detail=(
                    "Detection mode must be "
                    "AUTO or MANUAL"
                ),
            )


        if mode == "AUTO":

            set_miner_detection_auto(
                miner_id
            )


            updated = get_miner(
                miner_id
            )


            log_event(
                source="SYSTEM",
                action="FIRMWARE_MODE_AUTO",
                miner=updated,
                success=True,
                message=(
                    "Firmware detection mode "
                    "changed to AUTO"
                ),
            )


            return {
                "success": True,
                "detection_mode": "AUTO",
            }


        # --------------------------------------------------------
        # MANUAL
        # --------------------------------------------------------

        driver = str(
            payload.get(
                "driver",
                miner["driver"],
            )
        ).strip()


        if driver not in (
            "unset",
            "awesome",
            "bitmain_stock",
        ):

            raise HTTPException(
                status_code=400,
                detail="Invalid driver",
            )


        model = str(
            payload.get(
                "model",
                miner["model"]
                or "",
            )
            or ""
        ).strip()


        firmware = str(
            payload.get(
                "firmware",
                miner["firmware"]
                or "",
            )
            or ""
        ).strip()


        if len(model) > 120:

            raise HTTPException(
                status_code=400,
                detail="Model is too long",
            )


        if len(firmware) > 160:

            raise HTTPException(
                status_code=400,
                detail="Firmware is too long",
            )


        if driver == "awesome":

            username = AWESOME_USERNAME
            password = AWESOME_PASSWORD

        elif driver == "bitmain_stock":

            username = BITMAIN_USERNAME
            password = BITMAIN_PASSWORD

        else:

            username = ""
            password = ""


        driver_changed = (
            driver
            !=
            miner["driver"]
        )


        set_miner_manual_firmware(
            miner_id,
            driver,
            username,
            password,
            model,
            firmware,
            driver_changed,
        )


        updated = get_miner(
            miner_id
        )


        log_event(
            source="SYSTEM",
            action="FIRMWARE_MODE_MANUAL",
            miner=updated,
            success=True,
            message=(
                f"Manual firmware configuration; "
                f"driver={driver}; "
                f"model={model or '-'}; "
                f"firmware={firmware or '-'}"
            ),
        )


        if driver != "unset":

            threading.Thread(
                target=poll_miner,
                args=(
                    miner_id,
                ),
                daemon=True,
            ).start()


        return {
            "success": True,
            "detection_mode": "MANUAL",
            "driver": driver,
            "model": model or None,
            "firmware": firmware or None,
        }

    @router.post(
        "/api/miners/{miner_id}/firmware-detect"
    )
    def firmware_detect_now(
        miner_id: int,
    ):

        miner = get_miner(
            miner_id
        )


        if not miner:

            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )


        detected = None


        for attempt in range(3):

            try:

                detected = detect_host(
                    miner["ip"]
                )

            except Exception:

                detected = None


            if (
                detected
                and
                detected.get("driver")
                in (
                    "awesome",
                    "bitmain_stock",
                )
            ):
                break


            if attempt < 2:

                time.sleep(
                    0.75
                )


        if (
            not detected
            or
            detected.get("driver")
            not in (
                "awesome",
                "bitmain_stock",
            )
        ):

            raise HTTPException(
                status_code=503,
                detail=(
                    "Firmware could not be "
                    "detected after 3 attempts"
                ),
            )


        driver = detected[
            "driver"
        ]

        model = (
            detected.get(
                "model"
            )
            or miner["model"]
        )

        firmware = (
            detected.get(
                "firmware"
            )
            or miner["firmware"]
        )


        if driver == "awesome":

            username = AWESOME_USERNAME
            password = AWESOME_PASSWORD

        else:

            username = BITMAIN_USERNAME
            password = BITMAIN_PASSWORD


        driver_changed = (
            driver
            !=
            miner["driver"]
        )


        set_miner_detected_firmware(
            miner_id,
            driver,
            username,
            password,
            model,
            firmware,
            driver_changed,
        )


        updated = get_miner(
            miner_id
        )


        log_event(
            source="SYSTEM",
            action="FIRMWARE_AUTO_DETECT",
            miner=updated,
            success=True,
            message=(
                f"Auto-detected "
                f"{driver}; "
                f"model={model or '-'}; "
                f"firmware={firmware or '-'}"
            ),
        )


        threading.Thread(
            target=poll_miner,
            args=(
                miner_id,
            ),
            daemon=True,
        ).start()


        return {
            "success": True,
            "detection_mode": "AUTO",
            "driver": driver,
            "model": model,
            "firmware": firmware,
        }

    @router.post(
        "/api/miners/{miner_id}/enabled"
    )
    def toggle_enabled(
        miner_id: int,
    ):
        miner = get_miner(
            miner_id
        )

        if not miner:
            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )

        new_value = not bool(
            miner["enabled"]
        )

        set_miner_enabled(
            miner_id,
            new_value,
        )

        if new_value:
            threading.Thread(
                target=poll_miner,
                args=(miner_id,),
                daemon=True,
            ).start()

        return {
            "enabled": new_value
        }

    @router.post(
        "/api/miners/{miner_id}/schedule"
    )
    def toggle_schedule(
        miner_id: int,
    ):
        miner = get_miner(
            miner_id
        )

        if not miner:
            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )

        if miner["driver"] == "unset":
            raise HTTPException(
                status_code=400,
                detail="Configure firmware first",
            )

        new_value = not bool(
            miner["schedule_enabled"]
        )

        set_miner_schedule_enabled(
            miner_id,
            new_value,
        )

        return {
            "schedule_enabled": new_value
        }

    @router.post(
        "/api/bulk/unconfigured-stock"
    )
    def unconfigured_to_stock():
        ids = convert_unconfigured_to_stock(
            app_config.BITMAIN_USERNAME,
            app_config.BITMAIN_PASSWORD,
        )

        for miner_id in ids:

            threading.Thread(
                target=poll_miner,
                args=(
                    miner_id,
                ),
                daemon=True,
            ).start()

        return {
            "success": True,
            "updated": len(ids),
        }

    @router.post(
        "/api/schedule/all/{state}"
    )
    def schedule_all(state: str):
        if state not in (
            "on",
            "off",
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid state",
            )

        enabled = state == "on"
        changed = set_all_schedule_enabled(
            enabled
        )

        log_event(
            source="SYSTEM",
            action=(
                "SCHEDULE_ALL_ON"
                if enabled
                else "SCHEDULE_ALL_OFF"
            ),
            success=True,
            message=f"Rows changed: {changed}",
        )

        return {
            "success": True,
            "schedule_enabled": enabled,
            "changed": changed,
        }

    @router.post(
        "/api/overrides/clear"
    )
    def clear_overrides():
        changed = clear_manual_overrides()

        log_event(
            source="SYSTEM",
            action="CLEAR_OVERRIDES",
            success=True,
            message=f"Rows changed: {changed}",
        )

        return {
            "success": True,
            "changed": changed,
        }

    return router
