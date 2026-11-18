"""Conversation history files must reload after a restart on every platform."""

import json
import os
import time
import unittest
from unittest.mock import patch

import module_stubs  # noqa: F401
import discord_utils as discord_utils_module
from history_sandbox import HistorySandbox


WINDOWS_FORBIDDEN_NAME_CHARACTERS = set('<>:"/\\|?*')
LEGACY_COLON_NAMES = os.name == "nt"
COLON_SKIP_REASON = "Windows cannot create the colon file names that Linux installs have"


class HistoryFileNameTests(HistorySandbox, unittest.TestCase):
    def test_dm_history_survives_a_restart(self):
        dm_key = discord_utils_module.dm_history_key("Firefly", 42)
        discord_utils_module.add_to_history(dm_key, "user", "Good night, Firefly", author_name="Alice", user_id=42, message_id=1)
        discord_utils_module.add_to_history(dm_key, "assistant", "Sleep well, Alice.", author_name="Firefly", message_id=2)
        discord_utils_module.save_history(force=True)

        self.restart()

        stored = discord_utils_module.get_history(dm_key)
        self.assertEqual([entry["content"] for entry in stored], ["Good night, Firefly", "Sleep well, Alice."])
        self.assertEqual([entry["role"] for entry in stored], ["user", "assistant"])

    def test_history_file_names_are_valid_on_windows_and_hold_their_key(self):
        dm_key = discord_utils_module.dm_history_key("Firefly", 42)
        discord_utils_module.add_to_history(dm_key, "user", "hello", author_name="Alice", message_id=1)
        discord_utils_module.add_to_history(77, "user", "hello channel", author_name="Bob", message_id=3)
        discord_utils_module.save_history(force=True)

        names = self.file_names()
        self.assertEqual(len(names), 2)
        self.assertIn("77.json", names)
        for name in names:
            self.assertFalse(set(name) & WINDOWS_FORBIDDEN_NAME_CHARACTERS, name)
        stored_keys = {json.loads((self.history_dir / name).read_text(encoding="utf-8"))["history_id"] for name in names}
        self.assertEqual(stored_keys, {dm_key, 77})

    def test_keys_that_differ_only_in_letter_case_get_separate_files(self):
        upper = discord_utils_module.dm_history_key("Firefly", 42)
        lower = discord_utils_module.dm_history_key("firefly", 42)
        discord_utils_module.add_to_history(upper, "user", "for the first bot", author_name="Alice", message_id=1)
        discord_utils_module.add_to_history(lower, "user", "for the second bot", author_name="Alice", message_id=2)
        discord_utils_module.save_history(force=True)

        self.assertEqual(len({name.casefold() for name in self.file_names()}), 2)
        self.restart()
        self.assertEqual(discord_utils_module.get_history(upper)[0]["content"], "for the first bot")
        self.assertEqual(discord_utils_module.get_history(lower)[0]["content"], "for the second bot")


