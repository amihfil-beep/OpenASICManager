import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import config as app_config

from api.miner_groups import (
    create_miner_group_router,
)
from db import (
    db,
    init_db,
)


class MinerGroupRoutesTests(
    unittest.TestCase,
):

    def setUp(self):

        fd, path = tempfile.mkstemp(
            prefix="oam-miner-group-routes-",
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
                'ROUTE-MINER',
                '192.0.2.30',
                'bitmain_stock'
            )
        """)

        self.miner_id = (
            cursor.lastrowid
        )

        conn.commit()
        conn.close()

        self.events = []


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


    def make_router(self):

        return create_miner_group_router(
            lambda **kwargs:
                self.events.append(
                    kwargs
                )
        )


    def endpoint(
        self,
        path,
        method,
    ):

        return next(
            route.endpoint
            for route
            in self.make_router().routes
            if (
                route.path == path
                and
                method in route.methods
            )
        )


    def test_router_exposes_group_paths(self):

        paths = {
            (
                route.path,
                tuple(
                    sorted(
                        route.methods
                    )
                ),
            )
            for route
            in self.make_router().routes
        }

        self.assertIn(
            (
                "/api/miner-groups",
                ("GET",),
            ),
            paths,
        )

        self.assertIn(
            (
                "/api/miner-groups",
                ("POST",),
            ),
            paths,
        )

        self.assertIn(
            (
                "/api/miner-groups/{group_id}",
                ("PUT",),
            ),
            paths,
        )

        self.assertIn(
            (
                "/api/miner-groups/{group_id}",
                ("DELETE",),
            ),
            paths,
        )

        self.assertIn(
            (
                "/api/miners/{miner_id}/group",
                ("PUT",),
            ),
            paths,
        )

        self.assertIn(
            (
                "/api/miners/{miner_id}/group",
                ("DELETE",),
            ),
            paths,
        )


    @patch(
        "api.miner_groups.current_audit_actor",
        return_value="TEST-OPERATOR",
    )
    def test_create_assign_clear_delete_are_audited(
        self,
        actor,
    ):

        create = self.endpoint(
            "/api/miner-groups",
            "POST",
        )

        assign = self.endpoint(
            "/api/miners/{miner_id}/group",
            "PUT",
        )

        clear = self.endpoint(
            "/api/miners/{miner_id}/group",
            "DELETE",
        )

        delete = self.endpoint(
            "/api/miner-groups/{group_id}",
            "DELETE",
        )

        created = create({
            "name":
                "Rack A",
        })

        group_id = (
            created["group"]["id"]
        )

        assigned = assign(
            self.miner_id,
            {
                "group_id":
                    group_id,
            },
        )

        self.assertTrue(
            assigned["changed"]
        )

        cleared = clear(
            self.miner_id
        )

        self.assertTrue(
            cleared["changed"]
        )

        deleted = delete(
            group_id
        )

        self.assertTrue(
            deleted["success"]
        )

        actions = [
            event["action"]
            for event in self.events
        ]

        self.assertEqual(
            actions,
            [
                "MINER_GROUP_CREATE",
                "MINER_GROUP_ASSIGN",
                "MINER_GROUP_CLEAR",
                "MINER_GROUP_DELETE",
            ],
        )


    def test_duplicate_group_name_returns_conflict(self):

        create = self.endpoint(
            "/api/miner-groups",
            "POST",
        )

        create({
            "name":
                "Rack A",
        })

        with self.assertRaises(
            HTTPException
        ) as caught:

            create({
                "name":
                    " rack   a ",
            })

        self.assertEqual(
            caught.exception.status_code,
            409,
        )


    def test_assign_requires_existing_group(self):

        assign = self.endpoint(
            "/api/miners/{miner_id}/group",
            "PUT",
        )

        with self.assertRaises(
            HTTPException
        ) as caught:

            assign(
                self.miner_id,
                {
                    "group_id":
                        9999,
                },
            )

        self.assertEqual(
            caught.exception.status_code,
            404,
        )


    def test_assign_rejects_boolean_group_id(self):

        assign = self.endpoint(
            "/api/miners/{miner_id}/group",
            "PUT",
        )

        with self.assertRaises(
            HTTPException
        ) as caught:

            assign(
                self.miner_id,
                {
                    "group_id":
                        True,
                },
            )

        self.assertEqual(
            caught.exception.status_code,
            400,
        )


if __name__ == "__main__":
    unittest.main()
