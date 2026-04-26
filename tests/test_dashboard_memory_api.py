import unittest
import sys
import types
import re
import asyncio
import threading
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import character as character_module
import stats as stats_module

from test_support import MemorySandboxMixin


class DashboardEmbeddingProvider:
    providers = {"test": object()}

    def __init__(self, result: str):
        self.result = result

    async def generate(self, messages, system_prompt):
        return self.result

    async def get_embedding(self, text):
        return [float(len(text)), 1.0]


class DashboardMemoryApiTests(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()

    def tearDown(self):
        self.tearDownMemorySandbox()

    def test_stats_use_unified_stores_and_ignore_legacy_files(self):
        self.manager.add_auto_memory(123, 456, "Alice likes tea")
        self.manager.add_auto_memory(0, 456, "Alice prefers DMs")
        self.manager.add_lore("user", 456, "Alice is trusted", added_by="dashboard")
        self.manager.save_all()

        self.write_json("memories.json", {"legacy": [{"content": "legacy memory", "auto": False}]})
        self.write_json("user_profiles.json", {"legacy": [{"content": "legacy profile", "auto": False}]})

        response = self.client.get("/api/memories/stats")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["auto"], 2)
        self.assertEqual(data["manual"], 1)
        self.assertEqual(set(data["by_type"].keys()), {"auto_memories", "manual_lore"})
        self.assertEqual(set(dashboard_module.get_memory_files().keys()), {"auto_memories", "manual_lore"})

    def test_auto_memory_api_and_page_use_unified_store(self):
        self.manager.add_auto_memory(
            server_id=123,
            user_id=456,
            content="Alice likes tea",
            user_name="Alice",
            server_name="Tea House"
        )
        self.manager.save_all()

        self.write_json("memories.json", {"legacy": [{"content": "legacy memory", "auto": False}]})

        response = self.client.get("/api/v2/memories/auto")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["total"], 1)
        self.assertTrue(data["memories"][0]["auto"])
        self.assertEqual(data["memories"][0]["scope"], "server")
        self.assertEqual(data["memories"][0]["key"], "server:123:user:456")
        self.assertEqual(data["memories"][0]["entry_type"], "profile")
        self.assertFalse(data["memories"][0]["needs_merge"])
        self.assertEqual(data["memories"][0]["profile_index"], 0)
        self.assertIsNone(data["memories"][0]["pending_index"])

        item_response = self.client.get("/api/v2/memories/auto/item?key=server%3A123%3Auser%3A456&index=0")
        item = item_response.get_json()
        self.assertEqual(item_response.status_code, 200)
        self.assertTrue(item["auto"])

        page = self.client.get("/memories").get_data(as_text=True)
        self.assertIn("Auto Memory Profiles", page)
        self.assertIn("A single pending entry appears only while a merge is waiting to succeed.", page)
        self.assertIn("Select All Visible", page)
        self.assertIn("Delete Targeted Users", page)
        self.assertIn("Merge Targeted Users", page)
        self.assertIn("Merge Now", page)
        self.assertIn("Delete Targeted User Lore", page)
        self.assertIn("Live JSON edits are not a supported delete path", page)
        self.assertIn("/api/v2/memories/auto", page)
        self.assertIn("/api/v2/memories/targets", page)
        self.assertNotIn("memories.json", page)

    def test_auto_memory_api_respects_scope_server_and_user_filters(self):
        self.manager.add_auto_memory(123, 456, "Alice likes tea", user_name="Alice", server_name="Tea House")
        self.manager.add_auto_memory(0, 456, "Alice prefers DMs", user_name="Alice", server_name="DM")
        self.manager.add_auto_memory("dm:bot:Nahida", 456, "Alice asks Nahida for gentle nudges", user_name="Alice")
        self.manager.add_auto_memory(123, 999, "Bob likes coffee", user_name="Bob", server_name="Tea House")

        response = self.client.get("/api/v2/memories/auto?scope=server&server_id=123&user_ids=456")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["memories"][0]["key"], "server:123:user:456")
        self.assertEqual(data["memories"][0]["scope"], "server")

        dm_response = self.client.get("/api/v2/memories/auto?scope=dm&user_ids=456&search=gentle")
        dm_data = dm_response.get_json()

        self.assertEqual(dm_response.status_code, 200)
        self.assertEqual(dm_data["total"], 1)
        self.assertEqual(dm_data["memories"][0]["key"], "dm:bot:Nahida:user:456")
        self.assertEqual(dm_data["memories"][0]["scope"], "dm")
        self.assertEqual(dm_data["memories"][0]["scope_label"], "DM (Nahida)")

    def test_auto_memory_bulk_delete_by_user_returns_counts(self):
        self.manager.add_auto_memory(123, 456, "Alice likes tea")
        self.manager.add_auto_memory(0, 456, "Alice prefers DMs")
        self.manager.add_auto_memory(123, 999, "Bob likes coffee")

        response = self.client.post(
            "/api/v2/memories/auto/bulk-delete",
            json={"user_ids": [456], "scope_mode": "all"},
            headers=self.csrf_headers()
        )
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["affected_keys"], 2)
        self.assertEqual(data["deleted"], 2)
        self.assertNotIn("server:123:user:456", self.manager.auto_memories)
        self.assertNotIn("dm:0:user:456", self.manager.auto_memories)
        self.assertIn("server:123:user:999", self.manager.auto_memories)

    def test_auto_memory_consolidate_requires_runtime_and_leaves_data_unchanged(self):
        key = "server:123:user:456"
        for fact in [
            "Alice likes green tea in the morning",
            "Alice enjoys jasmine tea while reading",
            "Alice prefers tea over coffee at work",
            "Alice drinks tea to relax after work",
            "Alice keeps loose-leaf tea at home",
        ]:
            self.manager.add_auto_memory(123, 456, fact)

        fake_providers = types.ModuleType("providers")
        fake_providers.provider_manager = types.SimpleNamespace(providers={})
        with patch.dict(sys.modules, {"providers": fake_providers}):
            response = self.client.post(
                "/api/v2/memories/auto/consolidate",
                json={"user_ids": [456], "scope_mode": "all"},
                headers=self.csrf_headers()
            )

        data = response.get_json()
        self.assertEqual(response.status_code, 503)
        self.assertEqual([entry["entry_type"] for entry in self.manager.auto_memories[key]], ["profile", "pending"])
        self.assertNotEqual(data["status"], "ok")

    def test_auto_memory_api_exposes_pending_metadata(self):
        self.manager.add_auto_memory(123, 456, "Alice likes tea", user_name="Alice", server_name="Tea House")
        self.manager.add_auto_memory(123, 456, "Alice collects bookmarks", user_name="Alice", server_name="Tea House")

        response = self.client.get("/api/v2/memories/auto")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["total"], 1)
        memory = data["memories"][0]
        self.assertEqual(memory["entry_type"], "profile")
        self.assertTrue(memory["needs_merge"])
        self.assertEqual(memory["entry_count"], 2)
        self.assertEqual(memory["profile_index"], 0)
        self.assertEqual(memory["pending_index"], 1)
        self.assertIn("bookmarks", memory["pending_content"])

    def test_auto_memory_consolidate_accepts_specific_key(self):
        key = "server:123:user:456"
        self.manager.add_auto_memory(123, 456, "Alice likes tea", user_name="Alice")
        self.manager.add_auto_memory(123, 456, "Alice collects bookmarks", user_name="Alice")

        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        fake_providers = types.ModuleType("providers")
        fake_providers.provider_manager = DashboardEmbeddingProvider("Alice likes tea and collects bookmarks")

        try:
            with patch.dict(sys.modules, {"providers": fake_providers}), patch.object(
                dashboard_module,
                "_get_live_bot_loop",
                return_value=loop,
            ):
                response = self.client.post(
                    "/api/v2/memories/auto/consolidate",
                    json={"keys": [key]},
                    headers=self.csrf_headers()
                )
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)
            loop.close()

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["matched_keys"], 1)
        self.assertEqual(data["consolidated"], 1)
        self.assertEqual(len(self.manager.auto_memories[key]), 1)
        self.assertEqual(self.manager.auto_memories[key][0]["entry_type"], "profile")
        self.assertIn("bookmarks", self.manager.auto_memories[key][0]["content"])

    def test_auto_memory_edit_and_delete_target_pending_entry(self):
        key = "server:123:user:456"
        self.manager.add_auto_memory(123, 456, "Alice likes tea", user_name="Alice")
        self.manager.add_auto_memory(123, 456, "Alice collects bookmarks", user_name="Alice")

        edit_response = self.client.put(
            "/api/v2/memories/auto/item",
            json={"key": key, "index": 1, "content": "Alice keeps a bookmark journal"},
            headers=self.csrf_headers()
        )
        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(self.manager.auto_memories[key][1]["entry_type"], "pending")
        self.assertIn("bookmark journal", self.manager.auto_memories[key][1]["content"])

        delete_response = self.client.delete(
            "/api/v2/memories/auto/item",
            json={"key": key, "index": 1},
            headers=self.csrf_headers()
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(len(self.manager.auto_memories[key]), 1)
        self.assertEqual(self.manager.auto_memories[key][0]["entry_type"], "profile")

    def test_lore_filters_and_bulk_delete_user_targets_only(self):
        self.manager.add_lore("user", 456, "Alice is trusted", added_by="dashboard")
        self.manager.add_lore("user", 999, "Bob is playful", added_by="dashboard")
        self.manager.add_lore("server", 123, "Tea House allows roleplay", added_by="dashboard")

        response = self.client.get("/api/v2/memories/lore?type=user&target_ids=456")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["lore"][0]["key"], "user:456")

        delete_response = self.client.post(
            "/api/v2/memories/lore/bulk-delete",
            json={"user_ids": [456]},
            headers=self.csrf_headers()
        )
        delete_data = delete_response.get_json()

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_data["affected_keys"], 1)
        self.assertEqual(delete_data["deleted"], 1)
        self.assertNotIn("user:456", self.manager.manual_lore)
        self.assertIn("user:999", self.manager.manual_lore)
        self.assertIn("server:123", self.manager.manual_lore)

    def test_memory_target_lists_only_include_active_users(self):
        self.manager.add_auto_memory(123, 456, "Alice likes tea", user_name="Alice", server_name="Tea House")
        self.manager.add_auto_memory(123, 999, "Bob likes coffee", user_name="Bob", server_name="Tea House")
        self.manager.add_lore("user", 777, "Carol is trusted", added_by="dashboard")

        with patch.object(stats_module.stats_manager, "get_user_name", side_effect=lambda user_id: {
            777: "Carol",
            888: "Ghost",
        }.get(user_id)):
            response = self.client.get("/api/v2/memories/targets")

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual({item["user_id"] for item in data["auto_users"]}, {456, 999})
        self.assertEqual({item["user_id"] for item in data["lore_users"]}, {777})
        self.assertNotIn(888, {item["user_id"] for item in data["lore_users"]})

    def test_users_drop_from_target_lists_after_last_matching_memory_is_deleted(self):
        self.manager.add_auto_memory(123, 456, "Alice likes tea", user_name="Alice", server_name="Tea House")
        self.manager.add_lore("user", 456, "Alice is trusted", added_by="dashboard")

        before = self.client.get("/api/v2/memories/targets").get_json()
        self.assertIn(456, {item["user_id"] for item in before["auto_users"]})
        self.assertIn(456, {item["user_id"] for item in before["lore_users"]})

        auto_delete = self.client.delete(
            "/api/v2/memories/auto/item",
            json={"key": "server:123:user:456", "index": 0},
            headers=self.csrf_headers()
        )
        lore_delete = self.client.post(
            "/api/v2/memories/lore/bulk-delete",
            json={"user_ids": [456]},
            headers=self.csrf_headers()
        )
        after = self.client.get("/api/v2/memories/targets").get_json()

        self.assertEqual(auto_delete.status_code, 200)
        self.assertEqual(lore_delete.status_code, 200)
        self.assertNotIn(456, {item["user_id"] for item in after["auto_users"]})
        self.assertNotIn(456, {item["user_id"] for item in after["lore_users"]})


