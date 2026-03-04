import unittest

from character import (
    _extract_special_users_block,
    _parse_special_users_block,
    character_manager,
)


class CharacterSpecialUsersParsingTests(unittest.TestCase):
    def test_parses_legacy_plain_special_users_format(self):
        content = """
# Cecile

## Persona
Some persona text.

Special Users

seelewee
Seele is Cecile's creator.

geechan
Geechan is Seele's boyfriend.
"""
        block = _extract_special_users_block(content)
        parsed = _parse_special_users_block(block)

        self.assertIn("seelewee", parsed)
        self.assertIn("geechan", parsed)
        self.assertIn("creator", parsed["seelewee"].lower())

    def test_cecile_file_exposes_creator_special_user(self):
        cecile = character_manager.load("cecile")
        self.assertIsNotNone(cecile)
        self.assertIn("seelewee", cecile.special_users)
        self.assertIn("creator", cecile.special_users["seelewee"].lower())


if __name__ == "__main__":
    unittest.main()
