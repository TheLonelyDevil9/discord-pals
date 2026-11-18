"""
Discord Pals - Discord Utilities
Helper functions for Discord interactions.
"""

import discord
import re
import hashlib
import json
import os
import aiohttp
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
from config import MAX_HISTORY_MESSAGES, MAX_EMOJIS_IN_PROMPT, DATA_DIR
import attribution
import logger as log
from scopes import dm_history_id

# Re-export from response_sanitizer for backwards compatibility
# These are used by other modules that import from discord_utils
from response_sanitizer import (  # noqa: F401
    remove_thinking_tags, clean_bot_name_prefix, clean_em_dashes, sanitize_response,
    strip_discord_ooc_comments, visible_user_mention_ids, RE_NAME_PREFIX
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


def dm_history_key(bot_name: str | None, user_id: int | str) -> str:
    """Return a stable per-bot/per-user key for DM conversation history."""
    return dm_history_id(bot_name, user_id)

# Recent message hashes for fast duplicate detection (channel_id -> OrderedDict of hashes)
# Using OrderedDict instead of set to maintain insertion order for proper FIFO eviction
_recent_message_hashes: Dict[int, OrderedDict] = {}
_RECENT_HASH_LIMIT = 50  # Number of recent hashes to track per channel

# Multi-part response tracking (message_id -> full_content)
multipart_responses: Dict[int, Dict[int, str]] = {}

# History persistence file
HISTORY_CACHE_FILE = os.path.join(DATA_DIR, "history_cache.json")
HISTORY_CHANNELS_DIR = os.path.join(DATA_DIR, "history_channels")

# Channel name mapping (channel_id -> name) for readable storage
channel_names: Dict[int, str] = {}

# Conversation history limits
_MAX_CHANNELS_IN_HISTORY = 500  # Max channels to keep in memory
_STALE_CHANNEL_THRESHOLD = 86400  # 24 hours - channels with no activity are candidates for cleanup
_channel_last_activity: Dict[int, float] = {}  # Track last activity per channel
_dirty_history_channels: set[int] = set()
_legacy_history_files: Dict[object, set[str]] = {}

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

_TIME_GAP_THRESHOLD_SECONDS = 30 * 60
_TIME_PASSAGE_CONTEXT_THRESHOLD_SECONDS = 2 * 60 * 60


def _local_timezone():
    """Get the current local timezone information."""
    return datetime.now().astimezone().tzinfo


def _normalize_history_timestamp(timestamp=None) -> str:
    """Convert supported timestamp inputs into an ISO string while preserving the source timezone."""
    local_tz = _local_timezone()

    if isinstance(timestamp, datetime):
        dt = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=local_tz)
        return dt.isoformat(timespec="seconds")

    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat(timespec="seconds")

    if isinstance(timestamp, str) and timestamp:
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=local_tz)
            return dt.isoformat(timespec="seconds")

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_history_timestamp(value) -> Optional[datetime]:
    """Parse stored history timestamps into aware datetimes."""
    if not value:
        return None

    if isinstance(value, datetime):
        return value.astimezone() if value.tzinfo else value.replace(tzinfo=_local_timezone())

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone()
        except (TypeError, ValueError, OSError):
            return None

    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_local_timezone())
        return dt.astimezone()

    return None


