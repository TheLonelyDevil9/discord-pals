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
    
    @tree.command(name="memory", description="Save a memory")
    @app_commands.describe(content="Memory to save")
    async def cmd_memory(interaction: discord.Interaction, content: str) -> None:
        is_dm = isinstance(interaction.channel, discord.DMChannel)
        if is_dm:
            memory_manager.add_dm_memory(interaction.user.id, content)
        else:
            memory_manager.add_server_memory(interaction.guild_id, content)
        await interaction.response.send_message("✅ Memory saved", ephemeral=True)
    
    @tree.command(name="memories", description="View saved memories")
    async def cmd_memories(interaction: discord.Interaction) -> None:
        is_dm = isinstance(interaction.channel, discord.DMChannel)
        char_name = bot_instance.character.name if bot_instance.character else None
        
        if is_dm:
            memories = memory_manager.get_dm_memories(interaction.user.id, character_name=char_name)
        else:
            memories = memory_manager.get_server_memories(interaction.guild_id)
        
        if memories:
            await interaction.response.send_message(
                f"**Memories:**\n{memories[:1900]}", ephemeral=True
            )
        else:
            await interaction.response.send_message("No memories saved yet.", ephemeral=True)
    
    @tree.command(name="lore", description="Add/view server lore")
    @app_commands.describe(content="Lore to add (empty to view)")
    async def cmd_lore(interaction: discord.Interaction, content: Optional[str] = None) -> None:
        if isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("Lore is server-only", ephemeral=True)
            return
        
        if content:
            memory_manager.add_lore(interaction.guild_id, content)
            await interaction.response.send_message("✅ Lore added", ephemeral=True)
        else:
            lore = memory_manager.get_lore(interaction.guild_id)
            if lore:
                await interaction.response.send_message(
                    f"**Server Lore:**\n{lore[:1900]}", ephemeral=True
                )
            else:
                await interaction.response.send_message("No lore set.", ephemeral=True)
