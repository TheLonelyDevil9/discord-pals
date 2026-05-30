import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import module_stubs  # noqa: F401
from post_response_tasks import PostResponseTaskContext, PostResponseTasks


class PostResponseTasksTests(unittest.TestCase):
    def test_records_visible_history_and_schedules_followups_after_confirmed_delivery(self):
        bot = types.SimpleNamespace(
            name="Nahida",
            character=types.SimpleNamespace(name="Nahida"),
            _record_emoji_budget=Mock(),
            _remember_recent_response=Mock(),
            _reset_failures=Mock(),
            _record_response=Mock(),
            _update_mood=Mock(),
            _send_staggered_reactions=AsyncMock(),
            _maybe_auto_memory=AsyncMock(),
            _maybe_handle_reminder_capture=AsyncMock(),
        )
        sent_records = (
            {"message": types.SimpleNamespace(id=101, created_at="t1"), "content": "one"},
            {"message": types.SimpleNamespace(id=102, created_at="t2"), "content": "two"},
        )
        task_context = PostResponseTaskContext(
            channel_id=77,
            discord_channel_id=77,
            guild_id=5,
            guild=types.SimpleNamespace(id=5),
            is_dm=False,
            user_id=42,
            user_name="Alice",
            content="hello",
            delivered_response="one\n\ntwo",
            sent_records=sent_records,
            reactions=("wave",),
            split_target=None,
            context={"channel_id": 77},
            message=types.SimpleNamespace(id=999),
            request={"guild": None},
            req_id="req-1",
        )

        def close_task(coro):
            coro.close()
            return None

        with patch("post_response_tasks.add_to_history") as add_history_mock, \
                patch("post_response_tasks.runtime_config.update_last_activity") as activity_mock, \
                patch("post_response_tasks.metrics_manager.update_last_activity") as metrics_activity_mock, \
                patch("post_response_tasks.store_multipart_response") as multipart_mock, \
                patch("post_response_tasks.diagnostic_events.log_delivery_complete") as diagnostic_mock, \
                patch("post_response_tasks.asyncio.create_task", side_effect=close_task) as create_task_mock:
            PostResponseTasks(bot).run_after_confirmed_delivery(task_context)

        bot._record_emoji_budget.assert_called_once_with(77, "one\n\ntwo")
        bot._remember_recent_response.assert_called_once_with(77, "one\n\ntwo")
        bot._reset_failures.assert_called_once_with(77)
        bot._record_response.assert_called_once_with(77)
        bot._update_mood.assert_called_once_with(77, "hello", "one\n\ntwo")
        self.assertEqual(add_history_mock.call_count, 2)
        self.assertEqual(add_history_mock.call_args_list[0].args[:3], (77, "assistant", "one"))
        activity_mock.assert_called_once_with("Nahida")
        metrics_activity_mock.assert_called_once()
        multipart_mock.assert_called_once_with(77, [101, 102], "one\n\ntwo")
        diagnostic_mock.assert_called_once()
        self.assertEqual(create_task_mock.call_count, 3)


if __name__ == "__main__":
    unittest.main()
