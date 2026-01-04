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
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from config import MAX_HISTORY_MESSAGES, MAX_EMOJIS_IN_PROMPT, DATA_DIR
import logger as log


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
HISTORY_SAVE_DEBOUNCE = 5.0  # Minimum seconds between saves


# Conversation history storage (in-memory, per channel/DM)
conversation_history: Dict[int, List[dict]] = {}

# Multi-part response tracking (message_id -> full_content)
multipart_responses: Dict[int, Dict[int, str]] = {}

# History persistence file
HISTORY_CACHE_FILE = os.path.join(DATA_DIR, "history_cache.json")

# Channel name mapping (channel_id -> name) for readable storage
channel_names: Dict[int, str] = {}

# Pre-compiled regex patterns for performance
RE_THINKING_OPEN = re.compile(r'<thinking>.*?</thinking>', re.DOTALL | re.IGNORECASE)
RE_THINK_OPEN = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)
RE_GLM_BOX = re.compile(r'<\|begin_of_box\|>.*?<\|end_of_box\|>', re.DOTALL)
RE_THINKING_PARTIAL_START = re.compile(r'^.*?</thinking>', re.DOTALL | re.IGNORECASE)
RE_THINK_PARTIAL_START = re.compile(r'^.*?</think>', re.DOTALL | re.IGNORECASE)
RE_GLM_PARTIAL_START = re.compile(r'^.*?<\|end_of_box\|>', re.DOTALL)
RE_THINKING_ORPHAN_END = re.compile(r'<thinking>.*$', re.DOTALL | re.IGNORECASE)
RE_THINK_ORPHAN_END = re.compile(r'<think>.*$', re.DOTALL | re.IGNORECASE)
RE_GLM_ORPHAN_END = re.compile(r'<\|begin_of_box\|>.*$', re.DOTALL)
RE_NAME_PREFIX = re.compile(r'^\s*\[[^\]]+\]:\s*', re.MULTILINE)
RE_REPLY_PREFIX = re.compile(r'^\s*\(replying to [^)]+\)\s*', re.IGNORECASE | re.MULTILINE)
RE_RE_PREFIX = re.compile(r'^\s*\(RE:?\s+[^)]+\)\s*', re.IGNORECASE | re.MULTILINE)

# Additional pre-compiled patterns for resolve_discord_formatting
RE_CUSTOM_EMOJI = re.compile(r'<a?:([a-zA-Z0-9_]+):\d+>')
RE_USER_MENTION = re.compile(r'<@!?(\d+)>')
RE_CHANNEL_MENTION = re.compile(r'<#(\d+)>')
RE_ROLE_MENTION = re.compile(r'<@&(\d+)>')
RE_TIMESTAMP = re.compile(r'<t:(\d+)(?::[tTdDfFR])?>')

# Pre-compiled patterns for clean_em_dashes
RE_EM_DASH_BETWEEN_WORDS = re.compile(r'(\w)\s*—\s*(\w)')
RE_EM_DASH_END = re.compile(r'—\s*$')

# Pre-compiled patterns for convert_emojis_in_text
RE_EMOJI_SHORTCODE = re.compile(r':([a-zA-Z0-9_]+):')
RE_BROKEN_EMOJI_END = re.compile(r'<a?:[a-zA-Z0-9_]*$')
RE_ORPHAN_SNOWFLAKE = re.compile(r'(?<![:\d])\d{17,21}>(?!\S)')
RE_EMPTY_ANGLE = re.compile(r'<>')
RE_MALFORMED_EMOJI = re.compile(r'<(?!a?:[a-zA-Z0-9_]+:\d{17,21}>)[^>]{0,50}>')

# Pre-compiled patterns for parse_reactions
RE_REACTION_TAG = re.compile(r'\[REACT:\s*([^\]]+)\]', re.IGNORECASE)

# Pre-compiled pattern for word extraction
RE_WORD = re.compile(r'\w+')

# Pre-compiled pattern for sentence splitting
RE_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')

