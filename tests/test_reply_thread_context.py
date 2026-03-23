import types
import unittest
from unittest.mock import AsyncMock

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module


class ReplyThreadContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_message_content_adds_reply_context_for_referenced_author(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999, display_name="Paimon"))

        referenced_author = types.SimpleNamespace(id=222, display_name="Cecile", name="Cecile", bot=True)
        referenced_message = types.SimpleNamespace(
            id=600,
            author=referenced_author,
            content="I'd like to see you try, haven't had my coffee yet so I'm already in a shit mood.",
        )
        guild = types.SimpleNamespace(
            me=types.SimpleNamespace(display_name="Paimon"),
            get_member=lambda user_id: None,
            get_channel=lambda channel_id: None,
            get_role=lambda role_id: None,
        )
        author = types.SimpleNamespace(id=42, display_name="TheLonelyDevil", name="TheLonelyDevil", bot=False)
        message = types.SimpleNamespace(
            id=1234,
            content="Ironically that would hurt",
            author=author,
            guild=guild,
            channel=types.SimpleNamespace(fetch_message=None),
            mentions=[],
            attachments=[],
            reference=types.SimpleNamespace(message_id=600, cached_message=referenced_message),
        )

        content = await instance._prepare_message_content(
            message=message,
            user_name="TheLonelyDevil",
            sticker_info=None,
            is_other_bot=False,
            is_autonomous=False,
            guild=guild,
        )

        self.assertIn('[Replying to Cecile: "', content)
        self.assertIn("Ironically that would hurt", content)
        self.assertNotIn("[Replying to TheLonelyDevil", content)

    async def test_get_referenced_message_caches_without_mutating_slotted_messages(self):
        instance = object.__new__(bot_instance_module.BotInstance)

        referenced_message = types.SimpleNamespace(
            id=600,
            author=types.SimpleNamespace(id=222, display_name="Cecile", name="Cecile", bot=True),
            content="Wake up.",
        )

        class SlottedMessage:
            __slots__ = ("id", "reference", "channel")

            def __init__(self, fetch_message):
                self.id = 1234
                self.reference = types.SimpleNamespace(message_id=600, cached_message=None)
                self.channel = types.SimpleNamespace(fetch_message=fetch_message)

        fetch_message = AsyncMock(return_value=referenced_message)
        message = SlottedMessage(fetch_message)

        first = await instance._get_referenced_message(message)
        second = await instance._get_referenced_message(message)

        self.assertIs(first, referenced_message)
        self.assertIs(second, referenced_message)
        fetch_message.assert_awaited_once_with(600)
