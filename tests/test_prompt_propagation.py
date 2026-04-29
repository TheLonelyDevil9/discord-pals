import json
import tempfile
import types
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import module_stubs  # noqa: F401
import character as character_module
import dashboard as dashboard_module
import discord_utils as discord_utils_module
import providers as providers_module
import runtime_config as runtime_config_module

from test_support import MemorySandboxMixin


class PromptPropagationTests(unittest.TestCase):
    def setUp(self):
        self._history_originals = {
            "conversation_history": discord_utils_module.conversation_history,
            "channel_names": discord_utils_module.channel_names,
            "channel_last_activity": discord_utils_module._channel_last_activity,
        }
        discord_utils_module.conversation_history = {}
        discord_utils_module.channel_names = {}
        discord_utils_module._channel_last_activity = {}

    def tearDown(self):
        discord_utils_module.conversation_history = self._history_originals["conversation_history"]
        discord_utils_module.channel_names = self._history_originals["channel_names"]
        discord_utils_module._channel_last_activity = self._history_originals["channel_last_activity"]

    def test_format_history_split_preserves_current_bot_author_in_user_only_mode(self):
        channel_id = 123
        discord_utils_module.conversation_history[channel_id] = [
            {"role": "user", "content": "Hi", "author": "Alice", "is_bot": False},
            {"role": "assistant", "content": "Hello there", "author": "Nahida"},
            {"role": "user", "content": "I can help too", "author": "Nilou", "is_bot": True},
        ]

        history, immediate = discord_utils_module.format_history_split(
            channel_id,
            user_only=True,
            context_count=5,
            current_bot_name="Nahida"
        )

        self.assertEqual(history, [])
        self.assertEqual(
            immediate,
            [
                {"role": "user", "content": "Alice: Hi"},
                {"role": "assistant", "content": "Hello there", "author": "Nahida"},
                {"role": "user", "content": "Nilou: I can help too"},
            ]
        )

    def test_format_history_split_preserves_current_bot_author_in_legacy_mode(self):
        channel_id = 456
        discord_utils_module.conversation_history[channel_id] = [
            {"role": "user", "content": "Hi", "author": "Alice", "is_bot": False},
            {"role": "assistant", "content": "Hello there", "author": "Nahida"},
            {"role": "assistant", "content": "I can help too", "author": "Nilou"},
        ]

        history, immediate = discord_utils_module.format_history_split(
            channel_id,
            total_limit=10,
            immediate_count=10,
            current_bot_name="Nahida"
        )

        self.assertEqual(history, [])
        self.assertEqual(
            immediate,
            [
                {"role": "user", "content": "Alice: Hi"},
                {"role": "assistant", "content": "Hello there", "author": "Nahida"},
                {"role": "user", "content": "Nilou: I can help too"},
            ]
        )

    def test_format_as_single_user_keeps_system_prompt_and_context_separate(self):
        result = providers_module.format_as_single_user(
            [
                {"role": "system", "content": "Guild: Sumeru", "kind": "chatroom_context"},
                {"role": "user", "content": "Hello", "author": "Alice"},
                {"role": "assistant", "content": "Welcome back", "author": "Nahida"},
            ],
            "You are Nahida."
        )

        self.assertEqual(len(result), 1)
        combined = result[0]["content"]
        self.assertIn("### Instructions\nYou are Nahida.", combined)
        self.assertIn("### Context\nGuild: Sumeru", combined)
        self.assertIn("Alice: Hello", combined)
        self.assertIn("Nahida: Welcome back", combined)
        self.assertNotIn("Assistant: Welcome back", combined)

    def test_format_history_split_marks_large_time_gaps(self):
        channel_id = 789
        discord_utils_module.conversation_history[channel_id] = [
            {
                "role": "user",
                "content": "Hi",
                "author": "Alice",
                "timestamp": "2026-03-31T09:00:00+00:00",
            },
            {
                "role": "user",
                "content": "Back again",
                "author": "Alice",
                "timestamp": "2026-04-01T09:30:00+00:00",
            },
        ]

        history, immediate = discord_utils_module.format_history_split(
            channel_id,
            total_limit=10,
            immediate_count=10,
            current_bot_name="Nahida"
        )

        self.assertEqual(history, [])
        self.assertEqual(immediate[0]["content"], "Alice: Hi")
        self.assertIn("[Time gap: 1 day, 30 minutes later]", immediate[1]["content"])
        self.assertIn("Alice: Back again", immediate[1]["content"])

    def test_time_passage_signal_uses_recent_large_gap(self):
        history = [
            {
                "role": "assistant",
                "content": "On my way now.",
                "author": "Firefly",
                "timestamp": "2026-04-01T10:00:00+00:00",
            },
            {
                "role": "user",
                "content": "How many hours ago was that?",
                "author": "Alice",
                "timestamp": "2026-04-01T15:30:00+00:00",
            },
        ]

        signal = discord_utils_module.build_time_passage_signal(history)

        self.assertIsNotNone(signal)
        self.assertEqual(signal["gap_label"], "5 hours, 30 minutes later")
        self.assertEqual(signal["before_author"], "Firefly")
        self.assertIn("On my way", signal["before_content"])
        self.assertEqual(signal["after_author"], "Alice")

    def test_time_passage_signal_ignores_short_gap(self):
        history = [
            {"role": "user", "content": "Hi", "author": "Alice", "timestamp": "2026-04-01T10:00:00+00:00"},
            {"role": "assistant", "content": "Hello", "author": "Nahida", "timestamp": "2026-04-01T10:20:00+00:00"},
        ]

        self.assertIsNone(discord_utils_module.build_time_passage_signal(history))


