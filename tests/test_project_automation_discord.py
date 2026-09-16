"""Exercise the native discord.py contract without the suite's legacy SDK stubs.

The regular suite installs module_stubs globally. A fresh test interpreter keeps
those stubs from replacing real Views, app commands, Embed and channel types.
Only Discord HTTP boundaries are mocked; no network connection is made.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def test_native_discord_transport_contracts():
    with tempfile.TemporaryDirectory(prefix="pals-discord-tests-") as directory:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--native"],
            cwd=directory, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    assert result.returncode == 0, result.stdout + result.stderr


def _native_suite():
    import asyncio
    import unittest
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, Mock, patch

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import discord
    from discord import app_commands
    from project_automation import WorkflowError
    from project_automation_discord import DecisionView, DiscordTransport, HumanReportModal, NO_MENTIONS, message_text
    from project_automation_store import Conflict

    async def iterate(items):
        for item in items:
            yield item

    def channel(kind=discord.Thread, channel_id=102, **attributes):
        value = MagicMock(spec=kind)
        value.id = channel_id
        value.guild = SimpleNamespace(id=100)
        value.parent_id = 101 if kind is discord.Thread else None
        value.owner_id = 456
        value.archived = False
        value.locked = False
        value.name = "Feedback"
        value.mention = f"<#{channel_id}>"
        value.edit = AsyncMock(return_value=value)
        value.fetch_message = AsyncMock()
        value.send = AsyncMock()
        value.create_thread = AsyncMock()
        value.history = Mock(side_effect=lambda **kwargs: iterate([]))
        if kind is discord.ForumChannel:
            value.threads = []
            value.archived_threads = Mock(side_effect=lambda **kwargs: iterate([]))
        for key, item in attributes.items():
            setattr(value, key, item)
        return value

    def message(channel_value=None, message_id=999, author_id=456, bot=False, **attributes):
        value = MagicMock(spec=discord.Message)
        value.id = message_id
        value.author = SimpleNamespace(id=author_id, name="reporter", bot=bot, roles=[])
        value.guild = SimpleNamespace(id=100)
        value.channel = channel_value or channel()
        value.content = "Settings fail to save."
        value.jump_url = f"https://discord.com/channels/100/{value.channel.id}/{message_id}"
        value.attachments = []
        value.embeds = []
        value.edit = AsyncMock(return_value=value)
        value.reply = AsyncMock()
        value.create_thread = AsyncMock(return_value=channel())
        for key, item in attributes.items():
            setattr(value, key, item)
        return value

    def interaction(guild_id=100, channel_id=102, user_id=456, role_ids=()):
        return SimpleNamespace(
            id=800, guild_id=guild_id, channel_id=channel_id,
            channel=channel(channel_id=channel_id),
            user=SimpleNamespace(id=user_id, name="reporter", roles=[SimpleNamespace(id=value) for value in role_ids]),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def case(**updates):
        return {
            "id": "12345678-1234-1234-1234-123456789abc", "revision": 4,
            "bot_name": "Project Helper", "guild_id": "100", "channel_id": "102",
            "reporter_id": "456", "repository": "SillyBunnyTeam/SillyBunny",
            "delivery_key": "12345678-1234-1234-1234-123456789abc:42",
            "reply": "That sounds ready. Have a look at your words below before you send them.",
            "state": "awaiting_submission", "gate": {"kind": "review", "options": [
                {"key": "submit", "label": "Publish this draft"}, {"key": "edit", "label": "Edit draft"}]},
            "draft": {"kind": "issue", "title": "Settings fail to save", "body": "Exact **Markdown**\n\n@everyone <@456> `trace`."},
            **updates,
        }

    def event(kind="issue", **updates):
        return {
            "kind": kind, "key": f"{kind}:17", "number": 17, "title": "Settings fail to save",
            "body": "Reported behavior, not a verified fix.", "state": "open", "closed": False,
            "merged": False, "draft": False,
            "html_url": f"https://github.com/SillyBunnyTeam/SillyBunny/{'pull' if kind == 'pull' else 'issues'}/17",
            **updates,
        }

    class Store:
        def __init__(self):
            self.links = {}
            self.pending = []
            self.current_case = None

        def get_link(self, kind, key):
            return deepcopy(self.links.get((kind, key)))

        def put_link(self, kind, key, data):
            self.links[(kind, key)] = deepcopy(data)

        def list_pending_cases(self):
            return deepcopy(self.pending)

        def find_case(self, channel_id):
            return self.current_case if self.current_case and self.current_case["channel_id"] == channel_id else None

    class NativeDiscordTransportTests(unittest.IsolatedAsyncioTestCase):
        async def asyncSetUp(self):
            self.config = {
                "enabled": True, "bot_name": "Project Helper", "guild_id": "100",
                "feedback_channel_id": "101", "support_channel_id": "110",
                "issues_channel_id": "201", "reviews_channel_id": "202",
                "commits_channel_id": "203", "github_channel_id": "204",
                "repository": "SillyBunnyTeam/SillyBunny",
            }
            self.store = Store()
            self.cases = {}
            self.channels = {}
            self.real_client = discord.Client(intents=discord.Intents.none())
            client = MagicMock(spec=discord.Client)
            client.user = SimpleNamespace(id=777)
            client.get_channel = Mock(side_effect=lambda value: self.channels.get(value))
            client.fetch_channel = AsyncMock(side_effect=lambda value: self.channels[value])
            client.add_view = Mock()
            self.bot = SimpleNamespace(name="Project Helper", client=client, tree=app_commands.CommandTree(self.real_client))
            self.service = SimpleNamespace(
                store=self.store, settings=lambda: self.config, paused=Mock(return_value=False),
                bots={self.bot.name: self.bot}, get_case=lambda identifier: self.cases.get(identifier),
                choose=Mock(), check_new_report=Mock(), receive_report=Mock(), add_detail=Mock(), recover=AsyncMock(return_value=False),
                retry=AsyncMock(return_value="Draft returned for fresh approval."),
                validate_report_author=Mock(), submit_report=Mock(),
                say=AsyncMock(side_effect=lambda item, event: "Character reply: " + event["facts"].get("error", event["action"])),
            )
            self.transport = DiscordTransport(self.service)
            self.access_patch = patch("response_access.message_access", return_value=(True, None))
            self.access = self.access_patch.start()

        async def asyncTearDown(self):
            self.access_patch.stop()
            for view in self.transport._views.values():
                view.stop()
            await self.real_client.close()

        def configure_feedback(self, item=None):
            item = item or case()
            self.cases[item["id"]] = item
            thread = channel()
            sent = message(thread, author_id=777, bot=True)
            thread.send.return_value = sent
            self.channels[thread.id] = thread
            return item, thread, sent

        def configure_forum(self, kind="issue"):
            forum = channel(discord.ForumChannel, 201 if kind == "issue" else 202)
            thread = channel(channel_id=301)
            starter = message(thread, message_id=301, author_id=777, bot=True)
            thread.fetch_message.return_value = starter
            forum.create_thread.return_value = SimpleNamespace(thread=thread, message=starter)
            self.channels[forum.id] = forum
            self.channels[thread.id] = thread
            return forum, thread, starter

        async def test_real_view_is_persistent_and_ids_bind_revision_and_action(self):
            item = case()
            view = DecisionView(self.service, item)
            self.assertIsNone(view.timeout)
            self.assertTrue(view.is_persistent())
            self.assertEqual([child.custom_id for child in view.children if child.custom_id], [
                f"project:{item['id']}:4:submit", f"project:{item['id']}:4:edit"])
            self.assertTrue(all(len(child.custom_id) <= 100 for child in view.children if child.custom_id))
            self.assertIs(view.children[0].style, discord.ButtonStyle.primary)
            self.assertEqual(view.children[0].label, "Approve and post")
            self.assertEqual(view.children[-1].label, "Review report")
            view.stop()

        async def test_callback_defers_before_authorization_and_passes_exact_actor(self):
            item = case()
            self.cases[item["id"]] = item
            view = DecisionView(self.service, item)
            click = interaction(role_ids=[900, 901])
            order = []
            click.response.defer.side_effect = lambda **kwargs: order.append("defer")
            self.service.choose.side_effect = lambda *args, **kwargs: order.append("choose")
            await view.children[0].callback(click)
            self.assertEqual(order, ["defer", "choose"])
            self.service.choose.assert_called_once_with(item["id"], 4, "submit", user_id=456, role_ids=["900", "901"])
            click.response.defer.assert_awaited_once_with(ephemeral=True)
            self.assertTrue(click.followup.send.await_args.kwargs["ephemeral"])
            view.stop()

        async def test_wrong_guild_or_channel_never_calls_choose(self):
            item = case()
            self.cases[item["id"]] = item
            view = DecisionView(self.service, item)
            for click in (interaction(guild_id=200), interaction(channel_id=103)):
                await view.children[0].callback(click)
                self.assertIn("another feedback thread", click.followup.send.await_args.args[0])
            self.service.choose.assert_not_called()
            view.stop()

        async def test_unauthorized_or_stale_choice_is_private_and_does_not_claim_success(self):
            item = case()
            self.cases[item["id"]] = item
            view = DecisionView(self.service, item)
            for failure in (WorkflowError("Only the reporter can choose. @everyone"), Conflict("This case changed.")):
                self.service.choose.side_effect = failure
                click = interaction(user_id=888)
                await view.children[0].callback(click)
                delivery = click.followup.send.await_args
                self.assertEqual(delivery.args[0], "Character reply: " + str(failure))
                self.assertTrue(delivery.kwargs["ephemeral"])
                self.assertIs(delivery.kwargs["allowed_mentions"], NO_MENTIONS)
                self.assertNotIn("Choice saved", delivery.args[0])
            view.stop()

        async def test_write_opens_empty_modal_after_local_authorization_without_defer(self):
            item = case(state="awaiting_report", gate={"options": [{"key": "write", "label": "Write report"}]})
            self.cases[item["id"]] = item
            view = DecisionView(self.service, item)
            click = interaction(role_ids=[900])
            await view.children[0].callback(click)
            self.service.validate_report_author.assert_called_once_with(item["id"], 4, 456, ["900"])
            click.response.defer.assert_not_awaited()
            self.service.say.assert_not_awaited()
            self.service.choose.assert_not_called()
            modal = click.response.send_modal.await_args.args[0]
            self.assertIsInstance(modal, HumanReportModal)
            self.assertEqual(modal.report_title.value, "")
            self.assertEqual(modal.report_body.value, "")
            self.assertEqual(modal.report_title.max_length, 180)
            self.assertEqual(modal.report_body.max_length, 2800)
            self.assertIn("wrote this myself", modal.authorship.label)
            modal.stop()
            view.stop()

        async def test_edit_prefills_only_attested_human_text_never_generated_draft(self):
            item = case(submitted_report={"title": "My title", "body": "My own report.", "human_authored": True})
            modal = HumanReportModal(self.service, item)
            self.assertEqual(modal.report_title.value, "My title")
            self.assertEqual(modal.report_body.value, "My own report.")
            self.assertEqual(modal.authorship.value, "")
            modal.stop()
            item["submitted_report"]["human_authored"] = False
            modal = HumanReportModal(self.service, item)
            self.assertEqual(modal.report_title.value, "")
            self.assertEqual(modal.report_body.value, "")
            modal.stop()

        async def test_unauthorized_or_stale_edit_never_opens_the_modal(self):
            item = case()
            self.cases[item["id"]] = item
            view = DecisionView(self.service, item)
            for error in (WorkflowError("Only the original reporter may write this report."), Conflict("This report changed.")):
                self.service.validate_report_author.side_effect = error
                click = interaction(user_id=888)
                await view.children[1].callback(click)
                click.response.send_modal.assert_not_awaited()
                click.response.defer.assert_awaited_once_with(ephemeral=True)
                self.assertIn(str(error), click.followup.send.await_args.args[0])
            view.stop()

        async def test_modal_saves_exact_human_text_only_after_authorship_confirmation(self):
            item = case()
            self.cases[item["id"]] = item
            modal = HumanReportModal(self.service, item)
            modal.report_title._value = "The title I typed"
            modal.report_body._value = "My **own** words.\n\nNothing rewritten."
            modal.authorship._value = "YES"
            click = interaction(role_ids=[900])
            order = []
            click.response.defer.side_effect = lambda **kwargs: order.append("defer")
            self.service.submit_report.side_effect = lambda *args, **kwargs: order.append("save")
            await modal.on_submit(click)
            self.assertEqual(order, ["defer", "save"])
            self.service.validate_report_author.assert_called_once_with(
                item["id"], 4, 456, ["900"], report_revision=0, issue_number=None,
            )
            self.service.submit_report.assert_called_once_with(
                item["id"], 4, user_id=456, username="reporter", title=modal.report_title.value,
                body=modal.report_body.value, role_ids=["900"], human_authored=True,
                report_revision=0, issue_number=None,
            )
            self.assertEqual(self.service.say.await_args.args[1]["facts"], {"saved": True, "published": False})
            self.assertEqual(click.followup.send.await_args.args[0], "Character reply: report_saved")
            self.assertIs(click.followup.send.await_args.kwargs["allowed_mentions"], NO_MENTIONS)
            modal.stop()

        async def test_modal_without_authorship_confirmation_does_not_save(self):
            item = case()
            self.cases[item["id"]] = item
            modal = HumanReportModal(self.service, item)
            modal.authorship._value = "no"
            await modal.on_submit(interaction())
            self.service.submit_report.assert_not_called()
            self.assertIn("typing YES", self.service.say.await_args.args[1]["facts"]["error"])
            modal.stop()

        async def test_modal_revalidates_after_the_report_changed_while_form_was_open(self):
            item = case()
            self.cases[item["id"]] = item
            modal = HumanReportModal(self.service, item)
            modal.authorship._value = "YES"
            self.service.validate_report_author.side_effect = Conflict("This report changed.")
            click = interaction()
            await modal.on_submit(click)
            self.service.submit_report.assert_not_called()
            self.assertIn("This report changed", click.followup.send.await_args.args[0])
            modal.stop()

        async def test_modal_cannot_cross_server_or_thread_boundaries(self):
            item = case()
            self.cases[item["id"]] = item
            modal = HumanReportModal(self.service, item)
            for click in (interaction(guild_id=200), interaction(channel_id=103)):
                await modal.on_submit(click)
                self.assertIsNone(self.service.say.await_args.args[0])
                self.assertIn("another feedback thread", click.followup.send.await_args.args[0])
            self.service.validate_report_author.assert_not_called()
            self.service.submit_report.assert_not_called()
            modal.stop()

        async def test_attach_restores_persistent_views_and_registers_commands_once(self):
            item = case()
            self.store.pending = [item, case(id="other", bot_name="Other Bot")]
            self.store.put_link("feedback", item["id"], {"message_id": "999", "channel_id": "102"})
            await self.transport.attach(self.bot)
            first = self.transport._views[item["id"]]
            await self.transport.attach(self.bot)
            self.assertTrue(first.is_finished())
            self.assertEqual([command.name for command in self.bot.tree.get_commands()], ["feedback", "project-recover", "project-retry"])
            self.assertEqual(self.bot.client.add_view.call_count, 2)
            self.assertEqual(self.bot.client.add_view.call_args.kwargs, {"message_id": 999})
            self.assertEqual(set(self.transport._views), {item["id"]})

        async def test_issue_and_comment_preview_preserve_exact_public_text(self):
            for kind in ("issue", "comment"):
                item, thread, sent = self.configure_feedback()
                item["draft"].update(kind=kind, issue_number=17)
                await self.transport.notify(item)
                kwargs = thread.send.await_args.kwargs
                self.assertEqual(kwargs["embed"].title, item["draft"]["title"])
                self.assertEqual(kwargs["embed"].description, item["draft"]["body"])
                self.assertIs(kwargs["allowed_mentions"], NO_MENTIONS)
                self.assertTrue(kwargs["view"].is_persistent())
                self.assertEqual(kwargs["content"], item["reply"])
                self.assertEqual(kwargs["embed"].footer.text, f"Feedback {item['delivery_key']}")
                self.assertEqual(kwargs["view"].children[-1].url, sent.jump_url)
                self.assertEqual(set(sent.edit.await_args.kwargs), {"view", "allowed_mentions"})
                self.assertEqual(self.store.get_link("feedback", item["id"])["message_id"], str(sent.id))
                self.store.links.clear()

        async def test_new_conversation_turn_preserves_the_previous_message(self):
            item, thread, sent = self.configure_feedback()
            previous = message(thread, message_id=998, author_id=777, bot=True)
            previous.embeds = [discord.Embed().set_footer(text=f"Feedback {item['id']}:41")]
            self.store.put_link("feedback", item["id"], {"channel_id": "102", "message_id": str(previous.id),
                                                        "delivery_key": f"{item['id']}:41", "revision": 3})
            thread.fetch_message.return_value = previous
            await self.transport.notify(item)
            thread.send.assert_awaited_once()
            previous.edit.assert_awaited_once_with(view=None, allowed_mentions=NO_MENTIONS)
            self.assertEqual(self.store.get_link("feedback_delivery", item["delivery_key"])["message_id"], str(sent.id))

        async def test_failure_to_retire_older_controls_does_not_fail_the_new_reply(self):
            item, thread, sent = self.configure_feedback()
            previous = message(thread, message_id=998, author_id=777, bot=True)
            previous.embeds = [discord.Embed().set_footer(text=f"Feedback {item['id']}:41")]
            previous.edit.side_effect = OSError("Permission changed")
            self.store.put_link("feedback", item["id"], {"channel_id": "102", "message_id": str(previous.id),
                                                        "delivery_key": f"{item['id']}:41", "revision": 3})
            thread.fetch_message.return_value = previous
            with patch("project_automation_discord.warn") as warning:
                self.assertTrue(await self.transport.notify(item))
            warning.assert_called_once()
            self.assertEqual(self.store.get_link("feedback", item["id"])["message_id"], str(sent.id))

        async def test_recovering_an_old_reply_keeps_the_newer_control_receipt(self):
            for revision in (4, 5):
                self.store.links.clear()
                item, thread, sent = self.configure_feedback()
                latest = {"channel_id": "102", "message_id": "1001", "revision": revision, "delivery_key": f"{item['id']}:43"}
                self.store.put_link("feedback", item["id"], latest)
                sent.embeds = [discord.Embed().set_footer(text=f"Feedback {item['delivery_key']}")]
                thread.history.side_effect = lambda **kwargs: iterate([sent])
                self.assertTrue(await self.transport.notify(item, recover_only=True))
                self.assertEqual(self.store.get_link("feedback", item["id"]), latest)
                self.assertEqual(self.store.get_link("feedback_delivery", item["delivery_key"])["message_id"], str(sent.id))

        async def test_retry_of_the_same_conversation_turn_does_not_resend_or_rewrite_it(self):
            item, thread, sent = self.configure_feedback()
            embed = discord.Embed().set_footer(text=f"Feedback {item['delivery_key']}")
            sent.embeds = [embed]
            self.store.put_link("feedback_delivery", item["delivery_key"], {"channel_id": "102", "message_id": str(sent.id)})
            thread.fetch_message.return_value = sent
            await self.transport.notify(item)
            thread.send.assert_not_awaited()
            sent.edit.assert_not_awaited()
            self.assertEqual(self.store.get_link("feedback", item["id"])["message_id"], str(sent.id))

        async def test_discussion_is_plain_generated_text_without_state_cards(self):
            item, thread, _ = self.configure_feedback(case(state="discussing", draft=None, gate=None, reply="Does it happen with a new preset too?"))
            await self.transport.notify(item)
            kwargs = thread.send.await_args.kwargs
            self.assertEqual(kwargs["content"], item["reply"])
            self.assertIsNone(kwargs["embed"].title)
            self.assertIsNone(kwargs["embed"].description)
            self.assertEqual(kwargs["embed"].fields, [])
            self.service.say.assert_not_awaited()

        async def test_failed_feedback_send_does_not_save_a_phantom_delivery(self):
            item, thread, _ = self.configure_feedback()
            thread.send.side_effect = OSError("No acknowledgement")
            with self.assertRaises(OSError):
                await self.transport.notify(item)
            self.assertIsNone(self.store.get_link("feedback", item["id"]))
            self.assertIsNone(self.store.get_link("feedback_delivery", item["delivery_key"]))

        async def test_recovery_of_one_turn_does_not_adopt_a_previous_turn(self):
            item, thread, sent = self.configure_feedback()
            sent.embeds = [discord.Embed().set_footer(text=f"Feedback {item['id']}:41")]
            thread.history.side_effect = lambda **kwargs: iterate([sent])
            self.store.put_link("feedback", item["id"], {"channel_id": "102", "message_id": str(sent.id)})
            self.assertFalse(await self.transport.notify(item, recover_only=True))
            thread.send.assert_not_awaited()
            sent.edit.assert_not_awaited()
            self.assertIsNone(self.store.get_link("feedback_delivery", item["delivery_key"]))

        async def test_recovery_restores_local_controls_without_public_writes(self):
            item, thread, sent = self.configure_feedback()
            thread.archived = True
            sent.embeds = [discord.Embed().set_footer(text=f"Feedback {item['delivery_key']}")]
            thread.history.side_effect = lambda **kwargs: iterate([sent])
            self.assertTrue(await self.transport.notify(item, recover_only=True))
            thread.edit.assert_not_awaited()
            thread.send.assert_not_awaited()
            sent.edit.assert_not_awaited()
            self.bot.client.add_view.assert_called_once()
            self.assertEqual(self.bot.client.add_view.call_args.kwargs["message_id"], sent.id)

        async def test_failed_preview_link_edit_preserves_receipt_and_retry_does_not_duplicate(self):
            item, thread, sent = self.configure_feedback()
            sent.edit.side_effect = OSError("Lost edit acknowledgement")
            with self.assertRaises(OSError):
                await self.transport.notify(item)
            self.assertEqual(self.store.get_link("feedback_delivery", item["delivery_key"])["message_id"], str(sent.id))
            sent.embeds = [thread.send.await_args.kwargs["embed"]]
            thread.fetch_message.return_value = sent
            self.assertTrue(await self.transport.notify(item, recover_only=True))
            self.assertEqual(thread.send.await_count, 1)
            self.assertEqual(sent.edit.await_count, 1)

        async def test_feedback_does_not_recover_a_saved_message_owned_by_someone_else(self):
            item, thread, sent = self.configure_feedback()
            sent.author.id = 888
            sent.embeds = [discord.Embed().set_footer(text=f"Feedback {item['delivery_key']}")]
            self.store.put_link("feedback_delivery", item["delivery_key"], {"channel_id": "102", "message_id": str(sent.id)})
            thread.fetch_message.return_value = sent
            with self.assertRaisesRegex(WorkflowError, "different Discord author"):
                await self.transport.notify(item, recover_only=True)
            thread.send.assert_not_awaited()
            sent.edit.assert_not_awaited()

        async def test_missing_or_oversized_generated_reply_is_not_replaced_with_a_template(self):
            for reply in (None, "", "x" * 2001):
                item, thread, _ = self.configure_feedback(case(reply=reply))
                with self.assertRaisesRegex(WorkflowError, "missing or too long"):
                    await self.transport.notify(item)
                thread.send.assert_not_awaited()

        async def test_feedback_recovery_only_finds_receipt_without_resending(self):
            item, thread, sent = self.configure_feedback()
            embed = discord.Embed(description="Prior send")
            embed.set_footer(text=f"Feedback {item['delivery_key']}")
            sent.embeds = [embed]
            thread.history.side_effect = lambda **kwargs: iterate([sent])
            self.assertTrue(await self.transport.notify(item, recover_only=True))
            thread.send.assert_not_awaited()
            sent.edit.assert_not_awaited()
            self.assertEqual(self.store.get_link("feedback", item["id"])["message_id"], str(sent.id))

        async def test_recovery_does_not_adopt_another_users_copied_marker(self):
            item, thread, _ = self.configure_feedback()
            copied = message(thread, author_id=888)
            embed = discord.Embed(description="Copied marker")
            embed.set_footer(text=f"Feedback {item['delivery_key']}")
            copied.embeds = [embed]
            thread.history.side_effect = lambda **kwargs: iterate([copied])
            self.assertFalse(await self.transport.notify(item, recover_only=True))
            thread.send.assert_not_awaited()
            copied.edit.assert_not_awaited()
            self.assertIsNone(self.store.get_link("feedback", item["id"]))

        async def test_channel_access_checks_server_and_response_policy(self):
            foreign = channel(guild=SimpleNamespace(id=999))
            self.channels[foreign.id] = foreign
            with self.assertRaisesRegex(WorkflowError, "different server"):
                await self.transport._channel(foreign.id)
            allowed = channel()
            self.channels[allowed.id] = allowed
            self.access.return_value = (False, "blocked")
            with self.assertRaisesRegex(WorkflowError, "block this project channel"):
                await self.transport._channel(allowed.id)
            self.access.assert_called_with(False, 0, 101)

        async def test_other_bots_yield_all_reserved_channels_even_when_paused(self):
            other_bot = SimpleNamespace(name="Firefly")
            self.service.paused.return_value = True
            for key in ("feedback_channel_id", "issues_channel_id", "reviews_channel_id", "commits_channel_id", "github_channel_id"):
                incoming = message(channel(discord.TextChannel, int(self.config[key])))
                self.assertTrue(await self.transport.handle_message(other_bot, incoming))
                incoming.create_thread.assert_not_awaited()
            self.service.receive_report.assert_not_called()
            self.service.add_detail.assert_not_called()

        async def test_normal_support_messages_do_not_start_feedback(self):
            incoming = message(channel(discord.TextChannel, 110))
            self.assertFalse(await self.transport.handle_message(self.bot, incoming))
            incoming.create_thread.assert_not_awaited()
            self.service.receive_report.assert_not_called()

        async def test_private_or_foreign_server_messages_do_not_enter_project_workflow(self):
            for guild in (None, SimpleNamespace(id=999)):
                incoming = message(guild=guild)
                self.assertFalse(await self.transport.handle_message(self.bot, incoming))
                incoming.create_thread.assert_not_awaited()
            self.service.receive_report.assert_not_called()
            self.service.add_detail.assert_not_called()

        async def test_bot_messages_are_not_feedback_and_existing_threads_forward_details(self):
            automated = message(bot=True)
            self.assertTrue(await self.transport.handle_message(self.bot, automated))
            self.service.receive_report.assert_not_called()
            item = case()
            self.store.current_case = item
            incoming = message()
            self.assertTrue(await self.transport.handle_message(self.bot, incoming))
            self.service.add_detail.assert_called_once_with(
                item["id"], user_id=456, content=incoming.content, message_id=999,
                source_url=incoming.jump_url, role_ids=[], username="reporter",
            )

        async def test_new_feedback_text_message_gets_one_thread_and_report(self):
            incoming = message(channel(discord.TextChannel, 101))
            self.assertTrue(await self.transport.handle_message(self.bot, incoming))
            incoming.create_thread.assert_awaited_once()
            self.service.receive_report.assert_called_once_with(
                source_key="discord:100:999", channel_id=102, reporter_id=456,
                content=incoming.content, source_url=incoming.jump_url, message_id=999,
                reporter_name="reporter", title="Feedback",
            )
            self.service.check_new_report.assert_called_once_with(456, incoming.content)

        async def test_report_limit_is_checked_before_creating_a_text_feedback_thread(self):
            incoming = message(channel(discord.TextChannel, 101))
            self.service.check_new_report.side_effect = WorkflowError("You already have three open reports.")
            self.assertTrue(await self.transport.handle_message(self.bot, incoming))
            incoming.create_thread.assert_not_awaited()
            self.service.receive_report.assert_not_called()
            self.assertIn("three open reports", incoming.reply.await_args.args[0])
            self.assertIs(incoming.reply.await_args.kwargs["allowed_mentions"], NO_MENTIONS)

        async def test_report_limit_is_checked_before_support_creates_a_forum_post(self):
            self.transport._commands(self.bot)
            forum = channel(discord.ForumChannel, 101)
            self.channels[101] = forum
            click = interaction(channel_id=110)
            click.channel = channel(discord.TextChannel, 110)
            self.service.check_new_report.side_effect = WorkflowError("You already have three open reports.")
            await self.bot.tree.get_command("feedback").callback(click, "One more report")
            forum.create_thread.assert_not_awaited()
            self.service.receive_report.assert_not_called()
            self.assertIn("three open reports", click.followup.send.await_args.args[0])
            self.assertTrue(click.followup.send.await_args.kwargs["ephemeral"])
            self.assertIs(click.followup.send.await_args.kwargs["allowed_mentions"], NO_MENTIONS)

        async def test_concurrent_support_and_text_intake_share_one_reporter_limit(self):
            self.transport._commands(self.bot)
            active_reports = 2

            def check_limit(user_id, content):
                if active_reports >= 3:
                    raise WorkflowError("You already have three open reports.")

            def store_report(**kwargs):
                nonlocal active_reports
                active_reports += 1

            async def create_text_thread(**kwargs):
                await asyncio.sleep(0)
                return channel(channel_id=302)

            async def create_forum_thread(**kwargs):
                await asyncio.sleep(0)
                return SimpleNamespace(thread=channel(channel_id=303))

            self.service.check_new_report.side_effect = check_limit
            self.service.receive_report.side_effect = store_report
            incoming = message(channel(discord.TextChannel, 101))
            incoming.create_thread.side_effect = create_text_thread
            forum = channel(discord.ForumChannel, 101)
            forum.create_thread.side_effect = create_forum_thread
            self.channels[101] = forum
            click = interaction(channel_id=110)
            click.channel = channel(discord.TextChannel, 110)
            await asyncio.gather(
                self.transport.handle_message(self.bot, incoming),
                self.bot.tree.get_command("feedback").callback(click, "Another simultaneous report"),
            )
            self.assertEqual(active_reports, 3)
            self.assertEqual(incoming.create_thread.await_count + forum.create_thread.await_count, 1)
            self.service.receive_report.assert_called_once()
            self.assertEqual(self.service.check_new_report.call_count, 2)

        async def test_thread_start_requires_original_reporter(self):
            incoming = message(channel(owner_id=987))
            self.assertTrue(await self.transport.handle_message(self.bot, incoming))
            self.service.receive_report.assert_not_called()
            self.assertIn("original reporter", incoming.reply.await_args.args[0])
            self.assertIs(incoming.reply.await_args.kwargs["allowed_mentions"], NO_MENTIONS)
            self.assertFalse(incoming.reply.await_args.kwargs["mention_author"])

        async def test_explicit_feedback_command_from_support_creates_forum_report(self):
            self.transport._commands(self.bot)
            forum = channel(discord.ForumChannel, 101)
            destination = channel(channel_id=302)
            forum.create_thread.return_value = SimpleNamespace(thread=destination)
            self.channels[101] = forum
            click = interaction(channel_id=110)
            click.channel = channel(discord.TextChannel, 110)
            report = "Save fails after changing a preset."
            await self.bot.tree.get_command("feedback").callback(click, report)
            forum.create_thread.assert_awaited_once()
            self.assertIs(forum.create_thread.await_args.kwargs["allowed_mentions"], NO_MENTIONS)
            self.service.receive_report.assert_called_once_with(
                source_key="interaction:800", channel_id=302, reporter_id=456, content=report,
                source_url="https://discord.com/channels/100/302", message_id=800,
                reporter_name="reporter", title=report,
            )
            self.assertTrue(click.followup.send.await_args.kwargs["ephemeral"])
            self.assertIs(click.followup.send.await_args.kwargs["allowed_mentions"], NO_MENTIONS)

        async def test_feedback_command_rejects_other_guild_channel_or_bot(self):
            self.transport._commands(self.bot)
            command = self.bot.tree.get_command("feedback")
            for click in (interaction(guild_id=999, channel_id=110), interaction(channel_id=999)):
                click.channel.parent_id = None
                await command.callback(click, "A report")
                click.followup.send.assert_awaited_once()
                click.response.defer.assert_awaited_once_with(ephemeral=True)
            self.config["bot_name"] = "Different Helper"
            await command.callback(interaction(channel_id=110), "A report")
            self.service.receive_report.assert_not_called()

        async def test_issue_and_pull_create_one_forum_post_with_truthful_state(self):
            for kind, updates, expected in (
                ("issue", {}, "Open"), ("pull", {"draft": True}, "Draft"),
                ("pull", {"closed": True, "state": "closed", "merged": False}, "Closed"),
                ("pull", {"closed": True, "state": "closed", "merged": True}, "Merged"),
            ):
                self.store.links.clear()
                forum, thread, _ = self.configure_forum(kind)
                await self.transport.mirror(event(kind, **updates))
                forum.create_thread.assert_awaited_once()
                created = forum.create_thread.await_args.kwargs
                self.assertTrue(created["embed"].title.startswith(expected + " · "))
                self.assertTrue(created["name"].endswith(" · #17"))
                self.assertIs(created["allowed_mentions"], NO_MENTIONS)
                if updates.get("closed"):
                    thread.edit.assert_awaited_once_with(archived=True, locked=True)
                else:
                    thread.edit.assert_not_awaited()

        async def test_reopened_mirror_updates_and_unlocks_the_original_post(self):
            forum, thread, starter = self.configure_forum()
            key = "100:Project Helper:201:SillyBunnyTeam/SillyBunny:issue:17"
            self.store.put_link("mirror", key, {"channel_id": "301", "message_id": "301"})
            thread.archived = thread.locked = True
            await self.transport.mirror(event())
            self.assertEqual(thread.edit.await_args_list[0].kwargs, {"archived": False, "locked": False})
            self.assertIn("Open", starter.edit.await_args.kwargs["embed"].title)
            self.assertIs(starter.edit.await_args.kwargs["allowed_mentions"], NO_MENTIONS)
            forum.create_thread.assert_not_awaited()
            self.assertEqual(self.store.get_link("mirror", key)["channel_id"], "301")

        async def test_reopening_also_unlocks_a_post_already_unarchived_by_a_moderator(self):
            _, thread, _ = self.configure_forum()
            self.store.put_link("mirror", "100:Project Helper:201:SillyBunnyTeam/SillyBunny:issue:17", {"channel_id": "301", "message_id": "301"})
            thread.archived = False
            thread.locked = True
            await self.transport.mirror(event())
            self.assertTrue(any(call.kwargs.get("locked") is False for call in thread.edit.await_args_list))

        async def test_lost_forum_create_receipt_is_recovered_from_its_owned_marker(self):
            forum, thread, starter = self.configure_forum()
            thread.name = "Open · Settings fail to save · #17"
            embed = discord.Embed(description="Prior post")
            embed.set_footer(text="Project 100:Project Helper:201:SillyBunnyTeam/SillyBunny:issue:17")
            starter.embeds = [embed]
            forum.threads = [thread]
            self.assertTrue(await self.transport.mirror(event(), recover_only=True))
            forum.create_thread.assert_not_awaited()
            starter.edit.assert_not_awaited()
            self.assertEqual(self.store.get_link("mirror", "100:Project Helper:201:SillyBunnyTeam/SillyBunny:issue:17")["message_id"], "301")

        async def test_changed_mirror_destination_does_not_edit_the_old_channel(self):
            _, old_thread, old_starter = self.configure_forum()
            await self.transport.mirror(event())
            new_forum = channel(discord.ForumChannel, 205)
            new_thread = channel(channel_id=302)
            new_starter = message(new_thread, message_id=302, author_id=777, bot=True)
            new_forum.create_thread.return_value = SimpleNamespace(thread=new_thread, message=new_starter)
            self.channels[205] = new_forum
            self.channels[302] = new_thread
            self.config["issues_channel_id"] = "205"
            await self.transport.mirror(event(state="closed", closed=True))
            new_forum.create_thread.assert_awaited_once()
            old_starter.edit.assert_not_awaited()
            old_thread.edit.assert_not_awaited()
            link = self.store.get_link("mirror", "100:Project Helper:205:SillyBunnyTeam/SillyBunny:issue:17")
            self.assertEqual(link["channel_id"], "302")
            self.assertEqual(link["bot_name"], "Project Helper")
            self.assertEqual(link["guild_id"], "100")

        async def test_changed_helper_identity_does_not_adopt_previous_bots_post(self):
            forum, old_thread, old_starter = self.configure_forum()
            await self.transport.mirror(event())
            old_thread.name = "Open · Settings fail to save · #17"
            old_embed = discord.Embed()
            old_embed.set_footer(text="Project 100:Project Helper:201:SillyBunnyTeam/SillyBunny:issue:17")
            old_starter.embeds = [old_embed]
            forum.threads = [old_thread]
            replacement_client = MagicMock(spec=discord.Client)
            replacement_client.user = SimpleNamespace(id=888)
            replacement_client.get_channel = Mock(side_effect=lambda identifier: self.channels.get(identifier))
            replacement = SimpleNamespace(name="Replacement Helper", client=replacement_client)
            self.service.bots[replacement.name] = replacement
            self.config["bot_name"] = replacement.name
            new_thread = channel(channel_id=302)
            new_starter = message(new_thread, message_id=302, author_id=888, bot=True)
            forum.create_thread.return_value = SimpleNamespace(thread=new_thread, message=new_starter)
            await self.transport.mirror(event())
            self.assertEqual(forum.create_thread.await_count, 2)
            old_starter.edit.assert_not_awaited()
            link = self.store.get_link("mirror", "100:Replacement Helper:201:SillyBunnyTeam/SillyBunny:issue:17")
            self.assertEqual(link["message_id"], "302")
            self.assertEqual(link["bot_name"], replacement.name)

        async def test_saved_mirror_owned_by_another_author_is_not_edited(self):
            forum, _, starter = self.configure_forum()
            starter.author.id = 888
            self.store.put_link("mirror", "100:Project Helper:201:SillyBunnyTeam/SillyBunny:issue:17", {"channel_id": "301", "message_id": "301"})
            with self.assertRaisesRegex(WorkflowError, "different Discord bot"):
                await self.transport.mirror(event())
            starter.edit.assert_not_awaited()
            forum.create_thread.assert_not_awaited()

        async def test_retry_command_passes_explicit_non_delivery_confirmation(self):
            self.transport._commands(self.bot)
            click = interaction(role_ids=[900])
            await self.bot.tree.get_command("project-retry").callback(click, 42, True)
            self.service.retry.assert_awaited_once_with(42, user_id=456, role_ids=["900"], confirmed_not_delivered=True)
            self.assertTrue(click.followup.send.await_args.kwargs["ephemeral"])
            self.assertIs(click.followup.send.await_args.kwargs["allowed_mentions"], NO_MENTIONS)

        async def test_issue_tracker_requires_forum_but_commit_and_general_use_text(self):
            self.channels[201] = channel(discord.TextChannel, 201)
            with self.assertRaisesRegex(WorkflowError, "forum channels"):
                await self.transport.mirror(event())
            for kind, identifier in (("commit", 203), ("general", 204)):
                destination = channel(discord.TextChannel, identifier)
                destination.send.return_value = message(destination, author_id=777, bot=True)
                self.channels[identifier] = destination
                await self.transport.mirror(event(kind))
                destination.send.assert_awaited_once()
                self.assertIs(destination.send.await_args.kwargs["allowed_mentions"], NO_MENTIONS)

        async def test_mentions_are_disabled_and_attachments_are_not_claimed_as_inspected(self):
            self.assertEqual(NO_MENTIONS.to_dict(), {"parse": []})
            incoming = message(attachments=[SimpleNamespace(url=f"https://example.org/log-{number}.txt") for number in range(8)])
            text = message_text(incoming)
            self.assertEqual(text.count("Attachment (not inspected):"), 5)
            self.assertNotIn("log-5", text)
            incoming.content = "x" * 9000
            self.assertEqual(len(message_text(incoming)), 8000)

    return unittest.defaultTestLoader.loadTestsFromTestCase(NativeDiscordTransportTests)


if __name__ == "__main__":
    import unittest
    result = unittest.TextTestRunner(verbosity=2).run(_native_suite())
    raise SystemExit(0 if result.wasSuccessful() else 1)