def _format_time_gap(delta_seconds: float) -> str:
    """Render a readable time-gap label for long pauses in conversation."""
    seconds = max(int(delta_seconds), 0)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = max(1, remainder // 60)

    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours and len(parts) < 2:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if not parts and minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    elif minutes and len(parts) < 2 and (not days or not hours):
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

    return ", ".join(parts) + " later"


def _get_time_gap_prefix(previous_msg: Optional[dict], current_msg: Optional[dict]) -> str:
    """Return a formatted time-gap marker to prepend before a message when needed."""
    previous_dt = _parse_history_timestamp(previous_msg.get("timestamp") if previous_msg else None)
    current_dt = _parse_history_timestamp(current_msg.get("timestamp") if current_msg else None)
    if not previous_dt or not current_dt:
        return ""

    gap_seconds = (current_dt - previous_dt).total_seconds()
    if gap_seconds < _TIME_GAP_THRESHOLD_SECONDS:
        return ""

    return f"[Time gap: {_format_time_gap(gap_seconds)}]\n"


def _compact_history_content(value, limit: int = 180) -> str:
    """Squash a stored message to a short single-line prompt excerpt."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "..."


def build_time_passage_signal(
    history: list[dict],
    *,
    now: datetime | None = None,
    min_gap_seconds: float = _TIME_PASSAGE_CONTEXT_THRESHOLD_SECONDS,
    max_scan: int = 12,
) -> Optional[dict]:
    """Return lightweight elapsed-time context for the most recent large history gap."""
    if not history or len(history) < 2:
        return None

    recent = history[-max(max_scan + 1, 2):]
    best_gap = None
    for idx in range(1, len(recent)):
        before = recent[idx - 1]
        after = recent[idx]
        before_dt = _parse_history_timestamp(before.get("timestamp"))
        after_dt = _parse_history_timestamp(after.get("timestamp"))
        if not before_dt or not after_dt:
            continue

        gap_seconds = (after_dt - before_dt).total_seconds()
        if gap_seconds >= min_gap_seconds:
            best_gap = (gap_seconds, before, after)

    if not best_gap:
        return None

    gap_seconds, before, after = best_gap
    return {
        "gap_seconds": gap_seconds,
        "gap_label": _format_time_gap(gap_seconds),
        "before_author": before.get("author") or ("Assistant" if before.get("role") == "assistant" else "User"),
        "before_content": _compact_history_content(before.get("content", "")),
        "before_timestamp": before.get("timestamp"),
        "after_author": after.get("author") or ("Assistant" if after.get("role") == "assistant" else "User"),
        "after_content": _compact_history_content(after.get("content", "")),
        "after_timestamp": after.get("timestamp"),
    }


def build_idle_time_passage_signal(
    history: list[dict],
    idle_seconds: float,
    *,
    min_gap_seconds: float = _TIME_PASSAGE_CONTEXT_THRESHOLD_SECONDS,
) -> Optional[dict]:
    """Return elapsed-time context for follow-ups where no new user message arrived."""
    if idle_seconds < min_gap_seconds or not history:
        return None

    before = history[-1]
    return {
        "gap_seconds": idle_seconds,
        "gap_label": _format_time_gap(idle_seconds),
        "before_author": before.get("author") or ("Assistant" if before.get("role") == "assistant" else "User"),
        "before_content": _compact_history_content(before.get("content", "")),
        "before_timestamp": before.get("timestamp"),
        "after_author": "No new reply",
        "after_content": "The conversation has been quiet since then.",
        "after_timestamp": (now := datetime.now().astimezone()).isoformat(timespec="seconds"),
    }


_HISTORY_FILE_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def _history_file_name(history_id) -> str:
    """Return the file name that stores one conversation history.

    Channel histories keep their numeric name. Other keys, such as DM keys,
    contain colons, which Windows rejects in file names, so they keep only
    letters, digits, underscores and hyphens. A digest of the exact key
    follows, so two keys that differ only in letter case do not share one
    file on a case-insensitive file system. The key itself is stored inside
    the file because the name cannot be turned back into it.
    """
    if isinstance(history_id, int):
        return f"{history_id}.json"
    key = str(history_id)
    readable = _HISTORY_FILE_NAME_UNSAFE.sub("-", key).strip("-")[:96] or "history"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{readable}.{digest}.json"


def _history_channel_path(channel_id: int) -> str:
    """Get the per-channel history cache path."""
    return os.path.join(HISTORY_CHANNELS_DIR, _history_file_name(channel_id))


def _stored_history_id(value):
    """Return the history key stored in a file, or None when it is unusable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) or (isinstance(value, str) and value):
        return value
    return None


def _legacy_history_id(stem: str):
    """Return the key that a file written without a stored key is named after.

    Channel files are named after the channel ID. DM files were named after
    their raw key, which only Linux and macOS could create. A name such as
    0123 is not the name of channel 123, so it maps to no key.
    """
    try:
        channel_id = int(stem)
    except ValueError:
        channel_id = None
    if channel_id is not None and str(channel_id) == stem:
        return channel_id
    if stem.startswith("dm:"):
        return stem
    return None


def _legacy_history_file_name(history_id):
    """Return the name that versions without a stored key gave this history's file, or None."""
    if isinstance(history_id, int) or str(history_id).startswith("dm:"):
        return f"{history_id}.json"
    return None


def _same_history_file(path, other) -> bool:
    """Return whether two paths name one file.

    Names that differ only in letter case are one file on Windows and on a
    default macOS volume, which os.path.samefile detects.
    """
    if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(other)):
        return True
    try:
        return os.path.samefile(path, other)
    except OSError:
        return False


def _history_last_activity(data: dict) -> float:
    try:
        return float(data.get("last_activity"))
    except (TypeError, ValueError):
        return float("-inf")


def _set_history_file_aside(path: Path) -> None:
    """Rename an older-named file that holds newer messages to a name the loader ignores."""
    target = path.with_name(f"{path.name}.superseded")
    suffix = 1
    while target.exists():
        target = path.with_name(f"{path.name}.superseded.{suffix}")
        suffix += 1
    try:
        os.rename(path, target)
    except OSError as e:
        log.warn(f"History file {path.name} holds newer messages than the file loaded and could not be renamed: {e}")
        return
    log.warn(f"History file {path.name} holds newer messages than the file loaded; it is kept as {target.name} and not loaded")


def _remove_legacy_history_files(history_id):
    """Delete the older-named files recorded for one history, never its current file."""
    current_path = _history_channel_path(history_id)
    for filepath in _legacy_history_files.pop(history_id, ()):
        if _same_history_file(filepath, current_path):
            continue
        lock = _get_file_lock(filepath)
        with lock:
            try:
                os.remove(filepath)
            except FileNotFoundError:
                pass
            except OSError as e:
                log.warn(f"Failed to delete old history file {os.path.basename(filepath)}: {e}")


def _mark_history_dirty(channel_id: int):
    """Mark a channel history entry as needing persistence."""
    with _history_save_lock:
        _dirty_history_channels.add(channel_id)


def _history_message_hash(role: str, content: str, author_name: str = None, message_id: int = None) -> int:
    """Build the dedupe hash used for recent-message tracking."""
    if message_id:
        return hash((role, content, author_name or '', message_id))
    return hash((role, content, author_name or ''))


def _rebuild_recent_message_hashes(channel_id: int):
    """Rebuild recent message hashes for a loaded channel history."""
    hashes = OrderedDict()
    for msg in conversation_history.get(channel_id, [])[-_RECENT_HASH_LIMIT:]:
        msg_hash = _history_message_hash(
            msg.get("role", "user"),
            msg.get("content", ""),
            msg.get("author"),
            msg.get("message_id"),
        )
        hashes[msg_hash] = True
    if hashes:
        _recent_message_hashes[channel_id] = hashes
    else:
        _recent_message_hashes.pop(channel_id, None)


