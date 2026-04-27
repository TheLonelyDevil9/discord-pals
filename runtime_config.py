"""
Discord Pals - Runtime Configuration
Live-adjustable settings via the web dashboard.
"""

import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from config import RUNTIME_CONFIG_FILE, DATA_DIR

# Default values
DEFAULTS = {
    "history_limit": 200,  # Total messages to include (history + immediate)
    "immediate_message_count": 5,  # Last N messages as "current" (placed after chatroom context)
    "active_provider": None,  # None = use first provider
    "bot_interactions_paused": False,  # Global stop for bot-bot conversations
    "global_paused": False,  # KILLSWITCH: Stops ALL bot activity when True
    "use_single_user": False,  # Message format: True = SillyTavern-style single user message, False = multi-role (system/user/assistant)
    "name_trigger_chance": 1.0,  # 0.0-1.0, chance to respond when bot's name/nickname is mentioned without @mention
    "custom_nicknames": "",  # Legacy global nickname field; current UI persists per-bot nicknames
    "raw_generation_logging": False,  # Log raw LLM output to live logs
    "bot_timezones": {},  # Per-bot IANA timezone overrides
    "bot_schedules": {},  # Per-bot availability schedules
    # Bot-on-bot conversation fall-off settings
    "bot_falloff_enabled": True,  # Enable progressive fall-off for bot-bot conversations
    "bot_falloff_base_chance": 0.8,  # Initial response probability (80%)
    "bot_falloff_decay_rate": 0.15,  # Decay per consecutive bot message (15%)
    "bot_falloff_min_chance": 0.05,  # Minimum probability floor (5%)
    "bot_falloff_hard_limit": 10,  # Hard cutoff after N consecutive bot messages
    # Split replies feature
    "split_replies_enabled": False,  # Enable split replies to multiple mentioned users
    "split_replies_max_targets": 5,  # Max users to split replies for (prevents spam)
    "concurrency_limit": 4,  # GLOBAL: Max concurrent AI requests across all bots
    # Mention features
    "allow_bot_mentions": True,  # Allow bots to generate @mentions for users in responses
    "allow_bot_to_bot_mentions": False,  # Allow bots to @mention other bots (can cause loops!)
    "mention_context_limit": 10,  # Max users to show in mention context for AI
    # Context system
    "user_only_context": False,  # When True, only human user messages are sent to the AI (discards all bot/assistant messages)
    "user_only_context_count": 20,  # Last N user messages to include when user_only_context is True
    # DM follow-up settings
    "dm_followup_enabled": False,  # Enable autonomous DM follow-ups after silence
    "dm_followup_timeout_minutes": 120,  # Minutes of silence before sending a follow-up
    "dm_followup_max_count": 1,  # Max follow-up messages before stopping
    "dm_followup_cooldown_hours": 24,  # Hours between follow-up attempts for same user
}
LEGACY_KEY_ALIASES = {
    "context_message_count": "user_only_context_count",
}

