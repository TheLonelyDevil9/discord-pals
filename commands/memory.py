"""
Discord Pals - Memory Commands
Memory management commands: memory, memories, lore
"""

import discord
from discord import app_commands
from typing import Optional

from memory import memory_manager


def setup_memory_commands(bot_instance) -> None:
    """Register memory management commands."""
    tree = bot_instance.tree

    @tree.command(name="memory", description="Save a memory about yourself or a user")
    @app_commands.describe(
        content="Memory to save",
        user_id="User ID (for user-specific memories, optional)"
    )
    async def cmd_memory(
        interaction: discord.Interaction,
        content: str,
        user_id: Optional[str] = None
    ) -> None:
        is_dm = isinstance(interaction.channel, discord.DMChannel)
        char_name = bot_instance.character.name if bot_instance.character else None
        server_id = interaction.guild_id if not is_dm else 0
        target_user_id = int(user_id) if user_id else interaction.user.id
        user_name = interaction.user.display_name
        server_name = interaction.guild.name if interaction.guild else "DM"

        try:
            added = memory_manager.add_auto_memory(
                server_id=server_id,
                user_id=target_user_id,
                content=content,
                character_name=char_name,
                user_name=user_name,
                server_name=server_name
            )
            if added:
                await interaction.response.send_message("Memory saved", ephemeral=True)
            else:
                await interaction.response.send_message("Duplicate memory — already saved", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error saving memory: {str(e)}", ephemeral=True)

    @tree.command(name="memories", description="View saved memories")
    async def cmd_memories(interaction: discord.Interaction) -> None:
        is_dm = isinstance(interaction.channel, discord.DMChannel)
        server_id = interaction.guild_id if not is_dm else 0
        user_id = interaction.user.id

        memories = memory_manager.get_auto_memories(server_id, user_id, limit=15)

        if memories:
            await interaction.response.send_message(
                f"**Your Memories:**\n{memories[:1900]}", ephemeral=True
            )
        else:
            await interaction.response.send_message("No memories saved yet.", ephemeral=True)

    @tree.command(name="lore", description="Add/view lore")
    @app_commands.describe(
        content="Lore to add (empty to view current lore)",
        target_type="What this lore is about",
        target_id="Target ID or name (server ID, user ID, or bot name)"
    )
    @app_commands.choices(target_type=[
        app_commands.Choice(name="Server (default)", value="server"),
        app_commands.Choice(name="User", value="user"),
        app_commands.Choice(name="Bot", value="bot"),
    ])
    async def cmd_lore(
        interaction: discord.Interaction,
        content: Optional[str] = None,
        target_type: Optional[app_commands.Choice[str]] = None,
        target_id: Optional[str] = None
    ) -> None:
        lore_type = target_type.value if target_type else "server"
        tid = target_id

        # Default target based on type
        if not tid:
            if lore_type == "server":
                if isinstance(interaction.channel, discord.DMChannel):
                    await interaction.response.send_message("Server lore requires a server context", ephemeral=True)
                    return
                tid = str(interaction.guild_id)
            else:
                await interaction.response.send_message("Please specify a target_id", ephemeral=True)
                return

        if content:
            added = memory_manager.add_lore(lore_type, tid, content, added_by=interaction.user.display_name)
            if added:
                await interaction.response.send_message(f"Lore added ({lore_type}: {tid})", ephemeral=True)
            else:
                await interaction.response.send_message("Duplicate lore entry", ephemeral=True)
        else:
            if lore_type == "server":
                lore = memory_manager.get_server_lore(int(tid))
            elif lore_type == "user":
                lore = memory_manager.get_user_lore(int(tid))
            elif lore_type == "bot":
                lore = memory_manager.get_bot_lore(tid)
            else:
                lore = ""

            if lore:
                await interaction.response.send_message(
                    f"**Lore ({lore_type}: {tid}):**\n{lore[:1900]}", ephemeral=True
                )
            else:
                await interaction.response.send_message(f"No lore set for {lore_type}: {tid}", ephemeral=True)

    @tree.command(name="clearmemories", description="Clear saved memories")
    @app_commands.describe(
        memory_type="Type of memories to clear",
        target_id="Target ID (user ID, server ID, or bot name)"
    )
    @app_commands.choices(memory_type=[
        app_commands.Choice(name="My Memories (this server/DM)", value="auto"),
        app_commands.Choice(name="Server Lore", value="server_lore"),
        app_commands.Choice(name="User Lore", value="user_lore"),
        app_commands.Choice(name="Bot Lore", value="bot_lore")
    ])
    async def cmd_clearmemories(
        interaction: discord.Interaction,
        memory_type: app_commands.Choice[str],
        target_id: Optional[str] = None
    ) -> None:
        is_dm = isinstance(interaction.channel, discord.DMChannel)
        memory_type_value = memory_type.value

        # Build the key and clear
        try:
            if memory_type_value == "auto":
                server_id = interaction.guild_id if not is_dm else 0
                key = memory_manager._auto_key(server_id, interaction.user.id)
                memory_manager.clear_auto_memories(key)
                await interaction.response.send_message("Memories cleared", ephemeral=True)
            elif memory_type_value == "server_lore":
                if is_dm:
                    await interaction.response.send_message("Server lore requires a server context", ephemeral=True)
                    return
                key = memory_manager._server_lore_key(interaction.guild_id)
                memory_manager.clear_lore(key)
                await interaction.response.send_message("Server lore cleared", ephemeral=True)
            elif memory_type_value == "user_lore":
                tid = int(target_id) if target_id else interaction.user.id
                key = memory_manager._user_lore_key(tid)
                memory_manager.clear_lore(key)
                await interaction.response.send_message(f"User lore cleared for {tid}", ephemeral=True)
            elif memory_type_value == "bot_lore":
                if not target_id:
                    await interaction.response.send_message("Please specify a bot name as target_id", ephemeral=True)
                    return
                key = memory_manager._bot_lore_key(target_id)
                memory_manager.clear_lore(key)
                await interaction.response.send_message(f"Bot lore cleared for {target_id}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)
