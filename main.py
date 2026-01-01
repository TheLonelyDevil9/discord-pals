"""
Discord Pals - Main Discord Client
Multi-bot architecture: Runs multiple Discord clients from one process.
"""

import discord
from discord import app_commands
import asyncio
import random
import re
import json
import os
from typing import Optional, List, Dict
import time
import logging

# Suppress verbose logging from all libraries
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)
logging.getLogger('discord.gateway').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('openai._base_client').setLevel(logging.WARNING)

from config import (
    DISCORD_TOKEN, DEFAULT_CHARACTER, ERROR_DELETE_AFTER,
    DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS
)
from providers import provider_manager
from character import character_manager, Character
from memory import memory_manager, ensure_data_dir
from discord_utils import (
    get_history, add_to_history, clear_history, format_history_for_ai,
    get_guild_emojis, parse_reactions, add_reactions, convert_emojis_in_text,
    process_attachments, autonomous_manager, get_active_users,
    get_reply_context, get_user_display_name, get_sticker_info,
    remove_thinking_tags, clean_bot_name_prefix, clean_em_dashes,
    update_history_on_edit, remove_assistant_from_history, store_multipart_response
)
from request_queue import RequestQueue
from stats import stats_manager
import logger as log


# --- Bot Instance Class ---

class BotInstance:
    """Encapsulates a single Discord bot with its own client, character, and state."""
    
    def __init__(self, name: str, token: str, character_name: str):
        self.name = name
        self.token = token
        self.character_name = character_name
        self.character: Optional[Character] = None
        
        # Create intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.emojis = True
        
        # Create client and tree
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)
        
        # Per-bot state
        self.request_queue = RequestQueue()
        self._last_bot_response: Dict[int, float] = {}  # channel_id -> timestamp
        self._channel_mood: Dict[int, float] = {}  # channel_id -> mood score (-1 to 1)
        self._message_batches: Dict[tuple, dict] = {}  # (channel_id, user_id) -> {messages, timer_task}
        
        # Set up events and commands
        self._setup_events()
        self._setup_commands()
    
    def _setup_events(self):
        """Register event handlers."""
        
        @self.client.event
        async def on_ready():
            ensure_data_dir()
            
            # Load character
            self.character = character_manager.load(self.character_name)
            if self.character:
                log.ok(f"Loaded character: {self.character.name}", self.name)
            else:
                log.warn(f"Character '{self.character_name}' not found!", self.name)
                available = character_manager.list_available()
                if available:
                    self.character = character_manager.load(available[0])
                    log.info(f"Loaded fallback: {self.character.name}", self.name)
            
            # Set up request processor
            self.request_queue.set_processor(self._process_request)
            
            # Sync commands
            try:
                synced = await self.tree.sync()
                log.ok(f"Synced {len(synced)} commands", self.name)
            except Exception as e:
                log.error(f"Command sync failed: {e}", self.name)
            
            log.online(f"{self.client.user} is online!", self.name)
        
        @self.client.event
        async def on_message(message: discord.Message):
            if message.author == self.client.user:
                return
            if message.content.startswith('/'):  # Slash commands and // OOC messages
                return
            
            is_dm = isinstance(message.channel, discord.DMChannel)
            is_other_bot = message.author.bot
            
            user_name = get_user_display_name(message.author)
            reply_to_name = get_reply_context(message)
            sticker_info = get_sticker_info(message) if not is_other_bot else None
            
            guild = message.guild
            guild_id = guild.id if guild else None
            
            # Bot chain prevention - don't respond to bots if we recently responded to a bot
            if is_other_bot:
                # Global stop for bot-bot interactions
                import runtime_config
                if runtime_config.get("bot_interactions_paused", False):
                    add_to_history(message.channel.id, "user", message.content, author_name=user_name)
                    return
                
                last_bot_response = self._last_bot_response.get(message.channel.id, 0)
                if time.time() - last_bot_response < 60:  # 60 second cooldown for bot chains
                    add_to_history(message.channel.id, "user", message.content, author_name=user_name)
                    return
            
            mentioned = self.client.user in message.mentions if not is_dm else True
            
            # Reply chain detection - treat reply to bot's message as implicit mention
            is_reply_to_bot = False
            if message.reference and message.reference.message_id:
                try:
                    ref_msg = message.reference.cached_message
                    if ref_msg and ref_msg.author == self.client.user:
                        is_reply_to_bot = True
                except Exception:
                    pass
            
            is_autonomous = not mentioned and not is_reply_to_bot and not is_dm and autonomous_manager.should_respond(message.channel.id)
            should_respond = mentioned or is_reply_to_bot or is_autonomous
            
            if should_respond:
                # Track if responding to a bot
                if is_other_bot:
                    self._last_bot_response[message.channel.id] = time.time()
                
                if sticker_info:
                    content = f"{user_name} {sticker_info}"
                else:
                    if is_other_bot:
                        content = message.content.strip()
                    else:
                        bot_name = guild.me.display_name if guild else self.client.user.display_name
                        content = message.content.replace(f'<@{self.client.user.id}>', bot_name).strip()
                    
                    if not content and not message.attachments:
                        content = "Hello!"
                
                # Use batching for this user
                await self._add_to_batch(
                    channel_id=message.channel.id,
                    message=message,
                    content=content,
                    guild=guild,
                    attachments=message.attachments if not is_other_bot else [],
                    user_name=user_name,
                    is_dm=is_dm,
                    user_id=message.author.id,
                    reply_to_name=reply_to_name,
                    sticker_info=sticker_info
                )
            else:
                add_to_history(
                    message.channel.id, "user", message.content,
                    author_name=user_name, reply_to=reply_to_name
                )
        
        @self.client.event
        async def on_message_edit(before: discord.Message, after: discord.Message):
            # Ignore our own edits (including slash command followups)
            if after.author == self.client.user:
                return
            # Ignore other bots' edits entirely
            if after.author.bot:
                return
            if before.content == after.content:
                return
            
            user_name = get_user_display_name(after.author)
            update_history_on_edit(after.channel.id, before.content, after.content, user_name)
            
            if self.client.user in after.mentions:
                return
            
            asyncio.create_task(self._maybe_respond_to_edit(before, after, user_name))
        
        @self.client.event
        async def on_message_delete(message: discord.Message):
            if message.author == self.client.user:
                remove_assistant_from_history(message.channel.id, 1)
    
    async def _send_organic_response(self, message: discord.Message, response: str) -> list:
        """Send response organically - split by lines and send separately with delays."""
        lines = []
        for para in response.split('\n\n'):
            para = para.strip()
            if para:
                if '\n' in para:
                    sublines = para.split('\n')
                    current = []
                    for sub in sublines:
                        current.append(sub.strip())
                        if len(current) >= 2:
                            lines.append('\n'.join(current))
                            current = []
                    if current:
                        lines.append('\n'.join(current))
                else:
                    lines.append(para)
        
        if not lines:
            lines = [response]
        
        sent_messages = []
        
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                if i == 0:
                    sent_msg = await message.reply(line)
                    sent_messages.append(sent_msg)
                else:
                    # Add delay between lines (0.5-1 second)
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                    sent_msg = await message.channel.send(line)
                    sent_messages.append(sent_msg)
            except discord.HTTPException as e:
                log.error(f"Failed to send: {e}", self.name)
        
        return sent_messages
    
    async def _send_staggered_reactions(self, message: discord.Message, reactions: list, guild: discord.Guild):
        """Send reactions to message."""
        for reaction in reactions:
            try:
                await add_reactions(message, [reaction], guild)
            except:
                pass
    
    async def _gather_mentioned_user_context(self, message: discord.Message, char_name: str) -> str:
        """Gather ephemeral context about mentioned users from recent channel messages.
        
        Only gathers info about users who:
        1. Are mentioned in the message
        2. Don't have existing memories with this character
        
        Returns a context string (not stored anywhere).
        """
        if not message.mentions:
            return ""
        
        mentioned_contexts = []
        
        for mentioned_user in message.mentions:
            # Skip bots and the message author
            if mentioned_user.bot or mentioned_user.id == message.author.id:
                continue
            
            # Skip if we already have memories about this user
            from memory import memory_manager
            existing_memories = ""
            if message.guild:
                existing_memories = memory_manager.get_user_memories(
                    message.guild.id, mentioned_user.id, character_name=char_name
                )
            else:
                existing_memories = memory_manager.get_dm_memories(
                    mentioned_user.id, character_name=char_name
                )
            
            if existing_memories:
                continue  # We already know this user
            
            # Gather recent messages from this user in the channel (last 50 messages)
            try:
                user_messages = []
                async for hist_msg in message.channel.history(limit=50):
                    if hist_msg.author.id == mentioned_user.id and hist_msg.content:
                        user_messages.append(hist_msg.content[:200])
                        if len(user_messages) >= 5:
                            break
                
                if user_messages:
                    display_name = mentioned_user.display_name
                    context = f"About {display_name} (from recent messages, no stored memories):\n"
                    context += "\n".join([f"- They said: \"{msg}\"" for msg in user_messages[:3]])
                    mentioned_contexts.append(context)
            except Exception as e:
                log.warn(f"Failed to gather context for {mentioned_user}: {e}", self.name)
        
        return "\n\n".join(mentioned_contexts)
    
    async def _add_to_batch(self, channel_id: int, message: discord.Message, content: str, 
                            guild, attachments, user_name: str, is_dm: bool, user_id: int,
                            reply_to_name: str, sticker_info: str):
        """Add message to batch and start/reset 10-second timer for this user."""
        batch_key = (channel_id, user_id)
        
        if batch_key in self._message_batches:
            # Add to existing batch and cancel old timer
            batch = self._message_batches[batch_key]
            batch['messages'].append({
                'message': message,
                'content': content,
                'attachments': attachments,
                'reply_to_name': reply_to_name,
                'sticker_info': sticker_info
            })
            # Cancel existing timer and start new one
            if batch['timer_task']:
                batch['timer_task'].cancel()
            batch['timer_task'] = asyncio.create_task(
                self._batch_timer(batch_key, channel_id, guild, user_name, is_dm, user_id)
            )
        else:
            # Create new batch
            self._message_batches[batch_key] = {
                'messages': [{
                    'message': message,
                    'content': content,
                    'attachments': attachments,
                    'reply_to_name': reply_to_name,
                    'sticker_info': sticker_info
                }],
                'timer_task': asyncio.create_task(
                    self._batch_timer(batch_key, channel_id, guild, user_name, is_dm, user_id)
                )
            }
    
    async def _batch_timer(self, batch_key: tuple, channel_id: int, guild, user_name: str, 
                           is_dm: bool, user_id: int):
        """Wait for batch timeout while showing typing, then process the batch."""
        import runtime_config
        timeout = runtime_config.get('batch_timeout', 15)
        
        # Get channel to show typing indicator
        if batch_key in self._message_batches:
            first_msg = self._message_batches[batch_key]['messages'][0]['message']
            channel = first_msg.channel
            
            # Show typing indicator while collecting
            try:
                async with channel.typing():
                    await asyncio.sleep(timeout)
            except Exception:
                await asyncio.sleep(timeout)
        else:
            await asyncio.sleep(timeout)
        
        if batch_key not in self._message_batches:
            return
        
        batch = self._message_batches.pop(batch_key)
        messages = batch['messages']
        
        if not messages:
            return
        
        # Combine all messages into one content block
        combined_content = "\n".join([m['content'] for m in messages])
        last_message = messages[-1]['message']
        
        # Collect all attachments
        all_attachments = []
        for m in messages:
            all_attachments.extend(m['attachments'])
        
        # Use the last message for replying, first for reply context
        reply_to_name = messages[0].get('reply_to_name', '')
        sticker_info = messages[-1].get('sticker_info', '')
        
        # Add to request queue with combined content
        await self.request_queue.add_request(
            channel_id=channel_id,
            message=last_message,
            content=combined_content,
            guild=guild,
            attachments=all_attachments,
            user_name=user_name,
            is_dm=is_dm,
            user_id=user_id,
            reply_to_name=reply_to_name,
            sticker_info=sticker_info
        )
    
    async def _process_request(self, request: dict):
        """Process a single queued request."""
        try:
            message = request['message']
            content = request['content']
            guild = request['guild']
            attachments = request['attachments']
            user_name = request['user_name']
            is_dm = request['is_dm']
            user_id = request['user_id']
            reply_to_name = request.get('reply_to_name')
            
            if not self.character:
                await message.channel.send("❌ No character loaded!", delete_after=ERROR_DELETE_AFTER)
                return
            
            channel_id = message.channel.id
            guild_id = guild.id if guild else None
            
            add_to_history(channel_id, "user", content, author_name=user_name, reply_to=reply_to_name)
            
            # Track stats
            channel_name = getattr(message.channel, 'name', 'DM')
            stats_manager.record_message(user_id, user_name, channel_id, channel_name)
            
            attachment_content = await process_attachments(message) if attachments else None
            
            emojis = get_guild_emojis(guild) if guild else ""
            lore = memory_manager.get_lore(guild_id) if guild_id else ""
            
            # Get both server-wide and per-user memories (per-character)
            char_name = self.character.name if self.character else None
            user_memories = ""  # Initialize for later use
            global_profile = memory_manager.get_global_user_profile(user_id)  # Cross-server facts
            
            if is_dm:
                memories = memory_manager.get_dm_memories(user_id, character_name=char_name)
                # Combine with global profile
                if global_profile and memories:
                    memories = f"What you know about this user (cross-server):\n{global_profile}\n\nFrom DMs:\n{memories}"
                elif global_profile:
                    memories = f"What you know about this user:\n{global_profile}"
            elif guild_id:
                server_memories = memory_manager.get_server_memories(guild_id)
                user_memories = memory_manager.get_user_memories(guild_id, user_id, character_name=char_name)
                
                # Combine memories: global profile + per-user + server-wide
                memory_parts = []
                if global_profile:
                    memory_parts.append(f"What you know about {user_name} (cross-server):\n{global_profile}")
                if user_memories:
                    memory_parts.append(f"About {user_name} (this server):\n{user_memories}")
                if server_memories:
                    memory_parts.append(f"General:\n{server_memories}")
                memories = "\n\n".join(memory_parts) if memory_parts else ""
            else:
                memories = ""
            
            # Get active users for social awareness
            active_users = get_active_users(channel_id) if not is_dm else []
            
            # Build system prompt from templates
            system_prompt = character_manager.build_system_prompt(
                character=self.character,
                guild_name=guild.name if guild else "DM",
                emojis=emojis,
                lore=lore,
                memories=memories,
                user_name=user_name,
                active_users=active_users
            )
            
            # Gather ephemeral context about mentioned users (not stored)
            mentioned_context = await self._gather_mentioned_user_context(message, char_name)
            if mentioned_context:
                system_prompt += f"\n\n--- Context about mentioned users ---\n{mentioned_context}"
            
            # Dynamic context limit: reduce chat history when user has more memories
            # This prioritizes "what we remember about you" over "what you just said"
            user_memory_count = len(user_memories.split('\n')) if user_memories else 0
            if user_memory_count >= 10:
                history_limit = 10  # Lots of memories = minimal recent context needed
            elif user_memory_count >= 5:
                history_limit = 20
            else:
                history_limit = 50  # New user = more recent context
            
            history = format_history_for_ai(channel_id, limit=history_limit)
            
            if attachment_content:
                history[-1]["content"] = attachment_content
            
            # Show typing indicator while generating
            async with message.channel.typing():
                # Store context for dashboard visualization
                import runtime_config
                token_estimate = len(system_prompt) // 4 + sum(len(m.get('content', '')) for m in history) // 4
                runtime_config.store_last_context(self.name, system_prompt, history, token_estimate)
                
                # Track response time
                start_time = time.time()
                response = await provider_manager.generate(
                    messages=history,
                    system_prompt=system_prompt,
                    temperature=DEFAULT_TEMPERATURE,
                    max_tokens=DEFAULT_MAX_TOKENS
                )
                response_time_ms = int((time.time() - start_time) * 1000)
                stats_manager.record_response(response_time_ms)
            
            response = remove_thinking_tags(response)
            response = clean_bot_name_prefix(response, self.character.name)
            response = clean_em_dashes(response)
            
            response, reactions = parse_reactions(response)
            
            if guild:
                response = convert_emojis_in_text(response, guild)
            
            # Handle empty response
            if not response or not response.strip():
                log.warn("Empty response after processing", self.name)
                response = "*tilts head*"
            
            # Update mood based on response sentiment
            self._update_mood(channel_id, content, response)
            
            add_to_history(channel_id, "assistant", response)
            
            sent_messages = await self._send_organic_response(message, response)
            
            if len(sent_messages) > 1:
                store_multipart_response(channel_id, [m.id for m in sent_messages], response)
            
            # Send reactions
            if reactions:
                asyncio.create_task(self._send_staggered_reactions(message, reactions, guild))
            
            asyncio.create_task(self._maybe_auto_memory(channel_id, is_dm, guild_id if not is_dm else user_id, user_id, content, user_name))
        
        except Exception as e:
            log.error(f"Error processing: {e}", self.name)
            try:
                await message.channel.send(f"❌ Error: {str(e)[:100]}", delete_after=ERROR_DELETE_AFTER)
            except:
                pass
    
    def _update_mood(self, channel_id: int, user_message: str, bot_response: str):
        """Feature 12: Update channel mood based on conversation sentiment."""
        # Simple sentiment detection
        excited_words = ['!', 'love', 'amazing', 'awesome', 'wow', 'haha', 'lol', '😂', '❤️', '🎉']
        bored_words = ['ok', 'meh', 'whatever', 'sure', 'fine', 'k', '...']
        
        combined = (user_message + " " + bot_response).lower()
        
        excited_score = sum(1 for word in excited_words if word in combined)
        bored_score = sum(1 for word in bored_words if word in combined)
        
        # Update mood (-1 to 1 scale)
        current = self._channel_mood.get(channel_id, 0)
        delta = (excited_score - bored_score) * 0.1
        new_mood = max(-1, min(1, current + delta))
        
        # Decay toward neutral
        new_mood *= 0.95
        self._channel_mood[channel_id] = new_mood
    
    async def _delayed_followup(self, message: discord.Message, user_name: str, guild: discord.Guild):
        """Feature 13: Occasionally send a delayed follow-up."""
        await asyncio.sleep(random.uniform(15, 45))
        
        followups = [
            f"Oh, {user_name}, one more thing...",
            f"Wait, {user_name}—",
            f"Actually, {user_name}...",
            "Oh! I just remembered—",
            "Hmm, also..."
        ]
        
        try:
            # Generate a small follow-up using AI
            followup_prompt = f"Generate a very brief (1 sentence) follow-up thought related to the conversation. Start with one of these: {', '.join(followups[:3])}"
            
            response = await provider_manager.generate(
                messages=[{"role": "user", "content": f"[Context: You just finished talking to {user_name}. Add a brief afterthought.]"}],
                system_prompt=f"You are {self.character.name}. Send a very brief follow-up (10 words max).",
                temperature=1.0,
                max_tokens=50
            )
            
            if response and len(response) < 200:
                await message.channel.send(response)
        except:
            pass  # Silently fail - this is optional
    
    async def _maybe_auto_memory(self, channel_id: int, is_dm: bool, id_key: int, user_id: int = None, last_message: str = "", user_name: str = None):
        """Check if the latest message contains significant information worth remembering."""
        # Quick pre-filter: skip very short messages
        if len(last_message) < 20:
            return
        
        # Cooldown: don't check for memories too frequently (once per 3 messages min)
        history = get_history(channel_id)
        if len(history) < 3:
            return
        
        # Check if we recently saved a memory (within last 5 messages)
        last_memory_check = getattr(self, '_last_memory_check', {})
        msg_count = len(history)
        last_checked = last_memory_check.get(channel_id, 0)
        if msg_count - last_checked < 3:
            return
        last_memory_check[channel_id] = msg_count
        self._last_memory_check = last_memory_check
        
        char_name = self.character.name if self.character else "the character"
        await memory_manager.generate_memory(
            provider_manager, history[-5:], is_dm, id_key, char_name, user_id=user_id, user_name=user_name
        )
    
    async def _maybe_respond_to_edit(self, before: discord.Message, after: discord.Message, user_name: str):
        """Check if an edit is significant and respond."""
        if not self.character:
            return
        
        # Only respond to edits if autonomous mode is enabled for this channel
        channel_id = after.channel.id
        if channel_id not in autonomous_manager.enabled_channels:
            return
        
        old = before.content.lower().strip()
        new = after.content.lower().strip()
        
        old_words = set(re.findall(r'\w+', old))
        new_words = set(re.findall(r'\w+', new))
        
        if old_words == new_words:
            return
        
        if abs(len(old) - len(new)) <= 2 and len(old_words.symmetric_difference(new_words)) <= 1:
            return
        
        eval_prompt = f"""You are evaluating if a message edit is significant enough for {self.character.name} to react to.

ORIGINAL: "{before.content}"
EDITED TO: "{after.content}"

Is this edit:
A) A typo fix or minor correction (NO response needed)
B) A meaningful change in tone, content, or intent (WORTH responding to)

Reply with ONLY "A" or "B"."""

        try:
            result = await provider_manager.generate(
                messages=[{"role": "user", "content": eval_prompt}],
                system_prompt="You evaluate message edits. Reply with only A or B.",
                max_tokens=5
            )
            
            if "B" not in result.upper():
                return
            
            react_prompt = f"""You are {self.character.name}. You noticed {user_name} sneakily edited their message.

ORIGINAL: "{before.content}"
CHANGED TO: "{after.content}"

React naturally and briefly (1-2 sentences) to catching them editing their message."""

            response = await provider_manager.generate(
                messages=[{"role": "user", "content": react_prompt}],
                system_prompt=f"You are {self.character.name}. React organically to message edits.",
                max_tokens=200
            )
            
            response = remove_thinking_tags(response)
            response = clean_bot_name_prefix(response, self.character.name)
            
            if after.guild:
                response = convert_emojis_in_text(response, after.guild)
            
            await after.reply(response)
            add_to_history(after.channel.id, "assistant", response)
            
        except Exception as e:
            log.debug(f"Edit response failed: {e}", self.name)
    
    def _setup_commands(self):
        """Register slash commands."""
        
        # Character reload command
        @self.tree.command(name="reload", description="Reload character from file")
        async def cmd_reload(interaction: discord.Interaction):
            if self.character:
                self.character = character_manager.load(self.character_name)
                await interaction.response.send_message(f"✅ Reloaded **{self.character.name}**", ephemeral=True)
            else:
                await interaction.response.send_message("❌ No character loaded", ephemeral=True)
        
        @self.tree.command(name="switch", description="Switch to a different character")
        @app_commands.describe(character="Character name (leave empty to list available)")
        async def cmd_switch(interaction: discord.Interaction, character: Optional[str] = None):
            available = character_manager.list_available()
            
            if not character:
                # List available characters
                if available:
                    current = self.character.name if self.character else "None"
                    char_list = "\n".join([f"• **{c}**" + (" ← current" if c.lower() == current.lower() else "") for c in available])
                    await interaction.response.send_message(f"**Available Characters:**\n{char_list}\n\nUse `/switch <name>` to switch.", ephemeral=True)
                else:
                    await interaction.response.send_message("❌ No characters found in `characters/` folder", ephemeral=True)
                return
            
            # Find matching character (case-insensitive)
            match = None
            for c in available:
                if c.lower() == character.lower():
                    match = c
                    break
            
            if not match:
                await interaction.response.send_message(f"❌ Character '{character}' not found. Use `/switch` to see available.", ephemeral=True)
                return
            
            # Load new character
            new_char = character_manager.load(match)
            if new_char:
                self.character = new_char
                self.character_name = match
                await interaction.response.send_message(f"✅ Switched to **{new_char.name}**", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Failed to load '{match}'", ephemeral=True)
        
        @self.tree.command(name="clear", description="Clear conversation history")
        async def cmd_clear(interaction: discord.Interaction):
            clear_history(interaction.channel_id)
            await interaction.response.send_message("✅ History cleared", ephemeral=True)
        
        @self.tree.command(name="recall", description="Load recent Discord messages into context")
        @app_commands.describe(count="Number of messages to recall (1-200)")
        async def cmd_recall(interaction: discord.Interaction, count: int = 20):
            if count < 1:
                count = 1
            elif count > 200:
                count = 200
            
            await interaction.response.defer(ephemeral=True)
            loaded = await self._recall_channel_history(interaction.channel, count)
            await interaction.followup.send(f"✅ Loaded {loaded} messages into context", ephemeral=True)
        
        @self.tree.command(name="memory", description="Save a memory")
        @app_commands.describe(content="Memory to save")
        async def cmd_memory(interaction: discord.Interaction, content: str):
            is_dm = isinstance(interaction.channel, discord.DMChannel)
            if is_dm:
                memory_manager.add_dm_memory(interaction.user.id, content)
            else:
                memory_manager.add_server_memory(interaction.guild_id, content)
            await interaction.response.send_message("✅ Memory saved", ephemeral=True)
        
        @self.tree.command(name="memories", description="View saved memories")
        async def cmd_memories(interaction: discord.Interaction):
            is_dm = isinstance(interaction.channel, discord.DMChannel)
            char_name = self.character.name if self.character else None
            if is_dm:
                memories = memory_manager.get_dm_memories(interaction.user.id, character_name=char_name)
            else:
                memories = memory_manager.get_server_memories(interaction.guild_id)
            
            if memories:
                await interaction.response.send_message(f"**Memories:**\n{memories[:1900]}", ephemeral=True)
            else:
                await interaction.response.send_message("No memories saved yet.", ephemeral=True)
        
        @self.tree.command(name="lore", description="Add/view server lore")
        @app_commands.describe(content="Lore to add (empty to view)")
        async def cmd_lore(interaction: discord.Interaction, content: Optional[str] = None):
            if isinstance(interaction.channel, discord.DMChannel):
                await interaction.response.send_message("Lore is server-only", ephemeral=True)
                return
            
            if content:
                memory_manager.add_lore(interaction.guild_id, content)
                await interaction.response.send_message("✅ Lore added", ephemeral=True)
            else:
                lore = memory_manager.get_lore(interaction.guild_id)
                if lore:
                    await interaction.response.send_message(f"**Server Lore:**\n{lore[:1900]}", ephemeral=True)
                else:
                    await interaction.response.send_message("No lore set.", ephemeral=True)
        
        @self.tree.command(name="status", description="Check bot status")
        async def cmd_status(interaction: discord.Interaction):
            char_name = self.character.name if self.character else "None"
            provider_status = provider_manager.get_status()
            msg = f"**Bot:** {self.name}\n**Character:** {char_name}\n\n{provider_status}"
            await interaction.response.send_message(msg, ephemeral=True)
        
        @self.tree.command(name="autonomous", description="Toggle autonomous responses")
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
        ):
            if isinstance(interaction.channel, discord.DMChannel):
                await interaction.response.send_message("Autonomous mode is server-only", ephemeral=True)
                return
            
            if enabled:
                # Convert percentage to decimal (e.g., 5 -> 0.05)
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
        
        @self.tree.command(name="stop", description="Stop/resume bot-to-bot conversations globally")
        async def cmd_stop(interaction: discord.Interaction):
            import runtime_config
            current = runtime_config.get("bot_interactions_paused", False)
            runtime_config.set("bot_interactions_paused", not current)
            if not current:
                await interaction.response.send_message("🛑 Bot-to-bot interactions **PAUSED** globally", ephemeral=True)
            else:
                await interaction.response.send_message("▶️ Bot-to-bot interactions **RESUMED**", ephemeral=True)
        
        @self.tree.command(name="delete_messages", description="Delete bot's last N messages")
        @app_commands.describe(count="Number of messages to delete (1-20)")
        async def cmd_delete_messages(interaction: discord.Interaction, count: int = 1):
            if count < 1:
                count = 1
            elif count > 20:
                count = 20
            
            await interaction.response.defer(ephemeral=True)
            
            deleted = 0
            async for msg in interaction.channel.history(limit=100):
                if msg.author == self.client.user:
                    try:
                        await msg.delete()
                        deleted += 1
                        remove_assistant_from_history(interaction.channel_id, 1)
                        if deleted >= count:
                            break
                    except:
                        pass
            
            await interaction.followup.send(f"✅ Deleted {deleted} messages", ephemeral=True)
        
        # Fun commands - dynamically registered
        FUN_COMMANDS = {
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
        
        for cmd_name, cmd_desc in FUN_COMMANDS.items():
            # Create closure to capture cmd_name correctly
            def make_callback(action: str):
                async def callback(interaction: discord.Interaction):
                    await self._fun_command(interaction, action)
                return callback
            
            cmd = app_commands.Command(
                name=cmd_name,
                description=cmd_desc,
                callback=make_callback(cmd_name)
            )
            self.tree.add_command(cmd)
        
        @self.tree.command(name="affection", description="Check affection level")
        async def cmd_affection(interaction: discord.Interaction):
            # Defer IMMEDIATELY to avoid 3-second Discord timeout
            await interaction.response.defer()
            
            if not self.character:
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
            char_name = self.character.name if self.character else None
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
            
            system = f"""You are {self.character.name}. Based on your interactions and memories with {user_name}, give a brief, 
in-character assessment of your affection/feelings toward them. Be genuine and reflect actual 
interactions. Include a rough affection percentage (0-100%) if it fits your character."""
            
            response = await provider_manager.generate(
                messages=[{"role": "user", "content": f"{full_context}\n\nHow do you feel about {user_name}?"}],
                system_prompt=system,
                max_tokens=400
            )
            
            response = remove_thinking_tags(response)
            response = clean_bot_name_prefix(response, self.character.name)
            
            await interaction.followup.send(response)
    
    async def _fun_command(self, interaction: discord.Interaction, action: str):
        """Handle fun interaction commands with relationship context."""
        # Defer IMMEDIATELY to avoid 3-second Discord timeout
        await interaction.response.defer()
        
        if not self.character:
            await interaction.followup.send("No character loaded", ephemeral=True)
            return
        
        user_name = get_user_display_name(interaction.user)
        user_id = interaction.user.id
        guild_id = interaction.guild.id if interaction.guild else None
        channel_id = interaction.channel_id
        
        # Get relationship context - recent chat history
        history = get_history(channel_id)
        recent_context = "\n".join([
            f"{m.get('role', 'user')}: {m.get('content', '')[:100]}"
            for m in history[-15:]
        ]) if history else ""
        
        # Get memories - both user-specific and server-wide
        char_name = self.character.name if self.character else None
        user_memories = ""
        server_memories = ""
        if guild_id:
            user_memories = memory_manager.get_user_memories(guild_id, user_id, character_name=char_name)
            server_memories = memory_manager.get_server_memories(guild_id)
        
        # Build prompts with relationship context
        prompts = {
            "kiss": f"{user_name} kisses you. React in character with a brief, natural response.",
            "hug": f"{user_name} hugs you. React in character with a brief, natural response.",
            "bonk": f"{user_name} bonks you. React in character with a brief, natural response.",
            "bite": f"{user_name} bites you. React in character with a brief, natural response.",
            "joke": f"{user_name} asks you to tell them a joke. Tell a joke that fits your character.",
            "pat": f"{user_name} pats your head. React in character with a brief, natural response.",
            "poke": f"{user_name} pokes you. React in character with a brief, natural response.",
            "tickle": f"{user_name} tickles you. React in character with a brief, natural response.",
            "slap": f"{user_name} slaps you. React in character with a brief, natural response.",
            "cuddle": f"{user_name} cuddles with you. React in character with a brief, natural response.",
            "compliment": f"{user_name} asks for a compliment. Give them a genuine, in-character compliment.",
            "roast": f"{user_name} asks you to roast them. Give a playful, in-character roast (keep it friendly).",
            "fortune": f"{user_name} asks for their fortune. Give a mystical, in-character fortune reading.",
            "challenge": f"{user_name} challenges you. React with a competitive, in-character response.",
            "holdhands": f"{user_name} holds your hand. React in character with a brief, natural response.",
            "squish": f"{user_name} squishes your face. React in character with a brief, natural response.",
            "spank": f"{user_name} spanks you. React in character with a brief, natural response.",
        }
        
        # Build comprehensive system prompt with all context
        context_parts = []
        if user_memories:
            context_parts.append(f"What you remember about {user_name}:\n{user_memories}")
        if server_memories:
            context_parts.append(f"Server context:\n{server_memories}")
        if recent_context:
            context_parts.append(f"Recent conversation:\n{recent_context}")
        
        relationship_context = "\n\n".join(context_parts) if context_parts else "No prior context with this user."
        
        system_prompt = f"""You are {self.character.name}. Keep your response brief (1-3 sentences).

{relationship_context}

Respond naturally based on your relationship with {user_name}. Consider the history and memories when responding. If you know them well, be warmer. If they're new, be appropriately reserved."""
        
        response = await provider_manager.generate(
            messages=[{"role": "user", "content": prompts.get(action, "React naturally.")}],
            system_prompt=system_prompt,
            max_tokens=300
        )
        
        response = remove_thinking_tags(response)
        response = clean_bot_name_prefix(response, self.character.name)
        
        if interaction.guild:
            response = convert_emojis_in_text(response, interaction.guild)
        
        await interaction.followup.send(response)
    
    async def _recall_channel_history(self, channel, limit: int = 20) -> int:
        """Fetch recent messages from Discord and load into context."""
        count = 0
        messages = []
        
        async for msg in channel.history(limit=limit):
            if msg.content and not msg.content.startswith('/'):
                messages.append(msg)
        
        messages.reverse()
        
        guild = getattr(channel, 'guild', None)
        bot_member = guild.me if guild else None
        
        for msg in messages:
            is_bot = msg.author.bot and (bot_member and msg.author == bot_member)
            role = "assistant" if is_bot else "user"
            user_name = get_user_display_name(msg.author) if not is_bot else None
            
            add_to_history(channel.id, role, msg.content, author_name=user_name)
            count += 1
        
        return count
    
    async def start(self):
        """Start the bot."""
        await self.client.start(self.token)
    
    async def close(self):
        """Close the bot connection."""
        await self.client.close()


# --- Bot Loading ---

def load_bot_configs() -> List[dict]:
    """Load bot configurations from bots.json or fall back to single-bot mode."""
    config_path = os.path.join(os.path.dirname(__file__), "bots.json")
    
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            data = json.load(f)
        
        bots = []
        for bot_cfg in data.get("bots", []):
            token = os.getenv(bot_cfg["token_env"])
            if not token:
                log.warn(f"Token env var '{bot_cfg['token_env']}' not set, skipping {bot_cfg['name']}")
                continue
            bots.append({
                "name": bot_cfg["name"],
                "token": token,
                "character_name": bot_cfg["character"]
            })
        
        if bots:
            return bots
        log.warn("No valid bots in bots.json, falling back to single-bot mode")
    
    # Fallback: Single bot mode
    if not DISCORD_TOKEN:
        log.error("DISCORD_TOKEN not set!")
        return []
    
    return [{
        "name": "Default",
        "token": DISCORD_TOKEN,
        "character_name": DEFAULT_CHARACTER
    }]


async def run_bots():
    """Run all configured bots."""
    configs = load_bot_configs()
    
    if not configs:
        log.error("No bots configured!")
        return
    
    log.startup(f"Starting {len(configs)} bot(s)...")
    log.divider()
    
    instances = [BotInstance(**cfg) for cfg in configs]
    
    # Start web dashboard
    try:
        from dashboard import start_dashboard
        start_dashboard(bots=instances, host='0.0.0.0', port=5000)
        log.online("Dashboard running at http://localhost:5000")
    except Exception as e:
        log.warn(f"Dashboard failed to start: {e}")
    
    try:
        await asyncio.gather(*[bot.start() for bot in instances])
    except KeyboardInterrupt:
        log.info("Shutting down...")
        for bot in instances:
            await bot.close()


# --- Entry Point ---

if __name__ == "__main__":
    # Run startup validation first
    from startup import validate_startup
    
    if not validate_startup(interactive=True):
        log.error("Startup validation failed. Please fix the issues above.")
        import sys
        sys.exit(1)
    
    log.divider()
    asyncio.run(run_bots())
