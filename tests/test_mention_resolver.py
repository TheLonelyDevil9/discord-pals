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
    def __init__(self, guild_id=1, members=None, query_pool=None, fetch_pool=None):
        self.id = guild_id
        self.members = list(members or [])
        self._query_pool = list(query_pool or [])
        self._fetch_pool = list(fetch_pool or [])

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

    async def fetch_members(self, limit=None):
        max_items = len(self._fetch_pool) if limit is None else min(len(self._fetch_pool), int(limit))
        for member in self._fetch_pool[:max_items]:
            yield member


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

    async def test_fetch_members_fallback_resolves_offline_user(self):
        offline_target = FakeMember(888888888888888888, "seele", "Seele", bot=False)
        guild = FakeGuild(members=[], query_pool=[], fetch_pool=[offline_target])

        result = await resolve_mentions_unified(
            response="Sure @seele, done.",
            request_content="tag seele",
            context_envelope={},
            guild=guild,
            include_bots=True,
            ambiguity_policy="best_match",
            min_score=4.0,
        )

        self.assertIn("<@888888888888888888>", result.text)
        self.assertNotIn("@seele", result.text.lower())

    async def test_unresolved_plain_mentions_are_demoted(self):
        result = await resolve_mentions_unified(
            response="@hopefully that works for you!",
            request_content="tag evoc",
            context_envelope={},
            guild=None,
            include_bots=True,
        )

        self.assertIn("hopefully that works for you!", result.text.lower())
        self.assertNotIn("@hopefully", result.text.lower())

    async def test_drops_conversational_plain_mentions(self):
        result = await resolve_mentions_unified(
            response="@yo @got someone looking for you 👀 and @heads up",
            request_content="starlord can you tag febs",
            context_envelope={},
            guild=None,
            include_bots=True,
        )

        self.assertNotIn("@yo", result.text.lower())
        self.assertNotIn("@got", result.text.lower())
        self.assertNotIn("@heads", result.text.lower())

    async def test_ignores_leading_reply_mention_for_target_extraction(self):
        febs = FakeMember(300300300300300300, "febs_wawa", "Febs WaWa", bot=False)
        kris = FakeMember(400400400400400400, "kriswawa", "Kris WaWa", bot=False)
        guild = FakeGuild(members=[febs, kris], query_pool=[febs, kris])

        result = await resolve_mentions_unified(
            response="Sure, on it.",
            request_content="@Kris WaWa collei, can you tag febs?",
            context_envelope={},
            guild=guild,
            include_bots=True,
            ambiguity_policy="best_match",
            min_score=4.0,
        )

        self.assertIn("<@300300300300300300>", result.text)
        self.assertNotIn("<@400400400400400400>", result.text)

    async def test_explicit_tag_intent_keeps_only_requested_target_mentions(self):
        febs = FakeMember(710710710710710710, "febs", "Febs", bot=False)
        wraith = FakeMember(720720720720720720, "wraith", "Wraith", bot=False)
        guild = FakeGuild(members=[febs, wraith], query_pool=[febs, wraith])

        result = await resolve_mentions_unified(
            response="@wraith, Kris wants you before bed.",
            request_content="max, tag febs for me?",
            context_envelope={},
            guild=guild,
            include_bots=True,
            ambiguity_policy="best_match",
            min_score=4.0,
        )

        self.assertIn("<@710710710710710710>", result.text)
        self.assertNotIn("<@720720720720720720>", result.text)
        self.assertNotIn("@wraith", result.text.lower())

    async def test_explicit_tag_intent_drops_non_target_mentions_when_target_unresolved(self):
        wraith = FakeMember(730730730730730730, "wraith", "Wraith", bot=False)
        guild = FakeGuild(members=[wraith], query_pool=[wraith])

        result = await resolve_mentions_unified(
            response="@wraith, okay.",
            request_content="starlord, tag febs?",
            context_envelope={},
            guild=guild,
            include_bots=True,
            ambiguity_policy="best_match",
            min_score=4.0,
        )

        self.assertNotIn("<@730730730730730730>", result.text)
        self.assertNotIn("@wraith", result.text.lower())


if __name__ == "__main__":
    unittest.main()
