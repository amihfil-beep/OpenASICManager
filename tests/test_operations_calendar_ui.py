import unittest

from ui.dashboard import dashboard_html


class OperationsCalendarUITests(unittest.TestCase):
    def test_dashboard_contains_operations_calendar_workflow(self):
        content = dashboard_html()

        expected = (
            "data-openasic-operations-calendar",
            "operationsCalendarButton",
            "OPERATIONS 0",
            "operationsCalendarBackdrop",
            "Operations Calendar",
            "operationsCalendarScope",
            "operationsCalendarUpcomingDays",
            "operationsCalendarRecentDays",
            "operationsCalendarActiveCount",
            "operationsCalendarUpcomingCount",
            "operationsCalendarRecentCount",
            "operationsCalendarActiveRows",
            "operationsCalendarUpcomingRows",
            "operationsCalendarRecentRows",
            "ONE-OFF",
            "RECURRING",
            "current preview",
            "snapshot",
            "loadOperationsCalendar",
            "openOperationsCalendar",
            "/api/maintenance/calendar",
            "affected_miner_ids",
            "operations-upcoming-mini",
            "MutationObserver",
            "Plan/fact view",
            "separate from ASIC control",
        )

        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    content,
                )

    def test_operations_calendar_ui_does_not_call_control_endpoints(self):
        content = dashboard_html()

        start = content.index(
            '<script data-openasic-operations-calendar>'
        )
        end = content.index(
            "</script>",
            start,
        )
        script = content[start:end]

        self.assertNotIn(
            "/api/control/",
            script,
        )
        self.assertNotIn(
            "queue_control",
            script,
        )


if __name__ == "__main__":
    unittest.main()
