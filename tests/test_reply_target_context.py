"""Regression tests for the message a queued request is answering.

Channel history is shared by every bot in the process, so it keeps growing while
a queued request waits its turn. The reply is attached to the message the request
was queued for, so the model's newest turn has to be that same message.
"""

import types
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
import discord_utils as discord_utils_module


def _entry(message_id, author, content, user_id=None, role="user"):
    entry = {"role": role, "content": content, "message_id": message_id}
    if author:
        entry["author"] = author
    if user_id:
        entry["user_id"] = user_id
    return entry


# Mirrors the reported thread: two people reply to the bot moments apart, and a
# third message lands while the first reply is still queued.
SHARED_HISTORY = [
    _entry(1, "TheLonelyWaWa", "Wow how do you know about maggot cheese?", user_id=42),
    _entry(2, "Fly", "Sandrone brought it up a while back.", role="assistant"),
    _entry(4, "Seele WaWa", "\N{FACE SCREAMING IN FEAR}", user_id=43),
    _entry(6, "TheLonelyWaWa", "You became chums with her in secret after that cake baking argument is it", user_id=42),
    _entry(7, "Seele WaWa", "Noooo", user_id=43),
]


class HistoryThroughMessageTests(unittest.TestCase):
    def test_cuts_entries_newer_than_the_requested_message(self):
        window = discord_utils_module.history_through_message(SHARED_HISTORY, 4)

        self.assertEqual([entry["message_id"] for entry in window], [1, 2, 4])

    def test_keeps_full_history_when_the_message_is_absent(self):
        window = discord_utils_module.history_through_message(SHARED_HISTORY, 999)

        self.assertEqual(window, SHARED_HISTORY)

    def test_keeps_full_history_without_a_message_id(self):
        window = discord_utils_module.history_through_message(SHARED_HISTORY, None)

        self.assertEqual(window, SHARED_HISTORY)

    def test_format_history_split_ends_on_the_requested_message(self):
        with patch.object(discord_utils_module, "get_history", return_value=list(SHARED_HISTORY)):
            _, immediate = discord_utils_module.format_history_split(
                77,
                total_limit=200,
                immediate_count=5,
                current_bot_name="Fly",
                up_to_message_id=4,
            )

        self.assertTrue(immediate[-1]["content"].endswith("\N{FACE SCREAMING IN FEAR}"))
        self.assertNotIn("You became chums", " ".join(item["content"] for item in immediate))


class RequestContextReplyTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_newest_model_turn_is_the_message_being_replied_to(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Fly"
        instance.character_name = "fly"
        instance.character = types.SimpleNamespace(name="Fly", example_dialogue="")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
        instance._processed_message_ids = set()
        instance._gather_mentioned_user_context = AsyncMock(return_value="")

        channel = types.SimpleNamespace(id=77, name="general")
        guild = types.SimpleNamespace(id=5, name="WaWa")
        message = types.SimpleNamespace(
            id=4,
            content="\N{FACE SCREAMING IN FEAR}",
            channel=channel,
            guild=guild,
            mentions=[],
            author=types.SimpleNamespace(id=43, bot=False),
            reference=None,
        )
        request = {
            "message": message,
            "content": message.content,
            "guild": guild,
            "attachments": [],
            "user_name": "Seele WaWa",
            "is_dm": False,
            "user_id": 43,
            "sticker_info": None,
        }
        runtime_values = {"allow_bot_mentions": False, "immediate_message_count": 5}

        with ExitStack() as stack:
            stack.enter_context(patch.object(bot_instance_module, "get_history", return_value=list(SHARED_HISTORY)))
            stack.enter_context(patch.object(discord_utils_module, "get_history", return_value=list(SHARED_HISTORY)))
            stack.enter_context(patch.object(bot_instance_module, "was_recently_cleared", return_value=False))
            stack.enter_context(patch.object(bot_instance_module, "acknowledge_cleared"))
            stack.enter_context(patch.object(bot_instance_module, "add_to_history"))
            stack.enter_context(patch.object(bot_instance_module, "set_channel_name"))
            stack.enter_context(patch.object(bot_instance_module.stats_manager, "record_message"))
            stack.enter_context(patch.object(bot_instance_module.metrics_manager, "record_message"))
            stack.enter_context(patch.object(bot_instance_module, "get_guild_emojis", return_value=""))
            stack.enter_context(patch.object(bot_instance_module.memory_manager, "get_server_lore", return_value=""))
            stack.enter_context(patch.object(bot_instance_module.memory_manager, "get_bot_lore", return_value=""))
            stack.enter_context(patch.object(bot_instance_module.memory_manager, "get_all_memories_for_context", Mock(return_value="")))
            stack.enter_context(patch.object(bot_instance_module, "get_active_users", return_value=[]))
            stack.enter_context(patch.object(bot_instance_module.character_manager, "build_system_prompt", Mock(return_value="SYSTEM")))
            stack.enter_context(patch.object(bot_instance_module.character_manager, "build_chatroom_context", Mock(return_value="CHATROOM")))
            stack.enter_context(patch.object(bot_instance_module, "get_other_bot_names", return_value=[]))
            stack.enter_context(patch.object(
                bot_instance_module.runtime_config,
                "get",
                side_effect=lambda key, default=None: runtime_values.get(key, default)
            ))
            stack.enter_context(patch.object(bot_instance_module.log, "info"))
            stack.enter_context(patch.object(bot_instance_module.log, "warn"))
            stack.enter_context(patch.object(bot_instance_module.log, "debug"))
            context = await instance._build_request_context(request)

        self.assertIsNotNone(context)
        rendered = context["messages_for_api"]
        newest_turn = next(item for item in reversed(rendered) if item.get("role") != "system")
        self.assertTrue(newest_turn["content"].endswith("\N{FACE SCREAMING IN FEAR}"))

        transcript = " ".join(str(item.get("content", "")) for item in rendered)
        self.assertNotIn("You became chums", transcript)
        self.assertNotIn("Noooo", transcript)


if __name__ == "__main__":
    unittest.main()