def _serialize_channel_history(channel_id: int) -> dict:
    """Serialize one channel history entry for per-channel persistence."""
    return {
        "history_id": channel_id,
        "name": channel_names.get(channel_id, str(channel_id)),
        "messages": conversation_history.get(channel_id, []),
        "last_activity": _channel_last_activity.get(channel_id, time.time()),
    }


def _delete_channel_history_file(channel_id: int):
    """Delete a persisted per-channel history file if it exists."""
    filepath = _history_channel_path(channel_id)
    lock = _get_file_lock(filepath)
    with lock:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError as e:
                log.warn(f"Failed to delete history cache for channel {channel_id}: {e}")
    _remove_legacy_history_files(channel_id)


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

        dirty_channels = set(_dirty_history_channels)
        _dirty_history_channels.clear()
        _history_save_pending = False
        _history_last_save = now

    if not dirty_channels:
        return

    os.makedirs(HISTORY_CHANNELS_DIR, exist_ok=True)
    failed_channels = set()
    for channel_id in dirty_channels:
        if channel_id in conversation_history:
            if safe_json_save(_history_channel_path(channel_id), _serialize_channel_history(channel_id)):
                _remove_legacy_history_files(channel_id)
            else:
                failed_channels.add(channel_id)
        else:
            _delete_channel_history_file(channel_id)

    if failed_channels:
        with _history_save_lock:
            _dirty_history_channels.update(failed_channels)
            _history_save_pending = True
        log.warn(f"Failed to save history for {len(failed_channels)} channel(s)")


def flush_pending_history():
    """Force save any pending history changes. Call on shutdown."""
    global _history_save_pending
    with _history_save_lock:
        if _history_save_pending:
            _history_save_pending = False
    save_history(force=True)


def load_history():
    """Load conversation history from disk on startup."""
    global conversation_history, channel_names, _channel_last_activity, _history_save_pending

    conversation_history = {}
    channel_names = {}
    _channel_last_activity = {}
    _recent_message_hashes.clear()
    _legacy_history_files.clear()

    with _history_save_lock:
        _dirty_history_channels.clear()
        _history_save_pending = False

    history_dir = Path(HISTORY_CHANNELS_DIR)
    history_files = sorted(history_dir.glob("*.json")) if history_dir.exists() else []
    if history_files:
        # A file is read only under its key's current name, or under the name
        # that versions without a stored key gave it. Any other file, such as
        # a hand copy or a name that disagrees with the key inside, could
        # hide or replace the real history, so it stays as it is. When both
        # names exist, the current file is loaded. The older-named file is
        # deleted when it is no newer, as after an interrupted rename. When
        # it is newer, as after a rollback to a version that writes that
        # name, it is renamed aside and kept, but not loaded.
        found = {}
        unreadable = object()
        for path in history_files:
            # A file that fails to parse is skipped, so the rewrite below
            # cannot replace it with an empty history.
            try:
                data = safe_json_load(str(path), default=unreadable)
            except Exception as e:
                log.warn(f"Skipped history file {path.name}: {e}")
                continue
            if data is unreadable:
                continue
            if not isinstance(data, dict) or not isinstance(data.get("messages", []), list):
                log.warn(f"Skipped history file {path.name}: it holds no message list")
                continue
            stored_id = _stored_history_id(data.get("history_id"))
            history_id = stored_id if stored_id is not None else _legacy_history_id(path.stem)
            if stored_id is not None and _same_history_file(path, _history_channel_path(history_id)):
                found.setdefault(history_id, {})["current"] = (path, data)
            elif history_id is not None and path.name == _legacy_history_file_name(history_id):
                found.setdefault(history_id, {})["older"] = (path, data)
            else:
                log.warn(f"Skipped history file {path.name}: its name does not match the conversation it holds")

        rewrite = []
        for history_id, files in found.items():
            path, data = files.get("current") or files["older"]
            try:
                conversation_history[history_id] = data.get("messages", [])
                name = data.get("name")
                if isinstance(name, str) and name:
                    channel_names[history_id] = name
                try:
                    _channel_last_activity[history_id] = float(data.get("last_activity"))
                except (TypeError, ValueError):
                    _channel_last_activity[history_id] = time.time()
                _rebuild_recent_message_hashes(history_id)
            except Exception as e:
                conversation_history.pop(history_id, None)
                channel_names.pop(history_id, None)
                _channel_last_activity.pop(history_id, None)
                _recent_message_hashes.pop(history_id, None)
                log.warn(f"Skipped history file {path.name}: {e}")
                continue

            if "older" in files:
                older_path, older_data = files["older"]
                if "current" in files and _history_last_activity(older_data) > _history_last_activity(data):
                    _set_history_file_aside(older_path)
                    continue
                if not _same_history_file(older_path, _history_channel_path(history_id)):
                    _legacy_history_files[history_id] = {str(older_path)}
                rewrite.append(history_id)

        log.info(f"Loaded history for {len(conversation_history)} channels")
        if rewrite:
            log.info(f"Rewriting {len(rewrite)} history file(s) in the current format")
            with _history_save_lock:
                _dirty_history_channels.update(rewrite)
            save_history(force=True)
        return

    data = safe_json_load(HISTORY_CACHE_FILE, default={})
    if not data:
        return

    loaded_channels = []
    for k, v in data.items():
        try:
            channel_id = int(k)
        except (TypeError, ValueError):
            continue

        if isinstance(v, dict) and "messages" in v:
            messages = v.get("messages", [])
            name = v.get("name")
        else:
            messages = v
            name = None

        if not isinstance(messages, list):
            continue

        conversation_history[channel_id] = messages
        if isinstance(name, str) and name:
            channel_names[channel_id] = name
        _channel_last_activity[channel_id] = time.time()
        _rebuild_recent_message_hashes(channel_id)
        loaded_channels.append(channel_id)

    if loaded_channels:
        with _history_save_lock:
            _dirty_history_channels.update(loaded_channels)
            _history_save_pending = True

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


