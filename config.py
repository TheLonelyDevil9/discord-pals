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


# --- Provider Configuration ---

def load_providers():
    """Load providers from providers.json or use defaults."""
    config_path = os.path.join(os.path.dirname(__file__), "providers.json")
    timeout = 60  # Default
    
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            data = json.load(f)
        
        providers = {}
        for i, p in enumerate(data.get("providers", [])):
            tier = ["primary", "secondary", "fallback"][i] if i < 3 else f"tier_{i}"
            
            # Support requires_key=false for local LLMs
            requires_key = p.get("requires_key", True)
            key_env = p.get("key_env", "")
            
            if requires_key:
                key = os.getenv(key_env, "")
            else:
                # Use placeholder for keyless providers (e.g., local llama.cpp)
                key = os.getenv(key_env, "") or "not-needed"
            
            providers[tier] = {
                "name": p.get("name", f"Provider {i+1}"),
                "url": p.get("url"),
                "key": key,
                "model": p.get("model", "gpt-4o")
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

# AI Settings
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_TOKENS = 8192

# Character Settings
CHARACTERS_DIR = "characters"
DEFAULT_CHARACTER = os.getenv('DEFAULT_CHARACTER', 'firefly')

# Data Storage
DATA_DIR = "bot_data"
MEMORIES_FILE = os.path.join(DATA_DIR, "memories.json")
DM_MEMORIES_FILE = os.path.join(DATA_DIR, "dm_memories.json")
USER_MEMORIES_FILE = os.path.join(DATA_DIR, "user_memories.json")
LORE_FILE = os.path.join(DATA_DIR, "lore.json")
AUTONOMOUS_FILE = os.path.join(DATA_DIR, "autonomous.json")

# Timing
ERROR_DELETE_AFTER = 10.0

# Limits
MAX_HISTORY_MESSAGES = 1000
MAX_EMOJIS_IN_PROMPT = 50