class TimePlaceholderTests(unittest.TestCase):
    def test_other_prompts_file_drives_chatroom_context_without_system_prompt(self):
        original_prompts_dir = character_module.PROMPTS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            prompts_dir = Path(tmp)
            (prompts_dir / "system.md").write_text("SYSTEM_SENTINEL", encoding="utf-8")
            (prompts_dir / "other_prompts.md").write_text(
                "# Other Prompts\n\n"
                "## Chatroom Context\n\n"
                "Other prompt for {{CHARACTER_NAME}}.\n"
                "{{TIME_PASSAGE_CONTEXT}}\n",
                encoding="utf-8",
            )
            character_module.PROMPTS_DIR = str(prompts_dir)
            try:
                manager = character_module.PromptManager()
            finally:
                character_module.PROMPTS_DIR = original_prompts_dir

        context = manager.build_chatroom_context(
            character_name="Nahida",
            time_passage_context="Five hours later."
        )

        self.assertIn("Other prompt for Nahida.", context)
        self.assertIn("Five hours later.", context)
        self.assertNotIn("SYSTEM_SENTINEL", context)

    def test_other_prompts_loader_falls_back_to_legacy_chatroom_context(self):
        original_prompts_dir = character_module.PROMPTS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            prompts_dir = Path(tmp)
            (prompts_dir / "chatroom_context.md").write_text(
                "Legacy context for {{CHARACTER_NAME}}.",
                encoding="utf-8",
            )
            character_module.PROMPTS_DIR = str(prompts_dir)
            try:
                manager = character_module.PromptManager()
            finally:
                character_module.PROMPTS_DIR = original_prompts_dir

        context = manager.build_chatroom_context(character_name="Nahida")

        self.assertIn("Legacy context for Nahida.", context)

    def test_time_passage_context_renders_as_post_system_context(self):
        manager = character_module.PromptManager()
        signal = {
            "gap_label": "5 hours later",
            "before_author": "Firefly",
            "before_content": "On my way now.",
            "after_author": "Alice",
            "after_content": "How many hours ago was that?",
        }

        context = manager.build_time_passage_context(signal, is_dm=False)

        self.assertIn("Elapsed time: 5 hours later.", context)
        self.assertIn("Before the pause: Firefly: On my way now.", context)
        self.assertIn("Infer lightly", context)

    def test_system_prompt_expands_time_placeholders_in_template_and_persona(self):
        manager = character_module.PromptManager()
        manager.system_template = "It is {{weekday}}, {{date}} at {{time}} for {{CHARACTER_NAME}}.\n{{PERSONA}}"
        now = datetime(2026, 4, 1, 18, 5, 9, tzinfo=timezone(timedelta(hours=5, minutes=30)))

        prompt = manager.build_prompt(
            character_name="Nahida",
            persona="Today's day-of-month is {{day}} in {{month_name}} {{year}}.",
            now=now
        )

        self.assertIn("It is Wednesday, 2026-04-01 at 6:05 PM for Nahida.", prompt)
        self.assertIn("Today's day-of-month is 1 in April 2026.", prompt)

    def test_chatroom_context_includes_current_time_context_and_time_tokens(self):
        manager = character_module.PromptManager()
        manager.chatroom_context_template = (
            "Server: {{GUILD_NAME}}\n"
            "{{CURRENT_TIME_CONTEXT}}\n"
            "Today is {{weekday}} and the day-of-month is {{day}}."
        )
        now = datetime(2026, 4, 1, 18, 5, 9, tzinfo=timezone(timedelta(hours=5, minutes=30)))

        context = manager.build_chatroom_context(
            guild_name="Sumeru",
            character_name="Nahida",
            user_name="Traveler",
            now=now
        )

        self.assertIn("Server: Sumeru", context)
        self.assertIn("Current local date/time: Wednesday, 2026-04-01 at 6:05 PM", context)
        self.assertIn("Today is Wednesday and the day-of-month is 1.", context)

    def test_system_prompt_includes_current_time_context_when_requested(self):
        manager = character_module.PromptManager()
        manager.system_template = "{{CURRENT_TIME_CONTEXT}}\n{{PERSONA}}"
        now = datetime(2026, 4, 1, 18, 5, 9, tzinfo=timezone(timedelta(hours=5, minutes=30)))

        prompt = manager.build_prompt(
            character_name="Nahida",
            persona="Stay aware of the date.",
            now=now
        )

        self.assertIn("Current local date/time: Wednesday, 2026-04-01 at 6:05 PM", prompt)
        self.assertIn("Stay aware of the date.", prompt)

    def test_resolve_discord_formatting_prefers_explicit_mentioned_users(self):
        mentioned_user = types.SimpleNamespace(id=123, display_name="Adam Best Boy", name="Adam")

        rendered = discord_utils_module.resolve_discord_formatting(
            "I tagged <@123> already.",
            mentioned_users=[mentioned_user]
        )

        self.assertEqual(rendered, "I tagged @Adam Best Boy already.")

    def test_get_mentionable_users_includes_guild_member_even_with_busy_history(self):
        channel_id = 321
        discord_utils_module.conversation_history[channel_id] = [
            {"role": "user", "content": "hi", "author": f"User{i}", "user_id": i}
            for i in range(1, 6)
        ]
        guild_member = types.SimpleNamespace(
            id=99,
            bot=False,
            display_name="Febs WaWa",
            global_name="Febs",
            name="febs1996",
        )
        guild = types.SimpleNamespace(members=[guild_member])

        users = discord_utils_module.get_mentionable_users(channel_id, limit=None, guild=guild)

        self.assertTrue(any(user["user_id"] == 99 for user in users))

    def test_process_outgoing_mentions_prefers_high_priority_alias_match(self):
        rendered = discord_utils_module.process_outgoing_mentions(
            "@Febs WaWa hey",
            mentionable_users=[
                {
                    "name": "Febs WaWa",
                    "user_id": 10,
                    "mention_syntax": "<@10>",
                    "aliases": ["Febs WaWa"],
                    "priority": 0,
                },
                {
                    "name": "Febs WaWa",
                    "user_id": 11,
                    "mention_syntax": "<@11>",
                    "aliases": ["Febs WaWa"],
                    "priority": 2,
                },
            ],
        )

        self.assertEqual(rendered, "<@10> hey")

    def test_process_outgoing_mentions_leaves_ambiguous_same_priority_alias_as_text(self):
        rendered = discord_utils_module.process_outgoing_mentions(
            "@Febs WaWa hey",
            mentionable_users=[
                {
                    "name": "Febs WaWa",
                    "user_id": 10,
                    "mention_syntax": "<@10>",
                    "aliases": ["Febs WaWa"],
                    "priority": 2,
                },
                {
                    "name": "Febs WaWa",
                    "user_id": 11,
                    "mention_syntax": "<@11>",
                    "aliases": ["Febs WaWa"],
                    "priority": 2,
                },
            ],
        )

        self.assertEqual(rendered, "@Febs WaWa hey")

    def test_add_to_history_persists_timestamp_metadata(self):
        channel_id = 999
        with patch.object(discord_utils_module, "save_history"):
            discord_utils_module.add_to_history(
                channel_id,
                "user",
                "Hello there",
                author_name="Alice",
                timestamp="2026-04-01T10:15:30+00:00"
            )

        stored = discord_utils_module.conversation_history[channel_id][0]
        self.assertEqual(stored["timestamp"], "2026-04-01T10:15:30+00:00")


