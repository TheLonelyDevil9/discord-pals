"""
Discord Pals - Configuration
API keys, provider URLs, and bot settings.
"""

import os
import json
from dotenv import load_dotenv
import logger as log

load_dotenv()

# Discord Bot Token
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')


# AI Settings (defined before load_providers so they can be used as defaults)
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_TOKENS = 2048

# --- Provider Configuration ---


def _validate_provider_value(value, expected_type, default, min_val=None, max_val=None, name="value"):
    """Validate and coerce a provider config value.

    Args:
        value: The value to validate
        expected_type: Expected type (int, float, str, dict)
        default: Default value if validation fails
        min_val: Minimum value for numeric types
        max_val: Maximum value for numeric types
        name: Name for logging

    Returns:
        Validated value or default
    """
    if value is None:
        return default

    # Type coercion
    try:
        if expected_type == int:
            value = int(value)
        elif expected_type == float:
            value = float(value)
        elif expected_type == str:
            value = str(value)
        elif expected_type == dict:
            if not isinstance(value, dict):
                log.warn(f"Provider {name} expected dict, got {type(value).__name__}, using default")
                return default
    except (ValueError, TypeError):
        log.warn(f"Provider {name} invalid type, using default: {default}")
        return default

    # Range validation for numeric types
    if expected_type in (int, float):
        if min_val is not None and value < min_val:
            log.warn(f"Provider {name}={value} below minimum {min_val}, clamping")
            value = min_val
        if max_val is not None and value > max_val:
            log.warn(f"Provider {name}={value} above maximum {max_val}, clamping")
            value = max_val

    return value

def load_providers() -> tuple[dict, int, dict]:
    """Load providers from providers.json or use defaults.

    Returns:
        tuple: (providers_dict, timeout_seconds, character_providers_dict)
    """
    config_path = os.path.join(os.path.dirname(__file__), "providers.json")
    timeout = 600  # Default 10 minutes (local LLMs can be slow)
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log.warn(f"Invalid providers.json: {e}")
            return {}, timeout, {}
        
        providers = {}
        provider_list = data.get("providers", [])
        
        if not provider_list:
            log.warn("providers.json has no providers defined")
            return {}, timeout, {}
        
        for i, p in enumerate(provider_list):
            tier = ["primary", "secondary", "fallback"][i] if i < 3 else f"tier_{i}"
            
            # Validate required fields
            if not p.get("url"):
                log.warn(f"Provider {i+1} missing 'url', skipping")
                continue
            
            # Support requires_key=false for local LLMs
            # Auto-detect: if key_env is empty or not set, assume no key needed
            key_env = p.get("key_env", "")
            requires_key = p.get("requires_key", bool(key_env))  # Auto-detect based on key_env
            
            if requires_key and key_env:
                key = os.getenv(key_env, "")
            else:
                # Use placeholder for keyless providers (e.g., local llama.cpp)
                key = os.getenv(key_env, "") if key_env else "not-needed"
            
            providers[tier] = {
                "name": _validate_provider_value(p.get("name"), str, f"Provider {i+1}", name="name"),
                "url": p.get("url"),  # Already validated above
                "key": key,
                "model": _validate_provider_value(p.get("model"), str, "gpt-4o", name="model"),
                "max_tokens": _validate_provider_value(p.get("max_tokens"), int, DEFAULT_MAX_TOKENS, min_val=1, max_val=128000, name="max_tokens"),
                "temperature": _validate_provider_value(p.get("temperature"), float, DEFAULT_TEMPERATURE, min_val=0.0, max_val=2.0, name="temperature"),
                "extra_body": _validate_provider_value(p.get("extra_body"), dict, {}, name="extra_body"),
                # SillyTavern-style YAML parameters (preferred)
                "include_body": _validate_provider_value(p.get("include_body"), str, "", name="include_body"),
                "exclude_body": _validate_provider_value(p.get("exclude_body"), str, "", name="exclude_body"),
                "include_headers": _validate_provider_value(p.get("include_headers"), str, "", name="include_headers"),
            }

        timeout = _validate_provider_value(data.get("timeout"), int, 60, min_val=5, max_val=3600, name="timeout")

        # Load character provider preferences
        character_providers = data.get("character_providers", {})
        # Validate that values are valid tier names
        valid_tiers = set(providers.keys())
        validated_char_providers = {}
        for char_name, tier in character_providers.items():
            if tier in valid_tiers:
                validated_char_providers[char_name] = tier
            else:
                log.warn(f"Invalid tier '{tier}' for character '{char_name}', ignoring")

        return providers, timeout, validated_char_providers
    
    # Default fallback (original hardcoded config)
    return {
        "primary": {
            "name": "OpenAI",
            "url": "https://api.openai.com/v1",
            "key": os.getenv('OPENAI_API_KEY'),
            "model": "gpt-4o"
        },
        "secondary": {
            "name": "DeepSeek",
            "url": "https://api.deepseek.com/v1",
            "key": os.getenv('DEEPSEEK_API_KEY'),
            "model": "deepseek-chat"
        }
    }, timeout, {}


PROVIDERS, API_TIMEOUT, CHARACTER_PROVIDERS = load_providers()


def reload_character_providers() -> dict:
    """Reload character_providers from providers.json without full restart."""
    global CHARACTER_PROVIDERS
    config_path = os.path.join(os.path.dirname(__file__), "providers.json")

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                data = json.load(f)
            char_providers = data.get("character_providers", {})
            # Validate against current provider tiers
            valid_tiers = set(PROVIDERS.keys())
            CHARACTER_PROVIDERS = {k: v for k, v in char_providers.items() if v in valid_tiers}
            return CHARACTER_PROVIDERS
        except Exception as e:
            log.warn(f"Failed to reload character_providers: {e}")

    return CHARACTER_PROVIDERS

# Character Settings
CHARACTERS_DIR = "characters"
DEFAULT_CHARACTER = os.getenv('DEFAULT_CHARACTER', 'firefly')

# Data Storage
DATA_DIR = "bot_data"
MEMORIES_FILE = os.path.join(DATA_DIR, "memories.json")
DM_MEMORIES_FILE = os.path.join(DATA_DIR, "dm_memories.json")  # Legacy shared file
USER_MEMORIES_FILE = os.path.join(DATA_DIR, "user_memories.json")  # Legacy shared file
LORE_FILE = os.path.join(DATA_DIR, "lore.json")
AUTONOMOUS_FILE = os.path.join(DATA_DIR, "autonomous.json")

# Per-character memory directories
DM_MEMORIES_DIR = os.path.join(DATA_DIR, "dm_memories")
USER_MEMORIES_DIR = os.path.join(DATA_DIR, "user_memories")

# Global user profiles (cross-server, per-user facts that follow users everywhere)
GLOBAL_USER_PROFILES_FILE = os.path.join(DATA_DIR, "user_profiles.json")

# Runtime config (live-adjustable settings via dashboard)
RUNTIME_CONFIG_FILE = os.path.join(DATA_DIR, "runtime_config.json")

# Timing
ERROR_DELETE_AFTER = 10.0

# Limits
MAX_HISTORY_MESSAGES = 1000
MAX_EMOJIS_IN_PROMPT = 50
