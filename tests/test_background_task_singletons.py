"""Reconnects must not leave two copies of a background loop running.

Discord fires on_ready again after any reconnect that cannot resume. A second
DM follow-up or reminder loop sends the same bot-initiated message twice,
because both copies read the send-once state before either writes it.
"""

import asyncio
import types
import unittest
from unittest.mock import patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
import background_tasks as background_tasks_module
from background_tasks import BackgroundTaskRegistry


class BackgroundTaskSingletonTests(unittest.IsolatedAsyncioTestCase):
    def _make_instance(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance._background_tasks = BackgroundTaskRegistry("Nahida")
        return instance

    async def asyncSetUp(self):
        patcher = patch.object(background_tasks_module.log, "debug")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_second_start_reuses_the_running_task(self):
        instance = self._make_instance()
        started = []

        async def loop_body(tag):
            started.append(tag)
            await asyncio.sleep(3600)

        first = instance._track_background_task(loop_body("first"), name="dm-followups")
        await asyncio.sleep(0)
        second = instance._track_background_task(loop_body("second"), name="dm-followups")
        await asyncio.sleep(0)

        self.assertIs(second, first)
        self.assertEqual(started, ["first"])
        self.assertEqual(len(instance._background_tasks), 1)

        await instance._cancel_background_tasks()

    async def test_distinct_names_still_run_side_by_side(self):
        instance = self._make_instance()

        async def idle():
            await asyncio.sleep(3600)

        followups = instance._track_background_task(idle(), name="dm-followups")
        reminders = instance._track_background_task(idle(), name="reminders")
        await asyncio.sleep(0)

        self.assertIsNot(followups, reminders)
        self.assertEqual(len(instance._background_tasks), 2)

        await instance._cancel_background_tasks()

    async def test_a_finished_task_can_be_restarted(self):
        instance = self._make_instance()
        runs = []

        async def one_shot():
            runs.append(1)

        first = instance._track_background_task(one_shot(), name="retry-pending-auto-profiles")
        await first

        second = instance._track_background_task(one_shot(), name="retry-pending-auto-profiles")
        await second

        self.assertIsNot(second, first)
        self.assertEqual(len(runs), 2)

    async def test_cancel_clears_the_name_index(self):
        instance = self._make_instance()

        async def idle():
            await asyncio.sleep(3600)

        instance._track_background_task(idle(), name="dm-followups")
        await asyncio.sleep(0)
        await instance._cancel_background_tasks()

        self.assertEqual(instance._background_tasks.names, ())
        self.assertEqual(len(instance._background_tasks), 0)


class OnReadyLoopStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_on_ready_does_not_duplicate_the_followup_loop(self):
        """The reported duplicate DMs came from on_ready running twice."""
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance._background_tasks = BackgroundTaskRegistry("Nahida")
        cycles = []

        async def followup_loop():
            while True:
                await asyncio.sleep(0)
                cycles.append(1)
                await asyncio.sleep(3600)

        with patch.object(background_tasks_module.log, "debug"):
            for _ in range(3):
                instance._track_background_task(followup_loop(), name="dm-followups")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        self.assertEqual(len(cycles), 1)
        self.assertEqual(len(instance._background_tasks), 1)

        await instance._cancel_background_tasks()


if __name__ == "__main__":
    unittest.main()
