import unittest

from coordinator import coordinator


class CoordinatorBusyTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_busy_lifecycle(self):
        coordinator._active_requests.clear()
        coordinator._active_channels.clear()
        coordinator._response_queue.clear()

        acquired = await coordinator.acquire_slot("BotA", 101, 555)
        self.assertTrue(acquired)
        self.assertTrue(coordinator.is_channel_busy(555))

        coordinator.release_slot("BotA", 101, 555)
        self.assertFalse(coordinator.is_channel_busy(555))


if __name__ == "__main__":
    unittest.main()
