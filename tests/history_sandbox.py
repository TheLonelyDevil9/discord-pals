import json
import tempfile
from pathlib import Path

import module_stubs  # noqa: F401
import discord_utils as discord_utils_module
import logger as logger_module


_HISTORY_STATE = (
    "DATA_DIR", "HISTORY_CACHE_FILE", "HISTORY_CHANNELS_DIR", "conversation_history",
    "channel_names", "_channel_last_activity", "_recent_message_hashes",
    "_dirty_history_channels", "_legacy_history_files", "_history_save_pending",
    "_history_last_save", "HISTORY_SAVE_DEBOUNCE",
)


class HistorySandbox:
    """Point the shared history store at an empty temporary directory."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.history_dir = self.data_dir / "history_channels"

        originals = {name: getattr(discord_utils_module, name) for name in _HISTORY_STATE}
        log_level = logger_module.LOG_LEVEL

        def restore():
            for name, value in originals.items():
                setattr(discord_utils_module, name, value)
            logger_module.LOG_LEVEL = log_level
            self.temp_dir.cleanup()

        self.addCleanup(restore)

        discord_utils_module.DATA_DIR = str(self.data_dir)
        discord_utils_module.HISTORY_CACHE_FILE = str(self.data_dir / "history_cache.json")
        discord_utils_module.HISTORY_CHANNELS_DIR = str(self.history_dir)
        discord_utils_module.conversation_history = {}
        discord_utils_module.channel_names = {}
        discord_utils_module._channel_last_activity = {}
        discord_utils_module._recent_message_hashes = {}
        discord_utils_module._dirty_history_channels = set()
        discord_utils_module._legacy_history_files = {}
        discord_utils_module._history_save_pending = False
        discord_utils_module._history_last_save = 0.0
        discord_utils_module.HISTORY_SAVE_DEBOUNCE = 0.0
        logger_module.LOG_LEVEL = logger_module.QUIET

    def write_file(self, name, payload):
        self.history_dir.mkdir(parents=True, exist_ok=True)
        path = self.history_dir / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def file_names(self):
        return sorted(path.name for path in self.history_dir.glob("*.json"))

    def restart(self):
        """Drop every in-memory history, then load the files the way a new process does."""
        discord_utils_module.conversation_history = {}
        discord_utils_module.channel_names = {}
        discord_utils_module._channel_last_activity = {}
        discord_utils_module._recent_message_hashes = {}
        discord_utils_module._dirty_history_channels = set()
        discord_utils_module.load_history()
