import tempfile
import sqlite3
from contextlib import closing
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import update as update_module


def build_database(root: Path, rows: int = 300) -> Path:
    """Write a Project database in WAL mode that spans a few hundred pages."""
    database = root / "bot_data" / "project_automation.sqlite3"
    with closing(sqlite3.connect(str(database))) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE evidence (value TEXT)")
        live.executemany("INSERT INTO evidence VALUES (?)", [("x" * 4000,)] * rows)
        live.commit()
    return database


def build_repo(root: Path) -> None:
    """Lay out the parts of a live install that the updater snapshots."""
    logs = root / "bot_data" / "logs"
    logs.mkdir(parents=True)
    (logs / "discord-pals.log").write_text("x" * 4096, encoding="utf-8")
    (logs / "discord-pals.log.1").write_text("x" * 4096, encoding="utf-8")

    (root / "bot_data" / "stats.json").write_text("{}", encoding="utf-8")
    (root / "bot_data" / "history_cache.json").write_text("{}", encoding="utf-8")

    # A directory that happens to share the name must still be backed up.
    prompt_logs = root / "prompts" / "logs"
    prompt_logs.mkdir(parents=True)
    (prompt_logs / "keep.txt").write_text("keep", encoding="utf-8")

    characters = root / "characters"
    characters.mkdir()
    (characters / "pal.json").write_text("{}", encoding="utf-8")


class UpdateBackupContentsTests(unittest.TestCase):
    """Rotated logs must not be copied into update snapshots.

    bot_data/logs is size-capped by the logger's own rotation, so copying it into
    every retained snapshot multiplied disk use by the full log budget.
    """

    def assert_backup_shape(self, backup: Path) -> None:
        self.assertFalse(
            (backup / "bot_data" / "logs").exists(),
            "bot_data/logs must be excluded from update snapshots",
        )
        self.assertTrue((backup / "bot_data" / "stats.json").exists())
        self.assertTrue((backup / "bot_data" / "history_cache.json").exists())
        self.assertTrue((backup / "characters" / "pal.json").exists())
        # The exclusion is scoped to bot_data, not applied as a global pattern.
        self.assertTrue(
            (backup / "prompts" / "logs" / "keep.txt").exists(),
            "only bot_data/logs is excluded; other 'logs' directories are still backed up",
        )

    def test_bootstrap_updater_excludes_bot_data_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            build_repo(repo)

            backup = update_module.create_state_backup(repo)

            self.assertIsNotNone(backup)
            self.assert_backup_shape(Path(backup))

    def test_dashboard_updater_excludes_bot_data_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            build_repo(repo)

            backup = dashboard_module._create_update_state_backup(str(repo))

            self.assertIsNotNone(backup)
            self.assert_backup_shape(Path(backup))

    def test_both_updaters_snapshot_committed_wal_without_copying_journals(self):
        for backup_fn in (update_module.create_state_backup, dashboard_module._create_update_state_backup):
            with self.subTest(updater=backup_fn.__name__), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                build_repo(repo)
                database = repo / "bot_data" / "project_automation.sqlite3"
                with closing(sqlite3.connect(str(database))) as live:
                    live.execute("PRAGMA journal_mode=WAL")
                    live.execute("CREATE TABLE evidence (value TEXT)")
                    live.execute("INSERT INTO evidence VALUES ('approved draft')")
                    live.commit()
                    self.assertTrue(Path(str(database) + "-wal").exists())
                    backup = Path(backup_fn(repo)) / "bot_data" / database.name
                    with closing(sqlite3.connect(str(backup))) as copied:
                        self.assertEqual(copied.execute("SELECT value FROM evidence").fetchone()[0], "approved draft")
                        self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertFalse(Path(str(backup) + "-wal").exists())
                    self.assertFalse(Path(str(backup) + "-shm").exists())

    def test_both_updaters_copy_the_project_database_in_one_step_while_the_worker_writes(self):
        real_connect = sqlite3.connect
        for backup_fn in (update_module.create_state_backup, dashboard_module._create_update_state_backup):
            with self.subTest(updater=backup_fn.__name__), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                build_repo(repo)
                database = build_database(repo)
                steps = []

                class WriterBetweenSteps(sqlite3.Connection):
                    def backup(self, target, *, pages=-1, progress=None, name="main", sleep=0.25):
                        def write(status, remaining, total):
                            steps.append(remaining)
                            if len(steps) > 20:
                                raise RuntimeError("the copy restarted after every write")
                            # A write through another connection restarts a copy that is not done.
                            with closing(real_connect(str(database))) as writer, writer:
                                writer.execute("INSERT INTO evidence VALUES ('written during the copy')")
                        return super().backup(target, pages=pages, progress=write, name=name, sleep=sleep)

                def connect(*args, **kwargs):
                    return real_connect(*args, factory=WriterBetweenSteps, **kwargs)

                with patch.object(update_module.sqlite3, "connect", connect):
                    backup = Path(backup_fn(repo)) / "bot_data" / database.name
                self.assertEqual(steps, [0])
                with closing(real_connect(str(backup))) as copied:
                    self.assertEqual(copied.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 300)

    def test_both_updaters_leave_out_a_project_database_above_the_size_limit_and_say_so(self):
        for backup_fn in (update_module.create_state_backup, dashboard_module._create_update_state_backup):
            for limit, kept in ((64 * 1024, False), (update_module.PROJECT_DATABASE_SNAPSHOT_MAX_BYTES, True)):
                with self.subTest(updater=backup_fn.__name__, limit=limit), tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    build_repo(repo)
                    database = build_database(repo)
                    warnings = []
                    with patch.object(update_module, "PROJECT_DATABASE_SNAPSHOT_MAX_BYTES", limit):
                        backup = Path(backup_fn(repo, warnings))
                    self.assertEqual((backup / "bot_data" / database.name).exists(), kept)
                    self.assertTrue((backup / "bot_data" / "stats.json").exists())
                    self.assertEqual(len(warnings), 0 if kept else 1)
                    self.assertTrue(all("project_automation.sqlite3" in warning for warning in warnings))


