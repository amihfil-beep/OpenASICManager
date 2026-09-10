"""Dashboard template loading."""

from pathlib import Path


DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")


def dashboard_html():
    return DASHBOARD_PATH.read_text(encoding="utf-8")


__all__ = (
    "DASHBOARD_PATH",
    "dashboard_html",
)
