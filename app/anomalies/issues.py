"""Operator workflow helpers for anomaly issues."""


MAX_ACKNOWLEDGEMENT_NOTE_LENGTH = 500


def normalize_acknowledgement_note(value):
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError(
            "note must be a string or null"
        )

    note = value.strip()

    if len(note) > MAX_ACKNOWLEDGEMENT_NOTE_LENGTH:
        raise ValueError(
            "note must be at most "
            f"{MAX_ACKNOWLEDGEMENT_NOTE_LENGTH} characters"
        )

    return note or None


__all__ = (
    "MAX_ACKNOWLEDGEMENT_NOTE_LENGTH",
    "normalize_acknowledgement_note",
)
