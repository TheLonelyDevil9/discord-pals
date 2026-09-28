import unittest
from unittest import mock

import module_stubs  # noqa: F401
import discord_utils
import response_sanitizer as sanitizer


class ResponseSanitizerTests(unittest.TestCase):
    def test_explicit_thinking_is_removed_before_plain_text_recovery(self):
        thought = "SYSTEM: choose a greeting\n\nI should choose a friendly greeting."
        wrappers = [
            ("<think>", "</think>"), ("<thinking>", "</thinking>"),
            ("<|begin_of_box|>", "<|end_of_box|>"),
            ("<reasoning>", "</reasoning>"), ("<reason>", "</reason>"),
            ("[think]", "[/think]"), ("[thinking]", "[/thinking]"),
            ("<|think|>", "<|/think|>"),
            ("<|startofthought|>", "<|endofthought|>"),
            ("||thinking||", "||end||"), ("[Internal:", "]"),
        ]
        for opening, closing in wrappers:
            for visible in ("", "Hello, <@123> <:wave:456>!"):
                with self.subTest(opening=opening, visible=visible):
                    raw = opening + thought + closing + visible
                    self.assertEqual(sanitizer.remove_thinking_tags(raw), visible)
                    self.assertEqual(sanitizer.sanitize_response(raw), visible)

    def test_explicit_boundaries_preserve_dialogue_that_mentions_thinking(self):
        for visible in (
            "Let me think about where we should go for dinner.",
            "I need to think about your invitation.",
            "SYSTEM: is just the label on the screen.\n\nLet me explain what it means.",
        ):
            for wrapper in ("{}", "<output>{}</output>", "<response>{}</response>"):
                with self.subTest(visible=visible, wrapper=wrapper):
                    raw = "<think>SYSTEM: plan</think>\n\n" + wrapper.format(visible)
                    self.assertEqual(sanitizer.remove_thinking_tags(raw), visible)
                    self.assertEqual(sanitizer.sanitize_response(raw), visible)

    def test_mixed_thinking_blocks_leave_only_visible_dialogue(self):
        raw = (
            "<think>SYSTEM: plan\n\nPrivate first thought.</think>"
            "Let me think about dinner."
            "<reasoning>SYSTEM: review\n\nPrivate second thought.</reasoning>"
            "\n\nI need to think about your invitation."
            "<thinking>Private unfinished thought."
        )
        expected = "Let me think about dinner.\n\nI need to think about your invitation."
        self.assertEqual(sanitizer.remove_thinking_tags(raw), expected)
        self.assertEqual(sanitizer.sanitize_response(raw), expected)

    def test_partial_thinking_boundaries_precede_plain_text_recovery(self):
        thought = "SYSTEM: choose a greeting\n\nI should choose a friendly greeting."
        for opening, closing in [
            ("<think>", "</think>"), ("<thinking>", "</thinking>"),
            ("<|begin_of_box|>", "<|end_of_box|>"),
        ]:
            with self.subTest(opening=opening):
                self.assertEqual(sanitizer.remove_thinking_tags(opening + thought), "")
                self.assertEqual(sanitizer.remove_thinking_tags(thought + closing + "Hello!"), "Hello!")
                self.assertEqual(sanitizer.remove_thinking_tags("Hello!" + opening + thought), "Hello!")

    def test_plain_text_glm_output_recovery_survives_tag_removal(self):
        examples = [
            "SYSTEM: choose a greeting\n\nHello, good to see you!",
            'think: choose a greeting\nActual output: "Hello, good to see you!"',
            'think: choose a greeting\nFinal Polish: "Hello, good to see you!"',
            'think: choose a greeting\n\n"Hello, good to see you!"',
        ]
        for raw in examples:
            with self.subTest(raw=raw):
                self.assertEqual(sanitizer.remove_thinking_tags(raw), "Hello, good to see you!")

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

    def test_sanitize_response_strips_leading_emote_state_markers(self):
        examples = {
            "[pleased] It is. A hot brew has its uses.": "It is. A hot brew has its uses.",
            "pleased] How does one manage to get drunk off fumes?": "How does one manage to get drunk off fumes?",
            "[angry] Kris! How can you accuse me of such a thing?": "Kris! How can you accuse me of such a thing?",
            "First line.\n[pleased] Second line.": "First line.\nSecond line.",
        }

        for raw, expected in examples.items():
            with self.subTest(raw=raw):
                self.assertEqual(sanitizer.sanitize_response(raw, "Firefly"), expected)

    def test_sanitize_response_preserves_reaction_tags_and_mid_sentence_brackets(self):
        cleaned = sanitizer.sanitize_response(
            "[REACT: :wave:] I meant [pleased] as the literal marker, <@123> <:wave:456>",
            "Firefly",
        )

        self.assertEqual(
            cleaned,
            "[REACT: :wave:] I meant [pleased] as the literal marker, <@123> <:wave:456>",
        )

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
            with mock.patch.object(discord_utils, "save_history"):
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
