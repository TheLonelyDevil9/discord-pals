import types
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module


class MessageVisualContextTests(unittest.IsolatedAsyncioTestCase):
    def _make_instance(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character_name = "nahida"
        instance.character = types.SimpleNamespace(name="Nahida", example_dialogue="")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
        instance._processed_message_ids = set()
        instance._gather_mentioned_user_context = AsyncMock(return_value="")
        return instance

    async def _build_context(self, *, attachments, attachment_content=None):
        instance = self._make_instance()
        channel = types.SimpleNamespace(id=77, name="tea-room")
        guild = types.SimpleNamespace(id=5, name="Sumeru")
        message = types.SimpleNamespace(
            id=1234,
            content="😀 <:nahida_wave:123456789012345678>",
            channel=channel,
            guild=guild,
            mentions=[],
        )
        request = {
            "message": message,
            "content": message.content,
            "guild": guild,
            "attachments": attachments,
            "user_name": "Alice",
            "is_dm": False,
            "user_id": 42,
            "sticker_info": None,
        }
        runtime_values = {
            "user_only_context": True,
            "user_only_context_count": 20,
            "allow_bot_mentions": False,
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
                Mock(return_value=([], [{"role": "user", "content": f"Alice: {message.content}"}]))
            ))
            stack.enter_context(patch.object(bot_instance_module.log, "info"))
            stack.enter_context(patch.object(bot_instance_module.log, "warn"))
            stack.enter_context(patch.object(bot_instance_module.log, "debug"))
            if attachments:
                stack.enter_context(patch.object(
                    bot_instance_module,
                    "process_attachments",
                    AsyncMock(return_value=attachment_content)
                ))
            context = await instance._build_request_context(request)

        return context

    async def test_emoji_only_messages_remain_plain_text_in_request_payload(self):
        context = await self._build_context(attachments=[], attachment_content=None)

        self.assertIsNotNone(context)
        final_message = context["messages_for_api"][-1]
        self.assertEqual(final_message["role"], "user")
        self.assertIsInstance(final_message["content"], str)
        self.assertEqual(final_message["content"], "Alice: 😀 <:nahida_wave:123456789012345678>")

    async def test_real_image_attachments_still_attach_multimodal_content(self):
        attachment_content = [
            {"type": "text", "text": "😀 <:nahida_wave:123456789012345678>"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ATTACHMENT"}},
        ]

        context = await self._build_context(
            attachments=[types.SimpleNamespace(filename="image.png")],
            attachment_content=attachment_content
        )

        self.assertIsNotNone(context)
        final_message = context["messages_for_api"][-1]
        self.assertIsInstance(final_message["content"], list)
        self.assertTrue(any(part.get("type") == "image_url" for part in final_message["content"]))
