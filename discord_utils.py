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
USER_ALIAS_CACHE_FILE = os.path.join(DATA_DIR, "user_alias_cache.json")

# Channel name mapping (channel_id -> name) for readable storage
channel_names: Dict[int, str] = {}

# Durable per-guild alias cache to keep mention resolution working when
# members are offline or not present in the immediate context window.
_user_alias_cache: Dict[str, Dict[str, dict]] = {}
_user_alias_cache_loaded = False
_user_alias_cache_lock = threading.Lock()

# Conversation history limits
_MAX_CHANNELS_IN_HISTORY = 500  # Max channels to keep in memory
_STALE_CHANNEL_THRESHOLD = 86400  # 24 hours - channels with no activity are candidates for cleanup
_channel_last_activity: Dict[int, float] = {}  # Track last activity per channel
_channel_history_seq: Dict[int, int] = {}  # Monotonic sequence per channel (stable snapshots)

# Pre-compiled patterns for resolve_discord_formatting
RE_CUSTOM_EMOJI = re.compile(r'<a?:([a-zA-Z0-9_]+):\d+>')
RE_USER_MENTION = re.compile(r'<@!?(\d+)>')
RE_CHANNEL_MENTION = re.compile(r'<#(\d+)>')
RE_ROLE_MENTION = re.compile(r'<@&(\d+)>')
RE_TIMESTAMP = re.compile(r'<t:(\d+)(?::[tTdDfFR])?>')

# Pre-compiled patterns for convert_emojis_in_text
RE_EMOJI_SHORTCODE = re.compile(r':([a-zA-Z0-9_]+):')
RE_BROKEN_EMOJI_END = re.compile(r'<a?:[a-zA-Z0-9_]*(?::\d*)?$')
RE_INCOMPLETE_TAG = re.compile(r'<[a-zA-Z][a-zA-Z0-9_]*:\d{17,21}(?!>)')
RE_ORPHAN_SNOWFLAKE = re.compile(r'(?<![:\d@#&/])\d{17,21}>(?!\S)')
RE_MALFORMED_EMOJI_PREFIX = re.compile(r'<a?:([a-zA-Z0-9_]+):\d+(?![\d>])')
RE_EMPTY_ANGLE = re.compile(r'<>')
RE_MALFORMED_EMOJI = re.compile(
    r'<(?!'
    r'a?:[a-zA-Z0-9_]+:\d{17,21}>'  # Custom emoji: <:name:id> or <a:name:id>
    r'|@!?\d{17,21}>'               # User mention: <@id> or <@!id>
    r'|@&\d{17,21}>'                # Role mention: <@&id>
    r'|#\d{17,21}>'                 # Channel mention: <#id>
    r'|/[a-zA-Z0-9_-]+:\d{17,21}>'  # Slash command: </command:id>
    r'|t:\d+(?::[tTdDfFR])?>'       # Timestamp: <t:unix> or <t:unix:style>
    r')[^>]{0,50}>'
)

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
    global conversation_history, channel_names, _channel_history_seq

    data = safe_json_load(HISTORY_CACHE_FILE, default={})
    conversation_history = {}
    channel_names = {}
    _channel_history_seq = {}
    if not data:
        return

    # Handle both old format (list) and new format (dict with name/messages)
    for k, v in data.items():
        channel_id = int(k)
        if isinstance(v, dict) and "messages" in v:
            # New format with name
            messages = v["messages"] if isinstance(v["messages"], list) else []
            conversation_history[channel_id] = messages
            if "name" in v:
                channel_names[channel_id] = v["name"]
        else:
            # Old format - just list of messages
            messages = v if isinstance(v, list) else []
            conversation_history[channel_id] = messages

        # Restore per-channel history sequence counter (or infer fallback).
        max_seq = 0
        for msg in conversation_history[channel_id]:
            seq = msg.get("history_seq")
            if isinstance(seq, int) and seq > max_seq:
                max_seq = seq
        if max_seq <= 0 and conversation_history[channel_id]:
            max_seq = len(conversation_history[channel_id])
        if max_seq > 0:
            _channel_history_seq[channel_id] = max_seq

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


