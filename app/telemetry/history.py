"""Shared history range and downsampling policy."""

from dataclasses import dataclass


@dataclass(frozen=True)
class HistoryRange:
    key: str
    hours: int
    bucket_seconds: int

    @property
    def expected_points(self):
        return self.hours * 3600 // self.bucket_seconds


HISTORY_RANGES = {
    24: HistoryRange("24h", 24, 300),
    168: HistoryRange("7d", 168, 900),
    720: HistoryRange("30d", 720, 3600),
    2160: HistoryRange("90d", 2160, 10800),
}


SUPPORTED_HISTORY_HOURS = tuple(HISTORY_RANGES)


def history_range(hours):
    return HISTORY_RANGES.get(hours)


def aligned_history_window(now, selected_range):
    """Return a bounded window spanning exactly the configured buckets."""

    current_bucket = (
        int(now)
        // selected_range.bucket_seconds
        * selected_range.bucket_seconds
    )
    since = (
        current_bucket
        - (selected_range.expected_points - 1)
        * selected_range.bucket_seconds
    )
    return since, int(now)


__all__ = (
    "HISTORY_RANGES",
    "SUPPORTED_HISTORY_HOURS",
    "HistoryRange",
    "aligned_history_window",
    "history_range",
)
