import types
import unittest

import module_stubs  # noqa: F401
from context_builder import ContextBuilder


class ContextBuilderTests(unittest.TestCase):
    def test_forced_interact_target_overrides_direct_target(self):
        direct_target = types.SimpleNamespace(id=99, display_name="Bob", name="Bob")
        builder = ContextBuilder("Nahida")

        target = builder.resolve_target(
            {
                "user_id": 42,
                "user_name": "Invoker",
                "split_reply_target": direct_target,
                "forced_target_user_id": 42,
                "forced_target_user_name": "Invoker",
            },
            display_name_for=lambda user: user.display_name,
        )

        self.assertEqual(target.target_user_id, 42)
        self.assertEqual(target.target_user_name, "Invoker")
        self.assertIsNone(target.direct_target)

    def test_dm_request_scope_uses_bot_user_history_and_dm_memory_namespace(self):
        builder = ContextBuilder("Nahida")
        message = types.SimpleNamespace(
            channel=types.SimpleNamespace(id=777),
            guild=None,
        )
        target = builder.resolve_target(
            {
                "user_id": 42,
                "user_name": "Alice",
                "is_dm": True,
            },
            display_name_for=lambda user: user.display_name,
        )

        scope = builder.build_request_scope(
            request={"is_dm": True, "user_id": 42, "user_name": "Alice"},
            message=message,
            guild=None,
            target=target,
        )

        self.assertEqual(scope.history_id, "dm:Nahida:user:42")
        self.assertEqual(scope.discord_channel_id, 777)
        self.assertEqual(scope.memory_scope.server_id, "dm:bot:Nahida")
        self.assertEqual(scope.memory_scope.user_id, 42)
        self.assertTrue(scope.is_dm)

    def test_server_scope_key_uses_discord_channel_identity(self):
        builder = ContextBuilder("Nahida")
        guild = types.SimpleNamespace(id=5)
        message = types.SimpleNamespace(channel=types.SimpleNamespace(id=777), guild=guild)

        scope_key = builder.scope_key_for_message(
            message,
            is_dm=False,
            user_id=42,
            guild=guild,
        )

        self.assertEqual(scope_key.history_id, 777)
        self.assertEqual(scope_key.discord_channel_id, 777)
        self.assertEqual(scope_key.guild_id, 5)
        self.assertFalse(scope_key.is_dm)


if __name__ == "__main__":
    unittest.main()
