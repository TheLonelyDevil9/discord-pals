import types
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
import dashboard as dashboard_module
import discord_utils as discord_utils_module
import memory as memory_module
import reminder_delivery as reminder_delivery_module
import reminders as reminders_module
import runtime_config as runtime_config_module
import time_utils as time_utils_module

from history_sandbox import HistorySandbox
from test_support import MemorySandboxMixin


class ReminderManagerTests(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()

    def tearDown(self):
        self.tearDownMemorySandbox()

    def test_create_or_update_reminder_deduplicates_same_event_and_due_time(self):
        due_at = datetime(2026, 4, 5, 3, 30, tzinfo=timezone.utc)

        status_one, reminder_one = reminders_module.reminder_manager.create_or_update_reminder(
            bot_name="Nahida",
            target_user_id=42,
            target_user_name="Alice",
            source_type="channel",
            source_channel_id=77,
            source_channel_name="tea-room",
            source_guild_id=5,
            source_guild_name="Sumeru",
            timezone_name="Asia/Calcutta",
            timezone_offset_minutes=330,
            timezone_source="user",
            event_summary="Catch the flight",
            normalized_event="catch the flight",
            due_at_utc=due_at,
            pre_due_at_utc=due_at - timedelta(hours=3),
            creation_mode="explicit",
        )
        status_two, reminder_two = reminders_module.reminder_manager.create_or_update_reminder(
            bot_name="Nahida",
            target_user_id=42,
            target_user_name="Alice",
            source_type="dm",
            source_channel_id=88,
            source_channel_name="DM",
            source_guild_id=None,
            source_guild_name="DM",
            timezone_name="Asia/Calcutta",
            timezone_offset_minutes=330,
            timezone_source="user",
            event_summary="Catch the flight",
            normalized_event="catch the flight",
            due_at_utc=due_at,
            pre_due_at_utc=None,
            creation_mode="explicit",
        )

        self.assertEqual(status_one, "created")
        self.assertEqual(status_two, "updated")
        self.assertEqual(reminder_one["id"], reminder_two["id"])
        self.assertEqual(len(reminders_module.reminder_manager.reminders), 1)
        stored = reminders_module.reminder_manager.reminders[0]
        self.assertEqual(stored["source_type"], "dm")
        self.assertEqual(stored["source_channel_id"], 88)
        self.assertIsNone(stored["pre_due_at_utc"])

    def test_due_delivery_selection_handles_pre_stage_and_late_skip(self):
        now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        _, active = reminders_module.reminder_manager.create_or_update_reminder(
            bot_name="Nahida",
            target_user_id=42,
            target_user_name="Alice",
            source_type="channel",
            source_channel_id=77,
            source_channel_name="tea-room",
            source_guild_id=5,
            source_guild_name="Sumeru",
            timezone_name="UTC",
            timezone_offset_minutes=0,
            timezone_source="bot",
            event_summary="Doctor appointment",
            normalized_event="doctor appointment",
            due_at_utc=now + timedelta(hours=2),
            pre_due_at_utc=now - timedelta(minutes=5),
            creation_mode="auto",
        )
        _, stale = reminders_module.reminder_manager.create_or_update_reminder(
            bot_name="Nahida",
            target_user_id=99,
            target_user_name="Bob",
            source_type="dm",
            source_channel_id=101,
            source_channel_name="DM",
            source_guild_id=None,
            source_guild_name="DM",
            timezone_name="UTC",
            timezone_offset_minutes=0,
            timezone_source="process",
            event_summary="Past event",
            normalized_event="past event",
            due_at_utc=now - timedelta(hours=7),
            pre_due_at_utc=None,
            creation_mode="auto",
        )

        deliveries = reminders_module.reminder_manager.get_due_deliveries("Nahida", now=now)

        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["reminder"]["id"], active["id"])
        self.assertEqual(deliveries[0]["stage"], "pre")
        stale_entry = next(rem for rem in reminders_module.reminder_manager.reminders if rem["id"] == stale["id"])
        self.assertEqual(stale_entry["status"], "skipped")

        reminders_module.reminder_manager.mark_delivery_result(active["id"], stage="pre", success=True, sent_at=now)
        due_deliveries = reminders_module.reminder_manager.get_due_deliveries(
            "Nahida",
            now=now + timedelta(hours=3)
        )
        self.assertEqual(len(due_deliveries), 1)
        self.assertEqual(due_deliveries[0]["stage"], "due")


