"""Saved discovery-network profile HTTP routes."""

from fastapi import APIRouter, HTTPException

from discovery_profiles.service import (
    MAX_MULTI_SCAN_NETWORKS,
    create_saved_profile,
    delete_saved_profile,
    list_saved_profiles,
    scan_saved_profiles,
    update_saved_profile,
)
from inventory.repository import list_discovery_miners


def _decorate_managed_devices(result):
    managed = {
        row["ip"]: row
        for row in list_discovery_miners()
    }

    devices = []
    managed_count = 0
    new_known = 0
    new_unknown = 0

    for device in result.get("devices", []):
        item = dict(device)
        existing = managed.get(item.get("ip"))

        if existing:
            item["managed"] = True
            item["existing_id"] = existing["id"]
            item["existing_name"] = existing["name"]
            item["existing_driver"] = existing["driver"]
            managed_count += 1
        else:
            item["managed"] = False
            item["existing_id"] = None
            item["existing_name"] = None
            item["existing_driver"] = None

            if item.get("driver") in (
                "awesome",
                "bitmain_stock",
            ):
                new_known += 1
            else:
                new_unknown += 1

        devices.append(item)

    decorated = dict(result)
    decorated["devices"] = devices
    decorated["managed_count"] = managed_count
    decorated["new_known"] = new_known
    decorated["new_unknown"] = new_unknown
    return decorated


def create_discovery_profiles_router(log_event):
    router = APIRouter()

    @router.get("/api/discovery/profiles")
    def discovery_profiles_list():
        return {
            "profiles": list_saved_profiles(),
            "max_scan_networks": MAX_MULTI_SCAN_NETWORKS,
        }

    @router.post("/api/discovery/profiles")
    def discovery_profiles_create(payload: dict):
        try:
            profile = create_saved_profile(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        log_event(
            source="SYSTEM",
            action="DISCOVERY_PROFILE_CREATE",
            success=True,
            message=(
                f"{profile['name']} "
                f"{profile['network']} "
                f"enabled={profile['enabled']}"
            ),
        )

        return {
            "success": True,
            "profile": profile,
        }

    @router.put("/api/discovery/profiles/{profile_id}")
    def discovery_profiles_update(
        profile_id: int,
        payload: dict,
    ):
        try:
            profile = update_saved_profile(
                profile_id,
                payload,
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        log_event(
            source="SYSTEM",
            action="DISCOVERY_PROFILE_UPDATE",
            success=True,
            message=(
                f"{profile['name']} "
                f"{profile['network']} "
                f"enabled={profile['enabled']}"
            ),
        )

        return {
            "success": True,
            "profile": profile,
        }

    @router.delete("/api/discovery/profiles/{profile_id}")
    def discovery_profiles_delete(profile_id: int):
        try:
            profile = delete_saved_profile(profile_id)
        except LookupError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            )

        log_event(
            source="SYSTEM",
            action="DISCOVERY_PROFILE_DELETE",
            success=True,
            message=(
                f"{profile['name']} "
                f"{profile['network']}"
            ),
        )

        return {
            "success": True,
            "profile": profile,
        }

    @router.post("/api/discovery/profiles/scan")
    def discovery_profiles_scan(payload: dict):
        profile_ids = payload.get("profile_ids")

        try:
            result = scan_saved_profiles(
                profile_ids=profile_ids
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Discovery profile scan failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

        return _decorate_managed_devices(result)

    return router


__all__ = (
    "create_discovery_profiles_router",
)
