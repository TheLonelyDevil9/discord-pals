import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import discord_utils as discord_utils_module
import logger as logger_module
import request_queue as request_queue_module
import runtime_config as runtime_config_module

from test_support import MemorySandboxMixin


class FakeChannel:
    def __init__(self, channel_id, name):
        self.id = channel_id
        self.name = name


class FakeGuild:
    def __init__(self, guild_id, name, channels, member_count=10):
        self.id = guild_id
        self.name = name
        self.text_channels = channels
        self.member_count = member_count
        self.icon = None


class FakeClient:
    def __init__(self, guilds, ready=True):
        self.guilds = guilds
        self._ready = ready
        self.loop = types.SimpleNamespace(is_running=lambda: False)

    def is_ready(self):
        return self._ready

    def get_channel(self, channel_id):
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.id == channel_id:
                    channel.guild = guild
                    return channel
        return None


class FakeBot:
    def __init__(self, name, character_name, guilds=None, ready=True):
        self.name = name
        self.character = types.SimpleNamespace(name=character_name) if character_name else None
        self.character_name = character_name.lower() if character_name else None
        self.client = FakeClient(guilds or [], ready=ready)
        self.nicknames = ""


async def async_noop(*args, **kwargs):
    return None


class RequestQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_fifo_order_is_preserved_and_pending_counts_release(self):
        queue = request_queue_module.RequestQueue()
        processed = []

        async def processor(request):
            processed.append(request["content"])

        queue.set_processor(processor)
        channel_id = 100
        queue.processing[channel_id] = True

        for content in ("first", "second", "third"):
            added = await queue.add_request(
                channel_id=channel_id,
                message=types.SimpleNamespace(),
                content=content,
                guild=None,
                attachments=[],
                user_name="Alice",
                is_dm=False,
                user_id=42,
            )
            self.assertTrue(added)

        blocked = await queue.add_request(
            channel_id=channel_id,
            message=types.SimpleNamespace(),
            content="fourth",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=42,
        )
        self.assertFalse(blocked)
        self.assertEqual(queue.pending_counts[channel_id][42], 3)

        queue.processing[channel_id] = False
        with patch.object(request_queue_module.asyncio, "sleep", new=async_noop):
            await queue._process_queue(channel_id)

        self.assertEqual(processed, ["first", "second", "third"])
        self.assertEqual(queue.pending_counts[channel_id].get(42, 0), 0)

        added_again = await queue.add_request(
            channel_id=channel_id,
            message=types.SimpleNamespace(),
            content="fifth",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=42,
        )
        self.assertTrue(added_again)

    async def test_duplicate_suppression_uses_user_signature_window(self):
        queue = request_queue_module.RequestQueue()
        channel_id = 200
        queue.processing[channel_id] = True
        split_target = types.SimpleNamespace(id=777)

        first = await queue.add_request(
            channel_id=channel_id,
            message=types.SimpleNamespace(),
            content="Hello there",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=123,
            split_reply_target=split_target,
        )
        duplicate = await queue.add_request(
            channel_id=channel_id,
            message=types.SimpleNamespace(),
            content="Hello there",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=123,
            split_reply_target=split_target,
        )
        different_target = await queue.add_request(
            channel_id=channel_id,
            message=types.SimpleNamespace(),
            content="Hello there",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=123,
            split_reply_target=types.SimpleNamespace(id=778),
        )

        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertTrue(different_target)

    async def test_pending_limit_and_signature_remain_active_while_request_is_processing(self):
        queue = request_queue_module.RequestQueue()
        channel_id = 201
        started = asyncio.Event()
        release = asyncio.Event()

        async def processor(request):
            started.set()
            await release.wait()

        queue.set_processor(processor)

        first = await queue.add_request(
            channel_id=channel_id,
            message=types.SimpleNamespace(),
            content="Hello there",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=123,
        )

        await started.wait()

        duplicate = await queue.add_request(
            channel_id=channel_id,
            message=types.SimpleNamespace(),
            content="Hello there",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=123,
        )

        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertEqual(queue.pending_counts[channel_id][123], 1)

        release.set()
        for _ in range(20):
            if queue.pending_counts[channel_id].get(123, 0) == 0 and not queue.processing[channel_id]:
                break
            await asyncio.sleep(0.05)

        self.assertEqual(queue.pending_counts[channel_id].get(123, 0), 0)

    async def test_duplicate_signature_window_survives_request_completion(self):
        queue = request_queue_module.RequestQueue()
        channel_id = 202
        queue.processing[channel_id] = True

        with patch.object(request_queue_module.time, "time", return_value=1000.0):
            added = await queue.add_request(
                channel_id=channel_id,
                message=types.SimpleNamespace(),
                content="Hello there",
                guild=None,
                attachments=[],
                user_name="Alice",
                is_dm=False,
                user_id=123,
            )

        request = queue.queues[channel_id].popleft()
        with patch.object(request_queue_module.time, "time", return_value=1001.0):
            queue._release_request_tracking(channel_id, request)

        with patch.object(request_queue_module.time, "time", return_value=1001.0):
            duplicate = await queue.add_request(
                channel_id=channel_id,
                message=types.SimpleNamespace(),
                content="Hello there",
                guild=None,
                attachments=[],
                user_name="Alice",
                is_dm=False,
                user_id=123,
            )

        with patch.object(request_queue_module.time, "time", return_value=1004.1):
            allowed = await queue.add_request(
                channel_id=channel_id,
                message=types.SimpleNamespace(),
                content="Hello there",
                guild=None,
                attachments=[],
                user_name="Alice",
                is_dm=False,
                user_id=123,
            )

        self.assertTrue(added)
        self.assertFalse(duplicate)
        self.assertTrue(allowed)


