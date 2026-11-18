"""The notice an install gets when it still runs a Python version that is no longer supported."""

import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import python_support

ROOT = Path(__file__).resolve().parents[1]


class UnsupportedPythonNoticeTests(unittest.TestCase):
    def test_python_3_10_gets_a_notice_that_names_the_supported_versions(self):
        notice = python_support.unsupported_python_notice((3, 10, 14, "final", 0))

        self.assertIn("Python 3.10 is no longer supported", notice)
        self.assertIn("3.11, 3.12, 3.13 and 3.14", notice)

    def test_supported_versions_get_no_notice(self):
        for version in ((3, 11, 0), (3, 12, 9), (3, 13, 1), (3, 14, 0)):
            with self.subTest(version=version):
                self.assertIsNone(python_support.unsupported_python_notice(version))

    def test_startup_warns_once_on_python_3_10_and_keeps_going(self):
        warnings = []

        python_support.warn_if_unsupported(warnings.append, (3, 10, 14))

        self.assertEqual(len(warnings), 1)
        self.assertIn("no longer supported", warnings[0])

    def test_startup_stays_quiet_on_a_supported_version(self):
        warnings = []

        python_support.warn_if_unsupported(warnings.append, (3, 13, 1))

        self.assertEqual(warnings, [])

    def test_ci_tests_exactly_the_versions_the_notice_names(self):
        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))

        matrix = workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"]

        self.assertEqual(tuple(matrix), python_support.SUPPORTED_PYTHON_VERSIONS)


class DashboardPythonNoticeTests(unittest.TestCase):
    def setUp(self):
        dashboard_module.app.config["TESTING"] = True
        self.client = dashboard_module.app.test_client()

    def test_dashboard_home_shows_the_notice_on_an_unsupported_version(self):
        notice = "Python 3.10 is no longer supported."
        with patch.object(python_support, "unsupported_python_notice", return_value=notice):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(notice, response.get_data(as_text=True))

    def test_dashboard_home_has_no_notice_on_a_supported_version(self):
        with patch.object(python_support, "unsupported_python_notice", return_value=None):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Unsupported Python version", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
