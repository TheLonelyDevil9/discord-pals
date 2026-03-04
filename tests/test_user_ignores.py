import unittest

from user_ignores import _is_ignored_in_set, _find_best_match_from_options


class UserIgnoresMatchingTests(unittest.TestCase):
    def test_ignore_check_accepts_alias_prefix(self):
        entries = {"Max Verstappen"}
        self.assertTrue(_is_ignored_in_set(entries, "max"))
        self.assertTrue(_is_ignored_in_set(entries, "MAX-VERSTAPPEN"))

    def test_fuzzy_match_returns_best_option(self):
        options = ["Alhaitham", "Cecile", "Collei"]
        match, suggestions = _find_best_match_from_options(options, "colie")
        self.assertEqual(match, "Collei")
        self.assertIn("Cecile", suggestions)


if __name__ == "__main__":
    unittest.main()
