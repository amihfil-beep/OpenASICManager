"""Pure Telegram incident-notification policy logic."""


NOTIFICATION_POLICY_FIELDS = (
    "issue_open",
    "issue_resolved",
    "acknowledgement",
    "control_failure",
    "maintenance",
)


DEFAULT_NOTIFICATION_POLICY = {
    # Preserve existing Telegram incident behavior.
    "issue_open": True,
    "issue_resolved": True,
    "control_failure": True,

    # New event classes stay opt-in so upgrades
    # do not suddenly create additional messages.
    "acknowledgement": False,
    "maintenance": False,
}


CURRENT_TELEGRAM_ACTION_POLICY = {
    "ISSUE_OPEN":
        "issue_open",

    "ISSUE_RESOLVED":
        "issue_resolved",

    "ISSUE_ACKNOWLEDGE":
        "acknowledgement",

    "ISSUE_UNACKNOWLEDGE":
        "acknowledgement",

    "PAUSE_FAILED":
        "control_failure",

    "RESUME_FAILED":
        "control_failure",

    "REBOOT_FAILED":
        "control_failure",

    "MAINTENANCE_CREATE":
        "maintenance",

    "MAINTENANCE_EXTEND":
        "maintenance",

    "MAINTENANCE_END":
        "maintenance",
}


def _normalize_bool(
    field,
    value,
):
    if type(value) is not bool:
        raise ValueError(
            f"{field} must be boolean"
        )

    return value


def normalize_notification_policy(
    data,
    base=None,
):
    if not isinstance(
        data,
        dict,
    ):
        raise ValueError(
            "Notification policy must be an object"
        )

    unknown = sorted(
        set(data)
        -
        set(NOTIFICATION_POLICY_FIELDS)
    )

    if unknown:
        raise ValueError(
            "Unknown notification policy fields: "
            + ", ".join(unknown)
        )

    if base is None:
        result = dict(
            DEFAULT_NOTIFICATION_POLICY
        )
    else:
        result = dict(
            DEFAULT_NOTIFICATION_POLICY
        )

        result.update(
            normalize_notification_policy(
                base
            )
        )

    for field, value in data.items():
        result[field] = (
            _normalize_bool(
                field,
                value,
            )
        )

    return result


def apply_notification_policy_patch(
    current,
    patch,
):
    if not isinstance(
        patch,
        dict,
    ):
        raise ValueError(
            "Notification policy must be an object"
        )

    if not patch:
        raise ValueError(
            "At least one notification policy "
            "field is required"
        )

    current = (
        normalize_notification_policy(
            current
        )
    )

    unknown = sorted(
        set(patch)
        -
        set(NOTIFICATION_POLICY_FIELDS)
    )

    if unknown:
        raise ValueError(
            "Unknown notification policy fields: "
            + ", ".join(unknown)
        )

    for field, value in patch.items():
        current[field] = (
            _normalize_bool(
                field,
                value,
            )
        )

    return current


def notification_policy_field_for_action(
    action,
):
    return (
        CURRENT_TELEGRAM_ACTION_POLICY.get(
            str(action or "").upper()
        )
    )


def notification_delivery_allowed(
    action,
    policy,
):
    field = (
        notification_policy_field_for_action(
            action
        )
    )

    if field is None:
        return False

    normalized = (
        normalize_notification_policy(
            policy
        )
    )

    return bool(
        normalized[field]
    )


__all__ = (
    "NOTIFICATION_POLICY_FIELDS",
    "DEFAULT_NOTIFICATION_POLICY",
    "CURRENT_TELEGRAM_ACTION_POLICY",
    "normalize_notification_policy",
    "apply_notification_policy_patch",
    "notification_policy_field_for_action",
    "notification_delivery_allowed",
)
