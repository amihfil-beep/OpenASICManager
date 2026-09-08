"""
Verified ASIC control workers.

Long-running control and reboot verification lives here.

Application-specific services such as polling, audit logging
and shutdown signalling are supplied explicitly through
ControlRuntime. The worker module therefore never imports
the main application module.
"""

import time

from db import (
    get_control_job,
    get_miner,
    update_control_job,
    set_last_command,
)

from control.dispatch import (
    control,
    send_reboot_command,
)

from control.policy import (
    control_timeout,
    control_retry_after,
    should_retry_command,
)


CONTROL_VERIFY_INTERVAL = 5
CONTROL_RETRY_DELAY_ON_ERROR = 10
CONTROL_MAX_ATTEMPTS = 3
REBOOT_POLL_INTERVAL = 5
REBOOT_TRANSITION_TIMEOUT = 120
REBOOT_RETURN_TIMEOUT = 600
REBOOT_STABLE_POLLS = 2


class ControlRuntime:

    def __init__(
        self,
        poll_miner,
        log_event,
        stop_event,
    ):

        self.poll_miner = (
            poll_miner
        )

        self.log_event = (
            log_event
        )

        self.stop_event = (
            stop_event
        )


__all__ = (
    "CONTROL_MAX_ATTEMPTS",
    "ControlRuntime",
    "finish_control_job",
    "verified_control_worker",
    "reboot_worker",
)


def finish_control_job(
    runtime,
    job_id,
    miner,
    source,
    action,
    success,
    final_state,
    message,
):

    now = int(
        time.time()
    )

    update_control_job(
        job_id,

        status=(
            "VERIFIED"
            if success
            else "FAILED"
        ),

        completed_at=now,
        final_state=final_state,
        message=message,
    )

    runtime.log_event(
        source=source,

        action=(
            f"{action.upper()}_VERIFIED"
            if success
            else f"{action.upper()}_FAILED"
        ),

        miner=miner,
        success=success,

        message=message,
    )


