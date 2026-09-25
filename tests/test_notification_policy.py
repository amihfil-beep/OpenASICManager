import os
import tempfile
import unittest
from unittest.mock import patch

import config as app_config

from anomalies import service as anomaly_service
from anomalies.policy import DEFAULT_ANOMALY_POLICY
from audit.service import AuditRuntime
from db import db, init_db
from notifications import telegram
from notifications.policy import (
    DEFAULT_NOTIFICATION_POLICY,
    apply_notification_policy_patch,
    notification_delivery_allowed,
    notification_policy_field_for_action,
    normalize_notification_policy,
)
from notifications.repository import (
    load_notification_policy,
    save_notification_policy,
)


class NotificationPolicyPureTests(
    unittest.TestCase,
):

    def test_defaults_preserve_existing_event_delivery(self):

        policy = (
            normalize_notification_policy({})
        )

        self.assertTrue(
            policy["issue_open"]
        )

        self.assertTrue(
            policy["issue_resolved"]
        )

        self.assertTrue(
            policy["control_failure"]
        )

        self.assertFalse(
            policy["acknowledgement"]
        )

        self.assertFalse(
            policy["maintenance"]
        )


    def test_existing_actions_map_to_expected_classes(self):

        self.assertEqual(
            notification_policy_field_for_action(
                "ISSUE_OPEN"
            ),
            "issue_open",
        )

        self.assertEqual(
            notification_policy_field_for_action(
                "ISSUE_RESOLVED"
            ),
            "issue_resolved",
        )

        for action in (
            "PAUSE_FAILED",
            "RESUME_FAILED",
            "REBOOT_FAILED",
        ):

            with self.subTest(
                action=action
            ):

                self.assertEqual(
                    notification_policy_field_for_action(
                        action
                    ),
                    "control_failure",
                )


    def test_acknowledgement_and_maintenance_actions_are_mapped(self):

        for action in (
            "ISSUE_ACKNOWLEDGE",
            "ISSUE_UNACKNOWLEDGE",
        ):
            with self.subTest(
                action=action
            ):
                self.assertEqual(
                    notification_policy_field_for_action(
                        action
                    ),
                    "acknowledgement",
                )

        for action in (
            "MAINTENANCE_CREATE",
            "MAINTENANCE_EXTEND",
            "MAINTENANCE_END",
        ):
            with self.subTest(
                action=action
            ):
                self.assertEqual(
                    notification_policy_field_for_action(
                        action
                    ),
                    "maintenance",
                )


    def test_new_event_classes_are_opt_in(self):

        policy = dict(
            DEFAULT_NOTIFICATION_POLICY
        )

        self.assertFalse(
            notification_delivery_allowed(
                "ISSUE_ACKNOWLEDGE",
                policy,
            )
        )

        self.assertFalse(
            notification_delivery_allowed(
                "MAINTENANCE_CREATE",
                policy,
            )
        )

        policy["acknowledgement"] = True
        policy["maintenance"] = True

        self.assertTrue(
            notification_delivery_allowed(
                "ISSUE_ACKNOWLEDGE",
                policy,
            )
        )

        self.assertTrue(
            notification_delivery_allowed(
                "MAINTENANCE_CREATE",
                policy,
            )
        )


    def test_unknown_action_is_not_deliverable(self):

        self.assertFalse(
            notification_delivery_allowed(
                "SOMETHING_ELSE",
                DEFAULT_NOTIFICATION_POLICY,
            )
        )


    def test_partial_patch_changes_only_requested_field(self):

        updated = (
            apply_notification_policy_patch(
                DEFAULT_NOTIFICATION_POLICY,
                {
                    "issue_open": False,
                },
            )
        )

        self.assertFalse(
            updated["issue_open"]
        )

        self.assertTrue(
            updated["issue_resolved"]
        )

        self.assertTrue(
            updated["control_failure"]
        )


    def test_non_boolean_value_is_rejected(self):

        with self.assertRaisesRegex(
            ValueError,
            "must be boolean",
        ):

            apply_notification_policy_patch(
                DEFAULT_NOTIFICATION_POLICY,
                {
                    "issue_open": "false",
                },
            )


