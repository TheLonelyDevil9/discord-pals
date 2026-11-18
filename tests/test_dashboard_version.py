import threading
import time
import unittest
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import runtime_config as runtime_config_module
from test_support import GitCheckoutCase, MemorySandboxMixin, git


class VersionApiCase(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True
        self._runtime_paths = (runtime_config_module.DATA_DIR, runtime_config_module.RUNTIME_CONFIG_FILE)
        runtime_config_module.DATA_DIR = str(self.data_dir)
        runtime_config_module.RUNTIME_CONFIG_FILE = str(self.data_dir / "runtime_config.json")
        runtime_config_module.invalidate_cache()
        dashboard_module._invalidate_github_version_cache()
        for patcher in (
            patch.object(dashboard_module, "VERSION", "1.0.0"),
            patch.object(dashboard_module, "_get_file_version", return_value="1.0.0"),
            patch.object(dashboard_module, "_RUNNING_COMMIT", "a" * 40),
            patch.object(dashboard_module, "_git_head", return_value="a" * 40),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        dashboard_module._invalidate_github_version_cache()
        runtime_config_module.DATA_DIR, runtime_config_module.RUNTIME_CONFIG_FILE = self._runtime_paths
        runtime_config_module.invalidate_cache()
        self.tearDownMemorySandbox()

    def version(self):
        return self.client.get("/api/version").get_json()

    def refresh_now(self, github_version, tracked=(None, True)):
        with patch.object(dashboard_module, "_fetch_latest_available_version", return_value=github_version), \
                patch.object(dashboard_module, "_repo_root", return_value="repo"), \
                patch.object(dashboard_module, "_fetch_tracked_branch", return_value=tracked):
            dashboard_module._refresh_version_state(runtime_config_module.get("update_branch", ""))

    def age_cache(self, seconds):
        dashboard_module._github_version_cache["fetched_at"] = time.time() - seconds


class VersionCheckTimingTests(VersionApiCase):
    def test_a_page_load_never_waits_for_the_network(self):
        release = threading.Event()

        def slow_fetch():
            release.wait(10)
            return "1.1.0"

        with patch.object(dashboard_module, "_fetch_latest_available_version", side_effect=slow_fetch) as fetch, \
                patch.object(dashboard_module, "_repo_root", return_value="repo"), \
                patch.object(dashboard_module, "_fetch_tracked_branch", return_value=(None, True)):
            started = time.monotonic()
            first = self.version()
            second = self.version()
            elapsed = time.monotonic() - started
            release.set()
            deadline = time.monotonic() + 10
            while dashboard_module._github_version_cache["refreshing"] and time.monotonic() < deadline:
                time.sleep(0.05)

        self.assertLess(elapsed, 5)
        self.assertIsNone(first["github_version"])
        self.assertIsNone(second["github_version"])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.version()["github_version"], "1.1.0")

    def test_a_failed_check_is_retried_after_five_minutes(self):
        self.refresh_now(None)

        with patch.object(dashboard_module, "_start_version_refresh") as start:
            self.age_cache(4 * 60)
            self.version()
            start.assert_not_called()
            self.age_cache(6 * 60)
            self.version()
            start.assert_called_once()

    def test_a_good_check_is_kept_for_thirty_minutes(self):
        self.refresh_now("1.1.0")

        with patch.object(dashboard_module, "_start_version_refresh") as start:
            self.age_cache(29 * 60)
            self.assertEqual(self.version()["github_version"], "1.1.0")
            start.assert_not_called()
            self.age_cache(31 * 60)
            self.version()
            start.assert_called_once()

    def test_a_failed_fetch_of_the_tracked_branch_counts_as_a_failure(self):
        self.refresh_now("1.1.0", tracked=("origin/main", False))

        with patch.object(dashboard_module, "_start_version_refresh") as start:
            self.age_cache(6 * 60)
            self.version()
            start.assert_called_once()

    def test_changing_the_update_branch_starts_a_new_check(self):
        self.refresh_now("1.0.0", tracked=("origin/main", True))
        runtime_config_module.set("update_branch", "staging")

        with patch.object(dashboard_module, "_start_version_refresh") as start:
            data = self.version()

        start.assert_called_once_with("staging")
        self.assertIsNone(data["tracked_ref"])


class VersionDriftTests(VersionApiCase):
    def test_a_commit_on_disk_that_is_not_running_needs_a_restart(self):
        self.refresh_now("1.0.0")
        with patch.object(dashboard_module, "_git_head", return_value="b" * 40):
            data = self.version()

        self.assertEqual((data["running_commit"], data["disk_commit"]), ("a" * 40, "b" * 40))
        self.assertTrue(data["restart_required"])
        self.assertFalse(data["update_available"])

    def test_the_running_commit_on_disk_needs_no_restart(self):
        self.refresh_now("1.0.0")

        data = self.version()

        self.assertEqual(data["running_commit"], data["disk_commit"])
        self.assertFalse(data["restart_required"])

    def test_a_selected_branch_with_new_commits_has_an_update(self):
        runtime_config_module.set("update_branch", "staging")
        self.refresh_now("1.0.0", tracked=("origin/staging", True))

        with patch.object(dashboard_module, "_commits_behind", return_value=3) as behind:
            data = self.version()

        behind.assert_called_once()
        self.assertEqual((data["tracked_ref"], data["commits_behind"]), ("origin/staging", 3))
        self.assertTrue(data["update_available"])

    def test_the_pages_name_new_commits_on_the_selected_branch(self):
        page = self.client.get("/").get_data(as_text=True)

        self.assertIn("function updateCommitsLabel(data)", page)
        self.assertIn("updateCommitsLabel(data) || ('Update to v' + version)", page)
        self.assertIn("showNewVersionBadge(data.latest_version, false, window.updateCommitsLabel", page)

    def test_a_selected_branch_ignores_a_newer_release_tag(self):
        runtime_config_module.set("update_branch", "staging")
        self.refresh_now("2.0.0", tracked=("origin/staging", True))

        with patch.object(dashboard_module, "_commits_behind", return_value=0):
            current = self.version()
        with patch.object(dashboard_module, "_commits_behind", return_value=2):
            behind = self.version()

        self.assertFalse(current["update_available"])
        self.assertTrue(behind["update_available"])
        self.assertEqual((current["latest_version"], behind["latest_version"]), ("1.0.0", "1.0.0"))

    def test_a_restart_after_a_branch_update_names_the_running_version(self):
        runtime_config_module.set("update_branch", "staging")
        self.refresh_now("2.0.0", tracked=("origin/staging", True))

        with patch.object(dashboard_module, "_git_head", return_value="b" * 40), \
                patch.object(dashboard_module, "_commits_behind", return_value=0):
            data = self.version()

        self.assertTrue(data["restart_required"])
        self.assertEqual(data["latest_version"], data["running_version"])

    def test_without_a_selected_branch_new_commits_do_not_count_as_a_release(self):
        self.refresh_now("1.0.0", tracked=("origin/main", True))

        with patch.object(dashboard_module, "_commits_behind", return_value=3):
            data = self.version()

        self.assertEqual(data["commits_behind"], 3)
        self.assertFalse(data["update_available"])


class TrackedBranchTests(GitCheckoutCase):
    def test_the_tracked_branch_is_fetched_and_its_new_commits_counted(self):
        self.publish({"main.py": "STARTED = 2\n"})
        self.publish({"main.py": "STARTED = 3\n"})

        tracked, fetched = dashboard_module._fetch_tracked_branch(str(self.install), "main")

        self.assertEqual((tracked, fetched), ("origin/main", True))
        self.assertEqual(dashboard_module._commits_behind(str(self.install), tracked), 2)

    def test_a_fetch_that_fails_is_reported(self):
        git(self.install, "remote", "set-url", "origin", str(self.root / "missing.git"))

        self.assertEqual(dashboard_module._fetch_tracked_branch(str(self.install), ""), ("origin/main", False))


if __name__ == "__main__":
    unittest.main()
