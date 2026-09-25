import os
import tempfile
import unittest

from datetime import (
    datetime,
    timezone,
)
from unittest.mock import patch

from fastapi import HTTPException

import config as app_config

from api.control import (
    create_control_router,
)
from audit.identity import (
    audit_source,
    bind_audit_actor,
    reset_audit_actor,
)
from audit.service import (
    AuditRuntime,
)
from control.bulk import (
    BULK_CONTROL_MAX_TARGETS,
    BulkControlValidationError,
    bulk_control_preview,
    execute_bulk_control,
    normalize_bulk_control_request,
)
from control.queue import (
    QueueRuntime,
)
from control.repository import (
    create_control_job,
)
from db import (
    db,
    init_db,
)
from miner_groups.repository import (
    create_group,
)


class FakeRequest:

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class BulkControlValidationTests(
    unittest.TestCase,
):

    def test_requires_exactly_one_selection_source(self):

        with self.assertRaisesRegex(
            BulkControlValidationError,
            "Exactly one selection source",
        ):
            normalize_bulk_control_request(
                {}
            )


        with self.assertRaisesRegex(
            BulkControlValidationError,
            "Exactly one selection source",
        ):
            normalize_bulk_control_request({
                "miner_ids": [1],
                "group_id": 2,
            })


    def test_selected_ids_must_be_unique_positive_integers(self):

        for payload in (
            {
                "miner_ids":
                    [1, 1],
            },
            {
                "miner_ids":
                    [True],
            },
            {
                "miner_ids":
                    [0],
            },
        ):

            with self.subTest(
                payload=payload
            ):

                with self.assertRaises(
                    BulkControlValidationError
                ):

                    normalize_bulk_control_request(
                        payload
                    )


    def test_selected_batch_is_bounded(self):

        with self.assertRaisesRegex(
            BulkControlValidationError,
            "limited to",
        ):

            normalize_bulk_control_request({
                "miner_ids":
                    list(
                        range(
                            1,
                            BULK_CONTROL_MAX_TARGETS
                            + 2,
                        )
                    )
            })


    def test_selected_ids_are_deterministic(self):

        normalized = (
            normalize_bulk_control_request({
                "miner_ids":
                    [
                        9,
                        2,
                        7,
                    ],
            })
        )

        self.assertEqual(
            normalized["miner_ids"],
            [
                2,
                7,
                9,
            ],
        )


