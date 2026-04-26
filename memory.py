"""
Discord Pals - Memory System
Unified 2-store memory system: auto memories + manual lore.
With debounced saving, LLM profile consolidation, and legacy migration.
"""

import os
import re
import time
import glob
import difflib
import hashlib
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from config import (
    DATA_DIR, MEMORIES_FILE, DM_MEMORIES_FILE, USER_MEMORIES_FILE,
    LORE_FILE, DM_MEMORIES_DIR, USER_MEMORIES_DIR, GLOBAL_USER_PROFILES_FILE,
    AUTO_MEMORIES_FILE, MANUAL_LORE_FILE, MEMORY_STATE_FILE
)
from discord_utils import safe_json_load, safe_json_save, remove_thinking_tags
from scopes import auto_memory_key, dm_auto_memory_key as scoped_dm_auto_memory_key, dm_memory_server_id
from constants import (
    MEMORY_SAVE_INTERVAL, SEMANTIC_SIMILARITY_THRESHOLD,
    TEXTUAL_SIMILARITY_THRESHOLD, KEY_TERM_OVERLAP_THRESHOLD
)
import logger as log


def ensure_data_dir():
    """Create data directory and subdirectories if they don't exist."""
    for dir_path in [DATA_DIR, DM_MEMORIES_DIR, USER_MEMORIES_DIR]:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)


# =============================================================================
# MEMORY DEDUPLICATION
# =============================================================================

def _normalize_memory(text: str) -> str:
    """Normalize memory text for comparison."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)  # Remove punctuation
    text = re.sub(r'\s+', ' ', text).strip()  # Normalize whitespace
    return text


def _extract_key_terms(text: str) -> set:
    """Extract key terms (names, nouns) for comparison."""
    stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'has', 'have', 'had',
                  'that', 'this', 'they', 'them', 'their', 'mentioned', 'said', 'told',
                  'about', 'with', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                  'of', 'it', 'be', 'been', 'being', 'who', 'which', 'what', 'when',
                  'where', 'how', 'why', 'also', 'just', 'only', 'very', 'really'}
    words = set(_normalize_memory(text).split())
    return words - stop_words


def _cosine_similarity(vec1: list, vec2: list) -> float:
    """Calculate cosine similarity between two embedding vectors (pure Python)."""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def _sanitize_memory_content(content: str) -> str:
    """Normalize/sanitize memory text before storing."""
    if not isinstance(content, str):
        return ""
    cleaned = remove_thinking_tags(content).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned


def _memory_fingerprint(content: str) -> str:
    """Build a stable idempotency key for memory text using SHA256 hash."""
    normalized = _normalize_memory(content)
    if not normalized:
        return ""
    digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    return digest[:24]  # Use first 24 chars for compact storage


PROFILE_ENTRY = "profile"
PENDING_ENTRY = "pending"
LEGACY_ENTRY = "legacy"


def _is_dm_scope_value(value) -> bool:
    """Return whether a stored server_id/key value represents a DM memory scope."""
    return value == 0 or (isinstance(value, str) and value.startswith("dm:"))


def _normalize_server_id_value(value, fallback=None):
    """Normalize server_id while preserving per-bot DM namespace strings."""
    if value is None:
        value = fallback
    if isinstance(value, str) and value.startswith("dm:"):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback if isinstance(fallback, str) and fallback.startswith("dm:") else None


def _parse_auto_key(key: str) -> Tuple[Optional[int | str], Optional[int]]:
    """Parse a unified auto-memory key into server_id and user_id."""
    if not isinstance(key, str):
        return None, None

    server_match = re.fullmatch(r'server:(\d+):user:(\d+)', key)
    if server_match:
        return int(server_match.group(1)), int(server_match.group(2))

    dm_match = re.fullmatch(r'(dm:0):user:(\d+)', key)
    if dm_match:
        return 0, int(dm_match.group(2))

    dm_bot_match = re.fullmatch(r'(dm:bot:[^:]+):user:(\d+)', key)
    if dm_bot_match:
        return dm_bot_match.group(1), int(dm_bot_match.group(2))

    return None, None


def _safe_key_part(value: str) -> str:
    """Build a compact filesystem/JSON-key-safe identifier segment."""
    normalized = re.sub(r'[^a-zA-Z0-9_-]+', '-', str(value or '').strip()).strip('-')
    if normalized:
        return normalized[:48]
    return hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()[:16]


def dm_server_id_for_bot(bot_name: str | None) -> str:
    """Return the auto-memory namespace used for one bot's DMs."""
    return dm_memory_server_id(bot_name)


def dm_auto_memory_key(bot_name: str | None, user_id: int) -> str:
    """Return the per-bot, per-user DM auto-memory key."""
    return scoped_dm_auto_memory_key(bot_name, user_id)


def get_dm_server_id_for_bot(bot_name: str | None) -> str:
    """Public helper for callers that need a DM memory namespace."""
    return dm_server_id_for_bot(bot_name)


def get_dm_auto_memory_key(bot_name: str | None, user_id: int) -> str:
    """Public helper for callers that need a concrete DM auto-memory key."""
    return dm_auto_memory_key(bot_name, user_id)


def is_dm_memory_server_id(server_id) -> bool:
    """Return whether a server_id value represents a DM auto-memory namespace."""
    return _is_dm_scope_value(server_id)


def _parse_legacy_dm_auto_key(key: str) -> Optional[int]:
    dm_match = re.fullmatch(r'dm:0:user:(\d+)', key)
    if dm_match:
        return int(dm_match.group(1))
    return None


def _memory_entry_is_valid(entry: dict) -> bool:
    """Check if a memory entry has minimally valid structure."""
    if not isinstance(entry, dict):
        return False
    content = entry.get('content')
    timestamp = entry.get('timestamp')
    if not isinstance(content, str) or not content.strip():
        return False
    if timestamp is not None and not isinstance(timestamp, str):
        return False
    embedding = entry.get('embedding')
    if embedding is not None and not isinstance(embedding, list):
        return False
    return True


