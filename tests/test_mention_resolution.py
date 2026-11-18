import types
import unittest

import module_stubs  # noqa: F401
import discord_utils as discord_utils_module


def member(user_id, display_name, global_name=None, name=None, bot=False):
    return types.SimpleNamespace(
        id=user_id,
        display_name=display_name,
        global_name=global_name,
        name=name or display_name.lower(),
        bot=bot,
    )


class FakeGuild:
    """A guild double that counts how often the full member list is read."""

    def __init__(self, members):
        self._members = {item.id: item for item in members}
        self.member_list_reads = 0

    @property
    def members(self):
        self.member_list_reads += 1
        return list(self._members.values())

    def get_member(self, user_id):
        return self._members.get(user_id)


USERS = [
    {"name": "Alice Bloom", "user_id": 1, "mention_syntax": "<@1>",
     "aliases": ["Alice Bloom", "Alice", "alice_dev"], "priority": 1},
    {"name": "Ann", "user_id": 2, "mention_syntax": "<@2>", "aliases": ["Ann"], "priority": 2},
    {"name": "Ann Marie", "user_id": 3, "mention_syntax": "<@3>", "aliases": ["Ann Marie"], "priority": 2},
    {"name": "Mr. Smith", "user_id": 4, "mention_syntax": "<@4>", "aliases": ["Mr. Smith"], "priority": 2},
    {"name": "Zoë", "user_id": 5, "mention_syntax": "<@5>", "aliases": ["Zoë"], "priority": 2},
]


class OutgoingMentionCharacterizationTests(unittest.TestCase):
    """Replies that resolved before the lookup rewrite must resolve the same way after it."""

    def assertResolves(self, reply, expected, users=USERS, bots=None):
        self.assertEqual(discord_utils_module.process_outgoing_mentions(reply, users, bots), expected)

    def test_names_resolve_by_any_alias_regardless_of_case(self):
        self.assertResolves("@Alice hi", "<@1> hi")
        self.assertResolves("@alice_dev hi", "<@1> hi")
        self.assertResolves("@ALICE BLOOM, welcome", "<@1>, welcome")
        self.assertResolves("@Alice and @alice", "<@1> and <@1>")
        self.assertResolves("@zoë! @ZOË", "<@5>! <@5>")

    def test_a_multi_word_name_allows_any_whitespace_between_its_words(self):
        self.assertResolves("@Alice   Bloom hi", "<@1> hi")
        self.assertResolves("@Ann\nMarie", "<@3>")
        self.assertResolves("thanks @Ann Marie", "thanks <@3>")

    def test_the_longest_matching_name_wins(self):
        self.assertResolves("@Ann Marie and @Ann", "<@3> and <@2>")

    def test_a_name_must_end_at_a_non_word_character(self):
        self.assertResolves("@Annie hi", "@Annie hi")
        self.assertResolves("@Ann_x hi", "@Ann_x hi")
        self.assertResolves("@Ann's turn", "<@2>'s turn")
        self.assertResolves("bye @Ann.", "bye <@2>.")

    def test_names_with_regex_characters_resolve_literally(self):
        self.assertResolves("@Mr. Smith says hi", "<@4> says hi")
        self.assertResolves("@Mr Smith says hi", "@Mr Smith says hi")

    def test_a_name_written_in_mention_brackets_resolves(self):
        self.assertResolves("<@Alice> hi", "<@1> hi")
        self.assertResolves("<@!Ann Marie> hi", "<@3> hi")

    def test_an_unknown_name_stays_text(self):
        self.assertResolves("@Bob hi", "@Bob hi")

    def test_raw_mentions_the_reply_did_not_resolve_are_removed(self):
        self.assertResolves("hi <@999> there", "hi  there")
        self.assertResolves("@Alice meet <@1>", "<@1> meet <@1>")
        self.assertResolves("see <#123> now", "see  now")

    def test_without_candidates_the_reply_is_unchanged(self):
        self.assertResolves("@Alice <@999> <#1>", "@Alice <@999> <#1>", users=[])

    def test_other_bots_resolve_unless_a_user_has_the_same_name(self):
        nilou_bot = [{"character_name": "Nilou", "mention_syntax": "<@77>", "user_id": 77}]
        self.assertResolves("@Nilou dance", "<@77> dance", bots=nilou_bot)
        user_nilou = USERS + [{"name": "Nilou", "user_id": 8, "mention_syntax": "<@8>", "aliases": ["Nilou"], "priority": 2}]
        self.assertResolves("@Nilou dance", "<@8> dance", users=user_nilou, bots=nilou_bot)


class MentionCandidateCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self._history = discord_utils_module.conversation_history
        discord_utils_module.conversation_history = {77: [
            {"role": "user", "content": "first", "author": "Old Timer", "user_id": 11, "is_bot": False},
            {"role": "assistant", "content": "reply", "author": "Nahida"},
            {"role": "user", "content": "beep", "author": "Botty", "user_id": 12, "is_bot": True},
            {"role": "user", "content": "hello", "author": "Alice Bloom", "user_id": 1, "is_bot": False},
        ]}
        self.yara = member(21, "Yara")
        self.replied = member(30, "Replied Person", name="replied")
        self.guild = FakeGuild([
            member(1, "Alice Bloom", "Alice", "alice_dev"),
            member(12, "Botty", bot=True),
            member(20, "Zed"),
            self.yara,
            member(11, "Old Timer", name="oldtimer"),
            member(22, "Xeno"),
        ])

    def tearDown(self):
        discord_utils_module.conversation_history = self._history

    def _candidates(self, limit):
        users = discord_utils_module.get_mentionable_users(
            77, limit=limit, guild=self.guild, explicit_users=[self.yara], reply_target=self.replied
        )
        return [(user["user_id"], user["name"], user["priority"], user["aliases"]) for user in users]

    EXPECTED = [
        (30, "Replied Person", 0, ["Replied Person", "replied"]),
        (21, "Yara", 0, ["Yara", "yara"]),
        (1, "Alice Bloom", 1, ["Alice Bloom", "Alice", "alice_dev"]),
        (11, "Old Timer", 1, ["Old Timer", "oldtimer"]),
        (20, "Zed", 2, ["Zed", "zed"]),
        (22, "Xeno", 2, ["Xeno", "xeno"]),
    ]

    def test_candidates_come_by_reply_target_and_mentions_then_recent_speakers_then_members(self):
        self.assertEqual(self._candidates(None), self.EXPECTED)

    def test_a_limit_keeps_the_same_first_candidates(self):
        self.assertEqual(self._candidates(4), self.EXPECTED[:4])
        self.assertEqual(self._candidates(5), self.EXPECTED[:5])

    def test_a_reply_resolves_against_the_candidates_of_its_channel(self):
        users = discord_utils_module.get_mentionable_users(
            77, limit=None, guild=self.guild, explicit_users=[self.yara], reply_target=self.replied
        )
        reply = "@Alice and @oldtimer, meet @Xeno. @Nobody stays text <#5>"

        self.assertEqual(
            discord_utils_module.process_outgoing_mentions(reply, users, None),
            "<@1> and <@11>, meet <@22>. @Nobody stays text ",
        )


if __name__ == "__main__":
    unittest.main()
