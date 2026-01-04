"""
Discord Pals - Memory System
Stores and retrieves memories for the bot.
Per-character memory isolation: each character has its own DM and user memory files.
"""

import json
import os
from typing import Dict, List, Optional
from datetime import datetime
from config import (
    DATA_DIR, MEMORIES_FILE, DM_MEMORIES_FILE, USER_MEMORIES_FILE, 
    LORE_FILE, DM_MEMORIES_DIR, USER_MEMORIES_DIR, GLOBAL_USER_PROFILES_FILE
)


def ensure_data_dir():
    """Create data directory and subdirectories if they don't exist."""
    for dir_path in [DATA_DIR, DM_MEMORIES_DIR, USER_MEMORIES_DIR]:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)


def load_json(filepath: str) -> dict:
    """Load JSON file, return empty dict if not exists."""
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_json(filepath: str, data: dict):
    """Save dict to JSON file."""
    # Ensure parent directory exists
    parent_dir = os.path.dirname(filepath)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
    """
    
    def __init__(self):
        ensure_data_dir()
        # Shared across all characters
        self.server_memories: Dict[str, List[dict]] = load_json(MEMORIES_FILE)
        self.lore: Dict[str, str] = load_json(LORE_FILE)
        
        # Global user profiles (cross-server, follows users everywhere)
        self.global_user_profiles: Dict[str, List[dict]] = load_json(GLOBAL_USER_PROFILES_FILE)  # user_id -> memories
        
        # Per-character memory caches (loaded on demand)
        self._dm_memory_cache: Dict[str, Dict[str, List[dict]]] = {}  # character -> user_id -> memories
        self._user_memory_cache: Dict[str, Dict[str, Dict[str, List[dict]]]] = {}  # character -> guild_id -> user_id -> memories
        
        # Legacy shared memories (for backwards compatibility)
        self._legacy_dm_memories: Dict[str, List[dict]] = load_json(DM_MEMORIES_FILE)
        self._legacy_user_memories: Dict[str, Dict[str, List[dict]]] = load_json(USER_MEMORIES_FILE)
    
    def _get_dm_memories_for_character(self, character_name: str) -> Dict[str, List[dict]]:
        """Load DM memories for a character (with caching)."""
        if character_name not in self._dm_memory_cache:
            filepath = get_character_dm_file(character_name)
            self._dm_memory_cache[character_name] = load_json(filepath)
        return self._dm_memory_cache[character_name]
    
    def _get_user_memories_for_character(self, character_name: str) -> Dict[str, Dict[str, List[dict]]]:
        """Load user memories for a character (with caching)."""
        if character_name not in self._user_memory_cache:
            filepath = get_character_user_file(character_name)
            self._user_memory_cache[character_name] = load_json(filepath)
        return self._user_memory_cache[character_name]
    
    def _save_character_dm_memories(self, character_name: str):
        """Save DM memories for a specific character."""
        if character_name in self._dm_memory_cache:
            filepath = get_character_dm_file(character_name)
            save_json(filepath, self._dm_memory_cache[character_name])
    
    def _save_character_user_memories(self, character_name: str):
        """Save user memories for a specific character."""
        if character_name in self._user_memory_cache:
            filepath = get_character_user_file(character_name)
            save_json(filepath, self._user_memory_cache[character_name])
    
    def save_all(self):
        """Save all memories to disk."""
        save_json(MEMORIES_FILE, self.server_memories)
        save_json(LORE_FILE, self.lore)
        
        # Save all cached character memories
        for character_name in self._dm_memory_cache:
            self._save_character_dm_memories(character_name)
        for character_name in self._user_memory_cache:
            self._save_character_user_memories(character_name)
    
    # --- Server Memories (shared) ---
    
    def add_server_memory(self, guild_id: int, content: str, auto: bool = False):
        """Add a memory for a server."""
        key = str(guild_id)
        if key not in self.server_memories:
            self.server_memories[key] = []
        
        memory = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "auto": auto
        }
        self.server_memories[key].append(memory)
        
        # Keep max 50 memories per server
        if len(self.server_memories[key]) > 50:
            self.server_memories[key] = self.server_memories[key][-50:]
        
        save_json(MEMORIES_FILE, self.server_memories)
    
    def get_server_memories(self, guild_id: int, limit: int = 10) -> str:
        """Get formatted memories for a server."""
        key = str(guild_id)
        memories = self.server_memories.get(key, [])[-limit:]
        if not memories:
            return ""
        return "\n".join([f"- {m['content']}" for m in memories])
    
    def clear_server_memories(self, guild_id: int):
        """Clear all memories for a server."""
        key = str(guild_id)
        if key in self.server_memories:
            del self.server_memories[key]
            save_json(MEMORIES_FILE, self.server_memories)
    
    # --- DM Memories (per-character) ---
    
    def add_dm_memory(self, user_id: int, content: str, auto: bool = False, 
                      character_name: str = None, user_name: str = None):
        """Add a memory for a DM conversation."""
        if not character_name:
            # Fallback to legacy shared file
            return self._add_legacy_dm_memory(user_id, content, auto)
        
        dm_memories = self._get_dm_memories_for_character(character_name)
        key = str(user_id)
        
        if key not in dm_memories:
            dm_memories[key] = []
        
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
        
        self._save_character_dm_memories(character_name)
    
    def _add_legacy_dm_memory(self, user_id: int, content: str, auto: bool = False):
        """Add to legacy shared DM memories (backwards compatibility)."""
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
        
        save_json(DM_MEMORIES_FILE, self._legacy_dm_memories)
    
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
            memories = self._legacy_dm_memories.get(key, [])[-limit:]
        
        if not memories:
            return ""
        return "\n".join([f"- {m['content']}" for m in memories])
    
    def clear_dm_memories(self, user_id: int, character_name: str = None):
        """Clear all memories for a user's DMs."""
        key = str(user_id)
        
        if character_name:
            dm_memories = self._get_dm_memories_for_character(character_name)
            if key in dm_memories:
                del dm_memories[key]
                self._save_character_dm_memories(character_name)
        else:
            if key in self._legacy_dm_memories:
                del self._legacy_dm_memories[key]
                save_json(DM_MEMORIES_FILE, self._legacy_dm_memories)
    
    # --- Per-User Server Memories (per-character) ---
    
    def add_user_memory(self, guild_id: int, user_id: int, content: str, 
                        auto: bool = False, user_name: str = None, character_name: str = None):
        """Add a memory about a specific user in a server."""
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
        
        self._save_character_user_memories(character_name)
    
    def _add_legacy_user_memory(self, guild_id: int, user_id: int, content: str, 
                                 auto: bool = False, user_name: str = None):
        """Add to legacy shared user memories (backwards compatibility)."""
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
        
        save_json(USER_MEMORIES_FILE, self._legacy_user_memories)
    
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
            if guild_key in self._legacy_user_memories:
                memories = self._legacy_user_memories[guild_key].get(user_key, [])[-limit:]
        
        if not memories:
            return ""
        return "\n".join([f"- {m['content']}" for m in memories])
    
    def clear_user_memories(self, guild_id: int, user_id: int, character_name: str = None):
        """Clear memories about a specific user."""
        guild_key = str(guild_id)
        user_key = str(user_id)
        
        if character_name:
            user_memories = self._get_user_memories_for_character(character_name)
            if guild_key in user_memories and user_key in user_memories[guild_key]:
                del user_memories[guild_key][user_key]
                self._save_character_user_memories(character_name)
        else:
            if guild_key in self._legacy_user_memories and user_key in self._legacy_user_memories[guild_key]:
                del self._legacy_user_memories[guild_key][user_key]
                save_json(USER_MEMORIES_FILE, self._legacy_user_memories)
    
    # --- Global User Profiles (cross-server) ---
    
    def add_global_user_profile(self, user_id: int, content: str, auto: bool = False, 
                                 user_name: str = None, character_name: str = None):
        """Add a cross-server fact about a user (follows them everywhere)."""
        key = str(user_id)
        if key not in self.global_user_profiles:
            self.global_user_profiles[key] = []
        
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
        
        save_json(GLOBAL_USER_PROFILES_FILE, self.global_user_profiles)
    
    def get_global_user_profile(self, user_id: int, limit: int = 5) -> str:
        """Get cross-server facts about a user."""
        key = str(user_id)
        memories = self.global_user_profiles.get(key, [])[-limit:]
        if not memories:
            return ""
        return "\n".join([f"- {m['content']}" for m in memories])
    
    def clear_global_user_profile(self, user_id: int):
        """Clear all global facts about a user."""
        key = str(user_id)
        if key in self.global_user_profiles:
            del self.global_user_profiles[key]
            save_json(GLOBAL_USER_PROFILES_FILE, self.global_user_profiles)
    
    # --- Lore (shared) ---
    
    def add_lore(self, guild_id: int, lore_text: str):
        """Add or append lore for a server."""
        key = str(guild_id)
        existing = self.lore.get(key, "")
        if existing:
            self.lore[key] = existing + "\n" + lore_text
        else:
            self.lore[key] = lore_text
        save_json(LORE_FILE, self.lore)
    
    def get_lore(self, guild_id: int) -> str:
        """Get lore for a server."""
        return self.lore.get(str(guild_id), "")
    
    def clear_lore(self, guild_id: int):
        """Clear lore for a server."""
        key = str(guild_id)
        if key in self.lore:
            del self.lore[key]
            save_json(LORE_FILE, self.lore)
    
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
        
        # Build context with explicit user attribution (using author_name from history)
        context_lines = []
        for m in messages[-20:]:
            author = m.get('author_name', 'Unknown')
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

