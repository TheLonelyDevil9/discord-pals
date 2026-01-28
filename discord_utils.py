"""
Discord Pals - Discord Utilities
Helper functions for Discord interactions.
"""

import discord
import re
import base64
import json
import os
import aiohttp
import threading
import time
from collections import OrderedDict
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from config import MAX_HISTORY_MESSAGES, MAX_EMOJIS_IN_PROMPT, DATA_DIR
import logger as log

# Re-export from response_sanitizer for backwards compatibility
# These are used by other modules that import from discord_utils
from response_sanitizer import (  # noqa: F401
    remove_thinking_tags, clean_bot_name_prefix, clean_em_dashes, sanitize_response,
    RE_NAME_PREFIX
)


# --- Thread-safe JSON utilities ---

_file_locks: Dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_file_lock(filepath: str) -> threading.Lock:
    """Get or create a lock for a specific file path."""
    with _locks_lock:
        if filepath not in _file_locks:
            _file_locks[filepath] = threading.Lock()
        return _file_locks[filepath]


def safe_json_load(filepath: str, default=None) -> dict | list:
    """Thread-safe JSON loading with validation.

    Args:
        filepath: Path to JSON file
        default: Default value if file doesn't exist or is invalid (default: {})

    Returns:
        Parsed JSON data or default value
    """
    if default is None:
        default = {}

    if not os.path.exists(filepath):
        return default

    lock = _get_file_lock(filepath)
    with lock:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            log.warn(f"JSON decode error in {filepath}: {e}")
            return default
        except IOError as e:
            log.warn(f"IO error reading {filepath}: {e}")
            return default


def safe_json_save(filepath: str, data, indent: int = 2) -> bool:
    """Thread-safe JSON saving with validation and atomic write.

    Args:
        filepath: Path to JSON file
        data: Data to save (must be JSON-serializable)
        indent: JSON indentation (default: 2)

    Returns:
        True if save succeeded, False otherwise
    """
    # Validate JSON is serializable before writing
    try:
        json_str = json.dumps(data, indent=indent, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        log.warn(f"JSON serialization error for {filepath}: {e}")
        return False

    lock = _get_file_lock(filepath)
    with lock:
        try:
            # Ensure parent directory exists
            parent_dir = os.path.dirname(filepath)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir)

            # Write to temp file first, then rename (atomic on most systems)
            temp_path = filepath + '.tmp'
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(json_str)

            # Atomic rename
            os.replace(temp_path, filepath)
            return True
        except IOError as e:
            log.warn(f"IO error writing {filepath}: {e}")
            # Clean up temp file if it exists
            temp_path = filepath + '.tmp'
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            return False


# --- Debounced history saving ---

_history_save_pending = False
_history_save_lock = threading.Lock()
_history_last_save = 0.0
HISTORY_SAVE_DEBOUNCE = 30.0  # Minimum seconds between saves (increased from 5s for performance)


# Conversation history storage (in-memory, per channel/DM)
conversation_history: Dict[int, List[dict]] = {}

# Recent message hashes for fast duplicate detection (channel_id -> OrderedDict of hashes)
# Using OrderedDict instead of set to maintain insertion order for proper FIFO eviction
_recent_message_hashes: Dict[int, OrderedDict] = {}
_RECENT_HASH_LIMIT = 50  # Number of recent hashes to track per channel

# Multi-part response tracking (message_id -> full_content)
multipart_responses: Dict[int, Dict[int, str]] = {}

# History persistence file
HISTORY_CACHE_FILE = os.path.join(DATA_DIR, "history_cache.json")

# Channel name mapping (channel_id -> name) for readable storage
channel_names: Dict[int, str] = {}

# Conversation history limits
_MAX_CHANNELS_IN_HISTORY = 500  # Max channels to keep in memory
_STALE_CHANNEL_THRESHOLD = 86400  # 24 hours - channels with no activity are candidates for cleanup
_channel_last_activity: Dict[int, float] = {}  # Track last activity per channel

