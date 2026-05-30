import types
import unittest
from unittest.mock import patch

import module_stubs  # noqa: F401
import bot_instance as bot_instance_module


async def async_noop(*args, **kwargs):
    return None


class FakeSentMessage:
    def __init__(self, message_id):
        self.id = message_id


class FakeChannel:
    def __init__(self, outcomes):
        self.id = 9001
        self.outcomes = list(outcomes)
        self.sent = []

    async def send(self, content):
        self.sent.append(content)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeMessage:
    def __init__(self, reply_outcome, channel):
        self.id = 1234
        self.channel = channel
        self._discord_pals_req_id = "req-123"
        self._interaction = None
        self.reply_outcome = reply_outcome
        self.replies = []

    async def reply(self, content):
        self.replies.append(content)
        if isinstance(self.reply_outcome, Exception):
            raise self.reply_outcome
        return self.reply_outcome


class DeliveryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, parts):
        bot = object.__new__(bot_instance_module.BotInstance)
        bot.name = "Test Bot"
        bot._split_response_for_delivery = lambda response: list(parts)
        return bot

    async def test_organic_response_returns_only_confirmed_records_on_partial_send(self):
        bot = self.make_bot(["one", "two"])
        channel = FakeChannel([bot_instance_module.discord.HTTPException("timeout-ish")])
        message = FakeMessage(FakeSentMessage(1), channel)

        with patch.object(bot_instance_module.asyncio, "sleep", new=async_noop):
            records = await bot._send_organic_response(message, "ignored")

        self.assertEqual([record["content"] for record in records], ["one"])
        self.assertEqual([record["message"].id for record in records], [1])
        self.assertEqual(message.replies, ["one"])
        self.assertEqual(channel.sent, ["two"])

    async def test_organic_response_failed_before_first_confirmation_returns_no_records(self):
        bot = self.make_bot(["one", "two"])
        channel = FakeChannel([FakeSentMessage(2)])
        message = FakeMessage(bot_instance_module.discord.HTTPException("reply failed"), channel)

        records = await bot._send_organic_response(message, "ignored")

        self.assertEqual(records, [])
        self.assertEqual(message.replies, ["one"])
        self.assertEqual(channel.sent, [])

    async def test_direct_channel_response_preserves_confirmed_order(self):
        bot = self.make_bot(["one", "two"])
        channel = FakeChannel([FakeSentMessage(10), FakeSentMessage(11)])

        with patch.object(bot_instance_module.asyncio, "sleep", new=async_noop):
            records = await bot._send_organic_response_to_channel(channel, "ignored", req_id="req-direct")

        self.assertEqual([record["content"] for record in records], ["one", "two"])
        self.assertEqual([record["message"].id for record in records], [10, 11])
        self.assertEqual(channel.sent, ["one", "two"])


if __name__ == "__main__":
    unittest.main()
