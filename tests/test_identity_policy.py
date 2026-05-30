import unittest

from identity_policy import IdentityPolicy


class IdentityPolicyTests(unittest.TestCase):
    def test_current_speaker_context_keeps_direct_target_distinct_from_author(self):
        context = IdentityPolicy.current_speaker_context(
            speaker_name="Alice",
            speaker_is_bot=False,
            target_user_name="Alice",
            direct_target_name="Bob",
        )

        self.assertIn("Current Discord message author: Alice.", context)
        self.assertIn("addressed to Bob", context)
        self.assertIn('address Bob directly as "you"', context)

    def test_detect_violation_blocks_structural_other_bot_speech(self):
        violation = IdentityPolicy(enabled=True).detect_violation(
            "Firefly: I can answer that.",
            ["Firefly"],
        )

        self.assertEqual(violation["name"], "Firefly")
        self.assertEqual(violation["pattern"], "speaker_prefix")

    def test_detect_violation_allows_plain_reference(self):
        violation = IdentityPolicy(enabled=True).detect_violation(
            "I saw Firefly earlier, but I can answer for myself.",
            ["Firefly"],
        )

        self.assertIsNone(violation)

    def test_disabled_policy_allows_structural_other_bot_speech(self):
        violation = IdentityPolicy(enabled=False).detect_violation(
            "Firefly: I can answer that.",
            ["Firefly"],
        )

        self.assertIsNone(violation)


if __name__ == "__main__":
    unittest.main()
