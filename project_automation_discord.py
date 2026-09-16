"""Native Discord buttons, feedback threads, and repository mirrors."""

from __future__ import annotations

import asyncio
import io
import weakref
import discord
from discord import app_commands

from logger import warn
from project_automation import WorkflowError
from project_automation_github import GitHubError
from project_automation_store import Conflict


NO_MENTIONS = discord.AllowedMentions.none()
COLOUR = 0x539BEB


def roles(user):
    return [str(role.id) for role in getattr(user, "roles", [])]


def message_text(message):
    text = message.content or ""
    links = [f"Attachment (not inspected): {attachment.url}" for attachment in getattr(message, "attachments", [])[:5]]
    return (text + ("\n" + "\n".join(links) if links else ""))[:8000]


async def say(service, case, action, facts, next_step):
    return await service.say(case, {"action": action, "facts": facts, "next_step": next_step})


def in_case_thread(interaction, case):
    return bool(case and str(interaction.guild_id) == case["guild_id"]
                and str(interaction.channel_id) == case["channel_id"])


def feedback_order(receipt):
    job_id = str(receipt.get("delivery_key", "")).rsplit(":", 1)[-1]
    return receipt.get("revision", -1), int(job_id) if job_id.isdecimal() else -1


class HumanReportModal(discord.ui.Modal):
    """Collect the reporter's own public text separately from the conversation."""

    def __init__(self, service, case):
        super().__init__(title="Write your report", timeout=1800)
        self.service = service
        self.case_id = case["id"]
        self.revision = case["revision"]
        self.report_revision = case.get("report_revision", 0)
        self.issue_number = case.get("target_issue_number") or case.get("linked_issue_number")
        previous = case.get("submitted_report") or {}
        if not previous.get("human_authored"):
            previous = {}
        self.report_title = discord.ui.TextInput(
            label="Issue title (your own words)", max_length=180,
            default=previous.get("title"), required=True,
        )
        self.report_body = discord.ui.TextInput(
            label="Report (your own words)", style=discord.TextStyle.paragraph,
            placeholder="What happened, what you expected, and how to reproduce it.",
            max_length=2800, default=previous.get("body"), required=True,
        )
        self.authorship = discord.ui.TextInput(
            label="I wrote this myself: type YES", placeholder="YES", max_length=3, required=True,
        )
        for item in (self.report_title, self.report_body, self.authorship):
            self.add_item(item)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        case = self.service.get_case(self.case_id)
        if not in_case_thread(interaction, case):
            text = await say(self.service, None, "error", {"error": "This form belongs to another feedback thread."},
                             "Open the current report controls in its original thread.")
        else:
            try:
                self.service.validate_report_author(self.case_id, self.revision, interaction.user.id, roles(interaction.user),
                                                    report_revision=self.report_revision, issue_number=self.issue_number)
                if self.authorship.value.strip().upper() != "YES":
                    raise WorkflowError("Confirm that you wrote the report yourself by typing YES.")
                updated = self.service.submit_report(
                    self.case_id, self.revision, user_id=interaction.user.id, username=interaction.user.name,
                    title=self.report_title.value, body=self.report_body.value,
                    role_ids=roles(interaction.user), human_authored=True,
                    report_revision=self.report_revision, issue_number=self.issue_number,
                )
            except (WorkflowError, Conflict) as exc:
                text = await say(self.service, case, "error", {"error": str(exc)},
                                 "Use the current Write report or Edit report button to try again.")
            else:
                text = await say(self.service, updated, "report_saved", {"saved": True, "published": False},
                                 "Keep talking here while the report is checked; review your exact text before approving a post.")
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)


