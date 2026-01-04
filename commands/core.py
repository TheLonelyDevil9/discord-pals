"""
Discord Pals - Core Commands
Essential bot management commands: reload, switch, clear, recall, status, autonomous, stop, delete_messages
"""

import discord
from discord import app_commands
from typing import Optional

from discord_utils import clear_history, remove_assistant_from_history
from character import character_manager
import logger as log


async def is_owner(interaction: discord.Interaction) -> bool:
    """Check if the user is the application owner."""
    app_info = await interaction.client.application_info()
    if app_info.team:
        # If the bot is owned by a team, check if user is a team member
        return interaction.user.id in [m.id for m in app_info.team.members]
    return interaction.user.id == app_info.owner.id


def setup_core_commands(bot_instance) -> None:
    """Register core bot management commands."""
    tree = bot_instance.tree
    
    @tree.command(name="reload", description="Reload character from file")
    async def cmd_reload(interaction: discord.Interaction) -> None:
        if bot_instance.character:
            bot_instance.character = character_manager.load(bot_instance.character_name)
            await interaction.response.send_message(
                f"✅ Reloaded **{bot_instance.character.name}**", ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ No character loaded", ephemeral=True)
    
    @tree.command(name="switch", description="Switch to a different character")
    @app_commands.describe(character="Character name (leave empty to list available)")
    async def cmd_switch(interaction: discord.Interaction, character: Optional[str] = None) -> None:
        available = character_manager.list_available()
        
        if not character:
            if available:
                current = bot_instance.character.name if bot_instance.character else "None"
                char_list = "\n".join([
                    f"• **{c}**" + (" ← current" if c.lower() == current.lower() else "")
                    for c in available
                ])
                await interaction.response.send_message(
                    f"**Available Characters:**\n{char_list}\n\nUse `/switch <name>` to switch.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ No characters found in `characters/` folder", ephemeral=True
                )
            return
        
        # Find matching character (case-insensitive)
        match = next((c for c in available if c.lower() == character.lower()), None)
        
        if not match:
            await interaction.response.send_message(
                f"❌ Character '{character}' not found. Use `/switch` to see available.",
                ephemeral=True
            )
            return
        
        new_char = character_manager.load(match)
        if new_char:
            bot_instance.character = new_char
            bot_instance.character_name = match
            await interaction.response.send_message(
                f"✅ Switched to **{new_char.name}**", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Failed to load '{match}'", ephemeral=True
            )
    
    @tree.command(name="clear", description="Clear conversation history")
    async def cmd_clear(interaction: discord.Interaction) -> None:
        clear_history(interaction.channel_id)
        await interaction.response.send_message("✅ History cleared", ephemeral=True)
    
    @tree.command(name="recall", description="Load recent Discord messages into context")
    @app_commands.describe(count="Number of messages to recall (1-200)")
    async def cmd_recall(interaction: discord.Interaction, count: int = 20) -> None:
        count = max(1, min(200, count))
        await interaction.response.defer(ephemeral=True)
        loaded = await bot_instance._recall_channel_history(interaction.channel, count)
        await interaction.followup.send(f"✅ Loaded {loaded} messages into context", ephemeral=True)
    
    @tree.command(name="status", description="Check bot status")
    async def cmd_status(interaction: discord.Interaction) -> None:
        from providers import provider_manager
        char_name = bot_instance.character.name if bot_instance.character else "None"
        provider_status = provider_manager.get_status()
        msg = f"**Bot:** {bot_instance.name}\n**Character:** {char_name}\n\n{provider_status}"
        await interaction.response.send_message(msg, ephemeral=True)
    
    @tree.command(name="autonomous", description="Toggle autonomous responses (owner only)")
    @app_commands.describe(
        enabled="Enable or disable autonomous mode",
        chance="Response chance % (default: 5)",
        cooldown="Cooldown in minutes (default: 2)"
    )
    async def cmd_autonomous(
        interaction: discord.Interaction,
        enabled: bool,
        chance: int = 5,
        cooldown: int = 2
    ) -> None:
        # Owner-only check
        if not await is_owner(interaction):
            await interaction.response.send_message(
                "❌ Only the bot owner can use this command", ephemeral=True
            )
            return

        from discord_utils import autonomous_manager

        if isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("Autonomous mode is server-only", ephemeral=True)
            return

        if enabled:
            decimal_chance = min(100, max(1, chance)) / 100.0
            cooldown_mins = min(10, max(1, cooldown))
            autonomous_manager.set_channel(interaction.channel_id, True, decimal_chance, cooldown_mins)
            await interaction.response.send_message(
                f"✅ Autonomous mode ON ({chance}% chance, {cooldown_mins}min cooldown)",
                ephemeral=True
            )
        else:
            autonomous_manager.set_channel(interaction.channel_id, False)
            await interaction.response.send_message("✅ Autonomous mode OFF", ephemeral=True)
    
    @tree.command(name="stop", description="Pause/resume bot-to-bot conversations globally")
    @app_commands.describe(enable="True to pause bot interactions, False to resume (omit to toggle)")
    async def cmd_stop(interaction: discord.Interaction, enable: Optional[bool] = None) -> None:
        import runtime_config
        current = runtime_config.get("bot_interactions_paused", False)
        new_value = not current if enable is None else enable
        runtime_config.set("bot_interactions_paused", new_value)
        
        if new_value:
            await interaction.response.send_message(
                "🛑 Bot-to-bot interactions **PAUSED** globally", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "▶️ Bot-to-bot interactions **RESUMED**", ephemeral=True
            )
    
    @tree.command(name="pause", description="⚠️ KILLSWITCH: Pause/resume ALL bot activity globally (owner only)")
    @app_commands.describe(enable="True to pause all activity, False to resume (omit to toggle)")
    async def cmd_pause(interaction: discord.Interaction, enable: Optional[bool] = None) -> None:
        # Owner-only check
        if not await is_owner(interaction):
            await interaction.response.send_message(
                "❌ Only the bot owner can use this command", ephemeral=True
            )
            return

        import runtime_config
        current = runtime_config.get("global_paused", False)
        new_value = not current if enable is None else enable
        runtime_config.set("global_paused", new_value)

        if new_value:
            await interaction.response.send_message(
                "🛑 **KILLSWITCH ACTIVE** - All bot responses PAUSED globally", ephemeral=False
            )
            log.warn("Global killswitch activated", bot_instance.name)
        else:
            await interaction.response.send_message(
                "✅ **KILLSWITCH RELEASED** - Bot responses RESUMED", ephemeral=False
            )
            log.ok("Global killswitch released", bot_instance.name)
    
    @tree.command(name="delete_messages", description="Delete bot's last N messages")
    @app_commands.describe(count="Number of messages to delete (1-20)")
    async def cmd_delete_messages(interaction: discord.Interaction, count: int = 1) -> None:
        count = max(1, min(20, count))
        await interaction.response.defer(ephemeral=True)
        
        deleted = 0
        async for msg in interaction.channel.history(limit=100):
            if msg.author == bot_instance.client.user:
                try:
                    await msg.delete()
                    deleted += 1
                    remove_assistant_from_history(interaction.channel_id, 1)
                    if deleted >= count:
                        break
                except Exception as e:
                    log.debug(f"Failed to delete message: {e}", bot_instance.name)
        
        await interaction.followup.send(f"✅ Deleted {deleted} messages", ephemeral=True)
