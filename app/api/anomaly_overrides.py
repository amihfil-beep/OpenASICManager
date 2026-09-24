"""Layered anomaly policy override HTTP routes."""

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from anomalies.policy import (
    MINER_ANOMALY_OVERRIDE_FIELDS,
    apply_anomaly_override_patch,
    apply_group_anomaly_override_patch,
    layered_anomaly_policy_sources,
    resolve_layered_anomaly_policy,
)
from anomalies.repository import (
    clear_group_anomaly_overrides,
    clear_miner_anomaly_overrides,
    load_anomaly_override_snapshot,
    load_anomaly_policy,
    load_group_anomaly_overrides,
    load_miner_anomaly_overrides,
    save_group_anomaly_overrides,
    save_miner_anomaly_overrides,
)
from audit.identity import (
    current_audit_actor,
)
from db import get_miner
from miner_groups.repository import (
    get_group,
)


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


def _group_or_404(group_id):
    group = get_group(
        group_id
    )

    if group is None:
        raise HTTPException(
            status_code=404,
            detail="Group not found",
        )

    return group


def _group_policy_response(
    group_id,
):
    group = _group_or_404(
        group_id
    )

    global_policy = (
        load_anomaly_policy()
    )

    overrides = (
        load_group_anomaly_overrides(
            group_id,
            global_policy=global_policy,
        )
    )

    effective = (
        resolve_layered_anomaly_policy(
            global_policy,
            group_overrides=overrides,
            miner_overrides={},
        )
    )

    return {
        "group_id":
            int(group_id),

        "group_name":
            group["name"],

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
            layered_anomaly_policy_sources(
                overrides,
                {},
            ),
    }


def _policy_response(miner_id):
    miner = _miner_or_404(
        miner_id
    )

    global_policy = (
        load_anomaly_policy()
    )

    group_id = (
        miner["group_id"]
    )

    group = (
        get_group(
            group_id
        )
        if group_id is not None
        else None
    )

    group_overrides = (
        load_group_anomaly_overrides(
            group_id,
            global_policy=global_policy,
        )
        if group_id is not None
        else {}
    )

    inherited_policy = (
        resolve_layered_anomaly_policy(
            global_policy,
            group_overrides=group_overrides,
            miner_overrides={},
        )
    )

    overrides = (
        load_miner_anomaly_overrides(
            miner_id,
            global_policy=global_policy,
            group_overrides=(
                group_overrides
            ),
        )
    )

    effective = (
        resolve_layered_anomaly_policy(
            global_policy,
            group_overrides=group_overrides,
            miner_overrides=overrides,
        )
    )

    return {
        "miner_id":
            int(miner_id),

        "group_id":
            (
                int(group_id)
                if group_id is not None
                else None
            ),

        "group_name":
            (
                group["name"]
                if group is not None
                else None
            ),

        "override_fields":
            list(
                MINER_ANOMALY_OVERRIDE_FIELDS
            ),

        "global_policy":
            global_policy,

        "group_overrides":
            group_overrides,

        "group_policy":
            inherited_policy,

        "overrides":
            overrides,

        "effective_policy":
            effective,

        "sources":
            layered_anomaly_policy_sources(
                group_overrides,
                overrides,
            ),
    }


def _changed_details(
    previous,
    current,
):
    changed = sorted(
        field
        for field
        in (
            set(previous)
            |
            set(current)
        )
        if previous.get(field)
        != current.get(field)
    )

    details = []

    for field in changed:

        if field in current:
            details.append(
                f"{field}="
                f"{current[field]}"
            )

        else:
            details.append(
                f"{field}=INHERIT"
            )

    return details


