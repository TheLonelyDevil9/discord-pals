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
        self.bots_file = Path(self.temp_dir.name) / "bots.json"
        self.env_patch = patch.object(env_config, "ENV_FILE", self.env_file)
        self.bots_patch = patch.object(env_config, "BOTS_FILE", self.bots_file)
        self.env_patch.start()
        self.bots_patch.start()
        dashboard_module.app.config["TESTING"] = True
        self.client = dashboard_module.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "test-csrf"

    def tearDown(self):
        self.bots_patch.stop()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def csrf_headers(self):
        return {"X-CSRF-Token": "test-csrf", "Content-Type": "application/json"}

    def write_bots(self, bots):
        self.bots_file.write_text(json.dumps({"bots": bots}), encoding="utf-8")

    def test_get_discord_token_status_never_returns_secret(self):
        secret = "a" * 48
        self.env_file.write_text(f"DISCORD_TOKEN={secret}\n", encoding="utf-8")

        response = self.client.get("/api/discord-token", headers=self.csrf_headers())
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["configured"])
        self.assertTrue(data["single_bot_mode"])
        self.assertNotIn("token", data)
        self.assertNotIn(secret, json.dumps(data))

    def test_get_discord_token_status_includes_multi_bot_targets_without_secrets(self):
        secret = "f" * 48
        self.write_bots([
            {"name": "Firefly", "token_env": "FIREFLY_DISCORD_TOKEN", "character": "firefly"},
            {"name": "Nicole", "token_env": "NICOLE_DISCORD_TOKEN", "character": "nicole"},
        ])
        self.env_file.write_text(
            f"FIREFLY_DISCORD_TOKEN={secret}\nNICOLE_DISCORD_TOKEN=your_discord_bot_token_here\n",
            encoding="utf-8",
        )

        response = self.client.get("/api/discord-token", headers=self.csrf_headers())
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(data["single_bot_mode"])
        self.assertTrue(data["multi_bot_mode"])
        self.assertEqual(len(data["multi_bot_tokens"]), 2)
        targets = {target["token_env"]: target for target in data["multi_bot_tokens"]}
        self.assertTrue(targets["FIREFLY_DISCORD_TOKEN"]["configured"])
        self.assertFalse(targets["NICOLE_DISCORD_TOKEN"]["configured"])
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
        self.assertEqual(data["updated_key"], "DISCORD_TOKEN")
        self.assertNotIn("token", data)
        self.assertIn(f"DISCORD_TOKEN={new_secret}", env_text)
        self.assertIn("OPENAI_API_KEY=keep-this", env_text)
        self.assertIn("LOCAL_API_KEY=not-needed", env_text)
        self.assertNotIn(old_secret, env_text)

    def test_post_multi_bot_discord_token_updates_declared_env_key(self):
        old_secret = "old" * 16
        new_secret = "new" * 16
        self.write_bots([
            {"name": "Firefly", "token_env": "FIREFLY_DISCORD_TOKEN", "character": "firefly"},
            {"name": "Nicole", "token_env": "NICOLE_DISCORD_TOKEN", "character": "nicole"},
        ])
        self.env_file.write_text(
            f"FIREFLY_DISCORD_TOKEN={'f' * 48}\nNICOLE_DISCORD_TOKEN={old_secret}\nOPENAI_API_KEY=keep-this\n",
            encoding="utf-8",
        )

        response = self.client.post(
            "/api/discord-token",
            data=json.dumps({"token_env": "NICOLE_DISCORD_TOKEN", "token": new_secret}),
            headers=self.csrf_headers(),
        )
        data = response.get_json()
        env_text = self.env_file.read_text(encoding="utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["restart_required"])
        self.assertEqual(data["updated_key"], "NICOLE_DISCORD_TOKEN")
        self.assertIn(f"NICOLE_DISCORD_TOKEN={new_secret}", env_text)
        self.assertIn("OPENAI_API_KEY=keep-this", env_text)
        self.assertNotIn(old_secret, env_text)
        targets = {target["token_env"]: target for target in data["multi_bot_tokens"]}
        self.assertTrue(targets["NICOLE_DISCORD_TOKEN"]["configured"])
        self.assertNotIn(new_secret, json.dumps(data))

    def test_post_multi_bot_discord_token_rejects_undeclared_env_key(self):
        self.write_bots([
            {"name": "Firefly", "token_env": "FIREFLY_DISCORD_TOKEN", "character": "firefly"},
        ])
        self.env_file.write_text("OPENAI_API_KEY=keep-this\n", encoding="utf-8")

        response = self.client.post(
            "/api/discord-token",
            data=json.dumps({"token_env": "NICOLE_DISCORD_TOKEN", "token": "n" * 48}),
            headers=self.csrf_headers(),
        )
        env_text = self.env_file.read_text(encoding="utf-8")

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("NICOLE_DISCORD_TOKEN", env_text)

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

    def test_config_page_shows_multi_bot_token_fields_without_secrets(self):
        secret = "g" * 48
        self.write_bots([
            {"name": "Firefly", "token_env": "FIREFLY_DISCORD_TOKEN", "character": "firefly"},
            {"name": "Nicole", "token_env": "NICOLE_DISCORD_TOKEN", "character": "nicole"},
        ])
        self.env_file.write_text(f"FIREFLY_DISCORD_TOKEN={secret}\n", encoding="utf-8")

        response = self.client.get("/config")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Firefly Token", html)
        self.assertIn("Nicole Token", html)
        self.assertIn("FIREFLY_DISCORD_TOKEN", html)
        self.assertIn("NICOLE_DISCORD_TOKEN", html)
        self.assertNotIn(secret, html)


if __name__ == "__main__":
    unittest.main()
