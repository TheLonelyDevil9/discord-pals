import asyncio
import time
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
import failure_notices
import request_queue as request_queue_module
import runtime_config

from test_support import MemorySandboxMixin

STALE_REACTION = "⌛"


async def async_noop(*args, **kwargs):
    return None


def make_bot(name="Nahida"):
    instance = object.__new__(bot_instance_module.BotInstance)
    instance.name = name
    instance.character = types.SimpleNamespace(name=name)
    instance._build_request_context = AsyncMock(return_value=None)
    return instance


def make_request(*, waited, user_id=42, is_autonomous=False, author_bot=False, channel=None):
    channel = channel or types.SimpleNamespace(id=77, send=AsyncMock())
    message = types.SimpleNamespace(
        id=1000 + user_id,
        channel=channel,
        guild=types.SimpleNamespace(id=5),
        author=types.SimpleNamespace(id=user_id, bot=author_bot),
        add_reaction=AsyncMock(),
    )
    request = {
        "req_id": f"req-{user_id}",
        "message": message,
        "channel_id": 77,
        "guild": message.guild,
        "user_id": user_id,
        "is_dm": False,
        "is_autonomous": is_autonomous,
    }
    if waited is not None:
        request["timestamp"] = time.time() - waited
    return request


class RequestPatches:
    """Let _process_request run past its access checks with the given runtime settings."""

    def __init__(self, settings=None):
        values = {"global_paused": False, **(settings or {})}
        self._patches = [
            patch.object(bot_instance_module.runtime_config, "get",
                         side_effect=lambda key, default=None: values.get(key, default)),
            patch.object(bot_instance_module.response_access, "request_access", return_value=(True, None, 77, 42)),
            patch.object(bot_instance_module.runtime_config, "is_bot_available", return_value=True),
        ]
        self._log_patches = {
            name: patch.object(bot_instance_module.log, name)
            for name in ("info", "warn", "error", "debug", "diagnostic")
        }

    def __enter__(self):
        for item in self._patches:
            item.start()
        self.log = types.SimpleNamespace(**{name: item.start() for name, item in self._log_patches.items()})
        return self

    def __exit__(self, *exc_info):
        for item in [*self._patches, *self._log_patches.values()]:
            item.stop()

    def lines(self, event):
        calls = [call for level in vars(self.log).values() for call in level.call_args_list]
        return [call for call in calls if call.kwargs.get("event") == event]


class StaleRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_request_older_than_the_limit_is_dropped_with_a_reaction_and_one_log_line(self):
        instance, request = make_bot(), make_request(waited=200)

        with RequestPatches({"request_max_age_seconds": 180}) as patches:
            await instance._process_request(request)

        instance._build_request_context.assert_not_awaited()
        request["message"].add_reaction.assert_awaited_once_with(STALE_REACTION)
        lines = patches.lines("request_stale_dropped")
        self.assertEqual(len(lines), 1)
        self.assertIn("180", lines[0].args[0])
        self.assertEqual(lines[0].kwargs["max_age_s"], 180)
        self.assertGreaterEqual(lines[0].kwargs["waited_s"], 200)
        self.assertEqual(lines[0].kwargs["reaction"], "added")

    async def test_a_fresh_request_is_processed_as_before(self):
        instance, request = make_bot(), make_request(waited=5)

        with RequestPatches({"request_max_age_seconds": 180}) as patches:
            await instance._process_request(request)

        instance._build_request_context.assert_awaited_once()
        request["message"].add_reaction.assert_not_awaited()
        self.assertEqual(patches.lines("request_stale_dropped"), [])

    async def test_a_request_without_a_queue_time_is_processed(self):
        instance, request = make_bot(), make_request(waited=None)

        with RequestPatches({"request_max_age_seconds": 180}):
            await instance._process_request(request)

        instance._build_request_context.assert_awaited_once()

    async def test_a_limit_of_zero_keeps_every_request(self):
        instance, request = make_bot(), make_request(waited=10_000)

        with RequestPatches({"request_max_age_seconds": 0}):
            await instance._process_request(request)

        instance._build_request_context.assert_awaited_once()

    async def test_autonomous_and_bot_requests_are_dropped_without_a_reaction(self):
        for request in (make_request(waited=500, is_autonomous=True), make_request(waited=500, author_bot=True)):
            instance = make_bot()
            with RequestPatches({"request_max_age_seconds": 180}) as patches:
                await instance._process_request(request)

            instance._build_request_context.assert_not_awaited()
            request["message"].add_reaction.assert_not_awaited()
            self.assertEqual(patches.lines("request_stale_dropped")[0].kwargs["reaction"], "not_added")

    async def test_a_reaction_that_fails_is_logged_in_the_same_line(self):
        instance, request = make_bot(), make_request(waited=500)
        request["message"].add_reaction.side_effect = bot_instance_module.discord.HTTPException("missing permission")

        with RequestPatches({"request_max_age_seconds": 180}) as patches:
            await instance._process_request(request)

        lines = patches.lines("request_stale_dropped")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].kwargs["reaction"], "failed")

    async def test_a_paused_bot_drops_a_stale_request_without_a_reaction(self):
        instance, request = make_bot(), make_request(waited=500)

        with RequestPatches({"request_max_age_seconds": 180, "global_paused": True}):
            await instance._process_request(request)

        request["message"].add_reaction.assert_not_awaited()

    async def test_the_queue_drops_a_stale_request_when_it_is_taken_and_answers_the_next(self):
        queue = request_queue_module.RequestQueue()
        instance = make_bot()
        queue.set_processor(instance._process_request)
        stale, fresh = make_request(waited=None, user_id=42), make_request(waited=None, user_id=43)
        queue.processing[77] = True
        with patch.object(request_queue_module.log, "diagnostic"):
            with patch.object(request_queue_module.time, "time", return_value=time.time() - 400):
                await queue.add_request(channel_id=77, message=stale["message"], content="old", guild=stale["guild"],
                                        attachments=[], user_name="Alice", is_dm=False, user_id=42)
            await queue.add_request(channel_id=77, message=fresh["message"], content="new", guild=fresh["guild"],
                                    attachments=[], user_name="Bob", is_dm=False, user_id=43)
            queue.processing[77] = False

            with RequestPatches({"request_max_age_seconds": 180}), \
                    patch.object(request_queue_module.asyncio, "sleep", new=async_noop):
                await queue._process_queue(77)

        stale["message"].add_reaction.assert_awaited_once_with(STALE_REACTION)
        fresh["message"].add_reaction.assert_not_awaited()
        self.assertEqual(instance._build_request_context.await_count, 1)
        self.assertEqual(instance._build_request_context.await_args.args[0]["content"], "new")


