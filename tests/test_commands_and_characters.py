import tempfile
import types
import unittest
from pathlib import Path

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import character as character_module
import bot_instance as bot_instance_module
import discord
from unittest.mock import patch

from commands import setup_all_commands
from commands.registry import get_command_inventory, register_command_metadata, reset_command_registry
from test_support import MemorySandboxMixin


class FakeSyncTree:
    def __init__(self, fail_guild_ids=None):
        self.fail_guild_ids = set(fail_guild_ids or [])
        self.sync_calls = []
        self.copied_guild_ids = []
        self.cleared_guild_ids = []

    async def sync(self, guild=None):
        guild_id = getattr(guild, "id", None)
        self.sync_calls.append(guild_id)
        if guild_id in self.fail_guild_ids:
            raise RuntimeError(f"guild {guild_id} failed")
        return [types.SimpleNamespace(name="timezone"), types.SimpleNamespace(name="reminders")]

    def copy_global_to(self, guild=None):
        self.copied_guild_ids.append(getattr(guild, "id", None))

    def clear_commands(self, guild=None):
        self.cleared_guild_ids.append(getattr(guild, "id", None))


class CommandRegistrationTests(unittest.TestCase):
    def test_setup_all_commands_records_grouped_and_audience_metadata(self):
        fake_bot = types.SimpleNamespace(
            name="Nahida",
            character=types.SimpleNamespace(name="Nahida"),
            character_name="nahida",
            tree=discord.app_commands.CommandTree(types.SimpleNamespace()),
        )

        setup_all_commands(fake_bot)
        inventory = get_command_inventory(fake_bot)
        inventory_by_name = {entry["name"]: entry for entry in inventory}

        self.assertIn("timezone", inventory_by_name)
        self.assertIn("reminders", inventory_by_name)
        self.assertEqual(inventory_by_name["timezone"]["audience"], "user")
        self.assertEqual(
            [sub["name"] for sub in inventory_by_name["timezone"]["subcommands"]],
            ["set", "show", "clear"],
        )
        self.assertEqual(inventory_by_name["reload"]["audience"], "maintenance")
        self.assertEqual(inventory_by_name["interact"]["audience"], "user")


class CommandSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_helper_runs_global_and_guild_sync_and_records_partial_failures(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.tree = FakeSyncTree(fail_guild_ids={202})
        instance.client = types.SimpleNamespace(
            guilds=[
                types.SimpleNamespace(id=101, name="Sumeru"),
                types.SimpleNamespace(id=202, name="Fontaine"),
            ]
        )
        reset_command_registry(instance)
        register_command_metadata(instance, name="timezone", audience="user", kind="group", subcommands=[{"name": "set"}])
        register_command_metadata(instance, name="reload", audience="maintenance")

        with patch.object(bot_instance_module.log, "info"), \
                patch.object(bot_instance_module.log, "ok"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "error"):
            status = await instance._sync_slash_commands()

        self.assertEqual(instance.tree.sync_calls, [None, 101, 202])
        self.assertEqual(instance.tree.copied_guild_ids, [101, 202])
        self.assertEqual(instance.tree.cleared_guild_ids, [101, 202])
        self.assertTrue(status["global"]["ok"])
        self.assertEqual(status["global"]["count"], 2)
        self.assertEqual(status["top_level_commands"], ["timezone", "reload"])
        self.assertEqual(status["grouped_subcommands"]["timezone"], ["set"])
        self.assertEqual(status["guilds"][0]["ok"], True)
        self.assertEqual(status["guilds"][1]["ok"], False)
        self.assertIn("failed", status["guilds"][1]["error"])


class DashboardCommandStatusTests(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()

    def tearDown(self):
        self.tearDownMemorySandbox()

    def test_command_sync_status_api_and_config_page_render(self):
        dashboard_module.bot_instances = [
            types.SimpleNamespace(
                name="Nahida",
                character=None,
                character_name=None,
                nicknames="",
                client=types.SimpleNamespace(is_ready=lambda: False),
                command_sync_status={
                    "bot_name": "Nahida",
                    "last_attempt_at": "2026-04-02T10:30:00+00:00",
                    "last_success_at": "2026-04-02T10:30:05+00:00",
                    "top_level_commands": ["timezone", "reminders", "reload"],
                    "grouped_subcommands": {"timezone": ["set", "show", "clear"]},
                    "commands": [],
                    "audiences": {"user": ["timezone", "reminders"], "maintenance": ["reload"]},
                    "global": {"ok": True, "count": 3, "error": None},
                    "guilds": [{"id": 1, "name": "Sumeru", "ok": True, "count": 3, "error": None}],
                },
            )
        ]

        api_response = self.client.get("/api/command-sync-status")
        page = self.client.get("/config").get_data(as_text=True)

        self.assertEqual(api_response.status_code, 200)
        data = api_response.get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["bots"][0]["bot_name"], "Nahida")
        self.assertIn("Slash Command Sync", page)
        self.assertIn("/timezone", page)
        self.assertIn("applications.commands", page)


class CharacterSchemaTests(unittest.TestCase):
    def test_parse_character_content_supports_new_schema_and_ignores_unknown_sections(self):
        content = (
            "# Firefly\n\n"
            "## System Persona\n\n"
            "Warm and observant.\n\n"
            "## Example Dialogue\n\n"
            "\"Want coffee?\"\n\n"
            "## User Context\n\n"
            "### TheLonelyDevil\n"
            "Gets the observation deck seat.\n\n"
            "## Provider\n\n"
            "primary\n"
        )

        character = character_module.parse_character_content("firefly", content)

        self.assertEqual(character.name, "Firefly")
        self.assertEqual(character.schema_format, "explicit")
        self.assertEqual(character.persona, "Warm and observant.")
        self.assertEqual(character.example_dialogue, "\"Want coffee?\"")
        self.assertEqual(character.special_users["TheLonelyDevil"], "Gets the observation deck seat.")
        self.assertEqual(character.unused_sections["Provider"], "primary")

        prompt = character_module.character_manager.build_system_prompt(character, user_name="TheLonelyDevil")
        self.assertIn("<character_persona>", prompt)
        self.assertIn("<example_dialogue>", prompt)
        self.assertIn("<special_context>", prompt)
        self.assertNotIn("## Example Dialogue", prompt)

    def test_parse_character_content_keeps_legacy_schema_compatible(self):
        content = (
            "# Nahida\n\n"
            "## Persona\n\n"
            "Curious and playful.\n\n"
            "## Special Users\n\n"
            "### Febs\n"
            "Best tea company.\n"
        )

        character = character_module.parse_character_content("nahida", content)

        self.assertEqual(character.schema_format, "legacy")
        self.assertEqual(character.persona, "Curious and playful.")
        self.assertEqual(character.get_special_user_context("Febs WaWa"), "Best tea company.")


class CharacterDashboardTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.characters_dir = Path(self._temp_dir.name)
        self._orig_dashboard_characters_dir = dashboard_module.CHARACTERS_DIR
        self._orig_character_characters_dir = character_module.CHARACTERS_DIR
        dashboard_module.CHARACTERS_DIR = self.characters_dir
        character_module.CHARACTERS_DIR = str(self.characters_dir)
        character_module.character_manager.characters.clear()
        dashboard_module.app.config["TESTING"] = True
        self.client = dashboard_module.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "test-csrf"

    def tearDown(self):
        dashboard_module.CHARACTERS_DIR = self._orig_dashboard_characters_dir
        character_module.CHARACTERS_DIR = self._orig_character_characters_dir
        character_module.character_manager.characters.clear()
        self._temp_dir.cleanup()

    def test_preview_api_returns_gated_and_unused_sections(self):
        (self.characters_dir / "firefly.md").write_text(
            "# Firefly\n\n"
            "## System Persona\n\n"
            "Warm and observant.\n\n"
            "## User Context\n\n"
            "### TheLonelyDevil\n"
            "Gets the observation deck seat.\n\n"
            "## Notes\n\n"
            "Ignored by parser.\n",
            encoding="utf-8",
        )

        response = self.client.get("/api/preview/firefly?user_name=TheLonelyDevil")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["preview_user"], "TheLonelyDevil")
        self.assertEqual(data["matched_user_context"], "TheLonelyDevil")
        self.assertEqual(data["schema_format"], "explicit")
        self.assertEqual(data["always_injected"][0]["label"], "System Persona")
        self.assertEqual(data["conditional_user_contexts"][0]["included"], True)
        self.assertEqual(data["unused_sections"][0]["label"], "Notes")

    def test_new_character_scaffold_uses_explicit_schema(self):
        response = self.client.post(
            "/characters/new",
            data={"name": "SilverWolf", "csrf_token": "test-csrf"},
            follow_redirects=False,
        )

        created = (self.characters_dir / "SilverWolf.md").read_text(encoding="utf-8")

        self.assertEqual(response.status_code, 302)
        self.assertIn("## System Persona", created)
        self.assertIn("## Example Dialogue", created)
        self.assertIn("## User Context", created)
        self.assertNotIn("## Persona", created)
