"""
Discord Pals - Main Entry Point
Multi-bot architecture: Runs multiple Discord clients from one process.
"""

import asyncio
import logging
from typing import List

# Suppress verbose logging from all libraries
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)
logging.getLogger('discord.gateway').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('openai._base_client').setLevel(logging.WARNING)

from config import DISCORD_TOKEN, DEFAULT_CHARACTER
from bot_instance import BotInstance
from discord_utils import save_history
from memory import memory_manager
import logger as log


# --- Bot Loading ---

def load_bot_configs() -> List[dict]:
    """Load bot configurations from bots.json or fall back to single-bot mode."""
    import json
    import os
    import runtime_config
    
    if os.path.exists('bots.json'):
        try:
            with open('bots.json', 'r') as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            log.warn("Failed to parse bots.json, using single-bot mode")
            config = {}
        
        bots = []
        for bot_cfg in config.get('bots', []):
            token = os.getenv(bot_cfg['token_env'])
            if not token:
                log.warn(f"Token env var '{bot_cfg['token_env']}' not set, skipping {bot_cfg['name']}")
                continue
            bots.append({
                "name": bot_cfg["name"],
                "token": token,
                "character_name": bot_cfg["character"],
                "nicknames": bot_cfg.get("nicknames", "")  # Custom nicknames for this bot
            })
        
        if bots:
            return bots
        log.warn("No valid bots in bots.json, falling back to single-bot mode")
    
    # Fallback: Single bot mode
    if not DISCORD_TOKEN:
        log.error("DISCORD_TOKEN not set!")
        return []
    
    # Load nicknames from runtime_config for single-bot mode persistence
    bot_nicknames = runtime_config.get('bot_nicknames', {})
    default_nicknames = bot_nicknames.get('Default', '')
    
    return [{
        "name": "Default",
        "token": DISCORD_TOKEN,
        "character_name": DEFAULT_CHARACTER,
        "nicknames": default_nicknames
    }]


async def run_bots():
    """Run all configured bots."""
    configs = load_bot_configs()
    
    if not configs:
        log.error("No bots configured!")
        return
    
    log.startup(f"Starting {len(configs)} bot(s)...")
    log.divider()
    
    instances = [BotInstance(**cfg) for cfg in configs]
    
    # Start web dashboard
    try:
        from dashboard import start_dashboard
        start_dashboard(bots=instances, host='0.0.0.0', port=5000)
        log.online("Dashboard running at http://localhost:5000")
    except Exception as e:
        log.warn(f"Dashboard failed to start: {e}")
    
    try:
        await asyncio.gather(*[bot.start() for bot in instances])
    except KeyboardInterrupt:
        log.info("Shutting down...")
        save_history()  # Persist conversation history
        memory_manager.save_all()  # Persist memories
        for bot in instances:
            await bot.close()


# --- Entry Point ---

if __name__ == "__main__":
    # Run startup validation first
    from startup import validate_startup
    
    if not validate_startup(interactive=True):
        log.error("Startup validation failed. Please fix the issues above.")
        import sys
        sys.exit(1)
    
    log.divider()
    asyncio.run(run_bots())
