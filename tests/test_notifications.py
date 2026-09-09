import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import config as app_config
from db import db, init_db
from notifications import telegram


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "ok": True,
            "result": {
                "message_id": 123,
            },
        }


class NotificationFormattingTests(unittest.TestCase):
    def test_duration_and_metrics_formatting(self):
        self.assertEqual(
            telegram.telegram_format_duration(3661),
            "1h 1m 1s",
        )
        self.assertEqual(
            telegram.telegram_format_hashrate(173.25),
            "173.2 TH/s",
        )
        self.assertEqual(
            telegram.telegram_format_temperature(71),
            "71°C",
        )
        self.assertEqual(
            telegram.telegram_format_power(3500),
            "3.50 kW",
        )

    def test_control_message_parser(self):
        parsed = telegram.telegram_parse_control_message(
            "Expected=MINING actual=OFFLINE attempts=2/3"
        )
        self.assertEqual(parsed["expected"], "MINING")
        self.assertEqual(parsed["actual"], "OFFLINE")
        self.assertEqual(parsed["attempts"], "2/3")

    def test_summary_due_window(self):
        due = datetime(
            2026,
            9,
            7,
            telegram.TELEGRAM_SUMMARY_HOUR,
            telegram.TELEGRAM_SUMMARY_MINUTE,
            tzinfo=telegram.MOSCOW,
        )
        self.assertTrue(
            telegram.telegram_summary_due(due)
        )

        outside = due.replace(
            minute=(telegram.TELEGRAM_SUMMARY_MINUTE + 31) % 60,
            hour=(
                telegram.TELEGRAM_SUMMARY_HOUR
                + (
                    (telegram.TELEGRAM_SUMMARY_MINUTE + 31)
                    // 60
                )
            ) % 24,
        )
        self.assertFalse(
            telegram.telegram_summary_due(outside)
        )


class TelegramTransportTests(unittest.TestCase):
    def test_send_respects_disabled_notifications_and_force(self):
        original = (
            telegram.TELEGRAM_BOT_TOKEN,
            telegram.TELEGRAM_CHAT_ID,
            telegram.TELEGRAM_NOTIFICATIONS_ENABLED,
        )

        telegram.TELEGRAM_BOT_TOKEN = "test-token"
        telegram.TELEGRAM_CHAT_ID = "12345"
        telegram.TELEGRAM_NOTIFICATIONS_ENABLED = False

        try:
            with patch.object(
                telegram.requests,
                "post",
            ) as post:
                sent, message = telegram.telegram_send_message(
                    "test"
                )
                self.assertFalse(sent)
                self.assertIn("disabled", message.lower())
                post.assert_not_called()

            with patch.object(
                telegram.requests,
                "post",
                return_value=FakeResponse(),
            ) as post:
                sent, message = telegram.telegram_send_message(
                    "test",
                    force=True,
                )
                self.assertTrue(sent)
                self.assertEqual(message, "message_id=123")
                post.assert_called_once()
        finally:
            (
                telegram.TELEGRAM_BOT_TOKEN,
                telegram.TELEGRAM_CHAT_ID,
                telegram.TELEGRAM_NOTIFICATIONS_ENABLED,
            ) = original


class NotificationRepositoryTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(
            prefix="openasicmanager-notifications-",
            suffix=".db",
        )
        os.close(fd)
        os.unlink(path)
        self.path = path
        self.original_db = app_config.DATABASE_PATH
        app_config.DATABASE_PATH = path
        init_db()

        conn = db()
        cursor = conn.execute("""
            INSERT INTO miners
            (
                name,
                ip,
                driver,
                enabled,
                last_state,
                hashrate,
                avg_hashrate,
                temp,
                power
            )
            VALUES
            (
                'TEST-ASIC',
                '192.0.2.80',
                'bitmain_stock',
                1,
                'MINING',
                173,
                171,
                71,
                3500
            )
        """)
        self.miner_id = cursor.lastrowid

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
                ?,
                '192.0.2.80',
                'TEST-ASIC',
                'OVERHEAT',
                'CRITICAL',
                'ACTIVE',
                100,
                110,
                'Temperature 90 C'
            )
        """, (self.miner_id,))

        conn.commit()
        conn.close()

    def tearDown(self):
        app_config.DATABASE_PATH = self.original_db
        for suffix in ("", "-shm", "-wal"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_farm_summary_data_and_event_formatting(self):
        data = telegram.telegram_farm_summary_data()
        self.assertEqual(data["enabled_count"], 1)
        self.assertEqual(data["states"]["MINING"], 1)
        self.assertEqual(data["total_hashrate"], 173.0)
        self.assertEqual(data["known_power_count"], 1)
        self.assertEqual(len(data["issues"]), 1)

        conn = db()
        miner = conn.execute(
            "SELECT * FROM miners WHERE id=?",
            (self.miner_id,),
        ).fetchone()
        conn.close()

        message = telegram.telegram_format_event(
            source="SYSTEM",
            action="ISSUE_OPEN",
            miner=miner,
            success=False,
            message="CRITICAL OVERHEAT: Temperature 90 C",
        )

        self.assertIn("ASIC OVERHEAT", message)
        self.assertIn("TEST-ASIC", message)
        self.assertIn("Problem: OVERHEAT", message)


if __name__ == "__main__":
    unittest.main()
