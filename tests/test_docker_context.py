"""The Docker build context leaves out secrets and runtime state, so no image layer carries them."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DockerBuildContextTests(unittest.TestCase):
    def test_secrets_and_runtime_state_stay_out_of_the_build_context(self):
        entries = {
            line.strip()
            for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        for path in (".env", "providers.json", "bots.json", "bot_data/", "venv/"):
            with self.subTest(path=path):
                self.assertIn(path, entries)


if __name__ == "__main__":
    unittest.main()
