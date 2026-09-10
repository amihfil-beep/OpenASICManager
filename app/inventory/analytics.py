"""Read-model helpers for ASIC inventory status."""

import ipaddress


__all__ = (
    "miner_status_items",
)


def miner_status_items(
    miner_rows,
    active_job_rows,
):
    active_jobs = {}

    for job in active_job_rows:
        if job["miner_id"] not in active_jobs:
            active_jobs[job["miner_id"]] = job

    miners = list(miner_rows)
    miners.sort(
        key=lambda row: ipaddress.ip_address(
            row["ip"]
        )
    )

    result = []

    for row in miners:
        state = row["last_state"] or "UNKNOWN"
        if row["driver"] == "unset":
            state = "CONFIG_REQUIRED"

        job = active_jobs.get(row["id"])

        result.append({
            "id": row["id"],
            "name": row["name"],
            "ip": row["ip"],
            "driver": row["driver"],
            "detection_mode": (
                row["detection_mode"]
                or "AUTO"
            ),
            "enabled": bool(row["enabled"]),
            "schedule_enabled": bool(
                row["schedule_enabled"]
            ),
            "model": row["model"],
            "firmware": row["firmware"],
            "state": state,
            "hashrate": row["hashrate"],
            "avg_hashrate": row["avg_hashrate"],
            "temp": row["temp"],
            "power": row["power"],
            "pool": row["pool"],
            "last_seen": row["last_seen"],
            "last_error": row["last_error"],
            "last_action": row["last_action"],
            "manual_override_until": (
                row["manual_override_until"]
            ),
            "control_job": (
                {
                    "id": job["id"],
                    "status": job["status"],
                    "action": job["action"],
                    "target_state": job["target_state"],
                    "attempts": job["attempts"],
                    "max_attempts": job["max_attempts"],
                    "message": job["message"],
                }
                if job
                else None
            ),
        })

    return result
