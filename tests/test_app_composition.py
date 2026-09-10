import ast
import unittest
from pathlib import Path


class AppCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path("app/app.py")
        cls.source = cls.path.read_text()
        cls.tree = ast.parse(cls.source)

    def test_app_keeps_only_composition_level_functions(self):
        functions = {
            node.name
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(functions, {
            "lifespan",
            "audit_user_middleware",
            "start_telegram_summary_loop",
            "index",
        })

    def test_app_contains_no_direct_sql_or_driver_logic(self):
        forbidden = (
            "SELECT ",
            "INSERT INTO ",
            "UPDATE miners",
            "DELETE FROM ",
            "drivers.bitmain",
            "drivers.awesome",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.source)

    def test_expected_routers_are_registered(self):
        registered = set()

        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"
                and node.args
            ):
                continue

            router = node.args[0]
            if (
                isinstance(router, ast.Call)
                and isinstance(router.func, ast.Name)
            ):
                registered.add(router.func.id)

        self.assertEqual(registered, {
            "create_notifications_router",
            "create_scheduler_router",
            "create_telemetry_router",
            "create_operations_router",
            "create_discovery_router",
            "create_inventory_router",
            "create_control_router",
            "create_remote_web_router",
            "create_audit_auth_router",
            "create_system_router",
        })


if __name__ == "__main__":
    unittest.main()