class NotificationPolicyPersistenceTests(
    unittest.TestCase,
):

    def setUp(self):

        fd, path = tempfile.mkstemp(
            prefix="oam-notification-policy-",
            suffix=".db",
        )

        os.close(fd)
        os.unlink(path)

        self.path = path
        self.original_db = (
            app_config.DATABASE_PATH
        )

        app_config.DATABASE_PATH = path

        init_db()


    def tearDown(self):

        app_config.DATABASE_PATH = (
            self.original_db
        )

        for suffix in (
            "",
            "-shm",
            "-wal",
        ):

            try:
                os.unlink(
                    self.path + suffix
                )

            except FileNotFoundError:
                pass


    def test_fresh_database_uses_compatible_defaults(self):

        self.assertEqual(
            load_notification_policy(),
            DEFAULT_NOTIFICATION_POLICY,
        )


    def test_policy_round_trip(self):

        policy = dict(
            DEFAULT_NOTIFICATION_POLICY
        )

        policy["issue_open"] = False
        policy["acknowledgement"] = True

        saved = (
            save_notification_policy(
                policy
            )
        )

        self.assertEqual(
            saved,
            policy,
        )

        self.assertEqual(
            load_notification_policy(),
            policy,
        )


class TelegramNotificationRoutingTests(
    unittest.TestCase,
):

    def test_disabled_policy_class_does_not_start_thread(self):

        policy = dict(
            DEFAULT_NOTIFICATION_POLICY
        )

        policy["issue_open"] = False

        original_enabled = (
            telegram.TELEGRAM_NOTIFICATIONS_ENABLED
        )

        telegram.TELEGRAM_NOTIFICATIONS_ENABLED = True

        try:

            with patch.object(
                telegram,
                "load_notification_policy",
                return_value=policy,
            ):

                with patch.object(
                    telegram.threading,
                    "Thread",
                ) as thread:

                    telegram.telegram_event_async(
                        source="SYSTEM",
                        action="ISSUE_OPEN",
                        miner=None,
                        success=False,
                        message="test",
                    )

                    thread.assert_not_called()

        finally:
            telegram.TELEGRAM_NOTIFICATIONS_ENABLED = (
                original_enabled
            )


    def test_enabled_policy_class_starts_thread(self):

        policy = dict(
            DEFAULT_NOTIFICATION_POLICY
        )

        original_enabled = (
            telegram.TELEGRAM_NOTIFICATIONS_ENABLED
        )

        telegram.TELEGRAM_NOTIFICATIONS_ENABLED = True

        try:

            with patch.object(
                telegram,
                "load_notification_policy",
                return_value=policy,
            ):

                with patch.object(
                    telegram.threading,
                    "Thread",
                ) as thread:

                    telegram.telegram_event_async(
                        source="SYSTEM",
                        action="ISSUE_OPEN",
                        miner=None,
                        success=False,
                        message="test",
                    )

                    thread.assert_called_once()

                    thread.return_value.start \
                        .assert_called_once()

        finally:
            telegram.TELEGRAM_NOTIFICATIONS_ENABLED = (
                original_enabled
            )


    def test_policy_failure_does_not_start_thread(self):

        original_enabled = (
            telegram.TELEGRAM_NOTIFICATIONS_ENABLED
        )

        telegram.TELEGRAM_NOTIFICATIONS_ENABLED = True

        try:

            with patch.object(
                telegram,
                "load_notification_policy",
                side_effect=RuntimeError(
                    "database unavailable"
                ),
            ):

                with patch.object(
                    telegram.threading,
                    "Thread",
                ) as thread:

                    telegram.telegram_event_async(
                        source="SYSTEM",
                        action="ISSUE_OPEN",
                        miner=None,
                        success=False,
                        message="test",
                    )

                    thread.assert_not_called()

        finally:
            telegram.TELEGRAM_NOTIFICATIONS_ENABLED = (
                original_enabled
            )


