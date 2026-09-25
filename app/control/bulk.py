"""
Stateless bulk-control orchestration.

Bulk control never owns ASIC execution state.

It only:
- validates an explicit selection;
- previews per-miner eligibility;
- fans accepted targets into the existing verified
  per-miner control queue;
- reports deterministic per-target results.

The existing control_jobs table and workers remain the
single execution / verification authority.
"""

from control.repository import (
    get_bulk_control_group,
    list_bulk_control_miners_by_group,
    list_bulk_control_miners_by_ids,
)


BULK_CONTROL_MAX_TARGETS = 50

BULK_CONTROL_ACTIONS = (
    "pause",
    "resume",
    "reboot",
)

SUPPORTED_CONTROL_DRIVERS = (
    "awesome",
    "bitmain_stock",
)


class BulkControlValidationError(
    ValueError
):
    pass


def _positive_int(
    value,
    field,
):
    if (
        type(value) is not int
        or value <= 0
    ):
        raise BulkControlValidationError(
            f"{field} must be a positive integer"
        )

    return value


def normalize_bulk_control_request(
    payload,
):
    if not isinstance(
        payload,
        dict,
    ):
        raise BulkControlValidationError(
            "JSON body must be an object"
        )


    allowed = {
        "miner_ids",
        "group_id",
        "confirm_reboot",
    }


    unknown = sorted(
        set(payload)
        -
        allowed
    )


    if unknown:
        raise BulkControlValidationError(
            "Unknown bulk control fields: "
            +
            ", ".join(
                unknown
            )
        )


    has_miner_ids = (
        "miner_ids" in payload
        and
        payload["miner_ids"] is not None
    )

    has_group_id = (
        "group_id" in payload
        and
        payload["group_id"] is not None
    )


    if (
        has_miner_ids
        ==
        has_group_id
    ):
        raise BulkControlValidationError(
            "Exactly one selection source is required: "
            "miner_ids or group_id"
        )


    confirm_reboot = payload.get(
        "confirm_reboot",
        False,
    )


    if type(confirm_reboot) is not bool:
        raise BulkControlValidationError(
            "confirm_reboot must be boolean"
        )


    if has_miner_ids:

        miner_ids = payload[
            "miner_ids"
        ]


        if not isinstance(
            miner_ids,
            list,
        ):
            raise BulkControlValidationError(
                "miner_ids must be an array"
            )


        if not miner_ids:
            raise BulkControlValidationError(
                "miner_ids must not be empty"
            )


        normalized_ids = [
            _positive_int(
                value,
                "miner_ids item",
            )
            for value
            in miner_ids
        ]


        if (
            len(
                set(
                    normalized_ids
                )
            )
            !=
            len(
                normalized_ids
            )
        ):
            raise BulkControlValidationError(
                "miner_ids must not contain duplicates"
            )


        if (
            len(
                normalized_ids
            )
            >
            BULK_CONTROL_MAX_TARGETS
        ):
            raise BulkControlValidationError(
                "Bulk control is limited to "
                f"{BULK_CONTROL_MAX_TARGETS} ASICs"
            )


        return {
            "selection_type":
                "MINERS",

            "miner_ids":
                sorted(
                    normalized_ids
                ),

            "group_id":
                None,

            "confirm_reboot":
                confirm_reboot,
        }


    group_id = _positive_int(
        payload["group_id"],
        "group_id",
    )


    return {
        "selection_type":
            "GROUP",

        "miner_ids":
            None,

        "group_id":
            group_id,

        "confirm_reboot":
            confirm_reboot,
    }


def normalize_bulk_control_action(
    action,
):
    value = str(
        action
        or ""
    ).strip().lower()


    if (
        value
        not in
        BULK_CONTROL_ACTIONS
    ):
        raise BulkControlValidationError(
            "action must be pause, resume or reboot"
        )


    return value


def _public_active_job(
    miner,
):
    if (
        miner is None
        or
        miner["active_job_id"]
        is None
    ):
        return None


    return {
        "job_id":
            int(
                miner[
                    "active_job_id"
                ]
            ),

        "status":
            miner[
                "active_job_status"
            ],

        "action":
            miner[
                "active_job_action"
            ],

        "target_state":
            miner[
                "active_job_target_state"
            ],
    }


def _eligibility(
    miner,
):
    if miner is None:
        return (
            False,
            "NOT_FOUND",
            "Miner not found",
        )


    if not bool(
        miner["enabled"]
    ):
        return (
            False,
            "DISABLED",
            "ASIC is disabled",
        )


    if (
        miner["driver"]
        not in
        SUPPORTED_CONTROL_DRIVERS
    ):
        return (
            False,
            "UNSUPPORTED_DRIVER",
            "Firmware is not configured for control",
        )


    if (
        miner["active_job_id"]
        is not None
    ):
        return (
            False,
            "ACTIVE_JOB",
            "ASIC already has an active control job",
        )


    return (
        True,
        None,
        None,
    )


