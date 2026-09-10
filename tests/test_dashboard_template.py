import inspect
import unittest

from fastapi.responses import HTMLResponse

import app as application
from ui.dashboard import DASHBOARD_PATH, dashboard_html


class DashboardTemplateTests(unittest.TestCase):
    def test_dashboard_resource_loads(self):
        content = dashboard_html()
        self.assertTrue(DASHBOARD_PATH.is_file())
        self.assertIn("<title>OpenASICManager</title>", content)
        self.assertIn("</html>", content.lower())

    def test_root_route_uses_external_dashboard(self):
        route = next(
            route
            for route in application.app.routes
            if getattr(route, "path", None) == "/"
            and "GET" in getattr(route, "methods", set())
        )

        self.assertFalse(inspect.iscoroutinefunction(route.endpoint))
        response = route.endpoint()
        self.assertIsInstance(response, str)
        self.assertEqual(
            response,
            dashboard_html(),
        )


if __name__ == "__main__":
    unittest.main()