def resolve_discord_formatting(
    content: str,
    guild=None,
    mentioned_users: list | None = None,
    mentioned_channels: list | None = None,
    mentioned_roles: list | None = None,
) -> str:
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

    explicit_user_mentions = {}
    for user in mentioned_users or []:
        user_id = getattr(user, "id", None)
        if user_id is None:
            continue
        explicit_user_mentions[int(user_id)] = f"@{get_user_display_name(user)}"

    explicit_channel_mentions = {}
    for channel in mentioned_channels or []:
        channel_id = getattr(channel, "id", None)
        channel_name = getattr(channel, "name", None)
        if channel_id is None or not channel_name:
            continue
        explicit_channel_mentions[int(channel_id)] = f"#{channel_name}"

    explicit_role_mentions = {}
    for role in mentioned_roles or []:
        role_id = getattr(role, "id", None)
        role_name = getattr(role, "name", None)
        if role_id is None or not role_name:
            continue
        explicit_role_mentions[int(role_id)] = f"@{role_name}"

    # User mentions: <@123> or <@!123> → @Username
    def resolve_user_mention(match):
        user_id = int(match.group(1))
        explicit_name = explicit_user_mentions.get(user_id)
        if explicit_name:
            return explicit_name

        if guild:
            member = guild.get_member(user_id)
            if member:
                return f"@{member.display_name}"

        return "@user"

    content = RE_USER_MENTION.sub(resolve_user_mention, content)

    # Channel mentions: <#123> → #channel-name
    def resolve_channel_mention(match):
        channel_id = int(match.group(1))
        explicit_name = explicit_channel_mentions.get(channel_id)
        if explicit_name:
            return explicit_name

        if guild:
            channel = guild.get_channel(channel_id)
            if channel:
                return f"#{channel.name}"

        return "#channel"

    content = RE_CHANNEL_MENTION.sub(resolve_channel_mention, content)

    # Role mentions: <@&123> → @RoleName
    def resolve_role_mention(match):
        role_id = int(match.group(1))
        explicit_name = explicit_role_mentions.get(role_id)
        if explicit_name:
            return explicit_name

        if guild:
            role = guild.get_role(role_id)
            if role:
                return f"@{role.name}"

        return "@role"

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
                   is_bot: bool = False, timestamp=None, mentioned_users=None,
                   mentioned_channels=None, mentioned_roles=None, req_id: str | None = None):
    """Add a message to conversation history.

    Args:
        channel_id: Discord channel ID
        role: Message role ('user' or 'assistant')
        content: Message content
        author_name: Display name of the author
        user_id: Discord user ID (for mention features)
        guild: Discord guild object for resolving mentions (optional)
        message_id: Discord message ID (for precise duplicate detection)
        is_bot: Whether the author is a bot (for context filtering)
        timestamp: Optional message timestamp (Discord message time or datetime/ISO string)
    """
    if role == "user" and not is_bot:
        content = strip_discord_ooc_comments(content)
        if not str(content or "").strip():
            return

    if channel_id not in conversation_history:
        conversation_history[channel_id] = []

    # Track activity for this channel
    _channel_last_activity[channel_id] = time.time()

    # Sanitize Discord syntax before storage (fix before sending to LLM)
    if guild:
        content = resolve_discord_formatting(
            content,
            guild,
            mentioned_users=mentioned_users,
            mentioned_channels=mentioned_channels,
            mentioned_roles=mentioned_roles,
        )
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
    if is_bot:
        msg["is_bot"] = True
    msg["timestamp"] = _normalize_history_timestamp(timestamp)

    # Fast hash-based duplicate detection (O(1) instead of O(n))
    # Include message_id when available so different Discord messages with same content aren't deduped
    msg_hash = _history_message_hash(role, content, author_name, message_id)
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
    log.diagnostic(
        "History entry added",
        component="history",
        event="history_add",
        req_id=req_id,
        channel_id=channel_id,
        user_id=user_id,
        message_id=message_id,
        role=role,
        author=author_name,
        is_bot=is_bot,
        content_len=len(content or ""),
    )

    if len(conversation_history[channel_id]) > MAX_HISTORY_MESSAGES:
        conversation_history[channel_id] = conversation_history[channel_id][-MAX_HISTORY_MESSAGES:]

    # Trigger debounced save to prevent data loss on crash
    _mark_history_dirty(channel_id)
    save_history()

    # Periodic cleanup of stale channels (every 100 messages added)
    if len(conversation_history) > _MAX_CHANNELS_IN_HISTORY:
        cleanup_stale_conversation_history()


