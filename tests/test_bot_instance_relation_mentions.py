import re
import sys
import types
import unittest

# Lightweight stub for environments without prometheus_client.
if "prometheus_client" not in sys.modules:
    class _MetricStub:
        def labels(self, **_kwargs):
            return self

        def inc(self, *_args, **_kwargs):
            return None

        def observe(self, *_args, **_kwargs):
            return None

        def set(self, *_args, **_kwargs):
            return None

        def info(self, *_args, **_kwargs):
            return None

    def _metric_factory(*_args, **_kwargs):
        return _MetricStub()

    sys.modules["prometheus_client"] = types.SimpleNamespace(
        Counter=_metric_factory,
        Histogram=_metric_factory,
        Gauge=_metric_factory,
        Info=_metric_factory,
        start_http_server=lambda *_args, **_kwargs: None,
    )

try:
    import bot_instance as bot_instance_module
    from bot_instance import BotInstance
except ModuleNotFoundError:
    bot_instance_module = None
    BotInstance = None


class FakeMember:
    def __init__(self, user_id, name, display_name=None, *, bot=False, nick=None, global_name=None):
        self.id = user_id
        self.name = name
        self.display_name = display_name or name
        self.bot = bot
        self.nick = nick
        self.global_name = global_name


class FakeGuild:
    def __init__(self, guild_id=1, members=None):
        self.id = guild_id
        self.members = list(members or [])

    async def query_members(self, query: str, limit: int = 100, cache: bool = True):
        lowered = query.lower().strip()
        canonical = re.sub(r"[^a-z0-9]+", "", lowered)
        out = []
        for member in self.members:
            aliases = {
                str(member.display_name).lower(),
                str(member.name).lower(),
                str(getattr(member, "nick", "") or "").lower(),
                str(getattr(member, "global_name", "") or "").lower(),
            }
            aliases = {a for a in aliases if a}
            alias_canon = {re.sub(r"[^a-z0-9]+", "", a) for a in aliases}
            if any(lowered in alias for alias in aliases) or (canonical and canonical in alias_canon):
                out.append(member)
        return out[:limit]

    async def fetch_members(self, limit=None):
        max_items = len(self.members) if limit is None else min(len(self.members), int(limit))
        for member in self.members[:max_items]:
            yield member


class _DummyCharacter:
    def __init__(self):
        self.persona = "Cecile is Dottore's experiment. Seele is Cecile's creator."
        self.example_dialogue = ""
        self.special_users = {
            "seelewee": "Seele is Cecile's creator and main game developer.",
            "geechan": "Geechan is Seele's boyfriend.",
        }


class _DummyClient:
    class _User:
        id = 999999999999999999

    user = _User()


class _DummyBot:
    def __init__(self):
        self.character = _DummyCharacter()
        self.client = _DummyClient()


@unittest.skipIf(BotInstance is None, "bot_instance dependencies unavailable in test env")
class BotInstanceRelationMentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_creator_relation_prefers_special_user_human_target(self):
        seele_id = 111111111111111111
        dottore_id = 222222222222222222
        seele = FakeMember(seele_id, "seelewee", "Seele WaWa", bot=False)
        dottore = FakeMember(dottore_id, "dottore", "Dottore", bot=True)
        guild = FakeGuild(members=[seele, dottore])

        context = {
            "content": "cecile, can you tag your creator?",
            "context_envelope": {
                "mention_candidates": [
                    {
                        "user_id": seele_id,
                        "aliases": ["seelewee", "Seele", "Seele WaWa"],
                        "handle": "@u_111111111111111111",
                        "priority": "user",
                    },
                    {
                        "user_id": dottore_id,
                        "aliases": ["dottore", "Dottore"],
                        "handle": "@b_222222222222222222",
                        "priority": "bot",
                    },
                ]
            },
        }

        dummy = _DummyBot()
        original_cache_lookup = bot_instance_module.get_cached_mention_alias_entries
        bot_instance_module.get_cached_mention_alias_entries = lambda *_args, **_kwargs: []
        try:
            result = await BotInstance._inject_requested_mentions_failsafe(
                dummy,
                "<@222222222222222222> Fine.",
                context,
                guild,
            )
        finally:
            bot_instance_module.get_cached_mention_alias_entries = original_cache_lookup

        self.assertIn("<@111111111111111111>", result)
        self.assertNotIn("<@222222222222222222>", result)

    async def test_explicit_bot_target_still_allowed_even_with_creator_terms(self):
        dottore_id = 222222222222222222
        dottore = FakeMember(dottore_id, "dottore", "Dottore", bot=True)
        guild = FakeGuild(members=[dottore])

        context = {
            "content": "cecile, tag @dottore not your creator",
            "context_envelope": {
                "mention_candidates": [
                    {
                        "user_id": dottore_id,
                        "aliases": ["dottore", "Dottore"],
                        "handle": "@b_222222222222222222",
                        "priority": "bot",
                    },
                ]
            },
        }

        dummy = _DummyBot()
        original_cache_lookup = bot_instance_module.get_cached_mention_alias_entries
        bot_instance_module.get_cached_mention_alias_entries = lambda *_args, **_kwargs: []
        try:
            result = await BotInstance._inject_requested_mentions_failsafe(
                dummy,
                "Sure.",
                context,
                guild,
            )
        finally:
            bot_instance_module.get_cached_mention_alias_entries = original_cache_lookup

        self.assertIn("<@222222222222222222>", result)


if __name__ == "__main__":
    unittest.main()
