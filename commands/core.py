"""
Discord Pals - Core Commands
Essential bot management commands: reload, switch, clear, recall, status, autonomous, stop, delete_messages, ignore
"""

import discord
from discord import app_commands
from typing import Optional, Set
import re

from discord_utils import clear_history, remove_assistant_from_history
from character import character_manager
import logger as log
import user_ignores


# Cache for owner/team member IDs (populated on first check)
_owner_ids_cache: Set[int] = set()
_owner_cache_populated: bool = False


async def is_owner(interaction: discord.Interaction) -> bool:
    """Check if the user is the application owner (cached after first call)."""
    global _owner_ids_cache, _owner_cache_populated

    if not _owner_cache_populated:
        app_info = await interaction.client.application_info()
        if app_info.team:
            _owner_ids_cache = {m.id for m in app_info.team.members}
        else:
            _owner_ids_cache = {app_info.owner.id}
        _owner_cache_populated = True

    return interaction.user.id in _owner_ids_cache


def setup_core_commands(bot_instance) -> None:
    """Register core bot management commands."""
    tree = bot_instance.tree

    def _normalize_name(value: str) -> str:
        if not isinstance(value, str):
            return ""
        return re.sub(r'\s+', ' ', value.strip().lstrip('@')).lower()

    def _canonical_name(value: str) -> str:
        return re.sub(r'[^a-z0-9]+', '', _normalize_name(value))

    def _resolve_character_name(raw_name: str) -> str:
        """Map user input to a canonical character name when possible."""
        cleaned = re.sub(r'\s+', ' ', (raw_name or "").strip())
        if not cleaned:
            return ""

        known_names = set(character_manager.list_available())
        if bot_instance.character and bot_instance.character.name:
            known_names.add(bot_instance.character.name)
        if not known_names:
            return cleaned

        cleaned_norm = _normalize_name(cleaned)
        cleaned_can = _canonical_name(cleaned)

        # Exact (case-insensitive) first.
        for name in known_names:
            if _normalize_name(name) == cleaned_norm:
                return name

        # Canonical exact fallback.
        canonical_matches = [name for name in known_names if _canonical_name(name) == cleaned_can]
        if len(canonical_matches) == 1:
            return canonical_matches[0]

        # Unique prefix fallback.
        prefix_matches = []
        for name in known_names:
            norm = _normalize_name(name)
            can = _canonical_name(name)
            if norm.startswith(cleaned_norm) or (cleaned_can and can.startswith(cleaned_can)):
                prefix_matches.append(name)
        if len(prefix_matches) == 1:
            return prefix_matches[0]

        return cleaned
    
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

    @tree.command(name="ignore", description="Block a bot from responding to you")
    @app_commands.describe(bot_name="Name of the bot/character to ignore")
    async def cmd_ignore(interaction: discord.Interaction, bot_name: str) -> None:
        user_id = str(interaction.user.id)
        target_name = _resolve_character_name(bot_name)
        if not target_name:
            await interaction.response.send_message(
                "❌ Please provide a bot/character name to ignore.", ephemeral=True
            )
            return

        if user_ignores.add_ignore(user_id, target_name):
            await interaction.response.send_message(
                f"✅ **{target_name}** will no longer respond to your messages.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"ℹ️ You're already ignoring **{target_name}**.", ephemeral=True
            )

    @tree.command(name="unignore", description="Allow a bot to respond to you again")
    @app_commands.describe(bot_name="Name of the bot/character to unignore")
    async def cmd_unignore(interaction: discord.Interaction, bot_name: str) -> None:
        user_id = str(interaction.user.id)
        requested_name = _resolve_character_name(bot_name)
        if not requested_name:
            await interaction.response.send_message(
                "❌ Please provide a bot/character name to unignore.", ephemeral=True
            )
            return

        matched_name, suggestions = user_ignores.find_best_ignore_match(user_id, requested_name)
        if matched_name and user_ignores.remove_ignore(user_id, matched_name):
            await interaction.response.send_message(
                f"✅ **{matched_name}** can now respond to your messages again.", ephemeral=True
            )
        else:
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\nDid you mean: " + ", ".join([f"**{s}**" for s in suggestions])
            await interaction.response.send_message(
                f"ℹ️ You weren't ignoring **{requested_name}**.{suggestion_text}", ephemeral=True
            )

    @tree.command(name="ignorelist", description="Show which bots you're ignoring")
    async def cmd_ignorelist(interaction: discord.Interaction) -> None:
        user_id = str(interaction.user.id)
        ignores = user_ignores.get_ignores(user_id)

        if ignores:
            ignore_list = "\n".join([f"• **{name}**" for name in sorted(ignores)])
            await interaction.response.send_message(
                f"**Bots you're ignoring:**\n{ignore_list}\n\nUse `/unignore <name>` to allow responses again.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "You're not ignoring any bots. Use `/ignore <name>` to block a bot from responding to you.",
                ephemeral=True
            )