def sanitize_discord_syntax_fallback(content: str) -> str:
    """Sanitize Discord syntax when guild context is unavailable (e.g., DMs).

    This is a fallback that removes IDs but preserves readable format:
    - <:emoji_name:123> → :emoji_name:
    - <@123456> → @user
    - <#123456> → #channel
    - <@&123456> → @role
    - <t:123:R> → readable timestamp
    """
    # Custom emojis: <:name:id> or <a:name:id> → :name:
    content = RE_CUSTOM_EMOJI.sub(r':\1:', content)

    # User mentions without guild: <@123> or <@!123> → @user
    content = RE_USER_MENTION.sub('@user', content)

    # Channel mentions without guild: <#123> → #channel
    content = RE_CHANNEL_MENTION.sub('#channel', content)

    # Role mentions without guild: <@&123> → @role
    content = RE_ROLE_MENTION.sub('@role', content)

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


def add_to_history(channel_id: int, role: str, content: str, author_name: str = None,
                   user_id: int = None, guild=None, message_id: int = None,
                   reply_to_message_id: int = None):
    """Add a message to conversation history.

    Args:
        channel_id: Discord channel ID
        role: Message role ('user' or 'assistant')
        content: Message content
        author_name: Display name of the author
        user_id: Discord user ID (for mention features)
        guild: Discord guild object for resolving mentions (optional)
        message_id: Discord message ID (for precise duplicate detection)
        reply_to_message_id: For assistant messages, the source user message ID replied to
    """
    if channel_id not in conversation_history:
        conversation_history[channel_id] = []

    # Track activity for this channel
    _channel_last_activity[channel_id] = time.time()

    # Keep a durable alias map for deterministic mention fallback.
    if guild and user_id:
        try:
            _update_user_alias_cache_from_context(
                guild=guild,
                user_id=int(user_id),
                author_name=author_name
            )
        except Exception:
            pass

    # Sanitize Discord syntax before storage (fix before sending to LLM)
    if guild:
        content = resolve_discord_formatting(content, guild)
    else:
        content = sanitize_discord_syntax_fallback(content)

    # Strip character name prefixes from bot/assistant messages
    if role == "user" and author_name:
        content = strip_character_prefix(content)

    msg = {"role": role, "content": content}
    if author_name:
        msg["author"] = author_name
    if user_id:
        msg["user_id"] = user_id
    if message_id:
        msg["message_id"] = message_id
    if reply_to_message_id:
        msg["reply_to_message_id"] = reply_to_message_id

    # Fast hash-based duplicate detection (O(1) instead of O(n))
    msg_hash = hash((role, content, author_name or '', message_id or 0, reply_to_message_id or 0))
    if channel_id not in _recent_message_hashes:
        _recent_message_hashes[channel_id] = OrderedDict()

    if msg_hash in _recent_message_hashes[channel_id]:
        return  # Already added

    # Stamp stable ordering metadata only for newly-added messages.
    next_seq = _channel_history_seq.get(channel_id, 0) + 1
    _channel_history_seq[channel_id] = next_seq
    msg["history_seq"] = next_seq
    msg["history_ts"] = time.time()

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
        _channel_history_seq.pop(ch, None)
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


# Channels where history was explicitly cleared (suppresses auto-recall)
_cleared_channels: set = set()


def clear_history(channel_id: int):
    """Clear conversation history for a channel."""
    conversation_history.pop(channel_id, None)
    _recent_message_hashes.pop(channel_id, None)
    _channel_last_activity.pop(channel_id, None)
    _channel_history_seq.pop(channel_id, None)
    channel_names.pop(channel_id, None)
    multipart_responses.pop(channel_id, None)
    _cleared_channels.add(channel_id)
    save_history(force=True)


def was_recently_cleared(channel_id: int) -> bool:
    """Check if a channel's history was explicitly cleared (suppresses auto-recall)."""
    return channel_id in _cleared_channels


def acknowledge_cleared(channel_id: int):
    """Remove the cleared flag after the first successful message exchange."""
    _cleared_channels.discard(channel_id)


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


def format_history_split_from_messages(messages: List[dict], immediate_count: int = 5,
                                       current_bot_name: str = None) -> Tuple[List[dict], List[dict]]:
    """Format and split a provided history snapshot (legacy transcript mode)."""
    formatted = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        author = msg.get("author")

        if role == "user" and author:
            content = f"{author}: {content}"
        elif role == "assistant":
            if author and current_bot_name and author.lower() != current_bot_name.lower():
                role = "user"
                content = f"{author}: {content}"

        formatted.append({"role": role, "content": content})

    if len(formatted) <= immediate_count:
        return [], formatted
    return formatted[:-immediate_count], formatted[-immediate_count:]


