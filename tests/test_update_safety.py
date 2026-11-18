"""Failure paths of the dashboard updater, run against real Git checkouts (see GitCheckoutCase)."""

import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import contextmanager
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


class GitFixtureUserFileTests(GitCheckoutCase):
    def setUp(self):
        # Git reads a per-user ignore file from the home folder even when GIT_CONFIG_GLOBAL
        # names another config file.
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        user_git = Path(home.name, ".config", "git")
        user_git.mkdir(parents=True)
        (user_git / "ignore").write_text("*.md\n", encoding="utf-8")
        environment = patch.dict(os.environ, {"HOME": home.name, "USERPROFILE": home.name,
                                              "XDG_CONFIG_HOME": str(user_git.parent)})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()

    def test_a_per_user_ignore_file_does_not_change_the_fixtures(self):
        self.assertIn("characters/firefly.md", git(self.install, "ls-files").stdout.split())


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


class UntrackedPathTests(GitCheckoutCase):
    def test_a_case_only_rename_upstream_is_not_an_untracked_file(self):
        self.publish({"characters/Luna.md": "# Luna\n"})
        git(self.install, "pull", "-q", "--ff-only")
        lower = self.install / "characters" / "luna.md"
        if not lower.exists():
            # A case-sensitive file system: the hard link makes luna.md the tracked file, as a
            # case-insensitive file system does.
            os.link(self.install / "characters" / "Luna.md", lower)
        head = self.head()
        git(self.seed, "mv", "characters/Luna.md", "characters/luna.md")
        git(self.seed, "commit", "-q", "-m", "lower-case the file name")
        git(self.seed, "push", "-q", "origin", "HEAD:main")
        git(self.install, "fetch", "-q")

        update_module.refuse_overwriting_untracked(self.install, head, git(self.install, "rev-parse", "origin/main").stdout.strip())


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


@contextmanager
def decoding_text_output_as_cp1252():
    """Decode text output that names no encoding with cp1252, like Windows with that ANSI code page."""
    real_run = subprocess.run

    def run(*args, **kwargs):
        if kwargs.get("text") and not kwargs.get("encoding"):
            kwargs["encoding"] = "cp1252"
        return real_run(*args, **kwargs)

    with patch("subprocess.run", side_effect=run):
        yield


class DashboardNonAsciiPathTests(DashboardUpdateCase):
    OWN_FILE = "# My own Amélie\n".encode("utf-8")

    def test_an_ignored_file_with_a_non_ascii_name_at_an_added_path_refuses_the_update(self):
        self.publish({".gitignore": "characters/*.md\n!characters/firefly.md\n"})
        git(self.install, "pull", "-q", "--ff-only")
        own = self.install / "characters" / "Amélie.md"
        own.write_bytes(self.OWN_FILE)
        (self.seed / "characters" / "Amélie.md").write_text("# Upstream Amélie\n", encoding="utf-8", newline="\n")
        git(self.seed, "add", "-f", "characters/Amélie.md")
        self.publish({"main.py": "STARTED = 2\n"})
        before = self.head()

        with decoding_text_output_as_cp1252():
            response = self.post_update()

        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertIn("characters/Amélie.md", response.get_json()["message"])
        self.assertEqual(own.read_bytes(), self.OWN_FILE)
        self.assertEqual(self.head(), before)

    def test_an_added_file_whose_name_the_code_page_cannot_decode_updates_cleanly(self):
        upstream = self.publish({"characters/Ágota.md": "# Ágota\n", "main.py": "STARTED = 2\n"})

        with decoding_text_output_as_cp1252():
            response = self.post_update()

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["updated"])
        self.assertEqual(self.head(), upstream)
        self.assertEqual(self.read("characters/Ágota.md"), "# Ágota\n")