async def never_finishes(*args, **kwargs):
    await asyncio.Event().wait()


class RequestTimeBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_request_past_the_time_limit_is_stopped_with_a_notice_and_frees_its_scope(self):
        instance, request = make_bot(), make_request(waited=1)
        instance._build_request_context = AsyncMock(side_effect=never_finishes)

        with RequestPatches({"request_time_budget_seconds": 0.05}) as patches:
            await asyncio.wait_for(instance._process_request(request), timeout=5)

        lines = patches.lines("request_time_budget_exceeded")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].kwargs["budget_s"], 0.05)
        request["message"].channel.send.assert_awaited_once()
        self.assertIn("taking too long", request["message"].channel.send.await_args.args[0])

        follower = make_bot("Nilou")
        next_request = make_request(waited=1, user_id=43, channel=request["message"].channel)
        with RequestPatches():
            await asyncio.wait_for(follower._process_request(next_request), timeout=5)
        follower._build_request_context.assert_awaited_once()

    async def test_the_coordinator_slot_is_released_when_the_time_limit_stops_generation(self):
        instance, request = make_bot(), make_request(waited=1)
        instance._build_request_context = AsyncMock(return_value={"channel_id": 77})
        instance._generate_ai_response = never_finishes
        slot = object()

        with RequestPatches({"request_time_budget_seconds": 0.05, "provider_retry_window_seconds": 0}), \
                patch("coordinator.coordinator.acquire_slot", new=AsyncMock(return_value=slot)), \
                patch("coordinator.coordinator.release_slot", new=Mock()) as release_slot:
            await asyncio.wait_for(instance._process_request(request), timeout=5)

        release_slot.assert_called_once_with(slot)

    async def test_a_request_answered_within_the_time_limit_posts_no_notice(self):
        instance, request = make_bot(), make_request(waited=1)

        with RequestPatches({"request_time_budget_seconds": 900}) as patches:
            await instance._process_request(request)

        instance._build_request_context.assert_awaited_once()
        request["message"].channel.send.assert_not_awaited()
        self.assertEqual(patches.lines("request_time_budget_exceeded"), [])


class FailureNoticeLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = {"now": 0.0}

    def _bot(self, name="Nahida"):
        instance = make_bot(name)
        instance._failure_notice_limit = failure_notices.FailureNoticeLimit(clock=lambda: self.clock["now"])
        return instance

    async def _fail(self, instance, channel, *, at, build_error=None):
        self.clock["now"] = at
        if build_error:
            instance._build_request_context = AsyncMock(side_effect=build_error)
        else:
            instance._build_request_context = AsyncMock(return_value={"channel_id": channel.id})
            instance._generate_ai_response = AsyncMock(return_value=None)
        with RequestPatches({"provider_retry_attempts": 1, "provider_retry_window_seconds": 0}) as patches, \
                patch("coordinator.coordinator.acquire_slot", new=AsyncMock(return_value=object())), \
                patch("coordinator.coordinator.release_slot", new=Mock()):
            await instance._process_request(make_request(waited=1, channel=channel))
        return patches.lines("failure_notice_skipped")

    async def test_an_outage_posts_one_notice_per_channel_per_minute(self):
        instance, channel = self._bot(), types.SimpleNamespace(id=77, send=AsyncMock())

        skipped = []
        for at in (0, 10, 59, 61):
            skipped += await self._fail(instance, channel, at=at)

        self.assertEqual(channel.send.await_count, 2)
        self.assertEqual(channel.send.await_args.args[0], "Something went wrong - all providers failed.")
        self.assertEqual(len(skipped), 2)

    async def test_each_channel_gets_its_own_notice(self):
        instance = self._bot()
        first, second = types.SimpleNamespace(id=77, send=AsyncMock()), types.SimpleNamespace(id=78, send=AsyncMock())

        await self._fail(instance, first, at=0)
        await self._fail(instance, second, at=1)

        first.send.assert_awaited_once()
        second.send.assert_awaited_once()

    async def test_error_and_timeout_notices_share_the_limit(self):
        instance, channel = self._bot(), types.SimpleNamespace(id=77, send=AsyncMock())

        await self._fail(instance, channel, at=0, build_error=RuntimeError("broken context"))
        await self._fail(instance, channel, at=5)

        channel.send.assert_awaited_once()
        self.assertIn("Something went wrong", channel.send.await_args.args[0])

    async def test_each_bot_has_its_own_limit_in_a_shared_channel(self):
        channel = types.SimpleNamespace(id=77, send=AsyncMock())

        await self._fail(self._bot("Nahida"), channel, at=0)
        await self._fail(self._bot("Nilou"), channel, at=1)

        self.assertEqual(channel.send.await_count, 2)


