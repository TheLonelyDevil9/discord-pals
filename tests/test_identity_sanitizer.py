import unittest

try:
    from bot_instance import BotInstance
except ModuleNotFoundError:
    BotInstance = None


class _DummyCharacter:
    name = "Aether"


class _DummyBot:
    name = "AetherBot"
    character = _DummyCharacter()


class IdentitySanitizerTests(unittest.TestCase):
    @unittest.skipIf(BotInstance is None, "bot_instance dependencies unavailable in test env")
    def test_strips_inline_user_speaker_label(self):
        dummy = _DummyBot()
        text = "I can help. Kris WaWa: can i pet you"
        context = {
            "user_name": "Kris WaWa",
            "active_users": ["Kris WaWa"],
            "channel_id": 0,
            "mentionable_users": [],
            "other_bot_names": []
        }
        out = BotInstance._strip_user_attribution_lines(dummy, text, context)
        self.assertNotIn("Kris WaWa:", out)

    @unittest.skipIf(BotInstance is None, "bot_instance dependencies unavailable in test env")
    def test_strips_inline_other_bot_speaker_label(self):
        dummy = _DummyBot()
        text = "Let's proceed. Collei: @Kris I'm here!"
        context = {
            "user_name": "Kris WaWa",
            "active_users": ["Kris WaWa"],
            "channel_id": 0,
            "mentionable_users": [],
            "other_bot_names": ["Collei"]
        }
        out = BotInstance._strip_user_attribution_lines(dummy, text, context)
        self.assertNotIn("Collei:", out)


if __name__ == "__main__":
    unittest.main()
