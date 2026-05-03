import json
import unittest

import module_stubs  # noqa: F401
import dashboard as dashboard_module


class DashboardAssetTests(unittest.TestCase):
    def setUp(self):
        dashboard_module.app.config["TESTING"] = True
        self.client = dashboard_module.app.test_client()

    def test_dashboard_includes_generated_favicon_package(self):
        response = self.client.get("/")
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/static/favicon-96x96.png"', page)
        self.assertNotIn('href="/static/favicon.svg"', page)
        self.assertIn('/static/dashboard-brand.webp', page)
        self.assertIn('href="/static/favicon.ico"', page)
        self.assertIn('href="/static/apple-touch-icon.png"', page)
        self.assertIn('content="Discord Pals"', page)
        self.assertIn('href="/static/site.webmanifest"', page)

    def test_manifest_icons_use_static_asset_paths(self):
        response = self.client.get("/static/site.webmanifest")
        manifest = json.loads(response.get_data(as_text=True))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [icon["src"] for icon in manifest["icons"]],
            [
                "/static/web-app-manifest-192x192.png",
                "/static/web-app-manifest-512x512.png",
            ],
        )