def format_history_split(channel_id: int, total_limit: int = 200, immediate_count: int = 5,
                         current_bot_name: str = None,
                         history_override: Optional[List[dict]] = None) -> Tuple[List[dict], List[dict]]:
    """Split history into background + immediate sections (legacy transcript mode)."""
    source = history_override if history_override is not None else get_history(channel_id)
    all_history = source[-total_limit:]
    return format_history_split_from_messages(all_history, immediate_count=immediate_count, current_bot_name=current_bot_name)


def format_history_split_structured_from_messages(messages: List[dict], immediate_count: int = 5,
                                                  current_bot_name: str = None) -> Tuple[List[dict], List[dict]]:
    """Format and split a provided history snapshot (structured mode)."""
    formatted = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        author = msg.get("author")
        author_id = msg.get("user_id")

        speaker_kind = "assistant"
        if role == "assistant" and author and current_bot_name and author.lower() != current_bot_name.lower():
            role = "user"
            speaker_kind = "bot"
        elif role == "user":
            speaker_kind = "user"

        # Add deterministic speaker marker in structured mode so identity survives
        # provider-side metadata stripping.
        if role == "user" and author:
            marker = f"[speaker={author}|kind={speaker_kind}]"
            content = f"{marker} {content}".strip()

        entry = {"role": role, "content": content}
        if author:
            entry["author"] = author
        if author_id:
            entry["author_id"] = author_id
        if msg.get("message_id"):
            entry["message_id"] = msg.get("message_id")
        if msg.get("reply_to_message_id"):
            entry["reply_to_message_id"] = msg.get("reply_to_message_id")

        formatted.append(entry)

    if len(formatted) <= immediate_count:
        return [], formatted
    return formatted[:-immediate_count], formatted[-immediate_count:]


def format_history_split_structured(channel_id: int, total_limit: int = 200,
                                    immediate_count: int = 5,
                                    current_bot_name: str = None,
                                    history_override: Optional[List[dict]] = None) -> Tuple[List[dict], List[dict]]:
    """Split history for structured payload mode."""
    source = history_override if history_override is not None else get_history(channel_id)
    all_history = source[-total_limit:]
    return format_history_split_structured_from_messages(all_history, immediate_count=immediate_count, current_bot_name=current_bot_name)


def get_active_users(channel_id: int, limit: int = 20, history_override: Optional[List[dict]] = None) -> List[str]:
    """Get list of unique users who have participated recently."""
    source = history_override if history_override is not None else get_history(channel_id)
    history = source[-limit:]
    users = set()

    for msg in history:
        author = msg.get("author")
        if author and msg.get("role") == "user":
            users.add(author)

    return list(users)


def get_other_bot_names(channel_id: int, current_bot_name: str,
                        history_override: Optional[List[dict]] = None) -> List[str]:
    """Get names of other bot characters from history and the bot registry."""
    other_bots = set()

    # From history (bots that have spoken in this channel)
    history = history_override if history_override is not None else get_history(channel_id)
    for msg in history:
        if msg.get("role") == "assistant":
            author = msg.get("author")
            if author and author.lower() != current_bot_name.lower():
                other_bots.add(author)

    # From bot registry (all registered bots, even if they haven't spoken yet)
    for bot_id, info in _bot_registry.items():
        char_name = info.get("character_name")
        if char_name and char_name.lower() != current_bot_name.lower():
            other_bots.add(char_name)

    return list(other_bots)


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
    if not text:
        return text

    result = text
    if guild and guild.id in _emoji_cache:
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

        result = RE_EMOJI_SHORTCODE.sub(replace_emoji, result)

    # Drop malformed custom emoji fragments regardless of cache state.
    result = RE_MALFORMED_EMOJI_PREFIX.sub('', result)
    result = RE_INCOMPLETE_TAG.sub('', result)
    result = RE_BROKEN_EMOJI_END.sub('', result)
    result = RE_ORPHAN_SNOWFLAKE.sub('', result)
    result = RE_EMPTY_ANGLE.sub('', result)
    result = re.sub(r'\s{2,}', ' ', result)
    result = re.sub(r'\s+([,!?;:.])', r'\1', result)

    # Disabled: RE_MALFORMED_EMOJI is too aggressive — it can match legitimate
    # text between < and > (up to 50 chars) and truncate messages mid-sentence.
    # RE_BROKEN_EMOJI_END (applied elsewhere) handles the common case of
    # incomplete emoji at string end, which is sufficient.
    # result = RE_MALFORMED_EMOJI.sub('', result)

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


