"""Read-model helpers for ASIC control jobs."""


CONTROL_JOB_FIELDS = (
    "id",
    "created_at",
    "started_at",
    "completed_at",
    "miner_id",
    "ip",
    "name",
    "source",
    "action",
    "target_state",
    "status",
    "attempts",
    "max_attempts",
    "final_state",
    "message",
)


def control_job_items(rows):
    return [
        {
            field: row[field]
            for field in CONTROL_JOB_FIELDS
        }
        for row in rows
    ]


__all__ = (
    "CONTROL_JOB_FIELDS",
    "control_job_items",
)
