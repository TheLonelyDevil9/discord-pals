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

from config import ERROR_DELETE_AFTER, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS, CHARACTER_PROVIDERS
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
    resolve_discord_formatting, load_history, set_channel_name, get_other_bot_names
)
from request_queue import RequestQueue
from stats import stats_manager
import runtime_config
import logger as log
from prometheus_metrics import metrics_manager


def _is_same_character(bot_author_name: str, character_name: str) -> bool:
    """Check if a bot's name matches a character name (case-insensitive, partial match).

    Used to prevent self-loops where different bot clients share a character.
    """
    if not character_name:
        return False
    bot_name_lower = bot_author_name.lower()
    char_name_lower = character_name.lower()
    return (char_name_lower in bot_name_lower or
            bot_name_lower in char_name_lower)


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

        # Anti-looping state
        self._recent_responses: Dict[int, list] = {}  # channel_id -> list of recent response hashes
        self._consecutive_failures: Dict[int, int] = {}  # channel_id -> failure count
        self._response_timestamps: Dict[int, list] = {}  # channel_id -> list of response timestamps
        self._user_timestamps: Dict[int, list] = {}  # user_id -> list of message timestamps (per-user rate limit)

        # Bot-on-bot conversation fall-off tracker
        self._bot_conversation_tracker: Dict[int, dict] = {}  # channel_id -> {consecutive_bot_messages, last_message_time, last_human_message_time}

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

            # Start periodic state cleanup task (every 5 minutes)
            asyncio.create_task(self._periodic_state_cleanup())

            # Initialize Prometheus metrics for this bot
            metrics_manager.update_bot_status(
                bot_name=self.name,
                character_name=self.character.name if self.character else 'None',
                online=True
            )

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

            # Prevent self-loop: Don't respond to messages from bots with same character name
            if is_other_bot and self.character:
                bot_display = message.author.display_name if hasattr(message.author, 'display_name') else ""
                if _is_same_character(bot_display, self.character.name) or _is_same_character(message.author.name, self.character.name):
                    log.debug(f"Ignoring message from bot with same character: {message.author.name}", self.name)
                    add_to_history(message.channel.id, "user", message.content, author_name=get_user_display_name(message.author))
                    return

            user_name = get_user_display_name(message.author)
            reply_to_name = get_reply_context(message)
            sticker_info = get_sticker_info(message) if not is_other_bot else None
            
            guild = message.guild
            guild_id = guild.id if guild else None
            
            # Bot chain prevention - don't respond to bots if we recently responded to a bot
            if is_other_bot:
                # Global stop for bot-bot interactions
                if runtime_config.get("bot_interactions_paused", False):
                    add_to_history(message.channel.id, "user", message.content, author_name=user_name)
                    return

                # Progressive fall-off for bot-on-bot conversations
                channel_id = message.channel.id

                # Initialize or update conversation tracker
                if channel_id not in self._bot_conversation_tracker:
                    self._bot_conversation_tracker[channel_id] = {
                        "consecutive_bot_messages": 0,
                        "last_message_time": 0,
                        "last_human_message_time": time.time()
                    }

                tracker = self._bot_conversation_tracker[channel_id]

                # Increment consecutive bot message counter
                tracker["consecutive_bot_messages"] += 1
                tracker["last_message_time"] = time.time()

                # Calculate response probability based on conversation length
                consecutive_msgs = tracker["consecutive_bot_messages"]
                response_probability = self._calculate_bot_response_probability(
                    consecutive_msgs, runtime_config.get_all()
                )

                # Roll the dice
                if random.random() > response_probability:
                    log.debug(
                        f"Bot fall-off: Skipping response (consecutive: {consecutive_msgs}, "
                        f"probability: {response_probability:.2%})",
                        self.name
                    )
                    add_to_history(message.channel.id, "user", message.content, author_name=user_name)
                    return

                # Keep existing 60-second cooldown as additional safeguard
                last_bot_response = self._last_bot_response.get(channel_id, 0)
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

                    # Skip name trigger if message is quoting the bot itself
                    # Pattern: ↩️ [quoting CharName: or ↩️ [ CharName's message:
                    if char_name and (f"[quoting {char_name.lower()}" in content_lower
                                      or f"[ {char_name.lower()}'s message" in content_lower
                                      or f"[{char_name.lower()}'s message" in content_lower):
                        log.debug(f"Skipping name trigger - message quotes self: {char_name}", self.name)
                    else:
                        names_to_check = [n.lower() for n in [bot_display_name, bot_username] if n]
                        if char_name:
                            names_to_check.append(char_name.lower())

                        # Add custom nicknames from per-bot config
                        if self.nicknames:
                            for nick in self.nicknames.split(','):
                                nick = nick.strip().lower()
                                if nick and len(nick) >= 2:
                                    names_to_check.append(nick)

                        # Strip Discord emoji shortcodes before checking names
                        # This prevents :nahida_happy: from triggering "nahida" nickname
                        content_for_name_check = re.sub(r':[a-zA-Z0-9_]+:', '', content_lower)

                        # Strip GIF URLs to prevent false triggers from URLs like https://tenor.com/view/nahida-gif-123
                        content_for_name_check = re.sub(r'https?://\S+', '', content_for_name_check)

                        # Check if any name appears in message (excluding emoji names)
                        for name in names_to_check:
                            if name and len(name) >= 2:
                                # Use word boundary matching to prevent false triggers (e.g., "Luna" in "Lunatic")
                                pattern = rf'\b{re.escape(name)}\b'
                                if re.search(pattern, content_for_name_check, re.IGNORECASE):
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
                else:
                    # Reset bot conversation tracker when human speaks
                    if message.channel.id in self._bot_conversation_tracker:
                        self._bot_conversation_tracker[message.channel.id]["consecutive_bot_messages"] = 0
                        self._bot_conversation_tracker[message.channel.id]["last_human_message_time"] = time.time()
                        log.debug(f"Bot fall-off: Reset counter (human message)", self.name)

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

        # Filter out bots and message author first
        users_to_check = [
            u for u in message.mentions
            if not u.bot and u.id != message.author.id
        ]

        if not users_to_check:
            return ""

        # Check which users need context (don't have existing memories)
        users_needing_context = []
        for mentioned_user in users_to_check:
            if message.guild:
                existing_memories = memory_manager.get_user_memories(
                    message.guild.id, mentioned_user.id, character_name=char_name
                )
            else:
                existing_memories = memory_manager.get_dm_memories(
                    mentioned_user.id, character_name=char_name
                )

            if not existing_memories:
                users_needing_context.append(mentioned_user)

        if not users_needing_context:
            return ""

        # Fetch history ONCE for all users (instead of once per user)
        try:
            history_messages = [msg async for msg in message.channel.history(limit=50)]
        except Exception as e:
            log.warn(f"Failed to fetch channel history for mentioned users: {e}", self.name)
            return ""

        # Build context for each user from the cached history
        mentioned_contexts = []
        for mentioned_user in users_needing_context:
            user_messages = [
                msg.content[:200] for msg in history_messages
                if msg.author.id == mentioned_user.id and msg.content
            ][:5]

            if user_messages:
                display_name = mentioned_user.display_name
                context = f"About {display_name} (from recent messages, no stored memories):\n"
                context += "\n".join([f"- They said: \"{msg}\"" for msg in user_messages[:3]])
                mentioned_contexts.append(context)

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

            # Check for duplicate message before adding to history
            # IMPORTANT: Only skip if THIS BOT already processed this exact message
            # (not if another bot added it - we want multiple bots to respond when mentioned)
            recent = get_history(channel_id)[-5:]

            # Check if the last user message with this content already has our response after it
            already_responded = False
            history = get_history(channel_id)
            for i, m in enumerate(history):
                if m.get('content') == content and m.get('author') == user_name and m.get('role') == 'user':
                    # Check if any message after this one is from this bot
                    for j in range(i + 1, len(history)):
                        if history[j].get('author') == self.character.name and history[j].get('role') == 'assistant':
                            already_responded = True
                            break
                    if already_responded:
                        break

            if already_responded:
                log.debug(f"Skipping - already responded to this message from {user_name}", self.name)
                return

            # Only add to history if not already present (another bot may have added it)
            if not any(m.get('content') == content and m.get('author') == user_name for m in recent):
                add_to_history(channel_id, "user", content, author_name=user_name, reply_to=reply_to_name)
            
            # Store channel name for readable history display
            channel_name = getattr(message.channel, 'name', 'DM')
            if guild:
                channel_name = f"#{channel_name} ({guild.name})"
            set_channel_name(channel_id, channel_name)
            
            # Track stats
            stats_manager.record_message(user_id, user_name, channel_id, channel_name)

            # Record Prometheus metrics
            metrics_manager.record_message(
                bot_name=self.name,
                channel_type='dm' if is_dm else 'server'
            )

            attachment_content = await process_attachments(message) if attachments else None
            if attachment_content:
                log.info(f"[DEBUG] Processed attachments: {len(attachment_content)} parts, types: {[p.get('type') for p in attachment_content]}")
            
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

            # Sanitize memories to remove any reasoning tags that may have leaked in
            if memories:
                memories = remove_thinking_tags(memories)

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
            # Get other bot names to prevent impersonation
            other_bot_names = get_other_bot_names(channel_id, self.character.name)

            chatroom_context = character_manager.build_chatroom_context(
                guild_name=guild.name if guild else "DM",
                emojis=emojis,
                lore=lore,
                memories=memories,
                user_name=user_name,
                active_users=active_users,
                mentioned_context=mentioned_context,
                other_bot_names=other_bot_names
            )
            
            # Handle attachment content in the last immediate message
            if attachment_content and immediate:
                log.info(f"[DEBUG] Attaching multimodal content to last immediate message")
                immediate[-1]["content"] = attachment_content
            elif attachment_content and not immediate:
                log.warn(f"[DEBUG] Have attachment_content but immediate is empty!")
            
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
                def _get_content_len(msg):
                    content = msg.get('content', '')
                    if isinstance(content, str):
                        return len(content)
                    elif isinstance(content, list):
                        # Multimodal content - only count text parts
                        return sum(len(p.get('text', '')) for p in content if p.get('type') == 'text')
                    return 0
                token_estimate = len(system_prompt) // 4 + len(chatroom_context) // 4 + sum(_get_content_len(m) for m in messages_for_api) // 4
                runtime_config.store_last_context(self.name, system_prompt, messages_for_api, token_estimate)
                
                # Track response time
                start_time = time.time()

                # Get message format preference from runtime config
                use_single_user = runtime_config.get("use_single_user", False)

                # Get character's preferred provider tier from config
                preferred_tier = CHARACTER_PROVIDERS.get(self.character_name, "") if self.character_name else ""

                response = await provider_manager.generate(
                    messages=messages_for_api,
                    system_prompt=system_prompt,
                    temperature=None,  # Use per-provider config
                    max_tokens=None,   # Use per-provider config (fixes max_tokens issue)
                    use_single_user=use_single_user,
                    preferred_tier=preferred_tier
                )
                response_time_ms = int((time.time() - start_time) * 1000)
                stats_manager.record_response(response_time_ms)

                # Record Prometheus metrics
                metrics_manager.record_response(
                    bot_name=self.name,
                    success=bool(response),
                    duration_seconds=response_time_ms / 1000.0,
                    provider_tier=preferred_tier or 'default'
                )

            # Handle failed generation
            if not response:
                log.error("All providers failed to generate response")
                metrics_manager.record_error(bot_name=self.name, error_type='provider_failure')
                await message.channel.send("Something went wrong - all providers failed.")
                return

            response = remove_thinking_tags(response)
            response = clean_bot_name_prefix(response, self.character.name)
            response = clean_em_dashes(response)
            response = self._strip_other_bot_prefixes(response, other_bot_names)

            response, reactions = parse_reactions(response)

            if guild:
                response = convert_emojis_in_text(response, guild)

            # Anti-looping: Check rate limit before responding
            if self._check_rate_limit(channel_id):
                log.warn("Skipping response due to rate limit", self.name)
                metrics_manager.record_rate_limit_hit(bot_name=self.name, limit_type='channel')
                return

            # Anti-looping: Check circuit breaker
            if self._check_circuit_breaker(channel_id):
                log.warn("Skipping response due to circuit breaker", self.name)
                metrics_manager.record_circuit_breaker_trip(bot_name=self.name, channel_id=channel_id)
                # Decay the failure count slowly to allow recovery
                self._consecutive_failures[channel_id] = max(0, self._consecutive_failures.get(channel_id, 0) - 1)
                return

            # Handle empty response
            if not response or not response.strip():
                log.warn("Empty response after processing", self.name)
                self._record_failure(channel_id)
                # Use varied fallback responses to avoid repetition
                fallbacks = ["*tilts head*", "*blinks*", "*pauses thoughtfully*", "...", "*hums softly*"]
                response = random.choice(fallbacks)
            else:
                # Check for duplicate/repetitive responses
                if self._is_duplicate_response(channel_id, response):
                    log.warn("Skipping duplicate response", self.name)
                    self._record_failure(channel_id)
                    return
                # Valid unique response - reset failure counter
                self._reset_failures(channel_id)

            # Record this response for rate limiting
            self._record_response(channel_id)
            
            # Update mood based on response sentiment
            self._update_mood(channel_id, content, response)
            
            add_to_history(channel_id, "assistant", response, author_name=self.character.name)
            
            sent_messages = await self._send_organic_response(message, response)
            
            # Update last activity timestamp
            runtime_config.update_last_activity(self.name)
            metrics_manager.update_last_activity(bot_name=self.name, timestamp=time.time())
            
            if len(sent_messages) > 1:
                store_multipart_response(channel_id, [m.id for m in sent_messages], response)
            
            # Send reactions
            if reactions:
                asyncio.create_task(self._send_staggered_reactions(message, reactions, guild))
            
            asyncio.create_task(self._maybe_auto_memory(channel_id, is_dm, guild_id if not is_dm else user_id, user_id, content, user_name))

        except asyncio.TimeoutError:
            log.warn("Request timed out", self.name)
            await self._send_user_error(message.channel, "timeout")
        except discord.HTTPException as e:
            log.error(f"Discord API error: {e}", self.name)
            await self._send_user_error(message.channel, "provider_error")
        except Exception as e:
            log.error(f"Error processing ({type(e).__name__}): {e}", self.name)
            await self._send_user_error(message.channel, "default")

    async def _send_user_error(self, channel, error_type: str):
        """Send a user-friendly error message."""
        from constants import USER_FRIENDLY_ERRORS, ERROR_DELETE_AFTER
        msg = USER_FRIENDLY_ERRORS.get(error_type, USER_FRIENDLY_ERRORS["default"])
        try:
            await channel.send(f"❌ {msg}", delete_after=ERROR_DELETE_AFTER)
        except Exception:
            pass  # Can't send error message, silently fail
    
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

    def _calculate_bot_response_probability(self, consecutive_bot_msgs: int, config: dict) -> float:
        """Calculate probability of responding to another bot based on conversation length.

        Uses progressive decay formula to naturally taper off bot-on-bot conversations.

        Args:
            consecutive_bot_msgs: Number of consecutive bot-only messages
            config: Runtime configuration dict with fall-off settings

        Returns:
            Probability between 0.0 and 1.0
        """
        # Check if fall-off is enabled
        if not config.get('bot_falloff_enabled', True):
            return 1.0  # Always respond if disabled

        base_chance = config.get('bot_falloff_base_chance', 0.8)
        decay_rate = config.get('bot_falloff_decay_rate', 0.15)
        min_chance = config.get('bot_falloff_min_chance', 0.05)
        hard_limit = config.get('bot_falloff_hard_limit', 10)

        # Hard cutoff at limit
        if consecutive_bot_msgs >= hard_limit:
            return 0.0

        # Progressive decay: base_chance * (1 - decay_rate)^consecutive_msgs
        probability = base_chance * ((1 - decay_rate) ** consecutive_bot_msgs)

        # Apply minimum floor
        return max(probability, min_chance)

    def _strip_other_bot_prefixes(self, response: str, other_bot_names: list) -> str:
        """Remove any other bot's name prefix from response to prevent impersonation.

        Args:
            response: The bot's response text
            other_bot_names: List of other bot character names in the channel

        Returns:
            Response with any other bot name prefixes removed
        """
        if not other_bot_names:
            return response

        for name in other_bot_names:
            # Check for "Name:" or "Name :" at start
            patterns = [f"{name}:", f"{name} :"]
            for pattern in patterns:
                if response.lower().startswith(pattern.lower()):
                    response = response[len(pattern):].strip()
                    log.warn(f"Stripped impersonation prefix '{pattern}' from response", self.name)
                    break

        # Check for mid-response impersonation (lines starting with "OtherBot:")
        lines = response.split('\n')
        cleaned_lines = []
        for line in lines:
            skip = False
            for name in other_bot_names:
                # Check if line starts with "OtherBot:" pattern
                if re.match(rf'^{re.escape(name)}\s*:', line, re.IGNORECASE):
                    log.warn(f"Stripped mid-response impersonation line for '{name}'", self.name)
                    skip = True
                    break
            if not skip:
                cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def _check_rate_limit(self, channel_id: int) -> bool:
        """Check if we're responding too frequently (anti-loop measure).

        Returns True if rate limit exceeded (should skip response).
        """
        now = time.time()
        timestamps = self._response_timestamps.get(channel_id, [])

        # Remove timestamps older than 60 seconds
        timestamps = [t for t in timestamps if now - t < 60]
        self._response_timestamps[channel_id] = timestamps

        # Allow max 5 responses per minute per channel
        if len(timestamps) >= 5:
            log.warn(f"Rate limit exceeded for channel {channel_id} (5/min)", self.name)
            return True
        return False

    def _record_response(self, channel_id: int):
        """Record a response timestamp for rate limiting."""
        now = time.time()
        if channel_id not in self._response_timestamps:
            self._response_timestamps[channel_id] = []
        self._response_timestamps[channel_id].append(now)

    def _check_user_rate_limit(self, user_id: int) -> bool:
        """Check if a user is sending too many messages (spam protection).

        Returns True if rate limit exceeded (should skip response).
        """
        from constants import MAX_USER_MESSAGES_PER_MINUTE
        now = time.time()
        timestamps = self._user_timestamps.get(user_id, [])

        # Remove timestamps older than 60 seconds
        timestamps = [t for t in timestamps if now - t < 60]
        self._user_timestamps[user_id] = timestamps

        if len(timestamps) >= MAX_USER_MESSAGES_PER_MINUTE:
            log.debug(f"User {user_id} rate limited ({MAX_USER_MESSAGES_PER_MINUTE}/min)", self.name)
            return True
        return False

    def _record_user_message(self, user_id: int):
        """Record a user message timestamp for rate limiting."""
        now = time.time()
        if user_id not in self._user_timestamps:
            self._user_timestamps[user_id] = []
        self._user_timestamps[user_id].append(now)

    def _cleanup_stale_state(self):
        """Remove state for channels/users with no recent activity."""
        from constants import STALE_STATE_THRESHOLD
        now = time.time()

        # Cleanup channel state
        for channel_id in list(self._response_timestamps.keys()):
            timestamps = self._response_timestamps[channel_id]
            if not timestamps or now - max(timestamps) > STALE_STATE_THRESHOLD:
                del self._response_timestamps[channel_id]
                self._recent_responses.pop(channel_id, None)
                self._consecutive_failures.pop(channel_id, None)
                self._channel_mood.pop(channel_id, None)
                self._bot_conversation_tracker.pop(channel_id, None)
                self._last_bot_response.pop(channel_id, None)

        # Cleanup user state
        for user_id in list(self._user_timestamps.keys()):
            timestamps = self._user_timestamps[user_id]
            if not timestamps or now - max(timestamps) > STALE_STATE_THRESHOLD:
                del self._user_timestamps[user_id]

        # Cleanup message batches (orphaned batches)
        now = time.time()
        for batch_key in list(self._message_batches.keys()):
            batch = self._message_batches[batch_key]
            # Check if timer task is still running
            if batch.get('timer_task') and batch['timer_task'].done():
                # Timer completed but batch wasn't processed - clean it up
                del self._message_batches[batch_key]
                log.debug(f"Cleaned up orphaned message batch: {batch_key}", self.name)

        # Cleanup last memory check tracking
        last_memory_check = getattr(self, '_last_memory_check', {})
        for channel_id in list(last_memory_check.keys()):
            # Remove entries for channels that no longer exist in history
            from discord_utils import conversation_history
            if channel_id not in conversation_history:
                last_memory_check.pop(channel_id, None)

    async def _periodic_state_cleanup(self):
        """Periodically clean up stale state to prevent memory leaks."""
        from constants import STALE_STATE_THRESHOLD
        cleanup_interval = 300  # 5 minutes

        while True:
            try:
                await asyncio.sleep(cleanup_interval)
                self._cleanup_stale_state()
                log.debug("Periodic state cleanup completed", self.name)
            except asyncio.CancelledError:
                # Task was cancelled during shutdown
                break
            except Exception as e:
                log.error(f"Error during periodic state cleanup: {e}", self.name)

    def _is_duplicate_response(self, channel_id: int, response: str) -> bool:
        """Check if response is too similar to recent responses (anti-loop measure).

        Returns True if duplicate detected (should skip or vary response).
        """
        if not response:
            return False

        # Use first 100 chars as signature to detect repetition
        sig = response[:100].lower().strip()
        recent = self._recent_responses.get(channel_id, [])

        # Check against last 5 responses
        if sig in recent:
            log.warn(f"Duplicate response detected in channel {channel_id}", self.name)
            return True

        # Update recent responses (keep last 5)
        recent.append(sig)
        if len(recent) > 5:
            recent = recent[-5:]
        self._recent_responses[channel_id] = recent
        return False

    def _check_circuit_breaker(self, channel_id: int) -> bool:
        """Check if circuit breaker is tripped due to consecutive failures.

        Returns True if circuit breaker is active (should skip response).
        """
        failures = self._consecutive_failures.get(channel_id, 0)
        if failures >= 3:
            log.warn(f"Circuit breaker active for channel {channel_id} ({failures} consecutive failures)", self.name)
            return True
        return False

    def _record_failure(self, channel_id: int):
        """Record a failure (empty/fallback response)."""
        self._consecutive_failures[channel_id] = self._consecutive_failures.get(channel_id, 0) + 1

    def _reset_failures(self, channel_id: int):
        """Reset failure counter on successful response."""
        self._consecutive_failures[channel_id] = 0
    
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
            user_name = get_user_display_name(msg.author)  # Always store author, even for bots
            add_to_history(channel.id, role, msg.content, author_name=user_name)
            count += 1
        
        return count
    
    async def start(self):
        """Start the bot."""
        await self.client.start(self.token)
    
    async def close(self):
        """Close the bot connection."""
        await self.client.close()
