"""
Discord Pals - Runtime Configuration
Live-adjustable settings via the web dashboard.
"""

import json
import math
import os
import re
import time
import builtins
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from config import RUNTIME_CONFIG_FILE, DATA_DIR


@dataclass(frozen=True)
class ConfigField:
    """Runtime config boundary rule for one dashboard-editable setting."""

    value_type: type
    default: object
    min_value: float | None = None
    max_value: float | None = None
    choices: tuple | None = None


# Default values
DEFAULTS = {
    "history_limit": 200,  # Total messages to include (history + immediate)
    "immediate_message_count": 5,  # Last N messages as "current" (placed after chatroom context)
    "active_provider": None,  # None = use first provider
    "bot_interactions_paused": False,  # Global stop for bot-bot conversations
    "global_paused": False,  # KILLSWITCH: Stops ALL bot activity when True
    "server_responses_enabled": True,  # Allow normal server-channel replies
    "dm_responses_enabled": True,  # Allow direct-message replies and DM delivery helpers
    "response_channel_whitelist_only": False,  # Only reply in whitelisted server channels
    "response_channel_whitelist": [],  # Server channel IDs allowed when whitelist-only mode is on
    "response_channel_blacklist": [],  # Server channel IDs where all replies are blocked
    "dm_user_blacklist": [],  # User IDs whose DMs are blocked for replies and follow-ups
    "use_single_user": False,  # Message format: True = SillyTavern-style single user message, False = multi-role (system/user/assistant)
    "prose_polisher_enabled": False,  # Run a post-generation provider pass to clean repetitive prose patterns
    "prose_polisher_max_tokens": 8192,  # Max tokens for the post-generation polish pass
    "prose_polisher_preferred_tier": "",  # Optional provider tier for the polish pass
    "name_trigger_chance": 1.0,  # 0.0-1.0, chance to respond when bot's name/nickname is mentioned without @mention
    "custom_nicknames": "",  # Legacy global nickname field; current UI persists per-bot nicknames
    "raw_generation_logging": False,  # Log raw LLM output to live logs
    "diagnostic_logging": False,  # Print high-volume structured diagnostics to terminal
    "file_logging_enabled": True,  # Persist local JSONL logs in bot_data/logs
    "log_file_max_mb": 10,  # Max size of one JSONL log file before rotation
    "update_branch": "",  # Dashboard updater branch override: "" preserves current checkout/upstream behavior
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
    "identity_guard_enabled": True,  # Block generated text that structurally speaks as another bot
    "identity_guard_policy": "regenerate_then_drop",  # Guard response policy
    "bot_reference_context_mode": "neutral",  # Replace referenced bot prose with neutral metadata
    "time_passage_context_enabled": True,  # Add elapsed-time world-state cues after long chat gaps
    # DM follow-up settings
    "dm_followup_enabled": False,  # Enable autonomous DM follow-ups after silence
    "dm_followup_timeout_minutes": 120,  # Minutes of silence before sending a follow-up
    "dm_followup_max_count": 1,  # Max follow-up messages before stopping
    "dm_followup_cooldown_hours": 24,  # Hours between follow-up attempts for same user
    "dm_image_generation_enabled": False,  # Allow DM follow-ups to send generated images
    "dm_image_generation_chance": 0.25,  # Chance that an eligible DM follow-up becomes an image
    "dm_image_generation_caption_chance": 0.85,  # Chance to include a short in-character caption
    "dm_image_generation_preferred_tier": "",  # Optional preferred image provider tier
    "dm_image_generation_prompt": "A weird, low-stakes, incomprehensible AI-generated meme image that looks like something a friend would send without context.",
    "bot_nicknames": {},  # Single-bot nickname fallback, edited through dashboard nickname controls
}
REMOVED_CONFIG_KEYS = {
    "context_message_count",
    "user_only_context",
    "user_only_context_count",
    "strict_human_only_context",
}
LEGACY_KEY_ALIASES = {}
CONFIG_FIELDS = {
    "history_limit": ConfigField(int, DEFAULTS["history_limit"], 10, 1000),
    "immediate_message_count": ConfigField(int, DEFAULTS["immediate_message_count"], 1, 50),
    "active_provider": ConfigField(str, DEFAULTS["active_provider"]),
    "bot_interactions_paused": ConfigField(bool, DEFAULTS["bot_interactions_paused"]),
    "global_paused": ConfigField(bool, DEFAULTS["global_paused"]),
    "server_responses_enabled": ConfigField(bool, DEFAULTS["server_responses_enabled"]),
    "dm_responses_enabled": ConfigField(bool, DEFAULTS["dm_responses_enabled"]),
    "response_channel_whitelist_only": ConfigField(bool, DEFAULTS["response_channel_whitelist_only"]),
    "response_channel_whitelist": ConfigField(list, DEFAULTS["response_channel_whitelist"]),
    "response_channel_blacklist": ConfigField(list, DEFAULTS["response_channel_blacklist"]),
    "dm_user_blacklist": ConfigField(list, DEFAULTS["dm_user_blacklist"]),
    "use_single_user": ConfigField(bool, DEFAULTS["use_single_user"]),
    "prose_polisher_enabled": ConfigField(bool, DEFAULTS["prose_polisher_enabled"]),
    "prose_polisher_max_tokens": ConfigField(int, DEFAULTS["prose_polisher_max_tokens"], 16, 16000),
    "prose_polisher_preferred_tier": ConfigField(str, DEFAULTS["prose_polisher_preferred_tier"]),
    "name_trigger_chance": ConfigField(float, DEFAULTS["name_trigger_chance"], 0.0, 1.0),
    "custom_nicknames": ConfigField(str, DEFAULTS["custom_nicknames"]),
    "raw_generation_logging": ConfigField(bool, DEFAULTS["raw_generation_logging"]),
    "diagnostic_logging": ConfigField(bool, DEFAULTS["diagnostic_logging"]),
    "file_logging_enabled": ConfigField(bool, DEFAULTS["file_logging_enabled"]),
    "log_file_max_mb": ConfigField(int, DEFAULTS["log_file_max_mb"], 1, 100),
    "update_branch": ConfigField(str, DEFAULTS["update_branch"], choices=("", "main", "staging")),
    "bot_timezones": ConfigField(dict, DEFAULTS["bot_timezones"]),
    "bot_schedules": ConfigField(dict, DEFAULTS["bot_schedules"]),
    "bot_falloff_enabled": ConfigField(bool, DEFAULTS["bot_falloff_enabled"]),
    "bot_falloff_base_chance": ConfigField(float, DEFAULTS["bot_falloff_base_chance"], 0.0, 1.0),
    "bot_falloff_decay_rate": ConfigField(float, DEFAULTS["bot_falloff_decay_rate"], 0.0, 1.0),
    "bot_falloff_min_chance": ConfigField(float, DEFAULTS["bot_falloff_min_chance"], 0.0, 1.0),
    "bot_falloff_hard_limit": ConfigField(int, DEFAULTS["bot_falloff_hard_limit"], 1, 100),
    "split_replies_enabled": ConfigField(bool, DEFAULTS["split_replies_enabled"]),
    "split_replies_max_targets": ConfigField(int, DEFAULTS["split_replies_max_targets"], 1, 25),
    "concurrency_limit": ConfigField(int, DEFAULTS["concurrency_limit"], 1, 20),
    "allow_bot_mentions": ConfigField(bool, DEFAULTS["allow_bot_mentions"]),
    "allow_bot_to_bot_mentions": ConfigField(bool, DEFAULTS["allow_bot_to_bot_mentions"]),
    "mention_context_limit": ConfigField(int, DEFAULTS["mention_context_limit"], 1, 100),
    "identity_guard_enabled": ConfigField(bool, DEFAULTS["identity_guard_enabled"]),
    "identity_guard_policy": ConfigField(
        str,
        DEFAULTS["identity_guard_policy"],
        choices=("regenerate_then_drop", "drop"),
    ),
    "bot_reference_context_mode": ConfigField(
        str,
        DEFAULTS["bot_reference_context_mode"],
        choices=("neutral", "legacy"),
    ),
    "time_passage_context_enabled": ConfigField(bool, DEFAULTS["time_passage_context_enabled"]),
    "dm_followup_enabled": ConfigField(bool, DEFAULTS["dm_followup_enabled"]),
    "dm_followup_timeout_minutes": ConfigField(int, DEFAULTS["dm_followup_timeout_minutes"], 1, 10080),
    "dm_followup_max_count": ConfigField(int, DEFAULTS["dm_followup_max_count"], 1, 20),
    "dm_followup_cooldown_hours": ConfigField(int, DEFAULTS["dm_followup_cooldown_hours"], 1, 168),
    "dm_image_generation_enabled": ConfigField(bool, DEFAULTS["dm_image_generation_enabled"]),
    "dm_image_generation_chance": ConfigField(float, DEFAULTS["dm_image_generation_chance"], 0.0, 1.0),
    "dm_image_generation_caption_chance": ConfigField(float, DEFAULTS["dm_image_generation_caption_chance"], 0.0, 1.0),
    "dm_image_generation_preferred_tier": ConfigField(str, DEFAULTS["dm_image_generation_preferred_tier"]),
    "dm_image_generation_prompt": ConfigField(str, DEFAULTS["dm_image_generation_prompt"]),
    "bot_nicknames": ConfigField(dict, DEFAULTS["bot_nicknames"]),
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


def _coerce_bool(value, default: bool) -> bool:
    """Coerce dashboard JSON and file values into a strict boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _coerce_id_list(value) -> list[str]:
    """Normalize Discord snowflake lists without passing them through JS numbers."""
    tokens = []
    seen = builtins.set()

    def add(raw_value):
        for match in re.findall(r"\d+", str(raw_value or "")):
            normalized = match.lstrip("0") or "0"
            if normalized == "0" or normalized in seen:
                continue
            seen.add(normalized)
            tokens.append(normalized)

    if isinstance(value, (list, tuple, builtins.set)):
        for item in value:
            add(item)
    else:
        add(value)

    return tokens


def _coerce_config_value(key: str, value):
    """Parse a known runtime config value at the storage/API boundary."""
    field = CONFIG_FIELDS.get(key)
    if field is None:
        return value

    if value is None and field.default is None:
        return None

    if key == "update_branch" and value is not None:
        value = str(value).strip().lower()

    if field.value_type is bool:
        return _coerce_bool(value, field.default)

    if field.value_type is dict:
        return value if isinstance(value, dict) else dict(field.default)

    if field.value_type is list:
        return _coerce_id_list(value)

    try:
        if field.value_type is int:
            coerced = int(value)
        elif field.value_type is float:
            coerced = float(value)
        elif field.value_type is str:
            if value is None:
                return None if field.default is None else str(field.default)
            coerced = str(value)
        else:
            coerced = value
    except (TypeError, ValueError):
        return field.default

    if isinstance(coerced, float) and not math.isfinite(coerced):
        return field.default

    if isinstance(coerced, (int, float)):
        if field.min_value is not None and coerced < field.min_value:
            coerced = field.min_value
        if field.max_value is not None and coerced > field.max_value:
            coerced = field.max_value
        if field.value_type is int:
            coerced = int(coerced)

    if field.choices is not None and coerced not in field.choices:
        return field.default

    return coerced


def _default_value(key: str):
    """Return a fresh default value for mutable runtime settings."""
    value = DEFAULTS[key]
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def _normalize_config(config: dict | None) -> dict:
    """Backfill renamed keys, parse known fields, and merge missing defaults."""
    normalized = dict(config or {})

    for removed_key in REMOVED_CONFIG_KEYS:
        normalized.pop(removed_key, None)

    for legacy_key, current_key in LEGACY_KEY_ALIASES.items():
        if current_key not in normalized and legacy_key in normalized:
            normalized[current_key] = normalized[legacy_key]
        normalized.pop(legacy_key, None)

    for key, value in list(normalized.items()):
        normalized[key] = _coerce_config_value(key, value)

    for key, value in DEFAULTS.items():
        if key not in normalized:
            normalized[key] = _default_value(key)
        else:
            normalized[key] = _coerce_config_value(key, normalized[key])

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
    return _normalize_config({})


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
    _apply_logging_config(config)


def get(key: str, default=None):
    """Get a config value."""
    key = _normalize_key(key)
    if key in REMOVED_CONFIG_KEYS:
        return default
    config = load_config()
    return config.get(key, default if default is not None else DEFAULTS.get(key))


def set(key: str, value):
    """Set a config value."""
    key = _normalize_key(key)
    if key in REMOVED_CONFIG_KEYS:
        return
    config = load_config().copy()  # Copy only when modifying
    config[key] = _coerce_config_value(key, value)
    save_config(config)


def get_all() -> dict:
    """Get all config values (returns copy for safety)."""
    return load_config().copy()


def _configured_id_set(config: dict, key: str) -> builtins.set[str]:
    """Return one normalized Discord ID config list as a set of strings."""
    value = config.get(key, DEFAULTS.get(key, []))
    return builtins.set(_coerce_id_list(value))


def is_server_response_allowed(channel_id, config: dict | None = None) -> tuple[bool, str | None]:
    """Check whether normal replies may be generated in a server channel."""
    config = config or load_config()
    if not config.get("server_responses_enabled", DEFAULTS["server_responses_enabled"]):
        return False, "server_responses_disabled"

    channel_key = str(channel_id)
    if channel_key in _configured_id_set(config, "response_channel_blacklist"):
        return False, "response_channel_blacklist"

    if config.get("response_channel_whitelist_only", DEFAULTS["response_channel_whitelist_only"]):
        if channel_key not in _configured_id_set(config, "response_channel_whitelist"):
            return False, "response_channel_not_whitelisted"

    return True, None


def is_dm_response_allowed(user_id, config: dict | None = None) -> tuple[bool, str | None]:
    """Check whether direct-message replies or DM delivery may be sent to a user."""
    config = config or load_config()
    if not config.get("dm_responses_enabled", DEFAULTS["dm_responses_enabled"]):
        return False, "dm_responses_disabled"

    user_key = str(user_id)
    if user_key in _configured_id_set(config, "dm_user_blacklist"):
        return False, "dm_user_blacklist"

    return True, None


def _apply_logging_config(config: dict | None = None) -> None:
    """Apply runtime logging settings without making logger import mandatory at startup."""
    config = config or load_config()
    try:
        import logger
        logger.configure_file_logging(
            enabled=config.get("file_logging_enabled", DEFAULTS["file_logging_enabled"]),
            max_bytes=int(config.get("log_file_max_mb", DEFAULTS["log_file_max_mb"])) * 1024 * 1024,
        )
        logger.LOG_LEVEL = logger.DIAGNOSTIC if config.get("diagnostic_logging", False) else logger.NORMAL
    except Exception:
        pass


def apply_logging_config() -> None:
    """Apply persisted logging settings to the logger module."""
    _apply_logging_config(load_config())


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