# Pre-compiled patterns for remove_thinking_tags (additional local LLM formats)
RE_REASONING_TAG = re.compile(r'<reasoning>.*?</reasoning>', re.DOTALL | re.IGNORECASE)
RE_REASON_TAG = re.compile(r'<reason>.*?</reason>', re.DOTALL | re.IGNORECASE)
RE_BRACKET_THINKING = re.compile(r'\[thinking\].*?\[/thinking\]', re.DOTALL | re.IGNORECASE)
RE_BRACKET_THINK = re.compile(r'\[think\].*?\[/think\]', re.DOTALL | re.IGNORECASE)
RE_MARKDOWN_THINKING = re.compile(r'\*\*(?:Thinking|Reasoning|Internal|Analysis):\*\*.*?(?=\n\n|\Z)', re.DOTALL | re.IGNORECASE)
RE_REASONING_PREFIX = re.compile(r'^(?:Thinking:|Reasoning:|Let me think|I need to think|First, I should).*$', re.MULTILINE | re.IGNORECASE)
RE_OUTPUT_WRAPPER = re.compile(r'<output>(.*?)</output>', re.DOTALL | re.IGNORECASE)
RE_RESPONSE_WRAPPER = re.compile(r'<response>(.*?)</response>', re.DOTALL | re.IGNORECASE)
RE_MULTIPLE_NEWLINES = re.compile(r'\n{3,}')


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


def add_to_history(channel_id: int, role: str, content: str, author_name: str = None, reply_to: tuple = None):
    """Add a message to conversation history."""
    if channel_id not in conversation_history:
        conversation_history[channel_id] = []
    
    # Strip character name prefixes from bot/assistant messages
    if role == "user" and author_name:
        content = strip_character_prefix(content)
    
    # Format content with reply context (author, replied_content)
    # Fix personality bleed: clearly separate quoted author from current speaker
    if reply_to and role == "user":
        reply_author, reply_content = reply_to
        if reply_content:
            # Use explicit attribution to prevent LLM from confusing who said what
            # Format: ↩️ marks this as a reply, clearly states who was quoted
            content = f'↩️ [quoting {reply_author}: "{reply_content[:100]}..."] {content}'
        else:
            content = f"↩️ [replying to {reply_author}] {content}"
    
    msg = {"role": role, "content": content}
    if author_name:
        msg["author"] = author_name
    
    # Prevent duplicate entries (from multiple bot instances seeing same message)
    recent_messages = conversation_history[channel_id][-5:] if conversation_history[channel_id] else []
    for recent in recent_messages:
        if recent.get("content") == content and recent.get("role") == role:
            return  # Already added
    
    conversation_history[channel_id].append(msg)
    
    if len(conversation_history[channel_id]) > MAX_HISTORY_MESSAGES:
        conversation_history[channel_id] = conversation_history[channel_id][-MAX_HISTORY_MESSAGES:]


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
            content = f"[{author}]: {content}"
        
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
            # User messages get [Author]: prefix
            content = f"[{author}]: {content}"
        elif role == "assistant" and author:
            # Bot messages: check if this is from the CURRENT bot or a DIFFERENT bot
            if current_bot_name and author.lower() != current_bot_name.lower():
                # Different bot - treat as "user" role with name prefix to prevent personality bleed
                # This makes the LLM see it as another person's speech, not an example to follow
                role = "user"
                content = f"[{author}]: {content}"
            # If same bot or no current_bot_name specified, keep as assistant (no prefix)
        
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


# --- Text Cleanup ---

def remove_thinking_tags(text: str) -> str:
    """Remove all reasoning/thinking blocks from AI output.

    Handles:
    - <thinking>...</thinking>
    - <think>...</think>
    - <|begin_of_box|>...<|end_of_box|> (GLM)
    - Partial/unclosed tags at start or end of response
    - Various other reasoning formats from local LLMs
    """
    if not text:
        return text

    # Remove standard thinking tags (using pre-compiled patterns)
    text = RE_THINKING_OPEN.sub('', text)
    text = RE_THINK_OPEN.sub('', text)

    # Remove GLM box tags
    text = RE_GLM_BOX.sub('', text)

    # Remove partial/unclosed tags at START of response
    text = RE_THINKING_PARTIAL_START.sub('', text)
    text = RE_THINK_PARTIAL_START.sub('', text)
    text = RE_GLM_PARTIAL_START.sub('', text)

    # Remove orphaned opening tags at END
    text = RE_THINKING_ORPHAN_END.sub('', text)
    text = RE_THINK_ORPHAN_END.sub('', text)
    text = RE_GLM_ORPHAN_END.sub('', text)

    # Additional patterns for local LLMs that may use different formats
    # Remove <reasoning>...</reasoning> tags
    text = RE_REASONING_TAG.sub('', text)
    text = RE_REASON_TAG.sub('', text)

    # Remove [thinking]...[/thinking] or [think]...[/think] (bracket style)
    text = RE_BRACKET_THINKING.sub('', text)
    text = RE_BRACKET_THINK.sub('', text)

    # Remove **Thinking:** or **Reasoning:** blocks (markdown style)
    text = RE_MARKDOWN_THINKING.sub('', text)

    # Remove lines that start with common reasoning prefixes
    text = RE_REASONING_PREFIX.sub('', text)

    # Remove <output> wrapper if present (some models wrap actual response)
    text = RE_OUTPUT_WRAPPER.sub(r'\1', text)
    text = RE_RESPONSE_WRAPPER.sub(r'\1', text)

    # Clean up multiple newlines left behind
    text = RE_MULTIPLE_NEWLINES.sub('\n\n', text)

    return text.strip()