class RequestGuardSettingTests(MemorySandboxMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()
        self._runtime_originals = (runtime_config.DATA_DIR, runtime_config.RUNTIME_CONFIG_FILE)
        runtime_config.DATA_DIR = str(self.data_dir)
        runtime_config.RUNTIME_CONFIG_FILE = str(self.data_dir / "runtime_config.json")
        runtime_config.invalidate_cache()

    def tearDown(self):
        runtime_config.DATA_DIR, runtime_config.RUNTIME_CONFIG_FILE = self._runtime_originals
        runtime_config.invalidate_cache()
        self.tearDownMemorySandbox()

    def _saved_value(self, key, raw):
        response = self.client.post("/api/config", json={key: raw}, headers=self.csrf_headers())
        self.assertEqual(response.status_code, 200)
        return self.client.get("/api/config").get_json()[key]

    async def _process_with_saved_settings(self, request):
        instance = make_bot()
        with patch.object(bot_instance_module.response_access, "request_access", return_value=(True, None, 77, 42)), \
                patch.object(bot_instance_module.log, "info"), \
                patch.object(bot_instance_module.log, "diagnostic"):
            await instance._process_request(request)
        return instance

    def assertOnConfigPage(self, *snippets):
        page = self.client.get("/config").get_data(as_text=True)
        for snippet in snippets:
            self.assertTrue(snippet in page, f"config page lacks {snippet!r}")

    async def test_the_request_age_limit_is_edited_on_the_config_page_within_bounds(self):
        self.assertOnConfigPage(
            'id="request_max_age_seconds"',
            "request_max_age_seconds: parseInt(document.getElementById('request_max_age_seconds').value)",
        )
        self.assertEqual(self.client.get("/api/config").get_json()["request_max_age_seconds"], 180)
        for raw, expected in ((-5, 0), (99_999, 3600), ("not a number", 180), (30, 30)):
            self.assertEqual(self._saved_value("request_max_age_seconds", raw), expected)

    async def test_the_saved_age_limit_applies_to_the_next_dequeued_request(self):
        default_request = make_request(waited=45)
        processed = await self._process_with_saved_settings(default_request)
        processed._build_request_context.assert_awaited_once()

        self._saved_value("request_max_age_seconds", 30)
        stale_request = make_request(waited=45)
        dropped = await self._process_with_saved_settings(stale_request)

        dropped._build_request_context.assert_not_awaited()
        stale_request["message"].add_reaction.assert_awaited_once_with(STALE_REACTION)

    async def test_the_request_time_limit_is_edited_on_the_config_page_within_bounds(self):
        self.assertOnConfigPage(
            'id="request_time_budget_seconds"',
            "request_time_budget_seconds: parseInt(document.getElementById('request_time_budget_seconds').value)",
        )
        self.assertEqual(self.client.get("/api/config").get_json()["request_time_budget_seconds"], 900)
        for raw, expected in ((0, 60), (-5, 60), (99_999, 7200), ("not a number", 900), (120, 120)):
            self.assertEqual(self._saved_value("request_time_budget_seconds", raw), expected)

    async def test_the_saved_time_limit_bounds_the_next_request(self):
        self._saved_value("request_time_budget_seconds", 75)
        timeouts = []
        real_wait_for = asyncio.wait_for

        async def recording_wait_for(awaitable, timeout):
            timeouts.append(timeout)
            return await real_wait_for(awaitable, timeout)

        bot_asyncio = types.ModuleType("asyncio")
        bot_asyncio.__dict__.update(asyncio.__dict__)
        bot_asyncio.wait_for = recording_wait_for
        with patch.object(bot_instance_module, "asyncio", bot_asyncio):
            await self._process_with_saved_settings(make_request(waited=1))

        self.assertEqual(timeouts, [75])


if __name__ == "__main__":
    unittest.main()
