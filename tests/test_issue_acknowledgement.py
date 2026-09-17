import os
import sqlite3
import tempfile
import unittest

from fastapi import HTTPException

import config as app_config
from api.issues import create_issue_router
from audit.identity import (
    bind_audit_actor,
    reset_audit_actor,
)
from anomalies.analytics import issue_report
from anomalies.issues import (
    MAX_ACKNOWLEDGEMENT_NOTE_LENGTH,
    normalize_acknowledgement_note,
)
from anomalies.repository import (
    clear_issue_acknowledgement,
    set_issue_acknowledgement,
    transition_anomaly_condition,
)
from db import db, init_db


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class TemporaryIssueDatabaseMixin:
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-issue-ack-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)

        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path

        init_db()

        conn = db()
        conn.execute("""
            INSERT INTO miners
            (
                id,
                name,
                ip,
                driver
            )
            VALUES (
                1,
                'ASIC-1',
                '192.0.2.10',
                'bitmain_stock'
            )
        """)

        conn.execute("""
            INSERT INTO issues
            (
                id,
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
            VALUES (
                10,
                1,
                '192.0.2.10',
                'ASIC-1',
                'OVERHEAT',
                'CRITICAL',
                'ACTIVE',
                100,
                120,
                'hot'
            )
        """)

        conn.commit()
        conn.close()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db

        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(
                    self.path + suffix
                )
            except FileNotFoundError:
                pass


class IssueAcknowledgementMigrationTests(
    unittest.TestCase,
):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-issue-migration-",
            suffix=".db",
        )
        os.close(fd)

        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path

        conn = sqlite3.connect(
            self.path
        )

        conn.executescript("""
            CREATE TABLE issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                miner_id INTEGER NOT NULL,
                ip TEXT NOT NULL,
                name TEXT NOT NULL,

                code TEXT NOT NULL,
                severity TEXT NOT NULL,

                status TEXT NOT NULL,

                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                resolved_at INTEGER,

                message TEXT
            );

            INSERT INTO issues
            (
                id,
                miner_id,
                ip,
                name,
                code,
                severity,
                status,
                first_seen,
                last_seen,
                resolved_at,
                message
            )
            VALUES
            (
                42,
                7,
                '192.168.1.42',
                'Existing ASIC',
                'OFFLINE',
                'WARNING',
                'RESOLVED',
                100,
                200,
                200,
                'existing 0.4.0 issue'
            );
        """)

        conn.commit()
        conn.close()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db

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

    def test_0_4_0_issue_schema_migrates_idempotently(self):
        # First call upgrades the historical schema.
        init_db()

        # Second call proves the migration is safe
        # on every subsequent application startup.
        init_db()

        conn = db()

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(issues)"
            ).fetchall()
        }

        row = conn.execute("""
            SELECT *
            FROM issues
            WHERE id=42
        """).fetchone()

        conn.close()

        self.assertTrue({
            "acknowledged_at",
            "acknowledged_by",
            "acknowledgement_note",
        }.issubset(columns))

        # Existing history must survive the migration.
        self.assertEqual(
            row["status"],
            "RESOLVED",
        )
        self.assertEqual(
            row["message"],
            "existing 0.4.0 issue",
        )

        # Historical rows start unacknowledged.
        self.assertIsNone(
            row["acknowledged_at"]
        )
        self.assertIsNone(
            row["acknowledged_by"]
        )
        self.assertIsNone(
            row["acknowledgement_note"]
        )


class IssueAcknowledgementValidationTests(
    unittest.TestCase,
):
    def test_empty_note_becomes_none(self):
        self.assertIsNone(
            normalize_acknowledgement_note(
                "   "
            )
        )

    def test_non_string_note_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "string or null",
        ):
            normalize_acknowledgement_note(
                123
            )

    def test_note_length_is_bounded(self):
        with self.assertRaisesRegex(
            ValueError,
            "at most",
        ):
            normalize_acknowledgement_note(
                "x" * (
                    MAX_ACKNOWLEDGEMENT_NOTE_LENGTH
                    + 1
                )
            )