# Pre-compiled patterns for resolve_discord_formatting
RE_CUSTOM_EMOJI = re.compile(r'<a?:([a-zA-Z0-9_]+):\d+>')
RE_USER_MENTION = re.compile(r'<@!?(\d+)>')
RE_CHANNEL_MENTION = re.compile(r'<#(\d+)>')
RE_ROLE_MENTION = re.compile(r'<@&(\d+)>')
RE_TIMESTAMP = re.compile(r'<t:(\d+)(?::[tTdDfFR])?>')

# Pre-compiled patterns for convert_emojis_in_text
RE_EMOJI_SHORTCODE = re.compile(r':([a-zA-Z0-9_]+):')
RE_BROKEN_EMOJI_END = re.compile(r'<a?:[a-zA-Z0-9_]*$')
RE_ORPHAN_SNOWFLAKE = re.compile(r'(?<![:\d])\d{17,21}>(?!\S)')
RE_EMPTY_ANGLE = re.compile(r'<>')
RE_MALFORMED_EMOJI = re.compile(r'<(?!a?:[a-zA-Z0-9_]+:\d{17,21}>)[^>]{0,50}>')

# Pre-compiled patterns for parse_reactions
# Matches [REACT: emoji], [REACT emoji], or [REACT:emoji] (colon is optional)
RE_REACTION_TAG = re.compile(r'\[REACT:?\s*([^\]]+)\]', re.IGNORECASE)

# Pre-compiled pattern for word extraction
RE_WORD = re.compile(r'\w+')

# Pre-compiled pattern for sentence splitting
RE_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')


def save_history(force: bool = False):
    """Save conversation history to disk for persistence across restarts.

    Uses debouncing to avoid excessive disk writes during high activity.

    Args:
        force: If True, save immediately regardless of debounce timer
    """
    global _history_save_pending, _history_last_save

    now = time.time()

    with _history_save_lock:
        # Check if we should debounce
        if not force and (now - _history_last_save) < HISTORY_SAVE_DEBOUNCE:
            _history_save_pending = True
            return

        _history_save_pending = False
        _history_last_save = now

    # Build data structure with channel names for readability
    serializable = {}
    for channel_id, messages in conversation_history.items():
        key = str(channel_id)
        # Store both the channel name (if known) and messages
        name = channel_names.get(channel_id, str(channel_id))
        serializable[key] = {
            "name": name,
            "messages": messages
        }

    os.makedirs(DATA_DIR, exist_ok=True)
    if not safe_json_save(HISTORY_CACHE_FILE, serializable):
        log.warn("Failed to save history")


def flush_pending_history():
    """Force save any pending history changes. Call on shutdown."""
    global _history_save_pending
    with _history_save_lock:
        if _history_save_pending:
            _history_save_pending = False
    save_history(force=True)


def load_history():
    """Load conversation history from disk on startup."""
    global conversation_history, channel_names

    data = safe_json_load(HISTORY_CACHE_FILE, default={})
    if not data:
        conversation_history = {}
        return

    # Handle both old format (list) and new format (dict with name/messages)
    for k, v in data.items():
        channel_id = int(k)
        if isinstance(v, dict) and "messages" in v:
            # New format with name
            conversation_history[channel_id] = v["messages"]
            if "name" in v:
                channel_names[channel_id] = v["name"]
        else:
            # Old format - just list of messages
            conversation_history[channel_id] = v

    log.info(f"Loaded history for {len(conversation_history)} channels")


def set_channel_name(channel_id: int, name: str):
    """Store channel name for readable history display."""
    channel_names[channel_id] = name


def get_history(channel_id: int) -> List[dict]:
    """Get conversation history for a channel."""
    return conversation_history.get(channel_id, [])


def strip_character_prefix(content: str) -> str:
    """
    Strip character name prefixes from messages to prevent identity leakage.
    Removes patterns like '[CharacterName]: ' or 'CharacterName: ' at the start.
    """
    # Match [Name]: or Name: at the start of the message
    content = RE_NAME_PREFIX.sub('', content)
    return content