class DashboardConfigPageTests(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()

        self._dashboard_characters_dir = dashboard_module.CHARACTERS_DIR
        self._character_characters_dir = character_module.CHARACTERS_DIR
        self._character_cache = dict(character_module.character_manager.characters)

        self.characters_dir = self.data_dir / "characters"
        self.characters_dir.mkdir(parents=True, exist_ok=True)
        (self.characters_dir / "nilou.md").write_text("# Nilou\n\n## Persona\n\nGraceful dancer", encoding="utf-8")
        (self.characters_dir / "nahida.md").write_text("# Nahida\n\n## Persona\n\nWise archon", encoding="utf-8")

        dashboard_module.CHARACTERS_DIR = self.characters_dir
        character_module.CHARACTERS_DIR = str(self.characters_dir)
        character_module.character_manager.characters = {}

        class FakeClient:
            def is_ready(self):
                return False

        self.nahida_bot = types.SimpleNamespace(
            name="Nahida",
            character=types.SimpleNamespace(name="Nahida"),
            character_name="nahida",
            client=FakeClient(),
            nicknames=""
        )
        self.nilou_bot = types.SimpleNamespace(
            name="Nilou",
            character=types.SimpleNamespace(name="Nilou"),
            character_name="nilou",
            client=FakeClient(),
            nicknames=""
        )
        dashboard_module.bot_instances = [self.nahida_bot, self.nilou_bot]

    def tearDown(self):
        dashboard_module.CHARACTERS_DIR = self._dashboard_characters_dir
        character_module.CHARACTERS_DIR = self._character_characters_dir
        character_module.character_manager.characters = self._character_cache
        self.tearDownMemorySandbox()

    def test_config_page_selects_character_using_stable_key(self):
        page = self.client.get("/config").get_data(as_text=True)

        nahida_select = re.search(r'<select id="char-Nahida"[^>]*>(.*?)</select>', page, re.S)
        nilou_select = re.search(r'<select id="char-Nilou"[^>]*>(.*?)</select>', page, re.S)

        self.assertIsNotNone(nahida_select)
        self.assertIsNotNone(nilou_select)
        self.assertIn('value="nahida" selected', nahida_select.group(1))
        self.assertIn('value="nilou" selected', nilou_select.group(1))

    def test_switch_character_updates_bot_character_key(self):
        response = self.client.post(
            "/api/switch_character",
            json={"bot_name": "Nahida", "character": "nilou"},
            headers=self.csrf_headers()
        )
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(self.nahida_bot.character.name, "Nilou")
        self.assertEqual(self.nahida_bot.character_name, "nilou")
