import unittest

from discord_utils import (
    format_history_split,
    format_history_split_structured,
    get_mentionable_users,
    conversation_history,
)


class HistorySnapshotTests(unittest.TestCase):
    def test_split_uses_history_override(self):
        sample = [
            {"role": "user", "content": "hi", "author": "Kris", "user_id": 1, "message_id": 10},
            {"role": "assistant", "content": "hello", "author": "Aether", "message_id": 11},
            {"role": "assistant", "content": "intrude", "author": "Cecile", "message_id": 12},
        ]

        history, immediate = format_history_split(
            channel_id=9999,
            total_limit=20,
            immediate_count=2,
            current_bot_name="Aether",
            history_override=sample,
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(len(immediate), 2)
        # Other bot assistant messages should be downgraded into user-role with speaker prefix.
        self.assertEqual(immediate[1]["role"], "user")
        self.assertIn("Cecile: intrude", immediate[1]["content"])

    def test_structured_split_adds_speaker_markers(self):
        sample = [
            {"role": "user", "content": "hi", "author": "Kris", "user_id": 1, "message_id": 10},
            {"role": "assistant", "content": "hello", "author": "Aether", "message_id": 11},
            {"role": "assistant", "content": "intrude", "author": "Cecile", "message_id": 12},
        ]

        history, immediate = format_history_split_structured(
            channel_id=9999,
            total_limit=20,
            immediate_count=3,
            current_bot_name="Aether",
            history_override=sample,
        )

        self.assertEqual(history, [])
        self.assertTrue(immediate[0]["content"].startswith("[speaker=Kris|kind=user]"))
        self.assertTrue(immediate[2]["content"].startswith("[speaker=Cecile|kind=bot]"))

    def test_mentionable_users_prefers_history_override(self):
        channel_id = 4242
        conversation_history[channel_id] = [
            {"role": "user", "content": "old", "author": "WrongUser", "user_id": 999}
        ]
        override = [
            {"role": "user", "content": "new", "author": "RightUser", "user_id": 111}
        ]

        users = get_mentionable_users(channel_id, limit=5, guild=None, history_override=override)
        self.assertTrue(users)
        self.assertEqual(users[0]["user_id"], 111)

        conversation_history.pop(channel_id, None)


if __name__ == "__main__":
    unittest.main()
