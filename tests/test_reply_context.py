import unittest

import module_stubs  # noqa: F401
from reply_context import current_bot_reply_anchor, is_current_bot_author


class ReplyContextHelperTests(unittest.TestCase):
    def test_current_bot_reply_anchor_caps_previous_message(self):
        anchor = current_bot_reply_anchor("A" * 500)

        self.assertEqual(anchor["kind"], "current_bot_reply_anchor")
        self.assertIn("do not restart or re-answer older requests", anchor["content"])
        self.assertIn("...", anchor["content"])
        self.assertLess(len(anchor["content"]), 620)

    def test_current_bot_author_matches_by_id(self):
        author = type("User", (), {"id": 123})()
        bot_user = type("User", (), {"id": 123})()

        self.assertTrue(is_current_bot_author(author, bot_user))
        self.assertFalse(is_current_bot_author(type("User", (), {"id": 456})(), bot_user))
