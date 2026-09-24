"""Per-miner anomaly policy override HTTP routes."""

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from anomalies.policy import (
    MINER_ANOMALY_OVERRIDE_FIELDS,
    anomaly_policy_sources,
    apply_anomaly_override_patch,
    resolve_anomaly_policy,
)
from anomalies.repository import (
    clear_miner_anomaly_overrides,
    load_anomaly_policy,
    load_miner_anomaly_overrides,
    save_miner_anomaly_overrides,
)
from audit.identity import (
    current_audit_actor,
)
from db import get_miner


async def _json_body(request):
    try:
        return await request.json()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON body",
        )


def _miner_or_404(miner_id):
    miner = get_miner(
        miner_id
    )

    if miner is None:
        raise HTTPException(
            status_code=404,
            detail="Miner not found",
        )

    return miner


def _policy_response(miner_id):
    global_policy = (
        load_anomaly_policy()
    )

    overrides = (
        load_miner_anomaly_overrides(
            miner_id,
            global_policy=global_policy,
        )
    )

    effective = (
        resolve_anomaly_policy(
            global_policy,
            overrides,
        )
    )

    return {
        "miner_id":
            int(miner_id),

        "override_fields":
            list(
                MINER_ANOMALY_OVERRIDE_FIELDS
            ),

        "global_policy":
            global_policy,

        "overrides":
            overrides,

        "effective_policy":
            effective,

        "sources":
            anomaly_policy_sources(
                overrides
            ),
    }


def create_anomaly_override_router(
    log_event,
):
    router = APIRouter()

    @router.get(
        "/api/miners/{miner_id}/anomaly-policy"
    )
    def api_miner_anomaly_policy_get(
        miner_id: int,
    ):
        _miner_or_404(
            miner_id
        )

        return _policy_response(
            miner_id
        )

    @router.put(
        "/api/miners/{miner_id}/anomaly-policy"
    )
    async def api_miner_anomaly_policy_update(
        miner_id: int,
        request: Request,
    ):
        miner = _miner_or_404(
            miner_id
        )

        payload = await _json_body(
            request
        )

        global_policy = (
            load_anomaly_policy()
        )

        previous = (
            load_miner_anomaly_overrides(
                miner_id,
                global_policy=global_policy,
            )
        )

        try:
            overrides, _effective = (
                apply_anomaly_override_patch(
                    global_policy,
                    previous,
                    payload,
                )
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        if overrides != previous:

            actor = (
                current_audit_actor()
            )

            save_miner_anomaly_overrides(
                miner_id=miner_id,
                overrides=overrides,
                actor=actor,
            )

            changed = sorted(
                field
                for field
                in (
                    set(previous)
                    |
                    set(overrides)
                )
                if previous.get(field)
                != overrides.get(field)
            )

            details = []

            for field in changed:

                if field in overrides:
                    details.append(
                        f"{field}="
                        f"{overrides[field]}"
                    )

                else:
                    details.append(
                        f"{field}=INHERIT"
                    )

            action = (
                "ANOMALY_POLICY_OVERRIDE_CLEAR"
                if not overrides
                else
                "ANOMALY_POLICY_OVERRIDE_UPDATE"
            )

            log_event(
                source="MANUAL",
                action=action,
                miner=miner,
                success=True,
                message=(
                    "Per-miner anomaly policy: "
                    + ", ".join(details)
                ),
            )

        return {
            "success": True,
            **_policy_response(
                miner_id
            ),
        }

    @router.delete(
        "/api/miners/{miner_id}/anomaly-policy"
    )
    def api_miner_anomaly_policy_clear(
        miner_id: int,
    ):
        miner = _miner_or_404(
            miner_id
        )

        global_policy = (
            load_anomaly_policy()
        )

        previous = (
            load_miner_anomaly_overrides(
                miner_id,
                global_policy=global_policy,
            )
        )

        if previous:

            clear_miner_anomaly_overrides(
                miner_id
            )

            log_event(
                source="MANUAL",
                action=(
                    "ANOMALY_POLICY_OVERRIDE_CLEAR"
                ),
                miner=miner,
                success=True,
                message=(
                    "All per-miner anomaly "
                    "policy overrides cleared"
                ),
            )

        return {
            "success": True,
            **_policy_response(
                miner_id
            ),
        }

    return router


__all__ = (
    "create_anomaly_override_router",
)
