"""Run activity delivery contracts with native Discord classes in a fresh process."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def test_native_discord_activity_contracts():
    with tempfile.TemporaryDirectory(prefix="pals-activity-tests-") as directory:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--native"],
            cwd=directory, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    assert result.returncode == 0, result.stdout + result.stderr


def _native_suite():
    import asyncio
    from copy import deepcopy
    from types import SimpleNamespace
    import unittest
    from unittest.mock import AsyncMock, MagicMock, Mock, patch

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import discord
    from project_automation import WorkflowError
    from project_automation_discord import DiscordTransport, receipt_key
    from project_automation_discord_activity import activity_embed, activity_marker
    from project_automation_store import AutomationStore

    async def iterate(items):
        for item in items:
            yield item

    class ActivityTests(unittest.IsolatedAsyncioTestCase):
        async def asyncSetUp(self):
            self.directory = tempfile.TemporaryDirectory()
            self.path = Path(self.directory.name) / "activity.sqlite3"
            self.store = AutomationStore(self.path)
            self.config = {
                "bot_name": "Project Helper", "guild_id": "100", "reviews_channel_id": "202",
                "repository": "example/project", "issues_channel_id": "201",
                "commits_channel_id": "203", "github_channel_id": "204",
            }
            self.channels, self.messages = {}, {}
            self.next_thread, self.next_message = 300, 400
            self.forum = self.forum_channel(202)
            self.client = SimpleNamespace(
                user=SimpleNamespace(id=777), get_channel=lambda identifier: self.channels.get(identifier),
                fetch_channel=AsyncMock(side_effect=lambda identifier: self.channels[identifier]),
            )
            self.service = SimpleNamespace(
                store=self.store, settings=lambda: self.config,
                bots={"Project Helper": SimpleNamespace(client=self.client)}, say=AsyncMock(),
            )
            self.transport = DiscordTransport(self.service)
            self.access = patch("response_access.message_access", return_value=(True, None))
            self.access.start()

        async def asyncTearDown(self):
            self.access.stop()
            self.directory.cleanup()

        def forum_channel(self, identifier):
            forum = MagicMock(spec=discord.ForumChannel)
            forum.id, forum.guild, forum.parent_id = identifier, SimpleNamespace(id=100), None
            forum.threads = []
            forum.archived_threads = Mock(side_effect=lambda **kwargs: iterate([]))

            async def create(**kwargs):
                self.next_thread += 1
                thread = self.thread(self.next_thread, identifier)
                thread.name = kwargs["name"]
                starter = self.message(thread, thread.id, kwargs["embed"])
                forum.threads.append(thread)
                return SimpleNamespace(thread=thread, message=starter)

            forum.create_thread = AsyncMock(side_effect=create)
            self.channels[identifier] = forum
            return forum

        def thread(self, identifier, parent_id):
            thread = MagicMock(spec=discord.Thread)
            thread.id, thread.parent_id, thread.guild = identifier, parent_id, SimpleNamespace(id=100)
            thread.archived = thread.locked = False
            thread.name = "PR"
            self.messages[identifier] = {}

            async def edit(**kwargs):
                for key, value in kwargs.items():
                    setattr(thread, key, value)
                return thread

            async def fetch(message_id):
                if message_id not in self.messages[identifier]:
                    raise discord.NotFound(SimpleNamespace(status=404, reason="Not found"), "missing")
                return self.messages[identifier][message_id]

            async def send(**kwargs):
                self.next_message += 1
                return self.message(thread, self.next_message, kwargs["embed"])

            thread.edit = AsyncMock(side_effect=edit)
            thread.fetch_message = AsyncMock(side_effect=fetch)
            thread.send = AsyncMock(side_effect=send)
            thread.history = Mock(side_effect=lambda **kwargs: iterate(list(self.messages[identifier].values())[::-1]))
            self.channels[identifier] = thread
            return thread

        def message(self, thread, identifier, embed, *, author=777):
            message = MagicMock(spec=discord.Message)
            message.id, message.channel = identifier, thread
            message.author = SimpleNamespace(id=author, bot=True)
            message.embeds = [embed]

            async def edit(**kwargs):
                message.embeds = [kwargs["embed"]]
                return message

            message.edit = AsyncMock(side_effect=edit)
            self.messages[thread.id][identifier] = message
            return message

        def event(self, kind="comment", **updates):
            return {
                "kind": "pr_activity", "activity_type": kind, "number": 17,
                "source_id": "42", "source_version": "version-1", "key": f"pr:17:{kind}:42",
                "repository": "example/project", "action": "created", "author": "reviewer",
                "author_type": "User", "author_id": 19, "body": "Exact **source** text.",
                "body_truncated": False, "created_at": "2026-09-26T01:02:03Z",
                "updated_at": "2026-09-26T01:02:03Z",
                "url": "https://github.com/example/project/pull/17#issuecomment-42",
                "html_url": "https://github.com/example/project/pull/17#issuecomment-42",
                "pull": {"kind": "pull", "number": 17, "title": "A pull request", "body": "PR description",
                         "state": "open", "closed": False, "draft": False, "merged": False,
                         "html_url": "https://github.com/example/project/pull/17"},
                **updates,
            }

        def delivered(self, event):
            link = self.store.get_link("pr_activity", receipt_key(self.config, event))
            return self.channels[int(link["channel_id"])], self.messages[int(link["channel_id"])][int(link["message_id"])], link

        async def test_activity_types_preserve_source_metadata_without_an_llm(self):
            for kind in ("comment", "review_comment", "review", "thread", "commits"):
                event = self.event(kind, author="review[bot]", author_type="Bot", state="approved" if kind == "review" else "resolved",
                                   path="module.py", start_line=4, line=8, reply_to_id=21, resolved_by="maintainer")
                if kind == "commits":
                    event.update(before="a" * 40, after="b" * 40, head_ref="topic", head_repository="contributor/fork",
                                 compare_url="https://github.com/example/project/compare/aaa...bbb",
                                 commits=[{"sha": "b" * 40, "message": "Preserve source commit text", "url": "https://github.com/example/project/commit/" + "b" * 40}])
                self.assertTrue(await self.transport.mirror_activity(event))
                thread, message, link = self.delivered(event)
                embed = message.embeds[0]
                self.assertIn("(bot)", embed.fields[0].value)
                self.assertIn("2026-09-26T01:02:03Z", str(embed.to_dict()))
                self.assertIn("module.py", str(embed.to_dict()))
                self.assertIn("Line 4–8", str(embed.to_dict()))
                if kind != "thread":
                    self.assertIn("#discussion_r21", str(embed.to_dict()))
                    self.assertIn(event["body"], embed.description)
                else:
                    self.assertNotIn(event["body"], embed.description)
                    self.assertIn("Review discussion resolved.", embed.description)
                self.assertEqual(link["source_version"], "version-1")
                self.assertEqual(thread.send.await_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})
            self.forum.create_thread.assert_awaited_once()
            self.service.say.assert_not_awaited()

        async def test_replay_edits_and_deletion_use_one_source_message(self):
            event = self.event()
            await self.transport.mirror_activity(event)
            thread, message, _ = self.delivered(event)
            await self.transport.mirror_activity(event)
            message.edit.assert_not_awaited()
            event.update(source_version="version-2", action="edited", body="Edited source text.")
            await self.transport.mirror_activity(event)
            self.assertIn("Edited", message.embeds[0].title)
            self.assertIn("Edited source text.", message.embeds[0].description)
            event.update(source_version="version-3", action="deleted")
            await self.transport.mirror_activity(event)
            self.assertIn("Deleted", message.embeds[0].title)
            self.assertIn("Deleted on GitHub.", message.embeds[0].description)
            self.assertNotIn("Edited source text", message.embeds[0].description)
            thread.send.assert_awaited_once()
            self.assertEqual(message.edit.await_count, 2)
            self.assertEqual(message.edit.await_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})
            self.assertEqual(self.delivered(event)[2]["source_version"], "version-3")

        async def test_reply_links_to_verified_mirrored_parent(self):
            parent = self.event("review_comment")
            await self.transport.mirror_activity(parent)
            thread, _, receipt = self.delivered(parent)
            reply = self.event("review_comment", key="pr:17:review_comment:43", source_id="43", reply_to_id=42)
            await self.transport.mirror_activity(reply)
            _, message, _ = self.delivered(reply)
            self.assertIn(receipt["jump_url"], str(message.embeds[0].to_dict()))
            self.assertIn("#discussion_r42", str(message.embeds[0].to_dict()))
            self.assertEqual(thread.send.await_count, 2)

        async def test_reply_does_not_link_to_another_discord_author(self):
            parent = self.event("review_comment")
            await self.transport.mirror_activity(parent)
            _, message, receipt = self.delivered(parent)
            message.author.id = 999
            reply = self.event("review_comment", key="pr:17:review_comment:43", source_id="43", reply_to_id=42)
            await self.transport.mirror_activity(reply)
            _, mirrored, _ = self.delivered(reply)
            self.assertNotIn(receipt["jump_url"], str(mirrored.embeds[0].to_dict()))
            self.assertIn("#discussion_r42", str(mirrored.embeds[0].to_dict()))

        async def test_resolution_updates_one_state_message_without_copying_discussion(self):
            parent = self.event("review_comment")
            await self.transport.mirror_activity(parent)
            thread, _, receipt = self.delivered(parent)
            event = self.event("thread", state="resolved", comment_id=42)
            await self.transport.mirror_activity(event)
            _, message, _ = self.delivered(event)
            self.assertNotIn(parent["body"], message.embeds[0].description)
            self.assertIn(receipt["jump_url"], str(message.embeds[0].to_dict()))
            await self.transport.mirror_activity({**event, "state": "unresolved", "source_version": "version-2"})
            self.assertIn("reopened", message.embeds[0].description)
            self.assertEqual(thread.send.await_count, 2)
            message.edit.assert_awaited_once()

        async def test_long_valid_source_identity_fits_durable_receipt_keys(self):
            self.config["bot_name"] = "Helper" * 21
            self.service.bots[self.config["bot_name"]] = next(iter(self.service.bots.values()))
            self.config["repository"] = "a" * 39 + "/" + "b" * 100
            event = self.event("thread", source_id="t" * 200, key="pr:17:thread:" + "t" * 200,
                               repository=self.config["repository"], state="resolved", source_version="v" * 128)
            await self.transport.mirror_activity(event)
            self.assertLessEqual(len(receipt_key(self.config, event) + ":" + event["source_version"]), 512)
            self.assertTrue(self.delivered(event)[2]["message_id"])

        async def test_older_receipt_never_certifies_a_newer_edit(self):
            event = self.event()
            await self.transport.mirror_activity(event)
            thread, message, _ = self.delivered(event)
            starter = self.messages[thread.id][thread.id]
            starter.edit.reset_mock()
            thread.edit.reset_mock()
            newer = {**event, "source_version": "version-2", "body": "A later edit", "action": "edited"}
            self.assertFalse(await self.transport.mirror_activity(newer, recover_only=True))
            self.assertEqual(self.delivered(event)[2]["source_version"], "version-1")
            self.assertIsNone(self.store.get_link("pr_activity_delivery", receipt_key(self.config, event) + ":version-2"))
            message.edit.assert_not_awaited()
            starter.edit.assert_not_awaited()
            thread.edit.assert_not_awaited()
            thread.send.assert_awaited_once()

        async def test_recovering_applied_old_version_preserves_latest_receipt(self):
            first = self.event()
            await self.transport.mirror_activity(first)
            second = {**first, "source_version": "version-2", "body": "Current source", "action": "edited"}
            await self.transport.mirror_activity(second)
            self.assertTrue(await self.transport.mirror_activity(first, recover_only=True))
            self.assertEqual(self.delivered(first)[2]["source_version"], "version-2")

        async def test_lost_activity_ack_is_recovered_after_restart_without_sending(self):
            event = self.event()
            await self.transport.mirror({**event["pull"], "key": "pull:17"})
            thread = self.forum.threads[0]
            original_send = thread.send.side_effect

            async def lost_ack(**kwargs):
                await original_send(**kwargs)
                raise TimeoutError("Acknowledgement lost")

            thread.send.side_effect = lost_ack
            with self.assertRaises(TimeoutError):
                await self.transport.mirror_activity(event)
            self.service.store = self.store = AutomationStore(self.path)
            self.transport = DiscordTransport(self.service)
            self.assertTrue(await self.transport.mirror_activity(event, recover_only=True))
            self.assertEqual(self.delivered(event)[2]["source_version"], "version-1")
            thread.send.assert_awaited_once()
            self.forum.create_thread.assert_awaited_once()

        async def test_parent_ack_is_not_activity_and_parent_is_not_duplicated(self):
            event = self.event()
            original_create = self.forum.create_thread.side_effect

            async def lost_ack(**kwargs):
                await original_create(**kwargs)
                raise TimeoutError("Parent acknowledgement lost")

            self.forum.create_thread.side_effect = lost_ack
            with self.assertRaises(TimeoutError):
                await self.transport.mirror_activity(event)
            thread = self.forum.threads[0]
            self.assertFalse(await self.transport.mirror_activity(event, recover_only=True))
            thread.send.assert_not_awaited()
            self.assertTrue(await self.transport.mirror_activity(event))
            self.forum.create_thread.assert_awaited_once()
            thread.send.assert_awaited_once()

        async def test_unseen_parent_attempt_requires_explicit_retry_confirmation(self):
            event = self.event()
            original_create = self.forum.create_thread.side_effect
            self.forum.create_thread.side_effect = TimeoutError("No receipt")
            with self.assertRaises(TimeoutError):
                await self.transport.mirror_activity(event)
            self.service.store = self.store = AutomationStore(self.path)
            self.assertFalse(await self.transport.mirror_activity(event, recover_only=True))
            with self.assertRaisesRegex(WorkflowError, "needs recovery"):
                await self.transport.mirror_activity({**event, "key": "pr:17:comment:43"})
            self.forum.create_thread.assert_awaited_once()
            self.transport.confirm_not_delivered(event)
            self.forum.create_thread.side_effect = original_create
            self.assertTrue(await self.transport.mirror_activity(event))
            self.assertEqual(self.forum.create_thread.await_count, 2)

        async def test_unseen_activity_attempt_blocks_other_versions_until_confirmed(self):
            event = self.event()
            await self.transport.mirror({**event["pull"], "key": "pull:17"})
            thread = self.forum.threads[0]
            original_send = thread.send.side_effect
            thread.send.side_effect = TimeoutError("No receipt")
            with self.assertRaises(TimeoutError):
                await self.transport.mirror_activity(event)
            newer = {**event, "source_version": "version-2"}
            with self.assertRaisesRegex(WorkflowError, "needs recovery"):
                await self.transport.mirror_activity(newer)
            self.assertFalse(await self.transport.mirror_activity(newer, recover_only=True))
            thread.send.assert_awaited_once()
            self.transport.confirm_not_delivered(event)
            thread.send.side_effect = original_send
            await self.transport.mirror_activity(newer)
            self.assertEqual(thread.send.await_count, 2)

        async def test_copied_marker_from_another_author_is_not_recovered(self):
            event = self.event()
            await self.transport.mirror({**event["pull"], "key": "pull:17"})
            thread = self.forum.threads[0]
            self.message(thread, 900, activity_embed(event, activity_marker(self.config, event)), author=999)
            self.assertFalse(await self.transport.mirror_activity(event, recover_only=True))
            self.assertIsNone(self.store.get_link("pr_activity", receipt_key(self.config, event)))
            thread.send.assert_not_awaited()

        async def test_saved_message_from_another_author_cannot_be_edited(self):
            event = self.event()
            await self.transport.mirror_activity(event)
            thread, message, _ = self.delivered(event)
            message.author.id = 999
            with self.assertRaisesRegex(WorkflowError, "different Discord author"):
                await self.transport.mirror_activity({**event, "source_version": "version-2"})
            message.edit.assert_not_awaited()
            thread.send.assert_awaited_once()

        async def test_changed_destination_has_a_separate_receipt_and_old_binding_is_rejected(self):
            event = self.event()
            event["_binding"] = deepcopy(self.config)
            await self.transport.mirror_activity(event)
            old_thread, old_message, _ = self.delivered(event)
            old_starter = self.messages[old_thread.id][old_thread.id]
            self.config["reviews_channel_id"] = "205"
            new_forum = self.forum_channel(205)
            with self.assertRaisesRegex(WorkflowError, "earlier Discord configuration"):
                await self.transport.mirror_activity(event)
            event["_binding"] = deepcopy(self.config)
            await self.transport.mirror_activity(event)
            new_forum.create_thread.assert_awaited_once()
            old_message.edit.assert_not_awaited()
            old_starter.edit.assert_not_awaited()
            self.assertNotEqual(self.delivered(event)[0].id, old_thread.id)

        async def test_wrong_forum_parent_is_rejected(self):
            event = self.event()
            await self.transport.mirror({**event["pull"], "key": "pull:17"})
            thread = self.forum.threads[0]
            thread.parent_id = 999
            with self.assertRaisesRegex(WorkflowError, "different forum"):
                await self.transport.mirror_activity(event)
            thread.send.assert_not_awaited()

        async def test_closed_pr_send_failure_restores_archived_and_locked(self):
            event = self.event(state="open", closed=False)
            event["pull"].update(state="closed", closed=True, merged=True)
            await self.transport.mirror({**event["pull"], "key": "pull:17"})
            thread = self.forum.threads[0]
            thread.send.side_effect = TimeoutError("Send failed")
            with self.assertRaises(TimeoutError):
                await self.transport.mirror_activity(event)
            self.assertTrue(thread.archived)
            self.assertTrue(thread.locked)
            self.assertEqual(thread.edit.await_args.kwargs, {"archived": True, "locked": True})

        async def test_closed_parent_edit_failure_and_cancellation_restore_state(self):
            event = self.event()
            event["pull"].update(state="closed", closed=True)
            await self.transport.mirror({**event["pull"], "key": "pull:17"})
            thread = self.forum.threads[0]
            starter = self.messages[thread.id][thread.id]
            starter.edit.side_effect = TimeoutError("Parent edit failed")
            with self.assertRaises(TimeoutError):
                await self.transport.mirror_activity(event)
            self.assertTrue(thread.archived and thread.locked)
            starter.edit.side_effect = None
            thread.send.side_effect = asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await self.transport.mirror_activity(event)
            self.assertTrue(thread.archived and thread.locked)

        async def test_lost_edit_ack_recovers_the_exact_new_version(self):
            event = self.event()
            await self.transport.mirror_activity(event)
            thread, message, _ = self.delivered(event)
            original_edit = message.edit.side_effect

            async def lost_ack(**kwargs):
                await original_edit(**kwargs)
                raise TimeoutError("Edit acknowledgement lost")

            message.edit.side_effect = lost_ack
            newer = {**event, "source_version": "version-2", "action": "edited", "body": "Latest text"}
            with self.assertRaises(TimeoutError):
                await self.transport.mirror_activity(newer)
            self.assertTrue(await self.transport.mirror_activity(newer, recover_only=True))
            receipt = self.store.get_link("pr_activity_delivery", receipt_key(self.config, event) + ":version-2")
            self.assertEqual(receipt["message_id"], str(message.id))
            thread.send.assert_awaited_once()
            message.edit.assert_awaited_once()

        async def test_recovery_cannot_create_a_missing_parent(self):
            self.assertFalse(await self.transport.mirror_activity(self.event(), recover_only=True))
            self.forum.create_thread.assert_not_awaited()

        async def test_excerpt_is_labelled_linked_and_within_discord_limits(self):
            event = self.event(body="@everyone <@123> <#456> " + "x" * 65536)
            embed = activity_embed(event, activity_marker(self.config, event))
            self.assertIn("Excerpt", embed.description)
            self.assertIn(event["url"], embed.description)
            self.assertIn("@\u200beveryone", embed.description)
            self.assertNotIn("<#456>", embed.description)
            self.assertLessEqual(len(embed.description), 4096)
            self.assertLessEqual(len(embed), 6000)
            event.update(body="Available beginning", body_truncated=True)
            self.assertIn("Excerpt", activity_embed(event, activity_marker(self.config, event)).description)

        async def test_long_source_link_and_metadata_leave_room_for_the_excerpt(self):
            event = self.event(body="x" * 10000, path="p" * 1000, resolved_by="a" * 100,
                               reply_to_id=42, html_url="https://github.com/example/project/pull/17#" + "x" * 1900)
            embed = activity_embed(event, activity_marker(self.config, event))
            self.assertIn(event["html_url"], embed.description)
            self.assertLessEqual(len(embed.description), 4096)
            self.assertLessEqual(len(embed), 6000)

        async def test_review_edits_keep_the_review_state_and_edit_label(self):
            event = self.event("review", state="changes_requested", action="edited")
            embed = activity_embed(event, activity_marker(self.config, event))
            self.assertIn("Changes requested", embed.title)
            self.assertIn("Edited", embed.title)

        async def test_commit_reconciliation_does_not_invent_a_prior_head_or_count(self):
            event = self.event("commits", before="", after="a" * 40, commits=[])
            embed = activity_embed(event, activity_marker(self.config, event))
            self.assertIn("Head: `aaaaaaaaaaaa`", embed.description)
            self.assertNotIn("→", embed.description)
            self.assertFalse(any(field.name == "Commit count" for field in embed.fields))

    return unittest.defaultTestLoader.loadTestsFromTestCase(ActivityTests)


if __name__ == "__main__":
    import unittest
    result = unittest.TextTestRunner(verbosity=2).run(_native_suite())
    raise SystemExit(0 if result.wasSuccessful() else 1)
