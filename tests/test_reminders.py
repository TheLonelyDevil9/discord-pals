import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
import dashboard as dashboard_module
import memory as memory_module
import reminders as reminders_module
import runtime_config as runtime_config_module
import time_utils as time_utils_module

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
        self.assertIn("Cancel Selected", page)

    def test_config_page_renders_timezone_picker(self):
        page = self.client.get("/config").get_data(as_text=True)

        self.assertIn('class="timezone-select"', page)
        self.assertIn("Use process/server timezone", page)
        self.assertIn("Asia/Calcutta", page)
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
            "channel_id": 77,
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