class DashboardPerformanceApiTests(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True

        logger_module.debug = self._logger_originals["debug"]
        logger_module.info = self._logger_originals["info"]
        logger_module.warn = self._logger_originals["warn"]
        logger_module.error = self._logger_originals["error"]
        logger_module.ok = self._logger_originals["ok"]

        self._dashboard_cache_originals = {
            "topology": {
                "built_at": dashboard_module._topology_cache["built_at"],
                "value": dashboard_module._topology_cache["value"],
            },
            "version": {
                "fetched_at": dashboard_module._github_version_cache["fetched_at"],
                "github_version": dashboard_module._github_version_cache["github_version"],
                "has_value": dashboard_module._github_version_cache["has_value"],
            },
        }
        self._logger_state_originals = {
            "buffer": list(logger_module._log_buffer),
            "seq": logger_module._log_sequence,
            "reset": logger_module._log_reset_marker,
            "level": logger_module.LOG_LEVEL,
        }
        self._runtime_originals = {
            "last_context": dict(runtime_config_module._last_context),
            "context_revision": runtime_config_module._last_context_revision,
            "last_activity": dict(runtime_config_module._last_activity),
        }

        dashboard_module._topology_cache["built_at"] = 0.0
        dashboard_module._topology_cache["value"] = None
        dashboard_module._invalidate_github_version_cache()
        logger_module.clear_logs()
        logger_module.LOG_LEVEL = logger_module.QUIET
        runtime_config_module._last_context = {}
        runtime_config_module._last_context_revision = 0
        runtime_config_module._last_activity = {}

        shared_channel = FakeChannel(10, "shared")
        solo_channel = FakeChannel(20, "solo")
        guild = FakeGuild(1, "Guild One", [shared_channel, solo_channel])
        other_guild = FakeGuild(1, "Guild One", [shared_channel])
        self.bot_a = FakeBot("Nahida", "Nahida", [guild])
        self.bot_b = FakeBot("Nilou", "Nilou", [other_guild])
        dashboard_module.bot_instances = [self.bot_a, self.bot_b]

    def tearDown(self):
        dashboard_module._topology_cache["built_at"] = self._dashboard_cache_originals["topology"]["built_at"]
        dashboard_module._topology_cache["value"] = self._dashboard_cache_originals["topology"]["value"]
        dashboard_module._github_version_cache["fetched_at"] = self._dashboard_cache_originals["version"]["fetched_at"]
        dashboard_module._github_version_cache["github_version"] = self._dashboard_cache_originals["version"]["github_version"]
        dashboard_module._github_version_cache["has_value"] = self._dashboard_cache_originals["version"]["has_value"]

        logger_module._log_buffer = self._logger_state_originals["buffer"]
        logger_module._log_sequence = self._logger_state_originals["seq"]
        logger_module._log_reset_marker = self._logger_state_originals["reset"]
        logger_module.LOG_LEVEL = self._logger_state_originals["level"]

        runtime_config_module._last_context = self._runtime_originals["last_context"]
        runtime_config_module._last_context_revision = self._runtime_originals["context_revision"]
        runtime_config_module._last_activity = self._runtime_originals["last_activity"]
        self.tearDownMemorySandbox()

    def test_api_channels_returns_deduped_channels_across_bots(self):
        response = self.client.get("/api/channels")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual({channel["id"] for channel in data["channels"]}, {10, 20})
        self.assertEqual(len(data["channels"]), 2)

    def test_status_delta_returns_changed_false_when_etag_matches(self):
        runtime_config_module.update_last_activity("Nahida")

        first = self.client.get("/api/status/delta")
        first_data = first.get_json()
        second = self.client.get(f"/api/status/delta?etag={first_data['etag']}")
        second_data = second.get_json()

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first_data["changed"])
        self.assertFalse(second_data["changed"])
        self.assertEqual(second_data["etag"], first_data["etag"])

    def test_logs_delta_returns_appended_entries_and_reset_after_clear(self):
        logger_module.info("First entry")
        first = self.client.get("/api/logs/delta?after=0").get_json()

        logger_module.warn("Second entry")
        second = self.client.get(f"/api/logs/delta?after={first['cursor']}").get_json()

        logger_module.clear_logs()
        reset = self.client.get(f"/api/logs/delta?after={second['cursor']}").get_json()

        self.assertEqual(len(first["entries"]), 1)
        self.assertEqual(first["entries"][0]["message"], "First entry")
        self.assertFalse(second["reset"])
        self.assertEqual([entry["message"] for entry in second["entries"]], ["Second entry"])
        self.assertTrue(reset["reset"])
        self.assertEqual(reset["entries"], [])

    def test_contexts_delta_uses_revision_counter(self):
        unchanged = self.client.get("/api/contexts/delta?revision=0").get_json()
        runtime_config_module.store_last_context("Nahida", "System prompt", [{"role": "user", "content": "Hello"}], 42)
        changed = self.client.get("/api/contexts/delta?revision=0").get_json()

        self.assertFalse(unchanged["changed"])
        self.assertTrue(changed["changed"])
        self.assertGreater(changed["revision"], 0)
        self.assertIn("Nahida", changed["contexts"])

    def test_get_bot_falloff_config_matches_hot_path_values(self):
        runtime_config_module.invalidate_cache()
        config = runtime_config_module.get_all()
        falloff = runtime_config_module.get_bot_falloff_config()

        self.assertEqual(set(falloff.keys()), {
            "bot_falloff_enabled",
            "bot_falloff_base_chance",
            "bot_falloff_decay_rate",
            "bot_falloff_min_chance",
            "bot_falloff_hard_limit",
        })
        for key, value in falloff.items():
            self.assertEqual(value, config[key])

    def test_version_api_reuses_cached_value_and_refreshes_after_update(self):
        completed = types.SimpleNamespace(returncode=0, stdout="Already up to date", stderr="")

        with patch.object(dashboard_module, "_get_file_version", return_value="1.0.0"), \
                patch.object(dashboard_module, "_fetch_github_latest_version", side_effect=["1.1.0", "1.2.0"]) as fetch_mock, \
                patch("subprocess.run", return_value=completed):
            first = self.client.get("/api/version").get_json()
            second = self.client.get("/api/version").get_json()
            update = self.client.post("/api/update", headers=self.csrf_headers()).get_json()
            third = self.client.get("/api/version").get_json()

        self.assertEqual(first["github_version"], "1.1.0")
        self.assertEqual(second["github_version"], "1.1.0")
        self.assertEqual(third["github_version"], "1.2.0")
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(update["status"], "ok")


class HistoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)

        self._originals = {
            "DATA_DIR": discord_utils_module.DATA_DIR,
            "HISTORY_CACHE_FILE": discord_utils_module.HISTORY_CACHE_FILE,
            "HISTORY_CHANNELS_DIR": discord_utils_module.HISTORY_CHANNELS_DIR,
            "conversation_history": discord_utils_module.conversation_history,
            "channel_names": discord_utils_module.channel_names,
            "channel_last_activity": discord_utils_module._channel_last_activity,
            "recent_hashes": discord_utils_module._recent_message_hashes,
            "dirty_channels": discord_utils_module._dirty_history_channels,
            "history_pending": discord_utils_module._history_save_pending,
            "history_last_save": discord_utils_module._history_last_save,
            "history_debounce": discord_utils_module.HISTORY_SAVE_DEBOUNCE,
            "log_level": logger_module.LOG_LEVEL,
        }

        discord_utils_module.DATA_DIR = str(self.data_dir)
        discord_utils_module.HISTORY_CACHE_FILE = str(self.data_dir / "history_cache.json")
        discord_utils_module.HISTORY_CHANNELS_DIR = str(self.data_dir / "history_channels")
        discord_utils_module.conversation_history = {}
        discord_utils_module.channel_names = {}
        discord_utils_module._channel_last_activity = {}
        discord_utils_module._recent_message_hashes = {}
        discord_utils_module._dirty_history_channels = set()
        discord_utils_module._history_save_pending = False
        discord_utils_module._history_last_save = 0.0
        discord_utils_module.HISTORY_SAVE_DEBOUNCE = 0.0
        logger_module.LOG_LEVEL = logger_module.QUIET

    def tearDown(self):
        discord_utils_module.DATA_DIR = self._originals["DATA_DIR"]
        discord_utils_module.HISTORY_CACHE_FILE = self._originals["HISTORY_CACHE_FILE"]
        discord_utils_module.HISTORY_CHANNELS_DIR = self._originals["HISTORY_CHANNELS_DIR"]
        discord_utils_module.conversation_history = self._originals["conversation_history"]
        discord_utils_module.channel_names = self._originals["channel_names"]
        discord_utils_module._channel_last_activity = self._originals["channel_last_activity"]
        discord_utils_module._recent_message_hashes = self._originals["recent_hashes"]
        discord_utils_module._dirty_history_channels = self._originals["dirty_channels"]
        discord_utils_module._history_save_pending = self._originals["history_pending"]
        discord_utils_module._history_last_save = self._originals["history_last_save"]
        discord_utils_module.HISTORY_SAVE_DEBOUNCE = self._originals["history_debounce"]
        logger_module.LOG_LEVEL = self._originals["log_level"]
        self.temp_dir.cleanup()

    def _channel_file(self, channel_id):
        return self.data_dir / "history_channels" / f"{channel_id}.json"

    def test_legacy_history_cache_migrates_to_per_channel_files(self):
        legacy = {
            "123": {
                "name": "tea-room",
                "messages": [{"role": "user", "content": "Hello"}]
            },
            "456": [{"role": "assistant", "content": "Hi"}],
        }
        Path(discord_utils_module.HISTORY_CACHE_FILE).write_text(
            json.dumps(legacy, indent=2),
            encoding="utf-8"
        )

        discord_utils_module.load_history()

        self.assertEqual(set(discord_utils_module.conversation_history.keys()), {123, 456})
        self.assertTrue(discord_utils_module._history_save_pending)
        self.assertFalse(self._channel_file(123).exists())

        discord_utils_module.save_history(force=True)

        self.assertTrue(self._channel_file(123).exists())
        self.assertTrue(self._channel_file(456).exists())
        migrated = json.loads(self._channel_file(123).read_text(encoding="utf-8"))
        self.assertEqual(migrated["name"], "tea-room")
        self.assertIn("last_activity", migrated)
        self.assertTrue(Path(discord_utils_module.HISTORY_CACHE_FILE).exists())

    def test_only_dirty_channels_are_rewritten(self):
        discord_utils_module.conversation_history = {
            1: [{"role": "user", "content": "alpha"}],
            2: [{"role": "user", "content": "beta"}],
        }
        discord_utils_module.channel_names = {1: "one", 2: "two"}
        discord_utils_module._channel_last_activity = {1: 100.0, 2: 200.0}
        discord_utils_module._mark_history_dirty(1)
        discord_utils_module._mark_history_dirty(2)
        discord_utils_module.save_history(force=True)

        original_channel_two = self._channel_file(2).read_text(encoding="utf-8")

        discord_utils_module.conversation_history[1].append({"role": "assistant", "content": "updated"})
        discord_utils_module._channel_last_activity[1] = 300.0
        discord_utils_module._mark_history_dirty(1)
        discord_utils_module.save_history(force=True)

        updated_channel_one = json.loads(self._channel_file(1).read_text(encoding="utf-8"))
        self.assertEqual(len(updated_channel_one["messages"]), 2)
        self.assertEqual(self._channel_file(2).read_text(encoding="utf-8"), original_channel_two)

    def test_clear_history_deletes_persisted_channel_file_immediately(self):
        discord_utils_module.conversation_history = {7: [{"role": "user", "content": "hello"}]}
        discord_utils_module.channel_names = {7: "general"}
        discord_utils_module._channel_last_activity = {7: 123.0}
        discord_utils_module._mark_history_dirty(7)
        discord_utils_module.save_history(force=True)

        self.assertTrue(self._channel_file(7).exists())

        discord_utils_module.clear_history(7)

        self.assertFalse(self._channel_file(7).exists())
        self.assertNotIn(7, discord_utils_module.conversation_history)

    def test_edit_and_remove_only_persist_touched_channel(self):
        discord_utils_module.conversation_history = {
            11: [
                {"role": "user", "content": "before edit"},
                {"role": "assistant", "content": "assistant reply"},
            ],
            22: [{"role": "user", "content": "untouched"}],
        }
        discord_utils_module.channel_names = {11: "alpha", 22: "beta"}
        discord_utils_module._channel_last_activity = {11: 111.0, 22: 222.0}
        discord_utils_module._mark_history_dirty(11)
        discord_utils_module._mark_history_dirty(22)
        discord_utils_module.save_history(force=True)

        untouched_before = self._channel_file(22).read_text(encoding="utf-8")

        discord_utils_module.update_history_on_edit(11, "before", "after")
        discord_utils_module.remove_assistant_from_history(11, 1)
        discord_utils_module.save_history(force=True)

        touched = json.loads(self._channel_file(11).read_text(encoding="utf-8"))
        self.assertEqual(touched["messages"], [{"role": "user", "content": "after edit"}])
        self.assertEqual(self._channel_file(22).read_text(encoding="utf-8"), untouched_before)