class BulkControlDatabaseMixin:

    def setUp(self):

        fd, path = tempfile.mkstemp(
            prefix="oam-bulk-control-",
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
                group_id
            )
            VALUES (
                ?, ?, ?, ?, ?, 1, ?
            )
        """, (
            (
                1,
                "ASIC-1",
                "192.0.2.91",
                "bitmain_stock",
                1,
                self.group_a["id"],
            ),
            (
                2,
                "ASIC-2",
                "192.0.2.92",
                "awesome",
                0,
                self.group_a["id"],
            ),
            (
                3,
                "ASIC-3",
                "192.0.2.93",
                "unset",
                1,
                self.group_a["id"],
            ),
            (
                4,
                "ASIC-4",
                "192.0.2.94",
                "awesome",
                1,
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


    @staticmethod
    def endpoint(
        router,
        path,
        method="POST",
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


class BulkControlPreviewTests(
    BulkControlDatabaseMixin,
    unittest.TestCase,
):

    def test_selected_preview_reports_each_target(self):

        preview = (
            bulk_control_preview(
                "pause",
                {
                    "miner_ids":
                        [
                            999,
                            3,
                            1,
                            2,
                        ],
                },
            )
        )


        self.assertEqual(
            [
                target["miner_id"]
                for target
                in preview["targets"]
            ],
            [
                1,
                2,
                3,
                999,
            ],
        )


        by_id = {
            target["miner_id"]:
                target
            for target
            in preview["targets"]
        }


        self.assertTrue(
            by_id[1]["eligible"]
        )

        self.assertEqual(
            by_id[2]["rejection_code"],
            "DISABLED",
        )

        self.assertEqual(
            by_id[3]["rejection_code"],
            "UNSUPPORTED_DRIVER",
        )

        self.assertEqual(
            by_id[999]["rejection_code"],
            "NOT_FOUND",
        )


        self.assertEqual(
            preview["requested_count"],
            4,
        )

        self.assertEqual(
            preview["eligible_count"],
            1,
        )

        self.assertEqual(
            preview["rejected_count"],
            3,
        )


    def test_group_preview_resolves_current_group_members(self):

        preview = bulk_control_preview(
            "resume",
            {
                "group_id":
                    self.group_a["id"],
            },
        )


        self.assertEqual(
            preview["selection"],
            {
                "type":
                    "GROUP",

                "group_id":
                    self.group_a["id"],

                "group_name":
                    "Rack A",
            },
        )


        self.assertEqual(
            [
                item["miner_id"]
                for item
                in preview["targets"]
            ],
            [
                1,
                2,
                3,
            ],
        )


    def test_active_job_is_explicitly_ineligible(self):

        conn = db()

        miner = conn.execute("""
            SELECT *
            FROM miners
            WHERE id=1
        """).fetchone()

        conn.close()


        job_id = create_control_job(
            miner=miner,
            source="TEST",
            action="pause",
            target_state="PAUSED",
            max_attempts=3,
            now=100,
        )


        preview = bulk_control_preview(
            "resume",
            {
                "miner_ids":
                    [
                        1,
                    ],
            },
        )


        target = preview[
            "targets"
        ][0]


        self.assertFalse(
            target["eligible"]
        )

        self.assertEqual(
            target["rejection_code"],
            "ACTIVE_JOB",
        )

        self.assertEqual(
            target["active_job"]["job_id"],
            job_id,
        )


    def test_empty_group_is_rejected(self):

        empty = create_group(
            name="Empty",
            normalized_name="empty",
            actor="TEST",
        )


        with self.assertRaisesRegex(
            BulkControlValidationError,
            "no miners",
        ):

            bulk_control_preview(
                "pause",
                {
                    "group_id":
                        empty["id"],
                },
            )


class BulkControlExecutionTests(
    BulkControlDatabaseMixin,
    unittest.TestCase,
):

    def test_pause_fans_out_only_to_eligible_miners(self):

        queue_calls = []
        events = []


        def queue_control(
            miner_id,
            action,
            manual=False,
        ):
            queue_calls.append(
                (
                    miner_id,
                    action,
                    manual,
                )
            )

            return {
                "queued":
                    True,

                "already_active":
                    False,

                "job_id":
                    100 + miner_id,

                "status":
                    "QUEUED",

                "action":
                    action,

                "target_state":
                    "PAUSED",
            }


        result = execute_bulk_control(
            action="pause",
            payload={
                "miner_ids":
                    [
                        3,
                        2,
                        1,
                        999,
                    ],
            },
            queue_control=queue_control,
            queue_reboot=(
                lambda miner_id: None
            ),
            log_event=(
                lambda **kwargs:
                    events.append(
                        kwargs
                    )
            ),
        )


        self.assertEqual(
            queue_calls,
            [
                (
                    1,
                    "pause",
                    True,
                ),
            ],
        )


        self.assertEqual(
            result["accepted_count"],
            1,
        )

        self.assertEqual(
            result["rejected_count"],
            3,
        )


        accepted = next(
            item
            for item
            in result["results"]
            if item["miner_id"] == 1
        )


        self.assertTrue(
            accepted["accepted"]
        )

        self.assertEqual(
            accepted["job_id"],
            101,
        )


        rejected_actions = [
            event["action"]
            for event
            in events
        ]


        self.assertEqual(
            rejected_actions.count(
                "BULK_PAUSE_REJECTED"
            ),
            3,
        )


    def test_one_queue_failure_does_not_stop_other_targets(self):

        calls = []
        events = []


        def queue_control(
            miner_id,
            action,
            manual=False,
        ):

            calls.append(
                miner_id
            )


            if miner_id == 1:
                raise RuntimeError(
                    "network failure"
                )


            return {
                "queued":
                    True,

                "already_active":
                    False,

                "job_id":
                    200 + miner_id,

                "status":
                    "QUEUED",

                "action":
                    action,

                "target_state":
                    "MINING",
            }


        result = execute_bulk_control(
            action="resume",
            payload={
                "miner_ids":
                    [
                        4,
                        1,
                    ],
            },
            queue_control=queue_control,
            queue_reboot=(
                lambda miner_id: None
            ),
            log_event=(
                lambda **kwargs:
                    events.append(
                        kwargs
                    )
            ),
        )


        self.assertEqual(
            calls,
            [
                1,
                4,
            ],
        )


        by_id = {
            item["miner_id"]:
                item
            for item
            in result["results"]
        }


        self.assertFalse(
            by_id[1]["accepted"]
        )

        self.assertEqual(
            by_id[1]["rejection_code"],
            "QUEUE_ERROR",
        )


        self.assertTrue(
            by_id[4]["accepted"]
        )

        self.assertEqual(
            result["accepted_count"],
            1,
        )

        self.assertEqual(
            result["rejected_count"],
            1,
        )


    def test_reboot_requires_explicit_backend_confirmation(self):

        with self.assertRaisesRegex(
            BulkControlValidationError,
            "confirm_reboot=true",
        ):

            execute_bulk_control(
                action="reboot",
                payload={
                    "miner_ids":
                        [
                            1,
                        ],
                },
                queue_control=(
                    lambda *args, **kwargs:
                        None
                ),
                queue_reboot=(
                    lambda miner_id:
                        None
                ),
                log_event=(
                    lambda **kwargs:
                        None
                ),
            )


    def test_reboot_uses_existing_per_miner_reboot_queue(self):

        calls = []


        def reboot(miner_id):

            calls.append(
                miner_id
            )

            return {
                "queued":
                    True,

                "already_active":
                    False,

                "job_id":
                    301,

                "status":
                    "QUEUED",

                "action":
                    "reboot",

                "target_state":
                    "REBOOTED",
            }


        result = execute_bulk_control(
            action="reboot",
            payload={
                "miner_ids":
                    [
                        1,
                    ],

                "confirm_reboot":
                    True,
            },
            queue_control=(
                lambda *args, **kwargs:
                    None
            ),
            queue_reboot=reboot,
            log_event=(
                lambda **kwargs:
                    None
            ),
        )


        self.assertEqual(
            calls,
            [
                1,
            ],
        )

        self.assertEqual(
            result["accepted_count"],
            1,
        )

        self.assertEqual(
            result["results"][0]["job_id"],
            301,
        )


class BulkControlRouteTests(
    BulkControlDatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    def make_router(
        self,
        queue_control=None,
        queue_reboot=None,
        log_event=None,
    ):

        return create_control_router(
            queue_control
            or (
                lambda *args, **kwargs:
                    {}
            ),
            queue_reboot
            or (
                lambda *args, **kwargs:
                    {}
            ),
            log_event
            or (
                lambda **kwargs:
                    None
            ),
        )


    async def test_preview_route_returns_deterministic_contract(self):

        router = self.make_router()

        endpoint = self.endpoint(
            router,
            "/api/control/bulk/preview/{action}",
        )


        result = await endpoint(
            "pause",
            FakeRequest({
                "miner_ids":
                    [
                        2,
                        1,
                    ],
            }),
        )


        self.assertTrue(
            result["success"]
        )

        self.assertEqual(
            [
                item["miner_id"]
                for item
                in result["targets"]
            ],
            [
                1,
                2,
            ],
        )


    async def test_execute_route_preserves_partial_results(self):

        calls = []


        def queue(
            miner_id,
            action,
            manual=False,
        ):

            calls.append(
                (
                    miner_id,
                    action,
                    manual,
                )
            )

            return {
                "queued":
                    True,

                "already_active":
                    False,

                "job_id":
                    401,

                "status":
                    "QUEUED",

                "action":
                    action,

                "target_state":
                    "PAUSED",
            }


        router = self.make_router(
            queue_control=queue
        )


        endpoint = self.endpoint(
            router,
            "/api/control/bulk/{action}",
        )


        result = await endpoint(
            "pause",
            FakeRequest({
                "miner_ids":
                    [
                        1,
                        2,
                    ],
            }),
        )


        self.assertEqual(
            calls,
            [
                (
                    1,
                    "pause",
                    True,
                ),
            ],
        )

        self.assertEqual(
            result["accepted_count"],
            1,
        )

        self.assertEqual(
            result["rejected_count"],
            1,
        )


    async def test_route_maps_validation_to_400(self):

        router = self.make_router()

        endpoint = self.endpoint(
            router,
            "/api/control/bulk/preview/{action}",
        )


        with self.assertRaises(
            HTTPException
        ) as ctx:

            await endpoint(
                "pause",
                FakeRequest({
                    "miner_ids":
                        [],
                }),
            )


        self.assertEqual(
            ctx.exception.status_code,
            400,
        )


class BulkControlSemanticIntegrationTests(
    BulkControlDatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def test_bulk_control_uses_existing_verified_job_pipeline(
        self,
    ):

        notifications = []


        audit_runtime = AuditRuntime(
            lambda **kwargs:
                notifications.append(
                    kwargs
                )
        )


        next_transition_epoch = (
            2_000_000_000
        )


        queue_runtime = QueueRuntime(
            audit_source=(
                audit_source
            ),

            log_event=(
                audit_runtime.log_event
            ),

            next_transition=(
                lambda:
                    datetime.fromtimestamp(
                        next_transition_epoch,
                        timezone.utc,
                    )
            ),

            # Workers are not actually started in this
            # test because executor.submit() is patched.
            control_runtime=object(),
        )


        router = create_control_router(
            queue_runtime.queue_control,
            queue_runtime.queue_reboot,
            audit_runtime.log_event,
        )


        preview = self.endpoint(
            router,
            "/api/control/bulk/preview/{action}",
        )


        execute = self.endpoint(
            router,
            "/api/control/bulk/{action}",
        )


        actor = (
            "WEB:semantic-operator"
        )


        token = bind_audit_actor(
            actor
        )


        try:

            with patch(
                "control.queue."
                "control_executor.submit"
            ) as submit:


                # --------------------------------------------
                # SELECTED ASIC MODE
                #
                # ASIC-1 = eligible
                # ASIC-2 = disabled
                # ASIC-3 = unsupported
                # ASIC-4 = eligible
                # --------------------------------------------

                preview_result = await preview(
                    "pause",
                    FakeRequest({
                        "miner_ids":
                            [
                                4,
                                3,
                                2,
                                1,
                            ],
                    }),
                )


                self.assertEqual(
                    preview_result[
                        "requested_count"
                    ],
                    4,
                )

                self.assertEqual(
                    preview_result[
                        "eligible_count"
                    ],
                    2,
                )

                self.assertEqual(
                    preview_result[
                        "rejected_count"
                    ],
                    2,
                )


                preview_by_id = {
                    item["miner_id"]:
                        item
                    for item
                    in preview_result[
                        "targets"
                    ]
                }


                self.assertTrue(
                    preview_by_id[
                        1
                    ][
                        "eligible"
                    ]
                )

                self.assertEqual(
                    preview_by_id[
                        2
                    ][
                        "rejection_code"
                    ],
                    "DISABLED",
                )

                self.assertEqual(
                    preview_by_id[
                        3
                    ][
                        "rejection_code"
                    ],
                    "UNSUPPORTED_DRIVER",
                )

                self.assertTrue(
                    preview_by_id[
                        4
                    ][
                        "eligible"
                    ]
                )


                pause_result = await execute(
                    "pause",
                    FakeRequest({
                        "miner_ids":
                            [
                                4,
                                3,
                                2,
                                1,
                            ],
                    }),
                )


                self.assertEqual(
                    pause_result[
                        "accepted_count"
                    ],
                    2,
                )

                self.assertEqual(
                    pause_result[
                        "rejected_count"
                    ],
                    2,
                )


                # Existing worker submission path is used
                # exactly once for every accepted ASIC.
                self.assertEqual(
                    submit.call_count,
                    2,
                )


                conn = db()

                try:

                    pause_jobs = list(
                        conn.execute("""
                            SELECT
                                id,
                                miner_id,
                                source,
                                action,
                                target_state,
                                status,
                                max_attempts

                            FROM control_jobs

                            WHERE action='pause'

                            ORDER BY miner_id
                        """).fetchall()
                    )


                    miners = {
                        int(row["id"]):
                            row
                        for row
                        in conn.execute("""
                            SELECT
                                id,
                                manual_override_until

                            FROM miners

                            WHERE id IN (
                                1,
                                2,
                                3,
                                4
                            )
                        """).fetchall()
                    }


                    audit_rows = list(
                        conn.execute("""
                            SELECT
                                source,
                                action,
                                miner_id,
                                success

                            FROM action_log

                            ORDER BY id
                        """).fetchall()
                    )


                    table_names = {
                        row["name"]
                        for row
                        in conn.execute("""
                            SELECT name
                            FROM sqlite_master
                            WHERE type='table'
                        """).fetchall()
                    }


                finally:
                    conn.close()


                # Exactly one normal per-miner control job
                # exists for each accepted target.
                self.assertEqual(
                    len(
                        pause_jobs
                    ),
                    2,
                )

                self.assertEqual(
                    {
                        int(
                            row["miner_id"]
                        )
                        for row
                        in pause_jobs
                    },
                    {
                        1,
                        4,
                    },
                )


                self.assertEqual(
                    len({
                        int(
                            row["id"]
                        )
                        for row
                        in pause_jobs
                    }),
                    2,
                )


                for row in pause_jobs:

                    self.assertEqual(
                        row["source"],
                        actor,
                    )

                    self.assertEqual(
                        row["action"],
                        "pause",
                    )

                    self.assertEqual(
                        row["target_state"],
                        "PAUSED",
                    )

                    self.assertEqual(
                        row["status"],
                        "QUEUED",
                    )


                # Manual PAUSE uses the normal existing
                # scheduler override mechanism.
                self.assertEqual(
                    miners[
                        1
                    ][
                        "manual_override_until"
                    ],
                    next_transition_epoch,
                )

                self.assertEqual(
                    miners[
                        4
                    ][
                        "manual_override_until"
                    ],
                    next_transition_epoch,
                )


                # Rejected miners were not mutated.
                self.assertIsNone(
                    miners[
                        2
                    ][
                        "manual_override_until"
                    ]
                )

                self.assertIsNone(
                    miners[
                        3
                    ][
                        "manual_override_until"
                    ]
                )


                # Audit source preserves the request-scoped
                # operator identity for normal queue events
                # and bulk rejections.
                relevant_actions = {
                    (
                        row["source"],
                        row["action"],
                        row["miner_id"],
                    )
                    for row
                    in audit_rows
                }


                self.assertIn(
                    (
                        actor,
                        "PAUSE_QUEUED",
                        1,
                    ),
                    relevant_actions,
                )

                self.assertIn(
                    (
                        actor,
                        "PAUSE_QUEUED",
                        4,
                    ),
                    relevant_actions,
                )

                self.assertIn(
                    (
                        actor,
                        "BULK_PAUSE_REJECTED",
                        2,
                    ),
                    relevant_actions,
                )

                self.assertIn(
                    (
                        actor,
                        "BULK_PAUSE_REJECTED",
                        3,
                    ),
                    relevant_actions,
                )


                # There is deliberately no bulk execution
                # persistence / second state machine.
                self.assertFalse(
                    any(
                        "bulk"
                        in name.lower()
                        for name
                        in table_names
                    )
                )


                # --------------------------------------------
                # Complete PAUSE jobs in the database so that
                # the next command can enter the same normal
                # queue.
                # --------------------------------------------

                conn = db()

                conn.execute("""
                    UPDATE control_jobs

                    SET
                        status='VERIFIED',
                        completed_at=123456

                    WHERE action='pause'
                """)

                override_before_reboot = (
                    conn.execute("""
                        SELECT manual_override_until
                        FROM miners
                        WHERE id=4
                    """).fetchone()[
                        "manual_override_until"
                    ]
                )

                conn.commit()
                conn.close()


                # --------------------------------------------
                # GROUP MODE + REBOOT
                #
                # Rack B currently contains ASIC-4.
                # This proves GROUP execution uses the same
                # per-miner reboot queue.
                # --------------------------------------------

                reboot_preview = await preview(
                    "reboot",
                    FakeRequest({
                        "group_id":
                            self.group_b["id"],
                    }),
                )


                self.assertEqual(
                    reboot_preview[
                        "selection"
                    ][
                        "type"
                    ],
                    "GROUP",
                )

                self.assertEqual(
                    reboot_preview[
                        "selection"
                    ][
                        "group_id"
                    ],
                    self.group_b[
                        "id"
                    ],
                )

                self.assertEqual(
                    reboot_preview[
                        "eligible_count"
                    ],
                    1,
                )


                reboot_result = await execute(
                    "reboot",
                    FakeRequest({
                        "group_id":
                            self.group_b["id"],

                        "confirm_reboot":
                            True,
                    }),
                )


                self.assertEqual(
                    reboot_result[
                        "accepted_count"
                    ],
                    1,
                )

                self.assertEqual(
                    reboot_result[
                        "rejected_count"
                    ],
                    0,
                )


                # Two PAUSE worker submissions plus one
                # REBOOT worker submission.
                self.assertEqual(
                    submit.call_count,
                    3,
                )


                conn = db()

                try:

                    reboot_job = conn.execute("""
                        SELECT
                            id,
                            miner_id,
                            source,
                            action,
                            target_state,
                            status,
                            max_attempts

                        FROM control_jobs

                        WHERE action='reboot'

                        ORDER BY id DESC

                        LIMIT 1
                    """).fetchone()


                    override_after_reboot = (
                        conn.execute("""
                            SELECT manual_override_until
                            FROM miners
                            WHERE id=4
                        """).fetchone()[
                            "manual_override_until"
                        ]
                    )


                    all_jobs = list(
                        conn.execute("""
                            SELECT
                                id,
                                miner_id,
                                action

                            FROM control_jobs

                            ORDER BY id
                        """).fetchall()
                    )


                    final_audit = list(
                        conn.execute("""
                            SELECT
                                source,
                                action,
                                miner_id

                            FROM action_log

                            ORDER BY id
                        """).fetchall()
                    )


                finally:
                    conn.close()


                self.assertIsNotNone(
                    reboot_job
                )

                self.assertEqual(
                    reboot_job[
                        "miner_id"
                    ],
                    4,
                )

                self.assertEqual(
                    reboot_job[
                        "source"
                    ],
                    actor,
                )

                self.assertEqual(
                    reboot_job[
                        "action"
                    ],
                    "reboot",
                )

                self.assertEqual(
                    reboot_job[
                        "target_state"
                    ],
                    "REBOOTED",
                )

                self.assertEqual(
                    reboot_job[
                        "status"
                    ],
                    "QUEUED",
                )

                self.assertEqual(
                    reboot_job[
                        "max_attempts"
                    ],
                    1,
                )


                # Manual reboot deliberately does not create
                # or alter the schedule override.
                self.assertEqual(
                    override_after_reboot,
                    override_before_reboot,
                )


                # 2 accepted PAUSE targets + 1 accepted
                # REBOOT target = exactly 3 ordinary jobs.
                self.assertEqual(
                    len(
                        all_jobs
                    ),
                    3,
                )


                self.assertEqual(
                    [
                        row["action"]
                        for row
                        in all_jobs
                    ],
                    [
                        "pause",
                        "pause",
                        "reboot",
                    ],
                )


                self.assertIn(
                    (
                        actor,
                        "REBOOT_QUEUED",
                        4,
                    ),
                    {
                        (
                            row["source"],
                            row["action"],
                            row["miner_id"],
                        )
                        for row
                        in final_audit
                    },
                )


        finally:

            reset_audit_actor(
                token
            )


        # Notification side also receives normalized actor
        # attribution from AuditRuntime.
        self.assertTrue(
            notifications
        )

        self.assertTrue(
            all(
                item["source"]
                == actor
                for item
                in notifications
            )
        )


if __name__ == "__main__":
    unittest.main()
