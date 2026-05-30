import asyncio
import unittest
from unittest.mock import patch

import coordinator as coordinator_module


class CoordinatorCapacityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        coordinator_module.GlobalCoordinator._instance = None
        self.limit = 1
        self.runtime_get = patch.object(
            coordinator_module.runtime_config,
            "get",
            side_effect=lambda key, default=None: self.limit if key == "concurrency_limit" else default,
        )
        self.runtime_get.start()

    def tearDown(self):
        self.runtime_get.stop()
        coordinator_module.GlobalCoordinator._instance = None

    def new_coordinator(self):
        coordinator = coordinator_module.GlobalCoordinator()
        coordinator._RESIZE_POLL_INTERVAL = 0.001
        return coordinator

    async def test_resize_during_active_request_keeps_token_releasable(self):
        self.limit = 2
        coordinator = self.new_coordinator()
        first = await coordinator.acquire_slot("Nahida", 101)

        self.limit = 1
        waiting = asyncio.create_task(coordinator.acquire_slot("Kaveh", 102))
        await asyncio.sleep(0)

        self.assertFalse(waiting.done())
        self.assertEqual(coordinator.get_active_bots_for_message(101), {"Nahida"})

        coordinator.release_slot(first)
        second = await asyncio.wait_for(waiting, timeout=0.2)

        self.assertTrue(first.released)
        self.assertEqual(coordinator.get_active_bots_for_message(101), set())
        self.assertEqual(coordinator.get_active_bots_for_message(102), {"Kaveh"})
        coordinator.release_slot(second)

    async def test_double_release_is_harmless_and_does_not_add_capacity(self):
        coordinator = self.new_coordinator()
        first = await coordinator.acquire_slot("Nahida", 201)

        coordinator.release_slot(first)
        coordinator.release_slot(first)

        second = await asyncio.wait_for(coordinator.acquire_slot("Kaveh", 202), timeout=0.2)
        waiting = asyncio.create_task(coordinator.acquire_slot("Alhaitham", 203))
        await asyncio.sleep(0)

        self.assertFalse(waiting.done())

        coordinator.release_slot(second)
        third = await asyncio.wait_for(waiting, timeout=0.2)
        coordinator.release_slot(third)

    async def test_legacy_release_path_releases_matching_token_once(self):
        coordinator = self.new_coordinator()
        first = await coordinator.acquire_slot("Nahida", 301)

        coordinator.release_slot("Nahida", 301)
        coordinator.release_slot("Nahida", 301)

        self.assertTrue(first.released)
        second = await asyncio.wait_for(coordinator.acquire_slot("Kaveh", 302), timeout=0.2)
        coordinator.release_slot(second)

    async def test_grow_permits_additional_waiting_request(self):
        coordinator = self.new_coordinator()
        first = await coordinator.acquire_slot("Nahida", 401)
        waiting = asyncio.create_task(coordinator.acquire_slot("Kaveh", 402))
        await asyncio.sleep(0)

        self.assertFalse(waiting.done())

        self.limit = 2
        second = await asyncio.wait_for(waiting, timeout=0.2)

        self.assertEqual(coordinator.get_active_bots_for_message(401), {"Nahida"})
        self.assertEqual(coordinator.get_active_bots_for_message(402), {"Kaveh"})
        coordinator.release_slot(first)
        coordinator.release_slot(second)

    async def test_shrink_preserves_active_tokens_and_blocks_until_below_limit(self):
        self.limit = 2
        coordinator = self.new_coordinator()
        first = await coordinator.acquire_slot("Nahida", 501)
        second = await coordinator.acquire_slot("Kaveh", 502)

        self.limit = 1
        waiting = asyncio.create_task(coordinator.acquire_slot("Alhaitham", 503))
        await asyncio.sleep(0)

        self.assertFalse(waiting.done())
        self.assertEqual(coordinator.get_active_bots_for_message(501), {"Nahida"})
        self.assertEqual(coordinator.get_active_bots_for_message(502), {"Kaveh"})

        coordinator.release_slot(first)
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())

        coordinator.release_slot(second)
        third = await asyncio.wait_for(waiting, timeout=0.2)

        self.assertTrue(first.released)
        self.assertTrue(second.released)
        self.assertEqual(coordinator.get_active_bots_for_message(503), {"Alhaitham"})
        coordinator.release_slot(third)
