"""Validation and multi-network scanning for saved discovery profiles."""

import ipaddress
import time

from discovery import ip_sort, scan_network, validate_network
from discovery_profiles.repository import (
    create_discovery_profile,
    delete_discovery_profile,
    get_discovery_profile,
    list_discovery_profiles,
    update_discovery_profile,
)


MAX_MULTI_SCAN_NETWORKS = 8
MAX_PROFILE_NAME_LENGTH = 80


def _normalize_enabled(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, int) and value in (0, 1):
        return bool(value)

    raise ValueError("enabled must be a boolean")


def _normalize_name(value):
    name = str(value or "").strip()

    if not name:
        raise ValueError("Profile name is required")

    if len(name) > MAX_PROFILE_NAME_LENGTH:
        raise ValueError(
            f"Profile name is too long; maximum {MAX_PROFILE_NAME_LENGTH} characters"
        )

    return name


def _normalize_network(value):
    network = str(value or "").strip()

    if not network:
        raise ValueError("Network is required")

    return str(validate_network(network))


def _ensure_no_overlap(network, profiles, exclude_id=None):
    candidate = ipaddress.ip_network(network)

    for profile in profiles:
        if exclude_id is not None and profile["id"] == exclude_id:
            continue

        existing = ipaddress.ip_network(profile["network"])

        if candidate.overlaps(existing):
            raise ValueError(
                "Network overlaps saved profile "
                f"'{profile['name']}' ({profile['network']})"
            )


def list_saved_profiles(enabled_only=False):
    return list_discovery_profiles(
        enabled_only=enabled_only
    )


def create_saved_profile(payload):
    if not isinstance(payload, dict):
        raise ValueError("Profile payload must be an object")

    name = _normalize_name(payload.get("name"))
    network = _normalize_network(payload.get("network"))
    enabled = _normalize_enabled(
        payload.get("enabled", True)
    )

    profiles = list_discovery_profiles()
    _ensure_no_overlap(network, profiles)

    return create_discovery_profile(
        name,
        network,
        enabled,
    )


def update_saved_profile(profile_id, payload):
    if not isinstance(payload, dict):
        raise ValueError("Profile payload must be an object")

    current = get_discovery_profile(profile_id)

    if current is None:
        raise LookupError("Discovery profile not found")

    name = _normalize_name(
        payload.get("name", current["name"])
    )
    network = _normalize_network(
        payload.get("network", current["network"])
    )
    enabled = _normalize_enabled(
        payload.get("enabled", current["enabled"])
    )

    profiles = list_discovery_profiles()
    _ensure_no_overlap(
        network,
        profiles,
        exclude_id=profile_id,
    )

    updated = update_discovery_profile(
        profile_id,
        name,
        network,
        enabled,
    )

    if updated is None:
        raise LookupError("Discovery profile not found")

    return updated


def delete_saved_profile(profile_id):
    current = get_discovery_profile(profile_id)

    if current is None:
        raise LookupError("Discovery profile not found")

    if not delete_discovery_profile(profile_id):
        raise LookupError("Discovery profile not found")

    return current


def _selected_profiles(profile_ids):
    if profile_ids is None:
        profiles = list_discovery_profiles(
            enabled_only=True
        )
    else:
        if not isinstance(profile_ids, list):
            raise ValueError("profile_ids must be a list")

        if not profile_ids:
            raise ValueError("profile_ids must not be empty")

        clean_ids = []

        for value in profile_ids:
            if isinstance(value, bool):
                raise ValueError("profile_ids must contain positive integers")

            try:
                profile_id = int(value)
            except (TypeError, ValueError):
                raise ValueError(
                    "profile_ids must contain positive integers"
                )

            if profile_id <= 0:
                raise ValueError(
                    "profile_ids must contain positive integers"
                )

            if profile_id not in clean_ids:
                clean_ids.append(profile_id)

        profiles = []

        for profile_id in clean_ids:
            profile = get_discovery_profile(profile_id)

            if profile is None:
                raise LookupError(
                    f"Discovery profile not found: {profile_id}"
                )

            profiles.append(profile)

    profiles.sort(key=lambda item: item["id"])

    if not profiles:
        raise ValueError("No enabled discovery profiles")

    if len(profiles) > MAX_MULTI_SCAN_NETWORKS:
        raise ValueError(
            "Too many discovery profiles selected; "
            f"maximum {MAX_MULTI_SCAN_NETWORKS}"
        )

    return profiles


