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
    async def test_send_organic_response_keeps_single_newline_in_one_message_by_default(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"

        sent_message = types.SimpleNamespace(id=1)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=sent_message),
            channel=types.SimpleNamespace(send=AsyncMock()),
        )

        sent = await instance._send_organic_response(
            message,
            "still thinking about it\nand continuing the same thought"
        )

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["content"], "still thinking about it\nand continuing the same thought")
        message.channel.send.assert_not_called()

    async def test_send_organic_response_can_split_short_single_newlines(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"

        first = types.SimpleNamespace(id=1)
        second = types.SimpleNamespace(id=2)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock(return_value=second)),
        )

        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                "Sure.\nI'll tag them now."
            )

        self.assertEqual([item["content"] for item in sent], ["Sure.", "I'll tag them now."])

    async def test_send_organic_response_splits_explicit_newline_without_terminal_punctuation(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"

        first = types.SimpleNamespace(id=1)
        second = types.SimpleNamespace(id=2)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock(return_value=second)),
        )

        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                "Don't you start, I'm still recovering\nYou weren't even here for the worst of it."
            )

        self.assertEqual(
            [item["content"] for item in sent],
            ["Don't you start, I'm still recovering", "You weren't even here for the worst of it."]
        )

    async def test_send_organic_response_keeps_salutation_newline_in_one_message(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"

        sent_message = types.SimpleNamespace(id=1)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=sent_message),
            channel=types.SimpleNamespace(send=AsyncMock()),
        )

        sent = await instance._send_organic_response(
            message,
            "Tell Mr.\nAnderson I'm busy."
        )

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["content"], "Tell Mr.\nAnderson I'm busy.")
        message.channel.send.assert_not_called()

    async def test_send_organic_response_can_split_long_single_paragraph_on_sentences(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"

        first = types.SimpleNamespace(id=1)
        second = types.SimpleNamespace(id=2)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock(return_value=second)),
        )

        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                "Both? Himeko already made a pot earlier but I'll never say no to more coffee. "
                "And the hugs are non-negotiable, I've been on that observation deck for hours and I'm cold."
            )

        self.assertEqual(len(sent), 2)
        self.assertTrue(sent[0]["content"].startswith("Both?"))
        self.assertTrue(sent[1]["content"].startswith("And the hugs"))

    async def test_send_organic_response_sentence_split_ignores_salutations(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"

        first = types.SimpleNamespace(id=1)
        second = types.SimpleNamespace(id=2)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock(return_value=second)),
        )

        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                "I still can't believe Mr. Rogers talked me into this prank earlier, and now everyone thinks it was my idea. "
                "Anyway I'm hiding in the kitchen until the teasing stops."
            )

        self.assertEqual(len(sent), 2)
        self.assertNotEqual(sent[0]["content"], "I still can't believe Mr.")
        self.assertIn("Mr. Rogers", sent[0]["content"])
        self.assertTrue(sent[1]["content"].startswith("Anyway"))

    async def test_send_organic_response_caps_natural_burst_length(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"

        first = types.SimpleNamespace(id=1)
        second = types.SimpleNamespace(id=2)
        third = types.SimpleNamespace(id=3)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock(side_effect=[second, third])),
        )

        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                "One.\n\nTwo.\n\nThree.\n\nFour."
            )

        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[0]["content"], "One.")
        self.assertEqual(sent[1]["content"], "Two.")
        self.assertEqual(sent[2]["content"], "Three.\n\nFour.")

    async def test_send_and_finalize_records_each_sent_part_separately(self):
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
        instance._send_organic_response = AsyncMock(return_value=[
            {"message": types.SimpleNamespace(id=11, created_at="t1"), "content": "One."},
            {"message": types.SimpleNamespace(id=12, created_at="t2"), "content": "Two."},
        ])

        context = {
            "channel_id": 1,
            "is_dm": False,
            "guild_id": 5,
            "user_id": 42,
            "user_name": "Invoker",
            "content": "hi",
            "split_reply_target": None,
            "mention_resolution_users": [],
            "mentionable_users": [],
            "mentionable_bots": [],
        }
        request = {"guild": None}
        message = types.SimpleNamespace(channel=types.SimpleNamespace())

        with patch.object(bot_instance_module.runtime_config, "get", return_value=False), \
                patch.object(bot_instance_module, "parse_reactions", return_value=("hello", [])), \
                patch.object(bot_instance_module, "add_to_history") as add_history_mock, \
                patch.object(bot_instance_module, "store_multipart_response") as multipart_mock, \
                patch.object(bot_instance_module.runtime_config, "update_last_activity"), \
                patch.object(bot_instance_module.metrics_manager, "update_last_activity"), \
                patch.object(bot_instance_module.metrics_manager, "record_rate_limit_hit"), \
                patch.object(bot_instance_module.metrics_manager, "record_circuit_breaker_trip"), \
                patch.object(bot_instance_module.log, "warn"):
            sent = await instance._send_and_finalize_response("hello", context, message, request)

        self.assertTrue(sent)
        self.assertEqual(add_history_mock.call_count, 2)
        self.assertEqual(add_history_mock.call_args_list[0].kwargs["message_id"], 11)
        self.assertEqual(add_history_mock.call_args_list[0].args[2], "One.")
        self.assertEqual(add_history_mock.call_args_list[1].kwargs["message_id"], 12)
        self.assertEqual(add_history_mock.call_args_list[1].args[2], "Two.")
        multipart_mock.assert_called_once_with(1, [11, 12], "One.\n\nTwo.")

    async def test_split_reply_target_mention_survives_send_processing(self):
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
        instance._send_organic_response = AsyncMock(return_value=[
            {"message": types.SimpleNamespace(id=31, created_at="t1"), "content": "<@555> hello"},
        ])

        context = {
            "channel_id": 1,
            "is_dm": False,
            "guild_id": 5,
            "user_id": 42,
            "user_name": "Invoker",
            "content": "hi",
            "split_reply_target": types.SimpleNamespace(id=555),
            "mention_resolution_users": [],
            "mentionable_users": [],
            "mentionable_bots": [],
        }
        request = {"guild": None}
        message = types.SimpleNamespace(channel=types.SimpleNamespace())

        with patch.object(bot_instance_module.runtime_config, "get", return_value=True), \
                patch.object(bot_instance_module, "parse_reactions", side_effect=lambda value: (value, [])), \
                patch.object(bot_instance_module, "add_to_history") as add_history_mock, \
                patch.object(bot_instance_module.runtime_config, "update_last_activity"), \
                patch.object(bot_instance_module.metrics_manager, "update_last_activity"), \
                patch.object(bot_instance_module.metrics_manager, "record_rate_limit_hit"), \
                patch.object(bot_instance_module.metrics_manager, "record_circuit_breaker_trip"), \
                patch.object(bot_instance_module.log, "warn"):
            sent = await instance._send_and_finalize_response("hello", context, message, request)

        self.assertTrue(sent)
        instance._send_organic_response.assert_awaited_once_with(message, "<@555> hello")
        self.assertEqual(add_history_mock.call_args.args[2], "<@555> hello")

    async def test_send_and_finalize_avoids_phantom_history_after_later_send_failure(self):
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

        first_sent = types.SimpleNamespace(id=21, created_at="t1")
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first_sent),
            channel=types.SimpleNamespace(send=AsyncMock(side_effect=bot_instance_module.discord.HTTPException("boom"))),
        )
        context = {
            "channel_id": 1,
            "is_dm": False,
            "guild_id": 5,
            "user_id": 42,
            "user_name": "Invoker",
            "content": "hi",
            "split_reply_target": None,
            "mention_resolution_users": [],
            "mentionable_users": [],
            "mentionable_bots": [],
        }
        request = {"guild": None}

        with patch.object(bot_instance_module.runtime_config, "get", return_value=False), \
                patch.object(bot_instance_module, "parse_reactions", return_value=("One.\n\nTwo.", [])), \
                patch.object(bot_instance_module, "add_to_history") as add_history_mock, \
                patch.object(bot_instance_module.runtime_config, "update_last_activity"), \
                patch.object(bot_instance_module.metrics_manager, "update_last_activity"), \
                patch.object(bot_instance_module.metrics_manager, "record_rate_limit_hit"), \
                patch.object(bot_instance_module.metrics_manager, "record_circuit_breaker_trip"), \
                patch.object(bot_instance_module, "store_multipart_response") as multipart_mock, \
                patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "error"):
            sent = await instance._send_and_finalize_response("One.\n\nTwo.", context, message, request)

        self.assertTrue(sent)
        self.assertEqual(add_history_mock.call_count, 1)
        self.assertEqual(add_history_mock.call_args.kwargs["message_id"], 21)
        multipart_mock.assert_not_called()

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
        instance._send_organic_response = AsyncMock(return_value=[])

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
