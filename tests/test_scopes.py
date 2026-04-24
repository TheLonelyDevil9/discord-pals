import unittest

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
import discord_utils
import memory
from scopes import (
    MemoryScope,
    RequestContext,
    auto_memory_key,
    channel_display_label,
    conversation_history_id,
    dm_auto_memory_key,
    dm_history_id,
    dm_memory_server_id,
    memory_server_id,
)


class ScopeHelperTests(unittest.TestCase):
    def test_dm_history_is_isolated_by_bot_and_user(self):
        self.assertEqual(dm_history_id("Alpha Bot", 42), "dm:Alpha-Bot:user:42")
        self.assertNotEqual(dm_history_id("Alpha Bot", 42), dm_history_id("Beta Bot", 42))
        self.assertNotEqual(dm_history_id("Alpha Bot", 42), dm_history_id("Alpha Bot", 99))
        self.assertEqual(discord_utils.dm_history_key("Alpha Bot", 42), dm_history_id("Alpha Bot", 42))

    def test_memory_scope_is_isolated_by_bot_and_user(self):
        alpha_scope = dm_memory_server_id("Alpha Bot")
        beta_scope = dm_memory_server_id("Beta Bot")

        self.assertEqual(alpha_scope, "dm:bot:Alpha-Bot")
        self.assertNotEqual(alpha_scope, beta_scope)
        self.assertEqual(dm_auto_memory_key("Alpha Bot", 42), "dm:bot:Alpha-Bot:user:42")
        self.assertNotEqual(dm_auto_memory_key("Alpha Bot", 42), dm_auto_memory_key("Beta Bot", 42))
        self.assertNotEqual(dm_auto_memory_key("Alpha Bot", 42), dm_auto_memory_key("Alpha Bot", 99))
        self.assertEqual(memory.get_dm_server_id_for_bot("Alpha Bot"), alpha_scope)
        self.assertEqual(memory.get_dm_auto_memory_key("Alpha Bot", 42), dm_auto_memory_key("Alpha Bot", 42))

    def test_server_scopes_stay_numeric_and_readable(self):
        self.assertEqual(conversation_history_id("Alpha", 123, is_dm=False), 123)
        self.assertEqual(memory_server_id("Alpha", 456, is_dm=False), 456)
        self.assertEqual(auto_memory_key(456, 42), "server:456:user:42")
        self.assertEqual(channel_display_label("general", "Guild", is_dm=False), "#general (Guild)")
        self.assertEqual(channel_display_label(None, is_dm=True), "DM")

    def test_typed_request_context_aligns_history_and_memory_scope(self):
        context = RequestContext(
            bot_name="Alpha Bot",
            user_id=42,
            user_name="Alice",
            discord_channel_id=777,
            history_id=conversation_history_id("Alpha Bot", 777, is_dm=True, user_id=42),
            memory_scope=MemoryScope(server_id=memory_server_id("Alpha Bot", None, is_dm=True), user_id=42),
            display_label=channel_display_label(None, is_dm=True),
            is_dm=True,
        )

        self.assertEqual(context.history_id, "dm:Alpha-Bot:user:42")
        self.assertEqual(context.memory_scope.auto_key, "dm:bot:Alpha-Bot:user:42")
        self.assertEqual(context.display_label, "DM")


class BotInstanceScopeTests(unittest.TestCase):
    def test_bot_instance_conversation_key_uses_bot_specific_dm_scope(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Alpha Bot"

        dm_channel = bot_instance_module.discord.DMChannel()
        dm_channel.id = 777
        message = type("Message", (), {"channel": dm_channel})()

        self.assertEqual(instance._conversation_key(message, 42), "dm:Alpha-Bot:user:42")
        self.assertEqual(instance._dm_memory_server_id(), "dm:bot:Alpha-Bot")


if __name__ == "__main__":
    unittest.main()