def scan_saved_profiles(profile_ids=None, scanner=None):
    if scanner is None:
        scanner = scan_network

    profiles = _selected_profiles(profile_ids)
    started = time.monotonic()
    devices_by_ip = {}
    network_results = []
    hosts_scanned = 0
    successful_networks = 0
    failed_networks = 0

    for profile in profiles:
        network_started = time.monotonic()

        try:
            result = scanner(profile["network"])
        except Exception as exc:
            failed_networks += 1
            network_results.append({
                "profile_id": profile["id"],
                "profile_name": profile["name"],
                "network": profile["network"],
                "status": "failed",
                "hosts_scanned": 0,
                "duration_seconds": round(
                    time.monotonic() - network_started,
                    2,
                ),
                "total": 0,
                "awesome": 0,
                "bitmain_stock": 0,
                "unknown": 0,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            })
            continue

        successful_networks += 1
        hosts_scanned += int(
            result.get("hosts_scanned", 0) or 0
        )

        source = {
            "profile_id": profile["id"],
            "profile_name": profile["name"],
            "network": profile["network"],
        }

        for device in result.get("devices", []):
            item = dict(device)
            ip = str(item.get("ip", "")).strip()

            if not ip:
                continue

            existing = devices_by_ip.get(ip)

            if existing is not None:
                if source not in existing["sources"]:
                    existing["sources"].append(source)
                continue

            item["source_profile_id"] = profile["id"]
            item["source_profile_name"] = profile["name"]
            item["source_network"] = profile["network"]
            item["sources"] = [source]
            devices_by_ip[ip] = item

        network_results.append({
            "profile_id": profile["id"],
            "profile_name": profile["name"],
            "network": profile["network"],
            "status": "ok",
            "hosts_scanned": int(
                result.get("hosts_scanned", 0) or 0
            ),
            "duration_seconds": result.get(
                "duration_seconds",
                round(
                    time.monotonic() - network_started,
                    2,
                ),
            ),
            "total": int(result.get("total", 0) or 0),
            "awesome": int(result.get("awesome", 0) or 0),
            "bitmain_stock": int(
                result.get("bitmain_stock", 0) or 0
            ),
            "unknown": int(result.get("unknown", 0) or 0),
            "error": None,
        })

    devices = list(devices_by_ip.values())
    devices.sort(key=ip_sort)

    awesome = sum(
        1
        for item in devices
        if item.get("driver") == "awesome"
    )
    stock = sum(
        1
        for item in devices
        if item.get("driver") == "bitmain_stock"
    )
    unknown = sum(
        1
        for item in devices
        if item.get("driver") == "unknown_asic"
    )

    return {
        "mode": "saved_profiles",
        "profiles_scanned": len(profiles),
        "successful_networks": successful_networks,
        "failed_networks": failed_networks,
        "hosts_scanned": hosts_scanned,
        "duration_seconds": round(
            time.monotonic() - started,
            2,
        ),
        "total": len(devices),
        "awesome": awesome,
        "bitmain_stock": stock,
        "unknown": unknown,
        "devices": devices,
        "networks": network_results,
    }


__all__ = (
    "MAX_MULTI_SCAN_NETWORKS",
    "list_saved_profiles",
    "create_saved_profile",
    "update_saved_profile",
    "delete_saved_profile",
    "scan_saved_profiles",
)