def _public_target(
    requested_id,
    miner,
):
    (
        eligible,
        rejection_code,
        rejection_reason,
    ) = _eligibility(
        miner
    )


    if miner is None:

        return {
            "miner_id":
                int(
                    requested_id
                ),

            "name":
                None,

            "ip":
                None,

            "group_id":
                None,

            "group_name":
                None,

            "driver":
                None,

            "enabled":
                None,

            "eligible":
                False,

            "rejection_code":
                rejection_code,

            "rejection_reason":
                rejection_reason,

            "active_job":
                None,
        }


    return {
        "miner_id":
            int(
                miner["id"]
            ),

        "name":
            miner["name"],

        "ip":
            miner["ip"],

        "group_id":
            (
                int(
                    miner[
                        "group_id"
                    ]
                )
                if (
                    miner[
                        "group_id"
                    ]
                    is not None
                )
                else None
            ),

        "group_name":
            miner["group_name"],

        "driver":
            miner["driver"],

        "enabled":
            bool(
                miner["enabled"]
            ),

        "eligible":
            eligible,

        "rejection_code":
            rejection_code,

        "rejection_reason":
            rejection_reason,

        "active_job":
            _public_active_job(
                miner
            ),
    }


def resolve_bulk_control_selection(
    payload,
):
    normalized = (
        normalize_bulk_control_request(
            payload
        )
    )


    if (
        normalized[
            "selection_type"
        ]
        ==
        "MINERS"
    ):

        requested_ids = (
            normalized[
                "miner_ids"
            ]
        )


        rows = (
            list_bulk_control_miners_by_ids(
                requested_ids
            )
        )


        by_id = {
            int(row["id"]):
                row
            for row
            in rows
        }


        targets = [
            {
                "requested_id":
                    int(miner_id),

                "miner":
                    by_id.get(
                        int(
                            miner_id
                        )
                    ),
            }

            for miner_id
            in requested_ids
        ]


        selection = {
            "type":
                "MINERS",

            "group_id":
                None,

            "group_name":
                None,
        }


    else:

        group_id = (
            normalized[
                "group_id"
            ]
        )


        group = (
            get_bulk_control_group(
                group_id
            )
        )


        if group is None:
            raise BulkControlValidationError(
                "Group not found"
            )


        rows = (
            list_bulk_control_miners_by_group(
                group_id
            )
        )


        if not rows:
            raise BulkControlValidationError(
                "Selected group has no miners"
            )


        if (
            len(rows)
            >
            BULK_CONTROL_MAX_TARGETS
        ):
            raise BulkControlValidationError(
                "Bulk control is limited to "
                f"{BULK_CONTROL_MAX_TARGETS} ASICs; "
                f"group contains {len(rows)}"
            )


        targets = [
            {
                "requested_id":
                    int(
                        row["id"]
                    ),

                "miner":
                    row,
            }

            for row
            in rows
        ]


        selection = {
            "type":
                "GROUP",

            "group_id":
                int(
                    group["id"]
                ),

            "group_name":
                group["name"],
        }


    public_targets = [
        _public_target(
            target[
                "requested_id"
            ],
            target[
                "miner"
            ],
        )

        for target
        in targets
    ]


    eligible_count = sum(
        1
        for target
        in public_targets
        if target["eligible"]
    )


    return {
        "normalized":
            normalized,

        "selection":
            selection,

        "targets":
            targets,

        "public_targets":
            public_targets,

        "requested_count":
            len(
                public_targets
            ),

        "eligible_count":
            eligible_count,

        "rejected_count":
            (
                len(
                    public_targets
                )
                -
                eligible_count
            ),
    }


def bulk_control_preview(
    action,
    payload,
):
    action = (
        normalize_bulk_control_action(
            action
        )
    )


    resolved = (
        resolve_bulk_control_selection(
            payload
        )
    )


    return {
        "action":
            action,

        "max_targets":
            BULK_CONTROL_MAX_TARGETS,

        "selection":
            resolved[
                "selection"
            ],

        "requested_count":
            resolved[
                "requested_count"
            ],

        "eligible_count":
            resolved[
                "eligible_count"
            ],

        "rejected_count":
            resolved[
                "rejected_count"
            ],

        "targets":
            resolved[
                "public_targets"
            ],
    }


def _rejected_result(
    target,
):
    return {
        **target,

        "accepted":
            False,

        "queued":
            False,

        "job_id":
            (
                target[
                    "active_job"
                ][
                    "job_id"
                ]
                if (
                    target[
                        "active_job"
                    ]
                    is not None
                )
                else None
            ),
    }


