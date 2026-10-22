import unittest

from ui.dashboard import (
    TABLE_LAYOUT_SCRIPT_PATH,
    TABLE_LAYOUT_STYLE_PATH,
    dashboard_html,
)


class TableLayoutTests(unittest.TestCase):
    def test_layout_assets_exist(self):
        self.assertTrue(TABLE_LAYOUT_STYLE_PATH.is_file())
        self.assertTrue(TABLE_LAYOUT_SCRIPT_PATH.is_file())

    def test_dashboard_injects_layout_assets(self):
        content = dashboard_html()

        expected = (
            "data-openasic-table-layout",
            "asic-table-layout-toolbar",
            "asic-table-top-scroll",
            "asic-column-resizer",
            "RESET COLUMNS",
            "openasicmanager.minerTable.columnWidths.v1",
            "table.miner-table",
            "localStorage.setItem",
            "localStorage.removeItem",
            "setPointerCapture",
            "event.stopPropagation()",
        )

        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(marker, content)

    def test_layout_targets_only_main_miner_table(self):
        script = TABLE_LAYOUT_SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn(
            'document.querySelector("table.miner-table")',
            script,
        )
        self.assertNotIn("schedule-rules-table", script)
        self.assertNotIn("log-table", script)
        self.assertNotIn("serviceHistoryRows", script)

    def test_top_and_bottom_scroll_are_synchronized(self):
        script = TABLE_LAYOUT_SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn(
            'topScroll.addEventListener("scroll"',
            script,
        )
        self.assertIn(
            'wrap.addEventListener("scroll"',
            script,
        )
        self.assertIn("target.scrollLeft = source.scrollLeft", script)

    def test_column_widths_are_bounded_and_persisted(self):
        script = TABLE_LAYOUT_SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("const MIN_WIDTH = 64", script)
        self.assertIn("const SELECT_WIDTH = 46", script)
        self.assertIn("saveWidths(widths)", script)
        self.assertIn("loadSavedWidths(headers.length)", script)
        self.assertIn("naturalWidths.slice()", script)


if __name__ == "__main__":
    unittest.main()
