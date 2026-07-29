import json
import unittest
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module


class DashboardHealthProbeTests(unittest.TestCase):
    """The host health check must survive DASHBOARD_PASS being set.

    Monitoring polled /api/version, which redirects to the login page once
    authentication is enabled, so turning on the password would have made the
    5-minute cron alert forever.
    """

    def setUp(self):
        dashboard_module.app.config["TESTING"] = True
        self.client = dashboard_module.app.test_client()

    def test_health_probe_is_open_when_auth_is_enabled(self):
        with patch.dict("os.environ", {"DASHBOARD_PASS": "secret"}, clear=False):
            response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.get_data(as_text=True)), {"status": "ok"})

    def test_health_probe_reveals_no_runtime_detail(self):
        with patch.dict("os.environ", {"DASHBOARD_PASS": "secret"}, clear=False):
            payload = json.loads(self.client.get("/healthz").get_data(as_text=True))

        self.assertEqual(set(payload), {"status"})

    def test_other_routes_still_redirect_to_login(self):
        with patch.dict("os.environ", {"DASHBOARD_PASS": "secret"}, clear=False):
            root = self.client.get("/")
            version = self.client.get("/api/version")
            config = self.client.get("/api/config")

        for response in (root, version, config):
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