class LegacyHistoryFileTests(HistorySandbox, unittest.TestCase):
    def test_channel_file_from_an_earlier_version_loads_and_gains_its_key(self):
        messages = [{"role": "user", "content": "older line", "author": "Bob"}]
        path = self.write_file("123.json", {"name": "#tea-room", "messages": messages, "last_activity": 50.0})

        discord_utils_module.load_history()

        self.assertEqual(discord_utils_module.get_history(123), messages)
        self.assertEqual(self.file_names(), ["123.json"])
        rewritten = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rewritten["history_id"], 123)
        self.assertEqual(rewritten["messages"], messages)
        self.assertEqual(rewritten["name"], "#tea-room")

    @unittest.skipIf(LEGACY_COLON_NAMES, COLON_SKIP_REASON)
    def test_dm_file_named_with_its_key_loads_and_is_renamed(self):
        dm_key = discord_utils_module.dm_history_key("Firefly", 42)
        messages = [
            {"role": "user", "content": "Are you awake?", "author": "Alice", "user_id": 42},
            {"role": "assistant", "content": "Always, for you.", "author": "Firefly"},
        ]
        legacy = self.write_file(f"{dm_key}.json", {"name": "DM", "messages": messages, "last_activity": 70.0})

        discord_utils_module.load_history()

        self.assertEqual(discord_utils_module.get_history(dm_key), messages)
        self.assertFalse(legacy.exists())
        self.assertEqual(len(self.file_names()), 1)
        self.restart()
        self.assertEqual(discord_utils_module.get_history(dm_key), messages)

    @unittest.skipIf(LEGACY_COLON_NAMES, COLON_SKIP_REASON)
    def test_interrupted_rename_keeps_the_rewritten_file(self):
        dm_key = discord_utils_module.dm_history_key("Firefly", 42)
        discord_utils_module.add_to_history(dm_key, "user", "first", author_name="Alice", message_id=1)
        discord_utils_module.add_to_history(dm_key, "assistant", "second", author_name="Firefly", message_id=2)
        discord_utils_module.save_history(force=True)
        current_names = self.file_names()
        legacy = self.write_file(f"{dm_key}.json", {"name": "DM", "messages": [{"role": "user", "content": "first", "author": "Alice"}]})

        self.restart()

        self.assertEqual([entry["content"] for entry in discord_utils_module.get_history(dm_key)], ["first", "second"])
        self.assertFalse(legacy.exists())
        self.assertEqual(self.file_names(), current_names)

    @unittest.skipIf(LEGACY_COLON_NAMES, COLON_SKIP_REASON)
    def test_failed_rewrite_keeps_the_old_file_until_a_later_save(self):
        dm_key = discord_utils_module.dm_history_key("Firefly", 42)
        messages = [{"role": "user", "content": "keep me", "author": "Alice"}]
        legacy = self.write_file(f"{dm_key}.json", {"name": "DM", "messages": messages})

        with patch.object(discord_utils_module, "safe_json_save", return_value=False):
            discord_utils_module.load_history()

        self.assertTrue(legacy.exists())
        self.assertEqual(discord_utils_module.get_history(dm_key), messages)

        discord_utils_module.save_history(force=True)

        self.assertFalse(legacy.exists())
        self.restart()
        self.assertEqual(discord_utils_module.get_history(dm_key), messages)

    @unittest.skipIf(LEGACY_COLON_NAMES, COLON_SKIP_REASON)
    def test_clearing_a_conversation_also_deletes_its_old_file(self):
        dm_key = discord_utils_module.dm_history_key("Firefly", 42)
        self.write_file(f"{dm_key}.json", {"name": "DM", "messages": [{"role": "user", "content": "x", "author": "Alice"}]})
        with patch.object(discord_utils_module, "safe_json_save", return_value=False):
            discord_utils_module.load_history()

        discord_utils_module.clear_history(dm_key)

        self.assertEqual(self.file_names(), [])
        self.restart()
        self.assertEqual(discord_utils_module.get_history(dm_key), [])

    def test_loading_current_files_again_rewrites_nothing(self):
        dm_key = discord_utils_module.dm_history_key("Firefly", 42)
        discord_utils_module.add_to_history(dm_key, "user", "hello", author_name="Alice", message_id=1)
        discord_utils_module.add_to_history(77, "user", "hello channel", author_name="Bob", message_id=3)
        discord_utils_module.save_history(force=True)

        with patch.object(discord_utils_module, "safe_json_save", wraps=discord_utils_module.safe_json_save) as save_spy:
            self.restart()
            self.restart()

        save_spy.assert_not_called()
        self.assertEqual(discord_utils_module.get_history(dm_key)[0]["content"], "hello")
        self.assertEqual(discord_utils_module.get_history(77)[0]["content"], "hello channel")

    def test_a_file_that_fails_to_parse_is_left_as_it_is(self):
        self.history_dir.mkdir(parents=True, exist_ok=True)
        broken = self.history_dir / "123.json"
        broken.write_text('{"name": "#tea-room", "messages": [{"role": "user", "content": "cut off', encoding="utf-8")

        discord_utils_module.load_history()

        self.assertEqual(broken.read_text(encoding="utf-8"), '{"name": "#tea-room", "messages": [{"role": "user", "content": "cut off')
        self.assertNotIn(123, discord_utils_module.conversation_history)

    def test_a_file_that_is_not_text_is_left_as_it_is(self):
        self.history_dir.mkdir(parents=True, exist_ok=True)
        binary = self.history_dir / "123.json"
        binary.write_bytes(b"\xff\xfe\x00garbage")
        self.write_file("456.json", {"name": "#fine", "messages": [{"role": "user", "content": "still here"}]})

        discord_utils_module.load_history()

        self.assertEqual(binary.read_bytes(), b"\xff\xfe\x00garbage")
        self.assertEqual(discord_utils_module.get_history(456)[0]["content"], "still here")

    def test_a_file_without_a_message_list_is_skipped_with_one_warning(self):
        listing = self.write_file("123.json", [{"role": "user", "content": "not wrapped"}])
        wrong_messages = self.write_file("124.json", {"name": "#odd", "messages": "not a list"})

        with patch.object(discord_utils_module.log, "warn") as warn:
            discord_utils_module.load_history()

        self.assertEqual(json.loads(listing.read_text(encoding="utf-8")), [{"role": "user", "content": "not wrapped"}])
        self.assertEqual(json.loads(wrong_messages.read_text(encoding="utf-8"))["messages"], "not a list")
        warned = [call.args[0] for call in warn.call_args_list]
        self.assertEqual(sum("123.json" in line for line in warned), 1)
        self.assertEqual(sum("124.json" in line for line in warned), 1)

    def test_a_file_that_breaks_the_parser_does_not_stop_the_load(self):
        self.history_dir.mkdir(parents=True, exist_ok=True)
        nested = self.history_dir / "123.json"
        nested.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
        self.write_file("456.json", {"name": "#fine", "messages": [{"role": "user", "content": "still here"}]})

        discord_utils_module.load_history()

        self.assertEqual(discord_utils_module.get_history(456)[0]["content"], "still here")
        self.assertTrue(nested.exists())

    def test_a_malformed_file_does_not_stop_the_others_from_loading(self):
        self.write_file("11.json", {"name": "#broken", "messages": ["not an entry"]})
        self.write_file("22.json", {"name": "#fine", "messages": [{"role": "user", "content": "still here"}]})

        discord_utils_module.load_history()

        self.assertEqual(discord_utils_module.get_history(22)[0]["content"], "still here")
        self.assertEqual(discord_utils_module.get_history(11), [])
        self.assertIn("11.json", self.file_names())