def fake_git(refs, ancestors):
    """Answer the Git commands of a dashboard update from a ref table and (ancestor, descendant) pairs."""
    def run(args, repo_dir, timeout=dashboard_module._UPDATE_GIT_TIMEOUT):
        result = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "fetch" or args[:2] == ["status", "--porcelain"]:
            return result
        if args == ["remote"]:
            result.stdout = "origin\n"
        elif args[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            result.stdout = "main\n"
        elif args[:3] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
            result.stdout = "origin/main\n"
        elif args[:3] == ["rev-parse", "--verify", "--quiet"]:
            result.returncode = 0 if args[3] in refs else 1
        elif args[:2] == ["rev-parse", "--verify"]:
            result.stdout = refs[args[2].removesuffix("^{commit}")] + "\n"
        elif args[:2] == ["merge-base", "--is-ancestor"]:
            result.returncode = 0 if (args[2], args[3]) in ancestors else 1
        elif args[:2] == ["merge", "--ff-only"]:
            refs["HEAD"] = refs[args[2]]
        else:
            raise AssertionError(f"Unexpected git command: {args}")
        return result
    return run


class DashboardUpdateSnapshotTests(unittest.TestCase):
    """An Update click that leaves HEAD where it is must not rotate the last real snapshot away."""

    def update(self, repo, refs, ancestors):
        with patch.object(dashboard_module, "_run_git", side_effect=fake_git(refs, ancestors)), \
                patch.object(update_module, "preflight"), patch.object(update_module, "refuse_overwriting_untracked"):
            return dashboard_module._perform_git_update(str(repo), update_branch="main")

    def snapshots(self, repo):
        return list((repo / dashboard_module._UPDATE_BACKUP_ROOT).glob("pre-update-*"))

    def test_no_snapshot_when_the_checkout_is_current_or_ahead(self):
        for refs, ancestors in (({"HEAD": "aaa111", "origin/main": "aaa111"}, set()),
                                ({"HEAD": "bbb222", "origin/main": "aaa111"}, {("aaa111", "bbb222")})):
            with self.subTest(refs=refs), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                build_repo(repo)
                result = self.update(repo, refs, ancestors)
                self.assertFalse(result["updated"])
                self.assertIsNone(result["backup_path"])
                self.assertEqual(self.snapshots(repo), [])

    def test_an_update_that_moves_head_takes_a_snapshot_and_reports_a_left_out_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            build_repo(repo)
            build_database(repo)
            with patch.object(update_module, "PROJECT_DATABASE_SNAPSHOT_MAX_BYTES", 64 * 1024):
                result = self.update(repo, {"HEAD": "aaa111", "origin/main": "bbb222"}, {("aaa111", "bbb222")})
            self.assertTrue(result["updated"])
            self.assertEqual([str(path) for path in self.snapshots(repo)], [result["backup_path"]])
            self.assertEqual(result["warnings"], [])
            self.assertEqual(len(result["snapshot_warnings"]), 1)
            self.assertIn("project_automation.sqlite3", result["snapshot_warnings"][0])


if __name__ == "__main__":
    unittest.main()
