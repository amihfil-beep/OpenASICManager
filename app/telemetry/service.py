"""
Telemetry background service.

Runs periodic telemetry snapshot collection.

Application-owned logging and shutdown signalling are
supplied explicitly through TelemetryRuntime.
"""

from telemetry.repository import (
    save_telemetry_snapshot,
)


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

            runtime.log_event(
                source="SYSTEM",
                action="TELEMETRY_ERROR",
                success=False,
                message=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )


        if runtime.stop_event.wait(
            TELEMETRY_INTERVAL
        ):

            return
