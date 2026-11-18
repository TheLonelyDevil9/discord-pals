import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import memory as memory_module
import reminders as reminders_module
import time_utils as time_utils_module


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
    """A bare remote, a seed clone that publishes upstream commits, and an install clone.

    The install clone stands in for a live checkout that the updater moves.
    """

    def setUp(self):
        self._git_temp = tempfile.TemporaryDirectory()
        self.root = Path(self._git_temp.name)
        # A developer's global or system Git config (signing, hooks, autocrlf) and the per-user
        # ignore and attributes files under the home folder must not change the results.
        empty_config = self.root / "empty.gitconfig"
        empty_config.write_text("", encoding="utf-8")
        environment = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": str(empty_config), "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(self.root), "USERPROFILE": str(self.root), "XDG_CONFIG_HOME": str(self.root),
        })
        environment.start()
        self.addCleanup(environment.stop)
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
        """Leave the install with the unmerged paths of a stash pop that conflicted."""
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


class MemorySandboxMixin:
    """Patch memory/dashboard storage paths into an isolated temp directory."""

    def setUpMemorySandbox(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._temp_dir.name)

        self._memory_originals = {
            "DATA_DIR": memory_module.DATA_DIR,
            "MEMORIES_FILE": memory_module.MEMORIES_FILE,
            "DM_MEMORIES_FILE": memory_module.DM_MEMORIES_FILE,
            "USER_MEMORIES_FILE": memory_module.USER_MEMORIES_FILE,
            "LORE_FILE": memory_module.LORE_FILE,
            "DM_MEMORIES_DIR": memory_module.DM_MEMORIES_DIR,
            "USER_MEMORIES_DIR": memory_module.USER_MEMORIES_DIR,
            "GLOBAL_USER_PROFILES_FILE": memory_module.GLOBAL_USER_PROFILES_FILE,
            "AUTO_MEMORIES_FILE": memory_module.AUTO_MEMORIES_FILE,
            "MANUAL_LORE_FILE": memory_module.MANUAL_LORE_FILE,
            "MEMORY_STATE_FILE": memory_module.MEMORY_STATE_FILE,
            "memory_manager": memory_module.memory_manager,
        }
        self._reminder_originals = {
            "REMINDERS_FILE": reminders_module.REMINDERS_FILE,
            "reminder_manager": reminders_module.reminder_manager,
        }
        self._timezone_originals = {
            "USER_TIMEZONES_FILE": time_utils_module.USER_TIMEZONES_FILE,
            "timezone_manager": time_utils_module.timezone_manager,
        }
        self._dashboard_originals = {
            "DATA_DIR": dashboard_module.DATA_DIR,
            "AUTO_MEMORIES_FILE": dashboard_module.AUTO_MEMORIES_FILE,
            "MANUAL_LORE_FILE": dashboard_module.MANUAL_LORE_FILE,
            "bot_instances": list(dashboard_module.bot_instances),
        }
        self._logger_originals = {
            "debug": memory_module.log.debug,
            "info": memory_module.log.info,
            "warn": memory_module.log.warn,
            "error": memory_module.log.error,
            "ok": memory_module.log.ok,
        }

        memory_module.DATA_DIR = str(self.data_dir)
        memory_module.MEMORIES_FILE = str(self.data_dir / "memories.json")
        memory_module.DM_MEMORIES_FILE = str(self.data_dir / "dm_memories.json")
        memory_module.USER_MEMORIES_FILE = str(self.data_dir / "user_memories.json")
        memory_module.LORE_FILE = str(self.data_dir / "lore.json")
        memory_module.DM_MEMORIES_DIR = str(self.data_dir / "dm_memories")
        memory_module.USER_MEMORIES_DIR = str(self.data_dir / "user_memories")
        memory_module.GLOBAL_USER_PROFILES_FILE = str(self.data_dir / "user_profiles.json")
        memory_module.AUTO_MEMORIES_FILE = str(self.data_dir / "auto_memories.json")
        memory_module.MANUAL_LORE_FILE = str(self.data_dir / "manual_lore.json")
        memory_module.MEMORY_STATE_FILE = str(self.data_dir / "memory_state.json")
        reminders_module.REMINDERS_FILE = str(self.data_dir / "reminders.json")
        time_utils_module.USER_TIMEZONES_FILE = str(self.data_dir / "user_timezones.json")

        dashboard_module.DATA_DIR = self.data_dir
        dashboard_module.AUTO_MEMORIES_FILE = memory_module.AUTO_MEMORIES_FILE
        dashboard_module.MANUAL_LORE_FILE = memory_module.MANUAL_LORE_FILE
        dashboard_module.bot_instances = []

        memory_module.log.debug = lambda *args, **kwargs: None
        memory_module.log.info = lambda *args, **kwargs: None
        memory_module.log.warn = lambda *args, **kwargs: None
        memory_module.log.error = lambda *args, **kwargs: None
        memory_module.log.ok = lambda *args, **kwargs: None

        self.manager = self.make_manager()
        memory_module.memory_manager = self.manager
        reminders_module.reminder_manager = reminders_module.ReminderManager()
        time_utils_module.timezone_manager = time_utils_module.TimezoneManager()

    def tearDownMemorySandbox(self):
        memory_module.DATA_DIR = self._memory_originals["DATA_DIR"]
        memory_module.MEMORIES_FILE = self._memory_originals["MEMORIES_FILE"]
        memory_module.DM_MEMORIES_FILE = self._memory_originals["DM_MEMORIES_FILE"]
        memory_module.USER_MEMORIES_FILE = self._memory_originals["USER_MEMORIES_FILE"]
        memory_module.LORE_FILE = self._memory_originals["LORE_FILE"]
        memory_module.DM_MEMORIES_DIR = self._memory_originals["DM_MEMORIES_DIR"]
        memory_module.USER_MEMORIES_DIR = self._memory_originals["USER_MEMORIES_DIR"]
        memory_module.GLOBAL_USER_PROFILES_FILE = self._memory_originals["GLOBAL_USER_PROFILES_FILE"]
        memory_module.AUTO_MEMORIES_FILE = self._memory_originals["AUTO_MEMORIES_FILE"]
        memory_module.MANUAL_LORE_FILE = self._memory_originals["MANUAL_LORE_FILE"]
        memory_module.MEMORY_STATE_FILE = self._memory_originals["MEMORY_STATE_FILE"]
        memory_module.memory_manager = self._memory_originals["memory_manager"]
        reminders_module.REMINDERS_FILE = self._reminder_originals["REMINDERS_FILE"]
        reminders_module.reminder_manager = self._reminder_originals["reminder_manager"]
        time_utils_module.USER_TIMEZONES_FILE = self._timezone_originals["USER_TIMEZONES_FILE"]
        time_utils_module.timezone_manager = self._timezone_originals["timezone_manager"]

        dashboard_module.DATA_DIR = self._dashboard_originals["DATA_DIR"]
        dashboard_module.AUTO_MEMORIES_FILE = self._dashboard_originals["AUTO_MEMORIES_FILE"]
        dashboard_module.MANUAL_LORE_FILE = self._dashboard_originals["MANUAL_LORE_FILE"]
        dashboard_module.bot_instances = self._dashboard_originals["bot_instances"]

        memory_module.log.debug = self._logger_originals["debug"]
        memory_module.log.info = self._logger_originals["info"]
        memory_module.log.warn = self._logger_originals["warn"]
        memory_module.log.error = self._logger_originals["error"]
        memory_module.log.ok = self._logger_originals["ok"]

        self._temp_dir.cleanup()

    def make_manager(self):
        return memory_module.MemoryManager()

    def replace_manager(self, manager):
        self.manager = manager
        memory_module.memory_manager = manager

    def write_json(self, relative_path: str, data):
        path = self.data_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def read_json(self, relative_path: str):
        return json.loads((self.data_dir / relative_path).read_text(encoding="utf-8"))

    def make_client(self):
        dashboard_module.app.config["TESTING"] = True
        client = dashboard_module.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "test-csrf"
        return client

    def csrf_headers(self):
        return {"X-CSRF-Token": "test-csrf"}
