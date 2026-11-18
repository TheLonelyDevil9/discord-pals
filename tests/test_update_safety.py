"""Failure paths of the dashboard updater, run against real Git checkouts (see GitCheckoutCase)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import update as update_module
from test_support import GitCheckoutCase, MemorySandboxMixin, git


class GitFixtureTests(GitCheckoutCase):
    def test_git_reads_only_the_repository_config(self):
        lines = git(self.install, "config", "--list", "--show-scope").stdout.splitlines()

        self.assertEqual({line.split("\t", 1)[0] for line in lines}, {"local"})


class PreflightTests(GitCheckoutCase):
    def test_a_clean_checkout_passes(self):
        update_module.preflight(self.install)

    def test_unresolved_conflicts_refuse_the_update_and_name_the_files(self):
        self.leave_stash_pop_conflict()
        self.assertTrue(self.unmerged_paths())

        with self.assertRaises(update_module.UpdateRefused) as refused:
            update_module.preflight(self.install)

        self.assertIn("prompts/other_prompts.md", str(refused.exception))

    def test_an_unfinished_merge_refuses_the_update(self):
        self.leave_merge_in_progress()
        self.assertEqual(self.unmerged_paths(), "")

        with self.assertRaises(update_module.UpdateRefused) as refused:
            update_module.preflight(self.install)

        self.assertIn("merge", str(refused.exception))
        self.assertIn("git merge --abort", str(refused.exception))


class DashboardUpdateCase(MemorySandboxMixin, GitCheckoutCase):
    """Drive POST /api/update against the install clone."""

    def setUp(self):
        GitCheckoutCase.setUp(self)
        self.setUpMemorySandbox()
        self.client = self.make_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True
        self.log_path = self.root / "update_log.json"
        for patcher in (
            patch.object(dashboard_module, "_repo_root", return_value=str(self.install)),
            patch.object(dashboard_module, "_check_github_latest_version", return_value=None),
            patch.object(dashboard_module, "_get_file_version",
                         side_effect=lambda: update_module.read_file_version(self.install)),
            patch.object(dashboard_module, "_UPDATE_LOG_FILE", self.log_path),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tearDownMemorySandbox()
        GitCheckoutCase.tearDown(self)

    def post_update(self):
        return self.client.post("/api/update", json={"update_branch": "main"}, headers=self.csrf_headers())

    def last_log_entry(self):
        return json.loads(self.log_path.read_text(encoding="utf-8"))[-1]


class DashboardPreflightTests(DashboardUpdateCase):
    def test_update_refuses_a_checkout_left_with_conflicts_and_changes_nothing(self):
        self.leave_stash_pop_conflict()
        self.publish({"main.py": "STARTED = 2\n"})
        before = self.head()
        conflicted = self.read("prompts/other_prompts.md")

        response = self.post_update()

        data = response.get_json()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(data["status"], "error")
        self.assertIn("prompts/other_prompts.md", data["message"])
        self.assertEqual(self.head(), before)
        self.assertEqual(self.read("prompts/other_prompts.md"), conflicted)
        entry = self.last_log_entry()
        self.assertEqual(entry["status"], "refused")
        self.assertEqual(entry["before_head"], before)
        self.assertEqual(entry["after_head"], before)

    def test_update_refuses_while_a_merge_is_in_progress(self):
        self.leave_merge_in_progress()
        self.publish({"main.py": "STARTED = 2\n"})
        before = self.head()

        response = self.post_update()

        self.assertEqual(response.status_code, 409)
        self.assertIn("git merge --abort", response.get_json()["message"])
        self.assertEqual(self.head(), before)
        self.assertTrue((self.install / ".git" / "MERGE_HEAD").exists())


class DashboardUntrackedFileTests(DashboardUpdateCase):
    OWN_FILE = b"# My own Luna\r\nwritten by the operator\r\n"

    def check_refused(self, ignored, conflicting_edit):
        if ignored:
            self.publish({".gitignore": "characters/*.md\n!characters/firefly.md\n"})
            git(self.install, "pull", "-q", "--ff-only")
        own = self.install / "characters" / "luna.md"
        own.write_bytes(self.OWN_FILE)
        self.assertEqual(git(self.install, "check-ignore", "-q", "characters/luna.md", check=False).returncode,
                         0 if ignored else 1)
        if conflicting_edit:
            self.edit("prompts/other_prompts.md", "local first line\nsecond line\n")
        (self.seed / "characters" / "luna.md").write_text("# Upstream Luna\n", encoding="utf-8", newline="\n")
        git(self.seed, "add", "-f", "characters/luna.md")
        self.publish({"prompts/other_prompts.md": "upstream first line\nsecond line\n"} if conflicting_edit
                     else {"main.py": "STARTED = 2\n"})
        before = self.head()

        response = self.post_update()

        self.assertEqual(response.status_code, 409)
        self.assertIn("characters/luna.md", response.get_json()["message"])
        self.assertEqual(own.read_bytes(), self.OWN_FILE)
        self.assertEqual(self.head(), before)
        self.assertFalse((self.install / "bot_data" / "update_backups").exists())
        if conflicting_edit:
            self.assertEqual(self.read("prompts/other_prompts.md"), "local first line\nsecond line\n")
        self.assertEqual(self.last_log_entry()["status"], "refused")

    def test_an_ignored_file_at_a_path_the_update_adds_refuses_the_update(self):
        self.check_refused(ignored=True, conflicting_edit=False)

    def test_an_ignored_file_and_a_conflicting_edit_refuse_the_update(self):
        self.check_refused(ignored=True, conflicting_edit=True)

    def test_an_untracked_file_at_a_path_the_update_adds_refuses_the_update(self):
        self.check_refused(ignored=False, conflicting_edit=False)

    def test_an_untracked_file_and_a_conflicting_edit_refuse_the_update(self):
        self.check_refused(ignored=False, conflicting_edit=True)

    def test_an_untracked_file_where_the_update_adds_a_folder_refuses_the_update(self):
        (self.install / "extras").write_bytes(self.OWN_FILE)
        self.publish({"extras/notes.md": "upstream notes\n"})

        response = self.post_update()

        self.assertEqual(response.status_code, 409)
        self.assertIn("extras", response.get_json()["message"])
        self.assertEqual((self.install / "extras").read_bytes(), self.OWN_FILE)


class DashboardLocalChangesTests(DashboardUpdateCase):
    def stash_entries(self):
        return git(self.install, "stash", "list").stdout.splitlines()

    def test_local_changes_that_conflict_roll_the_update_back(self):
        self.edit("prompts/other_prompts.md", "local first line\nsecond line\n")
        self.edit("characters/firefly.md", None)
        self.publish({
            "prompts/other_prompts.md": "upstream first line\nsecond line\n",
            "characters/firefly.md": "# Firefly, revised upstream\n",
        })
        before = self.head()

        response = self.post_update()

        data = response.get_json()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(data["status"], "error")
        self.assertTrue(data["rolled_back"])
        self.assertIn("rolled back", data["message"])
        self.assertIn("prompts/other_prompts.md", data["message"])
        self.assertIn("characters/firefly.md", data["message"])
        self.assertIn("stash@{0}", data["message"])
        self.assertEqual(self.head(), before)
        self.assertEqual(self.unmerged_paths(), "")
        self.assertEqual(self.read("prompts/other_prompts.md"), "local first line\nsecond line\n")
        self.assertFalse((self.install / "characters" / "firefly.md").exists())
        self.assertEqual(len(self.stash_entries()), 1)
        entry = self.last_log_entry()
        self.assertEqual(entry["status"], "rolled_back")
        self.assertEqual((entry["before_head"], entry["after_head"]), (before, before))
        self.assertEqual(entry["conflicts"], ["characters/firefly.md", "prompts/other_prompts.md"])

    def test_local_changes_that_fit_are_carried_over_without_a_leftover_stash(self):
        self.edit("characters/firefly.md", None)
        upstream = self.publish({"main.py": "STARTED = 2\n"})

        response = self.post_update()

        data = response.get_json()
        self.assertEqual(response.status_code, 200, data)
        self.assertTrue(data["updated"])
        self.assertEqual(self.head(), upstream)
        self.assertEqual(self.read("main.py"), "STARTED = 2\n")
        self.assertFalse((self.install / "characters" / "firefly.md").exists())
        self.assertEqual(self.stash_entries(), [])


class DashboardAutoRestartTests(DashboardUpdateCase):
    def test_a_clean_update_restarts_automatically(self):
        self.publish({"main.py": "STARTED = 2\n"})

        data = self.post_update().get_json()

        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["updated"])
        self.assertTrue(data["restart_required"])
        self.assertEqual(data["warnings"], [])
        self.assertTrue(data["auto_restart"])

    def test_a_failed_dependency_install_keeps_the_update_without_restarting(self):
        upstream = self.publish({"requirements.txt": "=this is not a requirement=\n", "main.py": "STARTED = 2\n"})

        with patch.object(dashboard_module, "_pip_command_candidates",
                          return_value=[[sys.executable, "-m", "pip"]]):
            data = self.post_update().get_json()

        self.assertEqual(data["status"], "ok")
        self.assertEqual(self.head(), upstream)
        self.assertEqual(data["dependency_status"], "warning")
        self.assertTrue(any("Dependency install failed" in warning for warning in data["warnings"]))
        self.assertFalse(data["auto_restart"])
        entry = self.last_log_entry()
        self.assertEqual((entry["dependency_status"], entry["after_head"]), ("warning", upstream))

    def test_any_warning_stops_the_automatic_restart(self):
        git(self.install, "commit", "-q", "--allow-empty", "-m", "local commit")
        self.publish({"main.py": "STARTED = 2\n"})

        data = self.post_update().get_json()

        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["updated"])
        self.assertTrue(any("diverged" in warning for warning in data["warnings"]))
        self.assertFalse(data["auto_restart"])

    def test_the_page_restarts_only_on_auto_restart_and_shows_the_result(self):
        page = self.client.get("/").get_data(as_text=True)

        self.assertIn('id="update-result"', page)
        self.assertEqual(page.count("restartBot(true)"), 1)
        self.assertRegex(page, r"if \(data\.auto_restart\) \{[^}]*restartBot\(true\)")


class DashboardUpdateLogTests(DashboardUpdateCase):
    def test_a_failed_fetch_logs_the_commit_before_and_after(self):
        git(self.install, "remote", "set-url", "origin", str(self.root / "missing.git"))
        before = self.head()

        response = self.post_update()

        self.assertEqual(response.status_code, 500)
        entry = self.last_log_entry()
        self.assertEqual(entry["status"], "error")
        self.assertEqual((entry["before_head"], entry["after_head"]), (before, before))

    def test_a_timeout_logs_the_commit_before_and_after(self):
        before = self.head()
        with patch.object(dashboard_module, "_perform_git_update",
                          side_effect=subprocess.TimeoutExpired(["git", "fetch"], 180)):
            response = self.post_update()

        self.assertEqual(response.status_code, 500)
        entry = self.last_log_entry()
        self.assertEqual((entry["error"], entry["before_head"], entry["after_head"]), ("timeout", before, before))

    def test_an_update_already_on_disk_logs_the_commit(self):
        before = self.head()
        with patch.object(dashboard_module, "VERSION", "0.9.0"):
            response = self.post_update()

        self.assertTrue(response.get_json()["restart_required"])
        entry = self.last_log_entry()
        self.assertEqual((entry["status"], entry["before_head"], entry["after_head"]), ("restart_required", before, before))

    def test_a_pip_timeout_is_a_dependency_warning_not_an_error(self):
        (self.install / "requirements.txt").write_text("flask\n", encoding="utf-8")
        with patch.object(dashboard_module, "_run_command",
                          side_effect=subprocess.TimeoutExpired(["pip"], dashboard_module._UPDATE_PIP_TIMEOUT)) as run:
            result = dashboard_module._install_update_dependencies(str(self.install))

        self.assertEqual(result["status"], "warning")
        self.assertIn("timed out", result["warning"])
        self.assertEqual(run.call_count, 1)


class ImportCheckTests(unittest.TestCase):
    def check(self, files):
        with tempfile.TemporaryDirectory() as temp:
            for name, text in files.items():
                Path(temp, name).write_text(text, encoding="utf-8")
            return update_module.check_imports(Path(temp), sys.executable)

    def test_code_that_imports_passes(self):
        self.assertIsNone(self.check({"main.py": "import dashboard\n", "dashboard.py": "PAGES = ()\n"}))

    def test_an_import_error_is_reported_by_its_last_line(self):
        error = self.check({"main.py": "STARTED = True\n", "dashboard.py": "raise ImportError('broken on purpose')\n"})
        self.assertEqual(error, "ImportError: broken on purpose")

    def test_a_syntax_error_is_reported(self):
        error = self.check({"main.py": "def broken(:\n", "dashboard.py": "PAGES = ()\n"})
        self.assertIn("SyntaxError", error)

    def test_the_check_does_not_write_into_the_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "main.py").write_text("open('bot_data.json', 'w').close()\n", encoding="utf-8")
            Path(temp, "dashboard.py").write_text("PAGES = ()\n", encoding="utf-8")
            self.assertIsNone(update_module.check_imports(Path(temp), sys.executable))
            self.assertFalse(Path(temp, "bot_data.json").exists())


class DashboardImportGateTests(DashboardUpdateCase):
    def test_a_new_commit_that_does_not_import_is_rolled_back(self):
        self.edit("characters/firefly.md", "# Firefly, edited locally\n")
        self.publish({"dashboard.py": "raise ImportError('broken on purpose')\n"})
        before = self.head()

        response = self.post_update()

        data = response.get_json()
        self.assertEqual(response.status_code, 409)
        self.assertTrue(data["rolled_back"])
        self.assertIn("ImportError: broken on purpose", data["message"])
        self.assertEqual(self.head(), before)
        self.assertEqual(self.read("dashboard.py"), "PAGES = ()\n")
        self.assertEqual(self.read("characters/firefly.md"), "# Firefly, edited locally\n")
        self.assertEqual(len(git(self.install, "stash", "list").stdout.splitlines()), 1)
        entry = self.last_log_entry()
        self.assertEqual((entry["status"], entry["cause"]), ("rolled_back", "import_failed"))
        self.assertEqual((entry["before_head"], entry["after_head"]), (before, before))


if __name__ == "__main__":
    unittest.main()