def resolve_discord_formatting(content: str, guild=None) -> str:
    """
    Convert Discord formatting to readable format for LLMs.
    
    Resolves:
    - <:emoji_name:123> → :emoji_name:
    - <a:animated:123> → :animated:
    - <@123456> → @Username (if guild provided)
    - <@!123456> → @Username (nickname format)
    - <#123456> → #channel-name (if guild provided)
    - <@&123456> → @RoleName (if guild provided)
    - <t:123:R> → readable timestamp
    """
    # Custom emojis: <:name:id> or <a:name:id> → :name:
    content = RE_CUSTOM_EMOJI.sub(r':\1:', content)

    # User mentions: <@123> or <@!123> → @Username
    if guild:
        def resolve_user_mention(match):
            user_id = int(match.group(1))
            member = guild.get_member(user_id)
            if member:
                return f"@{member.display_name}"
            return match.group(0)  # Keep original if not found
        content = RE_USER_MENTION.sub(resolve_user_mention, content)

        # Channel mentions: <#123> → #channel-name
        def resolve_channel_mention(match):
            channel_id = int(match.group(1))
            channel = guild.get_channel(channel_id)
            if channel:
                return f"#{channel.name}"
            return match.group(0)
        content = RE_CHANNEL_MENTION.sub(resolve_channel_mention, content)

        # Role mentions: <@&123> → @RoleName
        def resolve_role_mention(match):
            role_id = int(match.group(1))
            role = guild.get_role(role_id)
            if role:
                return f"@{role.name}"
            return match.group(0)
        content = RE_ROLE_MENTION.sub(resolve_role_mention, content)

    # Timestamps: <t:123:R> → readable date
    def resolve_timestamp(match):
        try:
            timestamp = int(match.group(1))
            dt = datetime.fromtimestamp(timestamp)
            return dt.strftime("%Y-%m-%d %H:%M")
        except:
            return match.group(0)
    content = RE_TIMESTAMP.sub(resolve_timestamp, content)

    return content


def add_to_history(channel_id: int, role: str, content: str, author_name: str = None, reply_to: tuple = None, user_id: int = None):
    """Add a message to conversation history.

    Args:
        channel_id: Discord channel ID
        role: Message role ('user' or 'assistant')
        content: Message content
        author_name: Display name of the author
        reply_to: Tuple of (author_name, content) if this is a reply
        user_id: Discord user ID (for mention features)
    """
    if channel_id not in conversation_history:
        conversation_history[channel_id] = []

    # Track activity for this channel
    _channel_last_activity[channel_id] = time.time()

    # Strip character name prefixes from bot/assistant messages
    if role == "user" and author_name:
        content = strip_character_prefix(content)

    # Format content with reply context (author, replied_content)
    # Fix personality bleed: clearly separate quoted author from current speaker
    if reply_to and role == "user":
        reply_author, reply_content = reply_to
        if reply_content:
            # Use parentheses (not brackets) to prevent LLM from learning bracket patterns
            content = f'(replying to {reply_author}) {content}'
        else:
            content = f"(replying to {reply_author}) {content}"

    msg = {"role": role, "content": content}
    if author_name:
        msg["author"] = author_name
    if user_id:
        msg["user_id"] = user_id

    # Fast hash-based duplicate detection (O(1) instead of O(n))
    msg_hash = hash((role, content, author_name or ''))
    if channel_id not in _recent_message_hashes:
        _recent_message_hashes[channel_id] = OrderedDict()

    if msg_hash in _recent_message_hashes[channel_id]:
        return  # Already added

    # Add hash and maintain limit (OrderedDict preserves insertion order)
    _recent_message_hashes[channel_id][msg_hash] = True
    while len(_recent_message_hashes[channel_id]) > _RECENT_HASH_LIMIT:
        # Remove oldest entry (first inserted)
        _recent_message_hashes[channel_id].popitem(last=False)

    conversation_history[channel_id].append(msg)

    if len(conversation_history[channel_id]) > MAX_HISTORY_MESSAGES:
        conversation_history[channel_id] = conversation_history[channel_id][-MAX_HISTORY_MESSAGES:]

    # Trigger debounced save to prevent data loss on crash
    save_history()

    # Periodic cleanup of stale channels (every 100 messages added)
    if len(conversation_history) > _MAX_CHANNELS_IN_HISTORY:
        cleanup_stale_conversation_history()


