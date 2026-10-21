"""Validation and serialization for ASIC service history."""

from service_history.repository import (
    SERVICE_WORK_TYPES,
)


MAX_DESCRIPTION_LENGTH = 1000
MAX_PART_SERIAL_LENGTH = 200
MAX_PERFORMER_LENGTH = 120

FUTURE_CLOCK_TOLERANCE_SECONDS = 300


def normalize_service_record(
    payload,
    now,
):
    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Request body must be an object"
        )


    performed_at_raw = payload.get(
        "performed_at"
    )

    if isinstance(
        performed_at_raw,
        bool,
    ):
        raise ValueError(
            "performed_at must be a Unix timestamp"
        )

    try:
        performed_at = int(
            performed_at_raw
        )

    except (
        TypeError,
        ValueError,
    ):
        raise ValueError(
            "performed_at must be a Unix timestamp"
        )


    if performed_at <= 0:
        raise ValueError(
            "performed_at must be positive"
        )


    if (
        performed_at
        >
        int(now)
        + FUTURE_CLOCK_TOLERANCE_SECONDS
    ):
        raise ValueError(
            "Service work cannot be recorded "
            "in the future"
        )


    work_type = str(
        payload.get(
            "work_type",
            "",
        )
        or ""
    ).strip().upper()


    if work_type not in SERVICE_WORK_TYPES:
        raise ValueError(
            "Invalid service work type"
        )


    description = str(
        payload.get(
            "description",
            "",
        )
        or ""
    ).strip()


    if (
        len(description)
        >
        MAX_DESCRIPTION_LENGTH
    ):
        raise ValueError(
            "Description is too long"
        )


    part_serial = str(
        payload.get(
            "part_serial",
            "",
        )
        or ""
    ).strip()


    if (
        len(part_serial)
        >
        MAX_PART_SERIAL_LENGTH
    ):
        raise ValueError(
            "Part / serial information "
            "is too long"
        )


    performed_by = str(
        payload.get(
            "performed_by",
            "",
        )
        or ""
    ).strip()


    if not performed_by:
        raise ValueError(
            "performed_by is required"
        )


    if (
        len(performed_by)
        >
        MAX_PERFORMER_LENGTH
    ):
        raise ValueError(
            "performed_by is too long"
        )


    if (
        work_type == "OTHER"
        and not description
    ):
        raise ValueError(
            "Description is required "
            "for OTHER service work"
        )


    return {
        "performed_at": performed_at,
        "work_type": work_type,
        "description": description,
        "part_serial": (
            part_serial
            or None
        ),
        "performed_by": performed_by,
    }


def service_record_dict(row):
    return {
        "id": row["id"],
        "miner_id": row["miner_id"],
        "performed_at": (
            row["performed_at"]
        ),
        "work_type": row["work_type"],
        "description": (
            row["description"]
            or ""
        ),
        "part_serial": (
            row["part_serial"]
        ),
        "performed_by": (
            row["performed_by"]
            or ""
        ),
        "created_by": (
            row["created_by"]
        ),
        "created_at": (
            row["created_at"]
        ),
    }


__all__ = (
    "normalize_service_record",
    "service_record_dict",
)
