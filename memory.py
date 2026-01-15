"""
Discord Pals - Memory System
Stores and retrieves memories for the bot.
Per-character memory isolation: each character has its own DM and user memory files.
With debounced saving to reduce disk I/O.
"""

import os
import re
import time
import difflib
from typing import Dict, List, Optional
from datetime import datetime
from config import (
    DATA_DIR, MEMORIES_FILE, DM_MEMORIES_FILE, USER_MEMORIES_FILE,
    LORE_FILE, DM_MEMORIES_DIR, USER_MEMORIES_DIR, GLOBAL_USER_PROFILES_FILE
)
from discord_utils import safe_json_load, safe_json_save, remove_thinking_tags
from constants import MEMORY_SAVE_INTERVAL
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


def _is_duplicate_memory(new_content: str, existing_memories: list, threshold: float = 0.75) -> bool:
    """Check if new memory is semantically similar to existing ones.

    Uses two-stage check:
    1. Key term overlap (fast) - if <40% terms match, skip expensive check
    2. Sequence similarity (accurate) - confirms with difflib

    Args:
        new_content: The new memory content to check
        existing_memories: List of existing memory dicts with 'content' key
        threshold: Similarity threshold (0.0-1.0), default 0.75

    Returns:
        True if duplicate found, False otherwise
    """
    if not existing_memories or not new_content:
        return False

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
            if overlap < 0.4:  # Not enough overlap, skip expensive check
                continue

        # Stage 2: Sequence similarity
        similarity = difflib.SequenceMatcher(None, new_normalized, existing_normalized).ratio()
        if similarity >= threshold:
            return True

    return False


def get_character_dm_file(character_name: str) -> str:
    """Get the DM memories file path for a specific character."""
    safe_name = character_name.lower().replace(" ", "_")
    return os.path.join(DM_MEMORIES_DIR, f"{safe_name}.json")


def get_character_user_file(character_name: str) -> str:
    """Get the user memories file path for a specific character."""
    safe_name = character_name.lower().replace(" ", "_")
    return os.path.join(USER_MEMORIES_DIR, f"{safe_name}.json")