class ReminderTimezoneAndDashboardTests(MemorySandboxMixin, unittest.TestCase):
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

        class FakeBot:
            def __init__(self, name):
                self.name = name
                self.character = None
                self.character_name = None
                self.nicknames = ""
                self.client = types.SimpleNamespace(is_ready=lambda: False)

        dashboard_module.bot_instances = [FakeBot("Nahida")]

    def tearDown(self):
        runtime_config_module.DATA_DIR = self._runtime_originals["DATA_DIR"]
        runtime_config_module.RUNTIME_CONFIG_FILE = self._runtime_originals["RUNTIME_CONFIG_FILE"]
        runtime_config_module.invalidate_cache()
        self.tearDownMemorySandbox()

    def test_user_timezone_overrides_bot_timezone(self):
        runtime_config_module.set_bot_timezone("Nahida", "UTC")
        time_utils_module.timezone_manager.set_user_timezone(42, "Asia/Calcutta")

        context = time_utils_module.get_timezone_context(user_id=42, bot_name="Nahida")
        fallback = time_utils_module.get_timezone_context(user_id=999, bot_name="Nahida")

        self.assertEqual(context["timezone_name"], "Asia/Calcutta")
        self.assertEqual(context["timezone_source"], "user")
        self.assertEqual(fallback["timezone_name"], "UTC")
        self.assertEqual(fallback["timezone_source"], "bot")

    def test_dashboard_reminder_api_and_bot_timezone_endpoint(self):
        due_at = datetime(2026, 4, 5, 3, 30, tzinfo=timezone.utc)
        reminders_module.reminder_manager.create_or_update_reminder(
            bot_name="Nahida",
            target_user_id=42,
            target_user_name="Alice",
            source_type="channel",
            source_channel_id=77,
            source_channel_name="tea-room",
            source_guild_id=5,
            source_guild_name="Sumeru",
            timezone_name="Asia/Calcutta",
            timezone_offset_minutes=330,
            timezone_source="user",
            event_summary="Catch the flight",
            normalized_event="catch the flight",
            due_at_utc=due_at,
            pre_due_at_utc=None,
            creation_mode="explicit",
        )

        list_response = self.client.get("/api/reminders?status=pending")
        list_data = list_response.get_json()
        cancel_response = self.client.post(
            "/api/reminders/cancel",
            json={"ids": [list_data["reminders"][0]["id"]]},
            headers=self.csrf_headers()
        )
        timezone_response = self.client.post(
            "/api/bot-timezones",
            json={"bot_name": "Nahida", "timezone": "Asia/Calcutta"},
            headers=self.csrf_headers()
        )
        page = self.client.get("/reminders").get_data(as_text=True)

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_data["total"], 1)
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.get_json()["cancelled"], 1)
        self.assertEqual(timezone_response.status_code, 200)
        self.assertEqual(runtime_config_module.get_bot_timezone("Nahida"), "Asia/Calcutta")
        self.assertIn("Cancel Pending Selected", page)

    def test_config_page_renders_timezone_picker(self):
        page = self.client.get("/config").get_data(as_text=True)

        self.assertIn('class="timezone-select"', page)
        self.assertIn("Use process/server timezone", page)
        self.assertIn("/api/timezones", page)
        self.assertIn("Bot Availability Schedules", page)
        self.assertIn("schedule-bot-panel", page)

    def test_bot_schedule_endpoint_accepts_multiple_windows(self):
        response = self.client.post(
            "/api/bot-schedules",
            json={
                "bot_name": "Nahida",
                "enabled": True,
                "timezone": "UTC",
                "windows": [
                    {"days": ["mon", "wed"], "start": "09:00", "end": "11:30"},
                    {"days": ["fri"], "start": "22:00", "end": "08:00"},
                ],
            },
            headers=self.csrf_headers()
        )

        schedule = runtime_config_module.get_bot_schedule("Nahida")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")
        self.assertTrue(schedule["enabled"])
        self.assertEqual(schedule["timezone"], "UTC")
        self.assertEqual(len(schedule["unavailable"]), 2)
        self.assertEqual(schedule["unavailable"][0]["days"], ["mon", "wed"])
        self.assertEqual(schedule["unavailable"][1]["start"], "22:00")

    def test_bot_schedule_endpoint_accepts_dashboard_unavailable_payload(self):
        response = self.client.post(
            "/api/bot-schedules",
            json={
                "bot_name": "Nahida",
                "enabled": True,
                "timezone": "UTC",
                "unavailable": [
                    {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "start": "03:00", "end": "08:00"},
                ],
            },
            headers=self.csrf_headers()
        )

        schedule = runtime_config_module.get_bot_schedule("Nahida")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")
        self.assertEqual(schedule["unavailable"][0]["start"], "03:00")
        self.assertEqual(schedule["unavailable"][0]["end"], "08:00")

    def test_auto_memory_api_resolves_guild_name_labels(self):
        self.manager.auto_memories = {
            "server:123:user:42": [
                self.manager._build_auto_memory_entry(
                    "They like tea.",
                    user_id=42,
                    server_id=123,
                    user_name="Alice",
                )
            ]
        }

        with patch.object(
            dashboard_module,
            "_get_visible_topology",
            return_value={
                "guilds": [{"id": 123, "name": "Febs' Bruary"}],
                "channels": [],
                "channels_by_id": {},
                "accessible_channel_ids": set(),
            },
        ):
            response = self.client.get("/api/v2/memories/auto")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["memories"][0]["server_name"], "Febs' Bruary")
        self.assertEqual(payload["memories"][0]["scope_label"], "Febs' Bruary")
        self.assertEqual(payload["memories"][0]["key_label"], "Febs' Bruary • Alice")

    def test_lore_edit_endpoint_updates_manual_lore(self):
        self.manager.manual_lore = {
            "user:42": [
                memory_module.MemoryManager._build_lore_entry(
                    "Old lore note.",
                    added_by="dashboard",
                )
            ]
        }

        response = self.client.put(
            "/api/v2/memories/lore/edit",
            json={"key": "user:42", "index": 0, "content": "Updated lore note."},
            headers=self.csrf_headers()
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.manager.manual_lore["user:42"][0]["content"], "Updated lore note.")
        self.assertFalse(self.manager.manual_lore["user:42"][0]["auto"])


