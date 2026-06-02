import types
import unittest
from unittest.mock import AsyncMock, patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
import dm_images as dm_images_module


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeDMChannel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.sent_messages = []
        self.sent_files = []
        self.sent_message_objects = []

    def typing(self):
        return _FakeTyping()

    async def send(self, content=None, file=None):
        self.sent_messages.append(content)
        self.sent_files.append(file)
        sent = _FakeSentMessage(message_id=len(self.sent_messages))
        self.sent_message_objects.append(sent)
        return sent


class _FakeSentMessage:
    def __init__(self, message_id):
        self.id = message_id
        self.created_at = None
        self.reactions = []

    async def add_reaction(self, reaction):
        self.reactions.append(reaction)


class _FakeUser:
    def __init__(self, dm_channel):
        self.dm_channel = None
        self._dm_channel = dm_channel
        self.create_dm_calls = 0

    async def create_dm(self):
        self.create_dm_calls += 1
        self.dm_channel = self._dm_channel
        return self._dm_channel


class _FakeClient:
    def __init__(self, user):
        self._user = user

    def get_channel(self, channel_id):
        return None

    async def fetch_channel(self, channel_id):
        return None

    def get_user(self, user_id):
        return self._user


class DMFollowupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dm_access_patch = patch.object(
            bot_instance_module.runtime_config,
            "is_dm_response_allowed",
            return_value=(True, None),
        )
        self._dm_access_patch.start()

    async def asyncTearDown(self):
        self._dm_access_patch.stop()

    async def test_followup_cycle_recovers_when_dm_channel_is_not_cached(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        dm_channel = _FakeDMChannel(channel_id=555)
        fake_user = _FakeUser(dm_channel)
        instance.client = _FakeClient(fake_user)
        instance._dm_followup_state = {
            42: {
                "last_user_msg": 1_000.0,
                "followups_sent": 0,
                "last_followup": 0,
                "channel_id": 555,
            }
        }

        with patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "dm_followup_enabled": True,
            "global_paused": False,
            "dm_followup_timeout_minutes": 15,
            "dm_followup_max_count": 1,
            "dm_followup_cooldown_hours": 24,
        }.get(key, default)), \
                patch.object(bot_instance_module, "get_history", return_value=[{"role": "user", "author": "Alice", "content": "Hi"}]), \
                patch.object(bot_instance_module, "add_to_history") as add_history_mock, \
                patch.object(bot_instance_module.provider_manager, "generate", new=AsyncMock(return_value="Checking in.")), \
                patch.object(bot_instance_module.log, "info"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "debug"), \
                patch.object(bot_instance_module.asyncio, "sleep", new=AsyncMock(return_value=None)):
            sent = await instance._run_dm_followup_cycle(now=2_000.0)

        self.assertEqual(sent, 1)
        self.assertEqual(dm_channel.sent_messages, ["Checking in."])
        self.assertEqual(fake_user.create_dm_calls, 1)
        self.assertEqual(instance._dm_followup_state[42]["followups_sent"], 1)
        self.assertEqual(instance._dm_followup_state[42]["last_followup"], 2_000.0)
        add_history_mock.assert_called_once_with(
            "dm:Nahida:user:42", "assistant", "Checking in.", author_name="Nahida", timestamp=None
        )

    async def test_followup_cycle_strips_reaction_tags_before_dm_send(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"
        instance.character = types.SimpleNamespace(name="Firefly")
        dm_channel = _FakeDMChannel(channel_id=555)
        fake_user = _FakeUser(dm_channel)
        instance.client = _FakeClient(fake_user)
        instance._dm_followup_state = {
            42: {
                "last_user_msg": 1_000.0,
                "followups_sent": 0,
                "last_followup": 0,
                "channel_id": 555,
                "user_name": "Alice",
            }
        }

        with patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "dm_followup_enabled": True,
            "global_paused": False,
            "dm_followup_timeout_minutes": 15,
            "dm_followup_max_count": 1,
            "dm_followup_cooldown_hours": 24,
            "dm_image_generation_enabled": False,
        }.get(key, default)), \
                patch.object(bot_instance_module, "get_history", return_value=[{"role": "user", "author": "Alice", "content": "Good night"}]), \
                patch.object(bot_instance_module, "add_to_history") as add_history_mock, \
                patch.object(bot_instance_module.provider_manager, "generate", new=AsyncMock(return_value="[REACT: 😊] Good morning, sleepyhead.")), \
                patch.object(bot_instance_module.log, "info"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "debug"), \
                patch.object(bot_instance_module.asyncio, "sleep", new=AsyncMock(return_value=None)):
            sent = await instance._run_dm_followup_cycle(now=2_000.0)

        self.assertEqual(sent, 1)
        self.assertEqual(dm_channel.sent_messages, ["Good morning, sleepyhead."])
        self.assertEqual(dm_channel.sent_message_objects[0].reactions, ["😊"])
        add_history_mock.assert_called_once_with(
            "dm:Firefly:user:42", "assistant", "Good morning, sleepyhead.", author_name="Firefly", timestamp=None
        )

    async def test_followup_cycle_respects_unavailable_schedule(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        instance.client = _FakeClient(_FakeUser(_FakeDMChannel(channel_id=555)))
        instance._dm_followup_state = {
            42: {
                "last_user_msg": 1_000.0,
                "followups_sent": 0,
                "last_followup": 0,
                "channel_id": 555,
            }
        }

        with patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "dm_followup_enabled": True,
            "global_paused": False,
        }.get(key, default)), \
                patch.object(bot_instance_module.runtime_config, "is_bot_available", return_value=False), \
                patch.object(bot_instance_module.provider_manager, "generate", new=AsyncMock()) as generate_mock:
            sent = await instance._run_dm_followup_cycle(now=2_000.0)

        self.assertEqual(sent, 0)
        generate_mock.assert_not_awaited()
        self.assertEqual(instance._dm_followup_state[42]["followups_sent"], 0)

    async def test_followup_cycle_respects_global_dm_disable(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        instance.client = _FakeClient(_FakeUser(_FakeDMChannel(channel_id=555)))
        instance._dm_followup_state = {
            42: {
                "last_user_msg": 1_000.0,
                "followups_sent": 0,
                "last_followup": 0,
                "channel_id": 555,
            }
        }

        with patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "dm_followup_enabled": True,
            "global_paused": False,
            "dm_followup_timeout_minutes": 15,
            "dm_followup_max_count": 1,
            "dm_followup_cooldown_hours": 24,
        }.get(key, default)), \
                patch.object(bot_instance_module.runtime_config, "is_dm_response_allowed", return_value=(False, "dm_responses_disabled")), \
                patch.object(bot_instance_module.provider_manager, "generate", new=AsyncMock()) as generate_mock:
            sent = await instance._run_dm_followup_cycle(now=2_000.0)

        self.assertEqual(sent, 0)
        generate_mock.assert_not_awaited()
        self.assertEqual(instance._dm_followup_state[42]["followups_sent"], 0)

    async def test_followup_cycle_preserves_single_newline_response(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"
        instance.character = types.SimpleNamespace(name="Firefly")
        dm_channel = _FakeDMChannel(channel_id=555)
        fake_user = _FakeUser(dm_channel)
        instance.client = _FakeClient(fake_user)
        instance._dm_followup_state = {
            42: {
                "last_user_msg": 1_000.0,
                "followups_sent": 0,
                "last_followup": 0,
                "channel_id": 555,
                "user_name": "Alice",
            }
        }

        with patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "dm_followup_enabled": True,
            "global_paused": False,
            "dm_followup_timeout_minutes": 15,
            "dm_followup_max_count": 1,
            "dm_followup_cooldown_hours": 24,
        }.get(key, default)), \
                patch.object(bot_instance_module, "get_history", return_value=[{"role": "user", "author": "Alice", "content": "Hi"}]), \
                patch.object(bot_instance_module, "add_to_history") as add_history_mock, \
                patch.object(bot_instance_module.provider_manager, "generate", new=AsyncMock(return_value="Good morning.\nDid you sleep well?")), \
                patch.object(bot_instance_module.log, "info"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "debug"), \
                patch.object(bot_instance_module.asyncio, "sleep", new=AsyncMock(return_value=None)), \
                patch.object(bot_instance_module.random, "uniform", return_value=0.0):
            sent = await instance._run_dm_followup_cycle(now=2_000.0)

        self.assertEqual(sent, 1)
        self.assertEqual(dm_channel.sent_messages, ["Good morning.\nDid you sleep well?"])
        add_history_mock.assert_called_once_with(
            "dm:Firefly:user:42", "assistant", "Good morning.\nDid you sleep well?", author_name="Firefly", timestamp=None
        )

    async def test_followup_cycle_long_gap_uses_distinct_memory_topic_prompt(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        dm_channel = _FakeDMChannel(channel_id=555)
        fake_user = _FakeUser(dm_channel)
        instance.client = _FakeClient(fake_user)
        instance._dm_followup_state = {
            42: {
                "last_user_msg": 1_000.0,
                "followups_sent": 0,
                "last_followup": 0,
                "channel_id": 555,
                "user_name": "Alice",
            }
        }
        generate_mock = AsyncMock(return_value="Checking in.")
        history = [
            {"role": "assistant", "author": "Nahida", "content": "It reminds me of how knowledge works, in a way."},
            {"role": "user", "author": "Alice", "content": "Each new thing you learn opens doors to places you couldn't reach before."},
            {"role": "assistant", "author": "Nahida", "content": "Have you found any particularly tricky spots you're itching to get back to?"},
        ]
        memories = (
            "What you know about Alice:\n"
            "- Loves stargazing on quiet nights.\n"
            "- Keeps a notebook full of mushroom sketches."
        )

        with patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "dm_followup_enabled": True,
            "global_paused": False,
            "dm_followup_timeout_minutes": 120,
            "dm_followup_max_count": 1,
            "dm_followup_cooldown_hours": 24,
        }.get(key, default)), \
                patch.object(bot_instance_module, "get_history", return_value=history), \
                patch.object(bot_instance_module.memory_manager, "get_all_memories_for_context", return_value=memories), \
                patch.object(bot_instance_module, "add_to_history"), \
                patch.object(bot_instance_module.provider_manager, "generate", new=generate_mock), \
                patch.object(bot_instance_module.log, "info"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "debug"), \
                patch.object(bot_instance_module.asyncio, "sleep", new=AsyncMock(return_value=None)):
            sent = await instance._run_dm_followup_cycle(now=19_000.0)

        self.assertEqual(sent, 1)
        prompt = generate_mock.await_args.kwargs["messages"][0]["content"]
        system_prompt = generate_mock.await_args.kwargs["system_prompt"]
        self.assertIn("do not continue the last thread directly", prompt.lower())
        self.assertIn("Elapsed time:", prompt)
        self.assertIn("Infer lightly", prompt)
        self.assertIn("normal punctuation", prompt.lower())
        self.assertIn("normal punctuation", system_prompt.lower())
        self.assertIn("Loves stargazing on quiet nights.", prompt)
        self.assertIn("Recent topic:", prompt)

    async def test_followup_cycle_can_send_generated_image_in_dm(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"
        instance.character = types.SimpleNamespace(name="Firefly")
        dm_channel = _FakeDMChannel(channel_id=555)
        fake_user = _FakeUser(dm_channel)
        instance.client = _FakeClient(fake_user)
        instance._dm_followup_state = {
            42: {
                "last_user_msg": 1_000.0,
                "followups_sent": 0,
                "last_followup": 0,
                "channel_id": 555,
                "user_name": "Alice",
            }
        }

        with patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "dm_followup_enabled": True,
            "global_paused": False,
            "dm_followup_timeout_minutes": 15,
            "dm_followup_max_count": 1,
            "dm_followup_cooldown_hours": 24,
            "dm_image_generation_enabled": True,
            "dm_image_generation_chance": 1.0,
            "dm_image_generation_caption_chance": 0.0,
            "dm_image_generation_preferred_tier": "primary",
            "dm_image_generation_prompt": "A nonsense meme image",
        }.get(key, default)), \
                patch.object(bot_instance_module, "get_history", return_value=[{"role": "user", "author": "Alice", "content": "send me something cursed"}]), \
                patch.object(dm_images_module, "add_to_history") as add_history_mock, \
                patch.object(bot_instance_module.provider_manager, "generate", new=AsyncMock()) as generate_mock, \
                patch.object(bot_instance_module.provider_manager, "generate_image", new=AsyncMock(return_value={
                    "bytes": b"fake-png",
                    "filename": "meme.png",
                })) as image_mock, \
                patch.object(bot_instance_module.log, "info"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "debug"), \
                patch.object(bot_instance_module.asyncio, "sleep", new=AsyncMock(return_value=None)), \
                patch.object(dm_images_module.random, "random", return_value=0.0):
            sent = await instance._run_dm_followup_cycle(now=2_000.0)

        self.assertEqual(sent, 1)
        self.assertEqual(dm_channel.sent_messages, [None])
        self.assertEqual(dm_channel.sent_files[0].filename, "meme.png")
        self.assertEqual(instance._dm_followup_state[42]["followups_sent"], 1)
        self.assertEqual(instance._dm_followup_state[42]["last_followup"], 2_000.0)
        self.assertIn("nonsense meme", image_mock.await_args.args[0])
        self.assertEqual(image_mock.await_args.kwargs["preferred_tier"], "primary")
        generate_mock.assert_not_awaited()
        add_history_mock.assert_called_once_with(
            "dm:Firefly:user:42", "assistant", "[Sent a generated image]", author_name="Firefly", timestamp=None
        )

    async def test_followup_cycle_does_not_advance_state_when_image_send_fails(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"
        instance.character = types.SimpleNamespace(name="Firefly")
        dm_channel = _FakeDMChannel(channel_id=555)
        fake_user = _FakeUser(dm_channel)
        instance.client = _FakeClient(fake_user)
        instance._dm_followup_state = {
            42: {
                "last_user_msg": 1_000.0,
                "followups_sent": 0,
                "last_followup": 0,
                "channel_id": 555,
                "user_name": "Alice",
            }
        }

        with patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "dm_followup_enabled": True,
            "global_paused": False,
            "dm_followup_timeout_minutes": 15,
            "dm_followup_max_count": 1,
            "dm_followup_cooldown_hours": 24,
            "dm_image_generation_enabled": True,
            "dm_image_generation_chance": 1.0,
            "dm_image_generation_caption_chance": 0.0,
        }.get(key, default)), \
                patch.object(bot_instance_module, "get_history", return_value=[{"role": "user", "author": "Alice", "content": "hi"}]), \
                patch.object(dm_images_module, "add_to_history") as add_history_mock, \
                patch.object(bot_instance_module.provider_manager, "generate_image", new=AsyncMock(return_value=None)), \
                patch.object(bot_instance_module.log, "info"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "debug"), \
                patch.object(dm_images_module.random, "random", return_value=0.0):
            sent = await instance._run_dm_followup_cycle(now=2_000.0)

        self.assertEqual(sent, 0)
        self.assertEqual(instance._dm_followup_state[42]["followups_sent"], 0)
        self.assertEqual(instance._dm_followup_state[42]["last_followup"], 0)
        add_history_mock.assert_not_called()

    async def test_explicit_dm_image_request_uses_context_and_records_history(self):
        dm_channel = _FakeDMChannel(channel_id=555)
        history = [{"role": "user", "author": "Alice", "content": "The train window looks lonely tonight."}]

        with patch.object(dm_images_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "dm_image_generation_enabled": True,
            "dm_image_generation_preferred_tier": "primary",
            "dm_image_generation_caption_chance": 0.0,
            "dm_image_generation_prompt": "soft train-window scene",
        }.get(key, default)), \
                patch.object(dm_images_module.provider_manager, "generate_image", new=AsyncMock(return_value={
                    "bytes": b"fake-png",
                    "filename": "requested.png",
                })) as image_mock, \
                patch.object(dm_images_module, "add_to_history") as add_history_mock, \
                patch.object(dm_images_module.log, "info"), \
                patch.object(dm_images_module.log, "warn"):
            sent = await dm_images_module.generate_and_send_requested_dm_image(
                bot_name="Firefly",
                channel=dm_channel,
                user_id=42,
                user_name="Alice",
                character_name="Firefly",
                request_text="can you draw me a picture of the window?",
                history=history,
                history_id="dm:Firefly:user:42",
                system_prompt="Firefly likes gentle stargazing scenes.",
                context_messages=[{"role": "system", "content": "Recent memories mention the Astral Express."}],
                req_id="req-image",
            )

        self.assertTrue(sent)
        self.assertEqual(dm_channel.sent_files[0].filename, "requested.png")
        prompt = image_mock.await_args.args[0]
        self.assertIn("draw me a picture", prompt)
        self.assertIn("Firefly likes gentle stargazing scenes", prompt)
        self.assertIn("Astral Express", prompt)
        self.assertEqual(image_mock.await_args.kwargs["preferred_tier"], "primary")
        add_history_mock.assert_called_once_with(
            "dm:Firefly:user:42", "assistant", "[Sent a generated image]", author_name="Firefly", timestamp=None
        )

    async def test_bot_instance_handles_explicit_dm_image_request_before_text_generation(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Firefly"
        instance.character = types.SimpleNamespace(name="Firefly")
        dm_channel = _FakeDMChannel(channel_id=555)
        message = types.SimpleNamespace(channel=dm_channel)
        context = {
            "is_dm": True,
            "content": "please draw me a picture of the train",
            "channel_id": "dm:Firefly:user:42",
            "user_id": 42,
            "user_name": "Alice",
            "system_prompt": "SYSTEM",
            "messages_for_api": [{"role": "user", "content": "Alice: please draw me a picture of the train"}],
            "req_id": "req-image",
        }

        with patch.object(dm_images_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "dm_image_generation_enabled": True,
        }.get(key, default)), \
                patch.object(bot_instance_module, "get_history", return_value=[]), \
                patch.object(dm_images_module, "generate_and_send_requested_dm_image", new=AsyncMock(return_value=True)) as image_mock:
            handled = await instance._maybe_handle_dm_image_request(context, message)

        self.assertTrue(handled)
        self.assertEqual(image_mock.await_args.kwargs["request_text"], "please draw me a picture of the train")