class NotificationPolicySemanticIntegrationTests(
    unittest.TestCase,
):

    def setUp(self):

        fd, path = tempfile.mkstemp(
            prefix="oam-notification-separation-",
            suffix=".db",
        )

        os.close(fd)
        os.unlink(path)

        self.path = path

        self.original_db = (
            app_config.DATABASE_PATH
        )

        app_config.DATABASE_PATH = path

        init_db()

        conn = db()

        cursor = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                enabled,
                last_state,
                temp
            )
            VALUES
            (
                'POLICY-TEST',
                '192.0.2.59',
                'bitmain_stock',
                1,
                'OFFLINE',
                70
            )
        """)

        self.miner_id = (
            cursor.lastrowid
        )

        conn.commit()
        conn.close()


    def tearDown(self):

        app_config.DATABASE_PATH = (
            self.original_db
        )

        for suffix in (
            "",
            "-shm",
            "-wal",
        ):

            try:
                os.unlink(
                    self.path + suffix
                )

            except FileNotFoundError:
                pass


    def test_disabled_issue_open_suppresses_delivery_only(self):

        notification_policy = dict(
            DEFAULT_NOTIFICATION_POLICY
        )

        notification_policy[
            "issue_open"
        ] = False


        anomaly_policy = dict(
            DEFAULT_ANOMALY_POLICY
        )

        anomaly_policy[
            "offline_grace_seconds"
        ] = 0


        audit_runtime = AuditRuntime(
            notify_event=(
                telegram.telegram_event_async
            )
        )


        runtime = (
            anomaly_service.AnomalyRuntime(
                log_event=(
                    audit_runtime.log_event
                ),
                stop_event=None,
                effective_schedule_states=(
                    lambda miners, when: {
                        int(miner["id"]): {
                            "desired_state":
                                None,
                        }
                        for miner
                        in miners
                    }
                ),
            )
        )


        original_enabled = (
            telegram.TELEGRAM_NOTIFICATIONS_ENABLED
        )

        telegram.TELEGRAM_NOTIFICATIONS_ENABLED = (
            True
        )

        try:

            with patch.object(
                telegram,
                "load_notification_policy",
                return_value=notification_policy,
            ):

                with patch.object(
                    telegram.threading,
                    "Thread",
                ) as thread:

                    # First observation creates the
                    # zero-grace anomaly candidate.
                    anomaly_service.anomaly_scan(
                        runtime,
                        anomaly_policy=anomaly_policy,
                    )

                    # Second observation promotes the
                    # candidate to a persistent issue
                    # and writes ISSUE_OPEN to audit.
                    anomaly_service.anomaly_scan(
                        runtime,
                        anomaly_policy=anomaly_policy,
                    )

                    thread.assert_not_called()

        finally:

            telegram.TELEGRAM_NOTIFICATIONS_ENABLED = (
                original_enabled
            )


        conn = db()

        try:

            issue = conn.execute("""
                SELECT
                    id,
                    status,
                    code
                FROM issues
                WHERE
                    miner_id=?
                    AND code='OFFLINE'
                ORDER BY id DESC
                LIMIT 1
            """, (
                self.miner_id,
            )).fetchone()


            audit = conn.execute("""
                SELECT
                    action,
                    miner_id,
                    success
                FROM action_log
                WHERE
                    action='ISSUE_OPEN'
                    AND miner_id=?
                ORDER BY id DESC
                LIMIT 1
            """, (
                self.miner_id,
            )).fetchone()

        finally:
            conn.close()


        self.assertIsNotNone(
            issue
        )

        self.assertEqual(
            issue["status"],
            "ACTIVE",
        )

        self.assertEqual(
            issue["code"],
            "OFFLINE",
        )


        self.assertIsNotNone(
            audit
        )

        self.assertEqual(
            audit["action"],
            "ISSUE_OPEN",
        )

        self.assertEqual(
            audit["miner_id"],
            self.miner_id,
        )

        self.assertFalse(
            bool(
                audit["success"]
            )
        )


if __name__ == "__main__":
    unittest.main()
