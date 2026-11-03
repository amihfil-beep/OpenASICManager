"""Dashboard template loading."""

from pathlib import Path


DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")
TABLE_LAYOUT_STYLE_PATH = Path(__file__).with_name("table_layout.css")
TABLE_LAYOUT_SCRIPT_PATH = Path(__file__).with_name("table_layout.js")
OPERATIONS_CALENDAR_STYLE_PATH = Path(__file__).with_name(
    "operations_calendar.css"
)
OPERATIONS_CALENDAR_SCRIPT_PATH = Path(__file__).with_name(
    "operations_calendar.js"
)


def _dashboard_enhancements():
    table_style = TABLE_LAYOUT_STYLE_PATH.read_text(
        encoding="utf-8"
    )
    table_script = TABLE_LAYOUT_SCRIPT_PATH.read_text(
        encoding="utf-8"
    )
    calendar_style = OPERATIONS_CALENDAR_STYLE_PATH.read_text(
        encoding="utf-8"
    )
    calendar_script = OPERATIONS_CALENDAR_SCRIPT_PATH.read_text(
        encoding="utf-8"
    )

    return (
        (
            "\n<style data-openasic-table-layout>\n"
            f"{table_style}\n"
            "</style>\n"
            "<style data-openasic-operations-calendar>\n"
            f"{calendar_style}\n"
            "</style>\n"
        ),
        (
            "\n<script data-openasic-table-layout>\n"
            f"{table_script}\n"
            "</script>\n"
            "<script data-openasic-operations-calendar>\n"
            f"{calendar_script}\n"
            "</script>\n"
        ),
    )


def dashboard_html():
    content = DASHBOARD_PATH.read_text(encoding="utf-8")
    style, script = _dashboard_enhancements()
    content = content.replace("</head>", f"{style}</head>", 1)
    content = content.replace("</body>", f"{script}</body>", 1)
    return content


__all__ = (
    "DASHBOARD_PATH",
    "TABLE_LAYOUT_STYLE_PATH",
    "TABLE_LAYOUT_SCRIPT_PATH",
    "OPERATIONS_CALENDAR_STYLE_PATH",
    "OPERATIONS_CALENDAR_SCRIPT_PATH",
    "dashboard_html",
)
