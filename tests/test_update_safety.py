"""Failure paths of the dashboard updater, run against real Git checkouts.

Each test publishes commits from a seed clone to a bare remote and updates an
install clone that stands in for a live checkout.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import update as update_module
from test_support import MemorySandboxMixin


def git(cwd, *args, check=True):
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr or result.stdout}")
    return result


BASE_FILES = {
    "version.py": '__version__ = "1.0.0"\nVERSION = __version__\n',
    "main.py": "STARTED = True\n",
    "dashboard.py": "PAGES = ()\n",
    "prompts/other_prompts.md": "first line\nsecond line\n",
    "characters/firefly.md": "# Firefly\n",
}


@unittest.skipUnless(shutil.which("git"), "the updater tests need git")
class GitCheckoutCase(unittest.TestCase):
    def setUp(self):
        self._git_temp = tempfile.TemporaryDirectory()
        self.root = Path(self._git_temp.name)
        self.remote, self.seed, self.install = self.root / "remote.git", self.root / "seed", self.root / "install"
        git(self.root, "init", "-q", "--bare", "-b", "main", str(self.remote))
        git(self.root, "clone", "-q", "-c", "core.autocrlf=false", str(self.remote), str(self.seed))
        self.set_identity(self.seed)
        self.publish(BASE_FILES, "initial")
        git(self.root, "clone", "-q", "-c", "core.autocrlf=false", str(self.remote), str(self.install))
        self.set_identity(self.install)

    def tearDown(self):
        self._git_temp.cleanup()

    @staticmethod
    def set_identity(repo):
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Test")

    def publish(self, files, message="upstream change"):
        for relative, text in files.items():
            path = self.seed / relative
            if text is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
        git(self.seed, "add", "-A")
        git(self.seed, "commit", "-q", "-m", message)
        git(self.seed, "push", "-q", "origin", "HEAD:main")
        return git(self.seed, "rev-parse", "HEAD").stdout.strip()

    def edit(self, relative, text):
        path = self.install / relative
        if text is None:
            path.unlink()
        else:
            path.write_text(text, encoding="utf-8", newline="\n")

    def read(self, relative):
        return (self.install / relative).read_text(encoding="utf-8")

    def head(self):
        return git(self.install, "rev-parse", "HEAD").stdout.strip()

    def unmerged_paths(self):
        return git(self.install, "ls-files", "--unmerged").stdout.strip()

    def leave_stash_pop_conflict(self):
        """Wedge the install the way a stash pop that conflicted used to leave it."""
        self.edit("prompts/other_prompts.md", "local first line\nsecond line\n")
        self.publish({"prompts/other_prompts.md": "upstream first line\nsecond line\n"})
        git(self.install, "fetch", "-q")
        git(self.install, "stash", "push", "-q", "-m", "earlier update")
        git(self.install, "merge", "--ff-only", "-q", "origin/main")
        git(self.install, "stash", "pop", "--index", check=False)

    def leave_merge_in_progress(self):
        git(self.install, "checkout", "-q", "-b", "side")
        self.edit("characters/firefly.md", "# Firefly, edited on a side branch\n")
        git(self.install, "commit", "-q", "-am", "side change")
        git(self.install, "checkout", "-q", "main")
        git(self.install, "merge", "-q", "--no-ff", "--no-commit", "side")


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


class RestartGateTests(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True

    def tearDown(self):
        self.tearDownMemorySandbox()

    def post_restart(self, import_error):
        with patch.object(update_module, "check_imports", return_value=import_error) as check, \
                patch("discord_utils.save_history"), \
                patch.object(dashboard_module.threading, "Thread") as thread:
            response = self.client.post("/api/restart", headers=self.csrf_headers())
        return response, check, thread

    def test_restart_is_refused_when_the_code_on_disk_does_not_import(self):
        response, check, thread = self.post_restart("ImportError: broken on purpose")

        self.assertEqual(response.status_code, 409)
        self.assertIn("ImportError: broken on purpose", response.get_json()["message"])
        thread.assert_not_called()
        self.assertEqual(check.call_args.args[0], Path(dashboard_module.__file__).resolve().parent)

    def test_restart_goes_ahead_when_the_code_imports(self):
        response, _check, thread = self.post_restart(None)

        self.assertEqual(response.status_code, 200)
        thread.assert_called_once()


if __name__ == "__main__":
    unittest.main()
