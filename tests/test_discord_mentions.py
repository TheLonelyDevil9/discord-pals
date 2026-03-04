import unittest

from discord_utils import strip_unresolved_plain_mentions


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


if __name__ == "__main__":
    unittest.main()