class ConfigPropagationTests(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()

        self._runtime_originals = {
            "DATA_DIR": runtime_config_module.DATA_DIR,
            "RUNTIME_CONFIG_FILE": runtime_config_module.RUNTIME_CONFIG_FILE,
        }
        runtime_config_module.DATA_DIR = str(self.data_dir)
        runtime_config_module.RUNTIME_CONFIG_FILE = str(self.data_dir / "runtime_config.json")
        runtime_config_module.invalidate_cache()

    def tearDown(self):
        runtime_config_module.DATA_DIR = self._runtime_originals["DATA_DIR"]
        runtime_config_module.RUNTIME_CONFIG_FILE = self._runtime_originals["RUNTIME_CONFIG_FILE"]
        runtime_config_module.invalidate_cache()
        self.tearDownMemorySandbox()

    def test_runtime_config_migrates_legacy_context_message_count(self):
        legacy_path = self.data_dir / "runtime_config.json"
        legacy_path.write_text(
            json.dumps({"context_message_count": 7, "user_only_context": True}, indent=2),
            encoding="utf-8"
        )

        config = runtime_config_module.get_all()

        self.assertEqual(config["user_only_context_count"], 7)
        self.assertNotIn("context_message_count", config)

        runtime_config_module.save_config(config)
        saved = json.loads(legacy_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["user_only_context_count"], 7)
        self.assertNotIn("context_message_count", saved)

    def test_config_ui_and_api_use_user_only_context_count(self):
        page = self.client.get("/config").get_data(as_text=True)
        self.assertIn('id="user_only_context_count"', page)
        self.assertNotIn('id="context_message_count"', page)

    def test_config_page_groups_context_prompting_and_provider_controls(self):
        page = self.client.get("/config").get_data(as_text=True)

        self.assertIn('data-config-tab="context"', page)
        self.assertIn('data-config-tab="prompting"', page)
        self.assertIn('data-config-tab="providers"', page)
        self.assertIn('id="time_passage_context_enabled"', page)
        self.assertIn("prompts/system.md (read only)", page)
        self.assertIn("/prompts/other/save", page)
        self.assertIn("moveProvider(", page)

        response = self.client.post(
            "/api/config",
            json={"context_message_count": 9},
            headers=self.csrf_headers()
        )
        config_response = self.client.get("/api/config")
        config = config_response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(config_response.status_code, 200)
        self.assertEqual(config["user_only_context_count"], 9)
        self.assertNotIn("context_message_count", config)


class ProviderVisionSupportTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_only_provider_keeps_single_user_format_after_image_stripping(self):
        manager = object.__new__(providers_module.AIProviderManager)
        manager.providers = {"primary": object()}
        manager.status = {}
        manager._vision_support_overrides = {}
        manager._build_tier_order = lambda preferred_tier="": ["primary"]
        manager._try_generate = AsyncMock(return_value="ok")

        messages = [
            {
                "role": "assistant",
                "author": "Nahida",
                "content": [
                    {"type": "text", "text": "Welcome back"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]

        provider_cfg = {
            "primary": {
                "model": "test-text-model",
                "supports_vision": False,
                "timeout": 30,
                "max_tokens": 256,
                "temperature": 0.7,
                "url": "https://example.invalid",
            }
        }

        with patch.dict(providers_module.PROVIDERS, provider_cfg, clear=True):
            with patch.object(providers_module.log, "info"), \
                    patch.object(providers_module.log, "debug"), \
                    patch.object(providers_module.log, "warn"), \
                    patch.object(providers_module.log, "error"):
                result = await manager.generate(
                    messages=messages,
                    system_prompt="You are Nahida.",
                    use_single_user=True,
                )

        self.assertEqual(result, "ok")
        sent_messages = manager._try_generate.await_args.args[2]
        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0]["role"], "user")
        self.assertIn("### Instructions\nYou are Nahida.", sent_messages[0]["content"])
        self.assertIn("Nahida: Welcome back", sent_messages[0]["content"])
        self.assertIn("[Visual reference omitted for text-only model]", sent_messages[0]["content"])

    async def test_vision_provider_keeps_multimodal_payload(self):
        manager = object.__new__(providers_module.AIProviderManager)
        manager.providers = {"primary": object()}
        manager.status = {}
        manager._vision_support_overrides = {}
        manager._build_tier_order = lambda preferred_tier="": ["primary"]
        manager._try_generate = AsyncMock(return_value="ok")

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Alice: hi"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]

        provider_cfg = {
            "primary": {
                "model": "test-vision-model",
                "supports_vision": True,
                "timeout": 30,
                "max_tokens": 256,
                "temperature": 0.7,
                "url": "https://example.invalid",
            }
        }

        with patch.dict(providers_module.PROVIDERS, provider_cfg, clear=True):
            with patch.object(providers_module.log, "info"), \
                    patch.object(providers_module.log, "debug"), \
                    patch.object(providers_module.log, "warn"), \
                    patch.object(providers_module.log, "error"):
                result = await manager.generate(
                    messages=messages,
                    system_prompt="You are Nahida.",
                    use_single_user=True,
                )

        self.assertEqual(result, "ok")
        sent_messages = manager._try_generate.await_args.args[2]
        self.assertEqual(sent_messages[0]["role"], "system")
        self.assertEqual(sent_messages[0]["content"], "You are Nahida.")
        self.assertIsInstance(sent_messages[1]["content"], list)
        self.assertTrue(any(part.get("type") == "image_url" for part in sent_messages[1]["content"]))

    async def test_vision_rejection_retries_same_provider_as_text_only(self):
        manager = object.__new__(providers_module.AIProviderManager)
        manager.providers = {"primary": object()}
        manager.status = {}
        manager._vision_support_overrides = {}
        manager._build_tier_order = lambda preferred_tier="": ["primary"]
        manager._try_generate = AsyncMock(side_effect=[
            Exception("Error code: 404 - {'error': {'message': 'No endpoints found that support image input', 'code': 404}}"),
            "ok",
        ])

        messages = [
            {
                "role": "user",
                "author": "Alice",
                "content": [
                    {"type": "text", "text": "Alice: hi"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]

        provider_cfg = {
            "primary": {
                "model": "misdeclared-vision-model",
                "supports_vision": True,
                "timeout": 30,
                "max_tokens": 256,
                "temperature": 0.7,
                "url": "https://example.invalid",
            }
        }

        with patch.dict(providers_module.PROVIDERS, provider_cfg, clear=True):
            with patch.object(providers_module.log, "info"), \
                    patch.object(providers_module.log, "debug"), \
                    patch.object(providers_module.log, "warn"), \
                    patch.object(providers_module.log, "error"):
                result = await manager.generate(
                    messages=messages,
                    system_prompt="You are Nahida.",
                    use_single_user=True,
                )

        self.assertEqual(result, "ok")
        self.assertEqual(manager._try_generate.await_count, 2)
        first_messages = manager._try_generate.await_args_list[0].args[2]
        second_messages = manager._try_generate.await_args_list[1].args[2]
        self.assertIsInstance(first_messages[1]["content"], list)
        self.assertEqual(second_messages[0]["role"], "user")
        self.assertIn("[Visual reference omitted for text-only model]", second_messages[0]["content"])
        self.assertFalse(manager.can_use_vision())

    async def test_claude_models_use_provider_token_limit_without_local_cap(self):
        manager = object.__new__(providers_module.AIProviderManager)
        manager.providers = {"primary": object()}
        manager.status = {}
        manager._vision_support_overrides = {}
        manager._build_tier_order = lambda preferred_tier="": ["primary"]
        manager._try_generate = AsyncMock(return_value="ok")

        provider_cfg = {
            "primary": {
                "model": "claude-3-opus",
                "supports_vision": False,
                "timeout": 30,
                "max_tokens": 8192,
                "temperature": 0.7,
                "url": "https://example.invalid",
            }
        }

        with patch.dict(providers_module.PROVIDERS, provider_cfg, clear=True):
            with patch.object(providers_module.log, "info"), \
                    patch.object(providers_module.log, "debug"), \
                    patch.object(providers_module.log, "warn"), \
                    patch.object(providers_module.log, "error"):
                result = await manager.generate(
                    messages=[{"role": "user", "content": "Hello"}],
                    system_prompt="You are Nahida.",
                    use_single_user=False,
                )

        self.assertEqual(result, "ok")
        self.assertEqual(manager._try_generate.await_args.args[4], 8192)