def clean_bot_name_prefix(text: str, character_name: str = None) -> str:
    """
    Remove bot persona name prefix and other LLM artifacts from output.
    
    Strips:
    - [Name]: prefixes (learned from history format)
    - (replying to X's message: "...") prefixes
    - CharacterName: prefixes
    - *CharacterName*: prefixes
    """
    if not text:
        return text
    
    # Strip [Name]: prefix pattern (using pre-compiled regex)
    text = RE_NAME_PREFIX.sub('', text)
    
    # Strip (replying to X's message: "...") pattern
    text = RE_REPLY_PREFIX.sub('', text)
    
    # Strip (RE: ...) or (RE ...) patterns
    text = RE_RE_PREFIX.sub('', text)
    
    # Strip character-specific patterns if provided (dynamic, must compile at runtime)
    if character_name:
        patterns = [
            rf'^{re.escape(character_name)}:\s*',
            rf'^{re.escape(character_name)}\s*:\s*',
            rf'^\*{re.escape(character_name)}\*:\s*',
        ]
        for pattern in patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    return text.strip()


def clean_em_dashes(text: str) -> str:
    """Replace em-dashes with appropriate punctuation."""
    # Mid-sentence em-dashes become ", "
    text = re.sub(r'(\w)\s*—\s*(\w)', r'\1, \2', text)
    # End-sentence em-dashes become "-"
    text = re.sub(r'—$', '-', text)
    text = re.sub(r'—\s*$', '-', text)
    return text


# --- Sticker Support ---

def get_sticker_info(message: discord.Message) -> Optional[str]:
    """Get sticker info from message."""
    if not message.stickers:
        return None
    sticker = message.stickers[0]
    return f"sent a sticker: '{sticker.name}'"


# --- Emoji Handling ---

_emoji_cache: Dict[int, Dict[str, discord.Emoji]] = {}


def get_guild_emojis(guild: discord.Guild, max_count: int = MAX_EMOJIS_IN_PROMPT) -> str:
    """Get formatted emoji list for a guild and cache for later use."""
    if not guild:
        return ""
    
    emojis = list(guild.emojis)[:max_count]
    if not emojis:
        return ""
    
    _emoji_cache[guild.id] = {e.name: e for e in guild.emojis}
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
    
    if message.content:
        content_parts.append({"type": "text", "text": message.content})
    
    for attachment in message.attachments:
        if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
            base64_data = await download_image_as_base64(attachment.url)
            if base64_data:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{attachment.filename.split('.')[-1]};base64,{base64_data}"}
                })
    
    return content_parts if len(content_parts) > 1 else None


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

def store_multipart_response(channel_id: int, message_ids: List[int], full_content: str):
    """Store a multi-part response for tracking."""
    if channel_id not in multipart_responses:
        multipart_responses[channel_id] = {}
    
    for msg_id in message_ids:
        multipart_responses[channel_id][msg_id] = full_content
    
    # Cleanup: limit to 500 entries per channel to prevent memory leaks
    if len(multipart_responses[channel_id]) > 500:
        # Remove oldest entries (lowest message IDs)
        sorted_ids = sorted(multipart_responses[channel_id].keys())
        for old_id in sorted_ids[:-500]:
            del multipart_responses[channel_id][old_id]


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

