"""
Discord Pals - User Ignore List Management
Allows users to block specific bots from responding to them.
"""

import json
import threading
from pathlib import Path
from typing import List, Set

import logger as log

# Storage file
DATA_DIR = Path("data")
IGNORES_FILE = DATA_DIR / "user_ignores.json"

# In-memory cache with thread lock
_ignores: dict[str, Set[str]] = {}
_lock = threading.Lock()


def _load() -> None:
    """Load ignores from disk into memory."""
    global _ignores
    try:
        if IGNORES_FILE.exists():
            with open(IGNORES_FILE, 'r') as f:
                data = json.load(f)
                # Convert lists to sets for faster lookup
                _ignores = {k: set(v) for k, v in data.items()}
        else:
            _ignores = {}
    except Exception as e:
        log.error(f"Failed to load user ignores: {e}")
        _ignores = {}


def _save() -> None:
    """Save ignores from memory to disk."""
    try:
        DATA_DIR.mkdir(exist_ok=True)
        with open(IGNORES_FILE, 'w') as f:
            # Convert sets to lists for JSON serialization
            data = {k: list(v) for k, v in _ignores.items()}
            json.dump(data, f, indent=2)
    except Exception as e:
        log.error(f"Failed to save user ignores: {e}")


def is_ignored(user_id: str, bot_name: str) -> bool:
    """Check if a user has ignored a specific bot.

    Args:
        user_id: Discord user ID as string
        bot_name: Character/bot name to check

    Returns:
        True if the user has ignored this bot
    """
    with _lock:
        if not _ignores:
            _load()
        user_ignores = _ignores.get(user_id, set())
        # Case-insensitive comparison
        return bot_name.lower() in {name.lower() for name in user_ignores}


def add_ignore(user_id: str, bot_name: str) -> bool:
    """Add a bot to a user's ignore list.

    Args:
        user_id: Discord user ID as string
        bot_name: Character/bot name to ignore

    Returns:
        True if added, False if already ignored
    """
    with _lock:
        if not _ignores:
            _load()

        if user_id not in _ignores:
            _ignores[user_id] = set()

        # Check if already ignored (case-insensitive)
        existing = {name.lower() for name in _ignores[user_id]}
        if bot_name.lower() in existing:
            return False

        _ignores[user_id].add(bot_name)
        _save()
        log.info(f"User {user_id} now ignoring bot: {bot_name}")
        return True


def remove_ignore(user_id: str, bot_name: str) -> bool:
    """Remove a bot from a user's ignore list.

    Args:
        user_id: Discord user ID as string
        bot_name: Character/bot name to unignore

    Returns:
        True if removed, False if wasn't ignored
    """
    with _lock:
        if not _ignores:
            _load()

        if user_id not in _ignores:
            return False

        # Find the actual name (case-insensitive match)
        to_remove = None
        for name in _ignores[user_id]:
            if name.lower() == bot_name.lower():
                to_remove = name
                break

        if to_remove:
            _ignores[user_id].discard(to_remove)
            if not _ignores[user_id]:
                del _ignores[user_id]
            _save()
            log.info(f"User {user_id} no longer ignoring bot: {bot_name}")
            return True
        return False


def get_ignores(user_id: str) -> List[str]:
    """Get list of bots a user has ignored.

    Args:
        user_id: Discord user ID as string

    Returns:
        List of ignored bot names
    """
    with _lock:
        if not _ignores:
            _load()
        return list(_ignores.get(user_id, set()))


def get_all_ignores() -> dict:
    """Get all ignore data (for admin/debug purposes)."""
    with _lock:
        if not _ignores:
            _load()
        return {k: list(v) for k, v in _ignores.items()}
