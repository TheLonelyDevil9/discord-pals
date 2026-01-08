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
    @app_commands.describe(
        content="Memory to save",
        memory_type="Type of memory (default: auto-detect)",
        user_id="User ID (for user-specific memories, optional)"
    )
    @app_commands.choices(memory_type=[
        app_commands.Choice(name="Auto-detect (default)", value="auto"),
        app_commands.Choice(name="Server Memory", value="server"),
        app_commands.Choice(name="Server Lore", value="lore"),
        app_commands.Choice(name="User Memory", value="user"),
        app_commands.Choice(name="Global User Profile", value="global"),
        app_commands.Choice(name="DM Memory", value="dm")
    ])
    async def cmd_memory(
        interaction: discord.Interaction,
        content: str,
        memory_type: Optional[app_commands.Choice[str]] = None,
        user_id: Optional[str] = None
    ) -> None:
        is_dm = isinstance(interaction.channel, discord.DMChannel)
        char_name = bot_instance.character.name if bot_instance.character else None

        # Default to auto-detect if no memory_type specified
        memory_type_value = memory_type.value if memory_type else "auto"

        # Auto-detect mode (backwards compatible)
        if memory_type_value == "auto":
            if is_dm:
                memory_manager.add_dm_memory(interaction.user.id, content, character_name=char_name)
                await interaction.response.send_message("✅ Memory saved (DM)", ephemeral=True)
            else:
                memory_manager.add_server_memory(interaction.guild_id, content)
                await interaction.response.send_message("✅ Memory saved (Server)", ephemeral=True)
            return

        # Validate context for specific memory types
        if memory_type_value in ["server", "lore", "user"] and is_dm:
            await interaction.response.send_message(
                "❌ This memory type is only available in servers, not DMs.",
                ephemeral=True
            )
            return

        if memory_type_value == "dm" and not is_dm:
            await interaction.response.send_message(
                "❌ DM memories can only be saved from DMs.",
                ephemeral=True
            )
            return

        # Validate user_id for user-specific operations
        if memory_type_value in ["user", "global"] and not user_id:
            await interaction.response.send_message(
                "❌ User ID is required for this memory type.",
                ephemeral=True
            )
            return

        # Save memory based on type
        try:
            if memory_type_value == "server":
                memory_manager.add_server_memory(interaction.guild_id, content)
                await interaction.response.send_message("✅ Server memory saved", ephemeral=True)

            elif memory_type_value == "lore":
                memory_manager.add_lore(interaction.guild_id, content)
                await interaction.response.send_message("✅ Server lore added", ephemeral=True)

            elif memory_type_value == "user":
                memory_manager.add_user_memory(interaction.guild_id, int(user_id), content, character_name=char_name)
                await interaction.response.send_message(f"✅ Memory about user {user_id} saved", ephemeral=True)

            elif memory_type_value == "global":
                memory_manager.add_global_user_profile(int(user_id), content)
                await interaction.response.send_message(f"✅ Global profile for user {user_id} saved", ephemeral=True)

            elif memory_type_value == "dm":
                memory_manager.add_dm_memory(interaction.user.id, content, character_name=char_name)
                await interaction.response.send_message("✅ DM memory saved", ephemeral=True)

            else:
                await interaction.response.send_message("❌ Unknown memory type", ephemeral=True)

        except ValueError:
            await interaction.response.send_message("❌ Invalid user ID format", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error saving memory: {str(e)}", ephemeral=True)
    
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

    @tree.command(name="clearmemories", description="Clear saved memories")
    @app_commands.describe(
        memory_type="Type of memories to clear",
        user_id="User ID (for user-specific memories, optional)"
    )
    @app_commands.choices(memory_type=[
        app_commands.Choice(name="Server Memories", value="server"),
        app_commands.Choice(name="Server Lore", value="lore"),
        app_commands.Choice(name="DM Memories", value="dm"),
        app_commands.Choice(name="User Memories", value="user"),
        app_commands.Choice(name="Global User Profile", value="global")
    ])
    async def cmd_clearmemories(
        interaction: discord.Interaction,
        memory_type: app_commands.Choice[str],
        user_id: Optional[str] = None
    ) -> None:
        is_dm = isinstance(interaction.channel, discord.DMChannel)
        char_name = bot_instance.character.name if bot_instance.character else None
        memory_type_value = memory_type.value

        # Validate context
        if memory_type_value in ["server", "lore", "user"] and is_dm:
            await interaction.response.send_message(
                "❌ This memory type is only available in servers, not DMs.",
                ephemeral=True
            )
            return

        if memory_type_value == "dm" and not is_dm:
            await interaction.response.send_message(
                "❌ DM memories can only be cleared from DMs.",
                ephemeral=True
            )
            return

        # Validate user_id for user-specific operations
        if memory_type_value in ["user", "global"] and not user_id:
            await interaction.response.send_message(
                "❌ User ID is required for this memory type.",
                ephemeral=True
            )
            return

        # Check permissions for server-wide operations
        if memory_type_value in ["server", "lore"] and not is_dm:
            if not interaction.user.guild_permissions.manage_guild:
                await interaction.response.send_message(
                    "❌ You need 'Manage Server' permission to clear server-wide memories.",
                    ephemeral=True
                )
                return

        # Build confirmation message
        if memory_type_value == "server":
            confirm_msg = f"⚠️ Clear ALL server memories for this server?\n\nThis will delete all shared memories. This action cannot be undone."
        elif memory_type_value == "lore":
            confirm_msg = f"⚠️ Clear ALL server lore for this server?\n\nThis will delete all world-building information. This action cannot be undone."
        elif memory_type_value == "dm":
            confirm_msg = f"⚠️ Clear ALL your DM memories with {char_name}?\n\nThis will delete your entire conversation history with this character. This action cannot be undone."
        elif memory_type_value == "user":
            confirm_msg = f"⚠️ Clear memories about user {user_id}?\n\nThis will delete all memories about this user in this server. This action cannot be undone."
        elif memory_type_value == "global":
            confirm_msg = f"⚠️ Clear global profile for user {user_id}?\n\nThis will delete cross-server facts about this user. This action cannot be undone."
        else:
            confirm_msg = "⚠️ Are you sure you want to clear these memories?"

        # Create confirmation view with buttons
        class ConfirmView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)
                self.value = None

            @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.danger)
            async def confirm(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                if button_interaction.user.id != interaction.user.id:
                    await button_interaction.response.send_message("❌ Only the command user can confirm.", ephemeral=True)
                    return

                self.value = True
                self.stop()

                # Perform the clear operation
                try:
                    if memory_type_value == "server":
                        memory_manager.clear_server_memories(interaction.guild_id)
                        result_msg = "✅ Server memories cleared."
                    elif memory_type_value == "lore":
                        memory_manager.clear_lore(interaction.guild_id)
                        result_msg = "✅ Server lore cleared."
                    elif memory_type_value == "dm":
                        memory_manager.clear_dm_memories(interaction.user.id, character_name=char_name)
                        result_msg = "✅ DM memories cleared."
                    elif memory_type_value == "user":
                        memory_manager.clear_user_memories(interaction.guild_id, int(user_id), character_name=char_name)
                        result_msg = f"✅ Memories about user {user_id} cleared."
                    elif memory_type_value == "global":
                        memory_manager.clear_global_user_profile(int(user_id))
                        result_msg = f"✅ Global profile for user {user_id} cleared."
                    else:
                        result_msg = "❌ Unknown memory type."

                    await button_interaction.response.edit_message(content=result_msg, view=None)
                except Exception as e:
                    await button_interaction.response.edit_message(
                        content=f"❌ Error clearing memories: {str(e)}",
                        view=None
                    )

            @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                if button_interaction.user.id != interaction.user.id:
                    await button_interaction.response.send_message("❌ Only the command user can cancel.", ephemeral=True)
                    return

                self.value = False
                self.stop()
                await button_interaction.response.edit_message(content="❌ Cancelled.", view=None)

        view = ConfirmView()
        await interaction.response.send_message(confirm_msg, view=view, ephemeral=True)
