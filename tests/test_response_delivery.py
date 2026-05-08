import unittest

from response_delivery import DeliveryFormatOptions, format_response_for_delivery


class ResponseDeliveryFormattingTests(unittest.TestCase):
    def test_short_greeting_followup_is_split_into_discord_parts(self):
        parts = format_response_for_delivery("Good morning You're up early. Or did you not sleep?")

        self.assertEqual(parts, ["Good morning.", "You're up early. Or did you not sleep?"])

    def test_missing_punctuation_between_short_thoughts_is_repaired_and_split(self):
        parts = format_response_for_delivery(
            "I'm glad you actually slept properly for once I've been up for a little while. "
            "Watched the stars fade from the observation car, had coffee with Himeko. It's a nice morning."
        )

        self.assertEqual(
            parts,
            [
                "I'm glad you actually slept properly for once.",
                "I've been up for a little while.",
                "Watched the stars fade from the observation car, had coffee with Himeko. It's a nice morning.",
            ],
        )

    def test_compact_reply_with_question_can_stay_together(self):
        parts = format_response_for_delivery("I slept at a normal time and woke up at a good time for once! You?")

        self.assertEqual(parts, ["I slept at a normal time and woke up at a good time for once! You?"])

    def test_inline_conjunction_is_not_split_before_pronoun(self):
        parts = format_response_for_delivery(
            "Both? Himeko already made a pot earlier but I'll never say no to more coffee. "
            "And the hugs are non-negotiable, I've been on that observation deck for hours and I'm cold."
        )

        self.assertEqual(
            parts,
            [
                "Both? Himeko already made a pot earlier but I'll never say no to more coffee.",
                "And the hugs are non-negotiable, I've been on that observation deck for hours and I'm cold.",
            ],
        )

    def test_max_parts_rolls_overflow_into_last_part(self):
        parts = format_response_for_delivery(
            "One.\n\nTwo.\n\nThree.\n\nFour.",
            DeliveryFormatOptions(max_parts=3),
        )

        self.assertEqual(parts, ["One.", "Two.", "Three.\n\nFour."])

    def test_dash_started_newline_still_splits_as_new_chat_part(self):
        parts = format_response_for_delivery("I can handle that\n\u2014 wait, actually, give me one second.")

        self.assertEqual(parts, ["I can handle that.", "\u2014 wait, actually, give me one second."])


if __name__ == "__main__":
    unittest.main()
