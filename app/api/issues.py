"""Operator write actions for anomaly issues."""

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from anomalies.analytics import issue_dict
from anomalies.issues import (
    normalize_acknowledgement_note,
)
from anomalies.repository import (
    clear_issue_acknowledgement,
    set_issue_acknowledgement,
)
from audit.identity import current_audit_actor


def _issue_miner(issue):
    return {
        "id": issue["miner_id"],
        "ip": issue["ip"],
        "name": issue["name"],
    }


def create_issue_router(log_event):
    router = APIRouter()

    @router.put(
        "/api/issues/{issue_id}/acknowledgement"
    )
    async def api_issue_acknowledge(
        issue_id: int,
        request: Request,
    ):
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid JSON body",
            )

        if not isinstance(data, dict):
            raise HTTPException(
                status_code=400,
                detail="JSON body must be an object",
            )

        unknown = sorted(
            set(data) - {"note"}
        )

        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unknown acknowledgement fields: "
                    + ", ".join(unknown)
                ),
            )

        try:
            note = normalize_acknowledgement_note(
                data.get("note")
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        actor = current_audit_actor()

        issue = set_issue_acknowledgement(
            issue_id=issue_id,
            actor=actor,
            note=note,
        )

        if issue is None:
            raise HTTPException(
                status_code=404,
                detail="Issue not found",
            )

        log_event(
            source="MANUAL",
            action="ISSUE_ACKNOWLEDGE",
            miner=_issue_miner(issue),
            success=True,
            message=(
                f"Issue #{issue['id']} acknowledged"
            ),
        )

        return {
            "success": True,
            "issue": issue_dict(issue),
        }

    @router.delete(
        "/api/issues/{issue_id}/acknowledgement"
    )
    def api_issue_unacknowledge(
        issue_id: int,
    ):
        issue = clear_issue_acknowledgement(
            issue_id
        )

        if issue is None:
            raise HTTPException(
                status_code=404,
                detail="Issue not found",
            )

        log_event(
            source="MANUAL",
            action="ISSUE_UNACKNOWLEDGE",
            miner=_issue_miner(issue),
            success=True,
            message=(
                f"Issue #{issue['id']} acknowledgement cleared"
            ),
        )

        return {
            "success": True,
            "issue": issue_dict(issue),
        }

    return router


__all__ = (
    "create_issue_router",
)
