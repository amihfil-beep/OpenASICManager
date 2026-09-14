import os
import tempfile
import unittest

import config as app_config
from api.anomalies import create_anomaly_router
from anomalies.policy import (
    DEFAULT_ANOMALY_POLICY,
    normalize_anomaly_policy,
)
from anomalies.repository import (
    load_anomaly_policy,
    save_anomaly_policy,
)
from db import db, init_db


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class TemporaryDatabaseMixin:
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-anomaly-policy-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        init_db()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass


class AnomalyPolicyPersistenceTests(
    TemporaryDatabaseMixin,
    unittest.TestCase,
):
    def test_fresh_database_uses_0_3_0_defaults(self):
        self.assertEqual(
            load_anomaly_policy(),
            DEFAULT_ANOMALY_POLICY,
        )

    def test_policy_round_trip_persists_all_values(self):
        configured = dict(
            DEFAULT_ANOMALY_POLICY
        )
        configured.update({
            "interval_seconds": 45,
            "offline_grace_seconds": 240,
            "hot_temp_c": 90.0,
            "hot_clear_c": 86.0,
            "hot_grace_seconds": 120,
            "schedule_grace_seconds": 900,
        })

        saved = save_anomaly_policy(
            configured
        )

        self.assertEqual(saved, configured)
        self.assertEqual(
            load_anomaly_policy(),
            configured,
        )

    def test_corrupt_manual_setting_falls_back_to_defaults(self):
        conn = db()
        conn.execute("""
            INSERT INTO settings(key, value)
            VALUES('anomaly.hot_temp_c', 'not-a-number')
        """)
        conn.commit()
        conn.close()

        self.assertEqual(
            load_anomaly_policy(),
            DEFAULT_ANOMALY_POLICY,
        )


class AnomalyPolicyValidationTests(unittest.TestCase):
    def test_hot_clear_must_be_lower_than_trigger(self):
        payload = dict(
            DEFAULT_ANOMALY_POLICY
        )
        payload["hot_temp_c"] = 85.0
        payload["hot_clear_c"] = 85.0

        with self.assertRaisesRegex(
            ValueError,
            "hot_clear_c must be lower",
        ):
            normalize_anomaly_policy(
                payload
            )

    def test_interval_has_safe_lower_bound(self):
        payload = dict(
            DEFAULT_ANOMALY_POLICY
        )
        payload["interval_seconds"] = 1

        with self.assertRaisesRegex(
            ValueError,
            "interval_seconds must be between",
        ):
            normalize_anomaly_policy(
                payload
            )

    def test_unknown_fields_are_rejected(self):
        payload = dict(
            DEFAULT_ANOMALY_POLICY
        )
        payload["automatic_reboot"] = True

        with self.assertRaisesRegex(
            ValueError,
            "Unknown anomaly policy fields",
        ):
            normalize_anomaly_policy(
                payload
            )


class AnomalyPolicyRouteTests(
    TemporaryDatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):
    def endpoint(self, path, method, log_event=None):
        router = create_anomaly_router(
            log_event or (lambda **kwargs: None)
        )
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    def test_router_exposes_policy_paths(self):
        router = create_anomaly_router(
            lambda **kwargs: None
        )
        routes = {
            (route.path, method)
            for route in router.routes
            for method in route.methods
        }
        self.assertIn(
            ("/api/anomaly-policy", "GET"),
            routes,
        )
        self.assertIn(
            ("/api/anomaly-policy", "PUT"),
            routes,
        )

    async def test_update_persists_and_audits_policy(self):
        events = []

        def log_event(**kwargs):
            events.append(kwargs)

        payload = dict(
            DEFAULT_ANOMALY_POLICY
        )
        payload.update({
            "interval_seconds": 60,
            "hot_temp_c": 92.0,
            "hot_clear_c": 88.0,
        })

        endpoint = self.endpoint(
            "/api/anomaly-policy",
            "PUT",
            log_event,
        )
        result = await endpoint(
            FakeRequest(payload)
        )

        self.assertTrue(result["success"])
        self.assertEqual(
            result["policy"],
            payload,
        )
        self.assertEqual(
            load_anomaly_policy(),
            payload,
        )
        self.assertEqual(
            events[0]["action"],
            "ANOMALY_POLICY_UPDATE",
        )
        self.assertTrue(
            events[0]["success"]
        )


if __name__ == "__main__":
    unittest.main()
