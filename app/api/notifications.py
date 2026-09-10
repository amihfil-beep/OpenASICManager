"""Notification HTTP routes."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

import config as app_config
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


def create_notifications_router(runtime):
    router = APIRouter()

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
