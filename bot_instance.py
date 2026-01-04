"""
Discord Pals - Bot Instance
Encapsulates a single Discord bot with its own client, character, and state.
"""

import discord
from discord import app_commands
import asyncio
import random
import re
import time
from typing import Optional, Dict

from config import ERROR_DELETE_AFTER, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS
from providers import provider_manager
from character import character_manager, Character
from memory import memory_manager, ensure_data_dir
from discord_utils import (
    get_history, add_to_history, clear_history, format_history_split,
    get_guild_emojis, parse_reactions, add_reactions, convert_emojis_in_text,
    process_attachments, autonomous_manager, get_active_users,
    get_reply_context, get_user_display_name, get_sticker_info,
    remove_thinking_tags, clean_bot_name_prefix, clean_em_dashes,
    update_history_on_edit, remove_assistant_from_history, store_multipart_response,
    resolve_discord_formatting, load_history, set_channel_name
)
from request_queue import RequestQueue
from stats import stats_manager
import logger as log


class BotInstance:
    """Encapsulates a single Discord bot with its own client, character, and state."""
    
    def __init__(self, name: str, token: str, character_name: str, nicknames: str = ""):
        self.name = name
        self.token = token
        self.character_name = character_name
        self.nicknames = nicknames  # Comma-separated custom nicknames for this bot
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
            load_history()  # Restore history from previous session
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
            
            # Name/nickname trigger - respond when bot's name is mentioned (chance-based)
            # NOTE: Name trigger is now a SUBSET of autonomous mode - only works if autonomous is enabled
            name_triggered = False
            import runtime_config
            name_trigger_chance = runtime_config.get('name_trigger_chance', 1.0)
            channel_id = message.channel.id
            
            # Name trigger requires: autonomous enabled for channel + not already triggered by other means
            if (name_trigger_chance > 0 and not mentioned and not is_reply_to_bot and not is_dm
                and channel_id in autonomous_manager.enabled_channels):
                
                # Check if bot triggers are allowed for this channel when message is from a bot
                if is_other_bot and not autonomous_manager.can_bot_trigger(channel_id):
                    pass  # Skip name trigger for bots when not allowed
                else:
                    bot_display_name = guild.me.display_name if guild else self.client.user.display_name
                    bot_username = self.client.user.name
                    char_name = self.character.name if self.character else ""
                    
                    content_lower = message.content.lower()
                    names_to_check = [n.lower() for n in [bot_display_name, bot_username] if n]
                    if char_name:
                        names_to_check.append(char_name.lower())
                    
                    # Add custom nicknames from per-bot config
                    if self.nicknames:
                        for nick in self.nicknames.split(','):
                            nick = nick.strip().lower()
                            if nick and len(nick) >= 2:
                                names_to_check.append(nick)
                    
                    # Check if any name appears in message
                    for name in names_to_check:
                        if name and len(name) >= 2 and name in content_lower:
                            if random.random() < name_trigger_chance:
                                name_triggered = True
                                break
            
            should_respond = mentioned or is_reply_to_bot or is_autonomous or name_triggered
            
            # Debug: log why this bot is responding
            if should_respond:
                reason = []
                if mentioned:
                    reason.append("mentioned")
                if is_reply_to_bot:
                    reason.append("reply_to_bot")
                if is_autonomous:
                    reason.append("autonomous")
                if name_triggered:
                    reason.append("name_triggered")
                log.debug(f"Responding to '{message.content[:50]}...' - reason: {', '.join(reason)}", self.name)
            
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
                        # Replace THIS bot's mention with its name
                        bot_name = guild.me.display_name if guild else self.client.user.display_name
                        content = message.content.replace(f'<@{self.client.user.id}>', bot_name).strip()
                    
                    # Resolve all Discord formatting (emojis, mentions, channels, timestamps)
                    content = resolve_discord_formatting(content, guild)
                    
                    # For autonomous responses, prefix to indicate bot is chiming in
                    if is_autonomous and message.mentions:
                        # Check if someone ELSE was mentioned (not this bot)
                        other_mentions = [m for m in message.mentions if m != self.client.user]
                        if other_mentions:
                            content = f"[Someone else was mentioned, you're chiming in] {content}"
                    
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
            
            # Update history with the edited content (for context), but don't trigger a response
            user_name = get_user_display_name(after.author)
            update_history_on_edit(after.channel.id, before.content, after.content, user_name)
            # Note: Removed _maybe_respond_to_edit - edits no longer trigger bot responses
        
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
            except Exception as e:
                log.debug(f"Failed to add reaction: {e}", self.name)
    
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
        # GLOBAL KILLSWITCH: Skip all processing when paused
        import runtime_config
        if runtime_config.get("global_paused", False):
            log.debug("Request skipped - global killswitch active", self.name)
            return
        
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
            
            # Store channel name for readable history display
            channel_name = getattr(message.channel, 'name', 'DM')
            if guild:
                channel_name = f"#{channel_name} ({guild.name})"
            set_channel_name(channel_id, channel_name)
            
            # Track stats
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
            
            # Gather ephemeral context about mentioned users (not stored)
            mentioned_context = await self._gather_mentioned_user_context(message, char_name)
            
            # === BUILD 4-SECTION CONTEXT STRUCTURE ===
            
            # SECTION 1: Character Section (system prompt)
            # Contains: Guidelines, Character Persona, Example Dialogue, Special User Context
            system_prompt = character_manager.build_system_prompt(
                character=self.character,
                user_name=user_name
            )
            
            # Get runtime config for context limits
            import runtime_config
            total_limit = runtime_config.get('history_limit', 200)
            immediate_count = runtime_config.get('immediate_message_count', 5)
            
            # SECTION 2 & 4: Split history into older context and immediate messages
            # Pass current bot name so other bots' messages are tagged to prevent personality bleed
            history, immediate = format_history_split(
                channel_id, 
                total_limit=total_limit,
                immediate_count=immediate_count,
                current_bot_name=self.character.name
            )
            
            # SECTION 3: Chatroom Context (injected between history and immediate)
            # Contains: Lore, Memories, Emojis, Active Users, Reply Target, Mentioned Context
            chatroom_context = character_manager.build_chatroom_context(
                guild_name=guild.name if guild else "DM",
                emojis=emojis,
                lore=lore,
                memories=memories,
                user_name=user_name,
                active_users=active_users,
                mentioned_context=mentioned_context
            )
            
            # Handle attachment content in the last immediate message
            if attachment_content and immediate:
                immediate[-1]["content"] = attachment_content
            
            # Build the complete message list for the API
            # Order: [system] + [history] + [chatroom context as system] + [immediate]
            messages_for_api = []
            messages_for_api.extend(history)
            if chatroom_context:
                messages_for_api.append({"role": "system", "content": chatroom_context})
            messages_for_api.extend(immediate)
            
            # Show typing indicator while generating
            async with message.channel.typing():
                # Store context for dashboard visualization
                token_estimate = len(system_prompt) // 4 + len(chatroom_context) // 4 + sum(len(m.get('content', '')) for m in messages_for_api) // 4
                runtime_config.store_last_context(self.name, system_prompt, messages_for_api, token_estimate)
                
                # Track response time
                start_time = time.time()
                
                # Get message format preference from runtime config
                use_single_user = runtime_config.get("use_single_user", False)
                
                response = await provider_manager.generate(
                    messages=messages_for_api,
                    system_prompt=system_prompt,
                    temperature=None,  # Use per-provider config
                    max_tokens=None,   # Use per-provider config (fixes max_tokens issue)
                    use_single_user=use_single_user
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
            
            add_to_history(channel_id, "assistant", response, author_name=self.character.name)
            
            sent_messages = await self._send_organic_response(message, response)
            
            # Update last activity timestamp
            runtime_config.update_last_activity(self.name)
            
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
            except Exception as send_err:
                log.debug(f"Failed to send error message: {send_err}", self.name)
    
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
    
    async def _maybe_auto_memory(self, channel_id: int, is_dm: bool, id_key: int, user_id: int = None, last_message: str = "", user_name: str = None):
        """Check if the latest message contains significant information worth remembering."""
        # Quick pre-filter: skip very short messages (reduced from 20 to 10)
        if len(last_message) < 10:
            return
        
        # Cooldown: don't check for memories too frequently
        history = get_history(channel_id)
        if len(history) < 2:  # Reduced from 3 to 2
            return
        
        # Check if we recently analyzed for memories (every 2 messages instead of 3)
        last_memory_check = getattr(self, '_last_memory_check', {})
        msg_count = len(history)
        last_checked = last_memory_check.get(channel_id, 0)
        if msg_count - last_checked < 2:  # Reduced from 3 to 2
            return
        last_memory_check[channel_id] = msg_count
        self._last_memory_check = last_memory_check
        
        char_name = self.character.name if self.character else "the character"
        # Send more context to LLM (last 10 messages instead of 5)
        await memory_manager.generate_memory(
            provider_manager, history[-10:], is_dm, id_key, char_name, user_id=user_id, user_name=user_name
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
                system_prompt="You evaluate message edits. Reply with only A or B."
                # max_tokens removed - uses provider config
            )
            
            if "B" not in result.upper():
                return
            
            react_prompt = f"""You are {self.character.name}. You noticed {user_name} sneakily edited their message.

ORIGINAL: "{before.content}"
CHANGED TO: "{after.content}"

React naturally and briefly (1-2 sentences) to catching them editing their message."""

            response = await provider_manager.generate(
                messages=[{"role": "user", "content": react_prompt}],
                system_prompt=f"You are {self.character.name}. React organically to message edits."
                # max_tokens removed - uses provider config
            )
            
            response = remove_thinking_tags(response)
            response = clean_bot_name_prefix(response, self.character.name)
            
            if after.guild:
                response = convert_emojis_in_text(response, after.guild)
            
            await after.reply(response)
            add_to_history(after.channel.id, "assistant", response, author_name=self.character.name)
            
        except Exception as e:
            log.debug(f"Edit response failed: {e}", self.name)
    
    def _setup_commands(self) -> None:
        """Register slash commands from commands module."""
        from commands import setup_all_commands
        setup_all_commands(self)
    
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
