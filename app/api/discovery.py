"""ASIC discovery HTTP routes."""

import ipaddress
import threading

from fastapi import APIRouter, HTTPException

from config import (
    BITMAIN_USERNAME,
    BITMAIN_PASSWORD,
    AWESOME_USERNAME,
    AWESOME_PASSWORD,
)
from discovery import scan_network, detect_host
from inventory.repository import (
    list_discovery_miners,
    get_miner_by_ip,
    miner_name_exists,
    create_discovered_miner,
)


def create_discovery_router(
    poll_miner,
    log_event,
):
    router = APIRouter()

    @router.post(
        "/api/discovery/scan"
    )
    def discovery_scan(
        payload: dict,
    ):
        network = str(
            payload.get(
                "network",
                "",
            )
        ).strip()

        if not network:

            raise HTTPException(
                status_code=400,
                detail="Network is required",
            )

        try:

            result = scan_network(
                network
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
                    f"Discovery failed: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )


        rows = list_discovery_miners()


        managed = {
            row["ip"]: row
            for row in rows
        }


        devices = []

        managed_count = 0
        new_known = 0
        new_unknown = 0


        for device in result[
            "devices"
        ]:

            item = dict(
                device
            )

            existing = managed.get(
                item["ip"]
            )

            if existing:

                item["managed"] = True
                item["existing_id"] = (
                    existing["id"]
                )
                item["existing_name"] = (
                    existing["name"]
                )
                item["existing_driver"] = (
                    existing["driver"]
                )

                managed_count += 1

            else:

                item["managed"] = False
                item["existing_id"] = None
                item["existing_name"] = None
                item["existing_driver"] = None

                if (
                    item["driver"]
                    in (
                        "awesome",
                        "bitmain_stock",
                    )
                ):

                    new_known += 1

                else:

                    new_unknown += 1


            devices.append(
                item
            )


        result["devices"] = devices

        result["managed_count"] = (
            managed_count
        )

        result["new_known"] = (
            new_known
        )

        result["new_unknown"] = (
            new_unknown
        )

        return result


    @router.post(
        "/api/discovery/add"
    )
    def discovery_add(
        payload: dict,
    ):
        ips = payload.get(
            "ips"
        )

        if not isinstance(
            ips,
            list,
        ):

            raise HTTPException(
                status_code=400,
                detail=(
                    "ips must be a list"
                ),
            )


        clean_ips = []

        for value in ips:

            value = str(
                value
            ).strip()

            if not value:
                continue

            try:

                addr = ipaddress.ip_address(
                    value
                )

            except ValueError:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid IP: "
                        f"{value}"
                    ),
                )


            if addr.version != 4:

                raise HTTPException(
                    status_code=400,
                    detail="IPv4 only",
                )


            allowed = any(
                addr in network

                for network in (
                    ipaddress.ip_network(
                        "10.0.0.0/8"
                    ),

                    ipaddress.ip_network(
                        "172.16.0.0/12"
                    ),

                    ipaddress.ip_network(
                        "192.168.0.0/16"
                    ),
                )
            )


            if not allowed:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"IP outside RFC1918 "
                        f"private networks: "
                        f"{value}"
                    ),
                )


            if value not in clean_ips:

                clean_ips.append(
                    value
                )


        if not clean_ips:

            raise HTTPException(
                status_code=400,
                detail="No IP addresses supplied",
            )


        if len(clean_ips) > 256:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Maximum 256 ASICs "
                    "per import operation"
                ),
            )


        results = []


        for ip in clean_ips:

            existing = get_miner_by_ip(
                ip
            )


            if existing:

                results.append({
                    "ip": ip,
                    "success": True,
                    "status":
                        "already_managed",
                    "id":
                        existing["id"],
                })

                continue


            try:

                detected = detect_host(
                    ip
                )

            except Exception as exc:

                results.append({
                    "ip": ip,
                    "success": False,
                    "status":
                        "detection_failed",
                    "error":
                        str(exc),
                })

                continue


            if not detected:

                results.append({
                    "ip": ip,
                    "success": False,
                    "status":
                        "not_asic",
                    "error":
                        "ASIC signature not found",
                })

                continue


            driver = detected.get(
                "driver"
            )


            if driver not in (
                "awesome",
                "bitmain_stock",
            ):

                results.append({
                    "ip": ip,
                    "success": False,
                    "status":
                        "unknown_asic",
                    "error":
                        "Unsupported ASIC driver",
                })

                continue


            if driver == "awesome":

                username = AWESOME_USERNAME
                password = AWESOME_PASSWORD

            else:

                username = BITMAIN_USERNAME
                password = BITMAIN_PASSWORD


            last_octet = int(
                ip.split(".")[-1]
            )

            candidate_name = (
                f"ASIC-{last_octet:03d}"
            )


            if miner_name_exists(
                candidate_name
            ):

                candidate_name = (
                    "ASIC-"
                    + ip.replace(
                        ".",
                        "-",
                    )
                )


            miner_id, created = create_discovered_miner(
                candidate_name,
                ip,
                driver,
                username,
                password,
                detected.get(
                    "model"
                ),
                detected.get(
                    "firmware"
                ),
            )

            if not created:

                results.append({
                    "ip": ip,
                    "success": True,
                    "status":
                        "already_managed",
                    "id": miner_id,
                })

                continue


            log_event(
                source="SYSTEM",
                action="DISCOVERY_ADD",
                success=True,
                message=(
                    f"{ip} "
                    f"{driver} "
                    f"{detected.get('model')}"
                ),
            )


            threading.Thread(
                target=poll_miner,
                args=(
                    miner_id,
                ),
                daemon=True,
            ).start()


            results.append({
                "ip": ip,
                "success": True,
                "status": "added",
                "id": miner_id,
                "driver": driver,
                "name":
                    candidate_name,
            })


        return {
            "results": results
        }


    return router


__all__ = (
    "create_discovery_router",
)
