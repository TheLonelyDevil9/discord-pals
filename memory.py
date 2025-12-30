"""
Discord Pals - Memory System
Stores and retrieves memories for the bot.
"""

import json
import os
from typing import Dict, List, Optional
from datetime import datetime
from config import DATA_DIR, MEMORIES_FILE, DM_MEMORIES_FILE, USER_MEMORIES_FILE, LORE_FILE


def ensure_data_dir():
    """Create data directory if it doesn't exist."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


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
    ensure_data_dir()
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class MemoryManager:
    """Manages server and DM memories with auto-generation and manual saving."""
    
    def __init__(self):
        self.server_memories: Dict[str, List[dict]] = load_json(MEMORIES_FILE)
        self.dm_memories: Dict[str, List[dict]] = load_json(DM_MEMORIES_FILE)
        self.user_memories: Dict[str, Dict[str, List[dict]]] = load_json(USER_MEMORIES_FILE)  # guild_id -> user_id -> memories
        self.lore: Dict[str, str] = load_json(LORE_FILE)
    
    def save_all(self):
        """Save all memories to disk."""
        save_json(MEMORIES_FILE, self.server_memories)
        save_json(DM_MEMORIES_FILE, self.dm_memories)
        save_json(USER_MEMORIES_FILE, self.user_memories)
        save_json(LORE_FILE, self.lore)
    
    # --- Server Memories ---
    
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
        
        self.save_all()
    
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
            self.save_all()
    
    # --- DM Memories ---
    
    def add_dm_memory(self, user_id: int, content: str, auto: bool = False):
        """Add a memory for a DM conversation."""
        key = str(user_id)
        if key not in self.dm_memories:
            self.dm_memories[key] = []
        
        memory = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "auto": auto
        }
        self.dm_memories[key].append(memory)
        
        # Keep max 30 memories per user
        if len(self.dm_memories[key]) > 30:
            self.dm_memories[key] = self.dm_memories[key][-30:]
        
        self.save_all()
    
    def get_dm_memories(self, user_id: int, limit: int = 10) -> str:
        """Get formatted memories for a DM."""
        key = str(user_id)
        memories = self.dm_memories.get(key, [])[-limit:]
        if not memories:
            return ""
        return "\n".join([f"- {m['content']}" for m in memories])
    
    def clear_dm_memories(self, user_id: int):
        """Clear all memories for a user's DMs."""
        key = str(user_id)
        if key in self.dm_memories:
            del self.dm_memories[key]
            self.save_all()
    
    # --- Per-User Server Memories ---
    
    def add_user_memory(self, guild_id: int, user_id: int, content: str, auto: bool = False):
        """Add a memory about a specific user in a server."""
        guild_key = str(guild_id)
        user_key = str(user_id)
        
        if guild_key not in self.user_memories:
            self.user_memories[guild_key] = {}
        if user_key not in self.user_memories[guild_key]:
            self.user_memories[guild_key][user_key] = []
        
        memory = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "auto": auto
        }
        self.user_memories[guild_key][user_key].append(memory)
        
        # Keep max 20 memories per user per server
        if len(self.user_memories[guild_key][user_key]) > 20:
            self.user_memories[guild_key][user_key] = self.user_memories[guild_key][user_key][-20:]
        
        self.save_all()
    
    def get_user_memories(self, guild_id: int, user_id: int, limit: int = 5) -> str:
        """Get formatted memories about a specific user in a server."""
        guild_key = str(guild_id)
        user_key = str(user_id)
        
        if guild_key not in self.user_memories:
            return ""
        memories = self.user_memories[guild_key].get(user_key, [])[-limit:]
        if not memories:
            return ""
        return "\n".join([f"- {m['content']}" for m in memories])
    
    def clear_user_memories(self, guild_id: int, user_id: int):
        """Clear memories about a specific user."""
        guild_key = str(guild_id)
        user_key = str(user_id)
        
        if guild_key in self.user_memories and user_key in self.user_memories[guild_key]:
            del self.user_memories[guild_key][user_key]
            self.save_all()
    
    # --- Lore ---
    
    def add_lore(self, guild_id: int, lore_text: str):
        """Add or append lore for a server."""
        key = str(guild_id)
        existing = self.lore.get(key, "")
        if existing:
            self.lore[key] = existing + "\n" + lore_text
        else:
            self.lore[key] = lore_text
        self.save_all()
    
    def get_lore(self, guild_id: int) -> str:
        """Get lore for a server."""
        return self.lore.get(str(guild_id), "")
    
    def clear_lore(self, guild_id: int):
        """Clear lore for a server."""
        key = str(guild_id)
        if key in self.lore:
            del self.lore[key]
            self.save_all()
    
    # --- Memory Generation ---
    
    async def generate_memory(
        self,
        provider_manager,
        messages: List[dict],
        is_dm: bool,
        id_key: int,
        character_name: str = "the character",
        user_id: int = None
    ) -> Optional[str]:
        """Generate a memory summary from conversation using AI."""
        if len(messages) < 5:
            return None
        
        # Extract all unique users from messages for per-user memory
        users_in_convo = set()
        for m in messages:
            author = m.get("author")
            if author and m.get("role") == "user":
                users_in_convo.add(author)
        
        context = "\n".join([
            f"{m.get('role', 'user')}: {m.get('content', '')[:200]}"
            for m in messages[-20:]
        ])
        
        prompt = f"""Analyze this conversation and determine if there's anything TRULY SIGNIFICANT that {character_name} should remember long-term.

ONLY save memories about:
- Personal facts revealed (name, job, relationships, preferences, life events)
- Emotional breakthroughs or deep bonding moments
- Explicit promises, commitments, or agreements
- Conflicts, disagreements, or resolutions
- Shared secrets or confidential information

DO NOT save memories about:
- Casual greetings or small talk
- Generic questions and answers
- Temporary moods or passing comments
- Things already covered in previous memories

If nothing truly significant happened, respond with just "NOTHING".
If significant, write a concise memory (1 sentence max) in third person about the USER, not {character_name}.

Example good memory: "User revealed they work as a software engineer and have a cat named Mochi"
Example bad memory: "User said hello and asked about the weather"

Conversation:
{context}

Memory (or NOTHING):"""
        
        try:
            result = await provider_manager.generate(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a memory analyzer. Create brief, factual summaries.",
                max_tokens=200
            )
            
            if result and "NOTHING" not in result.upper() and not result.startswith("❌"):
                if is_dm:
                    self.add_dm_memory(id_key, result.strip(), auto=True)
                else:
                    # Add to server-wide memory
                    self.add_server_memory(id_key, result.strip(), auto=True)
                    
                    # Also add per-user memory for the current user
                    if user_id:
                        self.add_user_memory(id_key, user_id, result.strip(), auto=True)
                
                return result.strip()
        except Exception as e:
            print(f"Memory generation failed: {e}")
        
        return None


# Global instance
memory_manager = MemoryManager()
