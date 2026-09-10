import re
import unittest
from pathlib import Path

from app_version import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_runtime_version_matches_release_file(self):
        release_version = (
            (ROOT / "VERSION")
            .read_text()
            .strip()
        )

        self.assertEqual(
            APP_VERSION,
            release_version,
        )

    def test_version_is_semver(self):
        self.assertRegex(
            APP_VERSION,
            re.compile(r"^\d+\.\d+\.\d+$"),
        )


if __name__ == "__main__":
    unittest.main()
