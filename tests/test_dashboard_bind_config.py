import unittest
from unittest.mock import patch

import module_stubs  # noqa: F401
import config as config_module


class DashboardPortConfigTests(unittest.TestCase):
    """DASHBOARD_PORT/DASHBOARD_HOST exist so operators need not edit main.py.

    The live deployment previously carried a skip-worktree edit to main.py just
    to change the port, which hid the file from `git status` and made the
    checkout non-reproducible.
    """

    def resolve_port(self, raw, key="DASHBOARD_PORT", default=5000):
        with patch.dict("os.environ", {key: raw}, clear=False):
            return config_module._port_from_env(key, default)

    def test_unset_falls_back_to_default(self):
        self.assertEqual(self.resolve_port(""), 5000)

    def test_valid_port_is_used(self):
        self.assertEqual(self.resolve_port("2569"), 2569)

    def test_whitespace_is_tolerated(self):
        self.assertEqual(self.resolve_port("  2569  "), 2569)

    def test_non_numeric_falls_back(self):
        self.assertEqual(self.resolve_port("not-a-port"), 5000)

    def test_out_of_range_falls_back(self):
        self.assertEqual(self.resolve_port("70000"), 5000)
        self.assertEqual(self.resolve_port("0"), 5000)

    def test_host_default_is_unchanged(self):
        # Changing this default would silently cut off existing deployments that
        # reach the dashboard over a private network interface.
        self.assertEqual(config_module.DASHBOARD_HOST, "0.0.0.0")

    def test_metrics_port_uses_its_own_default(self):
        self.assertEqual(self.resolve_port("", key="METRICS_PORT", default=9090), 9090)
        self.assertEqual(self.resolve_port("9464", key="METRICS_PORT", default=9090), 9464)

    def test_metrics_exporter_is_off_and_local_by_default(self):
        # The exporter is unauthenticated; enabling it must be a deliberate act.
        self.assertFalse(config_module.METRICS_ENABLED)
        self.assertEqual(config_module.METRICS_HOST, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