def cleanup_stale_conversation_history():
    """Remove stale channels from conversation_history to prevent memory leaks."""
    now = time.time()
    channels_to_remove = []

    # Find channels with no recent activity
    for channel_id in list(conversation_history.keys()):
        last_activity = _channel_last_activity.get(channel_id, 0)
        if now - last_activity > _STALE_CHANNEL_THRESHOLD:
            channels_to_remove.append(channel_id)

    # If still over limit, remove oldest channels
    if len(conversation_history) - len(channels_to_remove) > _MAX_CHANNELS_IN_HISTORY:
        # Sort by last activity, oldest first
        sorted_channels = sorted(
            conversation_history.keys(),
            key=lambda cid: _channel_last_activity.get(cid, 0)
        )
        # Remove oldest until under limit
        excess = len(conversation_history) - _MAX_CHANNELS_IN_HISTORY
        channels_to_remove.extend(sorted_channels[:excess])

    # Remove duplicates and clean up
    channels_to_remove = list(set(channels_to_remove))
    for ch in channels_to_remove:
        conversation_history.pop(ch, None)
        _recent_message_hashes.pop(ch, None)
        _channel_last_activity.pop(ch, None)
        channel_names.pop(ch, None)

    if channels_to_remove:
        log.debug(f"Cleaned up {len(channels_to_remove)} stale channels from conversation history")


def update_history_on_edit(channel_id: int, old_content: str, new_content: str, user_name: str = None):
    """Update history when a message is edited."""
    if channel_id not in conversation_history:
        return
    
    history = conversation_history[channel_id]
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if msg["role"] == "user":
            stored = msg["content"]
            if isinstance(stored, str) and old_content in stored:
                history[i]["content"] = stored.replace(old_content, new_content)
                return


def remove_assistant_from_history(channel_id: int, count: int = 1):
    """Remove last N assistant messages from history."""
    if channel_id not in conversation_history:
        return
    
    history = conversation_history[channel_id]
    removed = 0
    
    for i in range(len(history) - 1, -1, -1):
        if removed >= count:
            break
        if history[i]["role"] == "assistant":
            del history[i]
            removed += 1


def clear_history(channel_id: int):
    """Clear conversation history for a channel."""
    if channel_id in conversation_history:
        del conversation_history[channel_id]


def format_history_for_ai(channel_id: int, limit: int = 50) -> List[dict]:
    """Format history for AI consumption with optional limit."""
    history = get_history(channel_id)[-limit:]  # Apply limit
    formatted = []

    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        author = msg.get("author")

        if role == "user" and author:
            content = f"{author}: {content}"

        formatted.append({"role": role, "content": content})

    return formatted


def format_history_split(channel_id: int, total_limit: int = 200, immediate_count: int = 5, 
                         current_bot_name: str = None) -> Tuple[List[dict], List[dict]]:
    """
    Split history into two parts for the new context structure:
    - history: older messages (background context)
    - immediate: recent messages (placed after chatroom context for focused response)
    
    If current_bot_name is provided, other bots' messages will be tagged with their name
    (like user messages) to prevent personality bleed.
    
    Returns: (history_messages, immediate_messages)
    """
    all_history = get_history(channel_id)[-total_limit:]  # Apply total limit
    
    # Format all messages
    formatted = []
    for msg in all_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        author = msg.get("author")

        if role == "user" and author:
            # User messages get Author: prefix (no brackets)
            content = f"{author}: {content}"
        elif role == "assistant":
            # Bot messages: check if this is from the CURRENT bot or a DIFFERENT bot
            if author and current_bot_name and author.lower() != current_bot_name.lower():
                # Different bot - treat as "user" role with name prefix to prevent personality bleed
                role = "user"
                content = f"{author}: {content}"
            # If same bot or no author field, keep as assistant (no prefix)

        formatted.append({"role": role, "content": content})
    
    # Split into history and immediate
    if len(formatted) <= immediate_count:
        # Not enough messages - everything goes to immediate
        return [], formatted
    
    history = formatted[:-immediate_count]
    immediate = formatted[-immediate_count:]
    
    return history, immediate


def get_active_users(channel_id: int, limit: int = 20) -> List[str]:
    """Get list of unique users who have participated recently."""
    history = get_history(channel_id)[-limit:]
    users = set()

    for msg in history:
        author = msg.get("author")
        if author and msg.get("role") == "user":
            users.add(author)

    return list(users)


