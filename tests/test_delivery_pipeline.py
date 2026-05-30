import asyncio
import unittest

from delivery_pipeline import (
    DeliveryNotSentError,
    DeliveryPlan,
    DeliverySendRequest,
    DeliveryState,
    deliver_multipart_response,
)


class ScriptedSender:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[DeliverySendRequest] = []

    async def __call__(self, request: DeliverySendRequest) -> str:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def calls(self):
        return [(request.part_index, request.attempt) for request in self.requests]


class DeliveryPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_parts_success(self):
        plan = DeliveryPlan.from_parts(
            ["one", "two", "three"],
            correlation_id="corr-1",
            idempotency_key="idem-1",
        )
        sender = ScriptedSender(["msg-1", "msg-2", "msg-3"])

        outcome = await deliver_multipart_response(plan, sender)

        self.assertEqual(outcome.state, DeliveryState.SUCCESS)
        self.assertEqual(outcome.confirmed_message_ids, ("msg-1", "msg-2", "msg-3"))
        self.assertEqual(outcome.persistable_visible_text, "one\n\ntwo\n\nthree")
        self.assertEqual(sender.calls, [(0, 0), (1, 0), (2, 0)])

    async def test_fail_before_first_confirmed_is_failed_with_no_persist_text(self):
        plan = DeliveryPlan.from_parts(
            ["one", "two"],
            correlation_id="corr-2",
            idempotency_key="idem-2",
        )
        sender = ScriptedSender([
            DeliveryNotSentError("pre-send rejection"),
            DeliveryNotSentError("still rejected"),
        ])

        outcome = await deliver_multipart_response(plan, sender)

        self.assertEqual(outcome.state, DeliveryState.FAILED)
        self.assertEqual(outcome.persistable_visible_text, "")
        self.assertEqual(outcome.confirmed_message_ids, ())
        self.assertEqual(outcome.failed_part_index, 0)
        self.assertEqual(outcome.retry_count, 1)
        self.assertEqual(sender.calls, [(0, 0), (0, 1)])

    async def test_fail_after_first_confirmed_is_partial_with_confirmed_persist_text_only(self):
        plan = DeliveryPlan.from_parts(
            ["one", "two", "three"],
            correlation_id="corr-3",
            idempotency_key="idem-3",
        )
        sender = ScriptedSender([
            "msg-1",
            DeliveryNotSentError("known not sent"),
            DeliveryNotSentError("still not sent"),
        ])

        outcome = await deliver_multipart_response(plan, sender)

        self.assertEqual(outcome.state, DeliveryState.PARTIAL)
        self.assertEqual(outcome.persistable_visible_text, "one")
        self.assertEqual(outcome.confirmed_message_ids, ("msg-1",))
        self.assertEqual(outcome.failed_part_index, 1)
        self.assertEqual(outcome.retry_count, 1)
        self.assertEqual(sender.calls, [(0, 0), (1, 0), (1, 1)])

    async def test_retry_succeeds_for_clearly_unconfirmed_remainder(self):
        plan = DeliveryPlan.from_parts(
            ["one", "two", "three"],
            correlation_id="corr-4",
            idempotency_key="idem-4",
        )
        sender = ScriptedSender([
            "msg-1",
            DeliveryNotSentError("known not sent"),
            "msg-2",
            "msg-3",
        ])

        outcome = await deliver_multipart_response(plan, sender)

        self.assertEqual(outcome.state, DeliveryState.SUCCESS)
        self.assertEqual(outcome.persistable_visible_text, "one\n\ntwo\n\nthree")
        self.assertEqual(outcome.confirmed_message_ids, ("msg-1", "msg-2", "msg-3"))
        self.assertEqual(outcome.retry_count, 1)
        self.assertEqual(sender.calls, [(0, 0), (1, 0), (1, 1), (2, 1)])

    async def test_ambiguous_timeout_is_not_duplicated(self):
        plan = DeliveryPlan.from_parts(
            ["one", "two", "three"],
            correlation_id="corr-5",
            idempotency_key="idem-5",
        )
        sender = ScriptedSender(["msg-1", TimeoutError("discord timeout")])

        outcome = await deliver_multipart_response(plan, sender)

        self.assertEqual(outcome.state, DeliveryState.PARTIAL)
        self.assertEqual(outcome.persistable_visible_text, "one")
        self.assertEqual(outcome.confirmed_message_ids, ("msg-1",))
        self.assertEqual(outcome.ambiguous_part_index, 1)
        self.assertEqual(outcome.retry_count, 0)
        self.assertEqual(sender.calls, [(0, 0), (1, 0)])

    async def test_cancellation_before_first_confirmed_propagates_without_persist_text(self):
        plan = DeliveryPlan.from_parts(
            ["one", "two"],
            correlation_id="corr-cancel-1",
            idempotency_key="idem-cancel-1",
        )
        sender = ScriptedSender([asyncio.CancelledError()])

        with self.assertRaises(asyncio.CancelledError):
            await deliver_multipart_response(plan, sender)

        self.assertEqual(sender.calls, [(0, 0)])

    async def test_cancellation_after_confirmed_part_returns_partial_outcome(self):
        plan = DeliveryPlan.from_parts(
            ["one", "two"],
            correlation_id="corr-cancel-2",
            idempotency_key="idem-cancel-2",
        )
        sender = ScriptedSender(["msg-1", asyncio.CancelledError()])

        outcome = await deliver_multipart_response(plan, sender)

        self.assertEqual(outcome.state, DeliveryState.PARTIAL)
        self.assertEqual(outcome.persistable_visible_text, "one")
        self.assertEqual(outcome.confirmed_message_ids, ("msg-1",))
        self.assertEqual(outcome.ambiguous_part_index, 1)
        self.assertEqual(outcome.error, "CancelledError")
        self.assertEqual(sender.calls, [(0, 0), (1, 0)])

    async def test_outcome_and_requests_preserve_correlation_and_idempotency_fields(self):
        plan = DeliveryPlan.from_parts(
            ["one"],
            correlation_id="message-123",
            idempotency_key="bot-a:message-123:response-1",
        )
        sender = ScriptedSender(["discord-message-1"])

        outcome = await deliver_multipart_response(plan, sender)

        self.assertEqual(outcome.correlation_id, "message-123")
        self.assertEqual(outcome.idempotency_key, "bot-a:message-123:response-1")
        self.assertEqual(sender.requests[0].correlation_id, "message-123")
        self.assertEqual(sender.requests[0].idempotency_key, "bot-a:message-123:response-1")
        self.assertEqual(sender.requests[0].part_idempotency_key, "bot-a:message-123:response-1:0")


if __name__ == "__main__":
    unittest.main()
