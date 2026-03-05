import tempfile
import unittest
from pathlib import Path
import json
import os
import sys
import types

# memory.py imports these helpers from discord_utils; tests only need minimal behavior.
discord_utils_stub = types.ModuleType("discord_utils")
_original_discord_utils = sys.modules.get("discord_utils")


def _safe_json_load(filepath: str, default=None):
    if default is None:
        default = {}
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _safe_json_save(filepath: str, data, indent: int = 2):
    parent = os.path.dirname(filepath)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent)
    return True


def _remove_thinking_tags(text: str):
    return text if isinstance(text, str) else ""


discord_utils_stub.safe_json_load = _safe_json_load
discord_utils_stub.safe_json_save = _safe_json_save
discord_utils_stub.remove_thinking_tags = _remove_thinking_tags
sys.modules["discord_utils"] = discord_utils_stub

import memory

if _original_discord_utils is not None:
    sys.modules["discord_utils"] = _original_discord_utils
else:
    sys.modules.pop("discord_utils", None)


class StubProvider:
    def __init__(self, generated_text: str, embedding: list[float]):
        self.generated_text = generated_text
        self.embedding = list(embedding)

    async def generate(self, *args, **kwargs):
        return self.generated_text

    async def get_embedding(self, text: str):
        return list(self.embedding)


class TempMemoryEnvMixin:
    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        base = Path(self._tmpdir.name)

        self._original_paths = {
            "DATA_DIR": memory.DATA_DIR,
            "MEMORIES_FILE": memory.MEMORIES_FILE,
            "DM_MEMORIES_FILE": memory.DM_MEMORIES_FILE,
            "USER_MEMORIES_FILE": memory.USER_MEMORIES_FILE,
            "LORE_FILE": memory.LORE_FILE,
            "DM_MEMORIES_DIR": memory.DM_MEMORIES_DIR,
            "USER_MEMORIES_DIR": memory.USER_MEMORIES_DIR,
            "GLOBAL_USER_PROFILES_FILE": memory.GLOBAL_USER_PROFILES_FILE,
        }

        memory.DATA_DIR = str(base)
        memory.MEMORIES_FILE = str(base / "memories.json")
        memory.DM_MEMORIES_FILE = str(base / "dm_memories_legacy.json")
        memory.USER_MEMORIES_FILE = str(base / "user_memories_legacy.json")
        memory.LORE_FILE = str(base / "lore.json")
        memory.DM_MEMORIES_DIR = str(base / "dm_memories")
        memory.USER_MEMORIES_DIR = str(base / "user_memories")
        memory.GLOBAL_USER_PROFILES_FILE = str(base / "global_profiles.json")

        self.manager = memory.MemoryManager()
        self.manager._mark_dirty = lambda *_args, **_kwargs: None
        self.manager.flush = lambda: None

    def tearDown(self):
        for key, value in self._original_paths.items():
            setattr(memory, key, value)
        self._tmpdir.cleanup()
        super().tearDown()


class MemoryDedupTests(TempMemoryEnvMixin, unittest.IsolatedAsyncioTestCase):
    def test_semantic_duplicate_detected_even_with_low_key_term_overlap(self):
        existing = [{
            "content": "She prefers jasmine tea in the evening.",
            "embedding": [0.91, 0.09, 0.0],
        }]
        duplicate = memory._is_duplicate_memory(
            "Seele enjoys dragonfruit smoothies at sunrise.",
            existing,
            new_embedding=[0.91, 0.09, 0.0],
        )
        self.assertTrue(duplicate)

    def test_sanitize_entries_preserves_subject_user_id(self):
        cleaned = memory._sanitize_memory_entries([
            {
                "content": "Seele likes black coffee.",
                "timestamp": "2026-03-05T00:00:00",
                "subject_user_id": "42",
            }
        ])
        self.assertEqual(cleaned[0].get("subject_user_id"), 42)

    def test_cross_store_manual_duplicate_is_blocked(self):
        first = self.manager.add_user_memory(
            guild_id=1,
            user_id=42,
            content="Seele likes black coffee.",
            character_name="Fly",
            embedding=[1.0, 0.0, 0.0],
        )
        second = self.manager.add_global_user_profile(
            user_id=42,
            content="Seele enjoys black coffee.",
            character_name="Fly",
            embedding=[1.0, 0.0, 0.0],
        )
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(self.manager.global_user_profiles.get("42", [])), 0)

    def test_subject_scoped_server_memory_blocks_same_user_only(self):
        seeded = self.manager.add_server_memory(
            guild_id=123,
            content="Seele likes jazz.",
            auto=True,
            embedding=[0.2, 0.8, 0.0],
            subject_user_id=42,
        )
        blocked = self.manager.add_user_memory(
            guild_id=123,
            user_id=42,
            content="Seele loves jazz music.",
            character_name="Fly",
            embedding=[0.2, 0.8, 0.0],
        )
        allowed_other_user = self.manager.add_user_memory(
            guild_id=123,
            user_id=99,
            content="User99 loves jazz music.",
            character_name="Fly",
            embedding=[0.2, 0.8, 0.0],
        )
        self.assertTrue(seeded)
        self.assertFalse(blocked)
        self.assertTrue(allowed_other_user)

    async def test_generate_memory_skips_cross_store_semantic_duplicate(self):
        self.manager.add_global_user_profile(
            user_id=42,
            content="Seele likes black coffee.",
            auto=True,
            character_name="Fly",
            embedding=[0.6, 0.4, 0.0],
            skip_cross_store_check=True,
        )

        provider = StubProvider(
            generated_text="Seele enjoys black coffee.",
            embedding=[0.6, 0.4, 0.0],
        )
        messages = [
            {"role": "user", "author": "Seele", "content": "I like black coffee."},
            {"role": "assistant", "author": "Fly", "content": "Noted."},
            {"role": "user", "author": "Seele", "content": "I drink it every morning."},
            {"role": "assistant", "author": "Fly", "content": "Got it."},
        ]

        server_before = len(self.manager.server_memories.get("777", []))
        user_before = len(
            self.manager._get_user_memories_for_character("Fly")
            .get("777", {})
            .get("42", [])
        )
        global_before = len(self.manager.global_user_profiles.get("42", []))

        result = await self.manager.generate_memory(
            provider_manager=provider,
            messages=messages,
            is_dm=False,
            id_key=777,
            character_name="Fly",
            user_id=42,
            user_name="Seele",
            cooldown_scope_id=777,
        )

        self.assertIsNone(result)
        self.assertEqual(len(self.manager.server_memories.get("777", [])), server_before)
        self.assertEqual(
            len(self.manager._get_user_memories_for_character("Fly").get("777", {}).get("42", [])),
            user_before,
        )
        self.assertEqual(len(self.manager.global_user_profiles.get("42", [])), global_before)


if __name__ == "__main__":
    unittest.main()
