"""
Scheduler service.

Runs the background scheduling loop and records processed
schedule occurrences.

Application-owned logging, control queuing and shutdown
signalling are supplied explicitly through SchedulerRuntime.
"""

from datetime import datetime

from db import get_setting

from scheduler.policy import (
    MOSCOW,
    schedule_days_string,
    schedule_time_string,
)

from scheduler.repository import (
    list_schedulable_miners,
    mark_schedule_rule_seen,
    schedule_state_details,
)


SCHEDULER_INTERVAL = 20
CONTROL_COOLDOWN = 30


class SchedulerRuntime:

    def __init__(
        self,
        log_event,
        queue_control,
        stop_event,
    ):

        self.log_event = (
            log_event
        )

        self.queue_control = (
            queue_control
        )

        self.stop_event = (
            stop_event
        )


    def run(self):

        return scheduler_loop(
            self
        )


__all__ = (
    "SchedulerRuntime",
    "schedule_mark_rule_seen",
    "scheduler_loop",
)


def schedule_mark_rule_seen(
    runtime,
    rule,
    occurrence,
):

    if (
        rule is None
        or
        occurrence is None
    ):
        return


    run_key = (
        occurrence.strftime(
            "%Y-%m-%dT%H:%M%z"
        )
    )


    changed = mark_schedule_rule_seen(
        rule["id"],
        run_key,
    )


    if changed:

        runtime.log_event(
            source="SCHEDULER",
            action="SCHEDULE_RULE_ACTIVE",
            success=True,
            message=(
                f"Rule #{rule['id']} "
                f"{rule['action']} "
                f"{schedule_time_string(rule['time_minutes'])} "
                f"{schedule_days_string(rule['days_mask'])}"
                +
                (
                    f" - {rule['comment']}"
                    if rule["comment"]
                    else ""
                )
            ),
        )


def scheduler_loop(runtime):
    while not runtime.stop_event.is_set():

        scheduler_enabled = (
            get_setting(
                "scheduler_enabled",
                "0",
            )
            == "1"
        )

        if scheduler_enabled:

            now = datetime.now(
                MOSCOW
            )

            now_epoch = int(
                now.timestamp()
            )

            (
                target,
                active_rule,
                active_occurrence,
            ) = schedule_state_details(
                now
            )


            if active_rule is not None:

                schedule_mark_rule_seen(runtime,
                    active_rule,
                    active_occurrence,
                )

            miners = list_schedulable_miners()

            for miner in miners:

                override_until = (
                    miner[
                        "manual_override_until"
                    ]
                    or 0
                )

                if (
                    override_until
                    > now_epoch
                ):
                    continue

                state = (
                    miner[
                        "last_state"
                    ]
                    or "UNKNOWN"
                )

                last_seen = (
                    miner[
                        "last_seen"
                    ]
                    or 0
                )

                last_action_at = (
                    miner[
                        "last_action_at"
                    ]
                    or 0
                )

                if (
                    now_epoch
                    - last_seen
                    > 60
                ):
                    continue

                if (
                    now_epoch
                    - last_action_at
                    < CONTROL_COOLDOWN
                ):
                    continue

                action = None

                if target == "MINING":

                    if state in (
                        "PAUSED",
                        "IDLE",
                    ):

                        action = (
                            "resume"
                        )

                elif target == "PAUSED":

                    if state in (
                        "MINING",
                        "STARTING",
                        "IDLE",
                    ):

                        action = (
                            "pause"
                        )

                if action:

                    try:
                        runtime.queue_control(
                            miner["id"],
                            action,
                            manual=False,
                        )

                    except Exception:
                        pass

        runtime.stop_event.wait(
            SCHEDULER_INTERVAL
        )
