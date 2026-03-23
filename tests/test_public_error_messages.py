import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
from config import ERROR_DELETE_AFTER


class PublicErrorMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_failure_notice_auto_deletes_in_channel(self):
        instance = object.__new__(bot_instance_module.BotInstance)
        instance.name = "Nahida"
        instance.character = types.SimpleNamespace(name="Nahida")

        channel = types.SimpleNamespace(send=AsyncMock())
        message = types.SimpleNamespace(id=321, channel=channel)
        request = {"message": message}

        with patch.object(bot_instance_module.runtime_config, "get", side_effect=lambda key, default=None: {
            "global_paused": False,
        }.get(key, default)), \
                patch.object(instance, "_build_request_context", new=AsyncMock(return_value={"channel_id": 1})), \
                patch.object(instance, "_generate_ai_response", new=AsyncMock(return_value=None)), \
                patch("coordinator.coordinator.acquire_slot", new=AsyncMock(return_value=True)), \
                patch("coordinator.coordinator.get_stagger_delay", new=AsyncMock(return_value=0.0)), \
                patch("coordinator.coordinator.release_slot", new=Mock()), \
                patch.object(bot_instance_module.log, "debug"), \
                patch.object(bot_instance_module.log, "warn"), \
                patch.object(bot_instance_module.log, "error"):
            await instance._process_request(request)

        channel.send.assert_awaited_once_with(
            "Something went wrong - all providers failed.",
            delete_after=ERROR_DELETE_AFTER
        )