class IssueAcknowledgementPersistenceTests(
    TemporaryIssueDatabaseMixin,
    unittest.TestCase,
):
    def test_schema_contains_acknowledgement_columns(self):
        conn = db()

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(issues)"
            ).fetchall()
        }

        conn.close()

        self.assertTrue({
            "acknowledged_at",
            "acknowledged_by",
            "acknowledgement_note",
        }.issubset(columns))

    def test_acknowledgement_round_trip(self):
        issue = set_issue_acknowledgement(
            issue_id=10,
            actor="WEB:alice",
            note="Investigating cooling.",
            now=500,
        )

        self.assertEqual(
            issue["acknowledged_at"],
            500,
        )
        self.assertEqual(
            issue["acknowledged_by"],
            "WEB:alice",
        )
        self.assertEqual(
            issue["acknowledgement_note"],
            "Investigating cooling.",
        )

        report = issue_report(10)
        item = report["active"][0]

        self.assertTrue(
            item["acknowledged"]
        )
        self.assertEqual(
            item["acknowledged_by"],
            "WEB:alice",
        )
        self.assertEqual(
            item["acknowledgement_note"],
            "Investigating cooling.",
        )
        self.assertIsNotNone(
            item["acknowledged_at"]
        )

    def test_clear_acknowledgement_keeps_issue(self):
        set_issue_acknowledgement(
            issue_id=10,
            actor="WEB:alice",
            note="Seen",
            now=500,
        )

        issue = clear_issue_acknowledgement(
            10
        )

        self.assertEqual(
            issue["status"],
            "ACTIVE",
        )
        self.assertIsNone(
            issue["acknowledged_at"]
        )
        self.assertIsNone(
            issue["acknowledged_by"]
        )
        self.assertIsNone(
            issue["acknowledgement_note"]
        )

    def test_resolution_preserves_acknowledgement(self):
        set_issue_acknowledgement(
            issue_id=10,
            actor="WEB:alice",
            note="Monitoring",
            now=500,
        )

        miner = {
            "id": 1,
            "ip": "192.0.2.10",
            "name": "ASIC-1",
        }

        opened, resolved = (
            transition_anomaly_condition(
                miner=miner,
                code="OVERHEAT",
                severity="CRITICAL",
                observed=False,
                grace_seconds=0,
                message="temperature recovered",
                now=600,
            )
        )

        self.assertFalse(opened)
        self.assertTrue(resolved)

        conn = db()

        row = conn.execute("""
            SELECT *
            FROM issues
            WHERE id=10
        """).fetchone()

        conn.close()

        self.assertEqual(
            row["status"],
            "RESOLVED",
        )
        self.assertEqual(
            row["acknowledged_at"],
            500,
        )
        self.assertEqual(
            row["acknowledged_by"],
            "WEB:alice",
        )
        self.assertEqual(
            row["acknowledgement_note"],
            "Monitoring",
        )


class IssueAcknowledgementRouteTests(
    TemporaryIssueDatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):
    def endpoint(
        self,
        path,
        method,
        log_event=None,
    ):
        router = create_issue_router(
            log_event
            or (lambda **kwargs: None)
        )

        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    def test_router_exposes_ack_paths(self):
        router = create_issue_router(
            lambda **kwargs: None
        )

        routes = {
            (route.path, method)
            for route in router.routes
            for method in route.methods
        }

        self.assertIn(
            (
                "/api/issues/{issue_id}/acknowledgement",
                "PUT",
            ),
            routes,
        )

        self.assertIn(
            (
                "/api/issues/{issue_id}/acknowledgement",
                "DELETE",
            ),
            routes,
        )

    async def test_acknowledge_uses_request_actor_and_audits(self):
        events = []

        def log_event(**kwargs):
            events.append(kwargs)

        endpoint = self.endpoint(
            "/api/issues/{issue_id}/acknowledgement",
            "PUT",
            log_event,
        )

        token = bind_audit_actor(
            "WEB:alice"
        )

        try:
            result = await endpoint(
                10,
                FakeRequest({
                    "note": "Checking cooling.",
                }),
            )
        finally:
            reset_audit_actor(token)

        self.assertTrue(
            result["success"]
        )
        self.assertTrue(
            result["issue"]["acknowledged"]
        )
        self.assertEqual(
            result["issue"]["acknowledged_by"],
            "WEB:alice",
        )
        self.assertEqual(
            result["issue"]["acknowledgement_note"],
            "Checking cooling.",
        )

        self.assertEqual(
            events[0]["action"],
            "ISSUE_ACKNOWLEDGE",
        )
        self.assertEqual(
            events[0]["source"],
            "MANUAL",
        )
        self.assertEqual(
            events[0]["miner"]["id"],
            1,
        )

    async def test_unknown_issue_returns_404(self):
        endpoint = self.endpoint(
            "/api/issues/{issue_id}/acknowledgement",
            "PUT",
        )

        with self.assertRaises(
            HTTPException
        ) as ctx:
            await endpoint(
                999,
                FakeRequest({}),
            )

        self.assertEqual(
            ctx.exception.status_code,
            404,
        )

    async def test_unknown_body_fields_are_rejected(self):
        endpoint = self.endpoint(
            "/api/issues/{issue_id}/acknowledgement",
            "PUT",
        )

        with self.assertRaises(
            HTTPException
        ) as ctx:
            await endpoint(
                10,
                FakeRequest({
                    "note": "Seen",
                    "reboot": True,
                }),
            )

        self.assertEqual(
            ctx.exception.status_code,
            400,
        )

    async def test_unacknowledge_clears_operator_state(self):
        set_issue_acknowledgement(
            issue_id=10,
            actor="WEB:alice",
            note="Seen",
            now=500,
        )

        events = []

        endpoint = self.endpoint(
            "/api/issues/{issue_id}/acknowledgement",
            "DELETE",
            lambda **kwargs: events.append(
                kwargs
            ),
        )

        result = endpoint(10)

        self.assertTrue(
            result["success"]
        )
        self.assertFalse(
            result["issue"]["acknowledged"]
        )
        self.assertIsNone(
            result["issue"]["acknowledged_by"]
        )
        self.assertEqual(
            events[0]["action"],
            "ISSUE_UNACKNOWLEDGE",
        )


if __name__ == "__main__":
    unittest.main()
