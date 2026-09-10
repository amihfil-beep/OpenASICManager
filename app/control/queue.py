"""
Control job queue.

Owns queue serialization and the executor used for verified
pause/resume and reboot jobs.

Application-specific audit and scheduler callbacks are
provided through QueueRuntime. This module never imports
the main application module.
"""

import threading
import time

from concurrent.futures import ThreadPoolExecutor

from db import get_miner

from control.repository import (
    create_control_job,
    get_active_control_job,
    set_manual_override,
)

from control.policy import (
    control_target,
)

from control.worker import (
    CONTROL_MAX_ATTEMPTS,
    reboot_worker,
    verified_control_worker,
)


CONTROL_WORKERS = 32


control_queue_lock = threading.Lock()

control_executor = ThreadPoolExecutor(
    max_workers=CONTROL_WORKERS
)


class QueueRuntime:

    def __init__(
        self,
        audit_source,
        log_event,
        next_transition,
        control_runtime,
    ):

        self.audit_source = (
            audit_source
        )

        self.log_event = (
            log_event
        )

        self.next_transition = (
            next_transition
        )

        self.control_runtime = (
            control_runtime
        )


    def queue_control(
        self,
        miner_id,
        action,
        manual=False,
    ):

        return queue_control(
            self,
            miner_id,
            action,
            manual,
        )


    def queue_reboot(
        self,
        miner_id,
    ):

        return queue_reboot(
            self,
            miner_id,
        )


__all__ = (
    "QueueRuntime",
    "queue_control",
    "queue_reboot",
)


def queue_reboot(
    runtime,
    miner_id,
):

    miner = get_miner(
        miner_id
    )

    if not miner:
        raise RuntimeError(
            "Miner not found"
        )

    if not miner["enabled"]:
        raise RuntimeError(
            "ASIC is disabled"
        )

    if miner["driver"] not in (
        "awesome",
        "bitmain_stock",
    ):
        raise RuntimeError(
            "Firmware does not support reboot"
        )

    # Manual reboot does not create a schedule override.
    with control_queue_lock:
        active = get_active_control_job(
            miner_id
        )

        if active:
            return {
                "queued": False,
                "already_active": True,
                "job_id": active["id"],
                "status": active["status"],
                "action": active["action"],
                "target_state": active["target_state"],
            }

        now = int(time.time())
        source = runtime.audit_source(
            "MANUAL"
        )

        job_id = create_control_job(
            miner=miner,
            source=source,
            action="reboot",
            target_state="REBOOTED",
            max_attempts=1,
            now=now,
        )

    runtime.log_event(
        source="MANUAL",
        action="REBOOT_QUEUED",
        miner=miner,
        success=True,
        message=(
            f"Job #{job_id}; "
            "full device reboot"
        ),
    )

    control_executor.submit(
        reboot_worker,
        runtime.control_runtime,
        job_id,
    )

    return {
        "queued": True,
        "already_active": False,
        "job_id": job_id,
        "status": "QUEUED",
        "action": "reboot",
        "target_state": "REBOOTED",
    }



def queue_control(
    runtime,
    miner_id,
    action,
    manual=False,
):

    if action not in (
        "pause",
        "resume",
    ):
        raise RuntimeError(
            "Invalid action"
        )

    miner = get_miner(
        miner_id
    )

    if not miner:
        raise RuntimeError(
            "Miner not found"
        )

    if not miner["enabled"]:
        raise RuntimeError(
            "ASIC is disabled"
        )

    if miner["driver"] not in (
        "awesome",
        "bitmain_stock",
    ):
        raise RuntimeError(
            "Firmware is not configured"
        )

    source = (
        runtime.audit_source(
            "MANUAL"
        )
        if manual
        else "SCHEDULER"
    )

    target = control_target(
        action
    )

    with control_queue_lock:
        active = get_active_control_job(
            miner_id
        )

        if active:
            return {
                "queued": False,
                "already_active": True,
                "job_id": active["id"],
                "status": active["status"],
                "action": active["action"],
                "target_state": active["target_state"],
            }

        now = int(time.time())

        if manual:
            override_until = int(
                runtime.next_transition()
                .timestamp()
            )
            set_manual_override(
                miner_id,
                override_until,
            )

        job_id = create_control_job(
            miner=miner,
            source=source,
            action=action,
            target_state=target,
            max_attempts=CONTROL_MAX_ATTEMPTS,
            now=now,
        )

    runtime.log_event(
        source=source,
        action=f"{action.upper()}_QUEUED",
        miner=miner,
        success=True,
        message=(
            f"Job #{job_id}; "
            f"target={target}"
        ),
    )

    control_executor.submit(
        verified_control_worker,
        runtime.control_runtime,
        job_id,
    )

    return {
        "queued": True,
        "already_active": False,
        "job_id": job_id,
        "status": "QUEUED",
        "action": action,
        "target_state": target,
    }
