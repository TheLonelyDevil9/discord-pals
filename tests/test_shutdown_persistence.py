import asyncio
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import module_stubs  # noqa: F401
import main as main_module


class PersistRuntimeStateTests(unittest.TestCase):
    """Shutdown must flush every buffered store, including stats.

    stats_manager.flush() previously had no call site anywhere, so recorded
    statistics were lost on every restart.
    """

    def test_all_stores_are_persisted(self):
        with patch.object(main_module, "save_history") as save_history, \
             patch.object(main_module.memory_manager, "save_all") as save_all, \
             patch.object(main_module.reminder_manager, "save") as save_reminders, \
             patch.object(main_module.stats_manager, "flush") as flush_stats:
            main_module._persist_runtime_state()

        save_history.assert_called_once_with(force=True)
        save_all.assert_called_once()
        save_reminders.assert_called_once()
        flush_stats.assert_called_once()

    def test_one_failing_store_does_not_block_the_others(self):
        with patch.object(main_module, "save_history", side_effect=OSError("disk full")), \
             patch.object(main_module.memory_manager, "save_all") as save_all, \
             patch.object(main_module.reminder_manager, "save") as save_reminders, \
             patch.object(main_module.stats_manager, "flush") as flush_stats:
            main_module._persist_runtime_state()

        save_all.assert_called_once()
        save_reminders.assert_called_once()
        flush_stats.assert_called_once()


class ShutdownSignalTests(unittest.TestCase):
    """systemd stops the service with SIGTERM, not SIGINT."""

    def test_sigterm_is_routed_to_the_keyboardinterrupt_path(self):
        original = signal.getsignal(signal.SIGTERM)
        try:
            main_module._install_shutdown_handlers()
            handler = signal.getsignal(signal.SIGTERM)
            self.assertTrue(callable(handler))
            with self.assertRaises(KeyboardInterrupt):
                handler(signal.SIGTERM, None)
        finally:
            signal.signal(signal.SIGTERM, original)


class BotLoginIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_login_does_not_stop_healthy_bot(self):
        rejected_closed = asyncio.Event()
        healthy_started = asyncio.Event()

        async def healthy_start():
            healthy_started.set()
            await asyncio.Event().wait()

        rejected = SimpleNamespace(
            name="Rejected", start=AsyncMock(side_effect=main_module.discord.LoginFailure("invalid")),
            close=AsyncMock(side_effect=rejected_closed.set),
        )
        healthy = SimpleNamespace(name="Healthy", start=healthy_start, close=AsyncMock())
        with patch.object(main_module, "load_bot_configs", return_value=[{}, {}]), \
                patch.object(main_module, "BotInstance", side_effect=[rejected, healthy]), \
                patch.dict("sys.modules", {"dashboard": SimpleNamespace(start_dashboard=lambda **kwargs: None)}), \
                patch.object(main_module, "_install_shutdown_handlers"), \
                patch.object(main_module, "_persist_runtime_state"):
            task = asyncio.create_task(main_module.run_bots())
            try:
                await asyncio.wait_for(rejected_closed.wait(), timeout=1)
                await asyncio.wait_for(healthy_started.wait(), timeout=1)
                self.assertFalse(task.done())
                healthy.close.assert_not_awaited()
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            healthy.close.assert_awaited_once()

    async def test_all_rejected_logins_leave_process_available_for_recovery(self):
        closed = asyncio.Event()
        bot = SimpleNamespace(
            name="Rejected", start=AsyncMock(side_effect=main_module.discord.LoginFailure("invalid")),
            close=AsyncMock(side_effect=closed.set),
        )
        with patch.object(main_module, "load_bot_configs", return_value=[{}]), \
                patch.object(main_module, "BotInstance", return_value=bot), \
                patch.dict("sys.modules", {"dashboard": SimpleNamespace(start_dashboard=lambda **kwargs: None)}), \
                patch.object(main_module, "_install_shutdown_handlers"), \
                patch.object(main_module, "_persist_runtime_state"):
            task = asyncio.create_task(main_module.run_bots())
            try:
                await asyncio.wait_for(closed.wait(), timeout=1)
                self.assertFalse(task.done())
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task


if __name__ == "__main__":
    unittest.main()
