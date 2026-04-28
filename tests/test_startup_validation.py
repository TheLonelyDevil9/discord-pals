import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import startup


class StartupBotsConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.base_dir.joinpath("characters").mkdir()
        self.base_patch = patch.object(startup, "BASE_DIR", self.base_dir)
        self.base_patch.start()

    def tearDown(self):
        self.base_patch.stop()
        self.temp_dir.cleanup()

    def write_env(self, content):
        self.base_dir.joinpath(".env").write_text(content, encoding="utf-8")

    def write_bots(self, bots):
        self.base_dir.joinpath("bots.json").write_text(json.dumps({"bots": bots}), encoding="utf-8")

    def test_check_bots_config_reports_missing_token_envs(self):
        self.write_env("FIREFLY_DISCORD_TOKEN=real.token.value\n")
        self.write_bots([
            {"name": "Firefly", "token_env": "FIREFLY_DISCORD_TOKEN", "character": "firefly"},
            {"name": "George", "token_env": "GEORGE_DISCORD_TOKEN", "character": "george"},
        ])

        passed, issues = startup.check_bots_config()

        self.assertFalse(passed)
        self.assertIn("George: missing GEORGE_DISCORD_TOKEN", issues)

    def test_check_bots_config_accepts_configured_multi_bot_tokens(self):
        self.write_env(
            "FIREFLY_DISCORD_TOKEN=real.token.value\n"
            "GEORGE_DISCORD_TOKEN=another.real.token\n"
        )
        self.write_bots([
            {"name": "Firefly", "token_env": "FIREFLY_DISCORD_TOKEN", "character": "firefly"},
            {"name": "George", "token_env": "GEORGE_DISCORD_TOKEN", "character": "george"},
        ])

        passed, issues = startup.check_bots_config()

        self.assertTrue(passed)
        self.assertEqual([], issues)

    def test_check_discord_token_skips_single_bot_token_when_bots_json_exists(self):
        self.write_bots([
            {"name": "Firefly", "token_env": "FIREFLY_DISCORD_TOKEN", "character": "firefly"},
        ])

        passed, issues = startup.check_discord_token()

        self.assertTrue(passed)
        self.assertEqual([], issues)


if __name__ == "__main__":
    unittest.main()
