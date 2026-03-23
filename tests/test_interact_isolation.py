import types
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
from commands.fun import handle_interact_command


class InteractCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_interact_command_pins_target_to_invoking_user(self):
        interaction = types.SimpleNamespace(
            id=555,
            response=types.SimpleNamespace(defer=AsyncMock()),
            followup=types.SimpleNamespace(send=AsyncMock()),
            user=types.SimpleNamespace(id=42, display_name="Invoker", name="Invoker"),
            guild=None,
            channel=types.SimpleNamespace(id=77)
        )
        bot_instance = types.SimpleNamespace(
            character=types.SimpleNamespace(name="Nahida"),
            request_queue=types.SimpleNamespace(add_request=AsyncMock())
        )

        with patch("commands.fun.get_user_display_name", return_value="Invoker"):
            await handle_interact_command(bot_instance, interaction, "hugs you")

        _, kwargs = bot_instance.request_queue.add_request.call_args
        self.assertTrue(kwargs["from_interact_command"])
        self.assertEqual(kwargs["forced_target_user_id"], 42)
        self.assertEqual(kwargs["forced_target_user_name"], "Invoker")
        self.assertEqual(kwargs["content"], "*hugs you*")


class InteractContextIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_request_context_ignores_other_thread_for_interact(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character_name = "nahida"
        instance.character = types.SimpleNamespace(name="Nahida", example_dialogue="")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
        instance._processed_message_ids = set()
        instance._gather_mentioned_user_context = AsyncMock(return_value="")

        channel = types.SimpleNamespace(id=77, name="tea-room")
        guild = types.SimpleNamespace(id=5, name="Sumeru")
        message = types.SimpleNamespace(
            id=1234,
            content="*hugs you*",
            channel=channel,
            guild=guild,
            mentions=[]
        )
        other_target = types.SimpleNamespace(id=900, display_name="OtherUser", name="OtherUser")
        request = {
            "message": message,
            "content": "*hugs you*",
            "guild": guild,
            "attachments": [],
            "user_name": "Invoker",
            "is_dm": False,
            "user_id": 42,
            "sticker_info": None,
            "from_interact_command": True,
            "split_reply_target": other_target,
            "forced_target_user_id": 42,
            "forced_target_user_name": "Invoker",
        }
        raw_history = [
            {"role": "user", "content": "Hello there", "author": "OtherUser", "user_id": 900},
            {"role": "assistant", "content": "Hi, OtherUser.", "author": "Nahida"},
            {"role": "user", "content": "*waves*", "author": "Invoker", "user_id": 42},
            {"role": "assistant", "content": "*waves back*", "author": "Nahida"},
            {"role": "user", "content": "beep boop", "author": "OtherBot", "user_id": 777, "is_bot": True},
            {"role": "user", "content": "*hugs you*", "author": "Invoker", "user_id": 42, "message_id": 1234},
        ]

        runtime_values = {
            "user_only_context": True,
            "user_only_context_count": 20,
            "allow_bot_mentions": False,
        }
        memories_mock = Mock(return_value="")
        build_system_prompt_mock = Mock(return_value="SYSTEM")
        build_chatroom_context_mock = Mock(return_value="CHATROOM")
        format_history_split_mock = Mock()

        with ExitStack() as stack:
            stack.enter_context(patch.object(bot_instance_module, "get_history", return_value=raw_history))
            stack.enter_context(patch.object(bot_instance_module, "was_recently_cleared", return_value=False))
            stack.enter_context(patch.object(bot_instance_module, "acknowledge_cleared"))
            stack.enter_context(patch.object(bot_instance_module, "add_to_history"))
            stack.enter_context(patch.object(bot_instance_module, "set_channel_name"))
            stack.enter_context(patch.object(bot_instance_module.stats_manager, "record_message"))
            stack.enter_context(patch.object(bot_instance_module.metrics_manager, "record_message"))
            stack.enter_context(patch.object(bot_instance_module, "get_guild_emojis", return_value=""))
            stack.enter_context(patch.object(bot_instance_module.memory_manager, "get_server_lore", return_value=""))
            stack.enter_context(patch.object(bot_instance_module.memory_manager, "get_bot_lore", return_value=""))
            stack.enter_context(patch.object(bot_instance_module.memory_manager, "get_all_memories_for_context", memories_mock))
            stack.enter_context(patch.object(bot_instance_module, "get_active_users", return_value=[]))
            stack.enter_context(patch.object(bot_instance_module.character_manager, "build_system_prompt", build_system_prompt_mock))
            stack.enter_context(patch.object(bot_instance_module.character_manager, "build_chatroom_context", build_chatroom_context_mock))
            stack.enter_context(patch.object(bot_instance_module, "get_other_bot_names", return_value=[]))
            stack.enter_context(patch.object(
                bot_instance_module.runtime_config,
                "get",
                side_effect=lambda key, default=None: runtime_values.get(key, default)
            ))
            stack.enter_context(patch.object(bot_instance_module.provider_manager, "can_use_vision", return_value=False))
            stack.enter_context(patch.object(bot_instance_module, "format_history_split", format_history_split_mock))
            stack.enter_context(patch.object(bot_instance_module.log, "info"))
            stack.enter_context(patch.object(bot_instance_module.log, "warn"))
            stack.enter_context(patch.object(bot_instance_module.log, "debug"))
            context = await instance._build_request_context(request)

        self.assertIsNotNone(context)
        self.assertIsNone(context["split_reply_target"])
        self.assertTrue(context["from_interact_command"])
        memories_mock.assert_called_once_with(5, 42, "Invoker")
        build_system_prompt_mock.assert_called_once_with(
            character=instance.character,
            user_name="Invoker"
        )
        build_chatroom_context_mock.assert_called_once()
        format_history_split_mock.assert_not_called()

        rendered_messages = context["messages_for_api"]
        self.assertEqual(rendered_messages[0]["content"], "CHATROOM")
        rendered_text = "\n".join(
            msg["content"]
            for msg in rendered_messages[1:]
            if isinstance(msg.get("content"), str)
        )
        self.assertIn("Invoker: *waves*", rendered_text)
        self.assertIn("*waves back*", rendered_text)
        self.assertIn("Invoker: *hugs you*", rendered_text)
        self.assertNotIn("OtherUser", rendered_text)
        self.assertNotIn("Hi, OtherUser.", rendered_text)
        self.assertNotIn("OtherBot", rendered_text)
