import asyncio
import unittest

import module_stubs  # noqa: F401
import memory as memory_module

from test_support import MemorySandboxMixin


class NoEmbeddingProvider:
    def __init__(self, result: str, delay: float = 0.0):
        self.result = result
        self.delay = delay
        self.generate_calls = 0
        self.embedding_calls = 0

    async def generate(self, messages, system_prompt):
        self.generate_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result

    async def get_embedding(self, text):
        self.embedding_calls += 1
        return None


class EmbeddingProvider(NoEmbeddingProvider):
    async def get_embedding(self, text):
        self.embedding_calls += 1
        return [float(len(text)), 1.0]


class MemoryManagerPersistenceTests(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()

    def tearDown(self):
        self.tearDownMemorySandbox()

    def test_add_auto_memory_persists_auto_flag_and_metadata(self):
        added = self.manager.add_auto_memory(
            server_id=123,
            user_id=456,
            content="Alice likes tea",
            character_name="firefly",
            user_name="Alice",
            server_name="Tea House",
            embedding=[0.1, 0.2, 0.3]
        )

        self.assertTrue(added)
        self.manager.save_all()

        stored = self.read_json("auto_memories.json")
        entry = stored["server:123:user:456"][0]

        self.assertTrue(entry["auto"])
        self.assertEqual(entry["user_id"], 456)
        self.assertEqual(entry["server_id"], 123)
        self.assertEqual(entry["user_name"], "Alice")
        self.assertEqual(entry["server_name"], "Tea House")
        self.assertEqual(entry["character"], "firefly")
        self.assertEqual(entry["embedding"], [0.1, 0.2, 0.3])
        self.assertTrue(entry["fingerprint"])

    def test_add_lore_persists_manual_flag(self):
        added = self.manager.add_lore("user", 456, "Alice is trusted", added_by="dashboard")

        self.assertTrue(added)
        self.manager.save_all()

        stored = self.read_json("manual_lore.json")
        entry = stored["user:456"][0]

        self.assertFalse(entry["auto"])
        self.assertEqual(entry["added_by"], "dashboard")
        self.assertTrue(entry["fingerprint"])

    def test_load_normalization_backfills_flags_and_ids_without_losing_metadata(self):
        self.write_json("auto_memories.json", {
            "server:1:user:2": [{
                "content": "  Alice likes coffee  ",
                "timestamp": "2026-01-01T00:00:00",
                "user_name": "Alice",
                "server_name": "Cafe",
                "character": "firefly"
            }]
        })
        self.write_json("manual_lore.json", {
            "user:2": [{
                "content": "  trusted ally ",
                "timestamp": "2026-01-01T00:00:00",
                "added_by": "migrated"
            }]
        })

        manager = self.make_manager()
        self.replace_manager(manager)

        auto_entry = manager.auto_memories["server:1:user:2"][0]
        lore_entry = manager.manual_lore["user:2"][0]

        self.assertEqual(auto_entry["content"], "Alice likes coffee")
        self.assertTrue(auto_entry["auto"])
        self.assertEqual(auto_entry["user_id"], 2)
        self.assertEqual(auto_entry["server_id"], 1)
        self.assertEqual(auto_entry["user_name"], "Alice")
        self.assertEqual(auto_entry["server_name"], "Cafe")
        self.assertEqual(auto_entry["character"], "firefly")
        self.assertTrue(auto_entry["fingerprint"])

        self.assertEqual(lore_entry["content"], "trusted ally")
        self.assertFalse(lore_entry["auto"])
        self.assertEqual(lore_entry["added_by"], "migrated")
        self.assertTrue(lore_entry["fingerprint"])

    def test_pending_auto_counter_survives_reload(self):
        self.manager.add_auto_memory(123, 456, "Alice likes tea")
        self.manager.add_auto_memory(123, 456, "Alice collects bookmarks")
        self.manager.save_all()

        reloaded = self.make_manager()

        self.assertEqual(reloaded._get_pending_auto_count("server:123:user:456"), 2)
        state = self.read_json("memory_state.json")
        self.assertEqual(state["pending_auto_since_dedup"]["server:123:user:456"], 2)

    def test_bulk_delete_auto_memories_across_all_scopes_clears_matching_keys_and_state(self):
        self.manager.add_auto_memory(123, 456, "Alice likes tea")
        self.manager.add_auto_memory(0, 456, "Alice prefers DMs")
        self.manager.add_auto_memory(999, 777, "Bob likes coffee")
        self.manager.save_all()

        result = self.manager.bulk_delete_auto_memories([456], scope_mode="all")
        self.manager.save_all()

        self.assertEqual(result["affected_keys"], 2)
        self.assertEqual(result["deleted"], 2)
        self.assertNotIn("server:123:user:456", self.manager.auto_memories)
        self.assertNotIn("dm:0:user:456", self.manager.auto_memories)
        self.assertIn("server:999:user:777", self.manager.auto_memories)

        state = self.read_json("memory_state.json")
        self.assertNotIn("server:123:user:456", state["pending_auto_since_dedup"])
        self.assertNotIn("dm:0:user:456", state["pending_auto_since_dedup"])
        self.assertEqual(state["pending_auto_since_dedup"]["server:999:user:777"], 1)

    def test_bulk_delete_auto_memories_for_one_server_preserves_other_scopes(self):
        self.manager.add_auto_memory(123, 456, "Alice likes tea")
        self.manager.add_auto_memory(999, 456, "Alice likes coffee")
        self.manager.add_auto_memory(0, 456, "Alice sends DMs")

        result = self.manager.bulk_delete_auto_memories([456], scope_mode="server", server_id=123)

        self.assertEqual(result["affected_keys"], 1)
        self.assertEqual(result["deleted"], 1)
        self.assertNotIn("server:123:user:456", self.manager.auto_memories)
        self.assertIn("server:999:user:456", self.manager.auto_memories)
        self.assertIn("dm:0:user:456", self.manager.auto_memories)

    def test_bulk_delete_user_lore_only_removes_user_keys(self):
        self.manager.add_lore("user", 456, "Alice is trusted", added_by="dashboard")
        self.manager.add_lore("server", 123, "Tea House allows roleplay", added_by="dashboard")
        self.manager.add_lore("bot", "Firefly", "Speaks softly", added_by="dashboard")

        result = self.manager.bulk_delete_user_lore([456])

        self.assertEqual(result["affected_keys"], 1)
        self.assertEqual(result["deleted"], 1)
        self.assertNotIn("user:456", self.manager.manual_lore)
        self.assertIn("server:123", self.manager.manual_lore)
        self.assertIn("bot:Firefly", self.manager.manual_lore)


class MemoryManagerAsyncTests(MemorySandboxMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setUpMemorySandbox()

    def tearDown(self):
        self.tearDownMemorySandbox()

    def seed_user_memories(self, manager):
        facts = [
            "Alice likes green tea in the morning",
            "Alice enjoys jasmine tea while reading",
            "Alice prefers tea over coffee at work",
            "Alice drinks tea to relax after work",
            "Alice keeps loose-leaf tea at home",
        ]
        for fact in facts:
            added = manager.add_auto_memory(123, 456, fact)
            self.assertTrue(added)

    async def test_llm_dedup_resets_counter_and_prevents_concurrent_runs(self):
        self.seed_user_memories(self.manager)
        key = "server:123:user:456"
        provider = NoEmbeddingProvider("Alice likes tea", delay=0.01)

        self.assertEqual(self.manager._get_pending_auto_count(key), 5)
        self.assertTrue(self.manager.should_llm_deduplicate(key))

        await asyncio.gather(
            self.manager.llm_deduplicate(key, provider),
            self.manager.llm_deduplicate(key, provider),
        )

        self.assertEqual(provider.generate_calls, 1)
        self.assertEqual(self.manager._get_pending_auto_count(key), 0)
        self.assertEqual(len(self.manager.auto_memories[key]), 1)
        self.assertTrue(self.manager.auto_memories[key][0]["auto"])
        self.assertNotIn(key, self.manager._dedup_in_flight)

    async def test_llm_dedup_keeps_auto_flag_and_refreshes_embeddings(self):
        self.seed_user_memories(self.manager)
        key = "server:123:user:456"
        provider = EmbeddingProvider("Alice likes tea\nAlice keeps tea at home")

        await self.manager.llm_deduplicate(key, provider)

        entries = self.manager.auto_memories[key]
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(entry["auto"] for entry in entries))
        self.assertTrue(all(isinstance(entry.get("embedding"), list) for entry in entries))
        self.assertEqual(self.manager._get_pending_auto_count(key), 0)

    async def test_llm_dedup_still_consolidates_without_embeddings(self):
        self.seed_user_memories(self.manager)
        key = "server:123:user:456"
        provider = NoEmbeddingProvider("Alice likes tea\nAlice relaxes with tea")

        await self.manager.llm_deduplicate(key, provider)

        self.assertEqual(len(self.manager.auto_memories[key]), 2)
        self.assertEqual(self.manager._get_pending_auto_count(key), 0)
        self.assertTrue(all(entry["auto"] for entry in self.manager.auto_memories[key]))
        self.assertTrue(self.manager._embedding_unavailable_logged)

    async def test_manual_consolidate_rewrites_matching_keys_and_resets_counters(self):
        self.seed_user_memories(self.manager)
        key = "server:123:user:456"
        provider = EmbeddingProvider("Alice likes tea\nAlice keeps tea at home")

        result = await self.manager.consolidate_auto_memory_keys([key], provider)

        self.assertEqual(result["matched_keys"], 1)
        self.assertEqual(result["consolidated"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["already_running"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(len(self.manager.auto_memories[key]), 2)
        self.assertTrue(all(entry["auto"] for entry in self.manager.auto_memories[key]))
        self.assertEqual(self.manager._get_pending_auto_count(key), 0)

    async def test_manual_consolidate_skips_small_and_inflight_keys(self):
        busy_key = "server:123:user:456"
        self.seed_user_memories(self.manager)
        self.manager.add_auto_memory(123, 999, "Bob likes games")
        self.manager.add_auto_memory(123, 999, "Bob likes pizza")
        small_key = "server:123:user:999"
        self.manager._dedup_in_flight.add(busy_key)
        provider = NoEmbeddingProvider("Unused")

        result = await self.manager.consolidate_auto_memory_keys([busy_key, small_key], provider)

        self.assertEqual(result["matched_keys"], 2)
        self.assertEqual(result["consolidated"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["already_running"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(provider.generate_calls, 0)
        self.manager._dedup_in_flight.discard(busy_key)
