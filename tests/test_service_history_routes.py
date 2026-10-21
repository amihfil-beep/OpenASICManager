import asyncio
import json
import os
import tempfile
import unittest

from fastapi import HTTPException
from starlette.requests import Request

import config as app_config
import db as db_module

from api.service_history import (
    create_service_history_router,
)
from db import init_db


class ServiceHistoryRoutesTests(
    unittest.TestCase
):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix=(
                "openasicmanager-"
                "service-history-api-"
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

        conn = db_module.db()

        self.miner_id = conn.execute(
            """
            INSERT INTO miners
            (
                name,
                ip,
                driver
            )
            VALUES (
                'T21-API',
                '192.168.20.21',
                'bitmain_stock'
            )
            """
        ).lastrowid

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


    def log_event(
        self,
        **kwargs,
    ):
        self.events.append(
            kwargs
        )


    def router(self):
        return (
            create_service_history_router(
                self.log_event
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
            in self.router().routes
            if (
                route.path == path
                and method
                in route.methods
            )
        )


    def request(
        self,
        payload,
    ):
        body = json.dumps(
            payload
        ).encode(
            "utf-8"
        )

        sent = False

        async def receive():
            nonlocal sent

            if sent:
                return {
                    "type":
                        "http.disconnect",
                }

            sent = True

            return {
                "type":
                    "http.request",
                "body": body,
                "more_body": False,
            }

        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": (
                "/api/miners/"
                f"{self.miner_id}/"
                "service-history"
            ),
            "raw_path": b"",
            "query_string": b"",
            "headers": [
                (
                    b"content-type",
                    b"application/json",
                ),
            ],
            "client": (
                "test",
                12345,
            ),
            "server": (
                "test",
                80,
            ),
        }

        return Request(
            scope,
            receive,
        )


    def control_job_count(self):
        conn = db_module.db()

        try:
            return conn.execute(
                """
                SELECT COUNT(*)
                FROM control_jobs
                """
            ).fetchone()[0]

        finally:
            conn.close()


    def test_create_list_audit_and_no_control_jobs(
        self,
    ):
        create_endpoint = (
            self.endpoint(
                (
                    "/api/miners/"
                    "{miner_id}/"
                    "service-history"
                ),
                "POST",
            )
        )

        before = (
            self.control_job_count()
        )

        result = asyncio.run(
            create_endpoint(
                self.miner_id,
                self.request(
                    {
                        "performed_at":
                            1000,
                        "work_type":
                            "fan_replacement",
                        "description":
                            "Rear fan replaced",
                        "part_serial":
                            "FAN-001",
                        "performed_by":
                            "Technician A",
                    }
                ),
            )
        )

        after = (
            self.control_job_count()
        )

        self.assertTrue(
            result["success"]
        )

        record = result[
            "record"
        ]

        self.assertEqual(
            record["miner_id"],
            self.miner_id,
        )

        self.assertEqual(
            record["work_type"],
            "FAN_REPLACEMENT",
        )

        self.assertEqual(
            record["part_serial"],
            "FAN-001",
        )

        self.assertEqual(
            record["created_by"],
            "LOCAL",
        )

        self.assertEqual(
            before,
            0,
        )

        self.assertEqual(
            after,
            0,
        )

        self.assertEqual(
            len(self.events),
            1,
        )

        self.assertEqual(
            self.events[0][
                "action"
            ],
            "SERVICE_RECORD_CREATE",
        )

        self.assertEqual(
            self.events[0][
                "miner"
            ]["id"],
            self.miner_id,
        )


        list_endpoint = (
            self.endpoint(
                (
                    "/api/miners/"
                    "{miner_id}/"
                    "service-history"
                ),
                "GET",
            )
        )

        listed = list_endpoint(
            self.miner_id,
            100,
        )

        self.assertEqual(
            listed["miner"]["id"],
            self.miner_id,
        )

        self.assertIn(
            "FAN_REPLACEMENT",
            listed["work_types"],
        )

        self.assertEqual(
            len(
                listed["records"]
            ),
            1,
        )

        self.assertEqual(
            listed["records"][0][
                "id"
            ],
            record["id"],
        )


    def test_validation_and_missing_miner(
        self,
    ):
        create_endpoint = (
            self.endpoint(
                (
                    "/api/miners/"
                    "{miner_id}/"
                    "service-history"
                ),
                "POST",
            )
        )


        with self.assertRaises(
            HTTPException
        ) as invalid:
            asyncio.run(
                create_endpoint(
                    self.miner_id,
                    self.request(
                        {
                            "performed_at":
                                1000,
                            "work_type":
                                "UNSUPPORTED",
                            "description":
                                "Bad",
                            "performed_by":
                                "TEST",
                        }
                    ),
                )
            )

        self.assertEqual(
            invalid.exception.status_code,
            400,
        )


        with self.assertRaises(
            HTTPException
        ) as other:
            asyncio.run(
                create_endpoint(
                    self.miner_id,
                    self.request(
                        {
                            "performed_at":
                                1000,
                            "work_type":
                                "OTHER",
                            "description":
                                "",
                            "performed_by":
                                "TEST",
                        }
                    ),
                )
            )

        self.assertEqual(
            other.exception.status_code,
            400,
        )


        missing_request = (
            self.request(
                {
                    "performed_at":
                        1000,
                    "work_type":
                        "CLEANING",
                    "description":
                        "Clean",
                    "performed_by":
                        "TEST",
                }
            )
        )


        with self.assertRaises(
            HTTPException
        ) as missing:
            asyncio.run(
                create_endpoint(
                    999999,
                    missing_request,
                )
            )

        self.assertEqual(
            missing.exception.status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
