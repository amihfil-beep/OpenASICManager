import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVICE_PATH = PROJECT_ROOT / "deploy/systemd/openasicmanager.service"


class SystemdRuntimeTests(unittest.TestCase):
    def test_main_service_uses_venv_python_module_entrypoint(self):
        unit = SERVICE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "ExecStart=/opt/openasicmanager/venv/bin/python \\\n"
            "    -m uvicorn \\\n",
            unit,
        )
        self.assertNotIn(
            "ExecStart=/opt/openasicmanager/venv/bin/uvicorn",
            unit,
        )


if __name__ == "__main__":
    unittest.main()
