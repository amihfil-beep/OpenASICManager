import os
import tempfile
import time
import unittest

from fastapi import HTTPException

import config as app_config

from anomalies import service
from anomalies.analytics import (
    issue_report,
)
from anomalies.policy import (
    DEFAULT_ANOMALY_POLICY,
)
from api.maintenance import (
    create_maintenance_router,
)
from api.miner_groups import (
    create_miner_group_router,
)
from audit.identity import (
    bind_audit_actor,
    reset_audit_actor,
)
from db import (
    db,
    init_db,
)
from maintenance_windows.policy import (
    maintenance_window_dict,
    normalize_maintenance_create,
)
from maintenance_windows.repository import (
    active_maintenance_snapshot,
    create_maintenance_window,
    end_maintenance_window,
    extend_maintenance_window,
    maintenance_window_member_rows,
)
from miner_groups.repository import (
    create_group,
    delete_group,
)


class FakeRequest:

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


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


class GroupMaintenanceValidationTests(
    unittest.TestCase,
):

    def test_group_scope_requires_group_id(self):

        with self.assertRaisesRegex(
            ValueError,
            "group_id is required",
        ):

            normalize_maintenance_create(
                {
                    "scope": "GROUP",
                    "ends_at": 200,
                },
                now=100,
            )


    def test_group_scope_rejects_miner_id(self):

        with self.assertRaisesRegex(
            ValueError,
            "miner_id must be null",
        ):

            normalize_maintenance_create(
                {
                    "scope": "GROUP",
                    "group_id": 1,
                    "miner_id": 1,
                    "ends_at": 200,
                },
                now=100,
            )


    def test_farm_scope_rejects_group_id(self):

        with self.assertRaisesRegex(
            ValueError,
            "group_id must be null",
        ):

            normalize_maintenance_create(
                {
                    "scope": "FARM",
                    "group_id": 1,
                    "ends_at": 200,
                },
                now=100,
            )


