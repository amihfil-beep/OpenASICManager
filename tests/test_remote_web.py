import ipaddress
import os
import tempfile
import unittest
from unittest.mock import patch

import config as app_config
from db import db, init_db
import remote_web


class FakeResponse:
    def __init__(self):
        self.deleted = None

    def delete_cookie(self, **kwargs):
        self.deleted = kwargs


class RemoteWebTokenTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "secret": remote_web.REMOTE_WEB_SECRET,
            "ttl": remote_web.REMOTE_WEB_TTL,
            "cookie_domain": remote_web.REMOTE_WEB_COOKIE_DOMAIN,
            "base_domain": remote_web.REMOTE_WEB_BASE_DOMAIN,
            "network": remote_web.REMOTE_WEB_NETWORK,
            "enabled": app_config.REMOTE_WEB_ENABLED,
            "public_domain": app_config.PUBLIC_DOMAIN,
        }

        remote_web.REMOTE_WEB_SECRET = b"unit-test-secret"
        remote_web.REMOTE_WEB_TTL = 60

    def tearDown(self):
        remote_web.REMOTE_WEB_SECRET = self.original["secret"]
        remote_web.REMOTE_WEB_TTL = self.original["ttl"]
        remote_web.REMOTE_WEB_COOKIE_DOMAIN = self.original["cookie_domain"]
        remote_web.REMOTE_WEB_BASE_DOMAIN = self.original["base_domain"]
        remote_web.REMOTE_WEB_NETWORK = self.original["network"]
        app_config.REMOTE_WEB_ENABLED = self.original["enabled"]
        app_config.PUBLIC_DOMAIN = self.original["public_domain"]

    def test_token_round_trip_and_tamper_rejection(self):
        with patch.object(
            remote_web.time,
            "time",
            return_value=1000,
        ):
            token = remote_web.remote_web_make_token(
                "WEB:alice"
            )

        with patch.object(
            remote_web.time,
            "time",
            return_value=1010,
        ):
            payload = remote_web.remote_web_verify_token(
                token
            )

        self.assertEqual(payload["actor"], "WEB:alice")
        self.assertEqual(payload["exp"], 1060)

        replacement = "A" if token[-1] != "A" else "B"
        tampered = token[:-1] + replacement

        with patch.object(
            remote_web.time,
            "time",
            return_value=1010,
        ):
            self.assertIsNone(
                remote_web.remote_web_verify_token(
                    tampered
                )
            )

    def test_expired_and_non_web_actor_tokens_are_rejected(self):
        with patch.object(
            remote_web.time,
            "time",
            return_value=1000,
        ):
            web_token = remote_web.remote_web_make_token(
                "WEB:alice"
            )
            local_token = remote_web.remote_web_make_token(
                "LOCAL"
            )

        with patch.object(
            remote_web.time,
            "time",
            return_value=1061,
        ):
            self.assertIsNone(
                remote_web.remote_web_verify_token(
                    web_token
                )
            )

        with patch.object(
            remote_web.time,
            "time",
            return_value=1001,
        ):
            self.assertIsNone(
                remote_web.remote_web_verify_token(
                    local_token
                )
            )

    def test_cookie_scope_and_clear_cookie(self):
        remote_web.REMOTE_WEB_COOKIE_DOMAIN = ".example.com"
        app_config.PUBLIC_DOMAIN = "manager.example.com"

        self.assertTrue(
            remote_web.remote_web_cookie_scope_valid()
        )

        app_config.PUBLIC_DOMAIN = "manager.invalid"
        self.assertFalse(
            remote_web.remote_web_cookie_scope_valid()
        )

        response = FakeResponse()
        result = remote_web.remote_web_clear_cookie(
            response
        )

        self.assertIs(result, response)
        self.assertEqual(
            response.deleted["key"],
            remote_web.REMOTE_WEB_COOKIE_NAME,
        )
        self.assertTrue(response.deleted["secure"])
        self.assertTrue(response.deleted["httponly"])

    def test_remote_host_policy(self):
        app_config.REMOTE_WEB_ENABLED = True
        remote_web.REMOTE_WEB_BASE_DOMAIN = "remote.example.com"
        remote_web.REMOTE_WEB_NETWORK = ipaddress.ip_network(
            "192.0.2.0/24"
        )

        self.assertEqual(
            remote_web.remote_web_host_for_ip(
                "192.0.2.5"
            ),
            "m192-0-2-5.remote.example.com",
        )

        self.assertIsNone(
            remote_web.remote_web_host_for_ip(
                "198.51.100.5"
            )
        )


class RemoteWebRepositoryTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-remote-web-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        self.original_enabled = app_config.REMOTE_WEB_ENABLED
        self.original_domain = remote_web.REMOTE_WEB_BASE_DOMAIN
        self.original_network = remote_web.REMOTE_WEB_NETWORK

        app_config.DATABASE_PATH = path
        app_config.REMOTE_WEB_ENABLED = True
        remote_web.REMOTE_WEB_BASE_DOMAIN = "remote.example.com"
        remote_web.REMOTE_WEB_NETWORK = ipaddress.ip_network(
            "192.0.2.0/24"
        )

        init_db()

        conn = db()
        cursor = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                enabled
            )
            VALUES
            (
                'TEST-ASIC',
                '192.0.2.8',
                'bitmain_stock',
                1
            )
        """)
        self.miner_id = cursor.lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        app_config.REMOTE_WEB_ENABLED = self.original_enabled
        remote_web.REMOTE_WEB_BASE_DOMAIN = self.original_domain
        remote_web.REMOTE_WEB_NETWORK = self.original_network

        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_miner_lookup_by_remote_host(self):
        miner = remote_web.remote_web_miner_for_host(
            "m192-0-2-8.remote.example.com"
        )

        self.assertIsNotNone(miner)
        self.assertEqual(miner["id"], self.miner_id)
        self.assertEqual(miner["name"], "TEST-ASIC")

        self.assertIsNone(
            remote_web.remote_web_miner_for_host(
                "m192-0-2-9.remote.example.com"
            )
        )


if __name__ == "__main__":
    unittest.main()