class HistoryLimitTests(HistorySandbox, unittest.TestCase):
    """The 500-conversation limit deletes the files of what it evicts."""

    def _start_with(self, *, channels, idle_channels, dms, idle_dms):
        now = time.time()
        keys = [1000 + index for index in range(channels)]
        keys += [discord_utils_module.dm_history_key("Firefly", 5000 + index) for index in range(dms)]
        for position, key in enumerate(keys):
            idle = position < idle_channels if isinstance(key, int) else position - channels < idle_dms
            discord_utils_module.conversation_history[key] = [{"role": "user", "content": f"line for {key}", "author": "Alice"}]
            discord_utils_module._channel_last_activity[key] = now - (3 * 86400 if idle else 60)
            discord_utils_module._dirty_history_channels.add(key)
        discord_utils_module.save_history(force=True)
        self.restart()
        return set(self.file_names())

    def test_dm_histories_do_not_count_toward_the_limit(self):
        before = self._start_with(channels=400, idle_channels=200, dms=150, idle_dms=75)

        discord_utils_module.add_to_history(1399, "user", "a new message", author_name="Alice", message_id=1)

        self.assertEqual(set(self.file_names()), before)

    def test_over_the_limit_only_idle_channel_files_are_deleted(self):
        before = self._start_with(channels=520, idle_channels=300, dms=150, idle_dms=75)

        discord_utils_module.add_to_history(1519, "user", "a new message", author_name="Alice", message_id=1)

        self.assertEqual(before - set(self.file_names()), {f"{1000 + index}.json" for index in range(300)})


if __name__ == "__main__":
    unittest.main()