def _build_mention_aliases(*names: Optional[str]) -> List[str]:
    """Build a de-duplicated alias list for @mention matching."""
    aliases = []
    seen = set()
    for name in names:
        if not name or not isinstance(name, str):
            continue
        cleaned = re.sub(r'\s+', ' ', name.strip().lstrip('@'))
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(cleaned)
    return aliases


def _load_user_alias_cache() -> Dict[str, Dict[str, dict]]:
    """Load alias cache from disk once per process."""
    global _user_alias_cache_loaded, _user_alias_cache
    with _user_alias_cache_lock:
        if _user_alias_cache_loaded:
            return _user_alias_cache

        raw = safe_json_load(USER_ALIAS_CACHE_FILE, default={})
        if isinstance(raw, dict):
            _user_alias_cache = raw
        else:
            _user_alias_cache = {}
        _user_alias_cache_loaded = True
        return _user_alias_cache


def _persist_user_alias_cache():
    """Persist alias cache to disk."""
    safe_json_save(USER_ALIAS_CACHE_FILE, _user_alias_cache)


def _record_user_aliases(guild_id: int, user_id: int, aliases: List[str], *, is_bot: bool = False):
    """Record aliases for a guild member in the durable alias cache."""
    if guild_id <= 0 or user_id <= 0:
        return

    cache = _load_user_alias_cache()
    guild_key = str(guild_id)
    user_key = str(user_id)
    normalized = _build_mention_aliases(*(aliases or []))
    if not normalized:
        return

    with _user_alias_cache_lock:
        guild_bucket = cache.setdefault(guild_key, {})
        existing = guild_bucket.get(user_key) or {}
        merged = _build_mention_aliases(*(existing.get("aliases") or []), *normalized)
        guild_bucket[user_key] = {
            "aliases": merged,
            "is_bot": bool(is_bot),
            "updated_at": time.time(),
        }
    _persist_user_alias_cache()


def _update_user_alias_cache_from_context(guild, user_id: int, author_name: Optional[str] = None):
    """Update durable alias cache using current guild/member context."""
    if not guild or user_id <= 0:
        return

    member = None
    try:
        member = guild.get_member(int(user_id))
    except Exception:
        member = None

    aliases = []
    is_bot = False
    if member:
        is_bot = bool(getattr(member, "bot", False))
        aliases = _build_mention_aliases(
            get_user_display_name(member),
            getattr(member, "name", None),
            getattr(member, "global_name", None),
            getattr(member, "nick", None),
            author_name,
        )
    else:
        aliases = _build_mention_aliases(author_name)

    _record_user_aliases(int(getattr(guild, "id", 0)), int(user_id), aliases, is_bot=is_bot)


def get_cached_mention_alias_entries(guild_id: int, include_bots: bool = True) -> List[dict]:
    """Return cached alias entries for mention fallback in a guild."""
    if guild_id <= 0:
        return []

    cache = _load_user_alias_cache()
    guild_bucket = cache.get(str(guild_id))
    if not isinstance(guild_bucket, dict):
        return []

    out = []
    for user_key, payload in guild_bucket.items():
        if not isinstance(payload, dict):
            continue
        try:
            user_id = int(user_key)
        except (TypeError, ValueError):
            continue
        if user_id <= 0:
            continue

        is_bot = bool(payload.get("is_bot"))
        if is_bot and not include_bots:
            continue

        aliases = _build_mention_aliases(*((payload.get("aliases") or [])))
        if not aliases:
            continue

        out.append({
            "user_id": user_id,
            "is_bot": is_bot,
            "aliases": aliases,
            "mention_syntax": f"<@{user_id}>"
        })

    return out


