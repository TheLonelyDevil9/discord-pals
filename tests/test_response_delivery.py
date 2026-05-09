import unittest

import module_stubs  # noqa: F401
from response_delivery import DeliveryFormatOptions, format_response_for_delivery


class ResponseDeliveryFormattingTests(unittest.TestCase):
    def test_single_paragraph_is_preserved_without_sentence_repair(self):
        parts = format_response_for_delivery(
            "Good morning You're up early. Or did you not sleep?"
        )

        self.assertEqual(parts, ["Good morning You're up early. Or did you not sleep?"])

    def test_single_newlines_are_preserved_inside_one_message(self):
        parts = format_response_for_delivery(
            "I noticed, babe.\n"
            "  Adding.\n"
            "Kaveh's name at the end doesn't hide it from me."
        )

        self.assertEqual(
            parts,
            ["I noticed, babe.\n  Adding.\nKaveh's name at the end doesn't hide it from me."],
        )

    def test_explicit_blank_lines_split_into_discord_parts(self):
        parts = format_response_for_delivery(
            "I noticed, babe.\n\n"
            "Adding.\n\n"
            "Kaveh's name at the end doesn't hide it from me.",
            DeliveryFormatOptions(max_parts=10),
        )

        self.assertEqual(
            parts,
            [
                "I noticed, babe.",
                "Adding.",
                "Kaveh's name at the end doesn't hide it from me.",
            ],
        )

    def test_max_parts_rolls_overflow_into_last_part(self):
        parts = format_response_for_delivery(
            "One.\n\nTwo.\n\nThree.\n\nFour.",
            DeliveryFormatOptions(max_parts=3),
        )

        self.assertEqual(parts, ["One.", "Two.", "Three.\n\nFour."])

    def test_length_limit_still_uses_discord_message_splitter(self):
        parts = format_response_for_delivery(
            "First paragraph.\n\nSecond paragraph is long.",
            DeliveryFormatOptions(max_message_length=18, max_parts=10),
        )

        self.assertEqual(parts, ["First paragraph.", "Second paragraph", "is long."])

    def test_length_limit_does_not_drop_oversized_words(self):
        parts = format_response_for_delivery(
            "abcdefghijklmnopqrstu",
            DeliveryFormatOptions(max_message_length=8, max_parts=10),
        )

        self.assertEqual("".join(parts), "abcdefghijklmnopqrstu")
        self.assertTrue(all(len(part) <= 8 for part in parts))

    def test_empty_response_returns_no_parts(self):
        self.assertEqual(format_response_for_delivery(" \n\n "), [])

    def test_idempotent_delivery_is_stable(self):
        input_text = "I noticed, babe.\nAdding.\nKaveh's name at the end doesn't hide it from me."

        options = DeliveryFormatOptions(max_parts=10)
        first_pass = format_response_for_delivery(input_text, options)
        second_pass = [part for item in first_pass for part in format_response_for_delivery(item, options)]

        self.assertEqual(first_pass, second_pass)


if __name__ == "__main__":
    unittest.main()
