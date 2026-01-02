"""
Discord Pals - Fun Commands
Interactive fun commands: kiss, hug, bonk, bite, joke, pat, poke, tickle, slap, cuddle,
compliment, roast, fortune, challenge, holdhands, squish, spank, affection
"""

import discord
from discord import app_commands
from typing import Dict

from providers import provider_manager
from memory import memory_manager
from discord_utils import (
    get_history, get_user_display_name,
    remove_thinking_tags, clean_bot_name_prefix, convert_emojis_in_text
)


# Fun command definitions
FUN_COMMANDS: Dict[str, str] = {
    "kiss": "Kiss the bot",
    "hug": "Hug the bot",
    "bonk": "Bonk the bot",
    "bite": "Bite the bot",
    "joke": "Get a joke",
    "pat": "Pat the bot's head",
    "poke": "Poke the bot",
    "tickle": "Tickle the bot",
    "slap": "Slap the bot",
    "cuddle": "Cuddle with the bot",
    "compliment": "Get a compliment",
    "roast": "Get roasted (playfully)",
    "fortune": "Get your fortune told",
    "challenge": "Challenge the bot",
    "holdhands": "Hold hands with the bot",
    "squish": "Squish the bot's face",
    "spank": "Spank the bot",
}

# Prompts for each action
ACTION_PROMPTS: Dict[str, str] = {
    "kiss": "{user} kisses you. React in character with a brief, natural response.",
    "hug": "{user} hugs you. React in character with a brief, natural response.",
    "bonk": "{user} bonks you. React in character with a brief, natural response.",
    "bite": "{user} bites you. React in character with a brief, natural response.",
    "joke": "{user} asks you to tell them a joke. Tell a joke that fits your character.",
    "pat": "{user} pats your head. React in character with a brief, natural response.",
    "poke": "{user} pokes you. React in character with a brief, natural response.",
    "tickle": "{user} tickles you. React in character with a brief, natural response.",
    "slap": "{user} slaps you. React in character with a brief, natural response.",
    "cuddle": "{user} cuddles with you. React in character with a brief, natural response.",
    "compliment": "{user} asks for a compliment. Give them a genuine, in-character compliment.",
    "roast": "{user} asks you to roast them. Give a playful, in-character roast (keep it friendly).",
    "fortune": "{user} asks for their fortune. Give a mystical, in-character fortune reading.",
    "challenge": "{user} challenges you. React with a competitive, in-character response.",
    "holdhands": "{user} holds your hand. React in character with a brief, natural response.",
    "squish": "{user} squishes your face. React in character with a brief, natural response.",
    "spank": "{user} spanks you. React in character with a brief, natural response.",
}


async def handle_fun_command(bot_instance, interaction: discord.Interaction, action: str) -> None:
    """Handle fun interaction commands with relationship context."""
    await interaction.response.defer()
    
    if not bot_instance.character:
        await interaction.followup.send("No character loaded", ephemeral=True)
        return
    
    user_name = get_user_display_name(interaction.user)
    user_id = interaction.user.id
    guild_id = interaction.guild.id if interaction.guild else None
    channel_id = interaction.channel_id
    
    # Get relationship context
    history = get_history(channel_id)
    recent_context = "\n".join([
        f"{m.get('role', 'user')}: {m.get('content', '')[:100]}"
        for m in history[-15:]
    ]) if history else ""
    
    # Get memories
    char_name = bot_instance.character.name if bot_instance.character else None
    user_memories = ""
    server_memories = ""
    if guild_id:
        user_memories = memory_manager.get_user_memories(guild_id, user_id, character_name=char_name)
        server_memories = memory_manager.get_server_memories(guild_id)
    
    # Build context
    context_parts = []
    if user_memories:
        context_parts.append(f"What you remember about {user_name}:\n{user_memories}")
    if server_memories:
        context_parts.append(f"Server context:\n{server_memories}")
    if recent_context:
        context_parts.append(f"Recent conversation:\n{recent_context}")
    
    relationship_context = "\n\n".join(context_parts) if context_parts else "No prior context with this user."
    
    system_prompt = f"""You are {bot_instance.character.name}. Keep your response brief (1-3 sentences).

{relationship_context}

Respond naturally based on your relationship with {user_name}. Consider the history and memories when responding. If you know them well, be warmer. If they're new, be appropriately reserved."""

    action_prompt = ACTION_PROMPTS.get(action, "React naturally.").format(user=user_name)
    
    response = await provider_manager.generate(
        messages=[{"role": "user", "content": action_prompt}],
        system_prompt=system_prompt,
        max_tokens=300
    )
    
    if not response:
        await interaction.followup.send("*no response*")
        return
    
    response = remove_thinking_tags(response)
    response = clean_bot_name_prefix(response, bot_instance.character.name)
    
    if interaction.guild:
        response = convert_emojis_in_text(response, interaction.guild)
    
    await interaction.followup.send(response)


def setup_fun_commands(bot_instance) -> None:
    """Register fun interaction commands."""
    tree = bot_instance.tree
    
    # Dynamically create fun commands
    for cmd_name, cmd_desc in FUN_COMMANDS.items():
        def make_callback(action: str):
            async def callback(interaction: discord.Interaction) -> None:
                await handle_fun_command(bot_instance, interaction, action)
            return callback
        
        cmd = app_commands.Command(
            name=cmd_name,
            description=cmd_desc,
            callback=make_callback(cmd_name)
        )
        tree.add_command(cmd)
    
    # Affection command (special, not in FUN_COMMANDS)
    @tree.command(name="affection", description="Check affection level")
    async def cmd_affection(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        
        if not bot_instance.character:
            await interaction.followup.send("No character loaded", ephemeral=True)
            return
        
        user_name = get_user_display_name(interaction.user)
        user_id = interaction.user.id
        guild_id = interaction.guild.id if interaction.guild else None
        
        # Get chat history
        history = get_history(interaction.channel_id)
        chat_context = "\n".join([
            f"{m.get('role', 'user')}: {m.get('content', '')[:100]}"
            for m in history[-20:]
        ]) if history else ""
        
        # Get memories
        char_name = bot_instance.character.name if bot_instance.character else None
        user_memories = ""
        server_memories = ""
        if guild_id:
            user_memories = memory_manager.get_user_memories(guild_id, user_id, character_name=char_name)
            server_memories = memory_manager.get_server_memories(guild_id)
        
        # Build context
        context_parts = []
        if user_memories:
            context_parts.append(f"What you remember about {user_name}:\n{user_memories}")
        if server_memories:
            context_parts.append(f"Server context:\n{server_memories}")
        if chat_context:
            context_parts.append(f"Recent conversations:\n{chat_context}")
        
        full_context = "\n\n".join(context_parts) if context_parts else "No prior interactions with this user."
        
        system = f"""You are {bot_instance.character.name}. Based on your interactions and memories with {user_name}, give a brief, 
in-character assessment of your affection/feelings toward them. Be genuine and reflect actual 
interactions. Include a rough affection percentage (0-100%) if it fits your character."""
        
        response = await provider_manager.generate(
            messages=[{"role": "user", "content": f"{full_context}\n\nHow do you feel about {user_name}?"}],
            system_prompt=system,
            max_tokens=400
        )
        
        if not response:
            await interaction.followup.send("*couldn't think of a response...*")
            return
        
        response = remove_thinking_tags(response)
        response = clean_bot_name_prefix(response, bot_instance.character.name)
        
        await interaction.followup.send(response)
