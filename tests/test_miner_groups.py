import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import config as app_config

from api.miner_groups import (
    create_miner_group_router,
)
from api.system import (
    create_system_router,
)
from audit.service import AuditRuntime
from db import (
    db,
    init_db,
)
from miner_groups.repository import (
    create_group,
    delete_group,
    get_group,
    list_groups,
    rename_group,
    set_miner_group,
)
from miner_groups.service import (
    normalize_group_name,
    normalized_group_key,
)


class MinerGroupValidationTests(
    unittest.TestCase,
):

    def test_group_name_normalization(self):

        self.assertEqual(
            normalize_group_name(
                "  Rack   A  "
            ),
            "Rack A",
        )

        self.assertEqual(
            normalized_group_key(
                " Rack A "
            ),
            "rack a",
        )


    def test_invalid_group_names_are_rejected(self):

        for value in (
            "",
            "   ",
            None,
            123,
            "x" * 81,
        ):

            with self.subTest(
                value=value
            ):

                with self.assertRaises(
                    ValueError
                ):
                    normalize_group_name(
                        value
                    )


class MinerGroupPersistenceTests(
    unittest.TestCase,
):

    def setUp(self):

        fd, path = tempfile.mkstemp(
            prefix="oam-miner-groups-",
            suffix=".db",
        )

        os.close(fd)
        os.unlink(path)

        self.path = path

        self.original_db = (
            app_config.DATABASE_PATH
        )

        app_config.DATABASE_PATH = (
            self.path
        )

        init_db()

        conn = db()

        cursor = conn.execute("""
            INSERT INTO miners(
                name,
                ip,
                driver
            )
            VALUES (
                'MINER-1',
                '192.0.2.10',
                'bitmain_stock'
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


    def create_test_group(
        self,
        name="Rack A",
    ):

        return create_group(
            name=name,
            normalized_name=(
                normalized_group_key(
                    name
                )
            ),
            actor="TEST",
        )


    def test_schema_adds_nullable_group_id(self):

        conn = db()

        try:
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(miners)"
                ).fetchall()
            }

            row = conn.execute("""
                SELECT group_id
                FROM miners
                WHERE id=?
            """, (
                self.miner_id,
            )).fetchone()

        finally:
            conn.close()

        self.assertIn(
            "group_id",
            columns,
        )

        self.assertIsNone(
            row["group_id"]
        )


    def test_create_and_list_group(self):

        group = (
            self.create_test_group()
        )

        groups = list_groups()

        self.assertEqual(
            len(groups),
            1,
        )

        self.assertEqual(
            group["name"],
            "Rack A",
        )

        self.assertEqual(
            groups[0]["member_count"],
            0,
        )


    def test_normalized_group_name_is_unique(self):

        self.create_test_group(
            "Rack A"
        )

        with self.assertRaises(
            ValueError
        ):
            create_group(
                name="rack a",
                normalized_name=(
                    normalized_group_key(
                        "rack a"
                    )
                ),
                actor="TEST",
            )


    def test_group_can_be_renamed(self):

        group = (
            self.create_test_group()
        )

        updated = rename_group(
            group_id=group["id"],
            name="Room 2",
            normalized_name=(
                normalized_group_key(
                    "Room 2"
                )
            ),
            actor="TEST-2",
        )

        self.assertEqual(
            updated["name"],
            "Room 2",
        )

        self.assertEqual(
            updated["updated_by"],
            "TEST-2",
        )


    def test_membership_assign_move_and_clear(self):

        first = (
            self.create_test_group(
                "Rack A"
            )
        )

        second = (
            self.create_test_group(
                "Rack B"
            )
        )

        assigned = set_miner_group(
            self.miner_id,
            first["id"],
        )

        self.assertEqual(
            assigned["status"],
            "UPDATED",
        )

        moved = set_miner_group(
            self.miner_id,
            second["id"],
        )

        self.assertEqual(
            moved["old_group_id"],
            first["id"],
        )

        self.assertEqual(
            moved["new_group_id"],
            second["id"],
        )

        cleared = set_miner_group(
            self.miner_id,
            None,
        )

        self.assertEqual(
            cleared["status"],
            "UPDATED",
        )

        conn = db()

        try:
            row = conn.execute("""
                SELECT group_id
                FROM miners
                WHERE id=?
            """, (
                self.miner_id,
            )).fetchone()

        finally:
            conn.close()

        self.assertIsNone(
            row["group_id"]
        )


    def test_delete_group_atomically_ungroups_members(self):

        group = (
            self.create_test_group()
        )

        set_miner_group(
            self.miner_id,
            group["id"],
        )

        deleted = delete_group(
            group["id"]
        )

        self.assertEqual(
            deleted["member_count"],
            1,
        )

        self.assertIsNone(
            get_group(
                group["id"]
            )
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

        finally:
            conn.close()

        self.assertIsNone(
            miner["group_id"]
        )


class MinerGroupMigrationTests(
    unittest.TestCase,
):

    def test_existing_miners_start_ungrouped(self):

        fd, path = tempfile.mkstemp(
            prefix="oam-miner-groups-migration-",
            suffix=".db",
        )

        os.close(fd)

        original_db = (
            app_config.DATABASE_PATH
        )

        try:
            conn = sqlite3.connect(
                path
            )

            conn.executescript("""
                CREATE TABLE miners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    ip TEXT NOT NULL UNIQUE,
                    driver TEXT NOT NULL
                );

                INSERT INTO miners(
                    name,
                    ip,
                    driver
                )
                VALUES (
                    'OLD-MINER',
                    '192.0.2.20',
                    'bitmain_stock'
                );
            """)

            conn.commit()
            conn.close()

            app_config.DATABASE_PATH = (
                path
            )

            init_db()

            conn = db()

            try:
                row = conn.execute("""
                    SELECT group_id
                    FROM miners
                    WHERE name='OLD-MINER'
                """).fetchone()

            finally:
                conn.close()

            self.assertIsNone(
                row["group_id"]
            )

        finally:
            app_config.DATABASE_PATH = (
                original_db
            )

            for suffix in (
                "",
                "-shm",
                "-wal",
            ):
                try:
                    os.unlink(
                        path + suffix
                    )
                except FileNotFoundError:
                    pass


class MinerGroupSemanticIntegrationTests(
    unittest.TestCase,
):

    def setUp(self):

        fd, path = tempfile.mkstemp(
            prefix="oam-group-semantic-",
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
            INSERT INTO miners(
                name,
                ip,
                driver,
                enabled,
                schedule_enabled,
                last_state
            )
            VALUES (
                'SEMANTIC-MINER',
                '192.0.2.66',
                'bitmain_stock',
                1,
                1,
                'MINING'
            )
        """)

        self.miner_id = (
            cursor.lastrowid
        )

        conn.commit()
        conn.close()


        self.notifications = []

        self.audit_runtime = AuditRuntime(
            notify_event=(
                lambda **kwargs:
                    self.notifications.append(
                        kwargs
                    )
            )
        )


        self.group_router = (
            create_miner_group_router(
                self.audit_runtime.log_event
            )
        )

        self.system_router = (
            create_system_router()
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


    def endpoint(
        self,
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


    def status_miner(self):

        endpoint = self.endpoint(
            self.system_router,
            "/api/status",
            "GET",
        )

        with patch(
            "api.system.list_active_control_jobs",
            return_value=[],
        ), patch(
            "api.system.next_transition",
            return_value=None,
        ), patch(
            "api.system.desired_state",
            return_value="MINING",
        ):

            result = endpoint()

        return next(
            miner
            for miner
            in result["miners"]
            if (
                miner["id"]
                ==
                self.miner_id
            )
        )


    def test_group_lifecycle_changes_metadata_only(self):

        create = self.endpoint(
            self.group_router,
            "/api/miner-groups",
            "POST",
        )

        assign = self.endpoint(
            self.group_router,
            "/api/miners/{miner_id}/group",
            "PUT",
        )

        clear = self.endpoint(
            self.group_router,
            "/api/miners/{miner_id}/group",
            "DELETE",
        )

        delete = self.endpoint(
            self.group_router,
            "/api/miner-groups/{group_id}",
            "DELETE",
        )


        with patch(
            "api.miner_groups.current_audit_actor",
            return_value="SEMANTIC-OPERATOR",
        ):

            rack_a = create({
                "name":
                    "Rack A",
            })["group"]

            rack_b = create({
                "name":
                    "Rack B",
            })["group"]


            assign(
                self.miner_id,
                {
                    "group_id":
                        rack_a["id"],
                },
            )


            status = (
                self.status_miner()
            )

            self.assertEqual(
                status["group_id"],
                rack_a["id"],
            )

            self.assertEqual(
                status["group_name"],
                "Rack A",
            )


            assign(
                self.miner_id,
                {
                    "group_id":
                        rack_b["id"],
                },
            )


            status = (
                self.status_miner()
            )

            self.assertEqual(
                status["group_id"],
                rack_b["id"],
            )

            self.assertEqual(
                status["group_name"],
                "Rack B",
            )


            clear(
                self.miner_id
            )


            status = (
                self.status_miner()
            )

            self.assertIsNone(
                status["group_id"]
            )

            self.assertIsNone(
                status["group_name"]
            )


            assign(
                self.miner_id,
                {
                    "group_id":
                        rack_b["id"],
                },
            )


            deleted = delete(
                rack_b["id"]
            )

            self.assertEqual(
                deleted[
                    "deleted_group"
                ][
                    "ungrouped_members"
                ],
                1,
            )


        status = (
            self.status_miner()
        )

        self.assertIsNone(
            status["group_id"]
        )

        self.assertIsNone(
            status["group_name"]
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


            control_job_count = (
                conn.execute("""
                    SELECT COUNT(*) AS count
                    FROM control_jobs
                """).fetchone()[
                    "count"
                ]
            )


            audit_actions = [
                row["action"]
                for row
                in conn.execute("""
                    SELECT action
                    FROM action_log
                    ORDER BY id
                """).fetchall()
            ]


            rack_b_exists = (
                conn.execute("""
                    SELECT id
                    FROM miner_groups
                    WHERE id=?
                """, (
                    rack_b["id"],
                )).fetchone()
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
            "MINING",
        )

        self.assertEqual(
            miner["schedule_enabled"],
            1,
        )

        self.assertIsNone(
            rack_b_exists
        )

        self.assertEqual(
            control_job_count,
            0,
        )


        self.assertEqual(
            audit_actions,
            [
                "MINER_GROUP_CREATE",
                "MINER_GROUP_CREATE",
                "MINER_GROUP_ASSIGN",
                "MINER_GROUP_ASSIGN",
                "MINER_GROUP_CLEAR",
                "MINER_GROUP_ASSIGN",
                "MINER_GROUP_DELETE",
            ],
        )


if __name__ == "__main__":
    unittest.main()