def cleanup_stale_conversation_history():
    """Remove stale channels from conversation_history to prevent memory leaks.

    Only channel histories count toward the limit and only they are removed.
    Removal deletes the history file, and every DM file loads at start, so
    counting DMs would delete idle conversations because of DM volume alone.
    """
    now = time.time()
    channel_ids = [history_id for history_id in conversation_history if isinstance(history_id, int)]
    if len(channel_ids) <= _MAX_CHANNELS_IN_HISTORY:
        return
    channels_to_remove = []

    # Find channels with no recent activity
    for channel_id in channel_ids:
        last_activity = _channel_last_activity.get(channel_id, 0)
        if now - last_activity > _STALE_CHANNEL_THRESHOLD:
            channels_to_remove.append(channel_id)

    # If still over limit, remove oldest channels
    if len(channel_ids) - len(channels_to_remove) > _MAX_CHANNELS_IN_HISTORY:
        # Sort by last activity, oldest first
        sorted_channels = sorted(
            channel_ids,
            key=lambda cid: _channel_last_activity.get(cid, 0)
        )
        # Remove oldest until under limit
        excess = len(channel_ids) - _MAX_CHANNELS_IN_HISTORY
        channels_to_remove.extend(sorted_channels[:excess])

    # Remove duplicates and clean up
    channels_to_remove = list(set(channels_to_remove))
    for ch in channels_to_remove:
        conversation_history.pop(ch, None)
        _recent_message_hashes.pop(ch, None)
        _channel_last_activity.pop(ch, None)
        channel_names.pop(ch, None)
        with _history_save_lock:
            _dirty_history_channels.discard(ch)
        _delete_channel_history_file(ch)

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
                _rebuild_recent_message_hashes(channel_id)
                _mark_history_dirty(channel_id)
                save_history()
                return


def remove_deleted_bot_message(channel_id, message_id: int, *, author_name: str | None, created_at) -> bool:
    """Remove the history entry of a bot message that was deleted on Discord.

    An entry with a message ID matches only that ID. An assistant entry
    without one matches when it is under this bot's character name and was
    recorded in the second the deleted message was created. Nothing else is
    removed, because the bot also deletes its own notices, which were never
    recorded.
    """
    if remove_message_from_history(channel_id, message_id):
        return True

    created = _parse_history_timestamp(created_at)
    if not author_name or created is None or channel_id not in conversation_history:
        return False
    created = created.replace(microsecond=0)

    history = conversation_history[channel_id]
    for i in range(len(history) - 1, -1, -1):
        entry = history[i]
        if entry.get("role") != "assistant" or entry.get("message_id"):
            continue
        if str(entry.get("author") or "").lower() != author_name.lower():
            continue
        if _parse_history_timestamp(entry.get("timestamp")) != created:
            continue
        del history[i]
        _rebuild_recent_message_hashes(channel_id)
        _mark_history_dirty(channel_id)
        save_history()
        return True
    return False


def remove_message_from_history(channel_id: int, message_id: int) -> bool:
    """Remove a specific stored message by Discord message ID."""
    if channel_id not in conversation_history or message_id is None:
        return False

    history = conversation_history[channel_id]
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("message_id") == message_id:
            del history[i]
            _rebuild_recent_message_hashes(channel_id)
            _mark_history_dirty(channel_id)
            save_history()
            return True

    return False


# Channels where history was explicitly cleared (suppresses auto-recall)
_cleared_channels: set = set()


def clear_history(channel_id: int):
    """Clear conversation history for a channel."""
    if channel_id in conversation_history:
        del conversation_history[channel_id]
    _recent_message_hashes.pop(channel_id, None)
    _channel_last_activity.pop(channel_id, None)
    channel_names.pop(channel_id, None)
    with _history_save_lock:
        _dirty_history_channels.discard(channel_id)
    _cleared_channels.add(channel_id)
    _delete_channel_history_file(channel_id)


def was_recently_cleared(channel_id: int) -> bool:
    """Check if a channel's history was explicitly cleared (suppresses auto-recall)."""
    return channel_id in _cleared_channels


def acknowledge_cleared(channel_id: int):
    """Remove the cleared flag after the first successful message exchange."""
    _cleared_channels.discard(channel_id)


def format_history_for_ai(channel_id: int, limit: int = 50) -> List[dict]:
    """Format history for AI consumption with optional limit."""
    history = get_history(channel_id)[-limit:]  # Apply limit
    canonical_authors = attribution.canonical_author_map(history)
    formatted = []

    for idx, msg in enumerate(history):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        gap_prefix = _get_time_gap_prefix(history[idx - 1] if idx > 0 else None, msg)

        attributed = False
        if role == "user":
            author = attribution.resolve_author(msg, canonical_authors)
            if author:
                content = attribution.render_attributed_content(author, content)
                attributed = True

        if gap_prefix:
            content = f"{gap_prefix}{content}"

        formatted_msg = {"role": role, "content": content}
        if attributed:
            formatted_msg["attributed"] = True
        formatted.append(formatted_msg)

    return formatted


def history_through_message(history: List[dict], message_id: int = None) -> List[dict]:
    """Drop history entries newer than one Discord message.

    Channel history is shared across every bot in the process and keeps growing
    while a queued request waits its turn. Cutting the window at the message a
    request was queued for keeps the model's newest turn identical to the
    message the reply will be attached to.
    """
    if not message_id:
        return history

    for index in range(len(history) - 1, -1, -1):
        if history[index].get("message_id") == message_id:
            return history[:index + 1]
    return history


