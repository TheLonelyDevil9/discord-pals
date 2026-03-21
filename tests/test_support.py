import json
import tempfile
from pathlib import Path

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import memory as memory_module


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
