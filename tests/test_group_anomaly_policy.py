import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import config as app_config

from api.anomalies import (
    create_anomaly_router,
)
from api.anomaly_overrides import (
    create_anomaly_override_router,
)
from api.miner_groups import (
    create_miner_group_router,
)
from anomalies import service
from anomalies.policy import (
    DEFAULT_ANOMALY_POLICY,
    apply_group_anomaly_override_patch,
    layered_anomaly_policy_sources,
    resolve_layered_anomaly_policy,
)
from anomalies.repository import (
    clear_group_anomaly_overrides,
    load_anomaly_policy,
    load_group_anomaly_override_snapshot,
    load_group_anomaly_overrides,
    save_group_anomaly_overrides,
    save_miner_anomaly_overrides,
)
from db import (
    db,
    init_db,
)
from miner_groups.repository import (
    create_group,
    get_group,
)


class FakeRequest:

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class FakeRuntime:

    def __init__(self):
        self.events = []

        self.stop_event = None

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


class GroupPolicyPureTests(
    unittest.TestCase,
):

    def test_global_group_miner_precedence(self):

        global_policy = dict(
            DEFAULT_ANOMALY_POLICY
        )

        effective = (
            resolve_layered_anomaly_policy(
                global_policy,
                group_overrides={
                    "offline_grace_seconds":
                        60,
                    "hot_temp_c":
                        90.0,
                },
                miner_overrides={
                    "offline_grace_seconds":
                        10,
                },
            )
        )

        self.assertEqual(
            effective[
                "offline_grace_seconds"
            ],
            10,
        )

        self.assertEqual(
            effective["hot_temp_c"],
            90.0,
        )

        self.assertEqual(
            effective[
                "interval_seconds"
            ],
            global_policy[
                "interval_seconds"
            ],
        )

        sources = (
            layered_anomaly_policy_sources(
                {
                    "hot_temp_c":
                        90.0,
                },
                {
                    "offline_grace_seconds":
                        10,
                },
            )
        )

        self.assertEqual(
            sources[
                "offline_grace_seconds"
            ],
            "MINER",
        )

        self.assertEqual(
            sources["hot_temp_c"],
            "GROUP",
        )

        self.assertEqual(
            sources["interval_seconds"],
            "GLOBAL",
        )


    def test_group_cannot_override_scan_interval(self):

        with self.assertRaisesRegex(
            ValueError,
            "Unknown group",
        ):
            apply_group_anomaly_override_patch(
                DEFAULT_ANOMALY_POLICY,
                {},
                {
                    "interval_seconds":
                        10,
                },
            )


    def test_cross_level_hysteresis_is_validated(self):

        with self.assertRaisesRegex(
            ValueError,
            "hot_clear_c must be lower",
        ):
            resolve_layered_anomaly_policy(
                DEFAULT_ANOMALY_POLICY,
                group_overrides={
                    "hot_temp_c":
                        90.0,
                },
                miner_overrides={
                    "hot_clear_c":
                        91.0,
                },
            )