def format_history_split(channel_id: int, total_limit: int = 200, immediate_count: int = 5,
                         current_bot_name: str = None,
                         up_to_message_id: int = None) -> Tuple[List[dict], List[dict]]:
    """
    Split history into two parts for the context structure:
    - history: older messages (background context)
    - immediate: recent messages (placed after chatroom context for focused response)

    If current_bot_name is provided, other bots' messages will be tagged with
    their name (like user messages) to prevent personality bleed.

    If up_to_message_id is provided and present in history, messages newer than
    it are excluded so the newest turn matches the message being replied to.

    Returns: (history_messages, immediate_messages)
    """
    all_history = history_through_message(get_history(channel_id), up_to_message_id)

    # Include all messages with bot personality bleed prevention.
    all_history = all_history[-total_limit:]

    # Format all messages through the shared attribution renderer so one
    # user_id keeps one canonical name across the whole rendered window.
    canonical_authors = attribution.canonical_author_map(all_history)
    formatted = []
    for msg in all_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        author = msg.get("author")
        attributed = False

        if role == "user":
            # User messages get Author: prefix (no brackets)
            resolved = attribution.resolve_author(msg, canonical_authors)
            if resolved:
                content = attribution.render_attributed_content(resolved, content)
                attributed = True
        elif role == "assistant":
            # Bot messages: check if this is from the CURRENT bot or a DIFFERENT bot
            if author and current_bot_name and author.lower() != current_bot_name.lower():
                # Different bot - treat as "user" role with name prefix to prevent personality bleed
                role = "user"
                content = attribution.render_attributed_content(author, content)
                attributed = True
            # If same bot or no author field, keep as assistant (no prefix)

        previous_msg = all_history[len(formatted) - 1] if formatted else None
        gap_prefix = _get_time_gap_prefix(previous_msg, msg)
        if gap_prefix:
            content = f"{gap_prefix}{content}"

        formatted_msg = {"role": role, "content": content}
        if attributed:
            formatted_msg["attributed"] = True
        if role == "assistant":
            assistant_author = author or current_bot_name
            if assistant_author:
                formatted_msg["author"] = assistant_author

        formatted.append(formatted_msg)

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
    """Get names of other bot characters from history and the bot registry."""
    other_bots = set()

    # From history (bots that have spoken in this channel)
    history = get_history(channel_id)
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

    # AFTER conversion, clean up malformed emoji-like tags that LLMs sometimes generate
    # Remove malformed emoji prefixes (e.g., "<:test:123" without closing ">")
    result = RE_MALFORMED_EMOJI_PREFIX.sub('', result)

    # Remove incomplete tags (e.g., "<:name:123456789012345678" without ">")
    result = RE_INCOMPLETE_TAG.sub('', result)

    # Remove incomplete emoji tags at end of string (e.g., "<:emoji" or "<a:")
    result = RE_BROKEN_EMOJI_END.sub('', result)

    # Remove orphaned emoji IDs without proper format (e.g., "12345678901234567890>")
    result = RE_ORPHAN_SNOWFLAKE.sub('', result)

    # Remove empty angle bracket pairs
    result = RE_EMPTY_ANGLE.sub('', result)

    # Clean up extra whitespace
    result = re.sub(r'\s{2,}', ' ', result)
    result = re.sub(r'\s+([,!?;:.])', r'\1', result)

    # Disabled: RE_MALFORMED_EMOJI is too aggressive — it can match legitimate
    # text between < and > (up to 50 chars) and truncate messages mid-sentence.
    # The targeted patterns above handle specific malformed cases without
    # the risk of truncating legitimate content.
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


# --- Message Splitting ---

def split_message(content: str, max_length: int = 2000) -> List[str]:
    """Split a long message into Discord-compatible chunks."""
    if max_length <= 0:
        return [content] if content else []
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
                            current_chunk = ""
                        sentence_chunks = _split_long_message_sentence(sentence, max_length)
                        if len(sentence_chunks) > 1:
                            chunks.extend(sentence_chunks[:-1])
                        current_chunk = sentence_chunks[-1] if sentence_chunks else ""
            else:
                current_chunk = para

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _split_long_message_sentence(sentence: str, max_length: int) -> List[str]:
    """Split one oversized sentence without dropping text."""
    if len(sentence) <= max_length:
        return [sentence]

    chunks: List[str] = []
    remaining = sentence
    while len(remaining) > max_length:
        split_at = _find_message_chunk_boundary(remaining, max_length)
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if remaining:
        chunks.append(remaining)
    return chunks


def _find_message_chunk_boundary(text: str, max_length: int) -> int:
    """Find a readable split boundary within Discord's hard message limit."""
    window = text[:max_length + 1]
    whitespace_matches = list(re.finditer(r"\s+", window))
    for match in reversed(whitespace_matches):
        if 0 < match.start() <= max_length:
            return match.start()
    return max_length


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
    """Manages autonomous (unprompted) responses and nickname trigger scoping with persistent storage."""

    def __init__(self):
        self.enabled_channels: Dict[int, float] = {}
        self.channel_cooldowns: Dict[int, timedelta] = {}
        self.allow_bot_triggers: Dict[int, bool] = {}  # Per-channel bot trigger control
        self.nickname_trigger_channels: Dict[int, bool] = {}  # Per-channel nickname trigger toggle
        self.last_autonomous: Dict[int, datetime] = {}
        self.default_cooldown = timedelta(minutes=2)
        self._load()
    
    def _load(self):
        """Load settings from disk."""
        data = safe_json_load(AUTONOMOUS_FILE, default={})

        # Load per-channel nickname trigger settings (stored at top level)
        nickname_data = data.pop('_nickname_triggers', {})
        for ch_id, enabled in nickname_data.items():
            self.nickname_trigger_channels[int(ch_id)] = bool(enabled)

        for ch_id, settings in data.items():
            try:
                ch_id = int(ch_id)
            except (ValueError, TypeError):
                continue
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
        # Store nickname trigger settings under a reserved key
        if self.nickname_trigger_channels:
            data['_nickname_triggers'] = {str(k): v for k, v in self.nickname_trigger_channels.items()}
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
    
    def is_nickname_trigger_enabled(self, channel_id: int) -> bool:
        """Check if nickname triggers are enabled for a channel. Default: OFF."""
        return self.nickname_trigger_channels.get(channel_id, False)

    def set_nickname_trigger(self, channel_id: int, enabled: bool):
        """Enable or disable nickname triggers for a channel."""
        if enabled:
            self.nickname_trigger_channels[channel_id] = True
        elif channel_id in self.nickname_trigger_channels:
            del self.nickname_trigger_channels[channel_id]
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


