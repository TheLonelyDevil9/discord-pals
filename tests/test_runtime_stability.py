import types
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, call, patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module


class SplitReplyProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_request_context_allows_same_message_for_multiple_split_targets(self):
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
            content="hi both",
            channel=channel,
            guild=guild,
            mentions=[],
        )
        runtime_values = {
            "user_only_context": True,
            "user_only_context_count": 20,
            "allow_bot_mentions": False,
        }
        memories_mock = Mock(return_value="")

        def build_request(target_id, target_name):
            return {
                "message": message,
                "content": "hi both",
                "guild": guild,
                "attachments": [],
                "user_name": "Invoker",
                "is_dm": False,
                "user_id": 42,
                "sticker_info": None,
                "split_reply_target": types.SimpleNamespace(
                    id=target_id,
                    display_name=target_name,
                    name=target_name,
                ),
            }

        with ExitStack() as stack:
            stack.enter_context(patch.object(bot_instance_module, "get_history", return_value=[]))
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
            stack.enter_context(patch.object(bot_instance_module.character_manager, "build_system_prompt", Mock(return_value="SYSTEM")))
            stack.enter_context(patch.object(bot_instance_module.character_manager, "build_chatroom_context", Mock(return_value="CHATROOM")))
            stack.enter_context(patch.object(bot_instance_module, "get_other_bot_names", return_value=[]))
            stack.enter_context(patch.object(
                bot_instance_module.runtime_config,
                "get",
                side_effect=lambda key, default=None: runtime_values.get(key, default)
            ))
            stack.enter_context(patch.object(
                bot_instance_module,
                "format_history_split",
                Mock(return_value=([], [{"role": "user", "content": "Invoker: hi both"}]))
            ))
            stack.enter_context(patch.object(bot_instance_module.log, "info"))
            stack.enter_context(patch.object(bot_instance_module.log, "warn"))
            stack.enter_context(patch.object(bot_instance_module.log, "debug"))

            first = await instance._build_request_context(build_request(100, "Alice"))
            second = await instance._build_request_context(build_request(101, "Bob"))

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first["split_reply_target"].id, 100)
        self.assertEqual(second["split_reply_target"].id, 101)
        self.assertEqual(
            memories_mock.call_args_list,
            [call(5, 100, "Alice"), call(5, 101, "Bob")]
        )


class SendFinalizeStabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_and_finalize_skips_history_when_discord_send_fails(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        instance._check_rate_limit = Mock(return_value=False)
        instance._check_circuit_breaker = Mock(return_value=False)
        instance._is_duplicate_response = Mock(return_value=False)
        instance._remember_recent_response = Mock()
        instance._reset_failures = Mock()
        instance._record_response = Mock()
        instance._update_mood = Mock()
        instance._record_failure = Mock()
        instance._send_organic_response = AsyncMock(return_value=([], ""))

        context = {
            "channel_id": 1,
            "is_dm": False,
            "guild_id": 5,
            "user_id": 42,
            "user_name": "Invoker",
            "content": "hi",
            "split_reply_target": None,
        }
        request = {"guild": None}
        message = types.SimpleNamespace(channel=types.SimpleNamespace())

        with patch.object(bot_instance_module.runtime_config, "get", return_value=False), \
                patch.object(bot_instance_module, "parse_reactions", return_value=("hello", [])), \
                patch.object(bot_instance_module, "add_to_history") as add_history_mock, \
                patch.object(bot_instance_module.runtime_config, "update_last_activity") as update_activity_mock, \
                patch.object(bot_instance_module.metrics_manager, "update_last_activity") as metrics_activity_mock, \
                patch.object(bot_instance_module.metrics_manager, "record_rate_limit_hit"), \
                patch.object(bot_instance_module.metrics_manager, "record_circuit_breaker_trip"), \
                patch.object(bot_instance_module.log, "warn"):
            sent = await instance._send_and_finalize_response("hello", context, message, request)

        self.assertFalse(sent)
        add_history_mock.assert_not_called()
        update_activity_mock.assert_not_called()
        metrics_activity_mock.assert_not_called()
        instance._remember_recent_response.assert_not_called()
        instance._record_response.assert_not_called()
        instance._update_mood.assert_not_called()
        instance._record_failure.assert_called_once()
