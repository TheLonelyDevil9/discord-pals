"""
Discord Pals - User Ignore List Management
Allows users to block specific bots from responding to them.
"""

import json
import re
import threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Set, Tuple

import logger as log
from config import DATA_DIR as CONFIG_DATA_DIR

# Storage file
DATA_DIR = Path(CONFIG_DATA_DIR)
IGNORES_FILE = DATA_DIR / "user_ignores.json"
LEGACY_IGNORES_FILE = Path("data") / "user_ignores.json"

# In-memory cache with thread lock
_ignores: dict[str, Set[str]] = {}
_lock = threading.Lock()


def _normalize_name(value: str) -> str:
    """Normalize a bot name for case-insensitive fuzzy matching."""
    if not isinstance(value, str):
        return ""
    value = re.sub(r'\s+', ' ', value.strip().lstrip('@')).lower()
    return value


def _canonical_name(value: str) -> str:
    """Aggressive canonicalization for matching nicknames/spacing variants."""
    return re.sub(r'[^a-z0-9]+', '', _normalize_name(value))


def _is_ignored_in_set(user_ignores: Set[str], bot_name: str) -> bool:
    """Check ignore membership against a specific set (no locking)."""
    normalized = _normalize_name(bot_name)
    canonical = _canonical_name(bot_name)
    for existing in user_ignores:
        if _normalize_name(existing) == normalized:
            return True
        if canonical:
            existing_can = _canonical_name(existing)
            if existing_can == canonical:
                return True
            # Allow short aliases like "max" to match "maxverstappen" and vice-versa.
            if len(canonical) >= 3 and existing_can.startswith(canonical):
                return True
            if len(existing_can) >= 3 and canonical.startswith(existing_can):
                return True
    return False


def _find_best_match_from_options(options: List[str], bot_name: str, max_suggestions: int = 3) -> Tuple[str | None, List[str]]:
    """Find best ignore match from already-loaded option list (no locking)."""
    if not options:
        return None, []

    query_norm = _normalize_name(bot_name)
    query_can = _canonical_name(bot_name)
    if not query_norm:
        return None, options[:max_suggestions]

    # Exact normalize match first.
    for option in options:
        if _normalize_name(option) == query_norm:
            return option, []

    # Canonical equality match.
    if query_can:
        canonical_matches = [o for o in options if _canonical_name(o) == query_can]
        if len(canonical_matches) == 1:
            return canonical_matches[0], []

    # Prefix match against normalized/canonical forms.
    prefix_matches = []
    for option in options:
        norm = _normalize_name(option)
        can = _canonical_name(option)
        if norm.startswith(query_norm) or (query_can and can.startswith(query_can)):
            prefix_matches.append(option)
    if len(prefix_matches) == 1:
        return prefix_matches[0], []

    # Fuzzy match with confidence threshold.
    scored = []
    for option in options:
        ratio = SequenceMatcher(None, query_norm, _normalize_name(option)).ratio()
        if query_can:
            ratio = max(ratio, SequenceMatcher(None, query_can, _canonical_name(option)).ratio())
        scored.append((ratio, option))
    scored.sort(key=lambda item: (-item[0], item[1].lower()))

    best_score, best_name = scored[0]
    if best_score >= 0.72:
        # Avoid ambiguous fuzzy picks if top two are too close.
        if len(scored) == 1 or (best_score - scored[1][0]) >= 0.08:
            return best_name, [name for _, name in scored[1:max_suggestions + 1]]

    suggestions = [name for _, name in scored[:max_suggestions]]
    return None, suggestions


def _migrate_legacy_file_if_needed() -> None:
    """Migrate ignore data from old ./data path to configured DATA_DIR."""
    if IGNORES_FILE.exists() or not LEGACY_IGNORES_FILE.exists():
        return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LEGACY_IGNORES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        with open(IGNORES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        log.info("Migrated user_ignores.json from legacy data/ path")
    except Exception as e:
        log.warn(f"Failed legacy ignore migration: {e}")


def _load() -> None:
    """Load ignores from disk into memory."""
    global _ignores
    try:
        _migrate_legacy_file_if_needed()
        if IGNORES_FILE.exists():
            with open(IGNORES_FILE, 'r', encoding='utf-8') as f:
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
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(IGNORES_FILE, 'w', encoding='utf-8') as f:
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
        return _is_ignored_in_set(_ignores.get(user_id, set()), bot_name)


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

        # Check if already ignored (case-insensitive + canonical)
        if _is_ignored_in_set(_ignores[user_id], bot_name):
            return False

        clean_name = re.sub(r'\s+', ' ', bot_name.strip())
        _ignores[user_id].add(clean_name)
        _save()
        log.info(f"User {user_id} now ignoring bot: {clean_name}")
        return True


def find_best_ignore_match(user_id: str, bot_name: str, max_suggestions: int = 3) -> Tuple[str | None, List[str]]:
    """Find the best ignore entry match for a user-provided bot name.

    Returns:
        (matched_name_or_none, suggestions)
    """
    with _lock:
        if not _ignores:
            _load()

        options = sorted(_ignores.get(user_id, set()))
        return _find_best_match_from_options(options, bot_name, max_suggestions=max_suggestions)


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

        # Find the actual name (case-insensitive + canonical + fuzzy)
        options = sorted(_ignores.get(user_id, set()))
        to_remove, _suggestions = _find_best_match_from_options(options, bot_name)

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
