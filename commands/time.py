"""
Discord Pals - Time and Reminder Commands
User timezone management and reminder inspection/cancellation.
"""

from __future__ import annotations

import discord
from discord import app_commands

from reminders import reminder_manager
from time_utils import get_context_now, get_timezone_context, timezone_manager


def setup_time_commands(bot_instance) -> None:
    """Register timezone and reminder slash commands."""
    tree = bot_instance.tree

    timezone_group = app_commands.Group(name="timezone", description="Manage your personal timezone")
    reminders_group = app_commands.Group(name="reminders", description="View or cancel your reminders")

    @timezone_group.command(name="set", description="Set your personal timezone")
    @app_commands.describe(timezone_name="IANA timezone, e.g. Asia/Calcutta or America/New_York")
    async def timezone_set(interaction: discord.Interaction, timezone_name: str) -> None:
        try:
            normalized = timezone_manager.set_user_timezone(interaction.user.id, timezone_name)
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid timezone. Use an IANA name like `Asia/Calcutta` or `America/New_York`.",
                ephemeral=True
            )
            return

        local_now = get_context_now(user_id=interaction.user.id, bot_name=bot_instance.name)
        await interaction.response.send_message(
            f"✅ Your timezone is now **{normalized}**.\n"
            f"Current local time: {local_now.strftime('%A, %Y-%m-%d at %I:%M %p').replace(' 0', ' ')}",
            ephemeral=True
        )

    @timezone_group.command(name="show", description="Show your effective timezone")
    async def timezone_show(interaction: discord.Interaction) -> None:
        context = get_timezone_context(user_id=interaction.user.id, bot_name=bot_instance.name)
        local_now = get_context_now(user_id=interaction.user.id, bot_name=bot_instance.name)
        await interaction.response.send_message(
            f"**Effective timezone:** {context['timezone_name']}\n"
            f"**Source:** {context['timezone_source']}\n"
            f"**Local time:** {local_now.strftime('%A, %Y-%m-%d at %I:%M %p').replace(' 0', ' ')}",
            ephemeral=True
        )

    @timezone_group.command(name="clear", description="Clear your personal timezone override")
    async def timezone_clear(interaction: discord.Interaction) -> None:
        removed = timezone_manager.clear_user_timezone(interaction.user.id)
        context = get_timezone_context(user_id=interaction.user.id, bot_name=bot_instance.name)
        if removed:
            await interaction.response.send_message(
                f"✅ Cleared your personal timezone. Falling back to **{context['timezone_name']}** ({context['timezone_source']}).",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"ℹ️ You did not have a personal timezone set. Effective timezone is **{context['timezone_name']}** ({context['timezone_source']}).",
                ephemeral=True
            )

    @reminders_group.command(name="list", description="List your pending reminders for this bot")
    async def reminders_list(interaction: discord.Interaction) -> None:
        reminders = reminder_manager.list_reminders(
            bot_name=bot_instance.name,
            user_id=interaction.user.id,
            status="pending"
        )
        if not reminders:
            await interaction.response.send_message("No pending reminders for this bot.", ephemeral=True)
            return

        lines = []
        for reminder in reminders[:15]:
            line = (
                f"• `{reminder['id']}` {reminder['event_summary']} "
                f"at {reminder['due_display']}"
            )
            if reminder.get("pre_due_display"):
                line += f" | pre: {reminder['pre_due_display']}"
            lines.append(line)

        await interaction.response.send_message(
            "**Pending reminders:**\n" + "\n".join(lines),
            ephemeral=True
        )

    @reminders_group.command(name="cancel", description="Cancel one of your pending reminders")
    @app_commands.describe(reminder_id="Reminder ID from /reminders list")
    async def reminders_cancel(interaction: discord.Interaction, reminder_id: str) -> None:
        cancelled = reminder_manager.cancel_reminders(
            [reminder_id],
            bot_name=bot_instance.name,
            target_user_id=interaction.user.id,
        )
        if cancelled:
            await interaction.response.send_message(f"✅ Cancelled reminder `{reminder_id}`.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"❌ No pending reminder `{reminder_id}` was found for this bot.",
                ephemeral=True
            )

    tree.add_command(timezone_group)
    tree.add_command(reminders_group)
