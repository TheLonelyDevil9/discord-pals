"""Owner-only commands recognise whoever hosts the deployment.

Each bot is its own Discord application, so the application owner differs per
bot. Operators are configured once for the host and pass on every bot here.
"""

import types
import unittest
from unittest.mock import AsyncMock, patch

import module_stubs  # noqa: F401
import runtime_config
from commands import core as core_commands


def _interaction(user_id, *, app_owner_id=1111):
    application_info = types.SimpleNamespace(
        team=None,
        owner=types.SimpleNamespace(id=app_owner_id),
    )
    return types.SimpleNamespace(
        user=types.SimpleNamespace(id=user_id),
        client=types.SimpleNamespace(application_info=AsyncMock(return_value=application_info)),
    )


class IsOperatorTests(unittest.TestCase):
    def test_configured_id_is_an_operator(self):
        config = {"operator_user_ids": ["4242"]}

        self.assertTrue(runtime_config.is_operator(4242, config))
        self.assertTrue(runtime_config.is_operator("4242", config))

    def test_unconfigured_id_is_not_an_operator(self):
        self.assertFalse(runtime_config.is_operator(4242, {"operator_user_ids": ["9999"]}))

    def test_empty_list_denies_everyone(self):
        self.assertFalse(runtime_config.is_operator(4242, {"operator_user_ids": []}))

    def test_missing_user_id_is_not_an_operator(self):
        self.assertFalse(runtime_config.is_operator(None, {"operator_user_ids": ["4242"]}))


class IsOwnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        core_commands._owner_ids_cache = set()
        core_commands._owner_cache_populated = False
        self.addCleanup(setattr, core_commands, "_owner_ids_cache", set())
        self.addCleanup(setattr, core_commands, "_owner_cache_populated", False)

    async def test_operator_passes_without_owning_the_application(self):
        interaction = _interaction(4242)

        with patch.object(runtime_config, "is_operator", return_value=True):
            self.assertTrue(await core_commands.is_owner(interaction))

        # An operator is recognised without paying for an application_info lookup.
        interaction.client.application_info.assert_not_awaited()

    async def test_application_owner_still_passes_with_no_operators_configured(self):
        interaction = _interaction(1111)

        with patch.object(runtime_config, "is_operator", return_value=False):
            self.assertTrue(await core_commands.is_owner(interaction))

    async def test_everyone_else_is_still_denied(self):
        interaction = _interaction(5555)

        with patch.object(runtime_config, "is_operator", return_value=False):
            self.assertFalse(await core_commands.is_owner(interaction))


if __name__ == "__main__":
    unittest.main()
