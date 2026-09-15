"""Native Discord buttons, feedback threads, and repository mirrors."""

from __future__ import annotations

import asyncio
import io
import weakref
import discord
from discord import app_commands

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


class DecisionView(discord.ui.View):
    def __init__(self, service, case):
        super().__init__(timeout=None)
        self.service = service
        self.case_id = case["id"]
        self.revision = case["revision"]
        for option in (case.get("gate") or {}).get("options", []):
            action = option["key"]
            button = discord.ui.Button(label=option["label"], custom_id=f"project:{case['id']}:{case['revision']}:{action}",
                                       style=discord.ButtonStyle.primary if action == "submit" else discord.ButtonStyle.secondary)
            button.callback = self._callback(action)
            self.add_item(button)

    def _callback(self, action):
        async def callback(interaction):
            await interaction.response.defer(ephemeral=True)
            case = self.service.get_case(self.case_id)
            if not case or str(interaction.guild_id) != case["guild_id"] or str(interaction.channel_id) != case["channel_id"]:
                await interaction.followup.send("These controls belong to another feedback thread.", ephemeral=True)
                return
            try:
                self.service.choose(self.case_id, self.revision, action, user_id=interaction.user.id, role_ids=roles(interaction.user))
            except (WorkflowError, Conflict) as exc:
                await interaction.followup.send(str(exc), ephemeral=True, allowed_mentions=NO_MENTIONS)
            else:
                await interaction.followup.send("Choice saved. I’ll update this report here.", ephemeral=True)
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
                                               message_id=message.id)

    async def _new_command_report(self, interaction, report):
        async with self._intake_lock(interaction.user.id):
            self.service.check_new_report(interaction.user.id, report)
            cfg = self.service.settings()
            channel = await self._channel(cfg["feedback_channel_id"])
            title = report.strip().splitlines()[0][:90]
            original = {"content": "**Original report**\n" + report[:1750], "allowed_mentions": NO_MENTIONS}
            if len(report) > 1750:
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
                                        message_id=interaction.id)
            return thread

    async def attach(self, bot):
        if bot.name not in self._registered:
            self._commands(bot)
            self._registered.add(bot.name)
        for case in self.service.store.list_pending_cases():
            if case["bot_name"] == bot.name and case.get("gate"):
                self._register_view(bot, case)

    def _register_view(self, bot, case):
        previous = self._views.pop(case["id"], None)
        if previous:
            previous.stop()
        view = DecisionView(self.service, case)
        link = self.service.store.get_link("feedback", case["id"])
        bot.client.add_view(view, message_id=int(link["message_id"]) if link else None)
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
        marker = f"Feedback {case['id']}"
        link = self.service.store.get_link("feedback", case["id"])
        message = None
        if link:
            try:
                message = await channel.fetch_message(int(link["message_id"]))
            except discord.NotFound:
                pass
        if message is None:
            message = await self._find_message(channel, marker)
        if recover_only:
            if message:
                self.service.store.put_link("feedback", case["id"], {"channel_id": str(channel.id), "message_id": str(message.id)})
            return message is not None
        if isinstance(channel, discord.Thread) and channel.archived:
            await channel.edit(archived=False, locked=False)
        assessment = case.get("assessment", {})
        status = case["state"].replace("_", " ").capitalize()
        draft = case.get("draft") if case["state"] == "awaiting_submission" else None
        if draft:
            heading = "Approve issue" if draft["kind"] == "issue" else f"Approve comment on #{draft['issue_number']}"
            embed = discord.Embed(title=draft["title"], description=draft["body"], color=COLOUR)
            content = f"**{heading} · {case['repository']}**\nThis is the complete public draft. Choose Publish to send it to GitHub."
        else:
            text = assessment.get("reply", "") if case["state"] == "awaiting_choice" else case.get("notice", "Preparing your feedback…")
            embed = discord.Embed(title=status, description=text[:3800], color=COLOUR)
            content = None
            if case.get("github_url"):
                embed.add_field(name="GitHub", value=case["github_url"], inline=False)
            remote = self.service.store.get_link("case_status", case["id"])
            if remote:
                embed.add_field(name="Issue status", value=remote["state"].capitalize(), inline=False)
            if case["state"] == "awaiting_choice":
                embed.add_field(name="Suggested next step", value=assessment.get("recommendation", "draft").capitalize(), inline=False)
                for candidate in case.get("candidates", []):
                    if candidate["number"] == assessment.get("duplicate_number"):
                        embed.add_field(name=f"Possible match · #{candidate['number']}", value=f"{candidate['title']}\n{candidate['html_url']}", inline=False)
                if case.get("search_unavailable"):
                    embed.add_field(name="Search unavailable", value="GitHub duplicate checks could not run. A draft can still be reviewed.", inline=False)
        question = self.service.store.get_link("maintainer_question", case["id"])
        if question:
            label = "Maintainer question (outside this draft)" if draft else "Maintainer question · reply in this thread"
            embed.add_field(name=label, value=f"{question['body'][:850]}\n{question['url']}", inline=False)
        embed.set_footer(text=marker)
        view = self._register_view(self._bot(), case) if case.get("gate") else None
        if not view and case["id"] in self._views:
            self._views.pop(case["id"]).stop()
        if message:
            await message.edit(content=content, embed=embed, view=view, allowed_mentions=NO_MENTIONS)
        else:
            message = await channel.send(content=content, embed=embed, view=view, allowed_mentions=NO_MENTIONS)
        self.service.store.put_link("feedback", case["id"], {"channel_id": str(channel.id), "message_id": str(message.id)})
        return True

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
                                        message_id=message.id, source_url=message.jump_url, role_ids=roles(message.author))
            else:
                await self._new_message_report(message)
        except (WorkflowError, Conflict) as exc:
            await message.reply(str(exc), allowed_mentions=NO_MENTIONS, mention_author=False)
        return True

    def _commands(self, bot):
        from commands.registry import register_command_metadata

        @bot.tree.command(name="feedback", description="Start a project feedback report from this channel")
        @app_commands.guild_only()
        @app_commands.describe(report="Describe the problem or desired outcome. This creates a feedback thread.")
        async def feedback(interaction: discord.Interaction, report: str):
            cfg = self.service.settings()
            current = str(getattr(interaction.channel, "parent_id", None) or interaction.channel_id)
            if bot.name != cfg.get("bot_name") or str(interaction.guild_id) != cfg.get("guild_id") or current not in {cfg.get("support_channel_id"), cfg.get("feedback_channel_id")}:
                await interaction.response.send_message("Use the project helper’s /feedback command in its support or feedback channel.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            if self.service.paused():
                await interaction.followup.send("Project feedback is paused.", ephemeral=True)
                return
            if not 1 <= len(report.strip()) <= 7000:
                await interaction.followup.send("Describe your report in 1–7,000 characters.", ephemeral=True)
                return
            try:
                thread = await self._new_command_report(interaction, report)
            except (WorkflowError, Conflict) as exc:
                await interaction.followup.send(str(exc), ephemeral=True, allowed_mentions=NO_MENTIONS)
                return
            await interaction.followup.send(f"Feedback started: {thread.mention}", ephemeral=True, allowed_mentions=NO_MENTIONS)

        @bot.tree.command(name="project-recover", description="Check a delivery that needs recovery without sending it again")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        async def project_recover(interaction: discord.Interaction, job_id: int):
            if bot.name != self.service.settings().get("bot_name") or str(interaction.guild_id) != self.service.settings().get("guild_id"):
                await interaction.response.send_message("Use the configured helper in its project server.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                found = await self.service.recover(job_id, user_id=interaction.user.id, role_ids=roles(interaction.user))
                text = "Existing delivery found and recovered." if found else "No matching delivery found. It remains held; check the destination before any manual resubmission."
            except (WorkflowError, GitHubError, Conflict) as exc:
                text = str(exc)
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)

        @bot.tree.command(name="project-retry", description="Retry failed work or return a held draft for fresh approval")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.describe(confirmed_not_delivered="Confirm you checked the destination and the held write was not delivered")
        async def project_retry(interaction: discord.Interaction, job_id: int, confirmed_not_delivered: bool = False):
            if bot.name != self.service.settings().get("bot_name") or str(interaction.guild_id) != self.service.settings().get("guild_id"):
                await interaction.response.send_message("Use the configured helper in its project server.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                text = await self.service.retry(job_id, user_id=interaction.user.id, role_ids=roles(interaction.user), confirmed_not_delivered=confirmed_not_delivered)
            except (WorkflowError, GitHubError, Conflict) as exc:
                text = str(exc)
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