def create_anomaly_override_router(
    log_event,
):
    router = APIRouter()

    @router.get(
        "/api/anomaly-policy-overrides"
    )
    def api_anomaly_policy_override_summary():
        global_policy = (
            load_anomaly_policy()
        )

        snapshot = (
            load_anomaly_override_snapshot(
                global_policy
            )
        )

        return {
            "miners": [
                {
                    "miner_id":
                        int(miner_id),

                    "override_count":
                        len(overrides),

                    "override_fields":
                        sorted(
                            overrides.keys()
                        ),
                }
                for miner_id, overrides
                in sorted(
                    snapshot.items()
                )
            ]
        }

    @router.get(
        "/api/miner-groups/"
        "{group_id}/anomaly-policy"
    )
    def api_group_anomaly_policy_get(
        group_id: int,
    ):
        return _group_policy_response(
            group_id
        )

    @router.put(
        "/api/miner-groups/"
        "{group_id}/anomaly-policy"
    )
    async def api_group_anomaly_policy_update(
        group_id: int,
        request: Request,
    ):
        group = _group_or_404(
            group_id
        )

        payload = await _json_body(
            request
        )

        global_policy = (
            load_anomaly_policy()
        )

        previous = (
            load_group_anomaly_overrides(
                group_id,
                global_policy=global_policy,
            )
        )

        try:
            overrides, _effective = (
                apply_group_anomaly_override_patch(
                    global_policy,
                    previous,
                    payload,
                )
            )

            if overrides != previous:

                actor = (
                    current_audit_actor()
                )

                save_group_anomaly_overrides(
                    group_id=group_id,
                    overrides=overrides,
                    actor=actor,
                )

                details = _changed_details(
                    previous,
                    overrides,
                )

                action = (
                    "ANOMALY_POLICY_GROUP_CLEAR"
                    if not overrides
                    else
                    "ANOMALY_POLICY_GROUP_UPDATE"
                )

                log_event(
                    source="MANUAL",
                    action=action,
                    miner=None,
                    success=True,
                    message=(
                        f"Group #{group_id} "
                        f"{group['name']} anomaly policy: "
                        + ", ".join(details)
                    ),
                )

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        return {
            "success": True,
            **_group_policy_response(
                group_id
            ),
        }

    @router.delete(
        "/api/miner-groups/"
        "{group_id}/anomaly-policy"
    )
    def api_group_anomaly_policy_clear(
        group_id: int,
    ):
        group = _group_or_404(
            group_id
        )

        global_policy = (
            load_anomaly_policy()
        )

        previous = (
            load_group_anomaly_overrides(
                group_id,
                global_policy=global_policy,
            )
        )

        if previous:

            try:
                clear_group_anomaly_overrides(
                    group_id
                )

            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=str(exc),
                )

            log_event(
                source="MANUAL",
                action=(
                    "ANOMALY_POLICY_GROUP_CLEAR"
                ),
                miner=None,
                success=True,
                message=(
                    f"All anomaly policy overrides "
                    f"cleared for group #{group_id} "
                    f"{group['name']}"
                ),
            )

        return {
            "success": True,
            **_group_policy_response(
                group_id
            ),
        }

    @router.get(
        "/api/miners/{miner_id}/anomaly-policy"
    )
    def api_miner_anomaly_policy_get(
        miner_id: int,
    ):
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

        group_id = (
            miner["group_id"]
        )

        group_overrides = (
            load_group_anomaly_overrides(
                group_id,
                global_policy=global_policy,
            )
            if group_id is not None
            else {}
        )

        inherited_policy = (
            resolve_layered_anomaly_policy(
                global_policy,
                group_overrides=group_overrides,
                miner_overrides={},
            )
        )

        previous = (
            load_miner_anomaly_overrides(
                miner_id,
                global_policy=global_policy,
                group_overrides=(
                    group_overrides
                ),
            )
        )

        try:
            overrides, _effective = (
                apply_anomaly_override_patch(
                    inherited_policy,
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

            try:
                save_miner_anomaly_overrides(
                    miner_id=miner_id,
                    overrides=overrides,
                    actor=actor,
                )

            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=str(exc),
                )

            details = _changed_details(
                previous,
                overrides,
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

        group_id = (
            miner["group_id"]
        )

        group_overrides = (
            load_group_anomaly_overrides(
                group_id,
                global_policy=global_policy,
            )
            if group_id is not None
            else {}
        )

        previous = (
            load_miner_anomaly_overrides(
                miner_id,
                global_policy=global_policy,
                group_overrides=(
                    group_overrides
                ),
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