# Config cache to avoid repeated file reads
_config_cache: dict = None
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL = 30.0  # Seconds before cache expires (increased from 2.0 for performance)
_BOT_FALLOFF_KEYS = (
    "bot_falloff_enabled",
    "bot_falloff_base_chance",
    "bot_falloff_decay_rate",
    "bot_falloff_min_chance",
    "bot_falloff_hard_limit",
)
_DAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def ensure_data_dir():
    """Create data directory if it doesn't exist."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


def _normalize_key(key: str) -> str:
    """Map legacy config keys to their current names."""
    return LEGACY_KEY_ALIASES.get(key, key)


def _normalize_config(config: dict | None) -> dict:
    """Backfill renamed keys and merge missing defaults."""
    normalized = dict(config or {})

    for legacy_key, current_key in LEGACY_KEY_ALIASES.items():
        if current_key not in normalized and legacy_key in normalized:
            normalized[current_key] = normalized[legacy_key]
        normalized.pop(legacy_key, None)

    for key, value in DEFAULTS.items():
        if key not in normalized:
            normalized[key] = value

    return normalized


def _load_config_from_disk() -> dict:
    """Load config from disk (internal, no caching)."""
    ensure_data_dir()
    if os.path.exists(RUNTIME_CONFIG_FILE):
        try:
            with open(RUNTIME_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return _normalize_config(json.load(f))
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULTS.copy()


def load_config() -> dict:
    """Load runtime config with caching to avoid repeated disk reads.

    Returns cached reference for reads (no copy overhead).
    """
    global _config_cache, _config_cache_time

    now = time.time()
    if _config_cache is not None and (now - _config_cache_time) < _CONFIG_CACHE_TTL:
        return _config_cache  # Return reference, not copy

    _config_cache = _load_config_from_disk()
    _config_cache_time = now
    return _config_cache


def invalidate_cache():
    """Invalidate the config cache (call after writes)."""
    global _config_cache, _config_cache_time
    _config_cache = None
    _config_cache_time = 0.0


def save_config(config: dict):
    """Save runtime config to file and invalidate cache."""
    ensure_data_dir()
    config = _normalize_config(config)
    with open(RUNTIME_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
    invalidate_cache()


def get(key: str, default=None):
    """Get a config value."""
    key = _normalize_key(key)
    config = load_config()
    return config.get(key, default if default is not None else DEFAULTS.get(key))


def set(key: str, value):
    """Set a config value."""
    key = _normalize_key(key)
    config = load_config().copy()  # Copy only when modifying
    config[key] = value
    save_config(config)


def get_all() -> dict:
    """Get all config values (returns copy for safety)."""
    return load_config().copy()


def get_bot_falloff_config() -> dict:
    """Get only the bot fall-off settings used on the message hot path."""
    config = load_config()
    return {key: config[key] for key in _BOT_FALLOFF_KEYS}


def get_bot_timezone(bot_name: str | None) -> str | None:
    """Get a bot-level timezone override."""
    if not bot_name:
        return None

    config = load_config()
    bot_timezones = config.get("bot_timezones", {})
    if not isinstance(bot_timezones, dict):
        return None

    timezone_name = bot_timezones.get(bot_name)
    return timezone_name if isinstance(timezone_name, str) and timezone_name.strip() else None


def set_bot_timezone(bot_name: str, timezone_name: str | None):
    """Set or clear a bot-level timezone override."""
    if not bot_name:
        return

    config = load_config().copy()
    bot_timezones = config.get("bot_timezones", {})
    if not isinstance(bot_timezones, dict):
        bot_timezones = {}
    else:
        bot_timezones = dict(bot_timezones)

    if timezone_name:
        bot_timezones[bot_name] = timezone_name
    else:
        bot_timezones.pop(bot_name, None)

    config["bot_timezones"] = bot_timezones
    save_config(config)


def get_bot_schedule(bot_name: str | None) -> dict:
    """Get a bot availability schedule."""
    if not bot_name:
        return {}
    schedules = load_config().get("bot_schedules", {})
    if not isinstance(schedules, dict):
        return {}
    schedule = schedules.get(bot_name, {})
    return schedule if isinstance(schedule, dict) else {}


def set_bot_schedule(bot_name: str, schedule: dict):
    """Set or clear a bot availability schedule."""
    if not bot_name:
        return
    config = load_config().copy()
    schedules = config.get("bot_schedules", {})
    schedules = dict(schedules) if isinstance(schedules, dict) else {}
    if schedule and schedule.get("enabled") and schedule.get("unavailable"):
        schedules[bot_name] = schedule
    else:
        schedules.pop(bot_name, None)
    config["bot_schedules"] = schedules
    save_config(config)


def is_bot_available(bot_name: str | None, now: datetime | None = None) -> bool:
    """Return False when the bot is inside a configured unavailable window."""
    schedule = get_bot_schedule(bot_name)
    if not schedule.get("enabled"):
        return True

    timezone_name = schedule.get("timezone") or get_bot_timezone(bot_name)
    try:
        tzinfo = ZoneInfo(timezone_name) if timezone_name else datetime.now().astimezone().tzinfo
    except Exception:
        tzinfo = datetime.now().astimezone().tzinfo
    if now is None:
        local_now = datetime.now(tzinfo)
    elif now.tzinfo is None:
        local_now = now.replace(tzinfo=tzinfo)
    else:
        local_now = now.astimezone(tzinfo)
    current_minutes = local_now.hour * 60 + local_now.minute
    current_day = local_now.strftime("%a").lower()[:3]
    current_day_index = _DAY_ORDER.index(current_day) if current_day in _DAY_ORDER else 0
    previous_day = _DAY_ORDER[(current_day_index - 1) % len(_DAY_ORDER)]

    for window in schedule.get("unavailable", []):
        if not isinstance(window, dict):
            continue
        days = window.get("days") or ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        if isinstance(days, str):
            days = [part.strip().lower()[:3] for part in days.split(",") if part.strip()]
        try:
            start_hour, start_min = [int(part) for part in str(window.get("start", "")).split(":", 1)]
            end_hour, end_min = [int(part) for part in str(window.get("end", "")).split(":", 1)]
        except Exception:
            continue
        start_minutes = start_hour * 60 + start_min
        end_minutes = end_hour * 60 + end_min
        if start_minutes == end_minutes:
            if current_day in days:
                return False
            continue
        if start_minutes < end_minutes:
            if current_day in days and start_minutes <= current_minutes < end_minutes:
                return False
            continue
        if (
            (current_day in days and current_minutes >= start_minutes)
            or (previous_day in days and current_minutes < end_minutes)
        ):
            return False
    return True


# Last context storage for visualization
_last_context = {}
_last_context_revision = 0

# Last activity tracking
_last_activity = {}


def store_last_context(bot_name: str, system_prompt: str, messages: list,
                       token_estimate: int = 0):
    """Store the last context sent to LLM for visualization."""
    global _last_context_revision
    _last_context[bot_name] = {
        "system_prompt": system_prompt,
        "messages": messages,
        "token_estimate": token_estimate,
        "message_count": len(messages)
    }
    _last_context_revision += 1


def get_last_context(bot_name: str = None) -> dict:
    """Get the last context sent to LLM."""
    if bot_name:
        return _last_context.get(bot_name, {})
    return _last_context


def get_last_context_revision() -> int:
    """Get a revision counter for dashboard context polling."""
    return _last_context_revision


def update_last_activity(bot_name: str):
    """Update last activity timestamp for a bot."""
    _last_activity[bot_name] = time.time()


def get_last_activity(bot_name: str = None) -> dict:
    """Get last activity timestamp(s)."""
    if bot_name:
        return _last_activity.get(bot_name)
    return _last_activity
