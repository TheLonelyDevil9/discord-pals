import tempfile
import sqlite3
from contextlib import closing
import unittest
from pathlib import Path

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import update as update_module


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


if __name__ == "__main__":
    unittest.main()
