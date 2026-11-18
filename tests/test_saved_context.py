import base64
import copy
import json
import unittest

import module_stubs  # noqa: F401
import runtime_config

MB = 1024 * 1024


class SavedContextTests(unittest.TestCase):
    BOT = "K2 saved context"

    def tearDown(self):
        runtime_config.get_last_context().pop(self.BOT, None)

    def test_the_saved_context_keeps_a_placeholder_instead_of_each_image(self):
        png = base64.b64encode(b"\x89PNG" + b"x" * 300_000).decode()
        jpeg = base64.b64encode(b"j" * (3 * MB)).decode()
        messages = [
            {"role": "system", "content": "SYSTEM", "kind": "chatroom_context"},
            {"role": "user", "content": [
                {"type": "text", "text": "Alice: look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + png}},
                {"type": "image_url", "image_url": {"url": "data:image/jpg;base64," + jpeg}},
            ]},
        ]
        sent_to_provider = copy.deepcopy(messages)

        runtime_config.store_last_context(self.BOT, "SYSTEM", messages, 42)
        stored = runtime_config.get_last_context(self.BOT)

        self.assertTrue(messages == sent_to_provider, "the request sent to the provider lost its images")
        serialized = json.dumps(stored)
        self.assertFalse(png[:64] in serialized or jpeg[:64] in serialized, "an image payload is still stored")
        self.assertEqual(stored["messages"][0], messages[0])
        self.assertEqual(stored["messages"][1]["content"], [
            {"type": "text", "text": "Alice: look"},
            {"type": "image_url", "image_url": {"url": "[image/png, 293 KB]"}},
            {"type": "image_url", "image_url": {"url": "[image/jpg, 3.0 MB]"}},
        ])
        self.assertEqual(stored["message_count"], 2)
        self.assertEqual(stored["token_estimate"], 42)

    def test_text_only_context_is_stored_as_before(self):
        messages = [{"role": "user", "content": "Alice: hi"}]

        runtime_config.store_last_context(self.BOT, "SYSTEM", messages, 7)

        self.assertEqual(runtime_config.get_last_context(self.BOT), {
            "system_prompt": "SYSTEM",
            "messages": messages,
            "token_estimate": 7,
            "message_count": 1,
        })


if __name__ == "__main__":
    unittest.main()
