import re
import unittest

from mention_resolver import resolve_mentions_unified


class FakeMember:
    def __init__(self, user_id, name, display_name=None, *, bot=False, nick=None, global_name=None):
        self.id = user_id
        self.name = name
        self.display_name = display_name or name
        self.bot = bot
        self.nick = nick
        self.global_name = global_name


class FakeGuild:
    def __init__(self, guild_id=1, members=None, query_pool=None):
        self.id = guild_id
        self.members = list(members or [])
        self._query_pool = list(query_pool or [])

    async def query_members(self, query: str, limit: int = 100, cache: bool = True):
        lowered = query.lower().strip()
        canonical = re.sub(r"[^a-z0-9]+", "", lowered)
        out = []
        for member in self._query_pool:
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


class MentionResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_off_context_user_via_query(self):
        target = FakeMember(100100100100100100, "febs_wawa", "Febs WaWa", bot=False)
        guild = FakeGuild(members=[], query_pool=[target])

        result = await resolve_mentions_unified(
            response="Sure @febs, on it.",
            request_content="cecile, can you tag febs",
            context_envelope={},
            guild=guild,
            include_bots=True,
            ambiguity_policy="best_match",
            min_score=4.0,
        )

        self.assertIn("<@100100100100100100>", result.text)
        self.assertNotIn("@febs", result.text.lower())

    async def test_resolves_bot_plaintext_mention(self):
        bot_member = FakeMember(200200200200200200, "starlord", "Star-Lord", bot=True)
        guild = FakeGuild(members=[bot_member], query_pool=[bot_member])

        result = await resolve_mentions_unified(
            response="And @starlord should handle it.",
            request_content="tag starlord",
            context_envelope={},
            guild=guild,
            include_bots=True,
            ambiguity_policy="best_match",
            min_score=4.0,
        )

        self.assertIn("<@200200200200200200>", result.text)
        self.assertNotIn("@starlord", result.text.lower())

    async def test_best_match_policy_is_deterministic(self):
        alex_a = FakeMember(500500500500500500, "alex", "Alex", bot=False)
        alex_b = FakeMember(700700700700700700, "alex2", "Alex", bot=False)
        guild = FakeGuild(members=[alex_a, alex_b], query_pool=[alex_a, alex_b])

        result = await resolve_mentions_unified(
            response="ok",
            request_content="tag alex",
            context_envelope={},
            guild=guild,
            include_bots=True,
            ambiguity_policy="best_match",
            min_score=4.0,
        )

        # Tie-break should be stable by lower user_id.
        self.assertIn("<@500500500500500500>", result.text)

    async def test_cleans_dangling_markers(self):
        result = await resolve_mentions_unified(
            response="Hello @@ there <@ and @ >",
            request_content="",
            context_envelope={},
            guild=None,
            include_bots=True,
        )

        self.assertNotIn("<@", result.text)
        self.assertNotIn("@@", result.text)
        self.assertNotIn("@ >", result.text)


if __name__ == "__main__":
    unittest.main()
