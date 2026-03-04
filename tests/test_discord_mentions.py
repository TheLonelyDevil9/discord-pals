import unittest

import discord_utils
from discord_utils import convert_emojis_in_text, strip_unresolved_plain_mentions


class DiscordMentionSanitizerTests(unittest.TestCase):
    def test_demotes_plaintext_mentions(self):
        text = "@yo @got someone looking for you and @heads up"
        cleaned = strip_unresolved_plain_mentions(text)
        self.assertNotIn("@yo", cleaned.lower())
        self.assertNotIn("@got", cleaned.lower())
        self.assertNotIn("@heads", cleaned.lower())
        self.assertIn("someone looking for you", cleaned.lower())

    def test_preserves_valid_numeric_mentions(self):
        text = "<@123456789012345678> @yo this should only keep numeric mention"
        cleaned = strip_unresolved_plain_mentions(text)
        self.assertIn("<@123456789012345678>", cleaned)
        self.assertNotIn("@yo", cleaned.lower())

    def test_does_not_touch_email_addresses(self):
        text = "Reach me at febs@example.com and @yo if needed"
        cleaned = strip_unresolved_plain_mentions(text)
        self.assertIn("febs@example.com", cleaned)
        self.assertNotIn("@yo", cleaned.lower())

    def test_removes_dangling_markers(self):
        text = "@ @> hello"
        cleaned = strip_unresolved_plain_mentions(text)
        self.assertNotIn("@>", cleaned)
        self.assertNotIn(" @ ", f" {cleaned} ")


class DummyGuild:
    def __init__(self, guild_id):
        self.id = guild_id


class FakeEmoji:
    def __init__(self, name, emoji_id, animated=False):
        self.name = name
        self.id = emoji_id
        self.animated = animated


class DiscordEmojiSanitizerTests(unittest.TestCase):
    def test_drops_malformed_custom_emoji_without_cache(self):
        guild = DummyGuild(424242)
        text = "@huffs but pulls out phone <:huh:1445393773831131146 Fine."
        cleaned = convert_emojis_in_text(text, guild)
        self.assertNotIn("<:huh:1445393773831131146", cleaned)
        self.assertIn("Fine.", cleaned)

    def test_drops_malformed_custom_emoji_without_guild(self):
        text = "Paimon was listening <:cute:1440651153741189181 But yippee is good!"
        cleaned = convert_emojis_in_text(text, None)
        self.assertNotIn("<:cute:1440651153741189181", cleaned)
        self.assertIn("yippee is good", cleaned.lower())

    def test_still_converts_valid_shortcode_when_cache_exists(self):
        guild = DummyGuild(999)
        try:
            discord_utils._emoji_cache[guild.id] = {
                "huh": FakeEmoji("huh", 111111111111111111, animated=False)
            }

            cleaned = convert_emojis_in_text("Fine. :huh:", guild)
            self.assertIn("<:huh:111111111111111111>", cleaned)
        finally:
            discord_utils._emoji_cache.pop(guild.id, None)

    def test_does_not_strip_valid_user_mentions_during_emoji_cleanup(self):
        text = "<@123456789012345678> Kris wants you before bed."
        cleaned = convert_emojis_in_text(text, None)
        self.assertIn("<@123456789012345678>", cleaned)


if __name__ == "__main__":
    unittest.main()