class NonAsciiCheckoutFolderTests(GitCheckoutCase):
    def test_the_checkout_folder_is_read_from_git_as_utf_8(self):
        folder = self.root / "Zoë bot"
        git(self.root, "clone", "-q", str(self.remote), str(folder))

        with decoding_text_output_as_cp1252():
            found = dashboard_module._repo_root(str(folder))

        self.assertTrue(Path(found).is_dir(), found)
        self.assertTrue(os.path.samefile(found, folder))


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
        self.assertIn("with your local changes in place", data["message"])
        self.assertEqual(self.head(), before)
        self.assertEqual(self.unmerged_paths(), "")
        self.assertEqual(self.read("prompts/other_prompts.md"), "local first line\nsecond line\n")
        self.assertFalse((self.install / "characters" / "firefly.md").exists())
        self.assertEqual(self.stash_entries(), [])
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


class DashboardRestartRefusalTests(DashboardUpdateCase):
    def post_restart(self):
        with patch.object(dashboard_module, "__file__", str(self.install / "dashboard.py")), \
                patch.object(update_module, "check_imports", return_value=None) as check, \
                patch("dashboard_restart.start_restart") as start:
            response = self.client.post("/api/restart", headers=self.csrf_headers())
        return response, check, start

    def test_a_restart_is_refused_while_the_checkout_has_unresolved_conflicts(self):
        self.leave_stash_pop_conflict()

        response, check, thread = self.post_restart()

        self.assertEqual(response.status_code, 409)
        self.assertIn("Restart refused", response.get_json()["message"])
        self.assertIn("prompts/other_prompts.md", response.get_json()["message"])
        self.assertNotIn("update", response.get_json()["message"])
        thread.assert_not_called()
        check.assert_not_called()
        self.assertFalse(dashboard_module._update_lock.locked())

    def test_a_restart_is_refused_while_a_merge_is_in_progress(self):
        self.leave_merge_in_progress()

        response, _check, thread = self.post_restart()

        self.assertEqual(response.status_code, 409)
        self.assertIn("git merge --abort", response.get_json()["message"])
        self.assertNotIn("update", response.get_json()["message"])
        thread.assert_not_called()


class DashboardBranchModeTests(DashboardUpdateCase):
    """An install that follows staging while main carries a newer release tag."""

    def setUp(self):
        super().setUp()
        git(self.seed, "push", "-q", "origin", "HEAD:staging")
        git(self.install, "fetch", "-q")
        git(self.install, "checkout", "-q", "-b", "staging", "--track", "origin/staging")
        git(self.seed, "checkout", "-q", "-b", "staging", "--track", "origin/staging")

    def publish_staging(self, files):
        for relative, text in files.items():
            (self.seed / relative).write_text(text, encoding="utf-8", newline="\n")
        git(self.seed, "commit", "-q", "-am", "staging change")
        git(self.seed, "push", "-q", "origin", "HEAD:staging")
        return git(self.seed, "rev-parse", "HEAD").stdout.strip()

    def tag_release_on_main(self, version):
        git(self.seed, "checkout", "-q", "main")
        (self.seed / "version.py").write_text(f'__version__ = "{version}"\nVERSION = __version__\n',
                                              encoding="utf-8", newline="\n")
        git(self.seed, "commit", "-q", "-am", f"release {version}")
        git(self.seed, "tag", "-a", f"v{version}", "-m", f"v{version}")
        git(self.seed, "push", "-q", "origin", "HEAD:main", f"v{version}")
        git(self.seed, "checkout", "-q", "staging")

    def post_staging(self):
        with patch.object(dashboard_module, "_check_github_latest_version", return_value="1.0.1"), \
                patch.object(dashboard_module, "VERSION", "1.0.0"):
            return self.client.post("/api/update", json={"update_branch": "staging"}, headers=self.csrf_headers())

    def test_a_branch_update_is_complete_at_the_branch_tip_whatever_the_newest_tag(self):
        tip = self.publish_staging({"main.py": "STARTED = 'staging'\n"})
        self.tag_release_on_main("1.0.1")

        first = self.post_staging()
        second = self.post_staging()

        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertTrue(first.get_json()["updated"])
        self.assertTrue(first.get_json()["auto_restart"])
        self.assertEqual(self.head(), tip)
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertFalse(second.get_json()["updated"])
        self.assertIn("Already up to date", second.get_json()["message"])


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
        self.assertIn("failed its import check", data["message"])
        self.assertEqual(self.head(), before)
        self.assertEqual(self.read("dashboard.py"), "PAGES = ()\n")
        self.assertEqual(self.read("characters/firefly.md"), "# Firefly, edited locally\n")
        self.assertEqual(git(self.install, "stash", "list").stdout, "")
        entry = self.last_log_entry()
        self.assertEqual((entry["status"], entry["cause"]), ("rolled_back", "import_failed"))
        self.assertEqual((entry["before_head"], entry["after_head"]), (before, before))


