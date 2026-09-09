"""Request-scoped audit identity and source attribution."""

import contextvars


audit_actor_context = contextvars.ContextVar(
    "asic_manager_audit_actor",
    default=None,
)


def sanitize_audit_username(value):
    value = str(value or "").strip()

    if not value:
        return None

    safe = "".join(
        character
        for character in value
        if (
            character.isalnum()
            or character in (".", "_", "-", "@")
        )
    )

    if not safe:
        return None

    return safe[:64]


def current_audit_actor():
    actor = audit_actor_context.get()

    if actor:
        return actor

    return "LOCAL"


def audit_source(source):
    source = str(source or "SYSTEM")

    if source == "LOCAL" or source.startswith("WEB:"):
        return source

    actor = audit_actor_context.get()

    if actor and source in ("MANUAL", "SYSTEM"):
        return actor

    return source


def audit_actor_from_remote_user(value):
    username = sanitize_audit_username(value)

    if username:
        return "WEB:" + username

    return "LOCAL"


def bind_audit_actor(actor):
    return audit_actor_context.set(actor)


def reset_audit_actor(token):
    audit_actor_context.reset(token)


__all__ = (
    "sanitize_audit_username",
    "current_audit_actor",
    "audit_source",
    "audit_actor_from_remote_user",
    "bind_audit_actor",
    "reset_audit_actor",
)
