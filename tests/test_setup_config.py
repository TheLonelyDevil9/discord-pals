"""The JSON files setup.sh writes from what the operator types."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_config", ROOT / "tools" / "setup_config.py")
setup_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_config)


class SetupConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output = Path(self.temp_dir.name) / "out.json"

    def written(self):
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_provider_values_with_quotes_and_backslashes_stay_valid_json(self):
        setup_config.main(["providers", str(self.output), "--",
                           'My "Fast" AI', "https://api.example/v1", "MY_KEY", "model\\v2",
                           "-dash", "http://localhost:1234/v1", "LOCAL_API_KEY", "local-model"])

        self.assertEqual(self.written(), {
            "providers": [
                {"name": 'My "Fast" AI', "url": "https://api.example/v1", "key_env": "MY_KEY", "model": "model\\v2"},
                {"name": "-dash", "url": "http://localhost:1234/v1", "key_env": "LOCAL_API_KEY",
                 "model": "local-model"},
            ],
            "timeout": 60,
        })

    def test_bot_values_are_written_as_entered(self):
        setup_config.main(["bots", str(self.output), "--",
                           "Fire'fly", "FIREFLY_TOKEN", "firefly", "Na\"hida", "NAHIDA_TOKEN", "nahida"])

        self.assertEqual(self.written(), {"bots": [
            {"name": "Fire'fly", "token_env": "FIREFLY_TOKEN", "character": "firefly"},
            {"name": 'Na"hida', "token_env": "NAHIDA_TOKEN", "character": "nahida"},
        ]})

    def test_zero_providers_still_writes_a_valid_file(self):
        setup_config.main(["providers", str(self.output)])

        self.assertEqual(self.written(), {"providers": [], "timeout": 60})

    def test_an_incomplete_entry_is_refused_without_writing(self):
        with self.assertRaises(SystemExit):
            setup_config.main(["bots", str(self.output), "--", "Firefly", "FIREFLY_TOKEN"])

        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
