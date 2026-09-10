import ast
import unittest
from pathlib import Path


APP_ROOT = Path("app")


class ArchitectureBoundaryTests(unittest.TestCase):
    def python_files(self):
        return sorted(APP_ROOT.rglob("*.py"))

    def test_direct_database_connections_stay_in_persistence_modules(self):
        violations = []

        for path in self.python_files():
            relative = path.relative_to(APP_ROOT)
            if relative == Path("db.py") or path.name == "repository.py":
                continue

            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "db"
                ):
                    violations.append(
                        f"{relative}:{node.lineno}"
                    )

        self.assertEqual(
            violations,
            [],
            "Direct db() calls outside persistence modules: "
            + ", ".join(violations),
        )

    def test_sqlite_imports_stay_in_persistence_modules(self):
        violations = []

        for path in self.python_files():
            relative = path.relative_to(APP_ROOT)
            if relative == Path("db.py") or path.name == "repository.py":
                continue

            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name for alias in node.names}
                    if "sqlite3" in names:
                        violations.append(
                            f"{relative}:{node.lineno}"
                        )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "sqlite3"
                ):
                    violations.append(
                        f"{relative}:{node.lineno}"
                    )

        self.assertEqual(
            violations,
            [],
            "sqlite3 imports outside persistence modules: "
            + ", ".join(violations),
        )

    def test_api_modules_do_not_import_application_root(self):
        violations = []

        for path in sorted((APP_ROOT / "api").glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"app", "app.app"}:
                            violations.append(
                                f"{path.name}:{node.lineno}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module in {"app", "app.app"}:
                        violations.append(
                            f"{path.name}:{node.lineno}"
                        )

        self.assertEqual(
            violations,
            [],
            "API modules import the application root: "
            + ", ".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
