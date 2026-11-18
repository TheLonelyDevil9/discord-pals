import types
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

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


class _FakeDMHistoryChannel(_FakeHistoryChannel, bot_instance_module.discord.DMChannel):
    pass


def _interaction(channel, user):
    return types.SimpleNamespace(
        channel=channel,
        channel_id=channel.id,
        user=user,
        response=types.SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=types.SimpleNamespace(send=AsyncMock()),
    )


def _command(bot, name):
    return next(command for command in bot.tree._commands if command.name == name)


def _user(user_id, name, *, bot=False):
    return types.SimpleNamespace(id=user_id, bot=bot, display_name=name, name=name.lower())


def _message(message_id, author, content, channel=None, created_at=None):
    return types.SimpleNamespace(id=message_id, author=author, content=content, channel=channel, created_at=created_at)


def _dm_channel(channel_id, recipient=None):
    channel = bot_instance_module.discord.DMChannel()
    channel.id = channel_id
    channel.recipient = recipient
    return channel


def _make_bot(name="Firefly"):
    instance = object.__new__(bot_instance_module.BotInstance)
    instance.name = name
    instance.character = types.SimpleNamespace(name=name)
    instance.client = types.SimpleNamespace(user=_user(BOT_USER_ID, f"{name} Bot", bot=True))
    return instance


def _make_registered_bot(name="Firefly"):
    """Build a bot through its constructor so its Discord event handlers are registered."""
    bot = bot_instance_module.BotInstance(name, "token", name.lower())
    bot.character = types.SimpleNamespace(name=name)
    bot.client.user = _user(BOT_USER_ID, f"{name} Bot", bot=True)
    return bot


class _FakeDeletableMessage:
    def __init__(self, message_id, author, created_at=None):
        self.id = message_id
        self.author = author
        self.created_at = created_at
        self.content = "deleted text"
        self.deleted = False

    async def delete(self):
        self.deleted = True


SENT_AT = datetime(2026, 11, 18, 9, 30, 15, 250000, tzinfo=timezone.utc)


class DeleteTests(HistorySandbox, unittest.IsolatedAsyncioTestCase):
    def _record(self, history_id, content, *, author="Firefly", message_id=None, timestamp=None):
        entry = {"role": "assistant", "content": content, "author": author, "timestamp": timestamp or "2026-11-01T10:00:00+00:00"}
        if message_id is not None:
            entry["message_id"] = message_id
        discord_utils_module.conversation_history.setdefault(history_id, []).append(entry)

    def _contents(self, history_id):
        return [entry["content"] for entry in discord_utils_module.get_history(history_id)]

    async def test_deleting_a_reply_in_a_server_removes_only_that_entry(self):
        bot = _make_registered_bot()
        channel = types.SimpleNamespace(id=77)
        self._record(77, "mine", message_id=10)
        self._record(77, "Kafka's", author="Kafka", message_id=11)

        await bot.client.on_message_delete(_message(10, bot.client.user, "mine", channel))

        self.assertEqual(self._contents(77), ["Kafka's"])

    async def test_deleting_a_notice_that_was_never_recorded_leaves_history_alone(self):
        bot = _make_registered_bot()
        channel = types.SimpleNamespace(id=77)
        self._record(77, "older reply without an ID")
        self._record(77, "Kafka's reply without an ID", author="Kafka")

        await bot.client.on_message_delete(_message(99, bot.client.user, "Something went wrong", channel, SENT_AT))

        self.assertEqual(self._contents(77), ["older reply without an ID", "Kafka's reply without an ID"])

    async def test_deleting_a_reply_recorded_without_an_id_removes_the_entry_from_that_second(self):
        bot = _make_registered_bot()
        channel = types.SimpleNamespace(id=77)
        self._record(77, "reminder sent before IDs were stored", timestamp="2026-11-18T09:30:15+00:00")
        self._record(77, "Kafka's reply without an ID", author="Kafka", timestamp="2026-11-18T09:30:15+00:00")

        await bot.client.on_message_delete(_message(55, bot.client.user, "reminder", channel, SENT_AT))

        self.assertEqual(self._contents(77), ["Kafka's reply without an ID"])

    async def test_deleting_a_reply_in_a_dm_removes_it_from_that_dms_history(self):
        bot = _make_registered_bot()
        alice = _user(42, "Alice")
        self._record(DM_KEY, "Sleep well.", message_id=10)

        await bot.client.on_message_delete(_message(10, bot.client.user, "Sleep well.", _dm_channel(555, recipient=alice)))

        self.assertEqual(self._contents(DM_KEY), [])

    async def test_a_dm_without_a_known_recipient_is_found_through_its_conversation(self):
        bot = _make_registered_bot()
        bot._dm_followup_state[42] = {"channel_id": 555, "history_key": DM_KEY}
        self._record(DM_KEY, "Sleep well.", message_id=10)

        await bot.client.on_message_delete(_message(10, bot.client.user, "Sleep well.", _dm_channel(555)))

        self.assertEqual(self._contents(DM_KEY), [])

    async def test_delete_messages_command_in_a_dm_removes_the_deleted_reply_from_that_dm(self):
        bot = _make_registered_bot()
        alice = _user(42, "Alice")
        self._record(DM_KEY, "first", message_id=10)
        self._record(DM_KEY, "second", message_id=11)
        newest = _FakeDeletableMessage(11, bot.client.user, SENT_AT)
        channel = _FakeDMHistoryChannel(555, [newest, _FakeDeletableMessage(10, bot.client.user, SENT_AT)])

        await _command(bot, "delete_messages").callback(_interaction(channel, alice), 1)

        self.assertTrue(newest.deleted)
        self.assertEqual(self._contents(DM_KEY), ["first"])


class EditTests(HistorySandbox, unittest.IsolatedAsyncioTestCase):
    async def test_an_edit_in_a_dm_updates_that_dms_history(self):
        bot = _make_registered_bot()
        alice = _user(42, "Alice")
        channel = _dm_channel(555)
        discord_utils_module.add_to_history(DM_KEY, "user", "I love tea", author_name="Alice", user_id=42, message_id=10)

        await bot.client.on_message_edit(
            _message(10, alice, "I love tea", channel),
            _message(10, alice, "I love coffee", channel),
        )

        self.assertEqual(discord_utils_module.get_history(DM_KEY)[0]["content"], "I love coffee")
        self.assertEqual(discord_utils_module.get_history(555), [])


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

    async def test_recall_command_in_a_dm_fills_that_dms_history(self):
        bot = _make_registered_bot()
        alice = _user(42, "Alice")
        channel = _FakeDMHistoryChannel(555, [_message(2, alice, "Are you there?"), _message(1, alice, "Hi")])
        interaction = _interaction(channel, alice)

        await _command(bot, "recall").callback(interaction, 20)

        self.assertEqual([entry["content"] for entry in discord_utils_module.get_history(DM_KEY)], ["Hi", "Are you there?"])
        self.assertEqual(discord_utils_module.get_history(555), [])
        interaction.followup.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
