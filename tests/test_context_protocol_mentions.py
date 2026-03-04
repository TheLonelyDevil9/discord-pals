import unittest

from context_protocol import extract_and_resolve_mentions


class ContextProtocolMentionTests(unittest.TestCase):
    def _envelope(self):
        return {
            "mention_candidates": [
                {
                    "handle": "@u_111111111111111111",
                    "user_id": 111111111111111111,
                    "aliases": ["seelewee", "Seele WaWa", "@u_111111111111111111"],
                    "priority": "user",
                },
            ],
            "participants": [
                {
                    "user_id": 111111111111111111,
                    "display_name": "Seele WaWa",
                    "username": "seelewee",
                    "is_bot": False,
                    "mention_handle": "@u_111111111111111111",
                },
            ],
        }

    def test_resolves_non_numeric_protocol_handle_by_alias(self):
        text = "@u_seelewee get in here"
        out = extract_and_resolve_mentions(text, self._envelope(), guild=None)
        self.assertIn("<@111111111111111111>", out)
        self.assertNotIn("@u_seelewee", out.lower())

    def test_resolves_bare_non_numeric_protocol_handle_by_alias(self):
        text = "u_seelewee is needed"
        out = extract_and_resolve_mentions(text, self._envelope(), guild=None)
        self.assertIn("<@111111111111111111>", out)
        self.assertNotIn("u_seelewee", out.lower())

    def test_demotes_unresolved_non_numeric_protocol_handle(self):
        text = "@u_unknown can you help"
        out = extract_and_resolve_mentions(text, self._envelope(), guild=None)
        self.assertIn("unknown can you help", out.lower())
        self.assertNotIn("@u_unknown", out.lower())

    def test_does_not_resolve_inside_words(self):
        text = "valueu_seelewee remains plain"
        out = extract_and_resolve_mentions(text, self._envelope(), guild=None)
        self.assertEqual(out, text)


if __name__ == "__main__":
    unittest.main()
