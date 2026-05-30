import unittest

import module_stubs  # noqa: F401
import discord_utils
import response_sanitizer as sanitizer


class ResponseSanitizerTests(unittest.TestCase):
    def test_sanitize_response_strips_generic_xml_wrapper_tags(self):
        cleaned = sanitizer.sanitize_response(
            "<seelewee> Seele is Cecile's creator. </seelewee> Hmm...",
            "Cecile",
        )

        self.assertEqual(cleaned, "Seele is Cecile's creator. Hmm...")

    def test_sanitize_response_preserves_discord_mentions_and_custom_emoji(self):
        cleaned = sanitizer.sanitize_response(
            "<profile name=\"nahida\">Hello</profile> <@123> <:wave:456>",
            "Nahida",
        )

        self.assertEqual(cleaned, "Hello <@123> <:wave:456>")

    def test_sanitize_response_turns_html_breaks_into_newlines(self):
        cleaned = sanitizer.sanitize_response(
            "<p>Hello<br/>there</p>",
            "Nahida",
        )

        self.assertEqual(cleaned, "Hello\nthere")

    def test_sanitize_response_strips_bracketed_reply_marker(self):
        cleaned = sanitizer.sanitize_response(
            '[Replying to Firefly: "I took my stockings off before I dozed off."] You too?',
            "Firefly",
        )

        self.assertEqual(cleaned, "You too?")

    def test_sanitize_response_strips_ooc_editorial_note_lines(self):
        cleaned = sanitizer.sanitize_response(
            "You're incorrigible, you know that?\n\n[OOC: tightened the wording here.]\nEditorial note: keep it casual.",
            "Firefly",
        )

        self.assertEqual(cleaned, "You're incorrigible, you know that?")

    def test_strip_discord_ooc_comments_hides_inline_note(self):
        cleaned = sanitizer.strip_discord_ooc_comments(
            "Yeah Kaveh, it sure is //Intentional, how do I check attribution"
        )

        self.assertEqual(cleaned, "Yeah Kaveh, it sure is")

    def test_strip_discord_ooc_comments_preserves_urls(self):
        cleaned = sanitizer.strip_discord_ooc_comments("Look at https://example.com/docs please")

        self.assertEqual(cleaned, "Look at https://example.com/docs please")

    def test_add_to_history_strips_inline_ooc_marker(self):
        channel_id = 882
        original_history = discord_utils.conversation_history
        original_last_activity = discord_utils._channel_last_activity
        original_recent_hashes = discord_utils._recent_message_hashes
        try:
            discord_utils.conversation_history = {}
            discord_utils._channel_last_activity = {}
            discord_utils._recent_message_hashes = {}
            with unittest.mock.patch.object(discord_utils, "save_history"):
                discord_utils.add_to_history(
                    channel_id,
                    "user",
                    "Yeah Kaveh, it sure is //Intentional, how do I check attribution",
                    author_name="TLD",
                )

            self.assertEqual(
                discord_utils.conversation_history[channel_id][0]["content"],
                "Yeah Kaveh, it sure is",
            )
        finally:
            discord_utils.conversation_history = original_history
            discord_utils._channel_last_activity = original_last_activity
            discord_utils._recent_message_hashes = original_recent_hashes

    def test_add_to_history_includes_request_id_in_diagnostics(self):
        channel_id = 883
        original_history = discord_utils.conversation_history
        original_last_activity = discord_utils._channel_last_activity
        original_recent_hashes = discord_utils._recent_message_hashes
        try:
            discord_utils.conversation_history = {}
            discord_utils._channel_last_activity = {}
            discord_utils._recent_message_hashes = {}
            with unittest.mock.patch.object(discord_utils, "save_history"):
                with unittest.mock.patch.object(discord_utils.log, "diagnostic") as diagnostic_mock:
                    discord_utils.add_to_history(
                        channel_id,
                        "assistant",
                        "Hello",
                        author_name="Firefly",
                        req_id="req-history",
                    )

            self.assertEqual(diagnostic_mock.call_args.kwargs["req_id"], "req-history")
        finally:
            discord_utils.conversation_history = original_history
            discord_utils._channel_last_activity = original_last_activity
            discord_utils._recent_message_hashes = original_recent_hashes
