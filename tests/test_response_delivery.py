import unittest

import module_stubs  # noqa: F401
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

    def test_title_and_initialism_false_positives_are_not_split(self):
        parts = format_response_for_delivery(
            "Mr. Rogers said hello to the neighborhood. I talked to Dr. Smith about the results. "
            "The U.S. Embassy downtown was busy. It happened around 3 p.m. People started leaving.",
            DeliveryFormatOptions(max_parts=10),
        )

        self.assertEqual(
            parts,
            [
                "Mr. Rogers said hello to the neighborhood.",
                "I talked to Dr. Smith about the results.",
                "The U.S. Embassy downtown was busy.",
                "It happened around 3 p.m. People started leaving.",
            ],
        )

    def test_bridge_word_false_positives_are_not_split(self):
        parts = format_response_for_delivery(
            "The artist named Sarah plays at the venue on Fridays. I heard a song called Yesterday by the Beatles. "
            "We talked about it earlier but I'll be there. He mentioned it to the team and Everyone agreed.",
            DeliveryFormatOptions(max_parts=10),
        )

        self.assertEqual(
            parts,
            [
                "The artist named Sarah plays at the venue on Fridays.",
                "I heard a song called Yesterday by the Beatles.",
                "We talked about it earlier but I'll be there.",
                "He mentioned it to the team and Everyone agreed.",
            ],
        )

    def test_time_abbreviation_false_positive_is_not_split(self):
        parts = format_response_for_delivery(
            "We met at 6 a.m. People were already awake.",
            DeliveryFormatOptions(max_parts=10),
        )

        self.assertEqual(parts, ["We met at 6 a.m. People were already awake."])

    def test_max_parts_rolls_overflow_into_last_part(self):
        parts = format_response_for_delivery(
            "One.\n\nTwo.\n\nThree.\n\nFour.",
            DeliveryFormatOptions(max_parts=3),
        )

        self.assertEqual(parts, ["One.", "Two.", "Three.\n\nFour."])

    def test_dash_started_newline_still_splits_as_new_chat_part(self):
        parts = format_response_for_delivery("I can handle that\n\u2014 wait, actually, give me one second.")

        self.assertEqual(parts, ["I can handle that.", "\u2014 wait, actually, give me one second."])

    def test_newline_fragments_reflow_before_delivery_split(self):
        parts = format_response_for_delivery(
            "Hmm, you're.\n"
            "Timoruz! You play.\n"
            "Beyond.\n\n"
            "All.\n\n"
            "Reason, right? Cortex.\n\n"
            "Vehicles if I remember correctly.\n\n"
            "Is there something else I should know about you?"
        )

        self.assertEqual(
            parts,
            [
                "Hmm, you're Timoruz!",
                "You play Beyond All Reason, right? Cortex.",
                "Vehicles if I remember correctly.",
                "Is there something else I should know about you?",
            ],
        )

    def test_comma_and_connector_newlines_reflow_without_extra_punctuation(self):
        parts = format_response_for_delivery(
            "I mean,\n"
            "that sounds like a good reason\n"
            "to ask about it directly."
        )

        self.assertEqual(parts, ["I mean, that sounds like a good reason to ask about it directly."])

    def test_complete_play_sentence_does_not_merge_next_topic(self):
        parts = format_response_for_delivery(
            "I play.\n\n"
            "Anyway, that's beside the point."
        )

        self.assertEqual(parts, ["I play.", "Anyway, that's beside the point."])

    def test_screenshot_transcript_splits_and_repairs_question_fragment(self):
        parts = format_response_for_delivery(
            "March mentioned some new flowers bloomed overnight, and I want to see them before she photographs "
            "every single one and blocks the whole pathway.\n"
            "What about you, after Operation.\n\n"
            "Breakfast wraps up? Job hunting all afternoon?"
        )

        self.assertEqual(
            parts,
            [
                "March mentioned some new flowers bloomed overnight, and I want to see them before she photographs "
                "every single one and blocks the whole pathway.",
                "What about you after Operation?",
                "Breakfast wraps up? Job hunting all afternoon?",
            ],
        )

    def test_wrapped_screenshot_transcript_splits_and_repairs_question_fragment(self):
        parts = format_response_for_delivery(
            "March mentioned some new flowers bloomed overnight, and I want to see them before she photographs "
            "every single one and blocks the whole pathway. What about you, after Operation.\n\n"
            "Breakfast wraps up? Job hunting all afternoon?"
        )

        self.assertEqual(
            parts,
            [
                "March mentioned some new flowers bloomed overnight, and I want to see them before she photographs "
                "every single one and blocks the whole pathway.",
                "What about you after Operation?",
                "Breakfast wraps up? Job hunting all afternoon?",
            ],
        )

    def test_idempotent_delivery_is_stable(self):
        input_text = (
            "Good morning You're up early. Or did you not sleep? "
            "The artist named Sarah plays at the venue on Fridays."
        )

        options = DeliveryFormatOptions(max_parts=10)
        first_pass = format_response_for_delivery(input_text, options)
        second_pass = [part for item in first_pass for part in format_response_for_delivery(item, options)]

        self.assertEqual(first_pass, second_pass)


if __name__ == "__main__":
    unittest.main()
