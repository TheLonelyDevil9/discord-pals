"""
Discord Pals - Interact Command
Single free-form interaction command that integrates with the message pipeline.
Replaces all individual fun commands (kiss, hug, bonk, etc.) with /interact <action>
"""

import discord
from discord import app_commands

from discord_utils import get_user_display_name


class SyntheticMessage:
    """Lightweight wrapper to simulate a Discord message for the request queue.

    This allows /interact commands to be processed through the normal message
    pipeline, enabling memory generation and consistent handling.
    """

    def __init__(self, interaction: discord.Interaction, content: str):
        self.author = interaction.user
        self.channel = interaction.channel
        self.guild = interaction.guild
        self.content = content
        self.attachments = []
        self.reference = None
        self.mentions = []
        self.stickers = []
        self.id = interaction.id
        self._interaction = interaction
        self._responded = False

    async def reply(self, content: str, **kwargs):
        """Send response via interaction followup."""
        self._responded = True
        return await self._interaction.followup.send(content, **kwargs)


async def handle_interact_command(bot_instance, interaction: discord.Interaction, action: str) -> None:
    """Handle the /interact command by processing through the normal message pipeline.

    This enables:
    - Memory generation for meaningful interactions
    - Consistent conversation history formatting
    - Proper character context and user recognition
    """
    await interaction.response.defer()

    if not bot_instance.character:
        await interaction.followup.send("No character loaded", ephemeral=True)
        return

    user = interaction.user
    user_name = get_user_display_name(user)
    guild = interaction.guild
    channel = interaction.channel

    # Format the action as roleplay - "Username: *action*"
    formatted_action = f"*{action}*"

    # Create a synthetic message for the request queue
    synthetic_message = SyntheticMessage(interaction, formatted_action)

    # Process through the normal pipeline (includes memory generation)
    await bot_instance.request_queue.add_request(
        channel_id=channel.id,
        message=synthetic_message,
        content=formatted_action,
        guild=guild,
        attachments=[],
        user_name=user_name,
        is_dm=isinstance(channel, discord.DMChannel),
        user_id=user.id,
        sticker_info=None,
        from_interact_command=True
    )


def setup_fun_commands(bot_instance) -> None:
    """Register the /interact command."""
    tree = bot_instance.tree

    @tree.command(name="interact", description="Perform an action (e.g., 'hugs you', 'pats your head', 'gives you a cookie')")
    @app_commands.describe(action="What you do (e.g., 'hugs you', 'asks how you feel about me')")
    async def cmd_interact(interaction: discord.Interaction, action: str) -> None:
        await handle_interact_command(bot_instance, interaction, action)