class GroupPolicyDatabaseMixin:

    def setUp(self):

        fd, path = tempfile.mkstemp(
            prefix="oam-group-policy-",
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

        self.group_a = create_group(
            name="Rack A",
            normalized_name="rack a",
            actor="TEST",
        )

        self.group_b = create_group(
            name="Rack B",
            normalized_name="rack b",
            actor="TEST",
        )

        conn = db()

        cursor = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                enabled,
                schedule_enabled,
                last_state,
                temp,
                group_id
            )
            VALUES
            (
                'GROUP-POLICY-MINER',
                '192.0.2.67',
                'bitmain_stock',
                1,
                1,
                'OFFLINE',
                70,
                ?
            )
        """, (
            self.group_a["id"],
        ))

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


    @staticmethod
    def endpoint(
        router,
        path,
        method,
    ):
        return next(
            route.endpoint
            for route
            in router.routes
            if (
                route.path == path
                and
                method in route.methods
            )
        )


class GroupPolicyPersistenceTests(
    GroupPolicyDatabaseMixin,
    unittest.TestCase,
):

    def test_group_override_round_trip_and_snapshot(self):

        saved = (
            save_group_anomaly_overrides(
                group_id=(
                    self.group_a["id"]
                ),
                overrides={
                    "offline_grace_seconds":
                        45,
                    "hot_temp_c":
                        90.0,
                },
                actor="TEST",
                now=100,
            )
        )

        self.assertEqual(
            load_group_anomaly_overrides(
                self.group_a["id"]
            ),
            saved,
        )

        self.assertEqual(
            load_group_anomaly_override_snapshot(),
            {
                self.group_a["id"]:
                    saved
            },
        )

        self.assertTrue(
            clear_group_anomaly_overrides(
                self.group_a["id"]
            )
        )

        self.assertEqual(
            load_group_anomaly_overrides(
                self.group_a["id"]
            ),
            {},
        )


class GroupPolicyApiTests(
    GroupPolicyDatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def test_group_api_updates_audits_and_clears(self):

        events = []

        router = (
            create_anomaly_override_router(
                lambda **kwargs:
                    events.append(
                        kwargs
                    )
            )
        )

        put = self.endpoint(
            router,
            (
                "/api/miner-groups/"
                "{group_id}/anomaly-policy"
            ),
            "PUT",
        )

        get = self.endpoint(
            router,
            (
                "/api/miner-groups/"
                "{group_id}/anomaly-policy"
            ),
            "GET",
        )

        delete = self.endpoint(
            router,
            (
                "/api/miner-groups/"
                "{group_id}/anomaly-policy"
            ),
            "DELETE",
        )

        with patch(
            "api.anomaly_overrides."
            "current_audit_actor",
            return_value="TEST-OPERATOR",
        ):

            result = await put(
                self.group_a["id"],
                FakeRequest({
                    "offline_grace_seconds":
                        45,
                }),
            )

        self.assertEqual(
            result["sources"][
                "offline_grace_seconds"
            ],
            "GROUP",
        )

        self.assertEqual(
            result["sources"][
                "interval_seconds"
            ],
            "GLOBAL",
        )

        self.assertEqual(
            get(
                self.group_a["id"]
            )["effective_policy"][
                "offline_grace_seconds"
            ],
            45,
        )

        self.assertEqual(
            events[0]["action"],
            "ANOMALY_POLICY_GROUP_UPDATE",
        )

        result = delete(
            self.group_a["id"]
        )

        self.assertEqual(
            result["overrides"],
            {},
        )

        self.assertEqual(
            events[-1]["action"],
            "ANOMALY_POLICY_GROUP_CLEAR",
        )


    async def test_miner_policy_reports_group_and_miner_sources(self):

        save_group_anomaly_overrides(
            group_id=self.group_a["id"],
            overrides={
                "hot_temp_c":
                    95.0,
                "offline_grace_seconds":
                    60,
            },
            actor="TEST",
            now=100,
        )

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={
                "offline_grace_seconds":
                    10,
                "hot_clear_c":
                    90.0,
            },
            actor="TEST",
            now=101,
        )

        router = (
            create_anomaly_override_router(
                lambda **kwargs: None
            )
        )

        get = self.endpoint(
            router,
            (
                "/api/miners/"
                "{miner_id}/anomaly-policy"
            ),
            "GET",
        )

        result = get(
            self.miner_id
        )

        self.assertEqual(
            result["group_id"],
            self.group_a["id"],
        )

        self.assertEqual(
            result["group_name"],
            "Rack A",
        )

        self.assertEqual(
            result["sources"][
                "offline_grace_seconds"
            ],
            "MINER",
        )

        self.assertEqual(
            result["sources"][
                "hot_temp_c"
            ],
            "GROUP",
        )

        self.assertEqual(
            result["sources"][
                "interval_seconds"
            ],
            "GLOBAL",
        )

        self.assertEqual(
            result["effective_policy"][
                "offline_grace_seconds"
            ],
            10,
        )

        self.assertEqual(
            result["effective_policy"][
                "hot_temp_c"
            ],
            95.0,
        )


    async def test_group_update_rejects_member_conflict(self):

        save_group_anomaly_overrides(
            group_id=self.group_a["id"],
            overrides={
                "hot_temp_c":
                    95.0,
            },
            actor="TEST",
            now=100,
        )

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={
                "hot_clear_c":
                    90.0,
            },
            actor="TEST",
            now=101,
        )

        router = (
            create_anomaly_override_router(
                lambda **kwargs: None
            )
        )

        put = self.endpoint(
            router,
            (
                "/api/miner-groups/"
                "{group_id}/anomaly-policy"
            ),
            "PUT",
        )

        with self.assertRaises(
            HTTPException
        ) as ctx:

            await put(
                self.group_a["id"],
                FakeRequest({
                    "hot_temp_c":
                        89.0,
                }),
            )

        self.assertEqual(
            ctx.exception.status_code,
            400,
        )

        self.assertEqual(
            load_group_anomaly_overrides(
                self.group_a["id"]
            )["hot_temp_c"],
            95.0,
        )


    async def test_global_update_rejects_group_conflict(self):

        save_group_anomaly_overrides(
            group_id=self.group_a["id"],
            overrides={
                "hot_clear_c":
                    84.0,
            },
            actor="TEST",
            now=100,
        )

        router = create_anomaly_router(
            lambda **kwargs: None
        )

        put = self.endpoint(
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

            await put(
                FakeRequest(payload)
            )

        self.assertEqual(
            ctx.exception.status_code,
            400,
        )

        self.assertIn(
            "group",
            ctx.exception.detail,
        )

        self.assertEqual(
            load_anomaly_policy(),
            DEFAULT_ANOMALY_POLICY,
        )


class GroupPolicyMembershipSafetyTests(
    GroupPolicyDatabaseMixin,
    unittest.TestCase,
):

    def test_move_clear_and_group_delete_reject_invalid_inheritance(self):

        save_group_anomaly_overrides(
            group_id=self.group_a["id"],
            overrides={
                "hot_temp_c":
                    95.0,
            },
            actor="TEST",
            now=100,
        )

        save_miner_anomaly_overrides(
            miner_id=self.miner_id,
            overrides={
                "hot_clear_c":
                    90.0,
            },
            actor="TEST",
            now=101,
        )

        router = (
            create_miner_group_router(
                lambda **kwargs: None
            )
        )

        assign = self.endpoint(
            router,
            "/api/miners/{miner_id}/group",
            "PUT",
        )

        clear = self.endpoint(
            router,
            "/api/miners/{miner_id}/group",
            "DELETE",
        )

        delete_group = self.endpoint(
            router,
            "/api/miner-groups/{group_id}",
            "DELETE",
        )


        with self.assertRaises(
            HTTPException
        ) as move_ctx:

            assign(
                self.miner_id,
                {
                    "group_id":
                        self.group_b["id"],
                },
            )

        self.assertEqual(
            move_ctx.exception.status_code,
            400,
        )


        with self.assertRaises(
            HTTPException
        ) as clear_ctx:

            clear(
                self.miner_id
            )

        self.assertEqual(
            clear_ctx.exception.status_code,
            400,
        )


        with self.assertRaises(
            HTTPException
        ) as delete_ctx:

            delete_group(
                self.group_a["id"]
            )

        self.assertEqual(
            delete_ctx.exception.status_code,
            400,
        )


        conn = db()

        try:
            miner = conn.execute("""
                SELECT group_id
                FROM miners
                WHERE id=?
            """, (
                self.miner_id,
            )).fetchone()

            control_jobs = (
                conn.execute("""
                    SELECT COUNT(*) AS count
                    FROM control_jobs
                """).fetchone()[
                    "count"
                ]
            )

        finally:
            conn.close()

        self.assertEqual(
            miner["group_id"],
            self.group_a["id"],
        )

        self.assertIsNotNone(
            get_group(
                self.group_a["id"]
            )
        )

        self.assertEqual(
            control_jobs,
            0,
        )


class GroupPolicyRuntimeTests(
    GroupPolicyDatabaseMixin,
    unittest.TestCase,
):

    def test_runtime_uses_group_grace(self):

        save_group_anomaly_overrides(
            group_id=self.group_a["id"],
            overrides={
                "offline_grace_seconds":
                    0,
            },
            actor="TEST",
            now=100,
        )

        runtime = FakeRuntime()

        global_policy = dict(
            DEFAULT_ANOMALY_POLICY
        )

        global_policy[
            "offline_grace_seconds"
        ] = 180

        service.anomaly_scan(
            runtime,
            anomaly_policy=global_policy,
        )

        service.anomaly_scan(
            runtime,
            anomaly_policy=global_policy,
        )

        conn = db()

        try:
            issue = conn.execute("""
                SELECT id
                FROM issues
                WHERE
                    miner_id=?
                    AND code='OFFLINE'
                    AND status='ACTIVE'
            """, (
                self.miner_id,
            )).fetchone()

        finally:
            conn.close()

        self.assertIsNotNone(
            issue
        )


class GroupPolicySemanticIntegrationTests(
    GroupPolicyDatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def test_full_layered_policy_lifecycle_is_detection_only(
        self,
    ):

        events = []

        policy_router = (
            create_anomaly_override_router(
                lambda **kwargs:
                    events.append(
                        kwargs
                    )
            )
        )

        group_router = (
            create_miner_group_router(
                lambda **kwargs:
                    events.append(
                        kwargs
                    )
            )
        )


        group_put = self.endpoint(
            policy_router,
            (
                "/api/miner-groups/"
                "{group_id}/anomaly-policy"
            ),
            "PUT",
        )

        miner_put = self.endpoint(
            policy_router,
            (
                "/api/miners/"
                "{miner_id}/anomaly-policy"
            ),
            "PUT",
        )

        miner_get = self.endpoint(
            policy_router,
            (
                "/api/miners/"
                "{miner_id}/anomaly-policy"
            ),
            "GET",
        )

        assign_group = self.endpoint(
            group_router,
            "/api/miners/{miner_id}/group",
            "PUT",
        )

        clear_group = self.endpoint(
            group_router,
            "/api/miners/{miner_id}/group",
            "DELETE",
        )


        with patch(
            "api.anomaly_overrides."
            "current_audit_actor",
            return_value="SEMANTIC-OPERATOR",
        ), patch(
            "api.miner_groups."
            "current_audit_actor",
            return_value="SEMANTIC-OPERATOR",
        ):

            # GROUP A:
            # immediate OFFLINE detection and
            # a high temperature trigger.
            await group_put(
                self.group_a["id"],
                FakeRequest({
                    "offline_grace_seconds":
                        0,
                    "hot_temp_c":
                        95.0,
                }),
            )


            # GROUP B:
            # different inherited values.
            await group_put(
                self.group_b["id"],
                FakeRequest({
                    "offline_grace_seconds":
                        60,
                    "hot_temp_c":
                        92.0,
                }),
            )


            # MINER override takes precedence over
            # both GROUP and GLOBAL.
            await miner_put(
                self.miner_id,
                FakeRequest({
                    "hot_clear_c":
                        90.0,
                }),
            )


            first = miner_get(
                self.miner_id
            )


            self.assertEqual(
                first["sources"][
                    "offline_grace_seconds"
                ],
                "GROUP",
            )

            self.assertEqual(
                first["effective_policy"][
                    "offline_grace_seconds"
                ],
                0,
            )

            self.assertEqual(
                first["sources"][
                    "hot_temp_c"
                ],
                "GROUP",
            )

            self.assertEqual(
                first["effective_policy"][
                    "hot_temp_c"
                ],
                95.0,
            )

            self.assertEqual(
                first["sources"][
                    "hot_clear_c"
                ],
                "MINER",
            )

            self.assertEqual(
                first["effective_policy"][
                    "hot_clear_c"
                ],
                90.0,
            )

            self.assertEqual(
                first["sources"][
                    "interval_seconds"
                ],
                "GLOBAL",
            )


            # Prove the runtime consumes the GROUP
            # value, not only the read model.
            runtime = FakeRuntime()

            global_policy = dict(
                DEFAULT_ANOMALY_POLICY
            )

            global_policy[
                "offline_grace_seconds"
            ] = 180


            service.anomaly_scan(
                runtime,
                anomaly_policy=global_policy,
            )

            service.anomaly_scan(
                runtime,
                anomaly_policy=global_policy,
            )


            conn = db()

            try:
                opened = conn.execute("""
                    SELECT id
                    FROM issues
                    WHERE
                        miner_id=?
                        AND code='OFFLINE'
                        AND status='ACTIVE'
                """, (
                    self.miner_id,
                )).fetchone()

            finally:
                conn.close()


            self.assertIsNotNone(
                opened
            )


            # Move from GROUP A -> GROUP B.
            # This is valid:
            # GROUP B hot_temp=92
            # MINER hot_clear=90.
            moved = assign_group(
                self.miner_id,
                {
                    "group_id":
                        self.group_b["id"],
                },
            )

            self.assertTrue(
                moved["changed"]
            )


            second = miner_get(
                self.miner_id
            )


            self.assertEqual(
                second["group_id"],
                self.group_b["id"],
            )

            self.assertEqual(
                second["effective_policy"][
                    "offline_grace_seconds"
                ],
                60,
            )

            self.assertEqual(
                second["effective_policy"][
                    "hot_temp_c"
                ],
                92.0,
            )

            self.assertEqual(
                second["effective_policy"][
                    "hot_clear_c"
                ],
                90.0,
            )


            # Ungrouping would now inherit
            # GLOBAL hot_temp=85 while keeping
            # MINER hot_clear=90.
            #
            # That would violate hysteresis, so the
            # operation must be rejected atomically.
            with self.assertRaises(
                HTTPException
            ) as invalid_clear:

                clear_group(
                    self.miner_id
                )


            self.assertEqual(
                invalid_clear.exception.status_code,
                400,
            )


            conn = db()

            try:
                after_reject = conn.execute("""
                    SELECT group_id
                    FROM miners
                    WHERE id=?
                """, (
                    self.miner_id,
                )).fetchone()

            finally:
                conn.close()


            self.assertEqual(
                after_reject["group_id"],
                self.group_b["id"],
            )


            # Make the MINER override compatible
            # with GLOBAL inheritance.
            await miner_put(
                self.miner_id,
                FakeRequest({
                    "hot_clear_c":
                        80.0,
                }),
            )


            cleared = clear_group(
                self.miner_id
            )

            self.assertTrue(
                cleared["changed"]
            )


            final = miner_get(
                self.miner_id
            )


            self.assertIsNone(
                final["group_id"]
            )

            self.assertIsNone(
                final["group_name"]
            )

            self.assertEqual(
                final["sources"][
                    "offline_grace_seconds"
                ],
                "GLOBAL",
            )

            self.assertEqual(
                final["effective_policy"][
                    "offline_grace_seconds"
                ],
                DEFAULT_ANOMALY_POLICY[
                    "offline_grace_seconds"
                ],
            )

            self.assertEqual(
                final["sources"][
                    "hot_temp_c"
                ],
                "GLOBAL",
            )

            self.assertEqual(
                final["effective_policy"][
                    "hot_temp_c"
                ],
                DEFAULT_ANOMALY_POLICY[
                    "hot_temp_c"
                ],
            )

            self.assertEqual(
                final["sources"][
                    "hot_clear_c"
                ],
                "MINER",
            )

            self.assertEqual(
                final["effective_policy"][
                    "hot_clear_c"
                ],
                80.0,
            )


        conn = db()

        try:

            miner = conn.execute("""
                SELECT
                    id,
                    group_id,
                    last_state,
                    schedule_enabled

                FROM miners

                WHERE id=?
            """, (
                self.miner_id,
            )).fetchone()


            control_jobs = (
                conn.execute("""
                    SELECT COUNT(*) AS count
                    FROM control_jobs
                """).fetchone()[
                    "count"
                ]
            )

        finally:
            conn.close()


        self.assertIsNotNone(
            miner
        )

        self.assertIsNone(
            miner["group_id"]
        )

        self.assertEqual(
            miner["last_state"],
            "OFFLINE",
        )

        self.assertEqual(
            miner["schedule_enabled"],
            1,
        )

        self.assertEqual(
            control_jobs,
            0,
        )


        actions = [
            event["action"]
            for event in events
        ]


        self.assertIn(
            "ANOMALY_POLICY_GROUP_UPDATE",
            actions,
        )

        self.assertIn(
            "ANOMALY_POLICY_OVERRIDE_UPDATE",
            actions,
        )

        self.assertIn(
            "MINER_GROUP_ASSIGN",
            actions,
        )

        self.assertIn(
            "MINER_GROUP_CLEAR",
            actions,
        )


        for forbidden in (
            "PAUSE",
            "RESUME",
            "REBOOT",
        ):

            self.assertNotIn(
                forbidden,
                actions,
            )


if __name__ == "__main__":
    unittest.main()