class DecisionView(discord.ui.View):
    def __init__(self, service, case):
        super().__init__(timeout=None)
        self.service = service
        self.case_id = case["id"]
        self.revision = case["revision"]
        for option in (case.get("gate") or {}).get("options", []):
            action = option["key"]
            label = "Approve and post" if action == "submit" else option["label"]
            button = discord.ui.Button(label=label, custom_id=f"project:{case['id']}:{case['revision']}:{action}",
                                       style=discord.ButtonStyle.primary if action == "submit" else discord.ButtonStyle.secondary)
            button.callback = self._callback(action)
            self.add_item(button)
        if case["state"] == "awaiting_submission" and case.get("draft"):
            self.add_item(discord.ui.Button(label="Review report", style=discord.ButtonStyle.link,
                                           url=f"https://discord.com/channels/{case['guild_id']}/{case['channel_id']}"))

    def _callback(self, action):
        async def callback(interaction):
            case = self.service.get_case(self.case_id)
            if not in_case_thread(interaction, case):
                await interaction.response.defer(ephemeral=True)
                text = await say(self.service, None, "error", {"error": "These controls belong to another feedback thread."},
                                 "Open the current controls in the original feedback thread.")
                await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)
                return
            if action in {"write", "edit"}:
                try:
                    self.service.validate_report_author(self.case_id, self.revision, interaction.user.id, roles(interaction.user))
                except (WorkflowError, Conflict) as exc:
                    await interaction.response.defer(ephemeral=True)
                    text = await say(self.service, case, "error", {"error": str(exc)},
                                     "The original reporter can use the current report controls.")
                    await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)
                else:
                    # Modal responses cannot follow a defer. Local authorization is synchronous.
                    await interaction.response.send_modal(HumanReportModal(self.service, case))
                return
            await interaction.response.defer(ephemeral=True)
            try:
                updated = self.service.choose(self.case_id, self.revision, action, user_id=interaction.user.id, role_ids=roles(interaction.user))
            except (WorkflowError, Conflict) as exc:
                text = await say(self.service, case, "error", {"error": str(exc)},
                                 "Use the latest controls in this feedback thread after resolving the error.")
            else:
                text = await say(self.service, updated, "choice_saved", {"choice": action, "saved": True},
                                 "Continue in this thread; the next update will appear here.")
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)
        return callback


