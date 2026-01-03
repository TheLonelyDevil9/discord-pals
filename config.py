"""
Discord Pals - Configuration
API keys, provider URLs, and bot settings.
"""

import os
import json
from dotenv import load_dotenv

load_dotenv()

# Discord Bot Token
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')


# AI Settings (defined before load_providers so they can be used as defaults)
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_TOKENS = 2048

# --- Provider Configuration ---

def load_providers() -> tuple[dict, int]:
    """Load providers from providers.json or use defaults.
    
    Returns:
        tuple: (providers_dict, timeout_seconds)
    """
    config_path = os.path.join(os.path.dirname(__file__), "providers.json")
    timeout = 600  # Default 10 minutes (local LLMs can be slow)
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"⚠️ Invalid providers.json: {e}")
            return {}, timeout
        
        providers = {}
        provider_list = data.get("providers", [])
        
        if not provider_list:
            print("⚠️ providers.json has no providers defined")
            return {}, timeout
        
        for i, p in enumerate(provider_list):
            tier = ["primary", "secondary", "fallback"][i] if i < 3 else f"tier_{i}"
            
            # Validate required fields
            if not p.get("url"):
                print(f"⚠️ Provider {i+1} missing 'url', skipping")
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
                "name": p.get("name", f"Provider {i+1}"),
                "url": p.get("url"),
                "key": key,
                "model": p.get("model", "gpt-4o"),
                "max_tokens": p.get("max_tokens", DEFAULT_MAX_TOKENS),  # Per-provider max tokens
                "temperature": p.get("temperature", DEFAULT_TEMPERATURE),  # Per-provider temperature
                "extra_body": p.get("extra_body", {}),  # Legacy: dict of custom request body options
                # SillyTavern-style YAML parameters (preferred)
                "include_body": p.get("include_body", ""),  # YAML string to merge into request body
                "exclude_body": p.get("exclude_body", ""),  # YAML string of keys to remove from request
                "include_headers": p.get("include_headers", ""),  # YAML string to merge into headers
            }
        
        timeout = data.get("timeout", 60)
        return providers, timeout
    
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
    }, timeout


PROVIDERS, API_TIMEOUT = load_providers()

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