def _normalize_mention_alias(alias: str) -> str:
    """Normalize a full-handle alias for case-insensitive mention matching."""
    if not isinstance(alias, str):
        return ""
    cleaned = " ".join(alias.replace("\u200b", "").strip().lstrip("@").split())
    return cleaned.casefold()


def _collect_member_aliases(member) -> List[str]:
    """Collect full-name aliases for a Discord member/user without generating partial names."""
    aliases = []
    for attr in ("display_name", "global_name", "name"):
        value = getattr(member, attr, None)
        if isinstance(value, str) and value.strip():
            aliases.append(" ".join(value.strip().split()))
    return list(dict.fromkeys(aliases))


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


def get_mentionable_users(
    channel_id: int,
    limit: int | None = 10,
    guild=None,
    explicit_users: list | None = None,
    reply_target=None,
) -> List[dict]:
    """Get prioritized users who can be mentioned.

    Priority order:
    1. Explicitly mentioned users and reply targets from the newest message
    2. Recent human conversation participants from history
    3. Any visible human guild member

    With a limit, guild members are read only until the limit is filled, so the cost
    follows the limit instead of the guild size.
    """
    users_by_id: Dict[int, dict] = {}
    sequence = 0

    def register_candidate(*, user_id, name: str, aliases: List[str], priority: int):
        nonlocal sequence
        try:
            resolved_user_id = int(user_id)
        except (TypeError, ValueError):
            return
        if resolved_user_id <= 0:
            return

        cleaned_aliases = []
        for alias in aliases or []:
            if isinstance(alias, str) and alias.strip():
                collapsed = " ".join(alias.strip().split())
                if collapsed and collapsed not in cleaned_aliases:
                    cleaned_aliases.append(collapsed)
        if not cleaned_aliases:
            if not isinstance(name, str) or not name.strip():
                return
            cleaned_aliases = [" ".join(name.strip().split())]

        primary_name = " ".join((name or cleaned_aliases[0]).strip().split())
        existing = users_by_id.get(resolved_user_id)
        if existing:
            for alias in cleaned_aliases:
                if alias not in existing["aliases"]:
                    existing["aliases"].append(alias)
            if priority < existing["priority"]:
                existing["priority"] = priority
                existing["order"] = sequence
                existing["name"] = primary_name
            sequence += 1
            return

        users_by_id[resolved_user_id] = {
            "name": primary_name,
            "user_id": resolved_user_id,
            "mention_syntax": f"<@{resolved_user_id}>",
            "aliases": cleaned_aliases,
            "priority": priority,
            "order": sequence,
        }
        sequence += 1

    if reply_target and not getattr(reply_target, "bot", False):
        register_candidate(
            user_id=getattr(reply_target, "id", None),
            name=get_user_display_name(reply_target),
            aliases=_collect_member_aliases(reply_target),
            priority=0,
        )

    for user in explicit_users or []:
        if getattr(user, "bot", False):
            continue
        register_candidate(
            user_id=getattr(user, "id", None),
            name=get_user_display_name(user),
            aliases=_collect_member_aliases(user),
            priority=0,
        )

    history = get_history(channel_id)
    for msg in reversed(history[-50:]):
        if msg.get("is_bot", False):
            continue
        user_id = msg.get("user_id")
        author = msg.get("author")
        if user_id and author:
            register_candidate(
                user_id=user_id,
                name=author,
                aliases=[author],
                priority=1,
            )

    if guild:
        # The scan below can stop before it reaches a candidate listed above, so that
        # candidate gets its member aliases, such as the account name, from a lookup.
        get_member = getattr(guild, "get_member", None)
        for listed_id in list(users_by_id) if callable(get_member) else []:
            listed_member = get_member(listed_id)
            if listed_member is not None and not listed_member.bot:
                register_candidate(
                    user_id=listed_id,
                    name=get_user_display_name(listed_member),
                    aliases=_collect_member_aliases(listed_member),
                    priority=2,
                )
        for member in guild.members:
            if limit is not None and len(users_by_id) >= limit:
                break
            if member.bot:
                continue
            register_candidate(
                user_id=member.id,
                name=get_user_display_name(member),
                aliases=_collect_member_aliases(member),
                priority=2,
            )

    users = sorted(users_by_id.values(), key=lambda item: (item["priority"], item["order"]))
    if limit is None:
        return users
    return users[:max(0, int(limit))]


# Bounds the members a reply's @names are looked up among, so that the cost of
# a reply stops growing with the guild.
MENTION_CANDIDATE_LIMIT = 1000
_NAME_IN_MENTION_BRACKETS = re.compile(r'<@!?([^>\d][^>]*)>')
_RAW_USER_MENTION = re.compile(r'<@!?\d+>')
_RAW_CHANNEL_MENTION = re.compile(r'<#\d+>')
_RAW_ROLE_MENTION = re.compile(r'<@&\d+>')
_MENTION_ID = re.compile(r'<@!?(\d+)>')
_WORD_CHARACTER = re.compile(r'\w')