IMPORTANT: Only save facts specifically about {target_user}, NOT about other users in the conversation.
If multiple users are chatting, focus ONLY on what {target_user} says and reveals about themselves.

SAVE memories about:
- Personal facts (name, job, hobbies, pets, relationships, location)
- Preferences and opinions (likes, dislikes, favorites)
- Life events or experiences mentioned
- Emotional moments or significant statements
- Quirks, habits, or recurring behaviors
- Promises, plans, or commitments they made

SKIP if:
- Generic greetings ("hi", "how are you")
- Very short exchanges with no substance
- Pure roleplay actions with no personal info
- The info is about someone OTHER than {target_user}

If nothing memorable about {target_user}, respond with just "NOTHING".
Otherwise, write ONE concise sentence starting with "{target_user}" (use their actual name).

Example: "{target_user} works as a nurse and has two cats named Luna and Mochi"
Example: "{target_user} mentioned they love cooking Italian food"
Example: "{target_user} gets anxious about job interviews"

Conversation:
{context}

Memory about {target_user} (or NOTHING):"""
        
        try:
            result = await provider_manager.generate(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a memory analyzer. Create brief, factual summaries."
                # max_tokens removed - uses provider config
            )
            
            if result and "NOTHING" not in result.upper() and not result.startswith("❌"):
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
            print(f"Memory generation failed: {e}")
        
        return None


# Global instance
memory_manager = MemoryManager()