def _sanitize_memory_entries(
    entries: list,
    *,
    auto_default: bool,
    memory_key: str = None
) -> Tuple[list, bool]:
    """Drop malformed entries and normalize fields while preserving valid metadata."""
    if not isinstance(entries, list):
        return [], bool(entries)

    fallback_server_id, fallback_user_id = _parse_auto_key(memory_key)
    cleaned_entries = []
    seen_fingerprints = set()
    changed = False

    missing_entry_type_default = PROFILE_ENTRY if len(entries) <= 1 else LEGACY_ENTRY

    for entry in entries:
        if not _memory_entry_is_valid(entry):
            changed = True
            continue

        content = _sanitize_memory_content(entry.get('content', ''))
        if not content:
            changed = True
            continue

        fingerprint = entry.get('fingerprint')
        if not isinstance(fingerprint, str) or not fingerprint:
            fingerprint = _memory_fingerprint(content)

        # Skip duplicates within this list
        if fingerprint and fingerprint in seen_fingerprints:
            changed = True
            continue
        if fingerprint:
            seen_fingerprints.add(fingerprint)

        cleaned = {
            "content": content,
            "timestamp": entry.get("timestamp") or datetime.now().isoformat(),
            "auto": auto_default,
            "fingerprint": fingerprint
        }

        if auto_default:
            entry_type = entry.get("entry_type")
            if entry_type not in {PROFILE_ENTRY, PENDING_ENTRY, LEGACY_ENTRY}:
                entry_type = missing_entry_type_default if not entry_type else LEGACY_ENTRY
            cleaned["entry_type"] = entry_type

        # Preserve optional string metadata when valid.
        for key in ("character", "user_name", "server_name", "learned_from", "added_by"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                cleaned[key] = value

        for key in ("user_id", "server_id", "subject_user_id"):
            value = entry.get(key)
            if key == "user_id" and value is None and fallback_user_id is not None:
                value = fallback_user_id
            if key == "server_id" and value is None and fallback_server_id is not None:
                value = fallback_server_id

            if key == "server_id":
                value = _normalize_server_id_value(value, fallback=fallback_server_id)
                if value is None:
                    continue
                if isinstance(value, int) and value < 0:
                    continue
                cleaned[key] = value
                continue

            try:
                value = int(value)
            except (TypeError, ValueError):
                value = None

            if value is None:
                continue
            if key == "subject_user_id" and value <= 0:
                continue
            if key in ("user_id", "server_id") and value < 0:
                continue
            cleaned[key] = value

        if isinstance(entry.get("embedding"), list):
            cleaned["embedding"] = entry["embedding"]

        if cleaned != entry:
            changed = True
        cleaned_entries.append(cleaned)

    return cleaned_entries, changed


def _contains_fingerprint(memories: list, fingerprint: str) -> bool:
    """Check if a memory list already contains the same fingerprint."""
    if not fingerprint:
        return False
    for entry in memories:
        if isinstance(entry, dict) and entry.get('fingerprint') == fingerprint:
            return True
    return False


def _is_duplicate_memory(
    new_content: str,
    existing_memories: list,
    new_embedding: list = None,
    textual_threshold: float = TEXTUAL_SIMILARITY_THRESHOLD,
    semantic_threshold: float = SEMANTIC_SIMILARITY_THRESHOLD
) -> bool:
    """Check if new memory is semantically similar to existing ones.

    Uses four-stage check:
    0. Fingerprint check (instant) - exact match detection via SHA256
    1. Key term overlap (fast) - if <30% terms match, skip expensive checks
    2. Sequence similarity (accurate) - confirms with difflib
    3. Semantic similarity (embeddings) - catches paraphrases

    Args:
        new_content: The new memory content to check
        existing_memories: List of existing memory dicts with 'content' key
        new_embedding: Pre-computed embedding for new memory (or None)
        textual_threshold: Similarity threshold for difflib (0.0-1.0)
        semantic_threshold: Cosine similarity threshold for embeddings (0.0-1.0)

    Returns:
        True if duplicate found, False otherwise
    """
    if not existing_memories or not new_content:
        return False

    # Stage 0: Fingerprint check (fastest - exact duplicates)
    new_fingerprint = _memory_fingerprint(new_content)
    if new_fingerprint and _contains_fingerprint(existing_memories, new_fingerprint):
        log.debug(f"Fingerprint duplicate detected: '{new_content[:40]}'")
        return True

    new_normalized = _normalize_memory(new_content)
    new_terms = _extract_key_terms(new_content)

    for mem in existing_memories:
        existing_content = mem.get('content', '')
        if not existing_content:
            continue

        existing_normalized = _normalize_memory(existing_content)

        # Stage 1: Quick key term check
        existing_terms = _extract_key_terms(existing_content)
        if new_terms and existing_terms:
            overlap = len(new_terms & existing_terms) / max(len(new_terms), len(existing_terms))
            if overlap < KEY_TERM_OVERLAP_THRESHOLD:
                continue

        # Stage 2: Textual sequence similarity
        similarity = difflib.SequenceMatcher(None, new_normalized, existing_normalized).ratio()
        if similarity >= textual_threshold:
            log.debug(f"Textual duplicate detected (sim={similarity:.3f}): '{new_content[:40]}' ~ '{existing_content[:40]}'")
            return True

        # Stage 3: Semantic similarity via embeddings
        if new_embedding and mem.get('embedding'):
            semantic_sim = _cosine_similarity(new_embedding, mem['embedding'])
            if semantic_sim >= semantic_threshold:
                log.debug(f"Semantic duplicate detected (sim={semantic_sim:.3f}): '{new_content[:40]}' ~ '{existing_content[:40]}'")
                return True

    return False


def deduplicate_memory_strings(memory_strings: list) -> list:
    """Deduplicate a list of memory strings across different stores.

    Takes raw memory lines (strings) and removes near-duplicates using
    the same textual similarity logic as _is_duplicate_memory().
    Returns a list with duplicates removed (keeps first occurrence).
    """
    if not memory_strings:
        return memory_strings

    unique = []
    for line in memory_strings:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        # Check against already-accepted lines
        is_dup = False
        new_normalized = _normalize_memory(line_stripped)
        new_terms = _extract_key_terms(line_stripped)
        for accepted in unique:
            accepted_normalized = _normalize_memory(accepted.strip())
            accepted_terms = _extract_key_terms(accepted.strip())
            # Quick key term check
            if new_terms and accepted_terms:
                overlap = len(new_terms & accepted_terms) / max(len(new_terms), len(accepted_terms))
                if overlap < KEY_TERM_OVERLAP_THRESHOLD:
                    continue
            # Textual similarity
            similarity = difflib.SequenceMatcher(None, new_normalized, accepted_normalized).ratio()
            if similarity >= TEXTUAL_SIMILARITY_THRESHOLD:
                is_dup = True
                break
        if not is_dup:
            unique.append(line_stripped)

    return unique


def get_character_dm_file(character_name: str) -> str:
    """Get the DM memories file path for a specific character."""
    safe_name = character_name.lower().replace(" ", "_")
    return os.path.join(DM_MEMORIES_DIR, f"{safe_name}.json")


def get_character_user_file(character_name: str) -> str:
    """Get the user memories file path for a specific character."""
    safe_name = character_name.lower().replace(" ", "_")
    return os.path.join(USER_MEMORIES_DIR, f"{safe_name}.json")


class MemoryManager:
    """Unified 2-store memory system.

    Store 1: Auto Memories (auto_memories.json)
      - Keyed by "server:{guild_id}:user:{user_id}" or "dm:bot:{bot}:user:{user_id}"
      - Auto-generated from conversations, with one living profile plus one temporary pending entry

    Store 2: Manual Lore (manual_lore.json)
      - Keyed by "user:{user_id}", "bot:{bot_name}", or "server:{guild_id}"
      - Manually inserted via dashboard or commands

    Includes migration from the legacy 5-store system.
    """

    # Legacy compatibility: older memory_state files stored a threshold counter.
    _LLM_DEDUP_EVERY = 5

    def __init__(self):
        ensure_data_dir()

        # Migrate legacy stores if needed (before loading new stores)
        self._migrate_if_needed()

        # New unified stores
        self.auto_memories: Dict[str, List[dict]] = safe_json_load(AUTO_MEMORIES_FILE)
        self.manual_lore: Dict[str, List[dict]] = safe_json_load(MANUAL_LORE_FILE)
        self.memory_state: Dict[str, Dict[str, int]] = safe_json_load(MEMORY_STATE_FILE)

        auto_changed, lore_changed = self._normalize_loaded_stores()
        state_changed = self._normalize_memory_state()

        # Debounce tracking
        self._dirty_files: set = set()
        self._last_save = time.time()

        # Per-key dedup coordination
        self._dedup_in_flight: set = set()

        # Global channel-level memory generation cooldown (prevents multi-bot duplication)
        self._channel_memory_cooldown: Dict[int | str, float] = {}
        self._MEMORY_COOLDOWN_SECONDS = 30

        # Only warn once per runtime when embeddings are unavailable.
        self._embedding_unavailable_logged = False

        if auto_changed or lore_changed or state_changed:
            self.save_all()

    # ==========================================================================
    # PERSISTENCE
    # ==========================================================================

    def _mark_dirty(self, file_type: str):
        """Mark a file type as needing to be saved."""
        self._dirty_files.add(file_type)
        self._maybe_save()

    def _maybe_save(self):
        """Save dirty files if enough time has passed (debounced)."""
        if self._dirty_files and (time.time() - self._last_save) >= MEMORY_SAVE_INTERVAL:
            self._save_dirty_files()

    def _save_dirty_files(self):
        """Save all files marked as dirty."""
        if 'auto' in self._dirty_files:
            safe_json_save(AUTO_MEMORIES_FILE, self.auto_memories)
        if 'lore' in self._dirty_files:
            safe_json_save(MANUAL_LORE_FILE, self.manual_lore)
        if 'state' in self._dirty_files:
            safe_json_save(MEMORY_STATE_FILE, self.memory_state)
        self._dirty_files.clear()
        self._last_save = time.time()

    def flush(self):
        """Force save all dirty files — call on shutdown."""
        if self._dirty_files:
            self._save_dirty_files()

    def save_all(self):
        """Save all stores to disk."""
        safe_json_save(AUTO_MEMORIES_FILE, self.auto_memories)
        safe_json_save(MANUAL_LORE_FILE, self.manual_lore)
        safe_json_save(MEMORY_STATE_FILE, self.memory_state)
        self._dirty_files.clear()
        self._last_save = time.time()

    def _normalize_loaded_stores(self) -> Tuple[bool, bool]:
        """Normalize loaded stores and backfill missing metadata once at startup."""
        auto_changed = not isinstance(self.auto_memories, dict)
        lore_changed = not isinstance(self.manual_lore, dict)

        normalized_auto = {}
        if isinstance(self.auto_memories, dict):
            for key, entries in self.auto_memories.items():
                if not isinstance(key, str):
                    auto_changed = True
                    continue
                cleaned_entries, changed = _sanitize_memory_entries(
                    entries,
                    auto_default=True,
                    memory_key=key
                )
                auto_changed = auto_changed or changed
                if cleaned_entries:
                    normalized_auto[key] = cleaned_entries
        self.auto_memories = normalized_auto

        normalized_lore = {}
        if isinstance(self.manual_lore, dict):
            for key, entries in self.manual_lore.items():
                if not isinstance(key, str):
                    lore_changed = True
                    continue
                cleaned_entries, changed = _sanitize_memory_entries(
                    entries,
                    auto_default=False,
                    memory_key=key
                )
                lore_changed = lore_changed or changed
                if cleaned_entries:
                    normalized_lore[key] = cleaned_entries
        self.manual_lore = normalized_lore

        return auto_changed, lore_changed

    def _normalize_memory_state(self) -> bool:
        """Normalize persisted internal state and drop stale counters."""
        changed = False
        if not isinstance(self.memory_state, dict):
            self.memory_state = {}
            changed = True

        raw_pending = self.memory_state.get("pending_auto_since_dedup", {})
        if not isinstance(raw_pending, dict):
            raw_pending = {}
            changed = True

        normalized_pending = {}
        for key, value in raw_pending.items():
            if key not in self.auto_memories:
                changed = True
                continue
            count = 1 if self._pending_index(key) is not None else 0
            if count:
                normalized_pending[key] = count
            try:
                previous_count = int(value or 0)
            except (TypeError, ValueError):
                previous_count = None
            if previous_count != count:
                changed = True

        for key in self.auto_memories.keys():
            if self._pending_index(key) is not None and key not in normalized_pending:
                normalized_pending[key] = 1
                changed = True

        normalized_state = {"pending_auto_since_dedup": normalized_pending}
        if normalized_state != self.memory_state:
            changed = True
            self.memory_state = normalized_state
        else:
            self.memory_state = normalized_state

        return changed

    def _pending_auto_counts(self) -> Dict[str, int]:
        """Return the mutable pending-auto counter mapping."""
        pending = self.memory_state.setdefault("pending_auto_since_dedup", {})
        if not isinstance(pending, dict):
            pending = {}
            self.memory_state["pending_auto_since_dedup"] = pending
        return pending

    def _get_pending_auto_count(self, key: str) -> int:
        """Get the persisted count of new auto memories since the last dedup."""
        return int(self._pending_auto_counts().get(key, 0))

    def _set_pending_auto_count(self, key: str, count: int):
        """Persist the number of new auto memories accumulated for a key."""
        pending = self._pending_auto_counts()
        current = int(pending.get(key, 0))
        max_count = len(self.auto_memories.get(key, []))
        count = max(0, min(int(count), max_count))

        if count == 0:
            if key in pending:
                del pending[key]
                self._mark_dirty('state')
            return

        if current != count:
            pending[key] = count
            self._mark_dirty('state')

    def _increment_pending_auto_count(self, key: str, amount: int = 1):
        """Increase the persisted pending-auto counter for a key."""
        self._set_pending_auto_count(key, self._get_pending_auto_count(key) + amount)

    def _decrement_pending_auto_count(self, key: str, amount: int = 1):
        """Decrease the persisted pending-auto counter for a key."""
        self._set_pending_auto_count(key, self._get_pending_auto_count(key) - amount)

    def _sync_pending_auto_count_for_key(self, key: str):
        """Keep the legacy pending counter aligned with profile pending state."""
        if self._pending_index(key) is not None:
            self._set_pending_auto_count(key, 1)
        else:
            self._set_pending_auto_count(key, 0)

    def _log_embedding_unavailable_once(self):
        """Warn once when embedding support is unavailable."""
        if self._embedding_unavailable_logged:
            return
        log.warn(
            "Embeddings unavailable; semantic auto-memory dedup is disabled for this runtime. "
            "Using fingerprint/text dedup plus LLM consolidation instead."
        )
        self._embedding_unavailable_logged = True

    # ==========================================================================
    # KEY HELPERS
    # ==========================================================================

    @staticmethod
    def _auto_key(server_id: int | str, user_id: int) -> str:
        """Build a key for auto memories. server_id=0 means DM."""
        return auto_memory_key(server_id, user_id)

    @staticmethod
    def _server_lore_key(guild_id: int) -> str:
        return f"server:{guild_id}"

    @staticmethod
    def _user_lore_key(user_id: int) -> str:
        return f"user:{user_id}"

    @staticmethod
    def _bot_lore_key(bot_name: str) -> str:
        return f"bot:{bot_name}"

    def resolve_auto_memory_keys(
        self,
        user_ids: Optional[List[int]] = None,
        *,
        scope_mode: str = "all",
        server_id: int = None
    ) -> List[str]:
        """Resolve auto-memory keys matching the given user/scope filters."""
        try:
            normalized_user_ids = {
                int(user_id) for user_id in (user_ids or [])
                if int(user_id) > 0
            }
        except (TypeError, ValueError):
            normalized_user_ids = set()

        try:
            normalized_server_id = int(server_id) if server_id is not None else None
        except (TypeError, ValueError):
            normalized_server_id = None

        scope = (scope_mode or "all").strip().lower()
        if scope not in {"all", "server", "dm"}:
            scope = "all"

        matched_keys = []
        for key in self.auto_memories.keys():
            parsed_server_id, parsed_user_id = _parse_auto_key(key)
            if parsed_user_id is None:
                continue
            if normalized_user_ids and parsed_user_id not in normalized_user_ids:
                continue

            is_dm = key.startswith('dm:') or parsed_server_id in (None, 0)
            if scope == "dm" and not is_dm:
                continue
            if scope == "server":
                if is_dm:
                    continue
                if normalized_server_id is not None and parsed_server_id != normalized_server_id:
                    continue

            matched_keys.append(key)

        return sorted(matched_keys)

    def resolve_user_lore_keys(self, user_ids: Optional[List[int]] = None) -> List[str]:
        """Resolve user-lore keys matching the given user IDs."""
        try:
            normalized_user_ids = {
                int(user_id) for user_id in (user_ids or [])
                if int(user_id) > 0
            }
        except (TypeError, ValueError):
            normalized_user_ids = set()

        matched_keys = []
        for key in self.manual_lore.keys():
            if not key.startswith("user:"):
                continue
            if not normalized_user_ids:
                matched_keys.append(key)
                continue
            try:
                target_user_id = int(key.split(":", 1)[1])
            except (IndexError, ValueError):
                continue
            if target_user_id in normalized_user_ids:
                matched_keys.append(key)

        return sorted(matched_keys)

    def get_active_auto_user_targets(self) -> List[dict]:
        """List users that currently have at least one active auto-memory entry."""
        by_user: Dict[int, dict] = {}

        for key, entries in self.auto_memories.items():
            if not entries:
                continue

            _, parsed_user_id = _parse_auto_key(key)
            if parsed_user_id is None or parsed_user_id <= 0:
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    continue

                try:
                    user_id = int(entry.get("user_id", parsed_user_id))
                except (TypeError, ValueError):
                    user_id = parsed_user_id

                if user_id <= 0:
                    continue

                timestamp = entry.get("timestamp") or ""
                user_name = str(entry.get("user_name") or "").strip()
                current = by_user.get(user_id)

                if current is None:
                    by_user[user_id] = {
                        "user_id": user_id,
                        "user_name": user_name or f"User {user_id}",
                        "_named_timestamp": timestamp if user_name else "",
                    }
                    continue

                if user_name and timestamp >= current.get("_named_timestamp", ""):
                    current["user_name"] = user_name
                    current["_named_timestamp"] = timestamp

        targets = []
        for target in by_user.values():
            targets.append({
                "user_id": target["user_id"],
                "user_name": target["user_name"] or f"User {target['user_id']}",
            })

        return sorted(targets, key=lambda item: ((item.get("user_name") or "").lower(), item["user_id"]))

    def get_active_user_lore_targets(self) -> List[dict]:
        """List users that currently have at least one active user-lore entry."""
        from stats import stats_manager

        targets = []
        for key, entries in self.manual_lore.items():
            if not key.startswith("user:") or not entries:
                continue

            try:
                user_id = int(key.split(":", 1)[1])
            except (IndexError, TypeError, ValueError):
                continue

            user_name = stats_manager.get_user_name(user_id) or f"User {user_id}"
            targets.append({
                "user_id": user_id,
                "user_name": user_name,
            })

        return sorted(targets, key=lambda item: ((item.get("user_name") or "").lower(), item["user_id"]))

    @staticmethod
    def _entry_type(entry: dict) -> str:
        """Return a normalized auto-memory entry type."""
        if not isinstance(entry, dict):
            return LEGACY_ENTRY
        entry_type = entry.get("entry_type")
        if entry_type in {PROFILE_ENTRY, PENDING_ENTRY, LEGACY_ENTRY}:
            return entry_type
        return LEGACY_ENTRY

    def _entry_index(self, key: str, entry_type: str) -> Optional[int]:
        """Return the first index for an auto-memory entry type."""
        for idx, entry in enumerate(self.auto_memories.get(key, [])):
            if self._entry_type(entry) == entry_type:
                return idx
        return None

    def _profile_index(self, key: str) -> Optional[int]:
        return self._entry_index(key, PROFILE_ENTRY)

    def _pending_index(self, key: str) -> Optional[int]:
        return self._entry_index(key, PENDING_ENTRY)

    def _profile_entry(self, key: str) -> Optional[dict]:
        idx = self._profile_index(key)
        if idx is None:
            return None
        return self.auto_memories.get(key, [])[idx]

    def _pending_entry(self, key: str) -> Optional[dict]:
        idx = self._pending_index(key)
        if idx is None:
            return None
        return self.auto_memories.get(key, [])[idx]

    def auto_memory_key_needs_merge(self, key: str) -> bool:
        """Return whether a key is not yet a single clean profile entry."""
        entries = self.auto_memories.get(key, [])
        if not entries:
            return False

        profile_count = 0
        pending_count = 0
        legacy_count = 0
        for entry in entries:
            entry_type = self._entry_type(entry)
            if entry_type == PROFILE_ENTRY:
                profile_count += 1
            elif entry_type == PENDING_ENTRY:
                pending_count += 1
            else:
                legacy_count += 1

        return (
            pending_count > 0
            or legacy_count > 0
            or profile_count != 1
            or len(entries) != 1
        )

    def get_auto_memory_profile_keys_needing_merge(self) -> List[str]:
        """Return auto-memory keys queued for LLM profile consolidation."""
        return sorted(
            key for key in self.auto_memories.keys()
            if self.auto_memory_key_needs_merge(key)
        )

    def _entry_metadata(
        self,
        key: str,
        entries: Optional[List[dict]] = None,
        *,
        user_id=None,
        server_id=None,
        user_name: str = None,
        server_name: str = None,
        character_name: str = None,
    ) -> dict:
        """Resolve stable metadata for a rewritten profile entry."""
        entries = entries if entries is not None else self.auto_memories.get(key, [])
        parsed_server_id, parsed_user_id = _parse_auto_key(key)

        def _first_string(*field_names):
            for field_name in field_names:
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    value = entry.get(field_name)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            return None

        resolved_user_id = user_id if user_id is not None else None
        if resolved_user_id is None:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                value = entry.get("user_id")
                try:
                    resolved_user_id = int(value)
                    break
                except (TypeError, ValueError):
                    continue
        if resolved_user_id is None:
            resolved_user_id = parsed_user_id or 0

        resolved_server_id = _normalize_server_id_value(server_id, fallback=parsed_server_id)
        if resolved_server_id is None:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                resolved_server_id = _normalize_server_id_value(
                    entry.get("server_id"),
                    fallback=parsed_server_id,
                )
                if resolved_server_id is not None:
                    break
        if resolved_server_id is None:
            resolved_server_id = parsed_server_id or 0

        return {
            "user_id": int(resolved_user_id),
            "server_id": resolved_server_id,
            "user_name": user_name or _first_string("user_name"),
            "server_name": server_name or _first_string("server_name"),
            "character_name": character_name or _first_string("character", "learned_from"),
        }

    @staticmethod
    def _build_auto_memory_entry(
        content: str,
        *,
        user_id: int,
        server_id: int | str,
        user_name: str = None,
        server_name: str = None,
        character_name: str = None,
        embedding: list = None,
        timestamp: str = None,
        entry_type: str = PROFILE_ENTRY
    ) -> dict:
        """Build a normalized auto-memory entry."""
        normalized_server_id = _normalize_server_id_value(server_id, fallback=server_id)
        if normalized_server_id is None:
            normalized_server_id = 0
        if entry_type not in {PROFILE_ENTRY, PENDING_ENTRY, LEGACY_ENTRY}:
            entry_type = PROFILE_ENTRY
        entry = {
            "content": content,
            "timestamp": timestamp or datetime.now().isoformat(),
            "auto": True,
            "fingerprint": _memory_fingerprint(content),
            "user_id": int(user_id),
            "server_id": normalized_server_id,
            "entry_type": entry_type,
        }
        if user_name:
            entry["user_name"] = user_name
        if server_name:
            entry["server_name"] = server_name
        if character_name:
            entry["character"] = character_name
        if isinstance(embedding, list):
            entry["embedding"] = embedding
        return entry

    @staticmethod
    def _build_lore_entry(
        content: str,
        *,
        added_by: str = None,
        timestamp: str = None
    ) -> dict:
        """Build a normalized manual-lore entry."""
        entry = {
            "content": content,
            "timestamp": timestamp or datetime.now().isoformat(),
            "auto": False,
            "fingerprint": _memory_fingerprint(content),
        }
        if added_by:
            entry["added_by"] = added_by
        return entry

    # ==========================================================================
    # AUTO MEMORIES
    # ==========================================================================

    @staticmethod
    def _normalize_profile_text(content: str) -> str:
        """Normalize provider output into one editable profile string."""
        if not isinstance(content, str):
            return ""
        content = remove_thinking_tags(content).strip()
        if not content:
            return ""

        lines = []
        for line in content.splitlines():
            line = re.sub(r'^\s*(?:[-*]+|\d+[\.\)])\s*', '', line).strip()
            if line:
                lines.append(line)

        if not lines:
            return ""
        return _sanitize_memory_content("; ".join(lines))

    @staticmethod
    def _combine_pending_content(*contents: str) -> str:
        """Combine pending facts into one readable temporary entry."""
        parts = []
        for content in contents:
            if not isinstance(content, str) or not content.strip():
                continue
            for part in re.split(r'(?:\r?\n|;)\s*', content):
                cleaned = _sanitize_memory_content(part)
                if cleaned:
                    parts.append(cleaned)

        unique_parts = deduplicate_memory_strings(parts)
        return "; ".join(unique_parts)

    @staticmethod
    def _auto_entry_snapshot(entries: List[dict]) -> List[tuple]:
        """Return a compact snapshot used to detect concurrent key changes."""
        return [
            (
                MemoryManager._entry_type(entry),
                entry.get("fingerprint"),
                entry.get("content"),
                entry.get("timestamp"),
            )
            for entry in entries
            if isinstance(entry, dict)
        ]

    def _upsert_pending_entry(
        self,
        key: str,
        content: str,
        *,
        user_id: int = None,
        server_id: int | str = None,
        user_name: str = None,
        server_name: str = None,
        character_name: str = None,
        embedding: list = None,
    ) -> bool:
        """Create or update the single pending entry for a key."""
        content = _sanitize_memory_content(content)
        if not content:
            return False

        entries = self.auto_memories.setdefault(key, [])
        metadata = self._entry_metadata(
            key,
            entries,
            user_id=user_id,
            server_id=server_id,
            user_name=user_name,
            server_name=server_name,
            character_name=character_name,
        )

        pending_indices = [
            idx for idx, entry in enumerate(entries)
            if self._entry_type(entry) == PENDING_ENTRY
        ]
        pending_contents = [entries[idx].get("content", "") for idx in pending_indices]
        pending_content = self._combine_pending_content(*pending_contents, content)
        if not pending_content:
            return False

        pending_entry = self._build_auto_memory_entry(
            pending_content,
            user_id=metadata["user_id"],
            server_id=metadata["server_id"],
            user_name=metadata["user_name"],
            server_name=metadata["server_name"],
            character_name=metadata["character_name"],
            embedding=embedding if not pending_indices else None,
            entry_type=PENDING_ENTRY,
        )

        if pending_indices:
            entries[pending_indices[0]] = pending_entry
            for idx in reversed(pending_indices[1:]):
                entries.pop(idx)
        else:
            entries.append(pending_entry)

        self._mark_dirty('auto')
        self._sync_pending_auto_count_for_key(key)
        return True

    def add_auto_memory(self, server_id: int | str, user_id: int, content: str,
                        character_name: str = None, user_name: str = None,
                        server_name: str = None, embedding: list = None) -> bool:
        """Add an auto-generated memory using local profile/pending semantics.

        First memory for a key becomes the profile. Later sync additions update
        the single pending entry; async callers should use
        upsert_auto_memory_profile() to merge immediately through a provider.
        """
        content = _sanitize_memory_content(content)
        if not content:
            return False

        key = self._auto_key(server_id, user_id)
        entries = self.auto_memories.setdefault(key, [])

        if _is_duplicate_memory(content, entries, new_embedding=embedding):
            log.debug(f"Skipping duplicate auto memory: {content[:50]}...")
            return False

        if not entries:
            self.auto_memories[key] = [self._build_auto_memory_entry(
                content,
                user_id=user_id,
                server_id=server_id,
                user_name=user_name,
                server_name=server_name,
                character_name=character_name,
                embedding=embedding,
                entry_type=PROFILE_ENTRY,
            )]
            self._mark_dirty('auto')
            self._set_pending_auto_count(key, 0)
            return True

        return self._upsert_pending_entry(
            key,
            content,
            user_id=user_id,
            server_id=server_id,
            user_name=user_name,
            server_name=server_name,
            character_name=character_name,
            embedding=embedding,
        )

    async def upsert_auto_memory_profile(
        self,
        server_id: int | str,
        user_id: int,
        content: str,
        provider_manager,
        *,
        character_name: str = None,
        user_name: str = None,
        server_name: str = None,
        embedding: list = None,
    ) -> bool:
        """Merge a newly learned fact into the one living profile for its key."""
        content = _sanitize_memory_content(content)
        if not content:
            return False

        key = self._auto_key(server_id, user_id)
        entries = self.auto_memories.setdefault(key, [])
        if _is_duplicate_memory(content, entries, new_embedding=embedding):
            log.debug(f"Skipping duplicate auto memory: {content[:50]}...")
            return False

        if not entries:
            self.auto_memories[key] = [self._build_auto_memory_entry(
                content,
                user_id=user_id,
                server_id=server_id,
                user_name=user_name,
                server_name=server_name,
                character_name=character_name,
                embedding=embedding,
                entry_type=PROFILE_ENTRY,
            )]
            self._mark_dirty('auto')
            self._set_pending_auto_count(key, 0)
            return True

        new_fact_entry = self._build_auto_memory_entry(
            content,
            user_id=user_id,
            server_id=server_id,
            user_name=user_name,
            server_name=server_name,
            character_name=character_name,
            embedding=embedding,
            entry_type=PENDING_ENTRY,
        )
        status = await self._merge_auto_memory_profile(
            key,
            provider_manager,
            new_fact_entry=new_fact_entry,
            metadata_overrides={
                "user_id": user_id,
                "server_id": server_id,
                "user_name": user_name,
                "server_name": server_name,
                "character_name": character_name,
            },
        )
        if status == "consolidated":
            return True

        return self._upsert_pending_entry(
            key,
            content,
            user_id=user_id,
            server_id=server_id,
            user_name=user_name,
            server_name=server_name,
            character_name=character_name,
            embedding=embedding,
        )

    def get_auto_memories(self, server_id: int | str, user_id: int, limit: int = 10) -> str:
        """Get formatted auto memories for a user in a server/DM."""
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 10
        key = self._auto_key(server_id, user_id)
        entries = self.auto_memories.get(key, [])
        if not entries:
            return ""

        profile_entries = [entry for entry in entries if self._entry_type(entry) == PROFILE_ENTRY]
        pending_entries = [entry for entry in entries if self._entry_type(entry) == PENDING_ENTRY]
        legacy_entries = [entry for entry in entries if self._entry_type(entry) == LEGACY_ENTRY]

        visible_entries = []
        if profile_entries:
            visible_entries.append((PROFILE_ENTRY, profile_entries[0]))
            legacy_limit = max(limit - 1, 0)
            visible_entries.extend((LEGACY_ENTRY, entry) for entry in legacy_entries[-legacy_limit:])
        else:
            visible_entries.extend((LEGACY_ENTRY, entry) for entry in legacy_entries[-limit:])
        visible_entries.extend((PENDING_ENTRY, entry) for entry in pending_entries[:1])

        lines = []
        for entry_type, entry in visible_entries:
            content = remove_thinking_tags(entry.get('content', '')).strip()
            if not content:
                continue
            prefix = "Pending merge: " if entry_type == PENDING_ENTRY else ""
            lines.append(f"- {prefix}{content}")
        return "\n".join(lines)

    def get_all_memories_for_context(self, server_id: int | str, user_id: int, user_name: str = "") -> str:
        """Get all relevant memories for LLM context — combines auto memories and lore."""
        parts = []

        # Auto memories for this user+server
        auto = self.get_auto_memories(server_id, user_id)
        if auto:
            parts.append(f"What you know about {user_name or 'this user'}:\n{auto}")

        # User lore
        user_lore = self.get_user_lore(user_id)
        if user_lore:
            parts.append(f"Lore about {user_name or 'this user'}:\n{user_lore}")

        # Deduplicate across combined lines
        if len(parts) > 1:
            all_lines = "\n".join(parts).split("\n")
            content_lines = [l for l in all_lines if l.startswith("- ")]
            header_lines = [l for l in all_lines if not l.startswith("- ")]
            deduped = deduplicate_memory_strings(content_lines)
            return "\n".join(header_lines[:1]) + "\n" + "\n".join(deduped) if deduped else ""

        return "\n".join(parts) if parts else ""

    def delete_auto_memories(self, key: str, indices: List[int]):
        """Delete specific auto memories by index (descending order safe)."""
        if key not in self.auto_memories:
            return 0
        removed = 0
        for idx in sorted(indices, reverse=True):
            if 0 <= idx < len(self.auto_memories[key]):
                self.auto_memories[key].pop(idx)
                removed += 1
        if not self.auto_memories[key]:
            del self.auto_memories[key]
            self._set_pending_auto_count(key, 0)
        elif removed:
            self._sync_pending_auto_count_for_key(key)
        if removed:
            self._mark_dirty('auto')
        return removed

    def clear_auto_memories(self, key: str):
        """Clear all auto memories for a key."""
        if key in self.auto_memories:
            removed = len(self.auto_memories[key])
            del self.auto_memories[key]
            self._set_pending_auto_count(key, 0)
            self._mark_dirty('auto')
            return removed
        return 0

    def update_auto_memory(self, key: str, index: int, content: str, character_name: str = None) -> bool:
        """Update an existing auto memory without changing its source classification."""
        entries = self.auto_memories.get(key, [])
        if not (0 <= index < len(entries)):
            return False

        content = _sanitize_memory_content(content)
        if not content:
            return False

        existing = entries[index]
        other_entries = entries[:index] + entries[index + 1:]
        if _is_duplicate_memory(content, other_entries):
            return False

        existing["content"] = content
        existing["timestamp"] = datetime.now().isoformat()
        existing["auto"] = True
        existing["fingerprint"] = _memory_fingerprint(content)
        existing["entry_type"] = self._entry_type(existing)
        existing.pop("embedding", None)

        if character_name:
            existing["character"] = character_name
        else:
            existing.pop("character", None)

        self._mark_dirty('auto')
        self._sync_pending_auto_count_for_key(key)
        return True

    def bulk_delete_auto_memories(
        self,
        user_ids: Optional[List[int]] = None,
        *,
        scope_mode: str = "all",
        server_id: int = None
    ) -> dict:
        """Delete all auto memories matching one or more users and scope filters."""
        keys = self.resolve_auto_memory_keys(user_ids, scope_mode=scope_mode, server_id=server_id)
        deleted = 0
        for key in keys:
            deleted += self.clear_auto_memories(key)

        return {
            "affected_keys": len(keys),
            "deleted": deleted,
        }

    async def consolidate_auto_memory_keys(self, keys: List[str], provider_manager) -> dict:
        """Manually consolidate one or more auto-memory keys in sequence."""
        unique_keys = []
        seen_keys = set()
        for key in keys or []:
            if not isinstance(key, str) or key not in self.auto_memories or key in seen_keys:
                continue
            unique_keys.append(key)
            seen_keys.add(key)

        result = {
            "matched_keys": len(unique_keys),
            "consolidated": 0,
            "skipped": 0,
            "already_running": 0,
            "failed": 0,
        }

        if not unique_keys:
            return result

        for key in unique_keys:
            status = await self.llm_deduplicate(key, provider_manager)
            if status == "consolidated":
                result["consolidated"] += 1
            elif status == "already_running":
                result["already_running"] += 1
            elif status == "failed":
                result["failed"] += 1
            else:
                result["skipped"] += 1

        return result

    async def consolidate_auto_memories(
        self,
        user_ids: Optional[List[int]],
        *,
        scope_mode: str = "all",
        server_id: int = None,
        provider_manager,
    ) -> dict:
        """Resolve keys for one or more users, then consolidate each key sequentially."""
        keys = self.resolve_auto_memory_keys(user_ids, scope_mode=scope_mode, server_id=server_id)
        return await self.consolidate_auto_memory_keys(keys, provider_manager)

    async def retry_pending_auto_profiles(self, provider_manager) -> dict:
        """Retry all profile keys that still need LLM consolidation."""
        return await self.consolidate_auto_memory_keys(
            self.get_auto_memory_profile_keys_needing_merge(),
            provider_manager,
        )

    # ==========================================================================
    # MANUAL LORE
    # ==========================================================================

    def add_lore(self, target_type: str, target_id, content: str, added_by: str = None) -> bool:
        """Add manual lore. target_type: 'user', 'bot', or 'server'."""
        content = _sanitize_memory_content(content)
        if not content:
            return False

        if target_type == "user":
            key = self._user_lore_key(int(target_id))
        elif target_type == "bot":
            key = self._bot_lore_key(str(target_id))
        elif target_type == "server":
            key = self._server_lore_key(int(target_id))
        else:
            return False

        if key not in self.manual_lore:
            self.manual_lore[key] = []

        # Check for duplicates
        if _is_duplicate_memory(content, self.manual_lore[key]):
            log.debug(f"Skipping duplicate lore: {content[:50]}...")
            return False

        self.manual_lore[key].append(self._build_lore_entry(content, added_by=added_by))
        self._mark_dirty('lore')
        return True

    def get_server_lore(self, guild_id: int) -> str:
        """Get server lore."""
        key = self._server_lore_key(guild_id)
        entries = self.manual_lore.get(key, [])
        if not entries:
            return ""
        return "\n".join([f"- {e['content']}" for e in entries])

    def get_user_lore(self, user_id: int) -> str:
        """Get user lore."""
        key = self._user_lore_key(user_id)
        entries = self.manual_lore.get(key, [])
        if not entries:
            return ""
        return "\n".join([f"- {e['content']}" for e in entries])

    def get_bot_lore(self, bot_name: str) -> str:
        """Get bot lore."""
        key = self._bot_lore_key(bot_name)
        entries = self.manual_lore.get(key, [])
        if not entries:
            return ""
        return "\n".join([f"- {e['content']}" for e in entries])

    def delete_lore(self, key: str, indices: List[int]):
        """Delete specific lore entries by index."""
        if key not in self.manual_lore:
            return 0
        removed = 0
        for idx in sorted(indices, reverse=True):
            if 0 <= idx < len(self.manual_lore[key]):
                self.manual_lore[key].pop(idx)
                removed += 1
        if not self.manual_lore[key]:
            del self.manual_lore[key]
        if removed:
            self._mark_dirty('lore')
        return removed

    def clear_lore(self, key: str):
        """Clear all lore for a key."""
        if key in self.manual_lore:
            removed = len(self.manual_lore[key])
            del self.manual_lore[key]
            self._mark_dirty('lore')
            return removed
        return 0

    def bulk_delete_user_lore(self, user_ids: Optional[List[int]] = None) -> dict:
        """Delete user-scoped lore for one or more target users."""
        keys = self.resolve_user_lore_keys(user_ids)
        deleted = 0
        for key in keys:
            deleted += self.clear_lore(key)

        return {
            "affected_keys": len(keys),
            "deleted": deleted,
        }

    # Legacy compatibility aliases
    def add_server_memory(self, guild_id: int, content: str, auto: bool = False, embedding: list = None) -> bool:
        """Legacy compat: add server memory → auto memory with server_id, user_id=0."""
        return self.add_auto_memory(guild_id, 0, content, embedding=embedding)

    def get_server_memories(self, guild_id: int, limit: int = 10) -> str:
        """Legacy compat: get server memories → auto memories with user_id=0."""
        return self.get_auto_memories(guild_id, 0, limit=limit)

    def get_lore(self, guild_id: int) -> str:
        """Legacy compat: get_lore → get_server_lore."""
        return self.get_server_lore(guild_id)

    def get_dm_memories(self, user_id: int, limit: int = 10, character_name: str = None) -> str:
        """Legacy compat: DM memories → auto memories with server_id=0."""
        return self.get_auto_memories(0, user_id, limit=limit)

    def get_user_memories(self, guild_id: int, user_id: int, limit: int = 5, character_name: str = None) -> str:
        """Legacy compat: user memories → auto memories."""
        return self.get_auto_memories(guild_id, user_id, limit=limit)

    def get_global_user_profile(self, user_id: int, limit: int = 5) -> str:
        """Legacy compat: global profile → user lore + auto memories across servers."""
        return self.get_user_lore(user_id)

    # ==========================================================================
    # LLM DEDUPLICATION
    # ==========================================================================

    def should_llm_deduplicate(self, key: str) -> bool:
        """Check if a key needs profile consolidation and is available to merge."""
        return self.auto_memory_key_needs_merge(key) and key not in self._dedup_in_flight

    def _build_profile_merge_prompt(
        self,
        key: str,
        entries: List[dict],
        *,
        new_fact_entry: dict = None,
    ) -> str:
        """Build the LLM prompt that rewrites entries into one profile."""
        metadata = self._entry_metadata(key, entries + ([new_fact_entry] if new_fact_entry else []))
        user_name = metadata.get("user_name") or f"User {metadata.get('user_id')}"

        profile_lines = []
        pending_lines = []
        legacy_lines = []
        for entry in entries:
            content = remove_thinking_tags(entry.get("content", "")).strip()
            if not content:
                continue
            entry_type = self._entry_type(entry)
            if entry_type == PROFILE_ENTRY:
                profile_lines.append(content)
            elif entry_type == PENDING_ENTRY:
                pending_lines.append(content)
            else:
                legacy_lines.append(content)

        if new_fact_entry:
            new_fact = remove_thinking_tags(new_fact_entry.get("content", "")).strip()
        else:
            new_fact = ""

        sections = []
        if profile_lines:
            sections.append("Current profile:\n" + "\n".join(f"- {line}" for line in profile_lines))
        if legacy_lines:
            sections.append("Legacy memories to preserve:\n" + "\n".join(f"- {line}" for line in legacy_lines))
        if pending_lines:
            sections.append("Pending facts waiting for merge:\n" + "\n".join(f"- {line}" for line in pending_lines))
        if new_fact:
            sections.append("New fact to merge now:\n" + f"- {new_fact}")

        memory_text = "\n\n".join(sections) if sections else "No stored facts."

        return f"""Rewrite the stored memories below into one living editable memory profile about {user_name}.

Preserve every durable, non-duplicate fact. Merge overlaps. Remove exact duplicates and throwaway wording.
Write one concise profile in plain text. Do not include numbering, explanations, JSON, headings, or metadata.

{memory_text}

Final memory profile:"""

    async def _merge_auto_memory_profile(
        self,
        key: str,
        provider_manager,
        *,
        new_fact_entry: dict = None,
        metadata_overrides: dict = None,
    ) -> str:
        """Use the provider to rewrite a key into exactly one profile entry."""
        if key in self._dedup_in_flight:
            log.debug(f"LLM profile merge already in flight for {key}")
            return "already_running"

        entries = list(self.auto_memories.get(key, []))
        if not entries and not new_fact_entry:
            return "skipped"
        if not new_fact_entry and not self.auto_memory_key_needs_merge(key):
            self._set_pending_auto_count(key, 0)
            return "skipped"

        self._dedup_in_flight.add(key)
        memory_snapshot = self._auto_entry_snapshot(entries)

        try:
            prompt = self._build_profile_merge_prompt(key, entries, new_fact_entry=new_fact_entry)
            result = await provider_manager.generate(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=(
                    "You are a memory consolidation assistant. "
                    "Return only one concise editable memory profile."
                )
            )

            if not result or result.startswith("❌"):
                return "failed"

            profile_text = self._normalize_profile_text(remove_thinking_tags(result))
            if not profile_text:
                return "failed"

            embedding = await provider_manager.get_embedding(profile_text)
            if embedding is None:
                self._log_embedding_unavailable_once()
                embedding = None
            elif not isinstance(embedding, list):
                embedding = None

            all_entries = entries + ([new_fact_entry] if new_fact_entry else [])
            overrides = metadata_overrides or {}
            metadata = self._entry_metadata(
                key,
                all_entries,
                user_id=overrides.get("user_id"),
                server_id=overrides.get("server_id"),
                user_name=overrides.get("user_name"),
                server_name=overrides.get("server_name"),
                character_name=overrides.get("character_name"),
            )
            profile_entry = self._build_auto_memory_entry(
                profile_text,
                user_id=metadata["user_id"],
                server_id=metadata["server_id"],
                user_name=metadata["user_name"],
                server_name=metadata["server_name"],
                character_name=metadata["character_name"] or "consolidated",
                embedding=embedding,
                entry_type=PROFILE_ENTRY,
            )

            current_snapshot = self._auto_entry_snapshot(self.auto_memories.get(key, []))
            if current_snapshot != memory_snapshot:
                log.debug(f"Skipping stale LLM profile merge result for {key}")
                return "skipped"

            before_count = len(entries)
            self.auto_memories[key] = [profile_entry]
            self._mark_dirty('auto')
            self._set_pending_auto_count(key, 0)
            log.info(f"LLM profile merge for {key}: {before_count} -> 1 profile")
            return "consolidated"

        except Exception as e:
            log.warn(f"LLM profile merge failed for {key}: {e}")
            return "failed"
        finally:
            self._dedup_in_flight.discard(key)

    async def llm_deduplicate(self, key: str, provider_manager):
        """Use LLM to consolidate a key into one editable profile entry."""
        return await self._merge_auto_memory_profile(key, provider_manager)

    # ==========================================================================
    # MEMORY GENERATION
    # ==========================================================================

    async def generate_memory(
        self,
        provider_manager,
        messages: List[dict],
        is_dm: bool,
        id_key: int | str,
        character_name: str = "the character",
        user_id: int = None,
        user_name: str = None
    ) -> Optional[str]:
        """Generate a memory summary from conversation using AI."""
        if len(messages) < 5:
            return None

        # Global channel-level cooldown
        channel_key = id_key
        now = time.time()
        last_gen = self._channel_memory_cooldown.get(channel_key, 0)
        if now - last_gen < self._MEMORY_COOLDOWN_SECONDS:
            log.debug(f"Memory generation skipped - channel cooldown ({now - last_gen:.0f}s < {self._MEMORY_COOLDOWN_SECONDS}s)")
            return None
        self._channel_memory_cooldown[channel_key] = now

        # Build context with explicit user attribution
        context_lines = []
        for m in messages[-20:]:
            author = m.get('author', 'Unknown')
            role = m.get('role', 'user')
            content = m.get('content', '')[:200]
            if role == 'assistant':
                context_lines.append(f"[{character_name}]: {content}")
            else:
                context_lines.append(f"[{author}]: {content}")
        context = "\n".join(context_lines)

        target_user = user_name if user_name else "the user"

        prompt = f"""Analyze this conversation and extract anything worth remembering about {target_user}.

CRITICAL RULES - READ CAREFULLY:
1. Only save facts that {target_user} DIRECTLY STATED about THEMSELVES in their own messages
2. Do NOT save things OTHER users said about {target_user} - those could be jokes, teasing, or wrong
3. Look for messages from [{target_user}] only - ignore what others say about them
4. The memory must be verifiable from {target_user}'s own words in the conversation

SAVE memories about (only if {target_user} said it themselves):
- Personal facts they shared (job, hobbies, pets, relationships, location)
- Preferences and opinions they expressed (likes, dislikes, favorites)
- Life events or experiences they mentioned
- Plans or commitments they made

NEVER SAVE:
- Things other users claimed about {target_user} (e.g., "User2 said {target_user} has a boyfriend")
- Jokes, teasing, or roleplay between users
- Generic greetings or short exchanges
- Anything {target_user} didn't explicitly say themselves

If nothing memorable that {target_user} PERSONALLY STATED, respond with just "NOTHING".
Otherwise, write ONE concise sentence starting with "{target_user}" (use their actual name).

Example: "{target_user} mentioned they work as a nurse"
Example: "{target_user} said they have two cats"

Conversation:
{context}

Memory about {target_user} (or NOTHING):"""

        try:
            result = await provider_manager.generate(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a memory analyzer. Create brief, factual summaries."
            )

            if result and "NOTHING" not in result.upper() and not result.startswith("❌"):
                result = remove_thinking_tags(result)

                # Validate memory is about the correct user
                if user_name and user_name.lower() not in result.lower():
                    log.debug(f"Rejected memory - doesn't mention target user {user_name}: {result[:100]}")
                    return None

                # Generate embedding for semantic dedup
                embedding = await provider_manager.get_embedding(result.strip())
                if embedding is None:
                    self._log_embedding_unavailable_once()

                # Determine server_id and server_name
                server_id = id_key
                server_name = None  # Will be populated by caller if available

                # Store in unified auto memory profile.
                added = await self.upsert_auto_memory_profile(
                    server_id=server_id,
                    user_id=user_id or id_key,
                    content=result.strip(),
                    provider_manager=provider_manager,
                    character_name=character_name,
                    user_name=user_name,
                    server_name=server_name,
                    embedding=embedding
                )

                return result.strip()
        except Exception as e:
            log.warn(f"Memory generation failed: {e}")

        return None

    # ==========================================================================
    # MIGRATION FROM LEGACY 5-STORE SYSTEM
    # ==========================================================================

    def _migrate_if_needed(self):
        """Check for legacy memory files and migrate to the new unified system."""
        # Skip if new files already exist
        if os.path.exists(AUTO_MEMORIES_FILE) or os.path.exists(MANUAL_LORE_FILE):
            return

        # Check if any legacy files exist
        legacy_files = [MEMORIES_FILE, LORE_FILE, GLOBAL_USER_PROFILES_FILE,
                        DM_MEMORIES_FILE, USER_MEMORIES_FILE]
        has_legacy = any(os.path.exists(f) for f in legacy_files)
        has_legacy_dirs = (os.path.exists(DM_MEMORIES_DIR) and os.listdir(DM_MEMORIES_DIR)) or \
                          (os.path.exists(USER_MEMORIES_DIR) and os.listdir(USER_MEMORIES_DIR))

        if not has_legacy and not has_legacy_dirs:
            return

        log.info("Migrating legacy memory files to unified system...")
        auto_memories = {}
        manual_lore = {}
        stats = {"migrated": 0, "skipped_dupes": 0}

        def _add_auto(key, entry):
            if key not in auto_memories:
                auto_memories[key] = []
            content = entry.get("content", "")
            if not content:
                return
            if _is_duplicate_memory(content, auto_memories[key]):
                stats["skipped_dupes"] += 1
                return
            server_id, user_id = _parse_auto_key(key)
            auto_memories[key].append(self._build_auto_memory_entry(
                content,
                user_id=user_id or 0,
                server_id=server_id or 0,
                user_name=entry.get("user_name"),
                server_name=entry.get("server_name"),
                character_name=entry.get("character") or entry.get("learned_from"),
                embedding=entry.get("embedding") if isinstance(entry.get("embedding"), list) else None,
                timestamp=entry.get("timestamp", datetime.now().isoformat()),
                entry_type=LEGACY_ENTRY,
            ))
            stats["migrated"] += 1

        # 1. Server memories (memories.json) → auto_memories keyed by server:X:user:0
        if os.path.exists(MEMORIES_FILE):
            data = safe_json_load(MEMORIES_FILE)
            for guild_id, entries in data.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict):
                        subj = entry.get("subject_user_id")
                        uid = int(subj) if subj else 0
                        key = f"server:{guild_id}:user:{uid}"
                        _add_auto(key, entry)

        # 2. Lore (lore.json) → manual_lore keyed by server:X
        if os.path.exists(LORE_FILE):
            data = safe_json_load(LORE_FILE)
            for guild_id, lore_text in data.items():
                if not isinstance(lore_text, str) or not lore_text.strip():
                    continue
                key = f"server:{guild_id}"
                manual_lore[key] = [self._build_lore_entry(
                    lore_text.strip(),
                    added_by="migrated"
                )]
                stats["migrated"] += 1

        # 3. Global user profiles → auto_memories (merged across servers)
        if os.path.exists(GLOBAL_USER_PROFILES_FILE):
            data = safe_json_load(GLOBAL_USER_PROFILES_FILE)
            for user_id, entries in data.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict):
                        key = f"dm:0:user:{user_id}"
                        _add_auto(key, entry)

        # 4. Per-character DM memories
        if os.path.exists(DM_MEMORIES_DIR):
            for filepath in glob.glob(os.path.join(DM_MEMORIES_DIR, "*.json")):
                data = safe_json_load(filepath)
                for user_id, entries in data.items():
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if isinstance(entry, dict):
                            key = f"dm:0:user:{user_id}"
                            _add_auto(key, entry)

        # 5. Legacy shared DM memories
        if os.path.exists(DM_MEMORIES_FILE):
            data = safe_json_load(DM_MEMORIES_FILE)
            for user_id, entries in data.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict):
                        key = f"dm:0:user:{user_id}"
                        _add_auto(key, entry)

        # 6. Per-character user memories
        if os.path.exists(USER_MEMORIES_DIR):
            for filepath in glob.glob(os.path.join(USER_MEMORIES_DIR, "*.json")):
                data = safe_json_load(filepath)
                for guild_id, guild_data in data.items():
                    if not isinstance(guild_data, dict):
                        continue
                    for user_id, entries in guild_data.items():
                        if not isinstance(entries, list):
                            continue
                        for entry in entries:
                            if isinstance(entry, dict):
                                key = f"server:{guild_id}:user:{user_id}"
                                _add_auto(key, entry)

        # 7. Legacy shared user memories
        if os.path.exists(USER_MEMORIES_FILE):
            data = safe_json_load(USER_MEMORIES_FILE)
            for guild_id, guild_data in data.items():
                if not isinstance(guild_data, dict):
                    continue
                for user_id, entries in guild_data.items():
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if isinstance(entry, dict):
                            key = f"server:{guild_id}:user:{user_id}"
                            _add_auto(key, entry)

        # Save new files
        safe_json_save(AUTO_MEMORIES_FILE, auto_memories)
        safe_json_save(MANUAL_LORE_FILE, manual_lore)

        # Rename old files to .bak
        for f in legacy_files:
            if os.path.exists(f):
                try:
                    os.rename(f, f + ".bak")
                except Exception as e:
                    log.warn(f"Could not rename {f} to .bak: {e}")

        # Rename legacy directories
        for d in [DM_MEMORIES_DIR, USER_MEMORIES_DIR]:
            if os.path.exists(d):
                try:
                    os.rename(d, d + "_bak")
                except Exception as e:
                    log.warn(f"Could not rename {d} to _bak: {e}")

        log.ok(f"Migration complete: {stats['migrated']} memories migrated, {stats['skipped_dupes']} duplicates skipped")


# Global instance
memory_manager = MemoryManager()

