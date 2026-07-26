import signal
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