def verified_control_worker(
    runtime,job_id):

    job = get_control_job(
        job_id
    )

    if not job:
        return


    miner_id = job[
        "miner_id"
    ]

    action = job[
        "action"
    ]

    source = job[
        "source"
    ]

    target = job[
        "target_state"
    ]


    miner = get_miner(
        miner_id
    )

    if not miner:

        update_control_job(
            job_id,
            status="FAILED",
            completed_at=int(
                time.time()
            ),
            message="Miner not found",
        )

        return


    update_control_job(
        job_id,
        status="RUNNING",
        started_at=int(
            time.time()
        ),
    )


    timeout_seconds = (
        control_timeout(
            action,
            miner,
        )
    )

    retry_after = (
        control_retry_after(
            miner
        )
    )

    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    attempts = 0
    last_send_monotonic = 0
    last_state = (
        miner["last_state"]
        or "UNKNOWN"
    )


    while (
        not runtime.stop_event.is_set()
        and time.monotonic()
        < deadline
    ):

        # ----------------------------------------------------
        # SEND / RETRY
        # ----------------------------------------------------

        need_send = False

        if attempts == 0:

            need_send = True

        elif (
            attempts
            < CONTROL_MAX_ATTEMPTS
            and
            (
                time.monotonic()
                - last_send_monotonic
            )
            >= retry_after
            and
            should_retry_command(
                action,
                last_state,
            )
        ):

            need_send = True


        if need_send:

            attempts += 1

            update_control_job(
                job_id,
                attempts=attempts,
            )


            try:

                miner = get_miner(
                    miner_id
                )

                result = control(
                    miner,
                    action,
                )

                last_send_monotonic = (
                    time.monotonic()
                )

                set_last_command(
                    miner_id,
                    action,
                )

                runtime.log_event(
                    source=source,

                    action=(
                        f"{action.upper()}_SENT"
                        if attempts == 1
                        else
                        f"{action.upper()}_RETRY"
                    ),

                    miner=miner,
                    success=True,

                    message=(
                        f"Attempt "
                        f"{attempts}/"
                        f"{CONTROL_MAX_ATTEMPTS}"
                        f"; response="
                        f"{result or 'OK'}"
                    ),
                )


            except Exception as exc:

                last_send_monotonic = (
                    time.monotonic()
                )

                message = (
                    f"Attempt "
                    f"{attempts}/"
                    f"{CONTROL_MAX_ATTEMPTS}"
                    f": "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                runtime.log_event(
                    source=source,
                    action=(
                        f"{action.upper()}_SEND_FAILED"
                    ),
                    miner=miner,
                    success=False,
                    message=message,
                )


                if (
                    attempts
                    >= CONTROL_MAX_ATTEMPTS
                ):

                    finish_control_job(runtime,
                        job_id=job_id,
                        miner=miner,
                        source=source,
                        action=action,
                        success=False,
                        final_state=last_state,
                        message=(
                            "Command could not be sent "
                            "after maximum attempts. "
                            + message
                        ),
                    )

                    return


                runtime.stop_event.wait(
                    CONTROL_RETRY_DELAY_ON_ERROR
                )

                continue


        # ----------------------------------------------------
        # VERIFY REAL ASIC STATE
        # ----------------------------------------------------

        if runtime.stop_event.wait(
            CONTROL_VERIFY_INTERVAL
        ):

            return


        runtime.poll_miner(
            miner_id
        )


        fresh = get_miner(
            miner_id
        )

        if not fresh:
            return


        last_state = (
            fresh["last_state"]
            or "UNKNOWN"
        )


        update_control_job(
            job_id,
            final_state=last_state,

            message=(
                f"Waiting for "
                f"{target}; "
                f"current={last_state}; "
                f"attempt="
                f"{attempts}/"
                f"{CONTROL_MAX_ATTEMPTS}; "
                f"timeout="
                f"{timeout_seconds}s"
            ),
        )


        if last_state == target:

            finish_control_job(runtime,
                job_id=job_id,
                miner=fresh,
                source=source,
                action=action,
                success=True,
                final_state=last_state,

                message=(
                    f"Verified {target}; "
                    f"attempts={attempts}"
                ),
            )

            return


    # --------------------------------------------------------
    # TIMEOUT
    # --------------------------------------------------------

    miner = get_miner(
        miner_id
    )

    if miner:

        last_state = (
            miner["last_state"]
            or last_state
        )


    finish_control_job(runtime,
        job_id=job_id,
        miner=miner,
        source=source,
        action=action,
        success=False,
        final_state=last_state,

        message=(
            f"Verification timeout. "
            f"Expected={target}; "
            f"actual={last_state}; "
            f"attempts={attempts}/"
            f"{CONTROL_MAX_ATTEMPTS}"
        ),
    )


def reboot_worker(
    runtime,
    job_id,
):

    job = get_control_job(
        job_id
    )


    if not job:
        return


    miner_id = job[
        "miner_id"
    ]


    miner = get_miner(
        miner_id
    )


    if not miner:

        update_control_job(
            job_id,
            status="FAILED",
            completed_at=int(
                time.time()
            ),
            message="Miner not found",
        )

        return


    source = job[
        "source"
    ]


    initial_state = (
        miner["last_state"]
        or "UNKNOWN"
    )


    update_control_job(
        job_id,
        status="RUNNING",
        started_at=int(
            time.time()
        ),
        attempts=1,
        final_state=
            initial_state,
        message=(
            "Sending full reboot command"
        ),
    )


    # --------------------------------------------------------
    # SEND EXACTLY ONCE
    # --------------------------------------------------------

    try:

        result = (
            send_reboot_command(
                miner
            )
        )


        set_last_command(
            miner_id,
            "reboot",
        )


        runtime.log_event(
            source=source,
            action="REBOOT_SENT",
            miner=miner,
            success=True,
            message=(
                f"Attempt 1/1; "
                f"response={result}"
            ),
        )


    except Exception as exc:

        finish_control_job(runtime,
            job_id=job_id,
            miner=miner,
            source=source,
            action="reboot",
            success=False,
            final_state=
                initial_state,
            message=(
                "Unable to send reboot command: "
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        )

        return


    # --------------------------------------------------------
    # PHASE 1:
    # ACTUAL REBOOT TRANSITION MUST BE OBSERVED
    # --------------------------------------------------------

    transition_deadline = (
        time.monotonic()
        +
        REBOOT_TRANSITION_TIMEOUT
    )


    transition_seen = False
    transition_state = None


    while (
        not runtime.stop_event.is_set()
        and
        time.monotonic()
        < transition_deadline
    ):

        if runtime.stop_event.wait(
            REBOOT_POLL_INTERVAL
        ):

            return


        try:

            runtime.poll_miner(
                miner_id
            )

        except Exception:
            pass


        fresh = get_miner(
            miner_id
        )


        if not fresh:
            continue


        state = (
            fresh["last_state"]
            or "UNKNOWN"
        )


        update_control_job(
            job_id,
            final_state=state,
            message=(
                "Waiting for reboot transition; "
                f"current={state}"
            ),
        )


        # OFFLINE is the strongest evidence.
        #
        # STARTING / RESTARTING is also accepted if the
        # previous state was different. This prevents a
        # very fast reboot being incorrectly reported as
        # failed simply because the 5 second poll missed
        # the offline interval.

        if state in (
            "OFFLINE",
            "UNKNOWN",
        ):

            transition_seen = True
            transition_state = state

            break


        if (
            state in (
                "STARTING",
                "RESTARTING",
            )
            and
            state != initial_state
        ):

            transition_seen = True
            transition_state = state

            break


    if not transition_seen:

        fresh = get_miner(
            miner_id
        )


        final_state = (
            fresh["last_state"]
            if fresh
            else initial_state
        )


        finish_control_job(runtime,
            job_id=job_id,
            miner=(
                fresh
                or miner
            ),
            source=source,
            action="reboot",
            success=False,
            final_state=
                final_state,
            message=(
                "Reboot command was sent, "
                "but reboot transition was not observed "
                f"within {REBOOT_TRANSITION_TIMEOUT}s"
            ),
        )

        return


    runtime.log_event(
        source=source,
        action="REBOOT_TRANSITION",
        miner=(
            get_miner(
                miner_id
            )
            or miner
        ),
        success=True,
        message=(
            "Reboot transition observed; "
            f"state={transition_state}"
        ),
    )


    # --------------------------------------------------------
    # PHASE 2:
    # WAIT FOR DEVICE TO RETURN
    # --------------------------------------------------------

    return_deadline = (
        time.monotonic()
        +
        REBOOT_RETURN_TIMEOUT
    )


    stable_polls = 0
    final_state = transition_state


    while (
        not runtime.stop_event.is_set()
        and
        time.monotonic()
        < return_deadline
    ):

        if runtime.stop_event.wait(
            REBOOT_POLL_INTERVAL
        ):

            return


        try:

            runtime.poll_miner(
                miner_id
            )

        except Exception:
            pass


        fresh = get_miner(
            miner_id
        )


        if not fresh:

            stable_polls = 0
            continue


        state = (
            fresh["last_state"]
            or "UNKNOWN"
        )


        final_state = state


        update_control_job(
            job_id,
            final_state=state,
            message=(
                "Waiting for ASIC to return; "
                f"current={state}; "
                f"stable="
                f"{stable_polls}/"
                f"{REBOOT_STABLE_POLLS}"
            ),
        )


        # Reboot is considered complete only when
        # the miner reaches a stable operational state.
        #
        # STARTING / RESTARTING proves that the device
        # returned, but does not yet mean the reboot
        # completed successfully.

        if state in (
            "MINING",
            "PAUSED",
        ):

            stable_polls += 1

        else:

            stable_polls = 0


        if (
            stable_polls
            >= REBOOT_STABLE_POLLS
        ):

            finish_control_job(runtime,
                job_id=job_id,
                miner=fresh,
                source=source,
                action="reboot",
                success=True,
                final_state=state,
                message=(
                    "Full reboot verified; "
                    f"transition={transition_state}; "
                    f"returned_state={state}; "
                    f"stable_polls="
                    f"{stable_polls}"
                ),
            )

            return


    # --------------------------------------------------------
    # RETURN TIMEOUT
    # --------------------------------------------------------

    fresh = get_miner(
        miner_id
    )


    finish_control_job(runtime,
        job_id=job_id,
        miner=(
            fresh
            or miner
        ),
        source=source,
        action="reboot",
        success=False,
        final_state=(
            (
                fresh["last_state"]
                if fresh
                else final_state
            )
            or "UNKNOWN"
        ),
        message=(
            "Reboot transition was observed, "
            "but ASIC did not return reliably "
            f"within {REBOOT_RETURN_TIMEOUT}s"
        ),
    )
