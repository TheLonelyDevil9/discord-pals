import types
import unittest
from unittest.mock import AsyncMock, patch

import module_stubs  # noqa: F401
import discord_utils as discord_utils_module
import providers as providers_module


class FakeEmojiLib:
    NAMES = {
        "😀": "grinning_face",
        "🔥": "fire",
    }

    @classmethod
    def emoji_list(cls, text):
        matches = []
        for index, char in enumerate(text or ""):
            if char in cls.NAMES:
                matches.append({
                    "emoji": char,
                    "match_start": index,
                    "match_end": index + len(char),
                })
        return matches

    @classmethod
    def demojize(cls, value, delimiters=("", "")):
        name = cls.NAMES.get(value, "emoji")
        return f"{delimiters[0]}{name}{delimiters[1]}"


class FakeDiscordEmoji:
    def __init__(self, emoji_id, name, animated=False):
        self.id = emoji_id
        self.name = name
        self.animated = animated


class FakeGuild:
    def __init__(self, guild_id, emojis):
        self.id = guild_id
        self.emojis = emojis


class EmojiContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_emoji_lib = discord_utils_module.emoji_lib
        self._original_emoji_cache = dict(discord_utils_module._emoji_cache)
        self._original_emoji_visual_cache = dict(discord_utils_module._emoji_visual_cache)
        discord_utils_module.emoji_lib = FakeEmojiLib
        discord_utils_module._emoji_cache.clear()
        discord_utils_module._emoji_visual_cache.clear()

    def tearDown(self):
        discord_utils_module.emoji_lib = self._original_emoji_lib
        discord_utils_module._emoji_cache.clear()
        discord_utils_module._emoji_cache.update(self._original_emoji_cache)
        discord_utils_module._emoji_visual_cache.clear()
        discord_utils_module._emoji_visual_cache.update(self._original_emoji_visual_cache)

    async def test_current_message_merges_attachments_with_custom_and_unicode_emoji_visuals(self):
        messages = [{"role": "user", "content": "Alice: hi :nahida_wave: 😀"}]
        attachment_content = [
            {"type": "text", "text": "hi <:nahida_wave:123456789012345678> 😀"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ATTACHMENT"}},
        ]

        async def fake_download(url, mime_type, cache_key=None):
            return f"data:{mime_type};base64,{cache_key}"

        with patch.object(discord_utils_module, "download_image_as_data_url", new=AsyncMock(side_effect=fake_download)):
            enriched = await discord_utils_module.enrich_messages_with_visual_emojis(
                messages,
                current_message_index=0,
                raw_current_text="hi <:nahida_wave:123456789012345678> 😀",
                attachment_content=attachment_content,
                enable_vision=True,
            )

        parts = enriched[0]["content"]
        self.assertIsInstance(parts, list)
        self.assertEqual(parts[0]["text"], "Alice: hi :nahida_wave: 😀")
        self.assertEqual(len([part for part in parts if part.get("type") == "image_url"]), 3)
        self.assertIn("Emoji reference: :nahida_wave:", [part["text"] for part in parts if part.get("type") == "text"])
        self.assertIn("Emoji reference: 😀 (grinning face)", [part["text"] for part in parts if part.get("type") == "text"])

    async def test_recent_history_turns_with_emojis_are_enriched(self):
        guild = FakeGuild(1, [FakeDiscordEmoji(111, "nahida_wave")])
        discord_utils_module.get_guild_emojis(guild)
        messages = [
            {"role": "assistant", "content": "Nahida: :nahida_wave:"},
            {"role": "user", "content": "Alice: 😀"},
        ]

        async def fake_download(url, mime_type, cache_key=None):
            return f"data:{mime_type};base64,{cache_key}"

        with patch.object(discord_utils_module, "download_image_as_data_url", new=AsyncMock(side_effect=fake_download)):
            enriched = await discord_utils_module.enrich_messages_with_visual_emojis(
                messages,
                guild,
                enable_vision=True,
            )

        self.assertIsInstance(enriched[0]["content"], list)
        self.assertIsInstance(enriched[1]["content"], list)
        self.assertTrue(any(part.get("type") == "image_url" for part in enriched[0]["content"]))
        self.assertTrue(any(part.get("type") == "image_url" for part in enriched[1]["content"]))

    async def test_unresolved_historical_custom_shortcodes_stay_text_only(self):
        messages = [{"role": "assistant", "content": "Nahida: :missing_emoji:"}]

        enriched = await discord_utils_module.enrich_messages_with_visual_emojis(
            messages,
            guild=None,
            enable_vision=True,
        )

        self.assertEqual(enriched[0]["content"], "Nahida: :missing_emoji:")

    async def test_emoji_visuals_are_deduped_and_capped(self):
        messages = [
            {"role": "user", "content": "Alice: 😀 🔥 😀"},
            {"role": "assistant", "content": "Nahida: 😀"},
        ]

        async def fake_download(url, mime_type, cache_key=None):
            return f"data:{mime_type};base64,{cache_key}"

        with patch.object(discord_utils_module, "download_image_as_data_url", new=AsyncMock(side_effect=fake_download)):
            enriched = await discord_utils_module.enrich_messages_with_visual_emojis(
                messages,
                enable_vision=True,
                max_per_message=1,
                max_per_request=1,
            )

        total_images = sum(
            1
            for message in enriched
            for part in (message["content"] if isinstance(message["content"], list) else [])
            if part.get("type") == "image_url"
        )
        self.assertEqual(total_images, 1)
        self.assertIsInstance(enriched[0]["content"], list)
        self.assertEqual(enriched[1]["content"], "Nahida: 😀")

    def test_text_only_fallback_keeps_labels_and_uses_generic_visual_note(self):
        stripped = providers_module.strip_images_from_messages([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Alice: 😀"},
                    {"type": "text", "text": "Emoji reference: 😀 (grinning face)"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,unicode:1f600"}},
                ],
            }
        ])

        self.assertIn("Emoji reference: 😀 (grinning face)", stripped[0]["content"])
        self.assertIn("[Visual reference omitted for text-only model]", stripped[0]["content"])
        self.assertNotIn("[User sent an image that this model cannot see]", stripped[0]["content"])

    async def test_no_vision_support_leaves_messages_text_only(self):
        messages = [{"role": "user", "content": "Alice: 😀"}]

        enriched = await discord_utils_module.enrich_messages_with_visual_emojis(
            messages,
            enable_vision=False,
        )
        flattened = providers_module.format_as_single_user(enriched, "You are Nahida.")

        self.assertEqual(enriched[0]["content"], "Alice: 😀")
        self.assertIsNotNone(flattened)
