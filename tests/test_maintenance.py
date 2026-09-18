import os
import sqlite3
import tempfile
import time
import unittest

from fastapi import HTTPException

import config as app_config
from anomalies.repository import (
    transition_anomaly_condition,
)
from api.maintenance import (
    create_maintenance_router,
)
from audit.identity import (
    bind_audit_actor,
    reset_audit_actor,
)
from db import db, init_db
from maintenance_windows.policy import (
    MAX_MAINTENANCE_DURATION_SECONDS,
    maintenance_status,
    maintenance_window_dict,
    normalize_maintenance_create,
    normalize_maintenance_extend,
)
from maintenance_windows.repository import (
    active_maintenance_snapshot,
    create_maintenance_window,
    end_maintenance_window,
    extend_maintenance_window,
    find_maintenance_conflict,
    get_maintenance_window,
)


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class TemporaryMaintenanceDatabaseMixin:
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-maintenance-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)

        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path

        init_db()

        conn = db()

        conn.executemany("""
            INSERT INTO miners
            (
                id,
                name,
                ip,
                driver
            )
            VALUES (?, ?, ?, ?)
        """, (
            (
                1,
                "ASIC-1",
                "192.0.2.11",
                "bitmain_stock",
            ),
            (
                2,
                "ASIC-2",
                "192.0.2.12",
                "awesome",
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


class MaintenanceValidationTests(
    unittest.TestCase,
):
    def test_farm_scope_rejects_miner_id(self):
        with self.assertRaisesRegex(
            ValueError,
            "miner_id must be null",
        ):
            normalize_maintenance_create(
                {
                    "scope": "FARM",
                    "miner_id": 1,
                    "ends_at": 200,
                },
                now=100,
            )

    def test_miner_scope_requires_miner_id(self):
        with self.assertRaisesRegex(
            ValueError,
            "miner_id is required",
        ):
            normalize_maintenance_create(
                {
                    "scope": "MINER",
                    "ends_at": 200,
                },
                now=100,
            )

    def test_already_expired_window_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "must be in the future",
        ):
            normalize_maintenance_create(
                {
                    "scope": "FARM",
                    "starts_at": 100,
                    "ends_at": 150,
                },
                now=200,
            )

    def test_duration_is_bounded(self):
        with self.assertRaisesRegex(
            ValueError,
            "must not exceed 7 days",
        ):
            normalize_maintenance_create(
                {
                    "scope": "FARM",
                    "starts_at": 100,
                    "ends_at": (
                        100
                        + MAX_MAINTENANCE_DURATION_SECONDS
                        + 1
                    ),
                },
                now=100,
            )

    def test_status_is_time_derived(self):
        base = {
            "ended_at": None,
            "starts_at": 200,
            "ends_at": 300,
        }

        self.assertEqual(
            maintenance_status(
                base,
                100,
            ),
            "SCHEDULED",
        )

        self.assertEqual(
            maintenance_status(
                base,
                250,
            ),
            "ACTIVE",
        )

        self.assertEqual(
            maintenance_status(
                base,
                300,
            ),
            "EXPIRED",
        )


class MaintenanceMigrationTests(
    unittest.TestCase,
):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-maintenance-migration-",
            suffix=".db",
        )
        os.close(fd)

        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path

        conn = sqlite3.connect(
            self.path
        )

        # Simulate an existing installation before
        # maintenance_windows existed.
        conn.executescript("""
            CREATE TABLE miners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                ip TEXT NOT NULL UNIQUE,
                driver TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );

            INSERT INTO miners
            (
                id,
                name,
                ip,
                driver,
                enabled
            )
            VALUES
            (
                77,
                'Existing ASIC',
                '192.0.2.77',
                'bitmain_stock',
                1
            );
        """)

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

    def test_existing_database_gains_maintenance_table_idempotently(self):
        # First startup upgrades the database.
        init_db()

        # Repeated startup must remain safe.
        init_db()

        conn = db()

        table = conn.execute("""
            SELECT name
            FROM sqlite_master
            WHERE
                type='table'
                AND name='maintenance_windows'
        """).fetchone()

        miner = conn.execute("""
            SELECT
                id,
                name,
                ip,
                driver,
                enabled
            FROM miners
            WHERE id=77
        """).fetchone()

        conn.close()

        self.assertIsNotNone(
            table
        )

        # Existing application data survives.
        self.assertEqual(
            miner["name"],
            "Existing ASIC",
        )
        self.assertEqual(
            miner["ip"],
            "192.0.2.77",
        )
        self.assertEqual(
            miner["driver"],
            "bitmain_stock",
        )
        self.assertEqual(
            miner["enabled"],
            1,
        )


class MaintenancePersistenceTests(
    TemporaryMaintenanceDatabaseMixin,
    unittest.TestCase,
):
    def create_window(
        self,
        *,
        scope="MINER",
        miner_id=1,
        starts_at=100,
        ends_at=200,
        actor="WEB:alice",
        note="maintenance",
    ):
        return create_maintenance_window(
            {
                "scope": scope,
                "miner_id": (
                    miner_id
                    if scope == "MINER"
                    else None
                ),
                "starts_at": starts_at,
                "ends_at": ends_at,
                "note": note,
            },
            actor=actor,
            now=90,
        )

    def test_schema_contains_maintenance_table(self):
        conn = db()

        row = conn.execute("""
            SELECT name
            FROM sqlite_master
            WHERE
                type='table'
                AND name='maintenance_windows'
        """).fetchone()

        conn.close()

        self.assertIsNotNone(
            row
        )

    def test_active_snapshot_supports_farm_and_miner(self):
        self.create_window(
            scope="MINER",
            miner_id=1,
            starts_at=100,
            ends_at=200,
        )

        snapshot = (
            active_maintenance_snapshot(
                150
            )
        )

        self.assertFalse(
            snapshot["farm_active"]
        )
        self.assertEqual(
            snapshot["miner_ids"],
            {1},
        )

        self.create_window(
            scope="FARM",
            starts_at=120,
            ends_at=180,
        )

        snapshot = (
            active_maintenance_snapshot(
                150
            )
        )

        self.assertTrue(
            snapshot["farm_active"]
        )
        self.assertEqual(
            snapshot["miner_ids"],
            {1},
        )

    def test_expired_window_is_not_active(self):
        self.create_window(
            starts_at=100,
            ends_at=150,
        )

        snapshot = (
            active_maintenance_snapshot(
                151
            )
        )

        self.assertEqual(
            snapshot["miner_ids"],
            set(),
        )

    def test_overlap_is_detected_for_same_miner(self):
        row = self.create_window(
            starts_at=100,
            ends_at=200,
        )

        conflict = (
            find_maintenance_conflict(
                scope="MINER",
                miner_id=1,
                starts_at=150,
                ends_at=250,
            )
        )

        self.assertEqual(
            conflict["id"],
            row["id"],
        )

        other_miner = (
            find_maintenance_conflict(
                scope="MINER",
                miner_id=2,
                starts_at=150,
                ends_at=250,
            )
        )

        self.assertIsNone(
            other_miner
        )

    def test_extend_and_end_preserve_history(self):
        row = self.create_window(
            starts_at=100,
            ends_at=200,
        )

        extended = (
            extend_maintenance_window(
                window_id=row["id"],
                normalized={
                    "ends_at": 250,
                    "note": "extended",
                },
                actor="WEB:bob",
                now=150,
            )
        )

        self.assertEqual(
            extended["ends_at"],
            250,
        )
        self.assertEqual(
            extended["updated_by"],
            "WEB:bob",
        )

        ended = end_maintenance_window(
            window_id=row["id"],
            actor="WEB:carol",
            now=175,
        )

        self.assertEqual(
            ended["ended_at"],
            175,
        )
        self.assertEqual(
            ended["ended_by"],
            "WEB:carol",
        )

        stored = get_maintenance_window(
            row["id"]
        )

        self.assertEqual(
            stored["note"],
            "extended",
        )


class MaintenanceAnomalyInteractionTests(
    TemporaryMaintenanceDatabaseMixin,
    unittest.TestCase,
):
    @property
    def miner(self):
        return {
            "id": 1,
            "ip": "192.0.2.11",
            "name": "ASIC-1",
        }

    def test_suppressed_new_condition_does_not_create_candidate(self):
        opened, resolved = (
            transition_anomaly_condition(
                miner=self.miner,
                code="OFFLINE",
                severity="CRITICAL",
                observed=True,
                grace_seconds=30,
                message="offline",
                now=100,
                suppress_new=True,
            )
        )

        self.assertFalse(
            opened
        )
        self.assertFalse(
            resolved
        )

        conn = db()

        candidate = conn.execute("""
            SELECT *
            FROM anomaly_candidates
            WHERE
                miner_id=1
                AND code='OFFLINE'
        """).fetchone()

        issue = conn.execute("""
            SELECT *
            FROM issues
            WHERE
                miner_id=1
                AND code='OFFLINE'
        """).fetchone()

        conn.close()

        self.assertIsNone(
            candidate
        )
        self.assertIsNone(
            issue
        )

    def test_suppression_clears_existing_candidate(self):
        transition_anomaly_condition(
            miner=self.miner,
            code="OFFLINE",
            severity="CRITICAL",
            observed=True,
            grace_seconds=30,
            message="offline",
            now=100,
        )

        transition_anomaly_condition(
            miner=self.miner,
            code="OFFLINE",
            severity="CRITICAL",
            observed=True,
            grace_seconds=30,
            message="offline",
            now=110,
            suppress_new=True,
        )

        conn = db()

        candidate = conn.execute("""
            SELECT *
            FROM anomaly_candidates
            WHERE
                miner_id=1
                AND code='OFFLINE'
        """).fetchone()

        conn.close()

        self.assertIsNone(
            candidate
        )

    def test_after_maintenance_grace_starts_again(self):
        transition_anomaly_condition(
            miner=self.miner,
            code="OFFLINE",
            severity="CRITICAL",
            observed=True,
            grace_seconds=30,
            message="offline",
            now=100,
            suppress_new=True,
        )

        opened, _ = (
            transition_anomaly_condition(
                miner=self.miner,
                code="OFFLINE",
                severity="CRITICAL",
                observed=True,
                grace_seconds=30,
                message="offline",
                now=200,
            )
        )

        self.assertFalse(
            opened
        )

        opened, _ = (
            transition_anomaly_condition(
                miner=self.miner,
                code="OFFLINE",
                severity="CRITICAL",
                observed=True,
                grace_seconds=30,
                message="offline",
                now=231,
            )
        )

        self.assertTrue(
            opened
        )

        conn = db()

        issue = conn.execute("""
            SELECT *
            FROM issues
            WHERE
                miner_id=1
                AND code='OFFLINE'
                AND status='ACTIVE'
        """).fetchone()

        conn.close()

        self.assertEqual(
            issue["first_seen"],
            200,
        )

    def test_existing_active_issue_can_resolve_during_maintenance(self):
        transition_anomaly_condition(
            miner=self.miner,
            code="OFFLINE",
            severity="CRITICAL",
            observed=True,
            grace_seconds=0,
            message="offline",
            now=100,
        )

        opened, _ = (
            transition_anomaly_condition(
                miner=self.miner,
                code="OFFLINE",
                severity="CRITICAL",
                observed=True,
                grace_seconds=0,
                message="offline",
                now=101,
            )
        )

        self.assertTrue(
            opened
        )

        _, resolved = (
            transition_anomaly_condition(
                miner=self.miner,
                code="OFFLINE",
                severity="CRITICAL",
                observed=False,
                grace_seconds=0,
                message="reachable",
                now=110,
                suppress_new=True,
            )
        )

        self.assertTrue(
            resolved
        )


class MaintenanceRouteTests(
    TemporaryMaintenanceDatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):
    def endpoint(
        self,
        path,
        method,
        log_event=None,
    ):
        router = create_maintenance_router(
            log_event
            or (lambda **kwargs: None)
        )

        return next(
            route.endpoint
            for route in router.routes
            if (
                route.path == path
                and method in route.methods
            )
        )

    def test_router_exposes_maintenance_paths(self):
        router = create_maintenance_router(
            lambda **kwargs: None
        )

        routes = {
            (
                route.path,
                method,
            )
            for route in router.routes
            for method in route.methods
        }

        self.assertIn(
            (
                "/api/maintenance",
                "GET",
            ),
            routes,
        )

        self.assertIn(
            (
                "/api/maintenance",
                "POST",
            ),
            routes,
        )

        self.assertIn(
            (
                "/api/maintenance/{window_id}",
                "PUT",
            ),
            routes,
        )

        self.assertIn(
            (
                "/api/maintenance/{window_id}/end",
                "POST",
            ),
            routes,
        )

    async def test_create_miner_window_uses_actor_and_audits(self):
        events = []

        endpoint = self.endpoint(
            "/api/maintenance",
            "POST",
            lambda **kwargs: events.append(
                kwargs
            ),
        )

        now = int(
            time.time()
        )

        token = bind_audit_actor(
            "WEB:alice"
        )

        try:
            result = await endpoint(
                FakeRequest({
                    "scope": "MINER",
                    "miner_id": 1,
                    "ends_at": now + 3600,
                    "note": "Power work",
                })
            )
        finally:
            reset_audit_actor(
                token
            )

        item = result[
            "maintenance"
        ]

        self.assertTrue(
            result["success"]
        )
        self.assertEqual(
            item["scope"],
            "MINER",
        )
        self.assertEqual(
            item["created_by"],
            "WEB:alice",
        )
        self.assertEqual(
            item["miner_name"],
            "ASIC-1",
        )
        self.assertEqual(
            events[0]["action"],
            "MAINTENANCE_CREATE",
        )
        self.assertEqual(
            events[0]["miner"]["id"],
            1,
        )

    async def test_unknown_miner_is_rejected(self):
        endpoint = self.endpoint(
            "/api/maintenance",
            "POST",
        )

        now = int(
            time.time()
        )

        with self.assertRaises(
            HTTPException
        ) as ctx:
            await endpoint(
                FakeRequest({
                    "scope": "MINER",
                    "miner_id": 999,
                    "ends_at": now + 3600,
                })
            )

        self.assertEqual(
            ctx.exception.status_code,
            404,
        )

    async def test_overlap_returns_409(self):
        now = int(
            time.time()
        )

        create_maintenance_window(
            {
                "scope": "MINER",
                "miner_id": 1,
                "starts_at": now,
                "ends_at": now + 3600,
                "note": None,
            },
            actor="LOCAL",
            now=now,
        )

        endpoint = self.endpoint(
            "/api/maintenance",
            "POST",
        )

        with self.assertRaises(
            HTTPException
        ) as ctx:
            await endpoint(
                FakeRequest({
                    "scope": "MINER",
                    "miner_id": 1,
                    "starts_at": now + 60,
                    "ends_at": now + 1800,
                })
            )

        self.assertEqual(
            ctx.exception.status_code,
            409,
        )

    async def test_extend_endpoint_updates_expiry(self):
        now = int(
            time.time()
        )

        row = create_maintenance_window(
            {
                "scope": "FARM",
                "miner_id": None,
                "starts_at": now,
                "ends_at": now + 1800,
                "note": None,
            },
            actor="LOCAL",
            now=now,
        )

        endpoint = self.endpoint(
            "/api/maintenance/{window_id}",
            "PUT",
        )

        result = await endpoint(
            row["id"],
            FakeRequest({
                "ends_at": now + 3600,
                "note": "Extended",
            }),
        )

        self.assertEqual(
            result[
                "maintenance"
            ]["ends_at"],
            now + 3600,
        )

    def test_end_endpoint_preserves_record(self):
        now = int(
            time.time()
        )

        row = create_maintenance_window(
            {
                "scope": "FARM",
                "miner_id": None,
                "starts_at": now,
                "ends_at": now + 3600,
                "note": None,
            },
            actor="LOCAL",
            now=now,
        )

        endpoint = self.endpoint(
            "/api/maintenance/{window_id}/end",
            "POST",
        )

        result = endpoint(
            row["id"]
        )

        self.assertEqual(
            result[
                "maintenance"
            ]["status"],
            "ENDED",
        )

        stored = get_maintenance_window(
            row["id"]
        )

        self.assertIsNotNone(
            stored
        )
        self.assertIsNotNone(
            stored["ended_at"]
        )


if __name__ == "__main__":
    unittest.main()
