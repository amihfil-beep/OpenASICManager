import os
import tempfile
import unittest

import config as app_config
from fastapi import HTTPException

from api.anomalies import (
    create_anomaly_router,
)
from api.anomaly_overrides import (
    create_anomaly_override_router,
)
from anomalies import service
from anomalies.policy import (
    DEFAULT_ANOMALY_POLICY,
    anomaly_policy_sources,
    apply_anomaly_override_patch,
    resolve_anomaly_policy,
)
from anomalies.repository import (
    clear_miner_anomaly_overrides,
    load_anomaly_override_snapshot,
    load_miner_anomaly_overrides,
    save_miner_anomaly_overrides,
)
from db import (
    db,
    init_db,
)


class FakeRequest:

    def __init__(
        self,
        payload,
    ):
        self.payload = payload


    async def json(self):
        return self.payload


class TemporaryDatabaseMixin:

    def setUp(self):

        fd, path = tempfile.mkstemp(
            prefix=(
                "openasicmanager-"
                "anomaly-override-"
            ),
            suffix=".db",
        )

        os.close(fd)
        os.unlink(path)

        self.path = path

        self.original_db = (
            app_config.DATABASE_PATH
        )

        app_config.DATABASE_PATH = (
            path
        )

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
                'OVERRIDE-ASIC',
                '192.0.2.81',
                'bitmain_stock',
                1,
                'MINING',
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


class AnomalyOverridePolicyTests(
    unittest.TestCase,
):

    def test_partial_override_inherits_global_fields(self):

        global_policy = dict(
            DEFAULT_ANOMALY_POLICY
        )

        effective = (
            resolve_anomaly_policy(
                global_policy,
                {
                    "hot_temp_c": 90.0,
                },
            )
        )

        self.assertEqual(
            effective["hot_temp_c"],
            90.0,
        )

        self.assertEqual(
            effective[
                "offline_grace_seconds"
            ],
            global_policy[
                "offline_grace_seconds"
            ],
        )

        self.assertEqual(
            effective[
                "interval_seconds"
            ],
            global_policy[
                "interval_seconds"
            ],
        )


    def test_null_patch_restores_inheritance(self):

        global_policy = dict(
            DEFAULT_ANOMALY_POLICY
        )

        overrides, effective = (
            apply_anomaly_override_patch(
                global_policy,
                {
                    "hot_temp_c": 90.0,
                    "hot_clear_c": 80.0,
                },
                {
                    "hot_temp_c": None,
                },
            )
        )

        self.assertNotIn(
            "hot_temp_c",
            overrides,
        )

        self.assertEqual(
            overrides["hot_clear_c"],
            80.0,
        )

        self.assertEqual(
            effective["hot_temp_c"],
            global_policy[
                "hot_temp_c"
            ],
        )


    def test_interval_is_not_per_miner_override(self):

        with self.assertRaisesRegex(
            ValueError,
            "Unknown per-miner",
        ):

            apply_anomaly_override_patch(
                DEFAULT_ANOMALY_POLICY,
                {},
                {
                    "interval_seconds": 10,
                },
            )


    def test_effective_hot_hysteresis_is_validated(self):

        with self.assertRaisesRegex(
            ValueError,
            "hot_clear_c must be lower",
        ):

            apply_anomaly_override_patch(
                DEFAULT_ANOMALY_POLICY,
                {},
                {
                    "hot_clear_c": 90.0,
                },
            )


    def test_sources_show_global_and_override(self):

        sources = (
            anomaly_policy_sources({
                "offline_grace_seconds": 30,
            })
        )

        self.assertEqual(
            sources[
                "offline_grace_seconds"
            ],
            "OVERRIDE",
        )

        self.assertEqual(
            sources["hot_temp_c"],
            "GLOBAL",
        )

        self.assertEqual(
            sources["interval_seconds"],
            "GLOBAL",
        )