def get_other_bot_names(channel_id: int, current_bot_name: str) -> List[str]:
    """Get names of other bot characters from history."""
    history = get_history(channel_id)
    other_bots = set()
    for msg in history:
        if msg.get("role") == "assistant":
            author = msg.get("author")
            if author and author.lower() != current_bot_name.lower():
                other_bots.add(author)
    return list(other_bots)


# --- Reply Context ---

def get_reply_context(message: discord.Message) -> Optional[tuple]:
    """Extract who the user is replying to and what that message said.
    Returns: (author_name, message_content) or None
    """
    if not message.reference or not message.reference.resolved:
        return None
    
    replied = message.reference.resolved
    
    # Get author name
    if hasattr(replied.author, 'display_name') and replied.author.display_name:
        author = replied.author.display_name
    elif hasattr(replied.author, 'global_name') and replied.author.global_name:
        author = replied.author.global_name
    else:
        author = replied.author.name
    
    # Get message content (truncated if too long)
    content = replied.content[:300] if replied.content else ""
    
    return (author, content)


def get_user_display_name(user: discord.User | discord.Member) -> str:
    """Get display name for a user."""
    if hasattr(user, 'display_name') and user.display_name:
        return user.display_name
    elif hasattr(user, 'global_name') and user.global_name:
        return user.global_name
    return user.name


# --- Sticker Support ---

def get_sticker_info(message: discord.Message) -> Optional[str]:
    """Get sticker info from message."""
    if not message.stickers:
        return None
    sticker = message.stickers[0]
    return f"sent a sticker: '{sticker.name}'"


# --- Emoji Handling ---

# LRU-style emoji cache using OrderedDict for O(1) operations
_emoji_cache: OrderedDict[int, Dict[str, discord.Emoji]] = OrderedDict()
_EMOJI_CACHE_MAX_SIZE = 50  # Max number of guilds to cache


def _update_emoji_cache_lru(guild_id: int):
    """Update LRU order and evict oldest if needed (O(1) operations)."""
    # Move to end if already exists (marks as recently used)
    if guild_id in _emoji_cache:
        _emoji_cache.move_to_end(guild_id)

    # Evict oldest entries if over limit
    while len(_emoji_cache) > _EMOJI_CACHE_MAX_SIZE:
        _emoji_cache.popitem(last=False)  # Remove oldest (first) item


def get_guild_emojis(guild: discord.Guild, max_count: int = MAX_EMOJIS_IN_PROMPT) -> str:
    """Get formatted emoji list for a guild and cache for later use."""
    if not guild:
        return ""

    emojis = list(guild.emojis)[:max_count]
    if not emojis:
        return ""

    _emoji_cache[guild.id] = {e.name: e for e in guild.emojis}
    _update_emoji_cache_lru(guild.id)
    emoji_list = [f":{e.name}:" for e in emojis]
    return ", ".join(emoji_list)


def convert_emojis_in_text(text: str, guild: discord.Guild) -> str:
    """Convert :emoji_name: to proper Discord format (including animated)."""
    if not guild or guild.id not in _emoji_cache:
        return text

    cache = _emoji_cache[guild.id]

    def replace_emoji(match):
        name = match.group(1)
        if name in cache:
            emoji = cache[name]
            if emoji.animated:
                return f"<a:{emoji.name}:{emoji.id}>"
            else:
                return f"<:{emoji.name}:{emoji.id}>"
        return match.group(0)

    result = RE_EMOJI_SHORTCODE.sub(replace_emoji, text)

    # AFTER conversion, clean up malformed emoji-like tags that LLMs sometimes generate
    # Remove incomplete emoji tags at end of string (e.g., "<:emoji" or "<a:")
    result = RE_BROKEN_EMOJI_END.sub('', result)

    # Remove orphaned emoji IDs without proper format (e.g., "12345678901234567890>")
    result = RE_ORPHAN_SNOWFLAKE.sub('', result)

    # Remove empty angle bracket pairs
    result = RE_EMPTY_ANGLE.sub('', result)

    # Remove malformed tags that have colons but no valid emoji structure
    result = RE_MALFORMED_EMOJI.sub('', result)

    return result.strip()