def get_other_bots_mentionable(current_bot_id: int, guild, limit: int = 15) -> List[dict]:
    """Get list of other bots that can be mentioned.

    Args:
        current_bot_id: Discord user ID of the current bot (to exclude)
        guild: Discord guild object to check membership
        limit: Maximum bots to return

    Returns:
        List of dicts with 'character_name', 'name', 'aliases', 'user_id', 'mention_syntax'
    """
    bots = []
    seen_ids = set()
    for bot_id, info in _bot_registry.items():
        if bot_id == current_bot_id:
            continue

        # Check if bot is in this guild
        if guild and guild.get_member(bot_id):
            aliases = _build_mention_aliases(
                info.get("character_name"),
                info.get("name")
            )
            seen_ids.add(bot_id)
            bots.append({
                "character_name": info["character_name"],
                "name": info.get("name"),
                "user_id": bot_id,
                "mention_syntax": f"<@{bot_id}>",
                "aliases": aliases
            })
            if len(bots) >= limit:
                return bots

    # Fallback: include other bot accounts visible in the guild even if they
    # are not in the local registry (e.g., bots from another process/host).
    if guild:
        for member in guild.members:
            if not member.bot:
                continue
            if member.id == current_bot_id or member.id in seen_ids:
                continue

            display_name = get_user_display_name(member)
            aliases = _build_mention_aliases(
                display_name,
                member.name,
                getattr(member, 'global_name', None),
                getattr(member, 'nick', None)
            )
            bots.append({
                "character_name": display_name,
                "name": member.name,
                "user_id": member.id,
                "mention_syntax": f"<@{member.id}>",
                "aliases": aliases
            })
            if len(bots) >= limit:
                break

    return bots


def get_mentionable_users(channel_id: int, limit: int = 10, guild=None,
                          history_override: Optional[List[dict]] = None) -> List[dict]:
    """Get list of users who can be mentioned based on conversation history.

    Args:
        channel_id: Discord channel ID
        limit: Maximum number of users to return
        guild: Optional guild object for fallback member lookup

    Returns:
        List of dicts with 'name', 'username', 'aliases', 'user_id', 'mention_syntax'
    """
    users = []
    seen_ids = set()

    # Primary: Get from recent message authors in history
    history = history_override if history_override is not None else get_history(channel_id)
    for msg in reversed(history[-50:]):
        user_id = msg.get("user_id")
        author = msg.get("author")

        if user_id and user_id not in seen_ids and author:
            seen_ids.add(user_id)

            display_name = author
            username = None
            aliases = _build_mention_aliases(author)

            if guild:
                member = guild.get_member(user_id)
                if member:
                    display_name = get_user_display_name(member)
                    username = member.name
                    aliases = _build_mention_aliases(
                        display_name,
                        member.name,
                        getattr(member, 'global_name', None),
                        getattr(member, 'nick', None),
                        author
                    )

            users.append({
                "name": display_name,
                "username": username,
                "aliases": aliases,
                "user_id": user_id,
                "mention_syntax": f"<@{user_id}>"
            })
            if len(users) >= limit:
                break

    # Fallback: If history is sparse and guild is provided, add recent active members
    if len(users) < 3 and guild:
        log.debug(f"[MENTIONS] History sparse ({len(users)} users), checking guild members")
        # Get members who are online or recently active
        for member in guild.members:
            if member.bot:
                continue  # Skip bots
            if member.id not in seen_ids:
                seen_ids.add(member.id)
                display_name = get_user_display_name(member)
                users.append({
                    "name": display_name,
                    "username": member.name,
                    "aliases": _build_mention_aliases(
                        display_name,
                        member.name,
                        getattr(member, 'global_name', None),
                        getattr(member, 'nick', None)
                    ),
                    "user_id": member.id,
                    "mention_syntax": f"<@{member.id}>"
                })
                if len(users) >= limit:
                    break

    return users


