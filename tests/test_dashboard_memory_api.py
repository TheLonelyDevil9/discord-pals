import unittest

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
        self.assertIn("/api/v2/memories/auto", page)
        self.assertNotIn("memories.json", page)
