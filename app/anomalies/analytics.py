"""Read-model helpers for anomaly issues."""

from datetime import datetime

from scheduler.policy import MOSCOW

from anomalies.policy import (
    ANOMALY_OFFLINE_GRACE,
    ANOMALY_HOT_TEMP,
    ANOMALY_HOT_CLEAR,
    ANOMALY_HOT_GRACE,
    ANOMALY_SCHEDULE_GRACE,
)
from anomalies.repository import issue_rows


__all__ = (
    "issue_dict",
    "issue_report",
)


def issue_dict(row):
    return {
        "id": row["id"],
        "miner_id": row["miner_id"],
        "ip": row["ip"],
        "name": row["name"],
        "code": row["code"],
        "severity": row["severity"],
        "status": row["status"],
        "first_seen": datetime.fromtimestamp(
            row["first_seen"],
            MOSCOW,
        ).isoformat(),
        "last_seen": datetime.fromtimestamp(
            row["last_seen"],
            MOSCOW,
        ).isoformat(),
        "resolved_at": (
            datetime.fromtimestamp(
                row["resolved_at"],
                MOSCOW,
            ).isoformat()
            if row["resolved_at"]
            else None
        ),
        "message": row["message"],
    }


def issue_report(limit=100):
    active_rows, resolved_rows = issue_rows(limit)

    return {
        "active_count": len(active_rows),
        "active": [issue_dict(row) for row in active_rows],
        "recent_resolved": [
            issue_dict(row)
            for row in resolved_rows
        ],
        "thresholds": {
            "offline_grace_seconds": ANOMALY_OFFLINE_GRACE,
            "hot_open_c": ANOMALY_HOT_TEMP,
            "hot_clear_c": ANOMALY_HOT_CLEAR,
            "hot_grace_seconds": ANOMALY_HOT_GRACE,
            "schedule_grace_seconds": ANOMALY_SCHEDULE_GRACE,
        },
    }
