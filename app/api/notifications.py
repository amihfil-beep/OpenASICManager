"""Notification HTTP routes."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

import config as app_config

from audit.identity import (
    current_audit_actor,
)
from notifications.policy import (
    apply_notification_policy_patch,
)
from notifications.repository import (
    load_notification_policy,
    save_notification_policy,
)
from notifications.telegram import (
    TELEGRAM_CHAT_ID,
    TELEGRAM_NOTIFICATIONS_ENABLED,
    ASIC_MANAGER_NAME,
    TELEGRAM_EVENT_ACTIONS,
    TELEGRAM_SUMMARY_ENABLED,
    TELEGRAM_SUMMARY_HOUR,
    TELEGRAM_SUMMARY_MINUTE,
    TELEGRAM_SUMMARY_WINDOW_MINUTES,
    telegram_configured,
    telegram_send_message,
    telegram_summary_last_date,
    telegram_transport_health,
)


TIMEZONE_NAME = app_config.TIMEZONE
MOSCOW = ZoneInfo(TIMEZONE_NAME)


async def _notification_json_body(
    request,
):
    try:
        return await request.json()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON body",
        )


def create_notifications_router(
    runtime,
    log_event,
):
    router = APIRouter()

    @router.get(
        "/api/notifications/policy"
    )
    def notifications_policy_get():
        return {
            "provider":
                "telegram",

            "policy":
                load_notification_policy(),

            "farm_summary_independent":
                True,
        }


    @router.put(
        "/api/notifications/policy"
    )
    async def notifications_policy_update(
        request: Request,
    ):
        payload = (
            await _notification_json_body(
                request
            )
        )

        current = (
            load_notification_policy()
        )

        try:
            updated = (
                apply_notification_policy_patch(
                    current,
                    payload,
                )
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        if updated != current:

            saved = (
                save_notification_policy(
                    updated
                )
            )

            changed = [
                field
                for field
                in saved
                if (
                    saved[field]
                    != current[field]
                )
            ]

            actor = (
                current_audit_actor()
            )

            details = ", ".join(
                (
                    f"{field}="
                    +
                    (
                        "enabled"
                        if saved[field]
                        else "disabled"
                    )
                )
                for field in changed
            )

            log_event(
                source="MANUAL",
                action=(
                    "NOTIFICATION_POLICY_UPDATE"
                ),
                miner=None,
                success=True,
                message=(
                    "Telegram incident notification "
                    f"policy updated by {actor}: "
                    + details
                ),
            )

        else:
            saved = current

        return {
            "success":
                True,

            "provider":
                "telegram",

            "policy":
                saved,

            "farm_summary_independent":
                True,
        }


    @router.get("/api/notifications/summary/status")
    def notification_summary_status():
        return {
            "enabled": TELEGRAM_SUMMARY_ENABLED,
            "weekdays": [
                "MON",
                "TUE",
                "WED",
                "THU",
                "FRI",
            ],
            "time": (
                f"{TELEGRAM_SUMMARY_HOUR:02d}:"
                f"{TELEGRAM_SUMMARY_MINUTE:02d}"
            ),
            "timezone": TIMEZONE_NAME,
            "window_minutes": TELEGRAM_SUMMARY_WINDOW_MINUTES,
            "last_sent_date": telegram_summary_last_date(),
        }

    @router.post("/api/notifications/summary/test")
    def notification_summary_test():
        result = runtime.send_farm_summary(
            force=True,
            source="MANUAL_TEST",
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get(
                    "message",
                    "Summary send failed",
                ),
            )

        return result

    @router.get("/api/notifications/health")
    def notifications_health():
        transport = telegram_transport_health()

        return {
            "provider": "telegram",
            "configured": telegram_configured(),
            "enabled": TELEGRAM_NOTIFICATIONS_ENABLED,
            "transport_ok": bool(
                transport.get("ok")
            ),
            "transport": transport,
        }

    @router.get("/api/notifications/status")
    def notifications_status():
        masked_chat = None

        if TELEGRAM_CHAT_ID:
            if len(TELEGRAM_CHAT_ID) <= 4:
                masked_chat = "*" * len(
                    TELEGRAM_CHAT_ID
                )
            else:
                masked_chat = (
                    "*" * (len(TELEGRAM_CHAT_ID) - 4)
                    + TELEGRAM_CHAT_ID[-4:]
                )

        return {
            "provider": "telegram",
            "configured": telegram_configured(),
            "enabled": TELEGRAM_NOTIFICATIONS_ENABLED,
            "chat_id": masked_chat,
            "manager_name": ASIC_MANAGER_NAME,
            "events": sorted(
                TELEGRAM_EVENT_ACTIONS
            ),
        }

    @router.post("/api/notifications/test")
    def notifications_test():
        if not telegram_configured():
            raise HTTPException(
                status_code=400,
                detail="Telegram is not configured",
            )

        message = (
            "✅ OpenASICManager Telegram test\\n"
            "\\n"
            f"Farm: {ASIC_MANAGER_NAME}\\n"
            "Status: notification channel works\\n"
            "Time: "
            + datetime.now(MOSCOW).strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            )
        )

        try:
            success, result = telegram_send_message(
                message,
                force=True,
            )

            return {
                "success": success,
                "message": result,
            }

        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )

    return router


__all__ = (
    "create_notifications_router",
)
