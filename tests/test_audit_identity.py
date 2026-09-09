import unittest

from audit.identity import (
    audit_actor_from_remote_user,
    audit_source,
    bind_audit_actor,
    current_audit_actor,
    reset_audit_actor,
    sanitize_audit_username,
)


class AuditIdentityTests(unittest.TestCase):
    def test_default_actor_is_local(self):
        self.assertEqual(
            current_audit_actor(),
            "LOCAL",
        )

    def test_username_sanitization(self):
        self.assertEqual(
            sanitize_audit_username(
                " alice+ops@example.com "
            ),
            "aliceops@example.com",
        )
        self.assertIsNone(
            sanitize_audit_username("   ")
        )
        self.assertEqual(
            len(sanitize_audit_username("x" * 100)),
            64,
        )

    def test_remote_user_actor_mapping(self):
        self.assertEqual(
            audit_actor_from_remote_user("alice"),
            "WEB:alice",
        )
        self.assertEqual(
            audit_actor_from_remote_user(""),
            "LOCAL",
        )

    def test_request_context_attributes_manual_and_system_sources(self):
        token = bind_audit_actor("WEB:alice")
        try:
            self.assertEqual(
                current_audit_actor(),
                "WEB:alice",
            )
            self.assertEqual(
                audit_source("MANUAL"),
                "WEB:alice",
            )
            self.assertEqual(
                audit_source("SYSTEM"),
                "WEB:alice",
            )
            self.assertEqual(
                audit_source("SCHEDULER"),
                "SCHEDULER",
            )
            self.assertEqual(
                audit_source("WEB:bob"),
                "WEB:bob",
            )
        finally:
            reset_audit_actor(token)

        self.assertEqual(
            current_audit_actor(),
            "LOCAL",
        )


if __name__ == "__main__":
    unittest.main()
