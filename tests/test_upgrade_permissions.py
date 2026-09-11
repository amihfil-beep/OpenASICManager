import stat
import tempfile
import unittest
from pathlib import Path

from maintenance.upgrade import _normalize_tree_permissions


class UpgradePermissionTests(unittest.TestCase):
    def test_staged_install_root_is_traversable_by_service_user(self):
        with tempfile.TemporaryDirectory(
            prefix="openasic-upgrade-permissions-"
        ) as temporary:
            root = Path(temporary) / "staging"
            app = root / "app"
            scripts = root / "scripts"

            app.mkdir(parents=True)
            scripts.mkdir()

            (app / "app.py").write_text(
                "app = None\n",
                encoding="utf-8",
            )
            tool = scripts / "tool"
            tool.write_text(
                "#!/bin/sh\n",
                encoding="utf-8",
            )

            root.chmod(0o700)

            _normalize_tree_permissions(root)

            self.assertEqual(
                stat.S_IMODE(root.stat().st_mode),
                0o755,
            )
            self.assertEqual(
                stat.S_IMODE(app.stat().st_mode),
                0o755,
            )
            self.assertEqual(
                stat.S_IMODE(
                    (app / "app.py").stat().st_mode
                ),
                0o644,
            )
            self.assertEqual(
                stat.S_IMODE(tool.stat().st_mode),
                0o755,
            )


if __name__ == "__main__":
    unittest.main()