def process_outgoing_mentions(content: str, mentionable_users: list = None,
                               mentionable_bots: list = None, guild=None) -> str:
    """Process AI response to convert name-based mentions to Discord format.

    The AI might generate "@Username" or "@username" - this converts them
    to proper Discord mention format <@user_id>.

    Args:
        content: AI-generated response text
        mentionable_users: List of user dicts from get_mentionable_users()
        mentionable_bots: List of bot dicts from get_other_bots_mentionable()
        guild: Optional guild object to validate existing numeric mentions

    Returns:
        Content with @Name converted to <@user_id> where applicable
    """
    if not content:
        return content

    def normalize_malformed_mentions(text: str) -> str:
        """Normalize malformed mention stubs like '<@ Name' into '@Name' or remove them."""
        # Convert "<@ Name" / "<@! Name" (without closing bracket) into "@Name"
        text = re.sub(
            r'<@!?\s*([A-Za-z][^>\n\r,!?;:.]{0,32}?)(?=[,!?;:.>|]|$)',
            r'@\1',
            text
        )
        # Clean stray trailing ">" after plaintext mentions (e.g. "@Name>")
        text = re.sub(r'@([A-Za-z][A-Za-z0-9 _.\'-]{0,63})>', r'@\1', text)
        # Remove dangling "<@" / "<@!" markers
        text = re.sub(r'<@!?(?=\s|$)', '', text)
        # Clean remaining incomplete fragments (but keep valid numeric mentions like <@123>)
        text = re.sub(r'<@!?(?!\d+>)[^>\s]*(?=\s|$)', '', text)
        return text

    # Build lookup of alias -> mention syntax (case-insensitive)
    mention_lookup = {}
    known_mention_ids = set()

    def register_alias(alias: str, mention_syntax: str):
        if not alias or not mention_syntax:
            return
        normalized = re.sub(r'\s+', ' ', alias.strip().lstrip('@')).lower()
        if not normalized:
            return
        # Keep first mapping for stability.
        if normalized not in mention_lookup:
            mention_lookup[normalized] = mention_syntax

    if mentionable_users:
        for user in mentionable_users:
            mention_syntax = user.get('mention_syntax')
            if not mention_syntax:
                continue
            id_match = re.search(r'<@!?(\d+)>', mention_syntax)
            if id_match:
                known_mention_ids.add(id_match.group(1))

            aliases = user.get('aliases') or [user.get('name'), user.get('username')]
            for alias in aliases:
                register_alias(alias, mention_syntax)

        log.debug(f"[MENTIONS] Built lookup for {len(mention_lookup)} user aliases")
    else:
        log.debug(f"[MENTIONS] mentionable_users is empty or None")

    if mentionable_bots:
        for bot in mentionable_bots:
            mention_syntax = bot.get('mention_syntax')
            if not mention_syntax:
                continue
            id_match = re.search(r'<@!?(\d+)>', mention_syntax)
            if id_match:
                known_mention_ids.add(id_match.group(1))

            aliases = bot.get('aliases') or [bot.get('character_name'), bot.get('name')]
            for alias in aliases:
                register_alias(alias, mention_syntax)

    # Durable alias fallback: supports users not currently present in context
    # (for example offline members or sparse history windows).
    include_cached_bots = mentionable_bots is not None
    if guild:
        for cached in get_cached_mention_alias_entries(guild.id, include_bots=include_cached_bots):
            mention_syntax = cached.get('mention_syntax')
            if not mention_syntax:
                continue
            id_match = re.search(r'<@!?(\d+)>', mention_syntax)
            if id_match:
                known_mention_ids.add(id_match.group(1))
            for alias in cached.get('aliases') or []:
                register_alias(alias, mention_syntax)

    # Fallback: include real guild members in alias lookup so @DisplayName can
    # still resolve even when mentionable_users context is sparse/stale.
    if guild:
        for member in guild.members:
            if member.bot:
                continue
            mention_syntax = f"<@{member.id}>"
            id_match = re.search(r'<@!?(\d+)>', mention_syntax)
            if id_match:
                known_mention_ids.add(id_match.group(1))

            aliases = _build_mention_aliases(
                get_user_display_name(member),
                member.name,
                getattr(member, 'global_name', None),
                getattr(member, 'nick', None)
            )
            for alias in aliases:
                register_alias(alias, mention_syntax)

    # Short-name fallback: if an alias is multi-part (e.g. "Febs WaWa"), allow
    # "@Febs" only when that short alias maps to exactly one mention target.
    short_alias_to_targets = {}
    for alias, mention_syntax in mention_lookup.items():
        if len(alias) < 3:
            continue
        if not re.search(r'[\s._-]', alias):
            continue
        short = re.split(r'[\s._-]+', alias, maxsplit=1)[0].strip()
        if len(short) < 3:
            continue
        short_alias_to_targets.setdefault(short, set()).add(mention_syntax)

    for short, targets in short_alias_to_targets.items():
        if short in mention_lookup:
            continue
        if len(targets) == 1:
            mention_lookup[short] = next(iter(targets))

    # Normalize malformed tags before deciding whether to return early.
    content = normalize_malformed_mentions(content)

    if not mention_lookup and not re.search(r'<@!?\d+>', content) and not guild:
        log.debug(f"[MENTIONS] mention_lookup is empty, returning content unchanged")
        return content.strip()

    log.debug(f"[MENTIONS] Processing content: {content[:100]}...")

    # Normalize AI-generated <@Name> to @Name (keep <@12345> for safety net)
    content = re.sub(r'<@!?([A-Za-z][^>]*)>', r'@\1', content)
    content = normalize_malformed_mentions(content)

    # Track which mention IDs we intentionally insert (for safety-net preservation)
    inserted_mention_ids = set()

    # Replace @Alias patterns with Discord mention syntax.
    # Sort by longest alias first to avoid partial replacements.
    sorted_names = sorted(mention_lookup.keys(), key=len, reverse=True)

    for name in sorted_names:
        mention_syntax = mention_lookup[name]
        # Match @Name with word boundary awareness (case-insensitive)
        # Handles both single-word and multi-word names
        pattern = re.compile(r'@' + re.escape(name) + r'(?=\W|$)', re.IGNORECASE)
        if pattern.search(content):
            log.debug(f"[MENTIONS] Matched @{name} -> {mention_syntax}")
            content = pattern.sub(mention_syntax, content)
            # Extract the user ID from the mention syntax to protect it from the safety net
            id_match = re.search(r'<@!?(\d+)>', mention_syntax)
            if id_match:
                inserted_mention_ids.add(id_match.group(1))
        else:
            log.debug(f"[MENTIONS] No match for @{name}")

    log.debug(f"[MENTIONS] Final content: {content[:100]}...")

    # Safety net: strip unknown/hallucinated raw Discord syntax,
    # but preserve IDs we inserted, known candidates, or valid guild members.
    def strip_if_untrusted(match):
        mention_id = match.group(1)
        if mention_id in inserted_mention_ids or mention_id in known_mention_ids:
            return match.group(0)
        if guild:
            try:
                if guild.get_member(int(mention_id)):
                    return match.group(0)
            except Exception:
                pass
        return ''

    content = re.sub(r'<@!?(\d+)>', strip_if_untrusted, content)
    content = re.sub(r'<#\d+>', '', content)      # Raw channel mentions (always strip)
    content = re.sub(r'<@&\d+>', '', content)     # Raw role mentions (always strip)
    content = normalize_malformed_mentions(content)

    return content.strip()


