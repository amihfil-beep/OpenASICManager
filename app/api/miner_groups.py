"""Operational miner-group HTTP routes."""

from fastapi import (
    APIRouter,
    HTTPException,
)

from anomalies.repository import (
    validate_group_policy_removal,
    validate_miner_group_policy_membership,
)
from audit.identity import (
    current_audit_actor,
)
from db import get_miner
from miner_groups.repository import (
    create_group,
    delete_group,
    get_group,
    list_groups,
    rename_group,
    set_miner_group,
)
from miner_groups.service import (
    normalize_group_name,
    normalized_group_key,
)


def _group_dict(row):

    if row is None:
        return None

    return {
        "id": row["id"],
        "name": row["name"],
        "member_count":
            int(
                row["member_count"]
            ),
        "created_by":
            row["created_by"],
        "created_at":
            row["created_at"],
        "updated_by":
            row["updated_by"],
        "updated_at":
            row["updated_at"],
    }


def _audit_miner(miner):

    return {
        "id": miner["id"],
        "ip": miner["ip"],
        "name": miner["name"],
    }


def _validated_name_payload(payload):

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail=(
                "JSON body must be an object"
            ),
        )

    unknown = sorted(
        set(payload) - {"name"}
    )

    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unknown group fields: "
                + ", ".join(unknown)
            ),
        )

    if "name" not in payload:
        raise HTTPException(
            status_code=400,
            detail="Group name is required",
        )

    try:
        name = normalize_group_name(
            payload["name"]
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    return (
        name,
        normalized_group_key(
            name
        ),
    )


def create_miner_group_router(
    log_event,
):
    router = APIRouter()

    @router.get(
        "/api/miner-groups"
    )
    def miner_groups_get():

        return {
            "groups": [
                _group_dict(row)
                for row in list_groups()
            ],
        }

    @router.post(
        "/api/miner-groups"
    )
    def miner_group_create(
        payload: dict,
    ):
        name, normalized_name = (
            _validated_name_payload(
                payload
            )
        )

        actor = (
            current_audit_actor()
        )

        try:
            group = create_group(
                name=name,
                normalized_name=(
                    normalized_name
                ),
                actor=actor,
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            )

        log_event(
            source="MANUAL",
            action="MINER_GROUP_CREATE",
            miner=None,
            success=True,
            message=(
                f"Miner group #{group['id']} "
                f"created by {actor}: "
                f"{group['name']}"
            ),
        )

        return {
            "success": True,
            "group":
                _group_dict(
                    group
                ),
        }

    @router.put(
        "/api/miner-groups/{group_id}"
    )
    def miner_group_update(
        group_id: int,
        payload: dict,
    ):
        name, normalized_name = (
            _validated_name_payload(
                payload
            )
        )

        actor = (
            current_audit_actor()
        )

        try:
            group = rename_group(
                group_id=group_id,
                name=name,
                normalized_name=(
                    normalized_name
                ),
                actor=actor,
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            )

        if group is None:
            raise HTTPException(
                status_code=404,
                detail="Group not found",
            )

        log_event(
            source="MANUAL",
            action="MINER_GROUP_RENAME",
            miner=None,
            success=True,
            message=(
                f"Miner group #{group['id']} "
                f"renamed by {actor}: "
                f"{group['name']}"
            ),
        )

        return {
            "success": True,
            "group":
                _group_dict(
                    group
                ),
        }

    @router.delete(
        "/api/miner-groups/{group_id}"
    )
    def miner_group_delete(
        group_id: int,
    ):
        actor = (
            current_audit_actor()
        )

        if get_group(group_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Group not found",
            )

        try:
            validate_group_policy_removal(
                group_id
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        deleted = delete_group(
            group_id
        )

        if deleted is None:
            raise HTTPException(
                status_code=404,
                detail="Group not found",
            )

        log_event(
            source="MANUAL",
            action="MINER_GROUP_DELETE",
            miner=None,
            success=True,
            message=(
                f"Miner group #{deleted['id']} "
                f"deleted by {actor}: "
                f"{deleted['name']}; "
                f"ungrouped="
                f"{deleted['member_count']}"
            ),
        )

        return {
            "success": True,
            "deleted_group": {
                "id":
                    deleted["id"],
                "name":
                    deleted["name"],
                "ungrouped_members":
                    deleted[
                        "member_count"
                    ],
            },
        }

    @router.put(
        "/api/miners/{miner_id}/group"
    )
    def miner_group_assign(
        miner_id: int,
        payload: dict,
    ):
        if not isinstance(
            payload,
            dict,
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "JSON body must be "
                    "an object"
                ),
            )

        unknown = sorted(
            set(payload) - {"group_id"}
        )

        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unknown membership fields: "
                    + ", ".join(unknown)
                ),
            )

        if "group_id" not in payload:
            raise HTTPException(
                status_code=400,
                detail="group_id is required",
            )

        group_id = (
            payload["group_id"]
        )

        if (
            type(group_id) is not int
            or group_id <= 0
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "group_id must be "
                    "a positive integer"
                ),
            )

        if get_miner(miner_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )

        if get_group(group_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Group not found",
            )

        try:
            validate_miner_group_policy_membership(
                miner_id,
                group_id,
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        result = set_miner_group(
            miner_id=miner_id,
            group_id=group_id,
        )

        if (
            result["status"]
            ==
            "MINER_NOT_FOUND"
        ):
            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )

        if (
            result["status"]
            ==
            "GROUP_NOT_FOUND"
        ):
            raise HTTPException(
                status_code=404,
                detail="Group not found",
            )

        miner = get_miner(
            miner_id
        )

        group = get_group(
            group_id
        )

        if (
            result["status"]
            ==
            "UPDATED"
        ):
            actor = (
                current_audit_actor()
            )

            log_event(
                source="MANUAL",
                action=(
                    "MINER_GROUP_ASSIGN"
                ),
                miner=_audit_miner(
                    miner
                ),
                success=True,
                message=(
                    f"Miner group changed "
                    f"by {actor}: "
                    f"{result['old_group_id']} "
                    f"-> {group_id}"
                ),
            )

        return {
            "success": True,
            "changed": (
                result["status"]
                ==
                "UPDATED"
            ),
            "miner_id":
                miner_id,
            "group":
                _group_dict(
                    group
                ),
        }

    @router.delete(
        "/api/miners/{miner_id}/group"
    )
    def miner_group_clear(
        miner_id: int,
    ):
        if get_miner(miner_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )

        try:
            validate_miner_group_policy_membership(
                miner_id,
                None,
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        result = set_miner_group(
            miner_id=miner_id,
            group_id=None,
        )

        if (
            result["status"]
            ==
            "MINER_NOT_FOUND"
        ):
            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )

        miner = get_miner(
            miner_id
        )

        if (
            result["status"]
            ==
            "UPDATED"
        ):
            actor = (
                current_audit_actor()
            )

            log_event(
                source="MANUAL",
                action="MINER_GROUP_CLEAR",
                miner=_audit_miner(
                    miner
                ),
                success=True,
                message=(
                    f"Miner group cleared "
                    f"by {actor}: "
                    f"{result['old_group_id']} "
                    "-> none"
                ),
            )

        return {
            "success": True,
            "changed": (
                result["status"]
                ==
                "UPDATED"
            ),
            "miner_id":
                miner_id,
            "group": None,
        }

    return router


__all__ = (
    "create_miner_group_router",
)
