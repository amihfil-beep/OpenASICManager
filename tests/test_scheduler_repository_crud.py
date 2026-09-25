import os
import tempfile
import unittest

from datetime import datetime

import config as app_config
import db as db_module
from db import init_db
from scheduler.policy import MOSCOW
from scheduler.service import (
    scheduler_iteration,
)
from scheduler.repository import (
    create_schedule_rule,
    delete_schedule_rule,
    effective_schedule_states,
    get_schedule_rule,
    list_schedule_rules,
    next_transition_for_miner,
    set_schedule_rule_enabled,
    update_schedule_rule,
)


class SchedulerRepositoryCrudTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-scheduler-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        db_module.SCHEDULE_RULES_SCHEMA_READY = False
        init_db()

        self.normalized = {
            "enabled": True,
            "action": "PAUSE",
            "time_minutes": 7 * 60,
            "days_mask": 0b00011111,
            "scope": "FARM",
            "group_id": None,
            "comment": "Morning pause",
        }

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_create_get_and_list_rule(self):
        rule = create_schedule_rule(
            self.normalized,
            1000,
        )

        fetched = get_schedule_rule(
            rule["id"]
        )
        listed = list_schedule_rules()

        self.assertEqual(fetched["action"], "PAUSE")
        self.assertEqual(fetched["effective_from"], 1000)
        self.assertEqual(fetched["created_at"], 1000)
        self.assertEqual(fetched["updated_at"], 1000)
        self.assertEqual(
            [item["id"] for item in listed],
            [rule["id"]],
        )

    def test_update_rule_and_clear_last_run_key(self):
        rule = create_schedule_rule(
            self.normalized,
            1000,
        )
        changed = dict(self.normalized)
        changed.update({
            "enabled": False,
            "action": "RESUME",
            "time_minutes": 13 * 60,
            "comment": "Lunch resume",
        })

        updated = update_schedule_rule(
            rule["id"],
            changed,
            1200,
            1300,
        )

        self.assertFalse(bool(updated["enabled"]))
        self.assertEqual(updated["action"], "RESUME")
        self.assertEqual(updated["time_minutes"], 13 * 60)
        self.assertEqual(updated["effective_from"], 1200)
        self.assertEqual(updated["updated_at"], 1300)
        self.assertIsNone(updated["last_run_key"])

    def test_toggle_persistence(self):
        rule = create_schedule_rule(
            self.normalized,
            1000,
        )

        disabled = set_schedule_rule_enabled(
            rule["id"],
            False,
            1000,
            1400,
        )
        enabled = set_schedule_rule_enabled(
            rule["id"],
            True,
            1500,
            1500,
        )

        self.assertFalse(bool(disabled["enabled"]))
        self.assertTrue(bool(enabled["enabled"]))
        self.assertEqual(enabled["effective_from"], 1500)
        self.assertEqual(enabled["updated_at"], 1500)

    def test_legacy_scope_migrates_to_farm(self):
        conn = db_module.db()

        conn.execute("""
            CREATE TABLE schedule_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enabled INTEGER NOT NULL DEFAULT 1,
                action TEXT NOT NULL,
                time_minutes INTEGER NOT NULL,
                days_mask INTEGER NOT NULL,
                scope TEXT NOT NULL DEFAULT 'SCHEDULED',
                comment TEXT NOT NULL DEFAULT '',
                effective_from INTEGER NOT NULL DEFAULT 0,
                last_run_key TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)

        conn.execute("""
            INSERT INTO schedule_rules
            (
                enabled,
                action,
                time_minutes,
                days_mask,
                scope,
                comment,
                effective_from,
                created_at,
                updated_at
            )
            VALUES (
                1,
                'PAUSE',
                420,
                31,
                'SCHEDULED',
                'Legacy',
                100,
                100,
                100
            )
        """)

        conn.commit()
        conn.close()

        db_module.SCHEDULE_RULES_SCHEMA_READY = False
        db_module.ensure_schedule_rules_schema()

        conn = db_module.db()

        try:
            row = conn.execute("""
                SELECT
                    scope,
                    group_id

                FROM schedule_rules
            """).fetchone()
        finally:
            conn.close()

        self.assertEqual(
            row["scope"],
            "FARM",
        )
        self.assertIsNone(
            row["group_id"]
        )


    def test_group_rule_lifecycle_exposes_group_context(self):
        conn = db_module.db()

        cursor = conn.execute("""
            INSERT INTO miner_groups
            (
                name,
                normalized_name,
                created_by,
                created_at
            )
            VALUES (
                'Rack A',
                'rack a',
                'TEST',
                100
            )
        """)

        group_id = cursor.lastrowid
        conn.commit()
        conn.close()

        normalized = dict(
            self.normalized
        )

        normalized.update({
            "scope":
                "GROUP",
            "group_id":
                group_id,
        })

        created = create_schedule_rule(
            normalized,
            2000,
        )

        fetched = get_schedule_rule(
            created["id"]
        )

        self.assertEqual(
            fetched["scope"],
            "GROUP",
        )
        self.assertEqual(
            fetched["group_id"],
            group_id,
        )
        self.assertEqual(
            fetched["group_name"],
            "Rack A",
        )
        self.assertEqual(
            fetched[
                "group_member_count"
            ],
            0,
        )

        changed = dict(
            normalized
        )

        changed.update({
            "scope":
                "FARM",
            "group_id":
                None,
        })

        updated = update_schedule_rule(
            created["id"],
            changed,
            2100,
            2200,
        )

        self.assertEqual(
            updated["scope"],
            "FARM",
        )
        self.assertIsNone(
            updated["group_id"]
        )


    def test_effective_schedule_layering_and_dynamic_membership(self):
        conn = db_module.db()

        rack_a = conn.execute("""
            INSERT INTO miner_groups
            (
                name,
                normalized_name,
                created_by,
                created_at
            )
            VALUES (
                'Rack A',
                'rack a',
                'TEST',
                100
            )
        """).lastrowid

        rack_b = conn.execute("""
            INSERT INTO miner_groups
            (
                name,
                normalized_name,
                created_by,
                created_at
            )
            VALUES (
                'Rack B',
                'rack b',
                'TEST',
                100
            )
        """).lastrowid

        grouped_miner = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                group_id
            )
            VALUES (
                'Grouped',
                '192.168.1.10',
                'awesome',
                ?
            )
        """, (
            rack_a,
        )).lastrowid

        ungrouped_miner = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                group_id
            )
            VALUES (
                'Ungrouped',
                '192.168.1.11',
                'awesome',
                NULL
            )
        """).lastrowid

        future_group_miner = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                group_id
            )
            VALUES (
                'Future group',
                '192.168.1.12',
                'awesome',
                ?
            )
        """, (
            rack_b,
        )).lastrowid

        conn.commit()
        conn.close()


        def rule(
            action,
            time_minutes,
            scope,
            group_id=None,
            effective_from=1000,
        ):

            return create_schedule_rule(
                {
                    "enabled":
                        True,

                    "action":
                        action,

                    "time_minutes":
                        time_minutes,

                    "days_mask":
                        31,

                    "scope":
                        scope,

                    "group_id":
                        group_id,

                    "comment":
                        "",
                },
                effective_from,
            )


        # FARM baseline:
        #
        # 08:30 PAUSE is already active at 09:00.
        # 10:00 RESUME is the next FARM transition.
        farm_pause = rule(
            "PAUSE",
            8 * 60 + 30,
            "FARM",
        )

        rule(
            "RESUME",
            10 * 60,
            "FARM",
        )


        # Rack A has its own active layer.
        #
        # Even though FARM PAUSE at 08:30 is newer than
        # GROUP RESUME at 08:00, GROUP is more specific.
        group_resume = rule(
            "RESUME",
            8 * 60,
            "GROUP",
            rack_a,
        )

        rule(
            "PAUSE",
            18 * 60,
            "GROUP",
            rack_a,
        )


        # Rack B has no GROUP occurrence yet at 09:00.
        #
        # FARM is therefore still the current state, but
        # 09:30 GROUP RESUME becomes the next effective event.
        rule(
            "RESUME",
            9 * 60 + 30,
            "GROUP",
            rack_b,
            effective_from=int(
                datetime(
                    2026,
                    9,
                    21,
                    8,
                    45,
                    tzinfo=MOSCOW,
                ).timestamp()
            ),
        )


        now = datetime(
            2026,
            9,
            21,
            9,
            0,
            tzinfo=MOSCOW,
        )


        conn = db_module.db()

        miners = list(
            conn.execute("""
                SELECT
                    id,
                    group_id

                FROM miners

                WHERE id IN (?, ?, ?)

                ORDER BY id
            """, (
                grouped_miner,
                ungrouped_miner,
                future_group_miner,
            )).fetchall()
        )

        conn.close()


        states = effective_schedule_states(
            miners,
            now,
        )


        grouped = states[
            grouped_miner
        ]

        self.assertEqual(
            grouped["desired_state"],
            "MINING",
        )

        self.assertEqual(
            grouped["source_scope"],
            "GROUP",
        )

        self.assertEqual(
            grouped["rule"]["id"],
            group_resume["id"],
        )

        self.assertEqual(
            grouped[
                "next_transition"
            ].hour,
            18,
        )


        ungrouped = states[
            ungrouped_miner
        ]

        self.assertEqual(
            ungrouped["desired_state"],
            "PAUSED",
        )

        self.assertEqual(
            ungrouped["source_scope"],
            "FARM",
        )

        self.assertEqual(
            ungrouped["rule"]["id"],
            farm_pause["id"],
        )

        self.assertEqual(
            ungrouped[
                "next_transition"
            ].hour,
            10,
        )


        future_group = states[
            future_group_miner
        ]

        self.assertEqual(
            future_group[
                "desired_state"
            ],
            "PAUSED",
        )

        self.assertEqual(
            future_group[
                "source_scope"
            ],
            "FARM",
        )

        self.assertEqual(
            (
                future_group[
                    "next_transition"
                ].hour,
                future_group[
                    "next_transition"
                ].minute,
            ),
            (
                9,
                30,
            ),
        )


        # The queue-facing helper must use the exact same
        # effective schedule semantics.
        self.assertEqual(
            next_transition_for_miner(
                grouped_miner,
                now,
            ),
            grouped[
                "next_transition"
            ],
        )


        # Membership is dynamic, not snapshotted.
        conn = db_module.db()

        conn.execute("""
            UPDATE miners
            SET group_id=NULL
            WHERE id=?
        """, (
            grouped_miner,
        ))

        conn.commit()

        moved = conn.execute("""
            SELECT
                id,
                group_id

            FROM miners

            WHERE id=?
        """, (
            grouped_miner,
        )).fetchone()

        conn.close()


        moved_state = (
            effective_schedule_states(
                [
                    moved,
                ],
                now,
            )[
                grouped_miner
            ]
        )


        self.assertEqual(
            moved_state[
                "desired_state"
            ],
            "PAUSED",
        )

        self.assertEqual(
            moved_state[
                "source_scope"
            ],
            "FARM",
        )

        self.assertEqual(
            moved_state[
                "rule"
            ][
                "id"
            ],
            farm_pause["id"],
        )


    def test_scheduler_iteration_uses_effective_scope_per_miner(self):
        now = datetime(
            2026,
            9,
            21,
            9,
            0,
            tzinfo=MOSCOW,
        )

        now_epoch = int(
            now.timestamp()
        )


        conn = db_module.db()

        conn.execute("""
            UPDATE settings
            SET value='1'
            WHERE key='scheduler_enabled'
        """)


        rack_id = conn.execute("""
            INSERT INTO miner_groups
            (
                name,
                normalized_name,
                created_by,
                created_at
            )
            VALUES (
                'Rack A',
                'rack a',
                'TEST',
                100
            )
        """).lastrowid


        grouped_id = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                group_id,
                last_state,
                last_seen,
                last_action_at
            )
            VALUES (
                'Grouped',
                '192.168.2.10',
                'awesome',
                ?,
                'MINING',
                ?,
                0
            )
        """, (
            rack_id,
            now_epoch,
        )).lastrowid


        farm_id = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                group_id,
                last_state,
                last_seen,
                last_action_at
            )
            VALUES (
                'Farm only',
                '192.168.2.11',
                'awesome',
                NULL,
                'PAUSED',
                ?,
                0
            )
        """, (
            now_epoch,
        )).lastrowid


        conn.commit()
        conn.close()


        create_schedule_rule(
            {
                "enabled": True,
                "action": "RESUME",
                "time_minutes": 8 * 60,
                "days_mask": 31,
                "scope": "FARM",
                "group_id": None,
                "comment": "",
            },
            1000,
        )


        create_schedule_rule(
            {
                "enabled": True,
                "action": "PAUSE",
                "time_minutes": 8 * 60 + 30,
                "days_mask": 31,
                "scope": "GROUP",
                "group_id": rack_id,
                "comment": "",
            },
            1000,
        )


        class Runtime:

            def __init__(self):
                self.calls = []
                self.events = []

            def queue_control(
                self,
                miner_id,
                action,
                manual=False,
            ):
                self.calls.append(
                    (
                        int(miner_id),
                        action,
                        manual,
                    )
                )

            def log_event(
                self,
                **kwargs,
            ):
                self.events.append(
                    kwargs
                )


        runtime = Runtime()


        scheduler_iteration(
            runtime,
            now,
        )


        self.assertEqual(
            sorted(
                runtime.calls
            ),
            [
                (
                    grouped_id,
                    "pause",
                    False,
                ),
                (
                    farm_id,
                    "resume",
                    False,
                ),
            ],
        )


        active_rule_events = [
            event
            for event
            in runtime.events
            if (
                event.get(
                    "action"
                )
                ==
                "SCHEDULE_RULE_ACTIVE"
            )
        ]


        self.assertEqual(
            len(
                active_rule_events
            ),
            2,
        )


    def test_delete_returns_deleted_rule(self):
        rule = create_schedule_rule(
            self.normalized,
            1000,
        )

        deleted = delete_schedule_rule(
            rule["id"]
        )

        self.assertEqual(deleted["id"], rule["id"])
        self.assertIsNone(
            get_schedule_rule(rule["id"])
        )
        self.assertIsNone(
            delete_schedule_rule(rule["id"])
        )


if __name__ == "__main__":
    unittest.main()
