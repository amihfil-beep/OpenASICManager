import inspect
import re
import unittest

from fastapi.responses import HTMLResponse

import app as application
from ui.dashboard import DASHBOARD_PATH, dashboard_html


class DashboardTemplateTests(unittest.TestCase):
    def test_dashboard_resource_loads(self):
        content = dashboard_html()
        self.assertTrue(DASHBOARD_PATH.is_file())
        self.assertIn("<title>OpenASICManager</title>", content)
        self.assertIn("</html>", content.lower())

    def test_root_route_uses_external_dashboard(self):
        route = next(
            route
            for route in application.app.routes
            if getattr(route, "path", None) == "/"
            and "GET" in getattr(route, "methods", set())
        )

        self.assertFalse(inspect.iscoroutinefunction(route.endpoint))
        response = route.endpoint()
        self.assertIsInstance(response, str)
        self.assertEqual(
            response,
            dashboard_html(),
        )

    def test_dashboard_contains_anomaly_policy_editor(self):
        content = dashboard_html()

        expected = (
            "ALERT POLICY",
            "anomalyPolicyBackdrop",
            "anomalyIntervalSeconds",
            "anomalyOfflineGraceSeconds",
            "anomalyHotTempC",
            "anomalyHotClearC",
            "anomalyHotGraceSeconds",
            "anomalyScheduleGraceSeconds",
            "/api/anomaly-policy",
            "saveAnomalyPolicy",
        )

        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    content,
                )

    def test_dashboard_contains_issue_acknowledgement_workflow(self):
        content = dashboard_html()

        expected = (
            "Acknowledgement",
            "ACKNOWLEDGED",
            "UNACKNOWLEDGED",
            "ACKNOWLEDGE",
            "CLEAR ACK",
            "issueAcknowledgementHTML",
            "issueActionHTML",
            "acknowledgeIssue",
            "unacknowledgeIssue",
            "issueAcknowledgementRequest",
            "/api/issues/${issueId}/acknowledgement",
            "acknowledgement_note",
            "acknowledged_by",
        )

        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    content,
                )


    def test_dashboard_contains_maintenance_window_workflow(self):
        content = dashboard_html()

        expected = (
            "MAINTENANCE 0",
            "maintenanceBackdrop",
            "maintenanceScope",
            "maintenanceMiner",
            "maintenanceStartsAt",
            "maintenanceEndsAt",
            "maintenanceNote",
            "maintenanceRows",
            "maintenanceBadgeHTML",
            "maintenanceWindowsForMiner",
            "openMaintenance",
            "loadMaintenance",
            "createMaintenanceWindow",
            "extendMaintenanceWindow",
            "endMaintenanceWindow",
            "/api/maintenance?limit=100",
            "/api/maintenance/${windowId}",
            "/api/maintenance/${windowId}/end",
            "Maximum duration: 7 days",
            "Telemetry and ASIC control remain independent",
        )

        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    content,
                )


    def test_dashboard_contains_per_miner_anomaly_policy_editor(self):
        content = dashboard_html()

        expected = (
            "Alert policy",
            "minerAnomalyPolicyBackdrop",
            "minerAnomalyPolicyTitle",
            "minerAnomalyPolicyRows",
            "minerAnomalyPolicyInterval",
            "minerAnomalyPolicyStatus",
            "MINER_ANOMALY_POLICY_FIELDS",
            "minerAnomalyPolicyButtonHTML",
            "loadMinerAnomalyPolicySummary",
            "/api/anomaly-policy-overrides",
            "openMinerAnomalyPolicy",
            "renderMinerAnomalyPolicy",
            "minerAnomalyPolicyPayload",
            "saveMinerAnomalyPolicy",
            "clearMinerAnomalyPolicy",
            "GLOBAL ONLY",
            "OVERRIDE",
            "INHERIT ALL",
            "/api/miners/",
            "/anomaly-policy",
            "effective_policy",
            "global_policy",
            "overrides",
            "sources",
        )

        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    content,
                )


    def test_dashboard_contains_miner_group_workflow(self):
        content = dashboard_html()

        expected = (
            "Miner Groups",
            "minerGroupsBackdrop",
            "minerGroupNameInput",
            "minerGroupRows",
            "minerGroupFilter",
            "GROUPS",
            "loadMinerGroups",
            "renderMinerGroupFilter",
            "renderMinerGroupRows",
            "matchesMinerGroup",
            "minerGroupSelectHTML",
            "assignMinerGroup",
            "createMinerGroup",
            "renameMinerGroup",
            "deleteMinerGroup",
            "/api/miner-groups",
            "/api/miners/${minerId}/group",
            "Ungrouped",
            "Changing membership does not",
        )

        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    content,
                )


    def test_dashboard_contains_telegram_incident_policy(self):
        content = dashboard_html()

        expected = (
            "Telegram Incident Policy",
            "notificationPolicyPanel",
            "notificationPolicyDetail",
            "notificationPolicyStatusBadge",
            "notificationPolicyIssueOpen",
            "notificationPolicyIssueResolved",
            "notificationPolicyAcknowledgement",
            "notificationPolicyControlFailure",
            "notificationPolicyMaintenance",
            "saveNotificationPolicyButton",
            "NOTIFICATION_POLICY_CONTROLS",
            "loadNotificationPolicy",
            "renderNotificationPolicy",
            "notificationPolicyPayload",
            "saveNotificationPolicy",
            "/api/notifications/policy",
            "New issue",
            "Recovery / clear",
            "Acknowledgement",
            "Control failure",
            "Maintenance",
            "Farm Summary is independent",
            "Incident notification policy saved",
        )

        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    content,
                )


    def test_history_charts_use_bucket_metadata_and_show_gaps(self):
        content = dashboard_html()

        expected = (
            "historyMetadata",
            "historySegments",
            "metadata.bucket_seconds",
            "metadata.expected_point_count",
            "metadata.missing_point_count",
            "Line breaks indicate missing telemetry",
            "No telemetry for this metric",
        )

        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(marker, content)

    def test_farm_history_does_not_coerce_missing_series_to_zero(self):
        content = dashboard_html()

        missing_as_zero = re.compile(
            r"point\s*\[\s*definition\.field\s*\]\s*\|\|\s*0"
        )

        self.assertIsNone(
            missing_as_zero.search(content)
        )

    def test_dashboard_contains_saved_discovery_profiles(self):
        content = dashboard_html()

        expected = (
            "Saved Networks",
            "Manual single-network scan",
            "discoveryProfileRows",
            "discoveryProfilesScanAllButton",
            "discoveryNetworkResults",
            "/api/discovery/profiles",
            "/api/discovery/profiles/scan",
            "/api/discovery/scan",
            "loadDiscoveryProfiles",
            "scanDiscoveryProfiles",
            "createDiscoveryProfile",
            "editDiscoveryProfile",
            "toggleDiscoveryProfile",
            "deleteDiscoveryProfile",
            "discoveryDeviceSource",
        )

        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    content,
                )




if __name__ == "__main__":
    unittest.main()
