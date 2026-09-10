import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
