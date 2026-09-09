"""Miner polling and monitoring background service."""

import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)

from control.dispatch import read_status
from db import (
    get_miner,
    get_poll_miners,
)
from monitoring.repository import (
    save_miner_status,
    mark_miner_offline,
)


POLL_INTERVAL = 15


def poll_miner(miner_id):
    miner = get_miner(
        miner_id
    )

    if not miner:
        return

    if not miner["enabled"]:
        return

    if miner["driver"] == "unset":
        return

    now = int(
        time.time()
    )

    try:
        status = read_status(
            miner
        )

        save_miner_status(
            miner_id,
            status,
            now,
        )

    except Exception as exc:
        mark_miner_offline(
            miner_id,
            (
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        )


def polling_loop(runtime):
    while not runtime.stop_event.is_set():

        miners = get_poll_miners()

        if miners:

            workers = min(
                12,
                len(miners),
            )

            with ThreadPoolExecutor(
                max_workers=workers
            ) as executor:

                futures = [
                    executor.submit(
                        runtime.poll_miner,
                        miner["id"],
                    )
                    for miner in miners
                ]

                for future in as_completed(
                    futures
                ):

                    try:
                        future.result()

                    except Exception:
                        pass

        runtime.stop_event.wait(
            POLL_INTERVAL
        )


def delayed_poll(
    runtime,
    miner_id,
):
    time.sleep(3)

    runtime.poll_miner(
        miner_id
    )


class MonitoringRuntime:
    """Application-owned shutdown boundary for monitoring."""

    def __init__(self, stop_event):
        self.stop_event = stop_event

    def poll_miner(self, miner_id):
        return poll_miner(miner_id)

    def run(self):
        return polling_loop(self)

    def delayed_poll(self, miner_id):
        return delayed_poll(
            self,
            miner_id,
        )


__all__ = (
    "POLL_INTERVAL",
    "MonitoringRuntime",
    "poll_miner",
    "polling_loop",
    "delayed_poll",
)
