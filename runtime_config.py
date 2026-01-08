"""
Discord Pals - Runtime Configuration
Live-adjustable settings via the web dashboard.
"""

import json
import os
import time
from config import RUNTIME_CONFIG_FILE, DATA_DIR

# Default values
DEFAULTS = {
    "history_limit": 200,  # Total messages to include (history + immediate)
    "immediate_message_count": 5,  # Last N messages as "current" (placed after chatroom context)
    "batch_timeout": 15,
    "active_provider": None,  # None = use first provider
    "bot_interactions_paused": False,  # Global stop for bot-bot conversations
    "global_paused": False,  # KILLSWITCH: Stops ALL bot activity when True
    "use_single_user": False,  # Message format: True = SillyTavern-style single user message, False = multi-role (system/user/assistant)
    "name_trigger_chance": 1.0,  # 0.0-1.0, chance to respond when bot's name/nickname is mentioned without @mention
    "custom_nicknames": "",  # Comma-separated list of additional nicknames the bot should respond to
    "raw_generation_logging": False,  # Log raw LLM output to live logs
    # Bot-on-bversation fall-off settings
    "bot_falloff_enabled": True,  # Enable progressive fall-off for bot-bot conversations
    "bot_falloff_base_chance": 0.8,  # Initial response probability (80%)
    "bot_falloff_decay_rate": 0.15,  # Decay per consecutive bot message (15%)
    "bot_falloff_min_chance": 0.05,  # Minimum probability floor (5%)
    "bot_falloff_hard_limit": 10,  # Hard cutoff after N consecutive bot messages
}

# Config cache to avoid repeated file reads
_config_cache: dict = None
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL = 30.0  # Seconds before cache expires (increased from 2.0 for performance)


def ensure_data_dir():
    """Create data directory if it doesn't exist."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


def _load_config_from_disk() -> dict:
    """Load config from disk (internal, no caching)."""
    ensure_data_dir()
    if os.path.exists(RUNTIME_CONFIG_FILE):
        try:
            with open(RUNTIME_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # Merge with defaults for any missing keys
                for key, value in DEFAULTS.items():
                    if key not in config:
                        config[key] = value
                return config
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULTS.copy()


def load_config() -> dict:
    """Load runtime config with caching to avoid repeated disk reads."""
    global _config_cache, _config_cache_time

    now = time.time()
    if _config_cache is not None and (now - _config_cache_time) < _CONFIG_CACHE_TTL:
        return _config_cache.copy()

    _config_cache = _load_config_from_disk()
    _config_cache_time = now
    return _config_cache.copy()


def invalidate_cache():
    """Invalidate the config cache (call after writes)."""
    global _config_cache, _config_cache_time
    _config_cache = None
    _config_cache_time = 0.0


def save_config(config: dict):
    """Save runtime config to file and invalidate cache."""
    ensure_data_dir()
    with open(RUNTIME_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
    invalidate_cache()


def get(key: str, default=None):
    """Get a config value."""
    config = load_config()
    return config.get(key, default if default is not None else DEFAULTS.get(key))


def set(key: str, value):
    """Set a config value."""
    config = load_config()
    config[key] = value
    save_config(config)


def get_all() -> dict:
    """Get all config values."""
    return load_config()


# Last context storage for visualization
_last_context = {}

# Last activity tracking
_last_activity = {}


def store_last_context(bot_name: str, system_prompt: str, messages: list,
                       token_estimate: int = 0):
    """Store the last context sent to LLM for visualization."""
    _last_context[bot_name] = {
        "system_prompt": system_prompt,
        "messages": messages,
        "token_estimate": token_estimate,
        "message_count": len(messages)
    }


def get_last_context(bot_name: str = None) -> dict:
    """Get the last context sent to LLM."""
    if bot_name:
        return _last_context.get(bot_name, {})
    return _last_context


def update_last_activity(bot_name: str):
    """Update last activity timestamp for a bot."""
    _last_activity[bot_name] = time.time()


def get_last_activity(bot_name: str = None) -> dict:
    """Get last activity timestamp(s)."""
    if bot_name:
        return _last_activity.get(bot_name)
    return _last_activity