def strip_unresolved_plain_mentions(content: str) -> str:
    """Demote unresolved plaintext @mentions while preserving valid numeric mentions."""
    if not content:
        return content

    placeholders = {}

    def _protect_valid_mention(match: re.Match) -> str:
        token = f"__VALID_USER_MENTION_{len(placeholders)}__"
        placeholders[token] = match.group(0)
        return token

    cleaned = re.sub(r"<@!?(\d{15,22})>", _protect_valid_mention, content)
    cleaned = re.sub(r"(?<!<)@{2,}(?=[A-Za-z0-9_])", "@", cleaned)
    cleaned = re.sub(r"@[\u200b\u2060\ufeff\s]+(?=[A-Za-z0-9])", "@", cleaned)

    def _demote_plain_mention(match: re.Match) -> str:
        candidate = re.sub(r"\s+", " ", match.group(1)).strip().rstrip(" >.,!?;:")
        if not candidate:
            return ""
        return candidate

    # Match plain @name tokens without touching emails or valid Discord syntax.
    cleaned = re.sub(
        r"(?<![\w<])@([A-Za-z0-9][A-Za-z0-9_.\'-]*(?:\s+[A-Za-z0-9][A-Za-z0-9_.\'-]*){0,7})(?=[^A-Za-z0-9_]|$)",
        _demote_plain_mention,
        cleaned
    )

    cleaned = re.sub(r"(^|[\s(\[{])@+(?=[\s)\]}.,!?;:]|$)", r"\1", cleaned)
    cleaned = re.sub(r"(^|[\s(\[{])@(?=[\s)\]}.,!?;:]|$)", r"\1", cleaned)
    cleaned = re.sub(r"@\s*>", "", cleaned)

    for token, original in placeholders.items():
        cleaned = cleaned.replace(token, original)

    return cleaned.strip()
