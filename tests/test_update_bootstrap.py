import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import update as update_module


REPO_ROOT = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT = REPO_ROOT / "update.py"


def run_git(args, cwd, check=True):
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


def write_version(repo: Path, version: str) -> None:
    (repo / "version.py").write_text(
        '"""\nVersion\n"""\n\n'
        f'__version__ = "{version}"\n'
        "VERSION = __version__\n",
        encoding="utf-8",
    )


def commit_all(repo: Path, message: str) -> None:
    run_git(["add", "."], repo)
    run_git(["commit", "-m", message], repo)


def ensure_remote_default_branch(remote: Path, branch: str = "main") -> None:
    head_file = remote / "HEAD"
    head_file.write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")


@unittest.skipUnless(shutil.which("git"), "git is required for bootstrap updater integration tests")
class BootstrapUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.install = self.root / "install"

        run_git(["init", "--bare", str(self.remote)], self.root)
        run_git(["clone", str(self.remote), str(self.seed)], self.root)
        run_git(["config", "user.email", "test@example.invalid"], self.seed)
        run_git(["config", "user.name", "Test User"], self.seed)
        write_version(self.seed, "2.2.1")
        (self.seed / "requirements.txt").write_text("", encoding="utf-8")
        commit_all(self.seed, "initial")
        run_git(["branch", "-M", "main"], self.seed)
        ensure_remote_default_branch(self.remote)
        run_git(["tag", "v2.2.1"], self.seed)
        run_git(["push", "origin", "main", "--tags"], self.seed)

        run_git(["clone", str(self.remote), str(self.install)], self.root)
        shutil.copy2(UPDATE_SCRIPT, self.install / "update.py")

    def tearDown(self):
        self.temp_dir.cleanup()

    def release_tag_ahead_of_branch(self, version: str = "2.2.10") -> None:
        run_git(["checkout", "-b", "release-work", "main"], self.seed)
        write_version(self.seed, version)
        commit_all(self.seed, f"release {version}")
        run_git(["tag", f"v{version}"], self.seed)
        ensure_remote_default_branch(self.remote)
        run_git(["push", "origin", f"v{version}"], self.seed)

    def retarget_remote_tag(self, version: str) -> None:
        run_git(["checkout", "main"], self.seed)
        write_version(self.seed, version)
        marker = self.seed / "retarget-marker.txt"
        marker.write_text(f"retargeted {version}\n", encoding="utf-8")
        commit_all(self.seed, f"retarget {version}")
        run_git(["tag", "-f", f"v{version}"], self.seed)
        run_git(["push", "--force", "origin", f"v{version}"], self.seed)

    def run_update(self):
        return subprocess.run(
            [sys.executable, "update.py"],
            cwd=str(self.install),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_bootstrap_updates_old_install_to_tag_ahead_of_branch(self):
        self.release_tag_ahead_of_branch()

        result = self.run_update()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('__version__ = "2.2.10"', (self.install / "version.py").read_text(encoding="utf-8"))
        self.assertTrue((self.install / "bot_data" / "update_log.json").exists())

    def test_bootstrap_preserves_untracked_runtime_data(self):
        self.release_tag_ahead_of_branch()
        runtime_file = self.install / "bot_data" / "history_channels" / "example.json"
        runtime_file.parent.mkdir(parents=True)
        runtime_file.write_text("{}", encoding="utf-8")

        result = self.run_update()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(runtime_file.exists())
        backups = list((self.install / "bot_data" / "update_backups").glob("pre-update-*"))
        self.assertTrue(backups)

    def test_bootstrap_logs_failure_when_no_release_tag_exists(self):
        run_git(["push", "origin", ":refs/tags/v2.2.1"], self.seed)

        result = self.run_update()

        self.assertNotEqual(result.returncode, 0)
        log_path = self.install / "bot_data" / "update_log.json"
        self.assertTrue(log_path.exists())

    def test_bootstrap_recovers_from_stale_local_release_tag(self):
        self.release_tag_ahead_of_branch("2.2.2")
        run_git(["fetch", "origin", "--tags"], self.install)
        self.retarget_remote_tag("2.2.2")

        result = self.run_update()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('__version__ = "2.2.2"', (self.install / "version.py").read_text(encoding="utf-8"))


class BootstrapUpdaterFetchRecoveryTests(unittest.TestCase):
    def test_fetch_all_with_tag_recovery_forces_tags_after_clobber_error(self):
        calls = []

        def fake_run(args, cwd, timeout=update_module.GIT_TIMEOUT, check=False):
            calls.append(args)
            if args == ["git", "fetch", "--all", "--tags", "--prune"]:
                return subprocess.CompletedProcess(
                    args,
                    1,
                    "",
                    "! [rejected] v1.1.0 -> v1.1.0 (would clobber existing tag)",
                )
            if args == [
                "git",
                "fetch",
                "--prune",
                "--force",
                "origin",
                "+refs/heads/*:refs/remotes/origin/*",
                "+refs/tags/*:refs/tags/*",
            ]:
                return subprocess.CompletedProcess(args, 0, "forced tags\n", "")
            raise AssertionError(f"Unexpected command: {args}")

        with patch.object(update_module, "run", side_effect=fake_run):
            result = update_module.fetch_all_with_tag_recovery(Path("repo"), "origin")

        self.assertEqual(result.returncode, 0)
        self.assertIn("Recovered from stale local release tags", result.stdout)
        self.assertEqual(len(calls), 2)
