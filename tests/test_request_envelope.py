import types
import unittest

import module_stubs  # noqa: F401
from request_envelope import RequestEnvelope


class RequestEnvelopeTests(unittest.TestCase):
    def test_legacy_dict_round_trip_preserves_current_request_shape(self):
        message = types.SimpleNamespace(id=123)
        guild = types.SimpleNamespace(id=456)
        target = types.SimpleNamespace(id=789)
        request = {
            "id": 1,
            "req_id": "req-1",
            "timestamp": 10.5,
            "channel_id": 42,
            "message": message,
            "content": " Hello ",
            "content_stripped": "Hello",
            "request_signature": ("Hello", 789),
            "guild": guild,
            "attachments": [types.SimpleNamespace(id=1)],
            "user_name": "Alice",
            "is_dm": False,
            "user_id": 99,
            "sticker_info": None,
            "from_interact_command": True,
            "split_reply_target": target,
            "forced_target_user_id": 100,
            "forced_target_user_name": "Bob",
            "allow_auto_reminders": True,
            "pending_reminder_clarification": {"kind": "time"},
            "is_autonomous": True,
            "dm_invite_requested": True,
        }

        envelope = RequestEnvelope.from_legacy_dict(request)
        legacy = envelope.to_legacy_dict()

        self.assertEqual(envelope.correlation_id, "req-1")
        self.assertEqual(envelope.req_id, "req-1")
        self.assertEqual(envelope.request_signature, ("Hello", 789))
        self.assertIs(envelope.direct_target, target)
        self.assertEqual(legacy["req_id"], request["req_id"])
        self.assertEqual(legacy["split_reply_target"], request["split_reply_target"])
        self.assertEqual(legacy["direct_target"], request["split_reply_target"])
        self.assertEqual(legacy["pending_reminder_clarification"], {"kind": "time"})

    def test_envelope_copies_mutable_collections_at_the_queue_seam(self):
        attachment = types.SimpleNamespace(id=1)
        envelope = RequestEnvelope(
            id=1,
            correlation_id="req-1",
            timestamp=1.0,
            channel_id=42,
            message=types.SimpleNamespace(),
            content="Hello",
            content_stripped="Hello",
            request_signature=("Hello", None),
            guild=None,
            attachments=(attachment,),
            user_name="Alice",
            is_dm=False,
            user_id=99,
            pending_reminder_clarification={"kind": "time"},
        )

        legacy = envelope.to_legacy_dict()
        legacy["attachments"].append(types.SimpleNamespace(id=2))
        legacy["pending_reminder_clarification"]["kind"] = "date"

        self.assertEqual(envelope.attachments, (attachment,))
        self.assertEqual(envelope.pending_reminder_clarification, {"kind": "time"})


if __name__ == "__main__":
    unittest.main()
