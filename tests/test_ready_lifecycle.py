import asyncio
import time
import types
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
import discord_utils as discord_utils_module
import main as main_module
import project_automation as project_automation_module
from commands.registry import register_command_metadata
from history_sandbox import HistorySandbox


def _make_registered_bot(name="Firefly"):
    bot = bot_instance_module.BotInstance(name, "token", name.lower())
    bot.client.user = types.SimpleNamespace(id=999, bot=True, display_name=f"{name} Bot", name=name.lower())
    return bot


class ReadyTestCase(HistorySandbox, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.automation = types.SimpleNamespace(attach=AsyncMock())
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.character_load = stack.enter_context(patch.object(
            bot_instance_module.character_manager, "load", Mock(return_value=types.SimpleNamespace(name="Firefly"))
        ))
        stack.enter_context(patch.object(bot_instance_module, "ensure_data_dir"))
        stack.enter_context(patch.object(discord_utils_module, "register_bot"))
        stack.enter_context(patch.object(project_automation_module, "get_automation", return_value=self.automation))
        stack.enter_context(patch.object(bot_instance_module.metrics_manager, "update_bot_status"))
        self.log = {
            level: stack.enter_context(patch.object(bot_instance_module.log, level))
            for level in ("online", "ok", "info", "warn", "error", "debug")
        }

    def make_bot(self):
        bot = _make_registered_bot()
        bot._track_background_task = Mock(side_effect=lambda coro, name: coro.close())
        bot._sync_slash_commands = AsyncMock(return_value={"global": {"ok": True}, "guilds": []})
        return bot


class ReadyHistoryTests(ReadyTestCase):
    async def test_repeated_ready_keeps_history_that_is_not_saved_yet(self):
        bot = self.make_bot()
        discord_utils_module.HISTORY_SAVE_DEBOUNCE = 3600.0
        discord_utils_module._history_last_save = time.time()
        discord_utils_module.add_to_history(77, "user", "said just before a reconnect", author_name="Alice", message_id=5)

        await bot.client.on_ready()
        await bot.client.on_ready()

        self.assertEqual(discord_utils_module.get_history(77)[0]["content"], "said just before a reconnect")
        self.assertIn(77, discord_utils_module._dirty_history_channels)
        self.assertTrue(discord_utils_module._history_save_pending)


class ReadyRobustnessTests(ReadyTestCase):
    async def test_ready_sets_the_request_processor_before_a_step_can_fail(self):
        bot = self.make_bot()
        self.character_load.side_effect = RuntimeError("character file unreadable")

        await bot.client.on_ready()

        self.assertEqual(bot.request_queue.process_callback, bot._process_request)
        started = {call.kwargs["name"] for call in bot._track_background_task.call_args_list}
        self.assertTrue({"periodic-state-cleanup", "dm-followups", "reminders"} <= started)
        self.automation.attach.assert_awaited_once_with(bot)
        bot._sync_slash_commands.assert_awaited_once()
        self.log["error"].assert_called_once()
        self.assertIn("character file unreadable", self.log["error"].call_args.args[0])

    async def test_the_request_processor_is_set_before_the_first_awaited_step(self):
        bot = self.make_bot()
        attach_started = asyncio.Event()
        release_attach = asyncio.Event()

        async def slow_attach(attached_bot):
            attach_started.set()
            await release_attach.wait()

        self.automation.attach = slow_attach
        ready = asyncio.create_task(bot.client.on_ready())
        try:
            await asyncio.wait_for(attach_started.wait(), timeout=1)
            self.assertEqual(bot.request_queue.process_callback, bot._process_request)
        finally:
            release_attach.set()
            await ready

    async def test_a_failing_step_does_not_stop_the_later_steps(self):
        bot = self.make_bot()
        self.automation.attach.side_effect = RuntimeError("helper store locked")

        await bot.client.on_ready()

        bot._sync_slash_commands.assert_awaited_once()
        self.log["online"].assert_called_once()
        self.assertIn("helper store locked", self.log["error"].call_args.args[0])


class SlashCommandSyncTests(ReadyTestCase):
    async def test_commands_sync_once_per_process(self):
        bot = self.make_bot()

        await bot.client.on_ready()
        await bot.client.on_ready()

        bot._sync_slash_commands.assert_awaited_once()

    async def test_commands_sync_again_after_the_inventory_changed(self):
        bot = self.make_bot()
        await bot.client.on_ready()

        register_command_metadata(bot, name="extra", audience="user", description="Added later")
        await bot.client.on_ready()

        self.assertEqual(bot._sync_slash_commands.await_count, 2)

    async def test_a_failed_sync_is_tried_again_on_the_next_ready(self):
        bot = self.make_bot()
        bot._sync_slash_commands.return_value = {"global": {"ok": False}, "guilds": []}

        await bot.client.on_ready()
        await bot.client.on_ready()

        self.assertEqual(bot._sync_slash_commands.await_count, 2)


class RunBotsHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_loads_once_before_any_bot_starts(self):
        events = []
        both_started = asyncio.Event()

        def fake_bot(name):
            async def start():
                events.append(f"start {name}")
                if len(events) == 3:
                    both_started.set()
                await asyncio.Event().wait()
            return types.SimpleNamespace(name=name, start=start, close=AsyncMock())

        with patch.object(main_module, "load_history", Mock(side_effect=lambda: events.append("load"))) as load_history, \
                patch.object(main_module, "load_bot_configs", return_value=[{}, {}]), \
                patch.object(main_module, "BotInstance", side_effect=[fake_bot("A"), fake_bot("B")]), \
                patch.dict("sys.modules", {"dashboard": types.SimpleNamespace(start_dashboard=lambda **kwargs: None)}), \
                patch.object(main_module, "_install_shutdown_handlers"), \
                patch.object(main_module, "_persist_runtime_state"), \
                patch.object(main_module.log, "run_log_retention", lambda: asyncio.Event().wait()):
            task = asyncio.create_task(main_module.run_bots())
            try:
                await asyncio.wait_for(both_started.wait(), timeout=1)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        load_history.assert_called_once_with()
        self.assertEqual(events, ["load", "start A", "start B"])


if __name__ == "__main__":
    unittest.main()
