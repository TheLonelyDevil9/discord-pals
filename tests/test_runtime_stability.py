import asyncio
import types
import unittest
from datetime import datetime
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, call, patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
import runtime_config
from scopes import ScopeLockRegistry


class _AsyncNoop:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


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
        runtime_values = {"allow_bot_mentions": False}
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
        first_speaker_context = [
            msg for msg in first["messages_for_api"]
            if msg.get("kind") == "current_speaker_context"
        ]
        self.assertEqual(len(first_speaker_context), 1)
        self.assertIn("Current Discord message author: Invoker.", first_speaker_context[0]["content"])
        self.assertIn("address Alice directly as \"you\"", first_speaker_context[0]["content"])
        self.assertNotIn("address Invoker directly", first_speaker_context[0]["content"])

    async def test_build_request_context_anchors_current_human_after_third_person_claims(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"
        instance.character_name = "firefly"
        instance.character = types.SimpleNamespace(name="Firefly", example_dialogue="")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
        instance._processed_message_ids = set()
        instance._gather_mentioned_user_context = AsyncMock(return_value="")

        channel = types.SimpleNamespace(id=77, name="lounge")
        guild = types.SimpleNamespace(id=5, name="Astral Express")
        author = types.SimpleNamespace(id=42, bot=False)
        message = types.SimpleNamespace(
            id=3003,
            content="Who's he?",
            author=author,
            channel=channel,
            guild=guild,
            mentions=[],
        )
        request = {
            "message": message,
            "content": "Who's he?",
            "guild": guild,
            "attachments": [],
            "user_name": "CurrentUser",
            "is_dm": False,
            "user_id": 42,
            "sticker_info": None,
        }
        immediate_history = [
            {"role": "user", "content": "Friend: CurrentUser is drunk"},
            {"role": "user", "content": "CurrentUser: No but I've never had booze nearby for a good reason"},
            {"role": "user", "content": "CurrentUser: I'm drunk sorry"},
            {"role": "user", "content": "CurrentUser: Who's he?"},
        ]
        runtime_values = {
            "allow_bot_mentions": False,
            "time_passage_context_enabled": False,
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
            stack.enter_context(patch.object(
                bot_instance_module,
                "format_history_split",
                Mock(return_value=([], immediate_history))
            ))
            stack.enter_context(patch.object(bot_instance_module.log, "info"))
            stack.enter_context(patch.object(bot_instance_module.log, "warn"))
            stack.enter_context(patch.object(bot_instance_module.log, "debug"))

            context = await instance._build_request_context(request)

        self.assertIsNotNone(context)
        rendered_messages = context["messages_for_api"]
        speaker_context = [
            msg for msg in rendered_messages
            if msg.get("kind") == "current_speaker_context"
        ]
        self.assertEqual(len(speaker_context), 1)
        self.assertIn("Current Discord message author: CurrentUser.", speaker_context[0]["content"])
        self.assertIn("address CurrentUser directly as \"you\"", speaker_context[0]["content"])
        self.assertIn("Earlier third-person lines about the addressed user", speaker_context[0]["content"])
        speaker_index = rendered_messages.index(speaker_context[0])
        self.assertGreater(speaker_index, 0)
        self.assertEqual(rendered_messages[speaker_index - 1]["content"], "CurrentUser: I'm drunk sorry")
        self.assertEqual(rendered_messages[speaker_index + 1]["content"], "CurrentUser: Who's he?")

    async def test_build_request_context_places_speaker_anchor_next_to_current_turn(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Kaveh"
        instance.character_name = "kaveh"
        instance.character = types.SimpleNamespace(name="Kaveh", example_dialogue="")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
        instance._processed_message_ids = set()
        instance._gather_mentioned_user_context = AsyncMock(return_value="")

        channel = types.SimpleNamespace(id=77, name="hangout-general")
        guild = types.SimpleNamespace(id=5, name="WaWa")
        author = types.SimpleNamespace(id=43, bot=False)
        message = types.SimpleNamespace(
            id=4004,
            content='kaveh, haitham, wanna see my "Chub"?',
            author=author,
            channel=channel,
            guild=guild,
            mentions=[],
        )
        request = {
            "message": message,
            "content": 'kaveh, haitham, wanna see my "Chub"?',
            "guild": guild,
            "attachments": [],
            "user_name": "Kris",
            "is_dm": False,
            "user_id": 43,
            "sticker_info": None,
        }
        immediate_history = [
            {"role": "user", "content": "TheLonelyWaWa: About 3 hours"},
            {
                "role": "user",
                "content": (
                    "Fly: Three hours to build an entire dashboard that replaces a broken API? "
                    "That's genuinely impressive."
                ),
            },
            {"role": "user", "content": 'Kris: kaveh, haitham, wanna see my "Chub"?'},
        ]
        runtime_values = {
            "allow_bot_mentions": False,
            "time_passage_context_enabled": False,
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
            stack.enter_context(patch.object(
                bot_instance_module,
                "format_history_split",
                Mock(return_value=([], immediate_history))
            ))
            stack.enter_context(patch.object(bot_instance_module.log, "info"))
            stack.enter_context(patch.object(bot_instance_module.log, "warn"))
            stack.enter_context(patch.object(bot_instance_module.log, "debug"))

            context = await instance._build_request_context(request)

        self.assertIsNotNone(context)
        rendered_messages = context["messages_for_api"]
        self.assertNotIn("current_turn_boundary", [msg.get("kind") for msg in rendered_messages])
        speaker_context = [
            msg for msg in rendered_messages
            if msg.get("kind") == "current_speaker_context"
        ]
        self.assertEqual(len(speaker_context), 1)
        speaker_index = rendered_messages.index(speaker_context[0])
        self.assertEqual(rendered_messages[speaker_index - 1]["content"], immediate_history[1]["content"])
        self.assertEqual(rendered_messages[speaker_index + 1]["content"], immediate_history[2]["content"])

    async def test_build_request_context_anchors_reply_to_current_bot_previous_message(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character_name = "nahida"
        instance.character = types.SimpleNamespace(name="Nahida", example_dialogue="")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999, display_name="Nahida"))
        instance._processed_message_ids = set()
        instance._gather_mentioned_user_context = AsyncMock(return_value="")

        channel = types.SimpleNamespace(id=77, name="tea-room")
        guild = types.SimpleNamespace(
            id=5,
            name="Sumeru",
            get_member=lambda user_id: None,
            get_channel=lambda channel_id: None,
            get_role=lambda role_id: None,
        )
        previous_bot_message = types.SimpleNamespace(
            id=600,
            author=instance.client.user,
            content=(
                "I don't know how to draw portraits, and I've never met this Firefly before. "
                "Is she someone from another world beyond Teyvat?"
            ),
            mentions=[],
        )
        author = types.SimpleNamespace(id=42, bot=False)
        message = types.SimpleNamespace(
            id=1234,
            content="Shes a good bot. One of the very best. So they had to put her down...",
            author=author,
            channel=channel,
            guild=guild,
            mentions=[],
            reference=types.SimpleNamespace(message_id=600, cached_message=previous_bot_message),
        )
        request = {
            "message": message,
            "content": "[Replying to Nahida's message] Shes a good bot. One of the very best. So they had to put her down...",
            "guild": guild,
            "attachments": [],
            "user_name": "The Primogem Guy",
            "is_dm": False,
            "user_id": 42,
            "sticker_info": None,
        }
        immediate_history = [
            {"role": "user", "content": "The Primogem Guy: draw firefly for me"},
            {
                "role": "user",
                "content": (
                    "The Primogem Guy: [Replying to Nahida's message] Shes a good bot. "
                    "One of the very best. So they had to put her down..."
                ),
            },
        ]
        runtime_values = {
            "allow_bot_mentions": False,
            "time_passage_context_enabled": False,
            "bot_reference_context_mode": "neutral",
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
            stack.enter_context(patch.object(
                bot_instance_module,
                "format_history_split",
                Mock(return_value=([], immediate_history))
            ))
            stack.enter_context(patch.object(bot_instance_module.log, "info"))
            stack.enter_context(patch.object(bot_instance_module.log, "warn"))
            stack.enter_context(patch.object(bot_instance_module.log, "debug"))

            context = await instance._build_request_context(request)

        anchors = [
            msg for msg in context["messages_for_api"]
            if msg.get("kind") == "current_bot_reply_anchor"
        ]
        self.assertEqual(len(anchors), 1)
        self.assertIn("replying directly to your previous Discord message", anchors[0]["content"])
        self.assertIn("I've never met this Firefly before", anchors[0]["content"])
        self.assertIn("do not restart or re-answer older requests", anchors[0]["content"])

    async def test_build_request_context_passes_time_passage_context_when_enabled(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"
        instance.character_name = "firefly"
        instance.character = types.SimpleNamespace(name="Firefly", example_dialogue="")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
        instance._processed_message_ids = set()
        instance._gather_mentioned_user_context = AsyncMock(return_value="")

        channel = types.SimpleNamespace(id=77, name="parlor-car")
        guild = types.SimpleNamespace(id=5, name="Astral Express")
        message = types.SimpleNamespace(
            id=1234,
            content="How many hours ago was that?",
            channel=channel,
            guild=guild,
            mentions=[],
        )
        history = [
            {
                "role": "assistant",
                "content": "On my way now.",
                "author": "Firefly",
                "timestamp": "2026-04-01T10:00:00+00:00",
            },
            {
                "role": "user",
                "content": "How many hours ago was that?",
                "author": "Invoker",
                "user_id": 42,
                "message_id": 1234,
                "timestamp": "2026-04-01T15:30:00+00:00",
            },
        ]
        request = {
            "message": message,
            "content": message.content,
            "guild": guild,
            "attachments": [],
            "user_name": "Invoker",
            "is_dm": False,
            "user_id": 42,
            "sticker_info": None,
        }
        build_chatroom_context_mock = Mock(return_value="CHATROOM")
        runtime_values = {
            "allow_bot_mentions": False,
            "time_passage_context_enabled": True,
        }

        with ExitStack() as stack:
            stack.enter_context(patch.object(bot_instance_module, "get_history", return_value=history))
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
            stack.enter_context(patch.object(bot_instance_module.character_manager, "build_chatroom_context", build_chatroom_context_mock))
            stack.enter_context(patch.object(bot_instance_module, "get_other_bot_names", return_value=[]))
            stack.enter_context(patch.object(
                bot_instance_module.runtime_config,
                "get",
                side_effect=lambda key, default=None: runtime_values.get(key, default)
            ))
            stack.enter_context(patch.object(
                bot_instance_module,
                "format_history_split",
                Mock(return_value=([], [{"role": "user", "content": "Invoker: How many hours ago was that?"}]))
            ))
            stack.enter_context(patch.object(bot_instance_module.log, "info"))
            stack.enter_context(patch.object(bot_instance_module.log, "warn"))
            stack.enter_context(patch.object(bot_instance_module.log, "debug"))

            context = await instance._build_request_context(request)

        self.assertIsNotNone(context)
        time_context = build_chatroom_context_mock.call_args.kwargs["time_passage_context"]
        self.assertIn("Elapsed time: 5 hours, 30 minutes later.", time_context)
        self.assertIn("On my way now.", time_context)


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

    async def test_send_organic_response_preserves_short_single_newlines(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"

        first = types.SimpleNamespace(id=1)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock()),
        )

        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                "Sure.\nI'll tag them now."
            )

        self.assertEqual([item["content"] for item in sent], ["Sure.\nI'll tag them now."])
        message.channel.send.assert_not_called()

    async def test_send_organic_response_preserves_single_newline_without_terminal_punctuation(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"

        first = types.SimpleNamespace(id=1)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock()),
        )

        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                "Don't you start, I'm still recovering\nYou weren't even here for the worst of it."
            )

        self.assertEqual(
            [item["content"] for item in sent],
            ["Don't you start, I'm still recovering\nYou weren't even here for the worst of it."]
        )
        message.channel.send.assert_not_called()

    async def test_send_organic_response_preserves_missing_punctuation_inside_single_newline_response(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"

        first = types.SimpleNamespace(id=1)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock()),
        )

        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                "I mean... they're cute? But I like mine.\n"
                "The gradient and the butterfly motifs mean something to me Why, you thinking of getting me a pair or something"
            )

        self.assertEqual(
            [item["content"] for item in sent],
            [
                "I mean... they're cute? But I like mine.\n"
                "The gradient and the butterfly motifs mean something to me Why, you thinking of getting me a pair or something",
            ]
        )
        message.channel.send.assert_not_called()

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

    async def test_send_organic_response_keeps_long_single_paragraph_together_under_discord_limit(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"

        first = types.SimpleNamespace(id=1)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock()),
        )

        response = (
            "Both? Himeko already made a pot earlier but I'll never say no to more coffee. "
            "And the hugs are non-negotiable, I've been on that observation deck for hours and I'm cold."
        )
        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                response
            )

        self.assertEqual([item["content"] for item in sent], [response])
        message.channel.send.assert_not_called()

    async def test_send_organic_response_preserves_missing_punctuation_before_capital_thought(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"

        first = types.SimpleNamespace(id=1)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock()),
        )

        response = (
            "The good kind, like when a song just hits you in the chest "
            "That's the best feeling honestly, when something resonates so deep it's almost physical"
        )
        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                response
            )

        self.assertEqual([item["content"] for item in sent], [response])
        message.channel.send.assert_not_called()

    async def test_send_organic_response_preserves_missing_punctuation_before_hyphenated_capital_thought(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"

        first = types.SimpleNamespace(id=1)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock()),
        )

        response = (
            "That's actually kind of heavy for a song title that sounds like a dessert "
            "Self-destruct tendencies hit different when you've lived on borrowed time though"
        )
        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                response
            )

        self.assertEqual([item["content"] for item in sent], [response])
        message.channel.send.assert_not_called()

    async def test_send_organic_response_does_not_soft_split_before_pronoun_i_or_titles(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"

        sent_message = types.SimpleNamespace(id=1)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=sent_message),
            channel=types.SimpleNamespace(send=AsyncMock()),
        )

        sent = await instance._send_organic_response(
            message,
            "That song title I mentioned earlier Nigh to Silence keeps looping in my head because "
            "it has that sharp kind of ache though I get why you'd connect it to heavier themes"
        )

        self.assertEqual(len(sent), 1)
        self.assertIn("though I get why", sent[0]["content"])
        self.assertIn("earlier Nigh to Silence", sent[0]["content"])
        message.channel.send.assert_not_called()

    async def test_send_organic_response_does_not_sentence_split_salutations(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"

        first = types.SimpleNamespace(id=1)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock()),
        )

        response = (
            "I still can't believe Mr. Rogers talked me into this prank earlier, and now everyone thinks it was my idea. "
            "Anyway I'm hiding in the kitchen until the teasing stops."
        )
        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                response
            )

        self.assertEqual([item["content"] for item in sent], [response])
        message.channel.send.assert_not_called()

    async def test_send_organic_response_splits_explicit_blank_line_parts(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"

        first = types.SimpleNamespace(id=1)
        second = types.SimpleNamespace(id=2)
        third = types.SimpleNamespace(id=3)
        fourth = types.SimpleNamespace(id=4)
        message = types.SimpleNamespace(
            reply=AsyncMock(return_value=first),
            channel=types.SimpleNamespace(send=AsyncMock(side_effect=[second, third, fourth])),
        )

        with patch.object(bot_instance_module.asyncio, "sleep", AsyncMock()), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._send_organic_response(
                message,
                "One.\n\nTwo.\n\nThree.\n\nFour."
            )

        self.assertEqual(len(sent), 4)
        self.assertEqual(sent[0]["content"], "One.")
        self.assertEqual(sent[1]["content"], "Two.")
        self.assertEqual(sent[2]["content"], "Three.")
        self.assertEqual(sent[3]["content"], "Four.")

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

    async def test_generate_ai_response_regenerates_then_drops_identity_violation(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character_name = "nahida"
        instance.character = types.SimpleNamespace(name="Nahida")

        context = {
            "system_prompt": "SYSTEM",
            "messages_for_api": [{"role": "user", "content": "Alice: hi"}],
            "chatroom_context": "",
            "other_bot_names": ["Firefly"],
        }
        message = types.SimpleNamespace(channel=types.SimpleNamespace())
        message.channel.typing = lambda: _AsyncNoop()
        generate_mock = AsyncMock(side_effect=[
            "Firefly: I can handle that.",
            "*Firefly says \"Sure.\"*",
        ])

        with patch.object(bot_instance_module.provider_manager, "generate", new=generate_mock), \
                patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
                    "identity_guard_enabled": True,
                    "identity_guard_policy": "regenerate_then_drop",
                    "use_single_user": False,
                    "prose_polisher_enabled": False,
                }.get(key, default)), \
                patch.object(bot_instance_module.runtime_config, "store_last_context"), \
                patch.object(bot_instance_module.stats_manager, "record_response"), \
                patch.object(bot_instance_module.metrics_manager, "record_response"), \
                patch.object(bot_instance_module.metrics_manager, "record_error"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "error"):
            response = await instance._generate_ai_response(context, message)

        self.assertIsNone(response)
        self.assertTrue(context["identity_guard_blocked"])
        self.assertEqual(generate_mock.await_count, 2)

    async def test_generate_ai_response_sends_safe_regeneration(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character_name = "nahida"
        instance.character = types.SimpleNamespace(name="Nahida")

        context = {
            "system_prompt": "SYSTEM",
            "messages_for_api": [{"role": "user", "content": "Alice: hi"}],
            "chatroom_context": "",
            "other_bot_names": ["Firefly"],
        }
        message = types.SimpleNamespace(channel=types.SimpleNamespace())
        message.channel.typing = lambda: _AsyncNoop()
        generate_mock = AsyncMock(side_effect=[
            "Firefly: I can handle that.",
            "I can help with that from here.",
        ])

        with patch.object(bot_instance_module.provider_manager, "generate", new=generate_mock), \
                patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
                    "identity_guard_enabled": True,
                    "identity_guard_policy": "regenerate_then_drop",
                    "use_single_user": False,
                    "prose_polisher_enabled": False,
                }.get(key, default)), \
                patch.object(bot_instance_module.runtime_config, "store_last_context"), \
                patch.object(bot_instance_module.stats_manager, "record_response"), \
                patch.object(bot_instance_module.metrics_manager, "record_response"), \
                patch.object(bot_instance_module.metrics_manager, "record_error"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "error"):
            response = await instance._generate_ai_response(context, message)

        self.assertEqual(response, "I can help with that from here.")
        self.assertFalse(context["identity_guard_blocked"])
        self.assertEqual(generate_mock.await_count, 2)

    def test_identity_guard_allows_plain_bot_name_reference(self):
        instance = object.__new__(bot_instance_module.BotInstance)

        with patch.object(bot_instance_module.runtime_config, "get", return_value=True):
            violation = instance._detect_identity_violation(
                "I saw Firefly earlier, but I can answer for myself.",
                ["Firefly"],
            )

        self.assertIsNone(violation)

    def test_identity_guard_allows_nonstructural_says_reference(self):
        instance = object.__new__(bot_instance_module.BotInstance)

        with patch.object(bot_instance_module.runtime_config, "get", return_value=True):
            violation = instance._detect_identity_violation(
                "When Firefly says things like that, I usually pause before answering.",
                ["Firefly"],
            )

        self.assertIsNone(violation)

    async def test_build_request_context_marks_triggered_bot_message_as_bot_history(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character_name = "nahida"
        instance.character = types.SimpleNamespace(name="Nahida", example_dialogue="")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
        instance._processed_message_ids = set()
        instance._gather_mentioned_user_context = AsyncMock(return_value="")

        channel = types.SimpleNamespace(id=77, name="tea-room")
        guild = types.SimpleNamespace(id=5, name="Sumeru")
        author = types.SimpleNamespace(id=222, bot=True)
        message = types.SimpleNamespace(
            id=1234,
            content="<@999> can you answer this?",
            author=author,
            channel=channel,
            guild=guild,
            mentions=[],
        )
        request = {
            "message": message,
            "content": "Nilou can you answer this?",
            "guild": guild,
            "attachments": [],
            "user_name": "Nilou",
            "is_dm": False,
            "user_id": 222,
            "sticker_info": None,
        }
        runtime_values = {
            "allow_bot_mentions": False,
            "time_passage_context_enabled": False,
        }

        with ExitStack() as stack:
            stack.enter_context(patch.object(bot_instance_module, "get_history", return_value=[]))
            stack.enter_context(patch.object(bot_instance_module, "was_recently_cleared", return_value=False))
            stack.enter_context(patch.object(bot_instance_module, "acknowledge_cleared"))
            add_history_mock = stack.enter_context(patch.object(bot_instance_module, "add_to_history"))
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
            stack.enter_context(patch.object(bot_instance_module, "format_history_split", Mock(return_value=([], []))))
            stack.enter_context(patch.object(bot_instance_module.log, "warn"))

            context = await instance._build_request_context(request)

        self.assertIsNotNone(context)
        self.assertTrue(add_history_mock.call_args.kwargs["is_bot"])
        self.assertNotIn("Nilou can you answer this?", str(context["messages_for_api"]))
        self.assertIn(
            {"role": "system", "content": "[Nilou sent a message]", "kind": "bot_event_context"},
            context["messages_for_api"],
        )

    async def test_process_request_skips_provider_when_server_channel_blocked(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")

        message = types.SimpleNamespace(
            id=123,
            channel=types.SimpleNamespace(id=77),
        )
        request = {
            "req_id": "req-1",
            "message": message,
            "channel_id": 77,
            "user_id": 42,
            "is_dm": False,
            "is_autonomous": False,
        }

        with patch.object(bot_instance_module.runtime_config, "get", return_value=False), \
                patch.object(bot_instance_module.runtime_config, "is_server_response_allowed", return_value=(False, "response_channel_blacklist")), \
                patch.object(instance, "_build_request_context", new=AsyncMock()) as build_mock, \
                patch.object(bot_instance_module.provider_manager, "generate", new=AsyncMock()) as generate_mock, \
                patch.object(bot_instance_module.log, "debug"):
            await instance._process_request(request)

        build_mock.assert_not_awaited()
        generate_mock.assert_not_awaited()

    async def test_process_request_serializes_same_scope_across_bot_instances(self):
        guild = types.SimpleNamespace(id=5)
        channel = types.SimpleNamespace(id=77, send=AsyncMock())
        first_message = types.SimpleNamespace(id=1001, channel=channel, guild=guild)
        second_message = types.SimpleNamespace(id=1002, channel=channel, guild=guild)
        first = object.__new__(bot_instance_module.BotInstance)
        second = object.__new__(bot_instance_module.BotInstance)
        first.name = "Nahida"
        second.name = "Nilou"
        first.character = types.SimpleNamespace(name="Nahida")
        second.character = types.SimpleNamespace(name="Nilou")
        order = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def build_first(request):
            order.append("first:build:start")
            first_started.set()
            await release_first.wait()
            order.append("first:build:end")
            return {"channel_id": 77, "identity_guard_blocked": False}

        async def build_second(request):
            order.append("second:build:start")
            return {"channel_id": 77, "identity_guard_blocked": False}

        first._build_request_context = AsyncMock(side_effect=build_first)
        second._build_request_context = AsyncMock(side_effect=build_second)
        first._generate_ai_response = AsyncMock(return_value="first response")
        second._generate_ai_response = AsyncMock(return_value="second response")
        first._send_and_finalize_response = AsyncMock(return_value=True)
        second._send_and_finalize_response = AsyncMock(return_value=True)
        first_request = {
            "req_id": "req-first",
            "message": first_message,
            "channel_id": 77,
            "guild": guild,
            "user_id": 42,
            "is_dm": False,
            "is_autonomous": False,
        }
        second_request = {
            "req_id": "req-second",
            "message": second_message,
            "channel_id": 77,
            "guild": guild,
            "user_id": 43,
            "is_dm": False,
            "is_autonomous": False,
        }

        with patch.object(bot_instance_module, "scope_lock_registry", ScopeLockRegistry()), \
                patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
                    "global_paused": False,
                }.get(key, default)), \
                patch.object(bot_instance_module.response_access, "request_access", return_value=(True, None, 77, 42)), \
                patch("coordinator.coordinator.acquire_slot", new=AsyncMock(return_value=object())), \
                patch("coordinator.coordinator.get_stagger_delay", new=AsyncMock(return_value=0.0)), \
                patch("coordinator.coordinator.release_slot", new=Mock()), \
                patch.object(bot_instance_module.log, "diagnostic"), \
                patch.object(bot_instance_module.log, "debug"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "error"):
            first_task = asyncio.create_task(first._process_request(first_request))
            await first_started.wait()
            second_task = asyncio.create_task(second._process_request(second_request))
            await asyncio.sleep(0)
            self.assertNotIn("second:build:start", order)

            release_first.set()
            await asyncio.gather(first_task, second_task)

        self.assertEqual(order, [
            "first:build:start",
            "first:build:end",
            "second:build:start",
        ])

    async def test_send_and_finalize_skips_dm_invite_when_dms_disabled(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        instance._check_rate_limit = Mock(return_value=False)
        instance._check_circuit_breaker = Mock(return_value=False)
        instance._is_duplicate_response = Mock(return_value=False)
        instance._send_organic_response = AsyncMock(return_value=[
            {"message": types.SimpleNamespace(id=31, created_at="t1"), "content": "server reply"},
        ])
        instance._send_organic_response_to_channel = AsyncMock()
        instance._resolve_user_dm_channel = AsyncMock()
        instance._remember_recent_response = Mock()
        instance._reset_failures = Mock()
        instance._record_response = Mock()
        instance._update_mood = Mock()

        context = {
            "channel_id": 1,
            "discord_channel_id": 77,
            "is_dm": False,
            "guild_id": 5,
            "user_id": 42,
            "user_name": "Invoker",
            "content": "please dm me",
            "split_reply_target": None,
            "mention_resolution_users": [],
            "mentionable_users": [],
            "mentionable_bots": [],
        }
        request = {"guild": None, "dm_invite_requested": True}
        message = types.SimpleNamespace(channel=types.SimpleNamespace())

        def runtime_get(key, default=None):
            if key == "allow_bot_mentions":
                return False
            return default

        def close_task(coro):
            coro.close()
            return None

        with patch.object(bot_instance_module.runtime_config, "get", side_effect=runtime_get), \
                patch.object(bot_instance_module.runtime_config, "is_server_response_allowed", return_value=(True, None)), \
                patch.object(bot_instance_module.runtime_config, "is_dm_response_allowed", return_value=(False, "dm_responses_disabled")), \
                patch.object(bot_instance_module, "parse_reactions", return_value=("server reply", [])), \
                patch.object(bot_instance_module, "add_to_history") as add_history_mock, \
                patch.object(bot_instance_module.runtime_config, "update_last_activity"), \
                patch.object(bot_instance_module.metrics_manager, "update_last_activity"), \
                patch.object(bot_instance_module.metrics_manager, "record_rate_limit_hit"), \
                patch.object(bot_instance_module.metrics_manager, "record_circuit_breaker_trip"), \
                patch.object(instance, "_maybe_auto_memory", new=AsyncMock()), \
                patch.object(instance, "_maybe_handle_reminder_capture", new=AsyncMock()), \
                patch.object(bot_instance_module.asyncio, "create_task", side_effect=close_task), \
                patch.object(bot_instance_module.log, "debug"):
            sent = await instance._send_and_finalize_response("server reply", context, message, request)

        self.assertTrue(sent)
        instance._resolve_user_dm_channel.assert_not_awaited()
        instance._send_organic_response_to_channel.assert_not_awaited()
        instance._send_organic_response.assert_awaited_once()
        self.assertEqual(add_history_mock.call_args.args[2], "server reply")


class NewRuntimeBehaviorTests(unittest.TestCase):
    def test_emoji_budget_limits_one_per_response_and_two_per_five(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        response = instance._apply_emoji_budget(123, "Hi ?? :wave: <:party:123456789012345678>")
        self.assertEqual(len(instance._emoji_matches(response)), 1)
        instance._record_emoji_budget(123, response)
        second = instance._apply_emoji_budget(123, "Again ?? :sparkles:")
        self.assertEqual(len(instance._emoji_matches(second)), 1)
        instance._record_emoji_budget(123, second)
        third = instance._apply_emoji_budget(123, "No more ?? :sparkles:")
        self.assertEqual(len(instance._emoji_matches(third)), 0)

    def test_bot_schedule_blocks_unavailable_window(self):
        schedule = {
            "enabled": True,
            "timezone": "UTC",
            "unavailable": [{"days": ["fri"], "start": "12:00", "end": "13:00"}],
        }
        with patch.object(runtime_config, "get_bot_schedule", return_value=schedule), \
                patch.object(runtime_config, "get_bot_timezone", return_value=None):
            self.assertFalse(runtime_config.is_bot_available("Nahida", datetime(2026, 4, 24, 12, 30)))
            self.assertTrue(runtime_config.is_bot_available("Nahida", datetime(2026, 4, 24, 13, 30)))

    def test_bot_schedule_blocks_overnight_window_next_morning(self):
        schedule = {
            "enabled": True,
            "timezone": "UTC",
            "unavailable": [{"days": ["fri"], "start": "22:00", "end": "08:00"}],
        }
        with patch.object(runtime_config, "get_bot_schedule", return_value=schedule), \
                patch.object(runtime_config, "get_bot_timezone", return_value=None):
            self.assertFalse(runtime_config.is_bot_available("Nahida", datetime(2026, 4, 24, 23, 0)))
            self.assertFalse(runtime_config.is_bot_available("Nahida", datetime(2026, 4, 25, 2, 0)))
            self.assertTrue(runtime_config.is_bot_available("Nahida", datetime(2026, 4, 25, 9, 0)))

    def test_dm_invite_detection_requires_dm_and_request_terms(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        self.assertTrue(instance._detect_dm_invite("Hey, could you DM me real quick?"))
        self.assertTrue(instance._detect_dm_invite("Please send me a message"))
        self.assertFalse(instance._detect_dm_invite("I opened my DMs yesterday"))
