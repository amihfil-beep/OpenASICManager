import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.operations import create_operations_router


class OperationsRoutesTests(unittest.TestCase):
    def endpoint(self, path, method):
        router = create_operations_router()
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    def test_router_exposes_operational_paths(self):
        router = create_operations_router()
        paths = {
            route.path
            for route in router.routes
        }
        self.assertEqual(paths, {
            "/api/logs",
            "/api/control/jobs",
            "/api/issues",
            "/api/maintenance/calendar",
        })

    @patch("api.operations.action_log_entries")
    def test_logs_delegate_to_audit_service(self, entries):
        entries.return_value = [{"action": "TEST"}]
        endpoint = self.endpoint("/api/logs", "GET")
        result = endpoint(25)
        self.assertEqual(result["logs"], [{"action": "TEST"}])
        self.assertEqual(entries.call_args.args[0], 25)

    @patch("api.operations.control_job_items")
    @patch("api.operations.list_control_jobs")
    def test_control_jobs_clamps_limit(self, list_jobs, items):
        list_jobs.return_value = ["row"]
        items.return_value = [{"id": 1}]
        endpoint = self.endpoint("/api/control/jobs", "GET")
        result = endpoint(9999)
        list_jobs.assert_called_once_with(500)
        items.assert_called_once_with(["row"])
        self.assertEqual(result["jobs"], [{"id": 1}])

    @patch("api.operations.issue_report")
    def test_issues_clamps_limit(self, report):
        report.return_value = []
        endpoint = self.endpoint("/api/issues", "GET")
        self.assertEqual(endpoint(0), [])
        report.assert_called_once_with(1)

    @patch("api.operations.operations_calendar_report")
    def test_calendar_delegates_filters_to_read_model(self, report):
        report.return_value = {
            "active": [],
            "upcoming": [],
            "recent": [],
        }

        endpoint = self.endpoint(
            "/api/maintenance/calendar",
            "GET",
        )

        result = endpoint(
            "GROUP",
            12,
            30,
            14,
            250,
        )

        report.assert_called_once_with(
            scope="GROUP",
            miner_id=12,
            upcoming_days=30,
            recent_days=14,
            limit=250,
        )
        self.assertEqual(
            result,
            report.return_value,
        )

    @patch("api.operations.operations_calendar_report")
    def test_calendar_validation_error_becomes_http_400(self, report):
        report.side_effect = ValueError("scope must be valid")

        endpoint = self.endpoint(
            "/api/maintenance/calendar",
            "GET",
        )

        with self.assertRaises(HTTPException) as ctx:
            endpoint(
                "INVALID",
                None,
                14,
                7,
                200,
            )

        self.assertEqual(
            ctx.exception.status_code,
            400,
        )
        self.assertEqual(
            ctx.exception.detail,
            "scope must be valid",
        )


if __name__ == "__main__":
    unittest.main()