class AnomalyOverridePersistenceTests(
    TemporaryDatabaseMixin,
    unittest.TestCase,
):

    def test_round_trip_partial_override(self):

        saved = (
            save_miner_anomaly_overrides(
                miner_id=self.miner_id,
                overrides={
                    "offline_grace_seconds": 10,
                    "hot_temp_c": 90.0,
                },
                actor="TEST",
                now=100,
            )
        )

        self.assertEqual(
            saved,
            {
                "offline_grace_seconds": 10,
                "hot_temp_c": 90.0,
            },
        )

        self.assertEqual(
            load_miner_anomaly_overrides(
                self.miner_id
            ),
            saved,
        )


    def test_snapshot_contains_only_miners_with_overrides(self):

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={
                "offline_grace_seconds": 10,
            },
            actor="TEST",
            now=100,
        )

        snapshot = (
            load_anomaly_override_snapshot()
        )

        self.assertEqual(
            snapshot,
            {
                self.miner_id: {
                    "offline_grace_seconds": 10,
                }
            },
        )


    def test_clear_restores_global_inheritance(self):

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={
                "offline_grace_seconds": 10,
            },
            actor="TEST",
            now=100,
        )

        self.assertTrue(
            clear_miner_anomaly_overrides(
                self.miner_id
            )
        )

        self.assertEqual(
            load_miner_anomaly_overrides(
                self.miner_id
            ),
            {},
        )


    def test_empty_override_set_removes_database_row(self):

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={
                "offline_grace_seconds": 10,
            },
            actor="TEST",
            now=100,
        )

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={},
            actor="TEST",
            now=101,
        )

        conn = db()

        row = conn.execute("""
            SELECT *
            FROM anomaly_policy_overrides
            WHERE miner_id=?
        """, (
            self.miner_id,
        )).fetchone()

        conn.close()

        self.assertIsNone(
            row
        )


    def test_invalid_corrupt_override_falls_back_to_inheritance(self):

        conn = db()

        conn.execute("""
            INSERT INTO anomaly_policy_overrides
            (
                miner_id,
                hot_clear_c,
                updated_by,
                updated_at
            )
            VALUES (?, ?, 'TEST', 100)
        """, (
            self.miner_id,
            100.0,
        ))

        conn.commit()
        conn.close()

        self.assertEqual(
            load_miner_anomaly_overrides(
                self.miner_id
            ),
            {},
        )




