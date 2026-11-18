"""Pure connectivity classification and edge-triggered alert policy.

The transport and persistence live outside this module. All models and
firmwares use the same state classification; PAUSED is healthy when fresh.
"""

from math import ceil


FRESH_SECONDS = 120
OUTAGE_GRACE_SECONDS = 90
RECOVERY_GRACE_SECONDS = 60
MASS_OUTAGE_FRACTION = 0.80
MASS_OUTAGE_MIN_MINERS = 5
HEALTHY_STATES = frozenset(("MINING", "PAUSED", "IDLE", "STARTING"))


def classify_fleet(rows, now, fresh_seconds=FRESH_SECONDS):
    """Count fresh, failed and stale miners, without equating PAUSED to down."""
    counts = {
        "total": 0,
        "fresh": 0,
        "offline": 0,
        "stale": 0,
        "paused": 0,
        "mining": 0,
    }
    for miner in rows:
        counts["total"] += 1
        state = miner["last_state"] or "UNKNOWN"
        seen = miner["last_seen"]

        if state == "OFFLINE":
            counts["offline"] += 1
            continue
        if (
            state not in HEALTHY_STATES
            or seen is None
            or not -5 <= now - float(seen) <= fresh_seconds
        ):
            counts["stale"] += 1
            continue

        counts["fresh"] += 1
        if state == "PAUSED":
            counts["paused"] += 1
        elif state == "MINING":
            counts["mining"] += 1

    counts["unavailable"] = counts["offline"] + counts["stale"]
    counts["status"] = (
        "empty" if counts["total"] == 0
        else "ok" if counts["unavailable"] == 0
        else "degraded"
    )
    return counts


def classify_incident(
    fleet, *, app_available, vpn_service_ok, vpn_route_ok,
    min_miners=MASS_OUTAGE_MIN_MINERS,
    fraction=MASS_OUTAGE_FRACTION,
):
    """Return a fixed problem code or None; no individual ASIC secrets."""
    if not vpn_service_ok:
        return "VPN_SERVICE_UNAVAILABLE"
    if not vpn_route_ok:
        return "VPN_ROUTE_UNAVAILABLE"
    if not app_available:
        return "APPLICATION_UNAVAILABLE"

    total = int(fleet["total"])
    required = max(int(min_miners), ceil(total * float(fraction)))
    if total >= min_miners and fleet["unavailable"] >= required:
        return "FARM_MASS_UNAVAILABLE"
    return None


def idle_state():
    return {
        "phase": "idle",
        "bad_since": None,
        "active_since": None,
        "good_since": None,
        "alert_sent": False,
        "reason": None,
    }


def advance_alarm(
    state, problem, now, *,
    outage_grace=OUTAGE_GRACE_SECONDS,
    recovery_grace=RECOVERY_GRACE_SECONDS,
):
    """Return (new state, pending event). The caller records send success.

    Only events ALERT/RECOVERY are deliverable. A failed delivery remains
    pending for later retries. No recurring reminders/spam.
    """
    state = dict(state or idle_state())
    if state.get("phase") not in ("idle", "active"):
        state = idle_state()

    if problem:
        state["good_since"] = None
        if state["phase"] == "idle":
            if state.get("bad_since") is None:
                state["bad_since"] = int(now)
            if now - state["bad_since"] >= outage_grace:
                state["phase"] = "active"
                state["active_since"] = int(state["bad_since"])
                state["reason"] = str(problem)
                state["alert_sent"] = False
        if state["phase"] == "active" and not state["alert_sent"]:
            return state, "ALERT"
        return state, None

    if state["phase"] == "idle":
        return idle_state(), None

    if state.get("good_since") is None:
        state["good_since"] = int(now)
    if now - state["good_since"] >= recovery_grace:
        if state["alert_sent"]:
            return state, "RECOVERY"
        return idle_state(), None
    return state, None


__all__ = (
    "classify_fleet", "classify_incident", "idle_state",
    "advance_alarm", "FRESH_SECONDS", "OUTAGE_GRACE_SECONDS",
    "RECOVERY_GRACE_SECONDS", "MASS_OUTAGE_FRACTION",
    "MASS_OUTAGE_MIN_MINERS",
)
