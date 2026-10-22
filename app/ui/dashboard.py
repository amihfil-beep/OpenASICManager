"""Dashboard template loading."""

from pathlib import Path


DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")
TABLE_LAYOUT_STYLE_PATH = Path(__file__).with_name("table_layout.css")
TABLE_LAYOUT_SCRIPT_PATH = Path(__file__).with_name("table_layout.js")


def _dashboard_enhancements():
    style = TABLE_LAYOUT_STYLE_PATH.read_text(encoding="utf-8")
    script = TABLE_LAYOUT_SCRIPT_PATH.read_text(encoding="utf-8")
    return (
        f"\n<style data-openasic-table-layout>\n{style}\n</style>\n",
        f"\n<script data-openasic-table-layout>\n{script}\n</script>\n",
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
    "dashboard_html",
)
