"""Background materializer for recurring maintenance windows."""

from datetime import datetime
import logging
import time

from recurring_maintenance.policy import (
    TIMEZONE,
    active_occurrences,
    occurrence_key,
)
from recurring_maintenance.repository import (
    list_enabled_recurring_rules,
    materialize_occurrence,
)


LOGGER = logging.getLogger(__name__)
MATERIALIZER_INTERVAL = 30


def materialize_due_occurrences(now=None):
    if now is None:
        now = datetime.now(TIMEZONE)

    now_epoch = int(now.timestamp())
    created = []

    for rule in list_enabled_recurring_rules():
        for start, end in active_occurrences(rule, now):
            try:
                item = materialize_occurrence(
                    rule_id=rule["id"],
                    occurrence_key=occurrence_key(start),
                    starts_at=int(start.timestamp()),
                    ends_at=int(end.timestamp()),
                    now=now_epoch,
                )
            except Exception:
                LOGGER.exception(
                    "Recurring maintenance rule #%s materialization failed",
                    rule["id"],
                )
                continue

            if item is not None:
                created.append(item)

    return created


class RecurringMaintenanceRuntime:
    def __init__(self, stop_event, log_event=None):
        self.stop_event = stop_event
        self.log_event = log_event

    def run(self):
        while not self.stop_event.is_set():
            try:
                created = materialize_due_occurrences()

                if self.log_event is not None:
                    for item in created:
                        self.log_event(
                            source="SCHEDULER",
                            action="MAINTENANCE_RECURRING_MATERIALIZE",
                            success=True,
                            message=(
                                "Recurring maintenance rule "
                                f"#{item['rule_id']} created "
                                f"window #{item['window_id']}"
                            ),
                        )

            except Exception:
                LOGGER.exception(
                    "Recurring maintenance materializer iteration failed"
                )

            self.stop_event.wait(
                MATERIALIZER_INTERVAL
            )


__all__ = (
    "MATERIALIZER_INTERVAL",
    "materialize_due_occurrences",
    "RecurringMaintenanceRuntime",
)
