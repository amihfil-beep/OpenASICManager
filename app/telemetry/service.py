"""
Telemetry background service.

Runs periodic telemetry snapshot collection.

Application-owned logging and shutdown signalling are
supplied explicitly through TelemetryRuntime.
"""

import logging

from telemetry.repository import (
    save_telemetry_snapshot,
)


LOGGER = logging.getLogger(__name__)
TELEMETRY_INTERVAL = 300


class TelemetryRuntime:

    def __init__(
        self,
        log_event,
        stop_event,
    ):

        self.log_event = (
            log_event
        )

        self.stop_event = (
            stop_event
        )


    def run(self):

        return telemetry_loop(
            self
        )


__all__ = (
    "TELEMETRY_INTERVAL",
    "TelemetryRuntime",
    "telemetry_loop",
)


def telemetry_loop(runtime):

    # Даём poller сначала получить
    # актуальные данные после старта сервиса.
    if runtime.stop_event.wait(30):
        return


    while not runtime.stop_event.is_set():

        try:

            save_telemetry_snapshot()

        except Exception as exc:

            LOGGER.exception("Telemetry snapshot failed")
            try:
                runtime.log_event(
                    source="SYSTEM",
                    action="TELEMETRY_ERROR",
                    success=False,
                    message=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                # Audit writes can fail in the same SQLite outage.
                LOGGER.exception("Unable to persist telemetry error")


        if runtime.stop_event.wait(
            TELEMETRY_INTERVAL
        ):

            return
