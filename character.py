"""
Discord Pals - Character Loader
Loads character definitions from markdown files.
Prompts are loaded from separate template files.
"""

import os
import re
from typing import Optional, Dict, List
from config import CHARACTERS_DIR

PROMPTS_DIR = "prompts"


class PromptManager:
    """Manages prompt templates."""
    
    def __init__(self):
        self.system_template = ""
        self.response_rules = ""
        self._load_templates()
    
    def _load_templates(self):
        """Load prompt templates from files."""
        prompts_path = os.path.join(os.path.dirname(__file__), PROMPTS_DIR)
        
        # Load system template
        system_path = os.path.join(prompts_path, "system.md")
        if os.path.exists(system_path):
            with open(system_path, 'r', encoding='utf-8') as f:
                self.system_template = f.read()
        
        # Load response rules
        rules_path = os.path.join(prompts_path, "response_rules.md")
        if os.path.exists(rules_path):
            with open(rules_path, 'r', encoding='utf-8') as f:
                self.response_rules = f.read()
    
    def reload(self):
        """Reload templates from disk."""
        self._load_templates()
    
    def build_prompt(
        self,
        character_name: str,
        persona: str,
        guild_name: str = "DM",
        emojis: str = "",
        lore: str = "",
        memories: str = "",
        special_user_context: str = "",
        user_name: str = "",
        active_users: str = ""
    ) -> str:
        """Build system prompt from template with substitutions."""
        
        # Start with template
        prompt = self.system_template
        
        # Make substitutions
        replacements = {
            "{{CHARACTER_NAME}}": character_name,
            "{{PERSONA}}": persona,
            "{{GUILD_NAME}}": guild_name,
            "{{EMOJIS}}": f"Available server emojis (use :emoji_name: format):\n{emojis}" if emojis else "",
            "{{LORE}}": f"<lore>\n{lore}\n</lore>" if lore else "",
            "{{MEMORIES}}": f"<memories>\n{memories}\n</memories>" if memories else "",
            "{{SPECIAL_USER_CONTEXT}}": f"<special_context>\n{special_user_context}\n</special_context>" if special_user_context else "",
            "{{USER_NAME}}": user_name,
            "{{ACTIVE_USERS}}": active_users,
            "{{RESPONSE_RULES}}": self.response_rules.replace("{{USER_NAME}}", user_name) if user_name else ""
        }
        
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        
        # Clean up empty lines from unused placeholders
        prompt = re.sub(r'\n{3,}', '\n\n', prompt)
        
        return prompt.strip()


class Character:
    """Represents a loaded character definition."""
    
    def __init__(self, name: str, persona: str, special_users: Dict[str, str] = None):
        self.name = name
        self.persona = persona
        self.special_users = special_users or {}
    
    def get_special_user_context(self, user_name: str) -> str:
        """Get special context for a user if it exists."""
        return self.special_users.get(user_name, "")


class CharacterManager:
    """Manages loading and switching characters."""
    
    def __init__(self):
        self.characters: Dict[str, Character] = {}
        self.current: Optional[Character] = None
        self.prompt_manager = PromptManager()
        self._ensure_dirs()
    
    def _ensure_dirs(self):
        """Create directories if needed."""
        if not os.path.exists(CHARACTERS_DIR):
            os.makedirs(CHARACTERS_DIR)
        prompts_path = os.path.join(os.path.dirname(__file__), PROMPTS_DIR)
        if not os.path.exists(prompts_path):
            os.makedirs(prompts_path)
    
    def list_available(self) -> List[str]:
        """List all available character files."""
        if not os.path.exists(CHARACTERS_DIR):
            return []
        return [
            f[:-3] for f in os.listdir(CHARACTERS_DIR) 
            if f.endswith('.md') and not f.startswith('_')
        ]
    
    def load(self, name: str) -> Optional[Character]:
        """Load a character from file."""
        filepath = os.path.join(CHARACTERS_DIR, f"{name}.md")
        
        if not os.path.exists(filepath):
            return None
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse sections
        char_name = name.title()
        persona = ""
        special_users = {}
        
        # Extract character name from title
        title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        if title_match:
            char_name = title_match.group(1).strip()
        
        # Extract Persona section
        persona_match = re.search(r'##\s*Persona\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL | re.IGNORECASE)
        if persona_match:
            persona = persona_match.group(1).strip()
        
        # Extract Special Users section
        special_match = re.search(r'##\s*Special Users?\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL | re.IGNORECASE)
        if special_match:
            lines = special_match.group(1).strip().split('\n')
            current_user = None
            current_context = []
            
            for line in lines:
                if line.startswith('### '):
                    if current_user:
                        special_users[current_user] = '\n'.join(current_context).strip()
                    current_user = line[4:].strip()
                    current_context = []
                elif current_user:
                    current_context.append(line)
            
            if current_user:
                special_users[current_user] = '\n'.join(current_context).strip()
        
        character = Character(char_name, persona, special_users)
        self.characters[name] = character
        return character
    
    def set_current(self, name: str) -> bool:
        """Set the current active character."""
        if name not in self.characters:
            char = self.load(name)
            if not char:
                return False
        
        self.current = self.characters[name]
        return True
    
    def reload_current(self) -> bool:
        """Reload the current character from file."""
        if not self.current:
            return False
        
        for name, char in self.characters.items():
            if char == self.current:
                self.load(name)
                self.current = self.characters[name]
                return True
        return False
    
    def reload_prompts(self):
        """Reload prompt templates."""
        self.prompt_manager.reload()
    
    def get_current(self) -> Optional[Character]:
        """Get the current character."""
        return self.current
    
    def build_system_prompt(
        self,
        character: Character,
        guild_name: str = "DM",
        emojis: str = "",
        lore: str = "",
        memories: str = "",
        user_name: str = "",
        active_users: list = None
    ) -> str:
        """Build complete system prompt for a character."""
        special_context = character.get_special_user_context(user_name)
        
        # Add active users for social awareness
        active_users_context = ""
        if active_users and len(active_users) > 1:
            others = [u for u in active_users if u != user_name][:5]
            if others:
                active_users_context = f"Other active participants: {', '.join(others)}"
        
        return self.prompt_manager.build_prompt(
            character_name=character.name,
            persona=character.persona,
            guild_name=guild_name,
            emojis=emojis,
            lore=lore,
            memories=memories,
            special_user_context=special_context,
            user_name=user_name,
            active_users=active_users_context
        )


# Global instance
character_manager = CharacterManager()
