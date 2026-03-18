"""
Discord Pals - Memory System
Unified 2-store memory system: auto memories + manual lore.
With debounced saving, LLM deduplication, and legacy migration.
"""

import os
import re
import time
import glob
import difflib
import hashlib
from typing import Dict, List, Optional
from datetime import datetime
from config import (
    DATA_DIR, MEMORIES_FILE, DM_MEMORIES_FILE, USER_MEMORIES_FILE,
    LORE_FILE, DM_MEMORIES_DIR, USER_MEMORIES_DIR, GLOBAL_USER_PROFILES_FILE,
    AUTO_MEMORIES_FILE, MANUAL_LORE_FILE
)
from discord_utils import safe_json_load, safe_json_save, remove_thinking_tags
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


def _sanitize_memory_entries(entries: list) -> list:
    """Drop malformed entries and normalize content/fingerprint fields."""
    if not isinstance(entries, list):
        return []

    cleaned_entries = []
    seen_fingerprints = set()
    for entry in entries:
        if not _memory_entry_is_valid(entry):
            continue

        content = _sanitize_memory_content(entry.get('content', ''))
        if not content:
            continue

        fingerprint = entry.get('fingerprint')
        if not isinstance(fingerprint, str) or not fingerprint:
            fingerprint = _memory_fingerprint(content)

        # Skip duplicates within this list
        if fingerprint and fingerprint in seen_fingerprints:
            continue
        if fingerprint:
            seen_fingerprints.add(fingerprint)

        cleaned = {
            "content": content,
            "timestamp": entry.get("timestamp") or datetime.now().isoformat(),
            "auto": bool(entry.get("auto", False)),
            "fingerprint": fingerprint
        }

        # Preserve optional metadata when valid
        for key in ("character", "user_name", "learned_from"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                cleaned[key] = value
        subject_user_id = entry.get("subject_user_id")
        try:
            subject_user_id = int(subject_user_id)
        except (TypeError, ValueError):
            subject_user_id = None
        if subject_user_id and subject_user_id > 0:
            cleaned["subject_user_id"] = subject_user_id
        if isinstance(entry.get("embedding"), list):
            cleaned["embedding"] = entry["embedding"]

        cleaned_entries.append(cleaned)

    return cleaned_entries


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
      - Keyed by "server:{guild_id}:user:{user_id}" or "dm:0:user:{user_id}"
      - Auto-generated from conversations, with LLM deduplication every N memories

    Store 2: Manual Lore (manual_lore.json)
      - Keyed by "user:{user_id}", "bot:{bot_name}", or "server:{guild_id}"
      - Manually inserted via dashboard or commands

    Includes migration from the legacy 5-store system.
    """

    # LLM dedup trigger threshold
    _LLM_DEDUP_EVERY = 5

    def __init__(self):
        ensure_data_dir()

        # Migrate legacy stores if needed (before loading new stores)
        self._migrate_if_needed()

        # New unified stores
        self.auto_memories: Dict[str, List[dict]] = safe_json_load(AUTO_MEMORIES_FILE)
        self.manual_lore: Dict[str, List[dict]] = safe_json_load(MANUAL_LORE_FILE)

        # LLM dedup counter per key
        self._memory_creation_counter: Dict[str, int] = {}

        # Debounce tracking
        self._dirty_files: set = set()
        self._last_save = time.time()

        # Global channel-level memory generation cooldown (prevents multi-bot duplication)
        self._channel_memory_cooldown: Dict[int, float] = {}
        self._MEMORY_COOLDOWN_SECONDS = 30

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
        self._dirty_files.clear()
        self._last_save = time.time()

    # ==========================================================================
    # KEY HELPERS
    # ==========================================================================

    @staticmethod
    def _auto_key(server_id: int, user_id: int) -> str:
        """Build a key for auto memories. server_id=0 means DM."""
        if server_id and server_id != 0:
            return f"server:{server_id}:user:{user_id}"
        return f"dm:0:user:{user_id}"

    @staticmethod
    def _server_lore_key(guild_id: int) -> str:
        return f"server:{guild_id}"

    @staticmethod
    def _user_lore_key(user_id: int) -> str:
        return f"user:{user_id}"

    @staticmethod
    def _bot_lore_key(bot_name: str) -> str:
        return f"bot:{bot_name}"

    # ==========================================================================
    # AUTO MEMORIES
    # ==========================================================================

    def add_auto_memory(self, server_id: int, user_id: int, content: str,
                        character_name: str = None, user_name: str = None,
                        server_name: str = None, embedding: list = None) -> bool:
        """Add an auto-generated memory.

        Returns True if memory was added, False if duplicate detected.
        """
        content = _sanitize_memory_content(content)
        if not content:
            return False

        key = self._auto_key(server_id, user_id)
        if key not in self.auto_memories:
            self.auto_memories[key] = []

        # Check for duplicates
        if _is_duplicate_memory(content, self.auto_memories[key], new_embedding=embedding):
            log.debug(f"Skipping duplicate auto memory: {content[:50]}...")
            return False

        memory = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "fingerprint": _memory_fingerprint(content),
            "user_id": user_id,
            "server_id": server_id,
        }
        if user_name:
            memory["user_name"] = user_name
        if server_name:
            memory["server_name"] = server_name
        if character_name:
            memory["character"] = character_name
        if embedding:
            memory["embedding"] = embedding

        self.auto_memories[key].append(memory)

        # Cap at 50 memories per key
        if len(self.auto_memories[key]) > 50:
            self.auto_memories[key] = self.auto_memories[key][-50:]

        self._mark_dirty('auto')

        # Track creation count for LLM dedup
        self._memory_creation_counter[key] = self._memory_creation_counter.get(key, 0) + 1
        return True

    def get_auto_memories(self, server_id: int, user_id: int, limit: int = 10) -> str:
        """Get formatted auto memories for a user in a server/DM."""
        key = self._auto_key(server_id, user_id)
        memories = self.auto_memories.get(key, [])[-limit:]
        if not memories:
            return ""
        return "\n".join([f"- {remove_thinking_tags(m['content'])}" for m in memories])

    def get_all_memories_for_context(self, server_id: int, user_id: int, user_name: str = "") -> str:
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
            return
        for idx in sorted(indices, reverse=True):
            if 0 <= idx < len(self.auto_memories[key]):
                self.auto_memories[key].pop(idx)
        if not self.auto_memories[key]:
            del self.auto_memories[key]
        self._mark_dirty('auto')

    def clear_auto_memories(self, key: str):
        """Clear all auto memories for a key."""
        if key in self.auto_memories:
            del self.auto_memories[key]
            self._mark_dirty('auto')

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

        entry = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "fingerprint": _memory_fingerprint(content),
        }
        if added_by:
            entry["added_by"] = added_by

        self.manual_lore[key].append(entry)
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
            return
        for idx in sorted(indices, reverse=True):
            if 0 <= idx < len(self.manual_lore[key]):
                self.manual_lore[key].pop(idx)
        if not self.manual_lore[key]:
            del self.manual_lore[key]
        self._mark_dirty('lore')

    def clear_lore(self, key: str):
        """Clear all lore for a key."""
        if key in self.manual_lore:
            del self.manual_lore[key]
            self._mark_dirty('lore')

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
        """Check if a key has accumulated enough new memories to trigger LLM dedup."""
        count = self._memory_creation_counter.get(key, 0)
        return count >= self._LLM_DEDUP_EVERY

    async def llm_deduplicate(self, key: str, provider_manager):
        """Use LLM to consolidate and deduplicate memories for a key."""
        memories = self.auto_memories.get(key, [])
        if len(memories) < 3:
            return

        # Build context
        memory_lines = [f"{i+1}. {m['content']}" for i, m in enumerate(memories)]
        memory_text = "\n".join(memory_lines)

        user_name = memories[0].get('user_name', 'the user') if memories else 'the user'

        prompt = f"""Here are {len(memories)} stored memories about {user_name}. Some may be duplicates or redundant.

Consolidate them into a clean, deduplicated list. Merge similar facts. Remove exact duplicates.
Keep all unique information. Return one memory per line, no numbering.

Memories:
{memory_text}

Deduplicated memories (one per line):"""

        try:
            result = await provider_manager.generate(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a memory consolidation assistant. Return only the deduplicated list, one per line."
            )

            if result and not result.startswith("❌"):
                result = remove_thinking_tags(result)
                new_lines = [line.strip() for line in result.strip().split("\n") if line.strip()]
                # Rebuild memory entries
                new_memories = []
                for line in new_lines:
                    # Remove leading "- " or numbering
                    line = re.sub(r'^[\-\d\.\)]+\s*', '', line).strip()
                    if not line:
                        continue
                    new_memories.append({
                        "content": line,
                        "timestamp": datetime.now().isoformat(),
                        "fingerprint": _memory_fingerprint(line),
                        "user_id": memories[0].get("user_id"),
                        "server_id": memories[0].get("server_id"),
                        "user_name": memories[0].get("user_name"),
                        "server_name": memories[0].get("server_name"),
                        "character": "consolidated",
                    })

                if new_memories:
                    before_count = len(memories)
                    self.auto_memories[key] = new_memories
                    self._mark_dirty('auto')
                    self._memory_creation_counter[key] = 0
                    log.info(f"LLM dedup for {key}: {before_count} -> {len(new_memories)} memories")

        except Exception as e:
            log.warn(f"LLM deduplication failed for {key}: {e}")

    # ==========================================================================
    # MEMORY GENERATION
    # ==========================================================================

    async def generate_memory(
        self,
        provider_manager,
        messages: List[dict],
        is_dm: bool,
        id_key: int,
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

                # Determine server_id and server_name
                server_id = id_key if not is_dm else 0
                server_name = None  # Will be populated by caller if available

                # Store in unified auto memories
                added = self.add_auto_memory(
                    server_id=server_id,
                    user_id=user_id or id_key,
                    content=result.strip(),
                    character_name=character_name,
                    user_name=user_name,
                    server_name=server_name,
                    embedding=embedding
                )

                if added:
                    # Check if LLM dedup should run
                    auto_key = self._auto_key(server_id, user_id or id_key)
                    if self.should_llm_deduplicate(auto_key):
                        # Fire-and-forget (will run async)
                        import asyncio
                        asyncio.create_task(self.llm_deduplicate(auto_key, provider_manager))

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
            auto_memories[key].append({
                "content": content,
                "timestamp": entry.get("timestamp", datetime.now().isoformat()),
                "fingerprint": entry.get("fingerprint") or _memory_fingerprint(content),
                "user_name": entry.get("user_name"),
                "character": entry.get("character") or entry.get("learned_from"),
            })
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
                manual_lore[key] = [{
                    "content": lore_text.strip(),
                    "timestamp": datetime.now().isoformat(),
                    "fingerprint": _memory_fingerprint(lore_text),
                    "added_by": "migrated"
                }]
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

