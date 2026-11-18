import types
import unittest

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module
import discord_utils as discord_utils_module
from history_sandbox import HistorySandbox


BOT_USER_ID = 999
DM_KEY = discord_utils_module.dm_history_key("Firefly", 42)


class _FakeHistoryChannel:
    def __init__(self, channel_id, messages, guild=None):
        self.id = channel_id
        self.guild = guild
        self._messages = messages

    def history(self, limit=None):
        async def newest_first():
            for message in self._messages[:limit]:
                yield message
        return newest_first()


def _user(user_id, name, *, bot=False):
    return types.SimpleNamespace(id=user_id, bot=bot, display_name=name, name=name.lower())


def _message(message_id, author, content):
    return types.SimpleNamespace(id=message_id, author=author, content=content, created_at=None)


def _make_bot(name="Firefly"):
    instance = object.__new__(bot_instance_module.BotInstance)
    instance.name = name
    instance.character = types.SimpleNamespace(name=name)
    instance.client = types.SimpleNamespace(user=_user(BOT_USER_ID, f"{name} Bot", bot=True))
    return instance


class RecallTests(HistorySandbox, unittest.IsolatedAsyncioTestCase):
    async def test_recall_in_a_dm_stores_the_bots_own_lines_as_its_turns(self):
        bot = _make_bot()
        own = _user(BOT_USER_ID, "Firefly Bot", bot=True)
        alice = _user(42, "Alice")
        channel = _FakeHistoryChannel(555, [
            _message(3, own, "Sleep well, Alice."),
            _message(2, alice, "Good night"),
            _message(1, own, "Evening!"),
        ])

        recalled = await bot._recall_channel_history(channel, limit=30, history_key=DM_KEY)

        self.assertEqual(recalled, 3)
        stored = discord_utils_module.get_history(DM_KEY)
        self.assertEqual(
            [(entry["role"], entry["author"], entry["content"]) for entry in stored],
            [
                ("assistant", "Firefly", "Evening!"),
                ("user", "Alice", "Good night"),
                ("assistant", "Firefly", "Sleep well, Alice."),
            ],
        )
        _, immediate = discord_utils_module.format_history_split(DM_KEY, current_bot_name="Firefly")
        self.assertEqual([turn["role"] for turn in immediate], ["assistant", "user", "assistant"])

    async def test_recall_in_a_server_keeps_another_bot_as_another_speaker(self):
        bot = _make_bot()
        own = _user(BOT_USER_ID, "Firefly Bot", bot=True)
        kafka = _user(777, "Kafka", bot=True)
        guild = types.SimpleNamespace(id=5, name="Belobog", me=own)
        channel = _FakeHistoryChannel(77, [
            _message(2, kafka, "Hello, little firefly."),
            _message(1, own, "Hi, Kafka."),
        ], guild=guild)

        await bot._recall_channel_history(channel, limit=30, history_key=77)

        stored = discord_utils_module.get_history(77)
        self.assertEqual(
            [(entry["role"], entry["author"]) for entry in stored],
            [("assistant", "Firefly"), ("user", "Kafka")],
        )
        self.assertTrue(stored[1]["is_bot"])


if __name__ == "__main__":
    unittest.main()