def execute_bulk_control(
    action,
    payload,
    queue_control,
    queue_reboot,
    log_event,
):
    action = (
        normalize_bulk_control_action(
            action
        )
    )


    resolved = (
        resolve_bulk_control_selection(
            payload
        )
    )


    if (
        action == "reboot"
        and
        not resolved[
            "normalized"
        ][
            "confirm_reboot"
        ]
    ):
        raise BulkControlValidationError(
            "Bulk REBOOT requires confirm_reboot=true"
        )


    results = []


    for (
        internal,
        public,
    ) in zip(
        resolved[
            "targets"
        ],
        resolved[
            "public_targets"
        ],
    ):

        miner = internal[
            "miner"
        ]


        if not public[
            "eligible"
        ]:

            log_event(
                source="MANUAL",
                action=(
                    "BULK_"
                    +
                    action.upper()
                    +
                    "_REJECTED"
                ),
                miner=miner,
                success=False,
                message=(
                    "Bulk control rejected; "
                    +
                    public[
                        "rejection_code"
                    ]
                    +
                    ": "
                    +
                    public[
                        "rejection_reason"
                    ]
                    +
                    (
                        ""
                        if miner is not None
                        else (
                            "; requested miner_id="
                            +
                            str(
                                public[
                                    "miner_id"
                                ]
                            )
                        )
                    )
                ),
            )


            results.append(
                _rejected_result(
                    public
                )
            )

            continue


        try:

            if action == "reboot":

                queued = (
                    queue_reboot(
                        public[
                            "miner_id"
                        ]
                    )
                )

            else:

                queued = (
                    queue_control(
                        public[
                            "miner_id"
                        ],
                        action,
                        manual=True,
                    )
                )


            if queued.get(
                "already_active"
            ):

                rejected = {
                    **public,

                    "eligible":
                        False,

                    "rejection_code":
                        "ACTIVE_JOB",

                    "rejection_reason":
                        (
                            "ASIC already has "
                            "an active control job"
                        ),

                    "active_job": {
                        "job_id":
                            queued.get(
                                "job_id"
                            ),

                        "status":
                            queued.get(
                                "status"
                            ),

                        "action":
                            queued.get(
                                "action"
                            ),

                        "target_state":
                            queued.get(
                                "target_state"
                            ),
                    },

                    "accepted":
                        False,

                    "queued":
                        False,

                    "job_id":
                        queued.get(
                            "job_id"
                        ),
                }


                log_event(
                    source="MANUAL",
                    action=(
                        "BULK_"
                        +
                        action.upper()
                        +
                        "_REJECTED"
                    ),
                    miner=miner,
                    success=False,
                    message=(
                        "Bulk control rejected; "
                        "ACTIVE_JOB"
                    ),
                )


                results.append(
                    rejected
                )

                continue


            accepted = bool(
                queued.get(
                    "queued"
                )
            )


            if not accepted:

                raise RuntimeError(
                    "Existing control queue "
                    "did not accept the request"
                )


            # The existing queue already writes the normal
            # per-miner PAUSE_QUEUED / RESUME_QUEUED /
            # REBOOT_QUEUED audit event.
            results.append({
                **public,

                "accepted":
                    True,

                "queued":
                    True,

                "job_id":
                    queued.get(
                        "job_id"
                    ),

                "job_status":
                    queued.get(
                        "status"
                    ),

                "job_action":
                    queued.get(
                        "action"
                    ),

                "target_state":
                    queued.get(
                        "target_state"
                    ),
            })


        except Exception as exc:

            log_event(
                source="MANUAL",
                action=(
                    "BULK_"
                    +
                    action.upper()
                    +
                    "_REJECTED"
                ),
                miner=miner,
                success=False,
                message=(
                    "Bulk queue failure; "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )


            results.append({
                **public,

                "accepted":
                    False,

                "queued":
                    False,

                "job_id":
                    None,

                "rejection_code":
                    "QUEUE_ERROR",

                "rejection_reason":
                    (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
            })


    accepted_count = sum(
        1
        for result
        in results
        if result["accepted"]
    )


    return {
        "action":
            action,

        "max_targets":
            BULK_CONTROL_MAX_TARGETS,

        "selection":
            resolved[
                "selection"
            ],

        "requested_count":
            len(
                results
            ),

        "accepted_count":
            accepted_count,

        "rejected_count":
            (
                len(
                    results
                )
                -
                accepted_count
            ),

        "results":
            results,
    }


__all__ = (
    "BULK_CONTROL_MAX_TARGETS",
    "BULK_CONTROL_ACTIONS",
    "BulkControlValidationError",
    "normalize_bulk_control_request",
    "normalize_bulk_control_action",
    "resolve_bulk_control_selection",
    "bulk_control_preview",
    "execute_bulk_control",
)