def _mention_lookup(mentionable_users, mentionable_bots) -> dict:
    # Build lookup of full-handle alias -> mention syntax while preserving priority
    # and refusing ambiguous matches at the same priority.
    alias_lookup = {}

    def register_alias(alias: str, mention_syntax: str, user_id, priority: int):
        normalized = _normalize_mention_alias(alias)
        if not normalized or not mention_syntax:
            return

        existing = alias_lookup.get(normalized)
        if existing is None or priority < existing["priority"]:
            alias_lookup[normalized] = {
                "alias": " ".join(alias.strip().split()),
                "mention_syntax": mention_syntax,
                "user_id": str(user_id),
                "priority": priority,
                "ambiguous": False,
            }
            return

        if priority > existing["priority"]:
            return

        if str(user_id) != existing["user_id"]:
            existing["ambiguous"] = True
            alias_lookup[normalized] = existing

    for user in mentionable_users or []:
        aliases = user.get("aliases") or [user.get("name", "")]
        priority = int(user.get("priority", 1))
        for alias in aliases:
            register_alias(alias, user.get("mention_syntax"), user.get("user_id"), priority)

    for bot in mentionable_bots or []:
        if bot.get('character_name'):
            register_alias(bot['character_name'], bot['mention_syntax'], bot.get('user_id'), 99)

    return {
        entry["alias"].casefold(): entry["mention_syntax"]
        for entry in alias_lookup.values()
        if not entry.get("ambiguous")
    }


def _longest_alias_after(content: str, start: int, lookup: dict, longest: int) -> tuple:
    """Return (end, mention) for the longest alias in lookup that starts at start, or (None, None).

    Matching follows the per-alias patterns this lookup replaced: case is ignored, a
    whitespace run in the reply stands for the single space inside an alias, and an
    alias counts only where the reply continues with a non-word character or ends.
    """
    best_end = best_mention = None
    name = []
    position = start
    while position < len(content) and len(name) < longest:
        character = content[position]
        position += 1
        if character.isspace():
            if not name:
                break
            if name[-1] != " ":
                name.append(" ")
            continue
        name.append(character)
        if position == len(content) or not _WORD_CHARACTER.match(content[position]):
            mention = lookup.get("".join(name).casefold())
            if mention:
                best_end, best_mention = position, mention
    return best_end, best_mention


def _resolve_mentions(content: str, mentionable_users, mentionable_bots) -> tuple[str, dict]:
    summary = {"candidates": len(mentionable_users or []), "at_names": 0, "resolved": 0}
    if not content:
        return content, summary
    lookup = _mention_lookup(mentionable_users, mentionable_bots)
    if not lookup:
        return content, summary

    # Normalize AI-generated <@Name> to @Name (keep <@12345> for safety net)
    content = _NAME_IN_MENTION_BRACKETS.sub(r'@\1', content)

    # Track which mention IDs we intentionally insert (so the safety net doesn't strip them)
    inserted_mention_ids = set()
    longest = max(len(alias) for alias in lookup)
    pieces = []
    copied_up_to = 0
    at = content.find("@")
    while at != -1:
        summary["at_names"] += 1
        end, mention = _longest_alias_after(content, at + 1, lookup, longest)
        if mention is None:
            at = content.find("@", at + 1)
            continue
        pieces.append(content[copied_up_to:at])
        pieces.append(mention)
        id_match = _MENTION_ID.search(mention)
        if id_match:
            inserted_mention_ids.add(id_match.group(1))
        summary["resolved"] += 1
        copied_up_to = end
        at = content.find("@", end)
    pieces.append(content[copied_up_to:])
    content = "".join(pieces)

    # Safety net: Strip any raw Discord syntax the AI hallucinated,
    # but preserve mentions we just intentionally inserted
    def strip_if_not_inserted(match):
        # Extract the numeric ID from the match
        id_match = re.search(r'\d+', match.group(0))
        if id_match and id_match.group(0) in inserted_mention_ids:
            return match.group(0)  # Keep - we inserted this
        return ''  # Strip - AI hallucinated this

    content = _RAW_USER_MENTION.sub(strip_if_not_inserted, content)
    content = _RAW_CHANNEL_MENTION.sub('', content)      # Raw channel mentions (always strip)
    content = _RAW_ROLE_MENTION.sub('', content)     # Raw role mentions (always strip)

    return content, summary


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
    return _resolve_mentions(content, mentionable_users, mentionable_bots)[0]


def resolve_reply_mentions(content: str, sources: dict | None,
                           mentionable_bots: list | None = None) -> tuple[str, dict]:
    """Resolve @Name in a reply; return the text and counts for the reply's one log line.

    sources holds the history_id, guild, explicit_users and reply_target of a server
    request, and is None in DMs, whose replies stay unchanged. Candidates are read only
    for a reply with an '@', and at most MENTION_CANDIDATE_LIMIT of them.
    """
    summary = {"candidates": 0, "at_names": 0, "resolved": 0}
    if not content or (sources is None and not mentionable_bots):
        return content, summary
    if "@" not in content:
        # A reply without '@' can only hit the raw channel rule of the safety net.
        return _RAW_CHANNEL_MENTION.sub('', content), summary
    users = []
    if sources is not None:
        users = get_mentionable_users(
            sources["history_id"],
            limit=MENTION_CANDIDATE_LIMIT,
            guild=sources.get("guild"),
            explicit_users=sources.get("explicit_users"),
            reply_target=sources.get("reply_target"),
        )
    return _resolve_mentions(content, users, mentionable_bots)

