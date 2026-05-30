import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import discord_utils as discord_utils_module
import logger as logger_module
import request_queue as request_queue_module
import runtime_config as runtime_config_module
from scopes import ScopeKey

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

    async def test_route_request_id_and_channel_id_are_stored_and_logged(self):
        queue = request_queue_module.RequestQueue()
        channel_id = 203
        queue.processing[channel_id] = True

        with patch.object(request_queue_module.log, "diagnostic") as diagnostic_mock:
            added = await queue.add_request(
                channel_id=channel_id,
                message=types.SimpleNamespace(),
                content="Hello there",
                guild=None,
                attachments=[],
                user_name="Alice",
                is_dm=False,
                user_id=123,
                route_req_id="route123",
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
                route_req_id="route456",
            )

        self.assertTrue(added)
        self.assertFalse(duplicate)
        queued_request = queue.queues[channel_id][0]
        self.assertEqual(queued_request["req_id"], "route123")
        self.assertEqual(queued_request["channel_id"], channel_id)
        queued_log = diagnostic_mock.call_args_list[0].kwargs
        rejected_log = diagnostic_mock.call_args_list[1].kwargs
        self.assertEqual(queued_log["req_id"], "route123")
        self.assertEqual(queued_log["channel_id"], channel_id)
        self.assertEqual(rejected_log["req_id"], "route456")
        self.assertEqual(rejected_log["channel_id"], channel_id)

    async def test_scope_key_is_preserved_in_queued_request(self):
        queue = request_queue_module.RequestQueue()
        channel_id = 204
        queue.processing[channel_id] = True
        scope_key = ScopeKey.for_channel(bot_name="Nahida", channel_id=channel_id, guild_id=5)

        added = await queue.add_request(
            channel_id=channel_id,
            message=types.SimpleNamespace(),
            content="Hello there",
            guild=types.SimpleNamespace(id=5),
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=123,
            scope_key=scope_key,
        )

        self.assertTrue(added)
        self.assertIs(queue.queues[channel_id][0]["scope_key"], scope_key)

    async def test_scope_key_history_identity_becomes_queue_key(self):
        queue = request_queue_module.RequestQueue()
        raw_channel_id = 999
        scope_key = ScopeKey.for_dm(bot_name="Nahida", channel_id=raw_channel_id, user_id=123)
        queue.processing[scope_key.history_id] = True

        added = await queue.add_request(
            channel_id=raw_channel_id,
            message=types.SimpleNamespace(),
            content="Hello in DM",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=True,
            user_id=123,
            scope_key=scope_key,
        )

        self.assertTrue(added)
        self.assertEqual(queue.queues[scope_key.history_id][0]["channel_id"], scope_key.history_id)
        self.assertEqual(len(queue.queues[raw_channel_id]), 0)

    async def test_drain_returns_true_when_queue_has_no_pending_work(self):
        queue = request_queue_module.RequestQueue()

        drained = await queue.drain(timeout=0.01, poll_interval=0)

        self.assertTrue(drained)

    async def test_drain_returns_false_after_timeout_with_active_work(self):
        queue = request_queue_module.RequestQueue()
        queue.processing[205] = True

        drained = await queue.drain(timeout=0.01, poll_interval=0)

        self.assertFalse(drained)

    async def test_drain_waits_until_active_processor_finishes(self):
        queue = request_queue_module.RequestQueue()
        started = asyncio.Event()
        release = asyncio.Event()

        async def processor(request):
            started.set()
            await release.wait()

        queue.set_processor(processor)
        added = await queue.add_request(
            channel_id=206,
            message=types.SimpleNamespace(),
            content="Hello",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=123,
        )
        self.assertTrue(added)
        await started.wait()

        drain_task = asyncio.create_task(queue.drain(timeout=1.0, poll_interval=0.01))
        await asyncio.sleep(0)
        self.assertFalse(drain_task.done())
        release.set()

        self.assertTrue(await drain_task)

    async def test_cancel_active_cancels_processing_task_and_releases_tracking(self):
        queue = request_queue_module.RequestQueue()
        started = asyncio.Event()

        async def processor(request):
            started.set()
            await asyncio.Event().wait()

        queue.set_processor(processor)
        added = await queue.add_request(
            channel_id=207,
            message=types.SimpleNamespace(),
            content="Hello",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=123,
        )
        self.assertTrue(added)
        await started.wait()

        await queue.cancel_active()

        self.assertFalse(queue.processing[207])
        self.assertEqual(queue.pending_counts[207].get(123, 0), 0)

    async def test_cancel_active_drops_queued_requests_without_restarting_processor(self):
        queue = request_queue_module.RequestQueue()
        started = asyncio.Event()
        processed = []

        async def processor(request):
            processed.append(request["content"])
            started.set()
            await asyncio.Event().wait()

        queue.set_processor(processor)
        first = await queue.add_request(
            channel_id=208,
            message=types.SimpleNamespace(),
            content="First",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=123,
        )
        await started.wait()
        second = await queue.add_request(
            channel_id=208,
            message=types.SimpleNamespace(),
            content="Second",
            guild=None,
            attachments=[],
            user_name="Alice",
            is_dm=False,
            user_id=123,
        )

        await queue.cancel_active()

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(processed, ["First"])
        self.assertFalse(queue.processing[208])
        self.assertEqual(len(queue.queues[208]), 0)
        self.assertEqual(queue.pending_counts[208].get(123, 0), 0)
        self.assertFalse(any(not task.done() for task in queue._processing_tasks))


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
            "file_enabled": logger_module.FILE_LOGGING_ENABLED,
            "log_file": logger_module.LOG_FILE,
            "log_dir": logger_module.LOG_DIR,
            "log_file_max_bytes": logger_module.LOG_FILE_MAX_BYTES,
            "log_file_backups": logger_module.LOG_FILE_BACKUPS,
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
        logger_module.configure_file_logging(enabled=False)
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
        logger_module.configure_file_logging(
            enabled=self._logger_state_originals["file_enabled"],
            log_dir=self._logger_state_originals["log_dir"],
            max_bytes=self._logger_state_originals["log_file_max_bytes"],
            backups=self._logger_state_originals["log_file_backups"],
        )
        logger_module.LOG_FILE = self._logger_state_originals["log_file"]

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
        logger_module.info("First entry", component="routing", event="first", req_id="abc123")
        first = self.client.get("/api/logs/delta?after=0").get_json()

        logger_module.warn("Second entry")
        second = self.client.get(f"/api/logs/delta?after={first['cursor']}").get_json()

        logger_module.clear_logs()
        reset = self.client.get(f"/api/logs/delta?after={second['cursor']}").get_json()

        self.assertEqual(len(first["entries"]), 1)
        self.assertEqual(first["entries"][0]["message"], "First entry")
        self.assertEqual(first["entries"][0]["component"], "routing")
        self.assertEqual(first["entries"][0]["event"], "first")
        self.assertEqual(first["entries"][0]["req_id"], "abc123")
        self.assertFalse(second["reset"])
        self.assertEqual([entry["message"] for entry in second["entries"]], ["Second entry"])
        self.assertTrue(reset["reset"])
        self.assertEqual(reset["entries"], [])

    def test_logs_delta_filters_structured_fields(self):
        logger_module.info("Routing entry", component="routing", event="message_received", req_id="route1")
        logger_module.info("Provider entry", component="provider", event="provider_response", req_id="prov1")

        by_component = self.client.get("/api/logs/delta?after=0&component=provider").get_json()
        by_req = self.client.get("/api/logs/delta?after=0&req_id=route1").get_json()

        self.assertEqual([entry["message"] for entry in by_component["entries"]], ["Provider entry"])
        self.assertEqual([entry["message"] for entry in by_req["entries"]], ["Routing entry"])

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
        git_update = {
            "updated": False,
            "output": "Already up to date",
            "warnings": [],
            "before_head": "abc123",
            "after_head": "abc123",
            "upstream": "origin/main",
        }
        dependencies = {"status": "skipped", "message": "", "warning": None}

        with patch.object(dashboard_module, "_get_file_version", side_effect=["1.0.0", "1.0.0", "1.2.0", "1.2.0", "1.2.0"]), \
                patch.object(dashboard_module, "_fetch_latest_available_version", side_effect=["1.1.0", "1.2.0"]) as fetch_mock, \
                patch.object(dashboard_module, "_repo_root", return_value="repo"), \
                patch.object(dashboard_module, "_perform_git_update", return_value=git_update), \
                patch.object(dashboard_module, "_install_update_dependencies", return_value=dependencies):
            first = self.client.get("/api/version").get_json()
            second = self.client.get("/api/version").get_json()
            update = self.client.post("/api/update", headers=self.csrf_headers()).get_json()
            third = self.client.get("/api/version").get_json()

        self.assertEqual(first["github_version"], "1.1.0")
        self.assertEqual(second["github_version"], "1.1.0")
        self.assertEqual(third["github_version"], "1.2.0")
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(update["status"], "ok")

    def test_update_endpoint_returns_warnings_without_failing_successful_update(self):
        git_update = {
            "updated": True,
            "output": "Reset to origin/main",
            "warnings": ["Local changes were preserved in stash@{0}."],
            "before_head": "abc123",
            "after_head": "def456",
            "upstream": "origin/main",
        }
        dependencies = {
            "status": "warning",
            "message": " Dependencies could not be fully updated.",
            "warning": "pip install failed",
        }

        with patch.object(dashboard_module, "_repo_root", return_value="repo"), \
                patch.object(dashboard_module, "_perform_git_update", return_value=git_update), \
                patch.object(dashboard_module, "_install_update_dependencies", return_value=dependencies), \
                patch.object(dashboard_module, "_check_github_latest_version", return_value="9.9.9"), \
                patch.object(dashboard_module, "_get_file_version", side_effect=["2.2.10", "9.9.9"]):
            response = self.client.post("/api/update", headers=self.csrf_headers())

        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["updated"])
        self.assertEqual(data["dependency_status"], "warning")
        self.assertEqual(data["new_version"], "9.9.9")
        self.assertEqual(len(data["warnings"]), 2)

    def test_github_latest_version_uses_highest_release_or_tag(self):
        responses = [
            {"tag_name": "v2.2.3"},
            [{"name": "v2.2.4"}, {"name": "not-a-release"}, {"name": "v2.1.0"}],
        ]

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(self.payload).encode()

        urlopen = Mock(side_effect=[FakeResponse(payload) for payload in responses])

        with patch.object(dashboard_module, "_get_github_repo_info", return_value=("owner", "repo")), \
                patch("urllib.request.urlopen", urlopen):
            latest = dashboard_module._fetch_github_latest_version()

        self.assertEqual(latest, "2.2.4")
        self.assertEqual(urlopen.call_count, 2)

    def test_latest_available_version_uses_remote_tags_when_github_tags_lag(self):
        with patch.object(dashboard_module, "_fetch_github_latest_version", return_value="2.2.4"), \
                patch.object(dashboard_module, "_fetch_remote_latest_tag_version", return_value="2.2.5"):
            latest = dashboard_module._fetch_latest_available_version()

        self.assertEqual(latest, "2.2.5")

    def test_update_endpoint_rejects_false_up_to_date_when_latest_version_missing(self):
        git_update = {
            "updated": False,
            "output": "Already up to date",
            "warnings": [],
            "before_head": "abc123",
            "after_head": "abc123",
            "upstream": "origin/main",
        }
        dependencies = {"status": "skipped", "message": "", "warning": None}

        with patch.object(dashboard_module, "_repo_root", return_value="repo"), \
                patch.object(dashboard_module, "_check_github_latest_version", return_value="1.2.0"), \
                patch.object(dashboard_module, "_perform_git_update", return_value=git_update), \
                patch.object(dashboard_module, "_install_update_dependencies", return_value=dependencies), \
                patch.object(dashboard_module, "_get_file_version", return_value="1.0.0"):
            response = self.client.post("/api/update", headers=self.csrf_headers())

        data = response.get_json()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["expected_version"], "1.2.0")
        self.assertEqual(data["file_version"], "1.0.0")
        self.assertIn("origin/main", data["message"])

    def test_update_endpoint_allows_current_checkout_after_verification(self):
        git_update = {
            "updated": False,
            "output": "Already up to date",
            "warnings": [],
            "before_head": "abc123",
            "after_head": "abc123",
            "upstream": "origin/main",
        }
        dependencies = {"status": "skipped", "message": "", "warning": None}

        with patch.object(dashboard_module, "_repo_root", return_value="repo"), \
                patch.object(dashboard_module, "_check_github_latest_version", return_value="1.2.0"), \
                patch.object(dashboard_module, "_perform_git_update", return_value=git_update), \
                patch.object(dashboard_module, "_install_update_dependencies", return_value=dependencies), \
                patch.object(dashboard_module, "_get_file_version", return_value="1.2.0"):
            response = self.client.post("/api/update", headers=self.csrf_headers())

        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["message"], "Already up to date at v1.2.0.")

    def test_update_endpoint_reports_restart_required_when_disk_is_newer(self):
        dependencies = {"status": "skipped", "message": "", "warning": None}

        with patch.object(dashboard_module, "_repo_root", return_value="repo"), \
                patch.object(dashboard_module, "VERSION", "2.2.9"), \
                patch.object(dashboard_module, "_check_github_latest_version", return_value="2.2.10"), \
                patch.object(dashboard_module, "_get_file_version", return_value="2.2.10"), \
                patch.object(dashboard_module, "_perform_git_update") as perform_update, \
                patch.object(dashboard_module, "_install_update_dependencies", return_value=dependencies), \
                patch.object(dashboard_module, "_write_update_log") as write_log:
            response = self.client.post("/api/update", headers=self.csrf_headers())

        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["restart_required"])
        self.assertFalse(data["updated"])
        self.assertEqual(data["message"], "Restart to apply v2.2.10.")
        perform_update.assert_not_called()
        write_log.assert_called()

    def test_git_update_prefers_advertised_version_tag_when_available(self):
        refs = {
            "HEAD": "aaa111",
            "origin/main": "aaa111",
            "refs/tags/v1.2.0": "bbb222",
        }
        commands = []

        def fake_run_git(args, repo_dir, timeout=dashboard_module._UPDATE_GIT_TIMEOUT):
            commands.append(args)
            command = args[0]
            if command == "fetch":
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if args[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return types.SimpleNamespace(returncode=0, stdout="main\n", stderr="")
            if args[:3] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
                return types.SimpleNamespace(returncode=0, stdout="origin/main\n", stderr="")
            if args[:3] == ["rev-parse", "--verify", "--quiet"]:
                return types.SimpleNamespace(returncode=0 if args[3] in refs else 1, stdout="", stderr="")
            if args[:2] == ["rev-parse", "--verify"]:
                ref = args[2].removesuffix("^{commit}")
                return types.SimpleNamespace(returncode=0, stdout=f"{refs[ref]}\n", stderr="")
            if args[:2] == ["show", "refs/tags/v1.2.0:version.py"]:
                return types.SimpleNamespace(returncode=0, stdout='__version__ = "1.2.0"\n', stderr="")
            if args[:3] == ["status", "--porcelain", "--untracked-files=no"]:
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if args[:3] == ["status", "--porcelain", "--untracked-files=all"]:
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if args[:2] == ["merge-base", "--is-ancestor"]:
                return types.SimpleNamespace(returncode=0 if args[2] == "aaa111" and args[3] == "bbb222" else 1, stdout="", stderr="")
            if args[:2] == ["merge", "--ff-only"]:
                refs["HEAD"] = refs[args[2]]
                return types.SimpleNamespace(returncode=0, stdout="Fast-forward\n", stderr="")
            raise AssertionError(f"Unexpected git command: {args}")

        with patch.object(dashboard_module, "_run_git", side_effect=fake_run_git):
            result = dashboard_module._perform_git_update("repo", expected_version="1.2.0")

        self.assertTrue(result["updated"])
        self.assertEqual(result["upstream"], "refs/tags/v1.2.0")
        self.assertEqual(result["branch_upstream"], "origin/main")
        self.assertEqual(result["after_head"], "bbb222")
        self.assertIn(["fetch", "--all", "--tags", "--prune"], commands)

    def test_git_update_recovers_from_stale_local_release_tag(self):
        refs = {
            "HEAD": "aaa111",
            "origin/main": "aaa111",
            "refs/tags/v1.2.0": "bbb222",
        }
        commands = []

        def fake_run_git(args, repo_dir, timeout=dashboard_module._UPDATE_GIT_TIMEOUT):
            commands.append(args)
            if args == ["fetch", "--all", "--tags", "--prune"]:
                return types.SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="! [rejected] v1.1.0 -> v1.1.0 (would clobber existing tag)",
                )
            if args == [
                "fetch",
                "--prune",
                "--force",
                "origin",
                "+refs/heads/*:refs/remotes/origin/*",
                "+refs/tags/*:refs/tags/*",
            ]:
                return types.SimpleNamespace(returncode=0, stdout="forced tags\n", stderr="")
            if args == ["remote"]:
                return types.SimpleNamespace(returncode=0, stdout="origin\n", stderr="")
            if args[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return types.SimpleNamespace(returncode=0, stdout="main\n", stderr="")
            if args[:3] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
                return types.SimpleNamespace(returncode=0, stdout="origin/main\n", stderr="")
            if args[:3] == ["rev-parse", "--verify", "--quiet"]:
                return types.SimpleNamespace(returncode=0 if args[3] in refs else 1, stdout="", stderr="")
            if args[:2] == ["rev-parse", "--verify"]:
                ref = args[2].removesuffix("^{commit}")
                return types.SimpleNamespace(returncode=0, stdout=f"{refs[ref]}\n", stderr="")
            if args[:2] == ["show", "refs/tags/v1.2.0:version.py"]:
                return types.SimpleNamespace(returncode=0, stdout='__version__ = "1.2.0"\n', stderr="")
            if args[:3] == ["status", "--porcelain", "--untracked-files=no"]:
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if args[:3] == ["status", "--porcelain", "--untracked-files=all"]:
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if args[:2] == ["merge-base", "--is-ancestor"]:
                return types.SimpleNamespace(returncode=0 if args[2] == "aaa111" and args[3] == "bbb222" else 1, stdout="", stderr="")
            if args[:2] == ["merge", "--ff-only"]:
                refs["HEAD"] = refs[args[2]]
                return types.SimpleNamespace(returncode=0, stdout="Fast-forward\n", stderr="")
            raise AssertionError(f"Unexpected git command: {args}")

        with patch.object(dashboard_module, "_run_git", side_effect=fake_run_git):
            result = dashboard_module._perform_git_update("repo", expected_version="1.2.0")

        self.assertTrue(result["updated"])
        self.assertEqual(result["after_head"], "bbb222")
        self.assertIn([
            "fetch",
            "--prune",
            "--force",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
            "+refs/tags/*:refs/tags/*",
        ], commands)
        self.assertTrue(any("Recovered from stale local release tags" in warning for warning in result["warnings"]))

    def test_git_update_skips_tag_whose_version_file_does_not_match(self):
        refs = {
            "HEAD": "aaa111",
            "origin/main": "aaa111",
            "refs/tags/v1.2.0": "bbb222",
        }

        def fake_run_git(args, repo_dir, timeout=dashboard_module._UPDATE_GIT_TIMEOUT):
            if args[:2] == ["show", "refs/tags/v1.2.0:version.py"]:
                return types.SimpleNamespace(returncode=0, stdout='__version__ = "1.1.0"\n', stderr="")
            if args[0] == "fetch":
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if args[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return types.SimpleNamespace(returncode=0, stdout="main\n", stderr="")
            if args[:3] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
                return types.SimpleNamespace(returncode=0, stdout="origin/main\n", stderr="")
            if args[:3] == ["rev-parse", "--verify", "--quiet"]:
                return types.SimpleNamespace(returncode=0 if args[3] in refs else 1, stdout="", stderr="")
            if args[:2] == ["rev-parse", "--verify"]:
                ref = args[2].removesuffix("^{commit}")
                return types.SimpleNamespace(returncode=0, stdout=f"{refs[ref]}\n", stderr="")
            if args[:3] == ["status", "--porcelain", "--untracked-files=no"]:
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if args[:3] == ["status", "--porcelain", "--untracked-files=all"]:
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if args[:2] == ["merge-base", "--is-ancestor"]:
                return types.SimpleNamespace(returncode=0 if args[2] == "aaa111" and args[3] == "bbb222" else 1, stdout="", stderr="")
            if args[:2] == ["merge", "--ff-only"]:
                refs["HEAD"] = refs[args[2]]
                return types.SimpleNamespace(returncode=0, stdout="Fast-forward\n", stderr="")
            raise AssertionError(f"Unexpected git command: {args}")

        with patch.object(dashboard_module, "_run_git", side_effect=fake_run_git):
            result = dashboard_module._perform_git_update("repo", expected_version="1.2.0")

        self.assertEqual(result["target_ref"], "origin/main")

    def test_stash_local_changes_does_not_include_untracked(self):
        calls = []

        def fake_run_git(args, repo_dir, timeout=dashboard_module._UPDATE_GIT_TIMEOUT):
            calls.append(args)
            if args[:3] == ["status", "--porcelain", "--untracked-files=no"]:
                return types.SimpleNamespace(returncode=0, stdout=" M dashboard.py\n", stderr="")
            if args[:2] == ["stash", "push"]:
                return types.SimpleNamespace(returncode=0, stdout="Saved working directory and index state", stderr="")
            if args[:3] == ["stash", "list", "--format=%gd%x00%s"]:
                return types.SimpleNamespace(returncode=0, stdout="stash@{0}\x00discord-pals-dashboard-update-123\n", stderr="")
            raise AssertionError(f"Unexpected git command: {args}")

        with patch.object(dashboard_module, "_run_git", side_effect=fake_run_git):
            stash = dashboard_module._stash_local_changes("repo")

        self.assertEqual(stash["ref"], "stash@{0}")
        self.assertNotIn(["stash", "push", "--include-untracked", "-m", "discord-pals-dashboard-update-123"], calls)

    def test_write_update_log_persists_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "bot_data" / "update_log.json"
            with patch.object(dashboard_module, "_UPDATE_LOG_FILE", log_path):
                dashboard_module._write_update_log({"status": "ok", "from_version": "2.2.9"})

            entries = json.loads(log_path.read_text(encoding="utf-8"))

        self.assertEqual(entries[0]["status"], "ok")
        self.assertEqual(entries[0]["from_version"], "2.2.9")


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
