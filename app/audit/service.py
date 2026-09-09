"""Audit event normalization, persistence and notification dispatch."""

import time
from datetime import datetime

from audit.identity import audit_source
from audit.repository import (
    list_action_log,
    write_action_log,
)


MAX_AUDIT_MESSAGE_LENGTH = 1000
MAX_AUDIT_LOG_LIMIT = 500


class AuditRuntime:
    """Application-facing audit event service."""

    def __init__(self, notify_event):
        self.notify_event = notify_event

    def log_event(
        self,
        source,
        action,
        miner=None,
        success=True,
        message=None,
    ):
        source = audit_source(source)

        try:
            miner_id = None
            ip = None
            name = None

            if miner is not None:
                miner_id = miner["id"]
                ip = miner["ip"]
                name = miner["name"]

            if message is None:
                message = ""

            message = str(message)

            if len(message) > MAX_AUDIT_MESSAGE_LENGTH:
                message = message[:MAX_AUDIT_MESSAGE_LENGTH]

            write_action_log(
                timestamp=int(time.time()),
                source=source,
                action=action,
                miner_id=miner_id,
                ip=ip,
                name=name,
                success=success,
                message=message,
            )

        except Exception:
            # Audit persistence must never break ASIC control.
            pass

        self.notify_event(
            source=source,
            action=action,
            miner=miner,
            success=success,
            message=message,
        )


def action_log_entries(
    limit,
    timezone,
):
    limit = max(
        1,
        min(
            int(limit),
            MAX_AUDIT_LOG_LIMIT,
        ),
    )

    rows = list_action_log(
        limit
    )

    return [
        {
            "id": row["id"],
            "time": datetime.fromtimestamp(
                row["ts"],
                timezone,
            ).isoformat(),
            "source": row["source"],
            "action": row["action"],
            "miner_id": row["miner_id"],
            "ip": row["ip"],
            "name": row["name"],
            "success": bool(row["success"]),
            "message": row["message"],
        }
        for row in rows
    ]


__all__ = (
    "AuditRuntime",
    "action_log_entries",
)
