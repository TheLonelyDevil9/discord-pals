import unittest

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