class DashboardRollbackTests(DashboardUpdateCase):
    LOCAL_PROMPT = "local first line\nsecond line\n"

    def stage_conflict(self):
        self.edit("prompts/other_prompts.md", self.LOCAL_PROMPT)
        self.publish({"prompts/other_prompts.md": "upstream first line\nsecond line\n"})

    def snapshots(self):
        root = self.install / "bot_data" / "update_backups"
        return sorted(path.name for path in root.iterdir()) if root.exists() else []

    def assert_restored(self, before):
        self.assertEqual(self.head(), before)
        self.assertEqual(self.unmerged_paths(), "")
        self.assertEqual(self.read("prompts/other_prompts.md"), self.LOCAL_PROMPT)
        self.assertEqual(git(self.install, "stash", "list").stdout, "")

    def test_rolled_back_presses_keep_the_last_real_snapshot_and_leave_nothing_behind(self):
        self.publish({"main.py": "STARTED = 2\n"})
        self.assertTrue(self.post_update().get_json()["updated"])
        kept = self.snapshots()
        self.assertEqual(len(kept), 1)
        git(self.install, "commit", "-q", "--allow-empty", "-m", "local commit")
        self.stage_conflict()
        before = self.head()

        for _ in range(5):
            data = self.post_update().get_json()
            self.assertTrue(data["rolled_back"])
            self.assertFalse(any("diverged" in warning for warning in data["warnings"]))

        self.assert_restored(before)
        self.assertEqual(self.snapshots(), kept)
        self.assertEqual(git(self.install, "branch", "--list", "dashboard-update-backup-*").stdout, "")

    def test_a_snapshot_that_fails_part_way_leaves_no_folder(self):
        (self.install / "bot_data").mkdir(exist_ok=True)
        (self.install / "bot_data" / "stats.json").write_text("{}", encoding="utf-8")
        self.publish({"main.py": "STARTED = 2\n"})
        before = self.head()

        with patch.object(update_module, "backup_project_database", side_effect=OSError("disk full")):
            response = self.post_update()

        self.assertEqual(response.status_code, 500, response.get_json())
        self.assertEqual(self.snapshots(), [])
        self.assertEqual(self.head(), before)

    def test_a_kept_update_prunes_the_oldest_snapshots(self):
        older = ["pre-update-20250101000000", "pre-update-20250102000000", "pre-update-20250103000000"]
        for name in older:
            (self.install / "bot_data" / "update_backups" / name).mkdir(parents=True)
        self.publish({"main.py": "STARTED = 2\n"})

        self.assertTrue(self.post_update().get_json()["updated"])

        kept = self.snapshots()
        self.assertEqual(len(kept), update_module.BACKUP_RETENTION)
        self.assertEqual(kept[:2], older[1:])

    def fail_once(self, fail):
        """Patch update.run so that the first `git stash apply` goes through fail(result) instead."""
        real_run = update_module.run
        calls = []

        def run(args, cwd, timeout=update_module.GIT_TIMEOUT, check=False):
            if args[:3] == ["git", "stash", "apply"] and not calls:
                calls.append(args)
                return fail(real_run(args, cwd, timeout=timeout, check=False), args)
            return real_run(args, cwd, timeout=timeout, check=check)

        return patch.object(update_module, "run", side_effect=run)

    def stash_push_that_fails_part_way(self):
        """Patch the stash push to save its entry and then fail with prompts/other_prompts.md not reset.

        Git does this when another process holds a file open that the reset after the save must replace.
        """
        real_run_git = dashboard_module._run_git

        def run_git(args, repo_dir, timeout=dashboard_module._UPDATE_GIT_TIMEOUT):
            result = real_run_git(args, repo_dir, timeout=timeout)
            if args[:2] != ["stash", "push"]:
                return result
            self.edit("prompts/other_prompts.md", self.LOCAL_PROMPT)
            return subprocess.CompletedProcess(["git", *args], 1, result.stdout,
                                               "error: unable to unlink old 'prompts/other_prompts.md': Invalid argument\n"
                                               "fatal: Could not reset index file to revision 'HEAD'.\n")

        return patch.object(dashboard_module, "_run_git", side_effect=run_git)

    def edit_two_files(self):
        self.edit("characters/firefly.md", "# Firefly, edited locally\n")
        self.edit("prompts/other_prompts.md", self.LOCAL_PROMPT)
        self.publish({"main.py": "STARTED = 2\n"})

    def test_a_stash_push_that_fails_after_saving_its_entry_is_rolled_back_from_that_entry(self):
        self.edit_two_files()
        before = self.head()

        with self.stash_push_that_fails_part_way():
            response = self.post_update()

        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertTrue(response.get_json()["rolled_back"])
        self.assertIn("with your local changes in place", response.get_json()["message"])
        self.assert_restored(before)
        self.assertEqual(self.read("characters/firefly.md"), "# Firefly, edited locally\n")
        self.assertEqual(self.snapshots(), [])

    def test_a_stash_entry_that_cannot_be_restored_is_named_and_its_snapshot_kept(self):
        self.edit_two_files()

        with self.stash_push_that_fails_part_way(), \
                patch.object(update_module, "git_until_unlocked", side_effect=RuntimeError("unable to unlink")):
            response = self.post_update()

        stash_sha = git(self.install, "rev-parse", "stash@{0}").stdout.strip()
        self.assertEqual(response.status_code, 500, response.get_json())
        self.assertIn(f"git stash apply --index {stash_sha}", response.get_json()["message"])
        self.assertEqual(len(self.snapshots()), 1)

    def test_a_stash_apply_that_times_out_is_rolled_back(self):
        self.stage_conflict()
        before = self.head()

        def time_out(_result, args):
            raise subprocess.TimeoutExpired(args, 120)

        with self.fail_once(time_out):
            data = self.post_update().get_json()

        self.assertTrue(data["rolled_back"])
        self.assertIn("timed out", data["message"])
        self.assert_restored(before)

    def lock_after_the_conflict(self, age=0.0, time_out=False):
        """fail_once callback that leaves index.lock, age seconds old, after the conflicting reapply."""
        def leave_lock(result, args):
            lock = self.install / ".git" / "index.lock"
            lock.write_text("", encoding="utf-8")
            old = time.time() - age
            os.utime(lock, (old, old))
            if time_out:
                raise subprocess.TimeoutExpired(args, 120)
            return result

        return leave_lock

    @staticmethod
    def waits(sleep):
        """Send the waits of update.py to sleep(delay); subprocess keeps the real time module."""
        return patch.object(update_module, "time", types.SimpleNamespace(time=time.time, sleep=sleep))

    def test_a_lock_that_a_running_git_holds_is_never_removed(self):
        self.stage_conflict()
        before = self.head()
        delays = []

        with self.fail_once(self.lock_after_the_conflict()), self.waits(delays.append):
            response = self.post_update()

        stash_sha = git(self.install, "rev-parse", "stash@{0}").stdout.strip()
        self.assertEqual(response.status_code, 500, response.get_json())
        self.assertTrue((self.install / ".git" / "index.lock").exists())
        self.assertEqual(len(delays), update_module.LOCK_RETRIES)
        self.assertIn("index.lock", response.get_json()["message"])
        self.assertIn(f"git reset --hard {before}", response.get_json()["message"])
        self.assertIn(f"git stash apply --index {stash_sha}", response.get_json()["message"])

    def test_a_lock_released_during_the_wait_lets_the_rollback_finish(self):
        self.stage_conflict()
        before = self.head()
        lock = self.install / ".git" / "index.lock"

        with self.fail_once(self.lock_after_the_conflict()), self.waits(lambda _delay: lock.unlink()):
            data = self.post_update().get_json()

        self.assertTrue(data["rolled_back"])
        self.assert_restored(before)

    def test_a_lock_left_by_a_git_command_this_update_killed_is_removed_at_once(self):
        self.stage_conflict()
        before = self.head()
        delays = []

        with self.fail_once(self.lock_after_the_conflict(time_out=True)), self.waits(delays.append):
            data = self.post_update().get_json()

        self.assertTrue(data["rolled_back"])
        self.assertIn("timed out", data["message"])
        self.assert_restored(before)
        self.assertEqual(delays, [])

    def test_a_stale_lock_is_removed_at_once(self):
        self.stage_conflict()
        before = self.head()
        delays = []

        with self.fail_once(self.lock_after_the_conflict(age=update_module.STALE_LOCK_SECONDS + 60)), \
                self.waits(delays.append):
            data = self.post_update().get_json()

        self.assertTrue(data["rolled_back"])
        self.assert_restored(before)
        self.assertEqual(delays, [])

    def lock_before_the_reapply(self):
        """Patch update.run so that index.lock exists when the reapply first runs `git stash apply`."""
        real_run = update_module.run
        made = []

        def run(args, cwd, timeout=update_module.GIT_TIMEOUT, check=False):
            if args[:3] == ["git", "stash", "apply"] and not made:
                made.append(True)
                (self.install / ".git" / "index.lock").write_text("", encoding="utf-8")
            return real_run(args, cwd, timeout=timeout, check=check)

        return patch.object(update_module, "run", side_effect=run)

    def test_a_lock_released_while_the_reapply_waits_lets_the_update_finish(self):
        self.edit("characters/firefly.md", "# Firefly, edited locally\n")
        upstream = self.publish({"main.py": "STARTED = 2\n"})
        lock = self.install / ".git" / "index.lock"

        with self.lock_before_the_reapply(), self.waits(lambda _delay: lock.unlink()):
            response = self.post_update()

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.head(), upstream)
        self.assertEqual(self.read("characters/firefly.md"), "# Firefly, edited locally\n")

    def test_a_reapply_that_fails_without_a_conflict_says_so(self):
        self.edit("characters/firefly.md", "# Firefly, edited locally\n")
        self.publish({"main.py": "STARTED = 2\n"})
        before = self.head()

        def unlink_fails(_result, args):
            return subprocess.CompletedProcess(args, 1, "", "error: unable to unlink old 'characters/firefly.md': "
                                                            "Invalid argument\n")

        with self.fail_once(unlink_fails):
            response = self.post_update()

        data = response.get_json()
        self.assertEqual(response.status_code, 409, data)
        self.assertIn("could not be reapplied", data["message"])
        self.assertIn("unable to unlink", data["message"])
        self.assertNotIn("conflict", data["message"])
        self.assertNotIn("Discard", data["message"])
        self.assertEqual(self.last_log_entry()["cause"], "reapply_failed")
        self.assertEqual(self.head(), before)
        self.assertEqual(self.read("characters/firefly.md"), "# Firefly, edited locally\n")

    def test_staged_changes_that_do_not_apply_are_a_conflict(self):
        self.edit("prompts/other_prompts.md", self.LOCAL_PROMPT)
        git(self.install, "add", "prompts/other_prompts.md")
        self.publish({"prompts/other_prompts.md": "upstream first line\nsecond line\n"})

        data = self.post_update().get_json()

        self.assertTrue(data["rolled_back"])
        self.assertIn("conflict with the new version", data["message"])
        self.assertEqual(self.last_log_entry()["cause"], "local_changes_conflict")

    def failing_rollback_reset(self, error):
        real = update_module.git_until_unlocked

        def run(repo, args, *rest, **options):
            if args[0] == "reset":
                raise error
            return real(repo, args, *rest, **options)

        return patch.object(update_module, "git_until_unlocked", side_effect=run)

    def test_a_rollback_that_cannot_restore_the_checkout_says_how_to_restore_it(self):
        self.stage_conflict()
        before = self.head()

        with self.failing_rollback_reset(RuntimeError("disk is full")):
            response = self.post_update()

        data = response.get_json()
        stash_sha = git(self.install, "rev-parse", "stash@{0}").stdout.strip()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(data["status"], "error")
        self.assertFalse(data.get("auto_restart"))
        self.assertIn("conflict with the new version", data["message"])
        self.assertIn("disk is full", data["message"])
        self.assertIn(f"git reset --hard {before}", data["message"])
        self.assertIn(f"git stash apply --index {stash_sha}", data["message"])
        self.assertEqual(self.last_log_entry()["status"], "rollback_failed")

    def test_an_error_after_the_checkout_moved_is_rolled_back(self):
        self.edit("characters/firefly.md", "# Firefly, edited locally\n")
        self.publish({"main.py": "STARTED = 2\n"})
        before = self.head()

        with patch.object(dashboard_module, "_install_update_dependencies",
                          side_effect=PermissionError("pip is not executable")):
            data = self.post_update().get_json()

        self.assertTrue(data["rolled_back"])
        self.assertIn("pip is not executable", data["message"])
        self.assertEqual(self.head(), before)
        self.assertEqual(self.read("characters/firefly.md"), "# Firefly, edited locally\n")

    def test_an_error_reading_head_after_the_move_is_rolled_back(self):
        self.edit("characters/firefly.md", "# Firefly, edited locally\n")
        self.publish({"main.py": "STARTED = 2\n"})
        before = self.head()
        real_ref_sha = dashboard_module._git_ref_sha
        head_reads = []

        def ref_sha(repo_dir, ref):
            if ref == "HEAD":
                head_reads.append(ref)
                if len(head_reads) == 2:
                    raise RuntimeError("Resolving Git ref HEAD failed: simulated")
            return real_ref_sha(repo_dir, ref)

        with patch.object(dashboard_module, "_git_ref_sha", side_effect=ref_sha):
            data = self.post_update().get_json()

        self.assertTrue(data.get("rolled_back"), data)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.read("characters/firefly.md"), "# Firefly, edited locally\n")
        self.assertEqual(git(self.install, "stash", "list").stdout, "")
        self.assertEqual(self.snapshots(), [])

    def test_an_import_check_that_cannot_run_rolls_back_and_says_so(self):
        self.publish({"main.py": "STARTED = 2\n"})
        before = self.head()

        with patch.object(update_module.tempfile, "TemporaryDirectory",
                          side_effect=FileNotFoundError("No usable temporary directory found")):
            data = self.post_update().get_json()

        self.assertTrue(data["rolled_back"])
        self.assertIn("import check could not run", data["message"])
        self.assertNotIn("Git is not installed", data["message"])
        self.assertEqual(self.head(), before)

    def installs_during_a_failed_import(self, new_requirements, reinstall_works):
        self.publish({"requirements.txt": "flask\n"})
        git(self.install, "pull", "-q", "--ff-only")
        self.publish({"requirements.txt": new_requirements, "dashboard.py": "raise ImportError('broken')\n"})
        installed = []

        def install(repo_dir):
            installed.append((Path(repo_dir) / "requirements.txt").read_text(encoding="utf-8"))
            if len(installed) > 1 and not reinstall_works:
                return {"status": "warning", "message": "", "warning": "pip could not reach the index"}
            return {"status": "ok", "message": "", "warning": None}

        with patch.object(dashboard_module, "_install_update_dependencies", side_effect=install):
            data = self.post_update().get_json()
        return data, installed

    def test_the_rollback_reinstalls_the_old_requirements_when_they_changed(self):
        data, installed = self.installs_during_a_failed_import("flask>=99\n", reinstall_works=True)

        self.assertTrue(data["rolled_back"])
        self.assertEqual(installed, ["flask>=99\n", "flask\n"])
        self.assertEqual(data["warnings"], [])

    def test_a_failed_reinstall_says_the_packages_may_be_newer(self):
        data, installed = self.installs_during_a_failed_import("flask>=99\n", reinstall_works=False)

        self.assertEqual(len(installed), 2)
        warning = next(warning for warning in data["warnings"] if "reinstalled" in warning)
        self.assertIn("pip could not reach the index", warning)
        self.assertIn("restores only versions pinned with ==", warning)
        self.assertIn("may be newer than the code expects", warning)
        self.assertIn(warning, self.last_log_entry()["warnings"])

    def test_unchanged_requirements_are_not_reinstalled(self):
        _data, installed = self.installs_during_a_failed_import("flask\n", reinstall_works=True)

        self.assertEqual(installed, ["flask\n"])


if __name__ == "__main__":
    unittest.main()
