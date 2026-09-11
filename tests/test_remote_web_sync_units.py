from pathlib import Path
import stat
import unittest


class RemoteWebSyncUnitTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_application_wants_sync_timer(self):
        content = (
            self.root
            / "deploy/systemd/openasicmanager.service"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "Wants=openasicmanager-remote-web-sync.timer",
            content,
        )

    def test_sync_timer_stops_with_application(self):
        content = (
            self.root
            / "deploy/systemd/openasicmanager-remote-web-sync.timer"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "PartOf=openasicmanager.service",
            content,
        )
        self.assertIn(
            "OnUnitActiveSec=60s",
            content,
        )

    def test_sync_service_is_root_owned_and_skips_without_nginx(self):
        content = (
            self.root
            / "deploy/systemd/openasicmanager-remote-web-sync.service"
        ).read_text(encoding="utf-8")

        self.assertIn("User=root", content)
        self.assertIn("Group=root", content)
        self.assertIn(
            "ConditionPathExists=/usr/sbin/nginx",
            content,
        )
        self.assertIn(
            "ProtectSystem=strict",
            content,
        )
        self.assertNotIn(
            "User=openasicmanager",
            content,
        )

    def test_sync_command_is_executable_in_repository(self):
        path = (
            self.root
            / "scripts/openasicmanager-remote-web-sync"
        )
        mode = stat.S_IMODE(
            path.stat().st_mode
        )

        self.assertTrue(
            mode & stat.S_IXUSR
        )


if __name__ == "__main__":
    unittest.main()
