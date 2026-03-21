import unittest
import sys
import types
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module

from test_support import MemorySandboxMixin


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

        item_response = self.client.get("/api/v2/memories/auto/item?key=server%3A123%3Auser%3A456&index=0")
        item = item_response.get_json()
        self.assertEqual(item_response.status_code, 200)
        self.assertTrue(item["auto"])

        page = self.client.get("/memories").get_data(as_text=True)
        self.assertIn("Consolidation runs after 5 new auto memories for the same key.", page)
        self.assertIn("Select All Visible", page)
        self.assertIn("Delete Targeted Users", page)
        self.assertIn("Consolidate Targeted Users", page)
        self.assertIn("Delete Targeted User Lore", page)
        self.assertIn("Live JSON edits are not a supported delete path", page)
        self.assertIn("/api/v2/memories/auto", page)
        self.assertNotIn("memories.json", page)

    def test_auto_memory_api_respects_scope_server_and_user_filters(self):
        self.manager.add_auto_memory(123, 456, "Alice likes tea", user_name="Alice", server_name="Tea House")
        self.manager.add_auto_memory(0, 456, "Alice prefers DMs", user_name="Alice", server_name="DM")
        self.manager.add_auto_memory(123, 999, "Bob likes coffee", user_name="Bob", server_name="Tea House")

        response = self.client.get("/api/v2/memories/auto?scope=server&server_id=123&user_ids=456")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["memories"][0]["key"], "server:123:user:456")
        self.assertEqual(data["memories"][0]["scope"], "server")

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
        self.assertEqual(len(self.manager.auto_memories[key]), 5)
        self.assertNotEqual(data["status"], "ok")

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