class ReminderBotFlowTests(MemorySandboxMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setUpMemorySandbox()

    def tearDown(self):
        self.tearDownMemorySandbox()

    async def test_reminder_capture_creates_persisted_reminder(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character_name = "nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
        instance._send_auxiliary_followup = AsyncMock(return_value=True)
        timezone_info = ZoneInfo("Asia/Calcutta")
        due_local = (datetime.now(timezone_info) + timedelta(days=2)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        pre_due_local = due_local - timedelta(hours=3)
        instance._extract_reminder_payload = AsyncMock(return_value={
            "action": "create",
            "creation_mode": "explicit",
            "event_summary": "Catch the flight",
            "normalized_event": "catch the flight",
            "due_local_iso": due_local.strftime("%Y-%m-%dT%H:%M:%S"),
            "pre_due_local_iso": pre_due_local.strftime("%Y-%m-%dT%H:%M:%S"),
        })

        context = {
            "channel_id": "dm:Nahida:user:42",
            "discord_channel_id": 77,
            "content": "Please remind me about my flight at 9 AM on Saturday.",
            "user_id": 42,
            "user_name": "Alice",
            "is_dm": True,
            "guild_id": None,
            "timezone_context": {
                "timezone_name": "Asia/Calcutta",
                "timezone_source": "user",
                "offset_minutes": 330,
                "tzinfo": timezone_info,
            },
        }
        message = types.SimpleNamespace(
            channel=types.SimpleNamespace(id=77, name="DM"),
            _interaction=None,
            reply=AsyncMock(),
        )
        request = {
            "allow_auto_reminders": True,
            "pending_reminder_clarification": None,
            "guild": None,
        }

        with patch.object(bot_instance_module, "reminder_manager", reminders_module.reminder_manager), \
                patch.object(bot_instance_module.log, "info"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "debug"):
            await instance._maybe_handle_reminder_capture(context, message, request)

        self.assertEqual(len(reminders_module.reminder_manager.reminders), 1)
        reminder = reminders_module.reminder_manager.reminders[0]
        self.assertEqual(reminder["bot_name"], "Nahida")
        self.assertEqual(reminder["target_user_id"], 42)
        self.assertEqual(reminder["creation_mode"], "explicit")
        self.assertEqual(reminder["pre_status"], "pending")
        self.assertEqual(reminder["source_type"], "dm")
        self.assertEqual(reminder["source_channel_id"], 77)

    def _make_dm_bot(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character_name = "nahida"
        instance.character = types.SimpleNamespace(name="Nahida", example_dialogue="")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
        instance._processed_message_ids = set()
        instance._gather_mentioned_user_context = AsyncMock(return_value="")
        instance._recall_channel_history = AsyncMock(return_value=0)
        instance._send_auxiliary_followup = AsyncMock(return_value=True)
        return instance

    def _dm_request(self, content):
        dm_channel = bot_instance_module.discord.DMChannel()
        dm_channel.id = 77
        message = types.SimpleNamespace(
            id=1234,
            content=content,
            channel=dm_channel,
            guild=None,
            mentions=[],
            author=types.SimpleNamespace(id=42, bot=False),
            created_at=None,
            _interaction=None,
            reply=AsyncMock(),
        )
        return {
            "message": message,
            "content": content,
            "guild": None,
            "attachments": [],
            "user_name": "Alice",
            "is_dm": True,
            "user_id": 42,
            "sticker_info": None,
            "allow_auto_reminders": True,
            "pending_reminder_clarification": None,
        }

    async def _build_dm_context(self, instance, request):
        with ExitStack() as stack:
            stack.enter_context(patch.object(bot_instance_module, "get_history", return_value=[]))
            stack.enter_context(patch.object(bot_instance_module, "was_recently_cleared", return_value=False))
            stack.enter_context(patch.object(bot_instance_module, "acknowledge_cleared"))
            stack.enter_context(patch.object(bot_instance_module, "add_to_history"))
            stack.enter_context(patch.object(bot_instance_module, "set_channel_name"))
            stack.enter_context(patch.object(bot_instance_module.stats_manager, "record_message"))
            stack.enter_context(patch.object(bot_instance_module.metrics_manager, "record_message"))
            stack.enter_context(patch.object(bot_instance_module.memory_manager, "get_bot_lore", return_value=""))
            stack.enter_context(patch.object(bot_instance_module.memory_manager, "get_all_memories_for_context", return_value=""))
            stack.enter_context(patch.object(bot_instance_module.character_manager, "build_system_prompt", Mock(return_value="SYSTEM")))
            stack.enter_context(patch.object(bot_instance_module.character_manager, "build_chatroom_context", Mock(return_value="CHATROOM")))
            stack.enter_context(patch.object(bot_instance_module, "get_other_bot_names", return_value=[]))
            stack.enter_context(patch.object(
                bot_instance_module,
                "format_history_split",
                Mock(return_value=([], [{"role": "user", "content": "Alice: hi"}])),
            ))
            stack.enter_context(patch.object(
                bot_instance_module.runtime_config,
                "get",
                side_effect=lambda key, default=None: {"time_passage_context_enabled": False}.get(key, default),
            ))
            stack.enter_context(patch.object(bot_instance_module.log, "info"))
            stack.enter_context(patch.object(bot_instance_module.log, "debug"))
            return await instance._build_request_context(request)

    async def test_reminder_asked_for_in_a_dm_is_created_for_the_dm_channel(self):
        instance = self._make_dm_bot()
        request = self._dm_request("Remind me to water the plants tomorrow at 8.")
        due_local = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=8, minute=0, second=0, microsecond=0)
        instance._extract_reminder_payload = AsyncMock(return_value={
            "action": "create",
            "creation_mode": "explicit",
            "event_summary": "Water the plants",
            "normalized_event": "water the plants",
            "due_local_iso": due_local.strftime("%Y-%m-%dT%H:%M:%S"),
        })

        context = await self._build_dm_context(instance, request)
        with patch.object(bot_instance_module, "reminder_manager", reminders_module.reminder_manager), \
                patch.object(bot_instance_module.log, "info"):
            await instance._maybe_handle_reminder_capture(context, request["message"], request)

        self.assertEqual(context["channel_id"], "dm:Nahida:user:42")
        self.assertEqual(instance._extract_reminder_payload.await_args.kwargs["channel_id"], "dm:Nahida:user:42")
        reminders = reminders_module.reminder_manager.reminders
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["source_type"], "dm")
        self.assertEqual(reminders[0]["source_channel_id"], 77)

    async def test_clarification_asked_for_in_a_dm_is_stored_for_the_dm_channel(self):
        instance = self._make_dm_bot()
        request = self._dm_request("Remind me about the dentist.")
        instance._extract_reminder_payload = AsyncMock(return_value={
            "action": "clarify",
            "event_summary": "Dentist appointment",
            "normalized_event": "dentist appointment",
            "clarification_prompt": "When is the appointment?",
        })
        instance._generate_reminder_clarification_message = AsyncMock(return_value="When is it?")

        context = await self._build_dm_context(instance, request)
        with patch.object(bot_instance_module, "reminder_manager", reminders_module.reminder_manager):
            await instance._maybe_handle_reminder_capture(context, request["message"], request)

        draft = reminders_module.reminder_manager.get_pending_clarification(
            bot_name="Nahida", target_user_id=42, source_channel_id=77,
        )
        self.assertIsNotNone(draft)
        self.assertEqual(draft["source_type"], "dm")
        instance._send_auxiliary_followup.assert_awaited_once()

    def test_dm_message_finds_the_clarification_it_answers(self):
        instance = self._make_dm_bot()
        message = self._dm_request("Thursday at 3")["message"]
        reminders_module.reminder_manager.create_pending_clarification(
            bot_name="Nahida", target_user_id=42, target_user_name="Alice",
            source_channel_id=77, source_channel_name="DM", source_guild_id=None,
            source_guild_name="DM", source_type="dm", timezone_name="UTC",
            timezone_offset_minutes=0, timezone_source="process",
            event_summary="Dentist appointment", normalized_event="dentist appointment",
            creation_mode="explicit", clarification_prompt="When is the appointment?",
        )

        with patch.object(bot_instance_module, "reminder_manager", reminders_module.reminder_manager):
            draft = instance._pending_reminder_clarification(message)

        self.assertEqual(draft["event_summary"], "Dentist appointment")

    def test_server_clarification_does_not_break_a_dm_message_from_the_same_user(self):
        instance = self._make_dm_bot()
        message = self._dm_request("hello again")["message"]
        reminders_module.reminder_manager.create_pending_clarification(
            bot_name="Nahida", target_user_id=42, target_user_name="Alice",
            source_channel_id=88, source_channel_name="tea-room", source_guild_id=5,
            source_guild_name="Sumeru", source_type="channel", timezone_name="UTC",
            timezone_offset_minutes=0, timezone_source="process",
            event_summary="Tea break", normalized_event="tea break",
            creation_mode="auto", clarification_prompt="What time?",
        )

        with patch.object(bot_instance_module, "reminder_manager", reminders_module.reminder_manager):
            self.assertIsNone(instance._pending_reminder_clarification(message))

    async def test_reminder_cycle_marks_due_delivery_completed(self):
        now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        _, reminder = reminders_module.reminder_manager.create_or_update_reminder(
            bot_name="Nahida",
            target_user_id=42,
            target_user_name="Alice",
            source_type="channel",
            source_channel_id=77,
            source_channel_name="tea-room",
            source_guild_id=5,
            source_guild_name="Sumeru",
            timezone_name="UTC",
            timezone_offset_minutes=0,
            timezone_source="bot",
            event_summary="Tea break",
            normalized_event="tea break",
            due_at_utc=now - timedelta(minutes=1),
            pre_due_at_utc=None,
            creation_mode="auto",
        )

        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character_name = "nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        instance.client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
        instance._generate_scheduled_reminder_text = AsyncMock(return_value="Tea time.")
        instance._send_scheduled_reminder = AsyncMock(return_value=(
            [types.SimpleNamespace(id=1, created_at=None, content="Tea time.")],
            77,
        ))

        with patch.object(bot_instance_module, "reminder_manager", reminders_module.reminder_manager), \
                patch.object(bot_instance_module.user_ignores, "is_ignored", return_value=False), \
                patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
                    "global_paused": False
                }.get(key, default)), \
                patch.object(bot_instance_module.runtime_config, "update_last_activity"), \
                patch.object(bot_instance_module.metrics_manager, "update_last_activity"), \
                patch.object(bot_instance_module.log, "info"), \
                patch.object(bot_instance_module.log, "warn"):
            sent = await instance._run_reminder_cycle(now=now)

        self.assertEqual(sent, 1)
        stored = next(item for item in reminders_module.reminder_manager.reminders if item["id"] == reminder["id"])
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["due_status"], "sent")

    async def test_scheduled_reminder_channel_block_does_not_fallback_to_dm(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        instance.client = types.SimpleNamespace(get_guild=lambda guild_id: None)
        instance._resolve_channel = AsyncMock()
        instance._resolve_user_dm_channel = AsyncMock()

        reminder = {
            "source_type": "channel",
            "target_user_id": 42,
            "source_channel_id": 77,
            "source_guild_id": 5,
        }

        with patch.object(bot_instance_module.runtime_config, "is_server_response_allowed", return_value=(False, "response_channel_blacklist")), \
                patch.object(bot_instance_module.runtime_config, "is_dm_response_allowed", return_value=(True, None)), \
                patch.object(bot_instance_module.log, "debug"):
            sent_messages, actual_channel_id = await instance._send_scheduled_reminder(reminder, "Tea time.")

        self.assertEqual(sent_messages, [])
        self.assertIsNone(actual_channel_id)
        instance._resolve_channel.assert_not_awaited()
        instance._resolve_user_dm_channel.assert_not_awaited()

    async def test_scheduled_reminder_missing_channel_can_fallback_to_allowed_dm(self):
        class FakeDMChannel:
            id = 555
            guild = None

            async def send(self, content):
                return types.SimpleNamespace(id=1, created_at=None, content=content)

        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        instance.client = types.SimpleNamespace(get_guild=lambda guild_id: None)
        instance._resolve_channel = AsyncMock(return_value=None)
        instance._resolve_user_dm_channel = AsyncMock(return_value=FakeDMChannel())

        reminder = {
            "source_type": "channel",
            "target_user_id": 42,
            "source_channel_id": 77,
            "source_guild_id": 5,
        }

        with patch.object(bot_instance_module.runtime_config, "is_server_response_allowed", return_value=(True, None)), \
                patch.object(bot_instance_module.runtime_config, "is_dm_response_allowed", return_value=(True, None)), \
                patch.object(reminder_delivery_module, "add_to_history") as add_history_mock:
            sent_messages, actual_channel_id = await instance._send_scheduled_reminder(reminder, "Tea time.")

        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(actual_channel_id, 555)
        instance._resolve_channel.assert_awaited_once_with(77)
        instance._resolve_user_dm_channel.assert_awaited_once_with(42)
        add_history_mock.assert_called_once()

    async def test_scheduled_reminder_partial_primary_delivery_does_not_resend_to_fallback_dm(self):
        class PartialChannel:
            id = 77
            guild = None

            def __init__(self):
                self.sent = []

            async def send(self, content):
                self.sent.append(content)
                if len(self.sent) == 1:
                    return types.SimpleNamespace(id=10, created_at=None, content=content)
                raise reminder_delivery_module.discord.HTTPException("boom")

        primary_channel = PartialChannel()
        fallback_dm = types.SimpleNamespace(id=555, guild=None, send=AsyncMock())
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        instance.client = types.SimpleNamespace(get_guild=lambda guild_id: None)
        instance._resolve_channel = AsyncMock(return_value=primary_channel)
        instance._resolve_user_dm_channel = AsyncMock(return_value=fallback_dm)
        instance._split_response_for_delivery = lambda response: ["One.", "Two."]

        reminder = {
            "id": "rem-1",
            "source_type": "channel",
            "target_user_id": 42,
            "source_channel_id": 77,
            "source_guild_id": 5,
        }

        with patch.object(bot_instance_module.runtime_config, "is_server_response_allowed", return_value=(True, None)), \
                patch.object(bot_instance_module.runtime_config, "is_dm_response_allowed", return_value=(True, None)), \
                patch("runtime_delivery.asyncio.sleep", new=AsyncMock()), \
                patch.object(reminder_delivery_module, "add_to_history") as add_history_mock:
            sent_messages, actual_channel_id = await instance._send_scheduled_reminder(reminder, "One.\n\nTwo.")

        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(actual_channel_id, 77)
        self.assertEqual(primary_channel.sent, ["<@42> One.", "Two."])
        fallback_dm.send.assert_not_awaited()
        add_history_mock.assert_called_once()
        self.assertEqual(add_history_mock.call_args.args[2], "<@42> One.")


class _FakeChannel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.guild = None
        self.sent = []

    async def send(self, content):
        self.sent.append(content)
        return types.SimpleNamespace(id=9000 + len(self.sent), created_at=None, content=content)


class DMReminderHistoryTests(HistorySandbox, MemorySandboxMixin, unittest.IsolatedAsyncioTestCase):
    DM_KEY = "dm:Nahida:user:42"

    def setUp(self):
        super().setUp()
        self.setUpMemorySandbox()
        self.addCleanup(self.tearDownMemorySandbox)
        for patcher in (
            patch.object(bot_instance_module.runtime_config, "is_server_response_allowed", return_value=(True, None)),
            patch.object(bot_instance_module.runtime_config, "is_dm_response_allowed", return_value=(True, None)),
            patch("runtime_delivery.asyncio.sleep", new=AsyncMock()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _make_bot(self, *, channel=None, dm_channel=None):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character_name = "nahida"
        instance.character = types.SimpleNamespace(name="Nahida")
        instance.client = types.SimpleNamespace(get_guild=lambda guild_id: None)
        instance._resolve_channel = AsyncMock(return_value=channel)
        instance._resolve_user_dm_channel = AsyncMock(return_value=dm_channel)
        instance._resolve_dm_followup_channel = AsyncMock(return_value=dm_channel)
        return instance

    @staticmethod
    def _reminder(source_type, source_channel_id):
        return {
            "id": "rem-1",
            "source_type": source_type,
            "target_user_id": 42,
            "source_channel_id": source_channel_id,
            "source_guild_id": None if source_type == "dm" else 5,
        }

    def _contents(self, history_id):
        return [entry["content"] for entry in discord_utils_module.get_history(history_id)]

    async def test_dm_reminder_is_recorded_in_the_dm_history(self):
        instance = self._make_bot(dm_channel=_FakeChannel(555))

        sent, _ = await instance._send_scheduled_reminder(self._reminder("dm", 555), "Time to water the plants.")

        self.assertEqual(len(sent), 1)
        self.assertEqual(self._contents(self.DM_KEY), ["Time to water the plants."])
        self.assertEqual(self._contents(555), [])

    async def test_server_reminder_sent_by_dm_is_recorded_in_the_dm_history(self):
        instance = self._make_bot(channel=None, dm_channel=_FakeChannel(555))

        sent, actual_channel_id = await instance._send_scheduled_reminder(self._reminder("channel", 77), "Tea time.")

        self.assertEqual(actual_channel_id, 555)
        self.assertEqual(self._contents(self.DM_KEY), ["Tea time."])
        self.assertEqual(self._contents(555), [])

    async def test_server_reminder_is_recorded_in_the_channel_history(self):
        instance = self._make_bot(channel=_FakeChannel(77), dm_channel=_FakeChannel(555))

        await instance._send_scheduled_reminder(self._reminder("channel", 77), "Tea time.")

        self.assertEqual(len(self._contents(77)), 1)
        self.assertEqual(self._contents(self.DM_KEY), [])

    async def test_dm_reminder_text_is_written_from_the_dm_history(self):
        instance = self._make_bot()
        discord_utils_module.add_to_history(self.DM_KEY, "user", "I must water the plants tomorrow", author_name="Alice", user_id=42, message_id=7)
        reminder = self._reminder("dm", 555)
        reminder.update({"target_user_name": "Alice", "event_summary": "Water the plants", "due_at_utc": "2026-11-20T08:00:00+00:00"})
        generate_mock = AsyncMock(return_value="Time to water the plants!")

        with patch.object(bot_instance_module.provider_manager, "generate", new=generate_mock), \
                patch.object(bot_instance_module.character_manager, "build_system_prompt", Mock(return_value="SYSTEM")), \
                patch.object(bot_instance_module.character_manager, "build_chatroom_context", Mock(return_value="CHATROOM")), \
                patch.object(bot_instance_module.character_manager, "build_reminder_delivery_context", Mock(return_value="REMINDER")), \
                patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
                    "time_passage_context_enabled": False,
                }.get(key, default)):
            text = await instance._generate_scheduled_reminder_text(reminder, "due")

        self.assertEqual(text, "Time to water the plants!")
        prompt_contents = " ".join(str(item["content"]) for item in generate_mock.await_args.kwargs["messages"])
        self.assertIn("I must water the plants tomorrow", prompt_contents)

    async def test_clarification_question_is_recorded_in_the_dm_history(self):
        instance = self._make_bot()
        instance.name = "Nahida"
        instance._extract_reminder_payload = AsyncMock(return_value={
            "action": "clarify",
            "event_summary": "Dentist appointment",
            "clarification_prompt": "When is the appointment?",
        })
        instance._generate_reminder_clarification_message = AsyncMock(return_value="When is it?")
        dm_channel = bot_instance_module.discord.DMChannel()
        dm_channel.id = 77
        message = types.SimpleNamespace(
            id=1234,
            channel=dm_channel,
            _interaction=None,
            reply=AsyncMock(return_value=types.SimpleNamespace(id=2001, created_at=None)),
        )
        context = {
            "channel_id": self.DM_KEY,
            "discord_channel_id": 77,
            "content": "Remind me about the dentist.",
            "user_id": 42,
            "user_name": "Alice",
            "is_dm": True,
            "guild_id": None,
            "timezone_context": {"timezone_name": "UTC", "timezone_source": "process", "offset_minutes": 0, "tzinfo": timezone.utc},
        }
        request = {"allow_auto_reminders": True, "pending_reminder_clarification": None, "guild": None}

        with patch.object(bot_instance_module, "reminder_manager", reminders_module.reminder_manager):
            await instance._maybe_handle_reminder_capture(context, message, request)

        self.assertEqual(self._contents(self.DM_KEY), ["When is it?"])
        self.assertEqual(self._contents(77), [])
