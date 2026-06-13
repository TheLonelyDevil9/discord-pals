import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import env_config


class DashboardDiscordTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_file = Path(self.temp_dir.name) / ".env"
        self.env_patch = patch.object(env_config, "ENV_FILE", self.env_file)
        self.env_patch.start()
        dashboard_module.app.config["TESTING"] = True
        self.client = dashboard_module.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "test-csrf"

    def tearDown(self):
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def csrf_headers(self):
        return {"X-CSRF-Token": "test-csrf", "Content-Type": "application/json"}

    def test_get_discord_token_status_never_returns_secret(self):
        secret = "a" * 48
        self.env_file.write_text(f"DISCORD_TOKEN={secret}\n", encoding="utf-8")

        response = self.client.get("/api/discord-token", headers=self.csrf_headers())
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["configured"])
        self.assertNotIn("token", data)
        self.assertNotIn(secret, json.dumps(data))

    def test_post_discord_token_updates_env_and_requires_restart(self):
        old_secret = "old" * 16
        new_secret = "new" * 16
        self.env_file.write_text(
            f"OPENAI_API_KEY=keep-this\nDISCORD_TOKEN={old_secret}\nLOCAL_API_KEY=not-needed\n",
            encoding="utf-8",
        )

        response = self.client.post(
            "/api/discord-token",
            data=json.dumps({"token": new_secret}),
            headers=self.csrf_headers(),
        )
        data = response.get_json()
        env_text = self.env_file.read_text(encoding="utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["configured"])
        self.assertTrue(data["restart_required"])
        self.assertNotIn("token", data)
        self.assertIn(f"DISCORD_TOKEN={new_secret}", env_text)
        self.assertIn("OPENAI_API_KEY=keep-this", env_text)
        self.assertIn("LOCAL_API_KEY=not-needed", env_text)
        self.assertNotIn(old_secret, env_text)

    def test_post_discord_token_rejects_empty_or_multiline_values(self):
        self.env_file.write_text("DISCORD_TOKEN=" + ("a" * 48) + "\n", encoding="utf-8")

        empty_response = self.client.post(
            "/api/discord-token",
            data=json.dumps({"token": ""}),
            headers=self.csrf_headers(),
        )
        multiline_response = self.client.post(
            "/api/discord-token",
            data=json.dumps({"token": "b" * 40 + "\n" + "c" * 8}),
            headers=self.csrf_headers(),
        )

        self.assertEqual(empty_response.status_code, 400)
        self.assertEqual(multiline_response.status_code, 400)
        self.assertNotIn("b" * 40, self.env_file.read_text(encoding="utf-8"))

    def test_post_discord_token_requires_csrf(self):
        response = self.client.post(
            "/api/discord-token",
            data=json.dumps({"token": "d" * 48}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_config_page_shows_token_status_without_secret(self):
        secret = "e" * 48
        self.env_file.write_text(f"DISCORD_TOKEN={secret}\n", encoding="utf-8")

        response = self.client.get("/config")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Discord Token", html)
        self.assertIn("Saved as <code>DISCORD_TOKEN</code>", html)
        self.assertNotIn(secret, html)


if __name__ == "__main__":
    unittest.main()