class GroupMaintenanceDatabaseMixin:

    def setUp(self):

        fd, path = tempfile.mkstemp(
            prefix="oam-group-maintenance-",
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

        conn.executemany("""
            INSERT INTO miners
            (
                id,
                name,
                ip,
                driver,
                enabled,
                schedule_enabled,
                last_state,
                temp,
                group_id
            )
            VALUES (
                ?, ?, ?, ?,
                1, 1, 'MINING', 70, ?
            )
        """, (
            (
                1,
                "ASIC-1",
                "192.0.2.81",
                "bitmain_stock",
                self.group_a["id"],
            ),
            (
                2,
                "ASIC-2",
                "192.0.2.82",
                "awesome",
                self.group_a["id"],
            ),
            (
                3,
                "ASIC-3",
                "192.0.2.83",
                "bitmain_stock",
                self.group_b["id"],
            ),
        ))

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


    def group_window(
        self,
        *,
        group_id=None,
        starts_at=100,
        ends_at=200,
    ):

        return create_maintenance_window(
            {
                "scope": "GROUP",
                "miner_id": None,
                "group_id": (
                    group_id
                    if group_id is not None
                    else self.group_a["id"]
                ),
                "starts_at": starts_at,
                "ends_at": ends_at,
                "note": "group maintenance",
            },
            actor="TEST",
            now=90,
        )


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


class GroupMaintenanceMigrationTests(
    unittest.TestCase,
):

    def setUp(self):

        fd, path = tempfile.mkstemp(
            prefix="oam-group-maintenance-migration-",
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


    def test_old_maintenance_schema_is_upgraded_without_data_loss(self):

        init_db()

        conn = db()

        conn.executescript("""
            DROP TABLE IF EXISTS maintenance_window_members;

            DROP INDEX IF EXISTS idx_maintenance_group;
            DROP INDEX IF EXISTS idx_maintenance_time;
            DROP INDEX IF EXISTS idx_maintenance_miner;

            DROP TABLE maintenance_windows;

            CREATE TABLE maintenance_windows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                scope TEXT NOT NULL,
                miner_id INTEGER,

                starts_at INTEGER NOT NULL,
                ends_at INTEGER NOT NULL,

                note TEXT,

                created_by TEXT NOT NULL,
                created_at INTEGER NOT NULL,

                updated_by TEXT,
                updated_at INTEGER,

                ended_at INTEGER,
                ended_by TEXT,

                CHECK (
                    scope IN (
                        'FARM',
                        'MINER'
                    )
                ),

                CHECK (
                    (
                        scope='FARM'
                        AND miner_id IS NULL
                    )
                    OR
                    (
                        scope='MINER'
                        AND miner_id IS NOT NULL
                    )
                )
            );

            INSERT INTO maintenance_windows
            (
                id,
                scope,
                miner_id,
                starts_at,
                ends_at,
                note,
                created_by,
                created_at
            )
            VALUES
            (
                41,
                'FARM',
                NULL,
                100,
                200,
                'existing maintenance',
                'TEST',
                90
            );
        """)

        conn.commit()
        conn.close()


        init_db()

        # Repeated startup must stay idempotent.
        init_db()


        conn = db()

        try:

            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info("
                    "maintenance_windows"
                    ")"
                ).fetchall()
            }


            row = conn.execute("""
                SELECT *
                FROM maintenance_windows
                WHERE id=41
            """).fetchone()


            member_table = (
                conn.execute("""
                    SELECT name
                    FROM sqlite_master
                    WHERE
                        type='table'
                        AND name='maintenance_window_members'
                """).fetchone()
            )

        finally:
            conn.close()


        self.assertIn(
            "group_id",
            columns,
        )

        self.assertIn(
            "group_name",
            columns,
        )

        self.assertEqual(
            row["scope"],
            "FARM",
        )

        self.assertEqual(
            row["note"],
            "existing maintenance",
        )

        self.assertIsNone(
            row["group_id"]
        )

        self.assertIsNotNone(
            member_table
        )


class GroupMaintenancePersistenceTests(
    GroupMaintenanceDatabaseMixin,
    unittest.TestCase,
):

    def test_group_window_snapshots_current_members(self):

        row = self.group_window()

        self.assertEqual(
            row["scope"],
            "GROUP",
        )

        self.assertEqual(
            row["group_id"],
            self.group_a["id"],
        )

        self.assertEqual(
            row["group_name"],
            "Rack A",
        )

        self.assertEqual(
            row["member_count"],
            2,
        )

        item = maintenance_window_dict(
            row,
            150,
        )

        self.assertEqual(
            item["member_ids"],
            [
                1,
                2,
            ],
        )


        members = (
            maintenance_window_member_rows(
                row["id"]
            )
        )

        self.assertEqual(
            {
                item["miner_id"]
                for item in members
            },
            {
                1,
                2,
            },
        )


    def test_snapshot_does_not_follow_later_membership_changes(self):

        row = self.group_window()


        conn = db()

        conn.execute("""
            UPDATE miners
            SET group_id=?
            WHERE id=1
        """, (
            self.group_b["id"],
        ))

        conn.execute("""
            UPDATE miners
            SET group_id=?
            WHERE id=3
        """, (
            self.group_a["id"],
        ))

        conn.commit()
        conn.close()


        snapshot = (
            active_maintenance_snapshot(
                150
            )
        )


        self.assertEqual(
            snapshot["miner_ids"],
            {
                1,
                2,
            },
        )

        self.assertNotIn(
            3,
            snapshot["miner_ids"],
        )


        item = maintenance_window_dict(
            row,
            150,
        )

        self.assertEqual(
            item["member_count"],
            2,
        )


    def test_group_rename_or_delete_does_not_change_snapshot(self):

        row = self.group_window()


        conn = db()

        conn.execute("""
            UPDATE miner_groups
            SET name='Rack A Renamed'
            WHERE id=?
        """, (
            self.group_a["id"],
        ))

        conn.commit()
        conn.close()


        stored = maintenance_window_dict(
            row,
            150,
        )

        self.assertEqual(
            stored["group_name"],
            "Rack A",
        )


        delete_group(
            self.group_a["id"]
        )


        snapshot = (
            active_maintenance_snapshot(
                150
            )
        )


        self.assertEqual(
            snapshot["miner_ids"],
            {
                1,
                2,
            },
        )


    def test_extend_and_end_do_not_change_member_snapshot(self):

        row = self.group_window()

        before = {
            item["miner_id"]
            for item
            in maintenance_window_member_rows(
                row["id"]
            )
        }


        extend_maintenance_window(
            window_id=row["id"],
            normalized={
                "ends_at": 250,
                "note": "extended",
            },
            actor="TEST",
            now=150,
        )


        after_extend = {
            item["miner_id"]
            for item
            in maintenance_window_member_rows(
                row["id"]
            )
        }


        end_maintenance_window(
            window_id=row["id"],
            actor="TEST",
            now=175,
        )


        after_end = {
            item["miner_id"]
            for item
            in maintenance_window_member_rows(
                row["id"]
            )
        }


        self.assertEqual(
            before,
            {
                1,
                2,
            },
        )

        self.assertEqual(
            after_extend,
            before,
        )

        self.assertEqual(
            after_end,
            before,
        )


class GroupMaintenanceApiTests(
    GroupMaintenanceDatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def test_group_window_creation_is_audited_and_reports_snapshot(self):

        events = []

        router = (
            create_maintenance_router(
                lambda **kwargs:
                    events.append(
                        kwargs
                    )
            )
        )

        create = self.endpoint(
            router,
            "/api/maintenance",
            "POST",
        )

        now = int(
            time.time()
        )


        token = bind_audit_actor(
            "WEB:operator"
        )

        try:

            result = await create(
                FakeRequest({
                    "scope":
                        "GROUP",

                    "group_id":
                        self.group_a["id"],

                    "ends_at":
                        now + 3600,

                    "note":
                        "Rack electrical work",
                })
            )

        finally:
            reset_audit_actor(
                token
            )


        item = result[
            "maintenance"
        ]


        self.assertEqual(
            item["scope"],
            "GROUP",
        )

        self.assertEqual(
            item["group_name"],
            "Rack A",
        )

        self.assertEqual(
            item["member_count"],
            2,
        )

        self.assertEqual(
            events[0]["action"],
            "MAINTENANCE_CREATE",
        )

        self.assertIsNone(
            events[0]["miner"]
        )

        self.assertIn(
            "Rack A",
            events[0]["message"],
        )

        self.assertIn(
            "members=2",
            events[0]["message"],
        )


        conn = db()

        try:
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
            control_jobs,
            0,
        )


    async def test_empty_group_is_rejected(self):

        empty = create_group(
            name="Empty Rack",
            normalized_name="empty rack",
            actor="TEST",
        )

        router = (
            create_maintenance_router(
                lambda **kwargs: None
            )
        )

        create = self.endpoint(
            router,
            "/api/maintenance",
            "POST",
        )

        now = int(
            time.time()
        )


        with self.assertRaises(
            HTTPException
        ) as ctx:

            await create(
                FakeRequest({
                    "scope":
                        "GROUP",

                    "group_id":
                        empty["id"],

                    "ends_at":
                        now + 3600,
                })
            )


        self.assertEqual(
            ctx.exception.status_code,
            400,
        )

        self.assertIn(
            "at least one current member",
            ctx.exception.detail,
        )


    async def test_unknown_group_is_404(self):

        router = (
            create_maintenance_router(
                lambda **kwargs: None
            )
        )

        create = self.endpoint(
            router,
            "/api/maintenance",
            "POST",
        )

        now = int(
            time.time()
        )


        with self.assertRaises(
            HTTPException
        ) as ctx:

            await create(
                FakeRequest({
                    "scope":
                        "GROUP",

                    "group_id":
                        999999,

                    "ends_at":
                        now + 3600,
                })
            )


        self.assertEqual(
            ctx.exception.status_code,
            404,
        )


    async def test_overlapping_same_group_window_is_rejected(self):

        now = int(
            time.time()
        )

        create_maintenance_window(
            {
                "scope": "GROUP",
                "miner_id": None,
                "group_id": self.group_a["id"],
                "starts_at": now,
                "ends_at": now + 3600,
                "note": None,
            },
            actor="TEST",
            now=now,
        )


        router = (
            create_maintenance_router(
                lambda **kwargs: None
            )
        )

        create = self.endpoint(
            router,
            "/api/maintenance",
            "POST",
        )


        with self.assertRaises(
            HTTPException
        ) as ctx:

            await create(
                FakeRequest({
                    "scope":
                        "GROUP",

                    "group_id":
                        self.group_a["id"],

                    "starts_at":
                        now + 60,

                    "ends_at":
                        now + 1800,
                })
            )


        self.assertEqual(
            ctx.exception.status_code,
            409,
        )


class GroupMaintenanceRuntimeTests(
    GroupMaintenanceDatabaseMixin,
    unittest.TestCase,
):

    def test_anomaly_suppression_uses_creation_snapshot(self):

        now = int(
            time.time()
        )


        create_maintenance_window(
            {
                "scope": "GROUP",
                "miner_id": None,
                "group_id": self.group_a["id"],
                "starts_at": now - 10,
                "ends_at": now + 3600,
                "note": None,
            },
            actor="TEST",
            now=now - 10,
        )


        # ASIC-1 leaves the group after maintenance
        # was created.
        #
        # ASIC-3 joins the group afterwards.
        conn = db()

        conn.execute("""
            UPDATE miners
            SET
                group_id=?,
                last_state='OFFLINE'

            WHERE id=1
        """, (
            self.group_b["id"],
        ))

        conn.execute("""
            UPDATE miners
            SET last_state='OFFLINE'
            WHERE id=2
        """)

        conn.execute("""
            UPDATE miners
            SET
                group_id=?,
                last_state='OFFLINE'

            WHERE id=3
        """, (
            self.group_a["id"],
        ))

        conn.commit()
        conn.close()


        policy = dict(
            DEFAULT_ANOMALY_POLICY
        )

        policy[
            "offline_grace_seconds"
        ] = 0


        runtime = FakeRuntime()


        service.anomaly_scan(
            runtime,
            anomaly_policy=policy,
        )

        service.anomaly_scan(
            runtime,
            anomaly_policy=policy,
        )


        conn = db()

        try:

            rows = conn.execute("""
                SELECT miner_id
                FROM issues
                WHERE
                    code='OFFLINE'
                    AND status='ACTIVE'
            """).fetchall()


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


        active_ids = {
            int(row["miner_id"])
            for row in rows
        }


        # Original creation snapshot remains covered.
        self.assertNotIn(
            1,
            active_ids,
        )

        self.assertNotIn(
            2,
            active_ids,
        )


        # Later group member was not part of the
        # creation snapshot, therefore is not covered.
        self.assertIn(
            3,
            active_ids,
        )


        self.assertEqual(
            control_jobs,
            0,
        )


class GroupAwareIssueReadModelTests(
    GroupMaintenanceDatabaseMixin,
    unittest.TestCase,
):

    def test_issue_report_contains_current_group_context(self):

        conn = db()

        conn.execute("""
            INSERT INTO issues
            (
                miner_id,
                ip,
                name,
                code,
                severity,
                status,
                first_seen,
                last_seen,
                message
            )
            VALUES
            (
                1,
                '192.0.2.81',
                'ASIC-1',
                'OFFLINE',
                'CRITICAL',
                'ACTIVE',
                100,
                100,
                'offline'
            )
        """)

        conn.commit()
        conn.close()


        report = issue_report()

        issue = report["active"][0]


        self.assertEqual(
            issue["group_id"],
            self.group_a["id"],
        )

        self.assertEqual(
            issue["group_name"],
            "Rack A",
        )


class GroupMaintenanceSemanticIntegrationTests(
    GroupMaintenanceDatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def test_group_maintenance_snapshot_survives_group_lifecycle(
        self,
    ):

        events = []

        maintenance_router = (
            create_maintenance_router(
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


        create_window = self.endpoint(
            maintenance_router,
            "/api/maintenance",
            "POST",
        )

        list_windows = self.endpoint(
            maintenance_router,
            "/api/maintenance",
            "GET",
        )

        extend_window = self.endpoint(
            maintenance_router,
            "/api/maintenance/{window_id}",
            "PUT",
        )

        end_window = self.endpoint(
            maintenance_router,
            "/api/maintenance/{window_id}/end",
            "POST",
        )


        assign_group = self.endpoint(
            group_router,
            "/api/miners/{miner_id}/group",
            "PUT",
        )

        rename_group = self.endpoint(
            group_router,
            "/api/miner-groups/{group_id}",
            "PUT",
        )

        delete_group_api = self.endpoint(
            group_router,
            "/api/miner-groups/{group_id}",
            "DELETE",
        )


        now = int(
            time.time()
        )


        token = bind_audit_actor(
            "WEB:semantic-operator"
        )

        try:

            # ------------------------------------------------
            # Create GROUP maintenance while Rack A contains
            # ASIC-1 and ASIC-2.
            # ------------------------------------------------

            created = await create_window(
                FakeRequest({
                    "scope":
                        "GROUP",

                    "group_id":
                        self.group_a["id"],

                    "starts_at":
                        now,

                    "ends_at":
                        now + 1800,

                    "note":
                        "Semantic snapshot test",
                })
            )


            window = created[
                "maintenance"
            ]

            window_id = int(
                window["id"]
            )


            self.assertEqual(
                window["scope"],
                "GROUP",
            )

            self.assertEqual(
                window["group_id"],
                self.group_a["id"],
            )

            self.assertEqual(
                window["group_name"],
                "Rack A",
            )

            self.assertEqual(
                window["member_count"],
                2,
            )

            self.assertEqual(
                window["member_ids"],
                [
                    1,
                    2,
                ],
            )


            # ------------------------------------------------
            # Change live membership:
            #
            # ASIC-1 leaves Rack A.
            # ASIC-3 joins Rack A.
            #
            # Existing maintenance MUST remain [1, 2].
            # ------------------------------------------------

            moved_out = assign_group(
                1,
                {
                    "group_id":
                        self.group_b["id"],
                },
            )

            self.assertTrue(
                moved_out["changed"]
            )


            moved_in = assign_group(
                3,
                {
                    "group_id":
                        self.group_a["id"],
                },
            )

            self.assertTrue(
                moved_in["changed"]
            )


            listed = list_windows(
                limit=100
            )

            current = next(
                item
                for item
                in (
                    listed["active"]
                    +
                    listed["scheduled"]
                )
                if (
                    int(item["id"])
                    ==
                    window_id
                )
            )


            self.assertEqual(
                current["member_ids"],
                [
                    1,
                    2,
                ],
            )

            self.assertEqual(
                current["member_count"],
                2,
            )


            # ------------------------------------------------
            # Rename the live group.
            #
            # Historical maintenance target keeps the original
            # snapshot label "Rack A".
            # ------------------------------------------------

            renamed = rename_group(
                self.group_a["id"],
                {
                    "name":
                        "Rack A Renamed",
                },
            )


            self.assertEqual(
                renamed["group"]["name"],
                "Rack A Renamed",
            )


            listed = list_windows(
                limit=100
            )

            current = next(
                item
                for item
                in (
                    listed["active"]
                    +
                    listed["scheduled"]
                )
                if (
                    int(item["id"])
                    ==
                    window_id
                )
            )


            self.assertEqual(
                current["group_name"],
                "Rack A",
            )

            self.assertEqual(
                current["member_ids"],
                [
                    1,
                    2,
                ],
            )


            # ------------------------------------------------
            # Delete the live group.
            #
            # ASIC-3 becomes ungrouped, but maintenance
            # snapshot [1,2] MUST survive.
            # ------------------------------------------------

            deleted = delete_group_api(
                self.group_a["id"]
            )


            self.assertTrue(
                deleted["success"]
            )


            listed = list_windows(
                limit=100
            )

            current = next(
                item
                for item
                in (
                    listed["active"]
                    +
                    listed["scheduled"]
                )
                if (
                    int(item["id"])
                    ==
                    window_id
                )
            )


            self.assertEqual(
                current["group_name"],
                "Rack A",
            )

            self.assertEqual(
                current["member_ids"],
                [
                    1,
                    2,
                ],
            )

            self.assertEqual(
                current["member_count"],
                2,
            )


            # ------------------------------------------------
            # Runtime proof:
            #
            # All three ASICs become OFFLINE.
            #
            # ASIC-1 and ASIC-2 are covered by the immutable
            # creation snapshot.
            #
            # ASIC-3 joined after creation, therefore it is
            # NOT covered even though it was later a member.
            # ------------------------------------------------

            conn = db()

            conn.execute("""
                UPDATE miners
                SET last_state='OFFLINE'
                WHERE id IN (
                    1,
                    2,
                    3
                )
            """)

            conn.commit()
            conn.close()


            policy = dict(
                DEFAULT_ANOMALY_POLICY
            )

            policy[
                "offline_grace_seconds"
            ] = 0


            runtime = FakeRuntime()


            service.anomaly_scan(
                runtime,
                anomaly_policy=policy,
            )

            service.anomaly_scan(
                runtime,
                anomaly_policy=policy,
            )


            conn = db()

            try:

                issue_rows = conn.execute("""
                    SELECT miner_id

                    FROM issues

                    WHERE
                        code='OFFLINE'
                        AND status='ACTIVE'

                    ORDER BY miner_id
                """).fetchall()

            finally:
                conn.close()


            active_issue_ids = [
                int(row["miner_id"])
                for row in issue_rows
            ]


            self.assertNotIn(
                1,
                active_issue_ids,
            )

            self.assertNotIn(
                2,
                active_issue_ids,
            )

            self.assertIn(
                3,
                active_issue_ids,
            )


            # ------------------------------------------------
            # Extend after the original live group no longer
            # exists. Snapshot identity still owns the window.
            # ------------------------------------------------

            extended = await extend_window(
                window_id,
                FakeRequest({
                    "ends_at":
                        now + 3600,

                    "note":
                        "Extended after group deletion",
                }),
            )


            extended_item = (
                extended["maintenance"]
            )


            self.assertEqual(
                extended_item["group_name"],
                "Rack A",
            )

            self.assertEqual(
                extended_item["member_ids"],
                [
                    1,
                    2,
                ],
            )

            self.assertEqual(
                extended_item["ends_at"],
                now + 3600,
            )


            # ------------------------------------------------
            # End the window explicitly.
            # ------------------------------------------------

            ended = end_window(
                window_id
            )


            ended_item = (
                ended["maintenance"]
            )


            self.assertEqual(
                ended_item["status"],
                "ENDED",
            )

            self.assertEqual(
                ended_item["group_name"],
                "Rack A",
            )

            self.assertEqual(
                ended_item["member_ids"],
                [
                    1,
                    2,
                ],
            )


        finally:

            reset_audit_actor(
                token
            )


        # ----------------------------------------------------
        # Final persistence / safety proof.
        # ----------------------------------------------------

        conn = db()

        try:

            snapshot_rows = conn.execute("""
                SELECT miner_id

                FROM maintenance_window_members

                WHERE window_id=?

                ORDER BY miner_id
            """, (
                window_id,
            )).fetchall()


            stored_window = conn.execute("""
                SELECT
                    scope,
                    group_id,
                    group_name,
                    ended_at

                FROM maintenance_windows

                WHERE id=?
            """, (
                window_id,
            )).fetchone()


            miner_1 = conn.execute("""
                SELECT group_id
                FROM miners
                WHERE id=1
            """).fetchone()


            miner_3 = conn.execute("""
                SELECT group_id
                FROM miners
                WHERE id=3
            """).fetchone()


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
            [
                int(row["miner_id"])
                for row in snapshot_rows
            ],
            [
                1,
                2,
            ],
        )


        self.assertEqual(
            stored_window["scope"],
            "GROUP",
        )

        self.assertEqual(
            stored_window["group_id"],
            self.group_a["id"],
        )

        self.assertEqual(
            stored_window["group_name"],
            "Rack A",
        )

        self.assertIsNotNone(
            stored_window["ended_at"]
        )


        # ASIC-1 remained in Rack B.
        self.assertEqual(
            miner_1["group_id"],
            self.group_b["id"],
        )


        # ASIC-3 was in Rack A when that live group
        # was deleted, therefore it is now ungrouped.
        self.assertIsNone(
            miner_3["group_id"]
        )


        self.assertEqual(
            control_jobs,
            0,
        )


        actions = [
            event["action"]
            for event in events
        ]


        for expected in (
            "MAINTENANCE_CREATE",
            "MINER_GROUP_ASSIGN",
            "MINER_GROUP_RENAME",
            "MINER_GROUP_DELETE",
            "MAINTENANCE_EXTEND",
            "MAINTENANCE_END",
        ):

            self.assertIn(
                expected,
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