class DiscordTransport:
    def __init__(self, service):
        self.service = service
        self._registered = set()
        self._views = {}
        self._intake_locks = weakref.WeakValueDictionary()

    def _intake_lock(self, user_id):
        key = str(user_id)
        lock = self._intake_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._intake_locks[key] = lock
        return lock

    async def _new_message_report(self, message):
        async with self._intake_lock(message.author.id):
            content = message_text(message)
            self.service.check_new_report(message.author.id, content)
            channel = message.channel
            if isinstance(channel, discord.Thread) and channel.owner_id != message.author.id:
                raise WorkflowError("The original reporter must start this feedback thread with the helper.")
            if not isinstance(channel, discord.Thread):
                channel = await message.create_thread(name=(message.content.strip() or "Project feedback")[:90])
            return self.service.receive_report(source_key=f"discord:{message.guild.id}:{message.id}", channel_id=channel.id,
                                               reporter_id=message.author.id, content=content, source_url=message.jump_url,
                                               message_id=message.id, reporter_name=message.author.name, title=channel.name)

    async def _new_command_report(self, interaction, report):
        async with self._intake_lock(interaction.user.id):
            self.service.check_new_report(interaction.user.id, report)
            cfg = self.service.settings()
            channel = await self._channel(cfg["feedback_channel_id"])
            title = report.strip().splitlines()[0][:90]
            quoted = ">>> " + report[:1500]
            original = {"content": f"**@{discord.utils.escape_markdown(interaction.user.name)}**\n{quoted}", "allowed_mentions": NO_MENTIONS}
            if len(report) > 1500:
                original["file"] = discord.File(io.BytesIO(report.encode("utf-8")), filename="feedback.txt")
            if isinstance(channel, discord.ForumChannel):
                created = await channel.create_thread(name=title, **original)
                thread = created.thread
            elif isinstance(channel, discord.TextChannel):
                thread = await channel.create_thread(name=title, type=discord.ChannelType.public_thread)
                await thread.send(**original)
            else:
                raise WorkflowError("The feedback destination must be a forum or text channel.")
            self.service.receive_report(source_key=f"interaction:{interaction.id}", channel_id=thread.id, reporter_id=interaction.user.id,
                                        content=report, source_url=f"https://discord.com/channels/{interaction.guild_id}/{thread.id}",
                                        message_id=interaction.id, reporter_name=interaction.user.name, title=title)
            return thread

    async def attach(self, bot):
        if bot.name not in self._registered:
            self._commands(bot)
            self._registered.add(bot.name)
        for case in self.service.store.list_pending_cases():
            if case["bot_name"] == bot.name and case.get("gate"):
                self._register_view(bot, case)

    def _register_view(self, bot, case, *, view=None, message_id=None):
        previous = self._views.pop(case["id"], None)
        if previous and previous is not view:
            previous.stop()
        view = view or DecisionView(self.service, case)
        link = self.service.store.get_link("feedback", case["id"])
        if message_id is None and link:
            message_id = int(link["message_id"])
        if message_id:
            for item in view.children:
                if item.url:
                    item.url = f"https://discord.com/channels/{case['guild_id']}/{case['channel_id']}/{message_id}"
        bot.client.add_view(view, message_id=message_id)
        self._views[case["id"]] = view
        return view

    def _bot(self):
        return self.service.bots[self.service.settings()["bot_name"]]

    async def _channel(self, channel_id):
        bot = self._bot()
        channel = bot.client.get_channel(int(channel_id)) or await bot.client.fetch_channel(int(channel_id))
        if str(getattr(getattr(channel, "guild", None), "id", "")) != self.service.settings()["guild_id"]:
            raise WorkflowError("A configured channel belongs to a different server.")
        import response_access
        check_id = getattr(channel, "parent_id", None) or channel.id
        if not response_access.message_access(False, 0, check_id)[0]:
            raise WorkflowError("Response access settings block this project channel.")
        return channel

    async def _find_message(self, channel, marker):
        async for message in channel.history(limit=200):
            if message.author.id != self._bot().client.user.id:
                continue
            if any(embed.footer.text == marker for embed in message.embeds):
                return message
        return None

    async def notify(self, case, *, recover_only=False):
        channel = await self._channel(case["channel_id"])
        delivery_key = case.get("delivery_key") or case["id"]
        marker = f"Feedback {delivery_key}"
        link = self.service.store.get_link("feedback_delivery", delivery_key)
        if not link and "delivery_key" not in case:
            link = self.service.store.get_link("feedback", case["id"])
        message = None
        if link and str(link["channel_id"]) == str(channel.id):
            try:
                message = await channel.fetch_message(int(link["message_id"]))
                if message.author.id != self._bot().client.user.id:
                    raise WorkflowError("The saved feedback reply belongs to a different Discord author.")
                if not any(embed.footer.text == marker for embed in message.embeds):
                    message = None
            except discord.NotFound:
                pass
        if message is None:
            message = await self._find_message(channel, marker)
        if message:
            current = self._record_feedback(case, channel, message, delivery_key)
            latest = self.service.get_case(case["id"])
            if (current and case.get("gate") and latest and latest["revision"] == case["revision"]
                    and latest.get("gate") == case["gate"]):
                self._register_view(self._bot(), case, message_id=message.id)
            return True
        if recover_only:
            return False
        reply = case.get("reply")
        if not isinstance(reply, str) or not reply.strip() or len(reply) > 2000:
            raise WorkflowError("The feedback reply is missing or too long. Retry its preparation before sending it.")
        if isinstance(channel, discord.Thread) and channel.archived:
            await channel.edit(archived=False, locked=False)
        draft = case.get("draft") if case["state"] == "awaiting_submission" else None
        if draft:
            embed = discord.Embed(title=draft["title"], description=draft["body"], color=COLOUR)
        else:
            # The footer is a delivery receipt, not a generated state card.
            embed = discord.Embed(color=COLOUR)
        embed.set_footer(text=marker)
        previous = self.service.store.get_link("feedback", case["id"])
        current = not previous or feedback_order(previous) <= feedback_order(case)
        view = DecisionView(self.service, case) if current and case.get("gate") else None
        message = await channel.send(content=reply, embed=embed, view=view, allowed_mentions=NO_MENTIONS)
        self._record_feedback(case, channel, message, delivery_key)
        if view:
            self._register_view(self._bot(), case, view=view, message_id=message.id)
            if any(item.url for item in view.children):
                await message.edit(view=view, allowed_mentions=NO_MENTIONS)
        elif current and case["id"] in self._views:
            self._views.pop(case["id"]).stop()
        if current:
            await self._retire_controls(case, channel, previous, message.id)
        return True

    def _record_feedback(self, case, channel, message, delivery_key):
        receipt = {"channel_id": str(channel.id), "message_id": str(message.id),
                   "revision": case["revision"], "delivery_key": delivery_key,
                   "created_at": discord.utils.snowflake_time(message.id).timestamp()}
        self.service.store.put_link("feedback_delivery", delivery_key, receipt)
        current = self.service.store.get_link("feedback", case["id"])
        if not current or feedback_order(current) <= feedback_order(receipt):
            self.service.store.put_link("feedback", case["id"], receipt)
            return True
        return False

    async def _retire_controls(self, case, channel, previous, message_id):
        if not previous or str(previous["channel_id"]) != str(channel.id) or str(previous["message_id"]) == str(message_id):
            return
        try:
            message = await channel.fetch_message(int(previous["message_id"]))
            if message.author.id != self._bot().client.user.id:
                return
            markers = {f"Feedback {case['id']}", f"Feedback {previous.get('delivery_key', '')}"}
            if any(embed.footer.text in markers for embed in message.embeds):
                # Keep the earlier conversation and exact public preview intact.
                await message.edit(view=None, allowed_mentions=NO_MENTIONS)
        except (discord.HTTPException, OSError) as exc:
            warn("Could not remove older feedback controls.", bot=case["bot_name"],
                 case_id=case["id"], error_type=type(exc).__name__)

    async def handle_message(self, bot, message):
        cfg = self.service.settings()
        if not message.guild or str(message.guild.id) != cfg.get("guild_id"):
            return False
        parent_id = str(getattr(message.channel, "parent_id", None) or message.channel.id)
        reserved = {cfg.get(key) for key in ("feedback_channel_id", "issues_channel_id", "reviews_channel_id", "commits_channel_id", "github_channel_id")}
        if parent_id not in reserved:
            return False
        # All character bots yield these channels to the selected helper, including when paused.
        if bot.name != cfg.get("bot_name") or self.service.paused() or message.author.bot or parent_id != cfg.get("feedback_channel_id"):
            return True
        import response_access
        if not response_access.message_access(False, message.author.id, parent_id)[0]:
            return True
        case = self.service.store.find_case(str(message.channel.id)) if isinstance(message.channel, discord.Thread) else None
        try:
            if case:
                self.service.add_detail(case["id"], user_id=message.author.id, content=message_text(message),
                                        message_id=message.id, source_url=message.jump_url, role_ids=roles(message.author),
                                        username=message.author.name)
            else:
                await self._new_message_report(message)
        except (WorkflowError, Conflict) as exc:
            text = await say(self.service, case, "error", {"error": str(exc)},
                             "Continue in this thread after resolving the error.")
            await message.reply(text, allowed_mentions=NO_MENTIONS, mention_author=False)
        return True

    def _commands(self, bot):
        from commands.registry import register_command_metadata

        @bot.tree.command(name="feedback", description="Start a project feedback report from this channel")
        @app_commands.guild_only()
        @app_commands.describe(report="Describe the problem or desired outcome. This creates a feedback thread.")
        async def feedback(interaction: discord.Interaction, report: str):
            await interaction.response.defer(ephemeral=True)
            cfg = self.service.settings()
            current = str(getattr(interaction.channel, "parent_id", None) or interaction.channel_id)
            if bot.name != cfg.get("bot_name") or str(interaction.guild_id) != cfg.get("guild_id") or current not in {cfg.get("support_channel_id"), cfg.get("feedback_channel_id")}:
                text = await say(self.service, None, "error", {"error": "This command is outside the configured feedback channels."},
                                 "Use the project helper's /feedback command in its support or feedback channel.")
            elif self.service.paused():
                text = await say(self.service, None, "error", {"error": "Project feedback is paused."},
                                 "Ask a project maintainer to resume feedback before starting a report.")
            elif not 1 <= len(report.strip()) <= 7000:
                text = await say(self.service, None, "error", {"error": "The report must contain 1–7,000 characters."},
                                 "Send a shorter description of the problem or desired outcome.")
            else:
                try:
                    thread = await self._new_command_report(interaction, report)
                except (WorkflowError, Conflict) as exc:
                    text = await say(self.service, None, "error", {"error": str(exc)},
                                     "Resolve the error before starting another feedback thread.")
                else:
                    text = await say(self.service, None, "feedback_started", {
                        "thread_url": f"https://discord.com/channels/{interaction.guild_id}/{thread.id}", "published": False,
                    }, "Open this feedback thread and talk through what happened; you will write and approve any public report.")
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)

        @bot.tree.command(name="project-recover", description="Check a delivery that needs recovery without sending it again")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        async def project_recover(interaction: discord.Interaction, job_id: int):
            await interaction.response.defer(ephemeral=True)
            if bot.name != self.service.settings().get("bot_name") or str(interaction.guild_id) != self.service.settings().get("guild_id"):
                text = await say(self.service, None, "error", {"error": "This command belongs to another project helper or server."},
                                 "Use the configured helper in its project server.")
            else:
                try:
                    found = await self.service.recover(job_id, user_id=interaction.user.id, role_ids=roles(interaction.user))
                except (WorkflowError, GitHubError, Conflict) as exc:
                    text = await say(self.service, None, "error", {"error": str(exc), "job_id": job_id},
                                     "Resolve the error before retrying this delivery.")
                else:
                    text = await say(self.service, None, "delivery_recovered", {"job_id": job_id, "found": found, "resent": False},
                                     "No further action is needed." if found else "The delivery remains held; check its destination before any retry.")
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)

        @bot.tree.command(name="project-retry", description="Retry failed work or return a held draft for fresh approval")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.describe(confirmed_not_delivered="Confirm you checked the destination and the held write was not delivered")
        async def project_retry(interaction: discord.Interaction, job_id: int, confirmed_not_delivered: bool = False):
            await interaction.response.defer(ephemeral=True)
            if bot.name != self.service.settings().get("bot_name") or str(interaction.guild_id) != self.service.settings().get("guild_id"):
                text = await say(self.service, None, "error", {"error": "This command belongs to another project helper or server."},
                                 "Use the configured helper in its project server.")
            else:
                try:
                    result = await self.service.retry(job_id, user_id=interaction.user.id, role_ids=roles(interaction.user), confirmed_not_delivered=confirmed_not_delivered)
                except (WorkflowError, GitHubError, Conflict) as exc:
                    text = await say(self.service, None, "error", {"error": str(exc), "job_id": job_id},
                                     "Resolve the error before retrying this delivery.")
                else:
                    text = await say(self.service, None, "delivery_retry", {"job_id": job_id, "result": result},
                                     "Follow the result; any returned report needs a fresh approval before publication.")
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)

        for name, description, audience in (("feedback", "Start project feedback", "user"), ("project-recover", "Inspect uncertain delivery", "maintenance"), ("project-retry", "Resume failed work", "maintenance")):
            register_command_metadata(bot, name=name, description=description, audience=audience)

    async def mirror(self, event, *, recover_only=False):
        cfg = self.service.settings()
        field = {"issue": "issues_channel_id", "pull": "reviews_channel_id", "commit": "commits_channel_id", "general": "github_channel_id"}[event["kind"]]
        key = f"{cfg['guild_id']}:{cfg['bot_name']}:{cfg[field]}:{cfg['repository']}:{event['key']}"
        channel = await self._channel(cfg[field])
        if event["kind"] in {"issue", "pull"} and not isinstance(channel, discord.ForumChannel):
            raise WorkflowError("Issue tracker and review destinations must be forum channels.")
        marker = f"Project {key}"
        link = self.service.store.get_link("mirror", key)
        thread, message = None, None
        if link:
            try:
                thread = await self._channel(link["channel_id"])
                message = await thread.fetch_message(int(link["message_id"]))
                if message.author.id != self._bot().client.user.id:
                    raise WorkflowError("The saved mirror belongs to a different Discord bot.")
            except discord.NotFound:
                thread = message = None
        if message is None and isinstance(channel, discord.ForumChannel):
            # Active posts plus a bounded archived search recover a lost create acknowledgement.
            threads = list(channel.threads)
            async for archived in channel.archived_threads(limit=100):
                threads.append(archived)
            for candidate in threads:
                if candidate.name.endswith(f" · #{event.get('number')}"):
                    try:
                        starter = await candidate.fetch_message(candidate.id)
                    except discord.NotFound:
                        continue
                    if starter.author.id == self._bot().client.user.id and any(embed.footer.text == marker for embed in starter.embeds):
                        thread, message = candidate, starter
                        break
        elif message is None:
            thread = channel
            message = await self._find_message(channel, marker)
        if recover_only:
            if message:
                self.service.store.put_link("mirror", key, {"channel_id": str(thread.id), "message_id": str(message.id), "kind": event["kind"], "number": event.get("number"), "repository": cfg["repository"], "guild_id": cfg["guild_id"], "bot_name": cfg["bot_name"]})
            return message is not None
        state = "Merged" if event.get("merged") else ("Draft" if event.get("draft") and event.get("state") == "open" else str(event.get("state", "Update")).capitalize())
        embed = discord.Embed(title=f"{state} · {event['title']}"[:250], description=event.get("body", "")[:3800],
                              url=event.get("html_url") or event.get("url"), color=COLOUR)
        embed.set_footer(text=marker)
        name = f"{state} · {event['title']}"[:75] + f" · #{event.get('number')}"
        if message:
            if isinstance(thread, discord.Thread) and (thread.archived or (thread.locked and not event.get("closed"))):
                await thread.edit(archived=False, locked=False)
            await message.edit(embed=embed, allowed_mentions=NO_MENTIONS)
            if isinstance(thread, discord.Thread):
                await thread.edit(name=name)
        elif isinstance(channel, discord.ForumChannel):
            created = await channel.create_thread(name=name, embed=embed, allowed_mentions=NO_MENTIONS)
            thread, message = created.thread, created.message
        else:
            thread, message = channel, await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
        self.service.store.put_link("mirror", key, {"channel_id": str(thread.id), "message_id": str(message.id), "kind": event["kind"], "number": event.get("number"), "repository": cfg["repository"], "guild_id": cfg["guild_id"], "bot_name": cfg["bot_name"]})
        if event.get("closed") and isinstance(thread, discord.Thread):
            await thread.edit(archived=True, locked=True)
        return True
