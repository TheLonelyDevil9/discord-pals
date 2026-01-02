"""
Discord Pals - Runtime Configuration
Live-adjustable settings via the web dashboard.
"""

import json
import os
from config import RUNTIME_CONFIG_FILE, DATA_DIR

# Default values
DEFAULTS = {
    "history_limit": 200,  # Total messages to include (history + immediate)
    "immediate_message_count": 5,  # Last N messages as "current" (placed after chatroom context)
    "batch_timeout": 15,
    "active_provider": None,  # None = use first provider
    "bot_interactions_paused": False,  # Global stop for bot-bot conversations
}


def ensure_data_dir():
    """Create data directory if it doesn't exist."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


def load_config() -> dict:
    """Load runtime config, creating with defaults if not exists."""
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


def save_config(config: dict):
    """Save runtime config to file."""
    ensure_data_dir()
    with open(RUNTIME_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


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
