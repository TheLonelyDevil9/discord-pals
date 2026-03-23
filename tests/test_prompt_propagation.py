import json
import unittest
from unittest.mock import AsyncMock, patch

import module_stubs  # noqa: F401
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
                    {"type": "text", "text": "Emoji reference: 😀 (grinning face)"},
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
        self.assertIn("Emoji reference: 😀 (grinning face)", sent_messages[0]["content"])
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
                    {"type": "text", "text": "Emoji reference: 😀 (grinning face)"},
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
                    {"type": "text", "text": "Emoji reference: 😀 (grinning face)"},
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
