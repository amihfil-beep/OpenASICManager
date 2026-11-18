"""Process descriptor pressure diagnostics for /health."""

import os
import resource


FD_WARNING_FRACTION = 0.80
FD_CRITICAL_FRACTION = 0.90


def file_descriptor_health_snapshot():
    """Observe current process limits without touching the database."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)

    if soft == resource.RLIM_INFINITY:
        soft_limit = None
    else:
        soft_limit = int(soft)

    if hard == resource.RLIM_INFINITY:
        hard_limit = None
    else:
        hard_limit = int(hard)

    try:
        count = len(os.listdir("/proc/self/fd"))
    except OSError as exc:
        return {
            "status": "unavailable",
            "healthy": False,
            "open_fds": None,
            "soft_limit": soft_limit,
            "hard_limit": hard_limit,
            "usage_percent": None,
            "error": type(exc).__name__,
        }

    usage = (
        round(100.0 * count / soft_limit, 2)
        if soft_limit and soft_limit > 0
        else None
    )
    if usage is None or usage < FD_WARNING_FRACTION * 100:
        status = "ok"
    elif usage >= FD_CRITICAL_FRACTION * 100:
        status = "critical"
    else:
        status = "warning"

    return {
        "status": status,
        "healthy": status != "critical",
        "open_fds": count,
        "soft_limit": soft_limit,
        "hard_limit": hard_limit,
        "usage_percent": usage,
        "error": None,
    }


__all__ = ("file_descriptor_health_snapshot",)
