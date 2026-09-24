"""Validation helpers for operational miner groups."""

MAX_GROUP_NAME_LENGTH = 80


def normalize_group_name(value):

    if not isinstance(value, str):
        raise ValueError(
            "Group name must be a string"
        )

    name = " ".join(
        value.split()
    )

    if not name:
        raise ValueError(
            "Group name must not be empty"
        )

    if len(name) > MAX_GROUP_NAME_LENGTH:
        raise ValueError(
            "Group name is too long"
        )

    if any(
        ord(character) < 32
        for character in name
    ):
        raise ValueError(
            "Group name contains invalid characters"
        )

    return name


def normalized_group_key(name):
    return normalize_group_name(
        name
    ).casefold()


__all__ = (
    "MAX_GROUP_NAME_LENGTH",
    "normalize_group_name",
    "normalized_group_key",
)