class MemoryManager:
    """Manages server and DM memories with auto-generation and manual saving.

    Memory layers:
    - Server memories: Shared across all characters (per-server events)
    - Lore: Shared across all characters (world-building)
    - Global user profiles: Cross-server facts about users (follows users everywhere)
    - DM memories: Per-character (each character remembers their own DM conversations)
    - User memories: Per-character, per-server (each character has their own memories about users)

    Uses debounced saving to reduce disk I/O.
    """

    def __init__(self):
        ensure_data_dir()
        # Shared across all characters
        self.server_memories: Dict[str, List[dict]] = safe_json_load(MEMORIES_FILE)
        self.lore: Dict[str, str] = safe_json_load(LORE_FILE)

        # Global user profiles (cross-server, follows users everywhere)
        self.global_user_profiles: Dict[str, List[dict]] = safe_json_load(GLOBAL_USER_PROFILES_FILE)

        # Per-character memory caches (loaded on demand)
        self._dm_memory_cache: Dict[str, Dict[str, List[dict]]] = {}
        self._user_memory_cache: Dict[str, Dict[str, Dict[str, List[dict]]]] = {}

        # Legacy shared memories (lazy-loaded for backwards compatibility)
        self._legacy_dm_memories: Optional[Dict[str, List[dict]]] = None
        self._legacy_user_memories: Optional[Dict[str, Dict[str, List[dict]]]] = None
        self._legacy_loaded = False

        # Debounce tracking
        self._dirty_files: set = set()  # Track which files need saving
        self._last_save = time.time()

    def _ensure_legacy_loaded(self):
        """Lazy-load legacy memory files only when needed."""
        if not self._legacy_loaded:
            self._legacy_dm_memories = safe_json_load(DM_MEMORIES_FILE)
            self._legacy_user_memories = safe_json_load(USER_MEMORIES_FILE)
            self._legacy_loaded = True

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
        if 'server' in self._dirty_files:
            safe_json_save(MEMORIES_FILE, self.server_memories)
        if 'lore' in self._dirty_files:
            safe_json_save(LORE_FILE, self.lore)
        if 'global_profiles' in self._dirty_files:
            safe_json_save(GLOBAL_USER_PROFILES_FILE, self.global_user_profiles)
        if 'legacy_dm' in self._dirty_files and self._legacy_loaded:
            safe_json_save(DM_MEMORIES_FILE, self._legacy_dm_memories)
        if 'legacy_user' in self._dirty_files and self._legacy_loaded:
            safe_json_save(USER_MEMORIES_FILE, self._legacy_user_memories)

        # Save character-specific files
        for file_type in list(self._dirty_files):
            if file_type.startswith('dm:'):
                char_name = file_type[3:]
                self._save_character_dm_memories(char_name)
            elif file_type.startswith('user:'):
                char_name = file_type[5:]
                self._save_character_user_memories(char_name)

        self._dirty_files.clear()
        self._last_save = time.time()

    def flush(self):
        """Force save all dirty files - call on shutdown."""
        if self._dirty_files:
            self._save_dirty_files()

    def _get_dm_memories_for_character(self, character_name: str) -> Dict[str, List[dict]]:
        """Load DM memories for a character (with caching)."""
        if character_name not in self._dm_memory_cache:
            filepath = get_character_dm_file(character_name)
            self._dm_memory_cache[character_name] = safe_json_load(filepath)
        return self._dm_memory_cache[character_name]

    def _get_user_memories_for_character(self, character_name: str) -> Dict[str, Dict[str, List[dict]]]:
        """Load user memories for a character (with caching)."""
        if character_name not in self._user_memory_cache:
            filepath = get_character_user_file(character_name)
            self._user_memory_cache[character_name] = safe_json_load(filepath)
        return self._user_memory_cache[character_name]

    def _save_character_dm_memories(self, character_name: str):
        """Save DM memories for a specific character."""
        if character_name in self._dm_memory_cache:
            filepath = get_character_dm_file(character_name)
            safe_json_save(filepath, self._dm_memory_cache[character_name])

    def _save_character_user_memories(self, character_name: str):
        """Save user memories for a specific character."""
        if character_name in self._user_memory_cache:
            filepath = get_character_user_file(character_name)
            safe_json_save(filepath, self._user_memory_cache[character_name])

    def save_all(self):
        """Save all memories to disk."""
        safe_json_save(MEMORIES_FILE, self.server_memories)
        safe_json_save(LORE_FILE, self.lore)
        safe_json_save(GLOBAL_USER_PROFILES_FILE, self.global_user_profiles)

        # Save all cached character memories
        for character_name in self._dm_memory_cache:
            self._save_character_dm_memories(character_name)
        for character_name in self._user_memory_cache:
            self._save_character_user_memories(character_name)

        # Clear dirty tracking since we saved everything
        self._dirty_files.clear()
        self._last_save = time.time()

    # --- Server Memories (shared) ---

    def add_server_memory(self, guild_id: int, content: str, auto: bool = False) -> bool:
        """Add a memory for a server.

        Returns True if memory was added, False if duplicate detected.
        """
        # Strip any reasoning tags before storing
        content = remove_thinking_tags(content)

        key = str(guild_id)
        if key not in self.server_memories:
            self.server_memories[key] = []

        # Check for duplicates before adding
        if _is_duplicate_memory(content, self.server_memories[key]):
            log.debug(f"Skipping duplicate server memory: {content[:50]}...")
            return False

        memory = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "auto": auto
        }
        self.server_memories[key].append(memory)

        # Keep max 50 memories per server
        if len(self.server_memories[key]) > 50:
            self.server_memories[key] = self.server_memories[key][-50:]

        self._mark_dirty('server')
        return True

    def get_server_memories(self, guild_id: int, limit: int = 10) -> str:
        """Get formatted memories for a server."""
        key = str(guild_id)
        memories = self.server_memories.get(key, [])[-limit:]
        if not memories:
            return ""
        return "\n".join([f"- {remove_thinking_tags(m['content'])}" for m in memories])

    def clear_server_memories(self, guild_id: int):
        """Clear all memories for a server."""
        key = str(guild_id)
        if key in self.server_memories:
            del self.server_memories[key]
            self._mark_dirty('server')

    # --- DM Memories (per-character) ---

    def add_dm_memory(self, user_id: int, content: str, auto: bool = False,
                      character_name: str = None, user_name: str = None) -> bool:
        """Add a memory for a DM conversation.

        Returns True if memory was added, False if duplicate detected.
        """
        if not character_name:
            # Fallback to legacy shared file
            return self._add_legacy_dm_memory(user_id, content, auto)

        dm_memories = self._get_dm_memories_for_character(character_name)
        key = str(user_id)

        if key not in dm_memories:
            dm_memories[key] = []

        # Check for duplicates before adding
        if _is_duplicate_memory(content, dm_memories[key]):
            log.debug(f"Skipping duplicate DM memory: {content[:50]}...")
            return False

        memory = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "auto": auto,
            "character": character_name
        }
        if user_name:
            memory["user_name"] = user_name

        dm_memories[key].append(memory)

        # Keep max 30 memories per user
        if len(dm_memories[key]) > 30:
            dm_memories[key] = dm_memories[key][-30:]

        self._mark_dirty(f'dm:{character_name}')
        return True

    def _add_legacy_dm_memory(self, user_id: int, content: str, auto: bool = False):
        """Add to legacy shared DM memories (backwards compatibility)."""
        self._ensure_legacy_loaded()
        key = str(user_id)
        if key not in self._legacy_dm_memories:
            self._legacy_dm_memories[key] = []

        self._legacy_dm_memories[key].append({
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "auto": auto
        })

        if len(self._legacy_dm_memories[key]) > 30:
            self._legacy_dm_memories[key] = self._legacy_dm_memories[key][-30:]

        self._mark_dirty('legacy_dm')

    def get_dm_memories(self, user_id: int, limit: int = 10, character_name: str = None) -> str:
        """Get formatted memories for a DM."""
        key = str(user_id)
        memories = []

        if character_name:
            # Get character-specific memories
            dm_memories = self._get_dm_memories_for_character(character_name)
            memories = dm_memories.get(key, [])[-limit:]

        # Fallback to legacy if no character-specific memories found
        if not memories:
            self._ensure_legacy_loaded()
            memories = self._legacy_dm_memories.get(key, [])[-limit:]

        if not memories:
            return ""
        return "\n".join([f"- {remove_thinking_tags(m['content'])}" for m in memories])

    def clear_dm_memories(self, user_id: int, character_name: str = None):
        """Clear all memories for a user's DMs."""
        key = str(user_id)

        if character_name:
            dm_memories = self._get_dm_memories_for_character(character_name)
            if key in dm_memories:
                del dm_memories[key]
                self._mark_dirty(f'dm:{character_name}')
        else:
            self._ensure_legacy_loaded()
            if key in self._legacy_dm_memories:
                del self._legacy_dm_memories[key]
                self._mark_dirty('legacy_dm')

    # --- Per-User Server Memories (per-character) ---

    def add_user_memory(self, guild_id: int, user_id: int, content: str,
                        auto: bool = False, user_name: str = None, character_name: str = None) -> bool:
        """Add a memory about a specific user in a server.

        Returns True if memory was added, False if duplicate detected.
        """
        # Strip any reasoning tags before storing
        content = remove_thinking_tags(content)

        if not character_name:
            # Fallback to legacy shared file
            return self._add_legacy_user_memory(guild_id, user_id, content, auto, user_name)

        user_memories = self._get_user_memories_for_character(character_name)
        guild_key = str(guild_id)
        user_key = str(user_id)

        if guild_key not in user_memories:
            user_memories[guild_key] = {}
        if user_key not in user_memories[guild_key]:
            user_memories[guild_key][user_key] = []

        # Check for duplicates before adding
        if _is_duplicate_memory(content, user_memories[guild_key][user_key]):
            log.debug(f"Skipping duplicate user memory: {content[:50]}...")
            return False

        memory = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "auto": auto,
            "character": character_name
        }
        if user_name:
            memory["user_name"] = user_name

        user_memories[guild_key][user_key].append(memory)

        # Keep max 20 memories per user per server
        if len(user_memories[guild_key][user_key]) > 20:
            user_memories[guild_key][user_key] = user_memories[guild_key][user_key][-20:]

        self._mark_dirty(f'user:{character_name}')
        return True

    def _add_legacy_user_memory(self, guild_id: int, user_id: int, content: str,
                                 auto: bool = False, user_name: str = None):
        """Add to legacy shared user memories (backwards compatibility)."""
        self._ensure_legacy_loaded()
        guild_key = str(guild_id)
        user_key = str(user_id)

        if guild_key not in self._legacy_user_memories:
            self._legacy_user_memories[guild_key] = {}
        if user_key not in self._legacy_user_memories[guild_key]:
            self._legacy_user_memories[guild_key][user_key] = []

        memory = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "auto": auto
        }
        if user_name:
            memory["user_name"] = user_name

        self._legacy_user_memories[guild_key][user_key].append(memory)

        if len(self._legacy_user_memories[guild_key][user_key]) > 20:
            self._legacy_user_memories[guild_key][user_key] = self._legacy_user_memories[guild_key][user_key][-20:]

        self._mark_dirty('legacy_user')

    def get_user_memories(self, guild_id: int, user_id: int, limit: int = 5,
                          character_name: str = None) -> str:
        """Get formatted memories about a specific user in a server."""
        guild_key = str(guild_id)
        user_key = str(user_id)
        memories = []

        if character_name:
            # Get character-specific memories
            user_memories = self._get_user_memories_for_character(character_name)
            if guild_key in user_memories:
                memories = user_memories[guild_key].get(user_key, [])[-limit:]

        # Fallback to legacy if no character-specific memories found
        if not memories:
            self._ensure_legacy_loaded()
            if guild_key in self._legacy_user_memories:
                memories = self._legacy_user_memories[guild_key].get(user_key, [])[-limit:]

        if not memories:
            return ""
        return "\n".join([f"- {remove_thinking_tags(m['content'])}" for m in memories])

    def clear_user_memories(self, guild_id: int, user_id: int, character_name: str = None):
        """Clear memories about a specific user."""
        guild_key = str(guild_id)
        user_key = str(user_id)

        if character_name:
            user_memories = self._get_user_memories_for_character(character_name)
            if guild_key in user_memories and user_key in user_memories[guild_key]:
                del user_memories[guild_key][user_key]
                self._mark_dirty(f'user:{character_name}')
        else:
            self._ensure_legacy_loaded()
            if guild_key in self._legacy_user_memories and user_key in self._legacy_user_memories[guild_key]:
                del self._legacy_user_memories[guild_key][user_key]
                self._mark_dirty('legacy_user')

    # --- Global User Profiles (cross-server) ---

    def add_global_user_profile(self, user_id: int, content: str, auto: bool = False,
                                 user_name: str = None, character_name: str = None) -> bool:
        """Add a cross-server fact about a user (follows them everywhere).

        Returns True if memory was added, False if duplicate detected.
        """
        # Strip any reasoning tags before storing
        content = remove_thinking_tags(content)

        key = str(user_id)
        if key not in self.global_user_profiles:
            self.global_user_profiles[key] = []

        # Check for duplicates before adding
        if _is_duplicate_memory(content, self.global_user_profiles[key]):
            log.debug(f"Skipping duplicate global profile: {content[:50]}...")
            return False

        memory = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "auto": auto
        }
        if user_name:
            memory["user_name"] = user_name
        if character_name:
            memory["learned_from"] = character_name

        self.global_user_profiles[key].append(memory)

        # Keep max 20 global facts per user
        if len(self.global_user_profiles[key]) > 20:
            self.global_user_profiles[key] = self.global_user_profiles[key][-20:]

        self._mark_dirty('global_profiles')
        return True

    def get_global_user_profile(self, user_id: int, limit: int = 5) -> str:
        """Get cross-server facts about a user."""
        key = str(user_id)
        memories = self.global_user_profiles.get(key, [])[-limit:]
        if not memories:
            return ""
        return "\n".join([f"- {remove_thinking_tags(m['content'])}" for m in memories])

    def clear_global_user_profile(self, user_id: int):
        """Clear all global facts about a user."""
        key = str(user_id)
        if key in self.global_user_profiles:
            del self.global_user_profiles[key]
            self._mark_dirty('global_profiles')

    # --- Lore (shared) ---

    def add_lore(self, guild_id: int, lore_text: str):
        """Add or append lore for a server."""
        # Strip any reasoning tags before storing
        lore_text = remove_thinking_tags(lore_text)

        key = str(guild_id)
        existing = self.lore.get(key, "")
        if existing:
            self.lore[key] = existing + "\n" + lore_text
        else:
            self.lore[key] = lore_text
        self._mark_dirty('lore')

    def get_lore(self, guild_id: int) -> str:
        """Get lore for a server."""
        return self.lore.get(str(guild_id), "")

    def clear_lore(self, guild_id: int):
        """Clear lore for a server."""
        key = str(guild_id)
        if key in self.lore:
            del self.lore[key]
            self._mark_dirty('lore')

    # --- Memory Generation ---

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

        # Build context with explicit user attribution (using author from history)
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

        # Use specific user name in prompt to ensure correct attribution
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
                # Sanitize before storing - remove any reasoning tags
                result = remove_thinking_tags(result)

                # Validate memory is about the correct user - must mention their name
                if user_name and user_name.lower() not in result.lower():
                    log.debug(f"Rejected memory - doesn't mention target user {user_name}: {result[:100]}")
                    return None

                if is_dm:
                    self.add_dm_memory(id_key, result.strip(), auto=True,
                                       character_name=character_name, user_name=user_name)
                    # Also add to global profile (DM facts are always important)
                    self.add_global_user_profile(id_key, result.strip(), auto=True,
                                                 user_name=user_name, character_name=character_name)
                else:
                    # Add to server-wide memory (shared)
                    self.add_server_memory(id_key, result.strip(), auto=True)

                    # Also add per-user memory (character-specific)
                    if user_id:
                        self.add_user_memory(id_key, user_id, result.strip(), auto=True,
                                             user_name=user_name, character_name=character_name)
                        # Also add to global profile (cross-server)
                        self.add_global_user_profile(user_id, result.strip(), auto=True,
                                                     user_name=user_name, character_name=character_name)

                return result.strip()
        except Exception as e:
            log.warn(f"Memory generation failed: {e}")

        return None


# Global instance
memory_manager = MemoryManager()