class AnomalyOverrideApiTests(
    TemporaryDatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    def endpoint(
        self,
        router,
        path,
        method,
    ):
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )


    def test_override_router_exposes_get_put_delete(self):

        router = (
            create_anomaly_override_router(
                lambda **kwargs: None
            )
        )

        routes = {
            (route.path, method)
            for route in router.routes
            for method in route.methods
        }

        path = (
            "/api/miners/"
            "{miner_id}/anomaly-policy"
        )

        self.assertIn(
            (path, "GET"),
            routes,
        )

        self.assertIn(
            (path, "PUT"),
            routes,
        )

        self.assertIn(
            (path, "DELETE"),
            routes,
        )


    async def test_bulk_summary_lists_only_override_miners(self):

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={
                "offline_grace_seconds": 15,
                "hot_grace_seconds": 20,
            },
            actor="TEST",
            now=100,
        )

        router = (
            create_anomaly_override_router(
                lambda **kwargs: None
            )
        )

        endpoint = self.endpoint(
            router,
            "/api/anomaly-policy-overrides",
            "GET",
        )

        result = endpoint()

        self.assertEqual(
            result,
            {
                "miners": [
                    {
                        "miner_id":
                            self.miner_id,

                        "override_count":
                            2,

                        "override_fields": [
                            "hot_grace_seconds",
                            "offline_grace_seconds",
                        ],
                    }
                ]
            },
        )


    async def test_get_returns_effective_policy_and_sources(self):

        router = (
            create_anomaly_override_router(
                lambda **kwargs: None
            )
        )

        endpoint = self.endpoint(
            router,
            (
                "/api/miners/"
                "{miner_id}/anomaly-policy"
            ),
            "GET",
        )

        result = endpoint(
            self.miner_id
        )

        self.assertEqual(
            result["overrides"],
            {},
        )

        self.assertEqual(
            result["effective_policy"],
            result["global_policy"],
        )

        self.assertEqual(
            result["sources"][
                "interval_seconds"
            ],
            "GLOBAL",
        )


    async def test_put_partial_override_and_null_clear(self):

        events = []

        router = (
            create_anomaly_override_router(
                lambda **kwargs:
                    events.append(kwargs)
            )
        )

        endpoint = self.endpoint(
            router,
            (
                "/api/miners/"
                "{miner_id}/anomaly-policy"
            ),
            "PUT",
        )

        result = await endpoint(
            self.miner_id,
            FakeRequest({
                "offline_grace_seconds": 15,
                "hot_grace_seconds": 20,
            }),
        )

        self.assertEqual(
            result["overrides"],
            {
                "offline_grace_seconds": 15,
                "hot_grace_seconds": 20,
            },
        )

        self.assertEqual(
            result["sources"][
                "offline_grace_seconds"
            ],
            "OVERRIDE",
        )

        result = await endpoint(
            self.miner_id,
            FakeRequest({
                "offline_grace_seconds": None,
            }),
        )

        self.assertEqual(
            result["overrides"],
            {
                "hot_grace_seconds": 20,
            },
        )

        self.assertEqual(
            result["sources"][
                "offline_grace_seconds"
            ],
            "GLOBAL",
        )

        self.assertEqual(
            events[0]["action"],
            "ANOMALY_POLICY_OVERRIDE_UPDATE",
        )


    async def test_delete_clears_all_overrides(self):

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={
                "offline_grace_seconds": 15,
            },
            actor="TEST",
            now=100,
        )

        events = []

        router = (
            create_anomaly_override_router(
                lambda **kwargs:
                    events.append(kwargs)
            )
        )

        endpoint = self.endpoint(
            router,
            (
                "/api/miners/"
                "{miner_id}/anomaly-policy"
            ),
            "DELETE",
        )

        result = endpoint(
            self.miner_id
        )

        self.assertEqual(
            result["overrides"],
            {},
        )

        self.assertEqual(
            events[0]["action"],
            "ANOMALY_POLICY_OVERRIDE_CLEAR",
        )


    async def test_global_policy_rejects_new_override_conflict(self):

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={
                "hot_clear_c": 84.0,
            },
            actor="TEST",
            now=100,
        )

        router = create_anomaly_router(
            lambda **kwargs: None
        )

        endpoint = self.endpoint(
            router,
            "/api/anomaly-policy",
            "PUT",
        )

        payload = dict(
            DEFAULT_ANOMALY_POLICY
        )

        payload["hot_temp_c"] = 83.0
        payload["hot_clear_c"] = 80.0

        with self.assertRaises(
            HTTPException
        ) as ctx:

            await endpoint(
                FakeRequest(payload)
            )

        self.assertEqual(
            ctx.exception.status_code,
            400,
        )

        self.assertIn(
            "conflicts with overrides",
            ctx.exception.detail,
        )


class FakeRuntime:

    def __init__(self):
        self.events = []

        self.desired_state = (
            lambda when: None
        )

    def log_event(
        self,
        **kwargs,
    ):
        self.events.append(
            kwargs
        )


class AnomalyOverrideRuntimeTests(
    TemporaryDatabaseMixin,
    unittest.TestCase,
):

    def test_runtime_uses_effective_per_miner_grace(self):

        conn = db()

        conn.execute("""
            UPDATE miners
            SET last_state='OFFLINE'
            WHERE id=?
        """, (
            self.miner_id,
        ))

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
                'INHERITED-ASIC',
                '192.0.2.82',
                'bitmain_stock',
                1,
                'OFFLINE',
                70
            )
        """)

        inherited_id = (
            cursor.lastrowid
        )

        conn.commit()
        conn.close()

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={
                "offline_grace_seconds": 0,
            },
            actor="TEST",
            now=100,
        )

        global_policy = dict(
            DEFAULT_ANOMALY_POLICY
        )

        global_policy[
            "offline_grace_seconds"
        ] = 180

        runtime = FakeRuntime()

        service.anomaly_scan(
            runtime,
            anomaly_policy=global_policy,
        )

        service.anomaly_scan(
            runtime,
            anomaly_policy=global_policy,
        )

        conn = db()

        rows = conn.execute("""
            SELECT miner_id
            FROM issues
            WHERE
                code='OFFLINE'
                AND status='ACTIVE'
        """).fetchall()

        conn.close()

        active_ids = {
            int(row["miner_id"])
            for row in rows
        }

        self.assertIn(
            self.miner_id,
            active_ids,
        )

        self.assertNotIn(
            inherited_id,
            active_ids,
        )


if __name__ == "__main__":
    unittest.main()
