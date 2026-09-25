import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.scheduler import create_scheduler_router
from scheduler.validation import (
    schedule_normalize_input,
)


class SchedulerRoutesTests(unittest.TestCase):
    def endpoint(self, path, method, log_event=None):
        router = create_scheduler_router(
            log_event or (lambda **kwargs: None)
        )
        return next(
            route.endpoint
            for route in router.routes
            if route.path == path
            and method in route.methods
        )

    def test_router_exposes_scheduler_paths(self):
        router = create_scheduler_router(
            lambda **kwargs: None
        )
        routes = {
            (route.path, method)
            for route in router.routes
            for method in route.methods
        }
        expected = {
            ("/api/schedule/rules", "GET"),
            ("/api/schedule/preview", "POST"),
            ("/api/schedule/rules", "POST"),
            ("/api/schedule/rules/{rule_id}", "PUT"),
            ("/api/schedule/rules/{rule_id}/toggle", "POST"),
            ("/api/schedule/rules/{rule_id}", "DELETE"),
            ("/api/scheduler/toggle", "POST"),
        }
        self.assertTrue(expected.issubset(routes))

    def test_scope_validation_matrix(self):
        cases = (
            (
                {
                    "scope":
                        "FARM",
                },
                "FARM",
                None,
                None,
            ),
            (
                {
                    "scope":
                        "GROUP",
                    "group_id":
                        7,
                },
                "GROUP",
                7,
                None,
            ),
            (
                {
                    "scope":
                        "GROUP",
                },
                None,
                None,
                "positive group_id",
            ),
            (
                {
                    "scope":
                        "FARM",
                    "group_id":
                        7,
                },
                None,
                None,
                "must not include group_id",
            ),
            (
                {
                    "scope":
                        "UNKNOWN",
                },
                None,
                None,
                "FARM or GROUP",
            ),
        )

        for (
            payload,
            expected_scope,
            expected_group_id,
            expected_error,
        ) in cases:

            with self.subTest(
                payload=payload
            ):

                if expected_error:

                    with self.assertRaisesRegex(
                        HTTPException,
                        expected_error,
                    ):
                        schedule_normalize_input(
                            payload
                        )

                    continue

                normalized = (
                    schedule_normalize_input(
                        payload
                    )
                )

                self.assertEqual(
                    normalized["scope"],
                    expected_scope,
                )

                self.assertEqual(
                    normalized["group_id"],
                    expected_group_id,
                )


    def test_preview_reports_dynamic_group_targets_and_conflict(
        self,
    ):

        endpoint = self.endpoint(
            "/api/schedule/preview",
            "POST",
        )


        class JsonRequest:

            async def json(self):
                return {
                    "action":
                        "PAUSE",

                    "time":
                        "21:30",

                    "days_mask":
                        127,

                    "scope":
                        "GROUP",

                    "group_id":
                        7,

                    "enabled":
                        True,

                    "comment":
                        "Night pause",
                }


        group = {
            "id":
                7,

            "name":
                "Rack A",

            "member_count":
                3,
        }


        miners = [
            {
                "id":
                    1,

                "group_id":
                    7,
            },
            {
                "id":
                    2,

                "group_id":
                    7,
            },
            {
                "id":
                    3,

                "group_id":
                    8,
            },
        ]


        conflict = {
            "id":
                91,

            "action":
                "RESUME",

            "time_minutes":
                21 * 60 + 30,

            "days_mask":
                31,

            "scope":
                "GROUP",

            "group_id":
                7,
        }


        with patch(
            "api.scheduler.get_group",
            return_value=group,
        ), patch(
            "api.scheduler.list_schedulable_miners",
            return_value=miners,
        ), patch(
            "api.scheduler.schedule_conflicting_rule",
            return_value=conflict,
        ):

            result = asyncio.run(
                endpoint(
                    JsonRequest()
                )
            )


        self.assertEqual(
            result[
                "scope"
            ],
            "GROUP",
        )

        self.assertEqual(
            result[
                "target"
            ],
            "Rack A",
        )

        self.assertTrue(
            result[
                "dynamic_membership"
            ]
        )

        self.assertEqual(
            result[
                "affected_miner_count"
            ],
            2,
        )

        self.assertEqual(
            result[
                "group_member_count"
            ],
            3,
        )

        self.assertFalse(
            result[
                "allowed"
            ]
        )

        self.assertEqual(
            result[
                "conflict"
            ][
                "id"
            ],
            91,
        )

        self.assertEqual(
            result[
                "conflict"
            ][
                "time"
            ],
            "21:30",
        )


    @patch("api.scheduler.set_setting")
    @patch("api.scheduler.get_setting", return_value="0")
    def test_scheduler_toggle_enables_scheduler(
        self,
        get_setting,
        set_setting,
    ):
        events = []
        endpoint = self.endpoint(
            "/api/scheduler/toggle",
            "POST",
            events.append,
        )

        def log_event(**kwargs):
            events.append(kwargs)

        endpoint = self.endpoint(
            "/api/scheduler/toggle",
            "POST",
            log_event,
        )
        result = endpoint()

        self.assertTrue(result["scheduler_enabled"])
        get_setting.assert_called_once_with(
            "scheduler_enabled",
            "0",
        )
        set_setting.assert_called_once_with(
            "scheduler_enabled",
            "1",
        )
        self.assertEqual(events[0]["action"], "SCHEDULER_ON")
        self.assertTrue(events[0]["success"])


if __name__ == "__main__":
    unittest.main()
