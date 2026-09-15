"""Anomaly policy HTTP routes."""

from fastapi import APIRouter, HTTPException, Request

from anomalies.policy import (
    DEFAULT_ANOMALY_POLICY,
    normalize_anomaly_policy,
)
from anomalies.repository import (
    load_anomaly_policy,
    save_anomaly_policy,
)


def create_anomaly_router(log_event):
    router = APIRouter()

    @router.get(
        "/api/anomaly-policy"
    )
    def api_anomaly_policy_get():
        return {
            "policy": load_anomaly_policy(),
            "defaults": dict(
                DEFAULT_ANOMALY_POLICY
            ),
        }

    @router.put(
        "/api/anomaly-policy"
    )
    async def api_anomaly_policy_update(
        request: Request,
    ):
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid JSON body",
            )

        try:
            normalized = normalize_anomaly_policy(
                data
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        previous = load_anomaly_policy()
        saved = save_anomaly_policy(
            normalized
        )

        if saved != previous:
            log_event(
                source="SYSTEM",
                action="ANOMALY_POLICY_UPDATE",
                success=True,
                message=(
                    "Anomaly policy updated: "
                    f"interval={saved['interval_seconds']}s; "
                    f"offline_grace={saved['offline_grace_seconds']}s; "
                    f"hot={saved['hot_temp_c']:.1f}C; "
                    f"hot_clear={saved['hot_clear_c']:.1f}C; "
                    f"hot_grace={saved['hot_grace_seconds']}s; "
                    f"schedule_grace={saved['schedule_grace_seconds']}s"
                ),
            )

        return {
            "success": True,
            "policy": saved,
        }

    return router


__all__ = (
    "create_anomaly_router",
)
