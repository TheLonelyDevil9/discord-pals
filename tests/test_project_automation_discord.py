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
    from project_automation_discord import DecisionView, DiscordTransport, NO_MENTIONS, message_text
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
        value.author = SimpleNamespace(id=author_id, bot=bot, roles=[])
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
            user=SimpleNamespace(id=user_id, roles=[SimpleNamespace(id=value) for value in role_ids]),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def case(**updates):
        return {
            "id": "12345678-1234-1234-1234-123456789abc", "revision": 4,
            "bot_name": "Project Helper", "guild_id": "100", "channel_id": "102",
            "reporter_id": "456", "repository": "SillyBunnyTeam/SillyBunny",
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
            self.assertEqual([child.custom_id for child in view.children], [
                f"project:{item['id']}:4:submit", f"project:{item['id']}:4:edit"])
            self.assertTrue(all(len(child.custom_id) <= 100 for child in view.children))
            self.assertIs(view.children[0].style, discord.ButtonStyle.primary)
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
                self.assertEqual(delivery.args[0], str(failure))
                self.assertTrue(delivery.kwargs["ephemeral"])
                self.assertIs(delivery.kwargs["allowed_mentions"], NO_MENTIONS)
                self.assertNotIn("Choice saved", delivery.args[0])
            view.stop()

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
                self.assertIn(item["repository"], kwargs["content"])
                self.assertIn("Approve issue" if kind == "issue" else "Approve comment on #17", kwargs["content"])
                self.assertEqual(self.store.get_link("feedback", item["id"])["message_id"], str(sent.id))
                self.store.links.clear()

        async def test_existing_feedback_is_edited_without_an_extra_message(self):
            item, thread, sent = self.configure_feedback()
            self.store.put_link("feedback", item["id"], {"channel_id": "102", "message_id": str(sent.id)})
            thread.fetch_message.return_value = sent
            await self.transport.notify(item)
            sent.edit.assert_awaited_once()
            thread.send.assert_not_awaited()
            self.assertIs(sent.edit.await_args.kwargs["allowed_mentions"], NO_MENTIONS)

        async def test_failed_feedback_send_does_not_save_a_phantom_delivery(self):
            item, thread, _ = self.configure_feedback()
            thread.send.side_effect = OSError("No acknowledgement")
            with self.assertRaises(OSError):
                await self.transport.notify(item)
            self.assertIsNone(self.store.get_link("feedback", item["id"]))

        async def test_feedback_recovery_only_finds_receipt_without_resending(self):
            item, thread, sent = self.configure_feedback()
            embed = discord.Embed(description="Prior send")
            embed.set_footer(text=f"Feedback {item['id']}")
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
            embed.set_footer(text=f"Feedback {item['id']}")
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
                source_url=incoming.jump_url, role_ids=[],
            )

        async def test_new_feedback_text_message_gets_one_thread_and_report(self):
            incoming = message(channel(discord.TextChannel, 101))
            self.assertTrue(await self.transport.handle_message(self.bot, incoming))
            incoming.create_thread.assert_awaited_once()
            self.service.receive_report.assert_called_once_with(
                source_key="discord:100:999", channel_id=102, reporter_id=456,
                content=incoming.content, source_url=incoming.jump_url, message_id=999,
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
            )
            self.assertTrue(click.followup.send.await_args.kwargs["ephemeral"])
            self.assertIs(click.followup.send.await_args.kwargs["allowed_mentions"], NO_MENTIONS)

        async def test_feedback_command_rejects_other_guild_channel_or_bot(self):
            self.transport._commands(self.bot)
            command = self.bot.tree.get_command("feedback")
            for click in (interaction(guild_id=999, channel_id=110), interaction(channel_id=999)):
                click.channel.parent_id = None
                await command.callback(click, "A report")
                click.response.send_message.assert_awaited_once()
                click.response.defer.assert_not_awaited()
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
