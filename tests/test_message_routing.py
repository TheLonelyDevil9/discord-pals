import types
import unittest

import module_stubs  # noqa: F401
from message_routing import InboundMessage, TriggerDecision


class InboundMessageTests(unittest.TestCase):
    def test_from_discord_captures_raw_event_facts(self):
        attachment = types.SimpleNamespace(
            id=10,
            filename="image.png",
            content_type="image/png",
            size=1234,
        )
        message = types.SimpleNamespace(
            id=99,
            channel=types.SimpleNamespace(id=77),
            guild=types.SimpleNamespace(id=5),
            author=types.SimpleNamespace(id=42, display_name="Alice", bot=False),
            content="hello",
            attachments=[attachment],
            mentions=[types.SimpleNamespace(id=1)],
            reference=types.SimpleNamespace(message_id=88),
        )

        inbound = InboundMessage.from_discord(
            message,
            correlation_id="req-1",
            is_dm=False,
        )

        self.assertEqual(inbound.correlation_id, "req-1")
        self.assertEqual(inbound.message_id, 99)
        self.assertEqual(inbound.channel_id, 77)
        self.assertEqual(inbound.guild_id, 5)
        self.assertEqual(inbound.user_id, 42)
        self.assertEqual(inbound.author_name, "Alice")
        self.assertEqual(inbound.content_len, 5)
        self.assertEqual(inbound.attachment_count, 1)
        self.assertEqual(inbound.attachments[0].filename, "image.png")
        self.assertEqual(inbound.mention_count, 1)
        self.assertEqual(inbound.reply_message_id, 88)


class TriggerDecisionTests(unittest.TestCase):
    def test_pending_reminder_clarification_forces_response(self):
        decision = TriggerDecision.from_legacy({
            "mentioned": False,
            "is_reply_to_bot": False,
            "is_autonomous": False,
            "name_triggered": False,
            "should_respond": False,
        }).with_pending_reminder_clarification()

        self.assertTrue(decision.should_respond)
        self.assertTrue(decision.pending_reminder_clarification)
        self.assertEqual(decision.reason_keys, ["pending_reminder_clarification"])
        self.assertTrue(decision.allows_auto_reminders(is_dm=False))

    def test_legacy_dict_round_trip_preserves_trigger_keys(self):
        decision = TriggerDecision.from_legacy({
            "mentioned": True,
            "is_reply_to_bot": False,
            "is_autonomous": True,
            "name_triggered": False,
            "should_respond": True,
        })

        self.assertEqual(decision.to_legacy_dict(), {
            "mentioned": True,
            "is_reply_to_bot": False,
            "is_autonomous": True,
            "name_triggered": False,
            "pending_reminder_clarification": False,
            "should_respond": True,
        })
        self.assertEqual(decision.reason_keys, ["mentioned", "is_autonomous"])


if __name__ == "__main__":
    unittest.main()