def parse_reactions(content: str) -> Tuple[str, List[str]]:
    """Parse [REACT: emoji] tags from response."""
    reactions = RE_REACTION_TAG.findall(content)
    cleaned = RE_REACTION_TAG.sub('', content).strip()
    return cleaned, [r.strip() for r in reactions]


async def add_reactions(message: discord.Message, reactions: List[str], guild: discord.Guild = None):
    """Add reactions to a message."""
    for reaction in reactions:
        try:
            if reaction.startswith(':') and reaction.endswith(':'):
                emoji_name = reaction[1:-1]
                if guild:
                    custom_emoji = discord.utils.get(guild.emojis, name=emoji_name)
                    if custom_emoji:
                        await message.add_reaction(custom_emoji)
                        continue
            await message.add_reaction(reaction)
        except discord.HTTPException:
            pass


# --- Media Handling ---

# Global aiohttp session for reuse
_http_session: Optional[aiohttp.ClientSession] = None


async def get_http_session() -> aiohttp.ClientSession:
    """Get or create a reusable HTTP session."""
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    return _http_session


async def close_http_session():
    """Close the HTTP session. Call on shutdown."""
    global _http_session
    if _http_session is not None and not _http_session.closed:
        await _http_session.close()
        _http_session = None
        log.debug("HTTP session closed")


async def download_image_as_base64(url: str) -> Optional[str]:
    """Download an image and convert to base64."""
    try:
        session = await get_http_session()
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.read()
                return base64.b64encode(data).decode('utf-8')
    except Exception as e:
        log.warn(f"Failed to download image: {e}")
    return None


async def process_attachments(message: discord.Message) -> List[dict]:
    """Process message attachments into AI-consumable format."""
    content_parts = []

    # Add text content if present
    if message.content and message.content.strip():
        content_parts.append({"type": "text", "text": message.content.strip()})

    for attachment in message.attachments:
        if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
            base64_data = await download_image_as_base64(attachment.url)
            if base64_data:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{attachment.filename.split('.')[-1]};base64,{base64_data}"}
                })

    # Return multimodal content if we have any images
    has_images = any(p.get("type") == "image_url" for p in content_parts)
    if has_images:
        # Ensure there's always a text part (required by some APIs)
        if not any(p.get("type") == "text" for p in content_parts):
            content_parts.insert(0, {"type": "text", "text": "(user sent an image)"})
        return content_parts
    return None


# --- Message Splitting ---

def split_message(content: str, max_length: int = 2000) -> List[str]:
    """Split a long message into Discord-compatible chunks."""
    if len(content) <= max_length:
        return [content]
    
    chunks = []
    current_chunk = ""
    paragraphs = content.split('\n\n')
    
    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 <= max_length:
            current_chunk += ('\n\n' if current_chunk else '') + para
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(para) > max_length:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                current_chunk = ""
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) + 1 <= max_length:
                        current_chunk += (' ' if current_chunk else '') + sentence
                    else:
                        if current_chunk:
                            chunks.append(current_chunk)
                        current_chunk = sentence[:max_length]
            else:
                current_chunk = para
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks


# --- Multi-part Response Tracking ---

_MULTIPART_MAX_PER_CHANNEL = 500
_MULTIPART_MAX_GLOBAL = 5000  # Total entries across all channels


def store_multipart_response(channel_id: int, message_ids: List[int], full_content: str):
    """Store a multi-part response for tracking."""
    if channel_id not in multipart_responses:
        multipart_responses[channel_id] = {}

    for msg_id in message_ids:
        multipart_responses[channel_id][msg_id] = full_content

    # Per-channel cleanup: limit to 500 entries per channel
    if len(multipart_responses[channel_id]) > _MULTIPART_MAX_PER_CHANNEL:
        # Remove oldest entries (lowest message IDs)
        sorted_ids = sorted(multipart_responses[channel_id].keys())
        for old_id in sorted_ids[:-_MULTIPART_MAX_PER_CHANNEL]:
            del multipart_responses[channel_id][old_id]

    # Global cleanup: limit total entries across all channels
    total_entries = sum(len(ch) for ch in multipart_responses.values())
    if total_entries > _MULTIPART_MAX_GLOBAL:
        # Collect all entries with channel info, sort by message ID (oldest first)
        all_entries = []
        for ch_id, msgs in multipart_responses.items():
            for msg_id in msgs:
                all_entries.append((msg_id, ch_id))
        all_entries.sort()

        # Remove oldest entries until under limit
        entries_to_remove = total_entries - _MULTIPART_MAX_GLOBAL
        for msg_id, ch_id in all_entries[:entries_to_remove]:
            if ch_id in multipart_responses and msg_id in multipart_responses[ch_id]:
                del multipart_responses[ch_id][msg_id]

        # Clean up empty channel dicts
        empty_channels = [ch_id for ch_id, msgs in multipart_responses.items() if not msgs]
        for ch_id in empty_channels:
            del multipart_responses[ch_id]


