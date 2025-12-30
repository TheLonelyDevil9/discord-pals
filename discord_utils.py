"""
Discord Pals - Discord Utilities
Helper functions for Discord interactions.
"""

import discord
import re
import base64
import aiohttp
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from config import MAX_HISTORY_MESSAGES, MAX_EMOJIS_IN_PROMPT


# Conversation history storage (in-memory, per channel/DM)
conversation_history: Dict[int, List[dict]] = {}

# Multi-part response tracking (message_id -> full_content)
multipart_responses: Dict[int, Dict[int, str]] = {}


def get_history(channel_id: int) -> List[dict]:
    """Get conversation history for a channel."""
    return conversation_history.get(channel_id, [])


def add_to_history(channel_id: int, role: str, content: str, author_name: str = None, reply_to: tuple = None):
    """Add a message to conversation history."""
    if channel_id not in conversation_history:
        conversation_history[channel_id] = []
    
    # Format content with reply context (author, replied_content)
    if reply_to and role == "user":
        reply_author, reply_content = reply_to
        if reply_content:
            content = f"(replying to {reply_author}'s message: \"{reply_content[:100]}...\") {content}"
        else:
            content = f"(replying to {reply_author}) {content}"
    
    msg = {"role": role, "content": content}
    if author_name:
        msg["author"] = author_name
    
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
    """Remove <thinking>...</thinking> blocks from AI output."""
    # Remove thinking tags and their content
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def clean_bot_name_prefix(text: str, character_name: str = None) -> str:
    """Remove bot persona name prefix from output."""
    if not character_name:
        return text
    
    # Remove patterns like "Firefly:" or "Firefly :" at the start
    patterns = [
        rf'^{re.escape(character_name)}:\s*',
        rf'^{re.escape(character_name)}\s*:\s*',
        rf'^\*{re.escape(character_name)}\*:\s*',
    ]
    
    for pattern in patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    return text


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
    
    # Clean up broken emoji codes
    text = re.sub(r'<[^\s:<>]*\d{15,25}>?', '', text)
    text = re.sub(r'<a?:?[^>]*$', '', text)
    text = re.sub(r'<a[^:>]+>', '', text)
    text = re.sub(r'<[^>]*\d{18,}[^>]*(?!>)', '', text)
    text = re.sub(r'\s*\d{1,5}>', '', text)
    text = re.sub(r'<[^>]{0,3}$', '', text)
    
    pattern = r':([a-zA-Z0-9_]+):'
    
    def replace_emoji(match):
        name = match.group(1)
        if name in cache:
            emoji = cache[name]
            if emoji.animated:
                return f"<a:{emoji.name}:{emoji.id}>"
            else:
                return f"<:{emoji.name}:{emoji.id}>"
        return match.group(0)
    
    return re.sub(pattern, replace_emoji, text)


def parse_reactions(content: str) -> Tuple[str, List[str]]:
    """Parse [REACT: emoji] tags from response."""
    pattern = r'\[REACT:\s*([^\]]+)\]'
    reactions = re.findall(pattern, content, re.IGNORECASE)
    cleaned = re.sub(pattern, '', content, flags=re.IGNORECASE).strip()
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


async def download_image_as_base64(url: str) -> Optional[str]:
    """Download an image and convert to base64."""
    try:
        session = await get_http_session()
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.read()
                return base64.b64encode(data).decode('utf-8')
    except Exception as e:
        print(f"Failed to download image: {e}")
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


# --- Autonomous Response ---

class AutonomousManager:
    """Manages autonomous (unprompted) responses."""
    
    def __init__(self):
        self.enabled_channels: Dict[int, float] = {}
        self.channel_cooldowns: Dict[int, timedelta] = {}
        self.last_autonomous: Dict[int, datetime] = {}
        self.default_cooldown = timedelta(minutes=2)
    
    def set_channel(self, channel_id: int, enabled: bool, chance: float = 0.1, cooldown_mins: int = 2):
        if enabled:
            self.enabled_channels[channel_id] = min(max(chance, 0.0), 1.0)  # 0-100%
            self.channel_cooldowns[channel_id] = timedelta(minutes=min(max(cooldown_mins, 0), 10))
        elif channel_id in self.enabled_channels:
            del self.enabled_channels[channel_id]
            if channel_id in self.channel_cooldowns:
                del self.channel_cooldowns[channel_id]
    
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
            return f"✅ Enabled ({self.enabled_channels[channel_id]*100:.0f}% chance, {cooldown.seconds//60}min cooldown)"
        return "❌ Disabled"


autonomous_manager = AutonomousManager()