# --- Autonomous Response ---

AUTONOMOUS_FILE = "bot_data/autonomous.json"


class AutonomousManager:
    """Manages autonomous (unprompted) responses with persistent storage."""
    
    def __init__(self):
        self.enabled_channels: Dict[int, float] = {}
        self.channel_cooldowns: Dict[int, timedelta] = {}
        self.allow_bot_triggers: Dict[int, bool] = {}  # Per-channel bot trigger control
        self.last_autonomous: Dict[int, datetime] = {}
        self.default_cooldown = timedelta(minutes=2)
        self._load()
    
    def _load(self):
        """Load settings from disk."""
        data = safe_json_load(AUTONOMOUS_FILE, default={})
        for ch_id, settings in data.items():
            ch_id = int(ch_id)
            self.enabled_channels[ch_id] = settings.get('chance', 0.05)
            self.channel_cooldowns[ch_id] = timedelta(minutes=settings.get('cooldown', 2))
            self.allow_bot_triggers[ch_id] = settings.get('allow_bot_triggers', False)

    def _save(self):
        """Save settings to disk."""
        data = {}
        for ch_id, chance in self.enabled_channels.items():
            cooldown = self.channel_cooldowns.get(ch_id, self.default_cooldown)
            data[str(ch_id)] = {
                'chance': chance,
                'cooldown': int(cooldown.total_seconds() // 60),
                'allow_bot_triggers': self.allow_bot_triggers.get(ch_id, False)
            }
        os.makedirs(os.path.dirname(AUTONOMOUS_FILE), exist_ok=True)
        safe_json_save(AUTONOMOUS_FILE, data)
    
    def set_channel(self, channel_id: int, enabled: bool, chance: float = 0.05,
                    cooldown_mins: int = 2, allow_bot_triggers: bool = False):
        """Set autonomous mode settings for a channel.
        
        Args:
            channel_id: Discord channel ID
            enabled: Whether autonomous mode is enabled
            chance: Probability of responding (0.0-1.0)
            cooldown_mins: Minimum minutes between autonomous responses
            allow_bot_triggers: Whether bots/apps can trigger name-based responses
        """
        if enabled:
            self.enabled_channels[channel_id] = min(max(chance, 0.0), 1.0)
            self.channel_cooldowns[channel_id] = timedelta(minutes=min(max(cooldown_mins, 0), 10))
            self.allow_bot_triggers[channel_id] = allow_bot_triggers
        elif channel_id in self.enabled_channels:
            del self.enabled_channels[channel_id]
            if channel_id in self.channel_cooldowns:
                del self.channel_cooldowns[channel_id]
            if channel_id in self.allow_bot_triggers:
                del self.allow_bot_triggers[channel_id]
        self._save()
    
    def can_bot_trigger(self, channel_id: int) -> bool:
        """Check if bots can trigger name-based responses in this channel."""
        return self.allow_bot_triggers.get(channel_id, False)
    
    def should_respond(self, channel_id: int) -> bool:
        import random
        if channel_id not in self.enabled_channels:
            return False
        cooldown = self.channel_cooldowns.get(channel_id, self.default_cooldown)
        last = self.last_autonomous.get(channel_id)
        if last and datetime.now() - last < cooldown:
            return False
        if random.random() < self.enabled_channels[channel_id]:
            self.last_autonomous[channel_id] = datetime.now()
            return True
        return False
    
    def get_status(self, channel_id: int) -> str:
        if channel_id in self.enabled_channels:
            cooldown = self.channel_cooldowns.get(channel_id, self.default_cooldown)
            bot_status = "bots: ✓" if self.allow_bot_triggers.get(channel_id, False) else "bots: ✗"
            return f"✅ Enabled ({self.enabled_channels[channel_id]*100:.0f}% chance, {int(cooldown.total_seconds()//60)}min cooldown, {bot_status})"
        return "❌ Disabled"


autonomous_manager = AutonomousManager()


# --- Bot Registry for Cross-Bot Awareness ---

_bot_registry: Dict[int, dict] = {}  # bot_user_id -> {name, character_name, user_id}


def register_bot(bot_instance):
    """Register a bot instance for cross-bot awareness.

    Called from BotInstance.on_ready() to make bots aware of each other.

    Args:
        bot_instance: BotInstance object with client and character attributes
    """
    if bot_instance.client.user:
        _bot_registry[bot_instance.client.user.id] = {
            "name": bot_instance.name,
            "character_name": bot_instance.character.name if bot_instance.character else None,
            "user_id": bot_instance.client.user.id
        }
        log.info(f"Registered bot '{bot_instance.name}' (ID: {bot_instance.client.user.id}) in bot registry")


def unregister_bot(bot_user_id: int):
    """Remove a bot from the registry.

    Args:
        bot_user_id: Discord user ID of the bot to remove
    """
    if bot_user_id in _bot_registry:
        del _bot_registry[bot_user_id]


def get_other_bots_mentionable(current_bot_id: int, guild) -> List[dict]:
    """Get list of other bots that can be mentioned.

    Args:
        current_bot_id: Discord user ID of the current bot (to exclude)
        guild: Discord guild object to check membership

    Returns:
        List of dicts with 'character_name', 'user_id', 'mention_syntax'
    """
    bots = []
    for bot_id, info in _bot_registry.items():
        if bot_id == current_bot_id:
            continue

        # Check if bot is in this guild
        if guild and guild.get_member(bot_id):
            bots.append({
                "character_name": info["character_name"],
                "user_id": bot_id,
                "mention_syntax": f"<@{bot_id}>"
            })

    return bots


def get_mentionable_users(channel_id: int, limit: int = 10) -> List[dict]:
    """Get list of users who can be mentioned based on conversation history.

    Args:
        channel_id: Discord channel ID
        limit: Maximum number of users to return

    Returns:
        List of dicts with 'name', 'user_id', 'mention_syntax'
    """
    users = []
    seen_ids = set()

    # Get from recent message authors in history
    history = get_history(channel_id)
    for msg in reversed(history[-50:]):
        user_id = msg.get("user_id")
        author = msg.get("author")

        if user_id and user_id not in seen_ids and author:
            seen_ids.add(user_id)
            users.append({
                "name": author,
                "user_id": user_id,
                "mention_syntax": f"<@{user_id}>"
            })
            if len(users) >= limit:
                break

    return users


def process_outgoing_mentions(content: str, mentionable_users: list = None,
                               mentionable_bots: list = None) -> str:
    """Process AI response to convert name-based mentions to Discord format.

    The AI might generate "@Username" or "@username" - this converts them
    to proper Discord mention format <@user_id>.

    Args:
        content: AI-generated response text
        mentionable_users: List of user dicts from get_mentionable_users()
        mentionable_bots: List of bot dicts from get_other_bots_mentionable()

    Returns:
        Content with @Name converted to <@user_id> where applicable
    """
    if not content:
        return content

    # Build lookup of name -> mention syntax (case-insensitive)
    mention_lookup = {}

    if mentionable_users:
        for user in mentionable_users:
            name_lower = user['name'].lower()
            mention_lookup[name_lower] = user['mention_syntax']

    if mentionable_bots:
        for bot in mentionable_bots:
            if bot.get('character_name'):
                name_lower = bot['character_name'].lower()
                mention_lookup[name_lower] = bot['mention_syntax']

    if not mention_lookup:
        return content

    # Pattern: @name or @Name (word characters after @)
    import re

    def replace_mention(match):
        name = match.group(1).lower()
        if name in mention_lookup:
            return mention_lookup[name]
        return match.group(0)  # Keep original if not found

    # Match @word patterns (handles @Username, @user_name, etc.)
    pattern = r'@(\w+)'
    content = re.sub(pattern, replace_mention, content)

    return content

