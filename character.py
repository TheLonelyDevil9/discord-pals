"""
Discord Pals - Character Loader
Loads character definitions from markdown files.
Prompts are loaded from separate template files.
"""

import os
import re
from datetime import datetime
from typing import Optional, Dict, List
from config import CHARACTERS_DIR

PROMPTS_DIR = "prompts"

# Pre-compiled regex patterns for character parsing
RE_TITLE = re.compile(r'^#\s+(.+)$', re.MULTILINE)
RE_PERSONA = re.compile(r'##\s*Persona\s*\n(.*?)(?=\n##|\Z)', re.DOTALL | re.IGNORECASE)
RE_DIALOGUE = re.compile(r'##\s*Example Dialogue\s*\n(.*?)(?=\n##|\Z)', re.DOTALL | re.IGNORECASE)
RE_SPECIAL_USERS = re.compile(r'##\s*Special Users?\s*\n(.*?)(?=\n##|\Z)', re.DOTALL | re.IGNORECASE)
RE_EMPTY_LINES = re.compile(r'\n{3,}')
RE_TEMPLATE_VAR = re.compile(r'{{\s*([a-zA-Z0-9_]+)\s*}}')


def _get_time_variables(now: Optional[datetime] = None) -> Dict[str, str]:
    """Build placeholder variables for current local date/time awareness."""
    now = now.astimezone() if now else datetime.now().astimezone()
    timezone_name = now.strftime("%Z") or now.strftime("UTC%z")
    utc_offset = now.strftime("%z")
    if utc_offset and len(utc_offset) == 5:
        utc_offset = f"{utc_offset[:3]}:{utc_offset[3:]}"

    return {
        "time": now.strftime("%I:%M %p").lstrip("0") or "12:00 AM",
        "time_12h": now.strftime("%I:%M %p").lstrip("0") or "12:00 AM",
        "time_24h": now.strftime("%H:%M"),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": now.strftime("%A"),
        "day": str(now.day),
        "day_padded": now.strftime("%d"),
        "month": str(now.month),
        "month_padded": now.strftime("%m"),
        "month_name": now.strftime("%B"),
        "month_short": now.strftime("%b"),
        "year": now.strftime("%Y"),
        "hour": str(now.hour),
        "hour_24": now.strftime("%H"),
        "hour_12": now.strftime("%I").lstrip("0") or "12",
        "minute": now.strftime("%M"),
        "second": now.strftime("%S"),
        "ampm": now.strftime("%p"),
        "timezone": timezone_name or utc_offset or "local time",
        "utc_offset": utc_offset or "",
        "datetime": now.strftime("%A, %Y-%m-%d %I:%M %p").replace(" 0", " "),
        "iso_datetime": now.isoformat(timespec="seconds"),
        "unix": str(int(now.timestamp())),
    }


def _render_template_variables(text: str, replacements: Dict[str, str], now: Optional[datetime] = None) -> str:
    """Render template variables, including current time placeholders, across the whole text."""
    if not text:
        return text

    variables = {key.lower(): value for key, value in replacements.items()}
    variables.update(_get_time_variables(now))

    rendered = text
    for _ in range(3):
        updated = RE_TEMPLATE_VAR.sub(
            lambda match: str(variables.get(match.group(1).lower(), match.group(0))),
            rendered
        )
        if updated == rendered:
            break
        rendered = updated
    return rendered


class PromptManager:
    """Manages prompt templates."""
    
    def __init__(self):
        self.system_template = ""
        self.chatroom_context_template = ""
        self._load_templates()
    
    def _load_templates(self):
        """Load prompt templates from files."""
        prompts_path = os.path.join(os.path.dirname(__file__), PROMPTS_DIR)
        
        # Load system template (character section only)
        system_path = os.path.join(prompts_path, "system.md")
        if os.path.exists(system_path):
            with open(system_path, 'r', encoding='utf-8') as f:
                self.system_template = f.read()
        
        # Load chatroom context template (injected between history and immediate)
        context_path = os.path.join(prompts_path, "chatroom_context.md")
        if os.path.exists(context_path):
            with open(context_path, 'r', encoding='utf-8') as f:
                self.chatroom_context_template = f.read()
    
    def reload(self):
        """Reload templates from disk."""
        self._load_templates()
    
    def build_prompt(
        self,
        character_name: str,
        persona: str,
        special_user_context: str = "",
        example_dialogue: str = "",
        now: Optional[datetime] = None
    ) -> str:
        """Build system prompt (character section) from template."""
        
        # Start with template
        prompt = self.system_template
        
        # Make substitutions (character section only)
        replacements = {
            "character_name": character_name,
            "persona": persona,
            "special_user_context": f"<special_context>\n{special_user_context}\n</special_context>" if special_user_context else "",
            "example_dialogue": f"## Example Dialogue\n\n{example_dialogue}" if example_dialogue else ""
        }

        prompt = _render_template_variables(prompt, replacements, now=now)
        
        # Clean up empty lines from unused placeholders
        prompt = RE_EMPTY_LINES.sub('\n\n', prompt)
        
        return prompt.strip()
    
    def build_chatroom_context(
        self,
        guild_name: str = "DM",
        character_name: str = "",
        emojis: str = "",
        lore: str = "",
        memories: str = "",
        user_name: str = "",
        active_users: str = "",
        mentioned_context: str = "",
        other_bots: str = "",
        mentionable_users: str = "",
        mentionable_bots: str = "",
        now: Optional[datetime] = None
    ) -> str:
        """Build chatroom context (injected between history and immediate messages)."""

        # Start with template
        context = self.chatroom_context_template

        time_vars = _get_time_variables(now)
        timezone_display = time_vars["timezone"]
        if time_vars["utc_offset"] and time_vars["utc_offset"] not in timezone_display:
            timezone_display = f"{timezone_display} {time_vars['utc_offset']}".strip()

        # Make substitutions
        replacements = {
            "guild_name": guild_name,
            "character_name": character_name,
            "emojis": f"Available server emojis (use :emoji_name: format):\n{emojis}" if emojis else "",
            "lore": f"<lore>\n{lore}\n</lore>" if lore else "",
            "memories": f"<memories>\n{memories}\n</memories>" if memories else "",
            "user_name": user_name,
            "active_users": active_users,
            "mentioned_context": f"--- Context about mentioned users ---\n{mentioned_context}" if mentioned_context else "",
            "other_bots": other_bots,
            "mentionable_users": mentionable_users,
            "mentionable_bots": mentionable_bots,
            "current_time_context": (
                f"Current local date/time: {time_vars['weekday']}, {time_vars['date']} "
                f"at {time_vars['time']} ({timezone_display})"
            ),
        }

        context = _render_template_variables(context, replacements, now=now)

        # Clean up empty lines from unused placeholders
        context = RE_EMPTY_LINES.sub('\n\n', context)

        return context.strip()


class Character:
    """Represents a loaded character definition."""

    def __init__(self, name: str, persona: str, special_users: Dict[str, str] = None, example_dialogue: str = ""):
        self.name = name
        self.persona = persona
        self.special_users = special_users or {}
        self.example_dialogue = example_dialogue

    def get_special_user_context(self, user_name: str) -> str:
        """Get special context for a user with fuzzy matching for Discord display names.

        Matching order:
        1. Exact match (fastest path)
        2. Display name starts with special user name ("Kris WaWa" matches "Kris")
        3. Special user name appears as a word in display name ("The Real Kris" matches "Kris")
        """
        # Exact match first
        if user_name in self.special_users:
            return self.special_users[user_name]

        # Fuzzy matching for Discord display names
        user_lower = user_name.lower()
        for special_name, context in self.special_users.items():
            special_lower = special_name.lower()

            # Check if display name starts with special name + separator
            # "Kris WaWa" matches "Kris", "Kris_Alt" matches "Kris"
            if (user_lower.startswith(special_lower + " ") or
                user_lower.startswith(special_lower + "_") or
                user_lower == special_lower):
                return context

            # Check if special name appears as a complete word
            # Handles "The Real Kris" matching "Kris"
            if re.search(rf'\b{re.escape(special_lower)}\b', user_lower):
                return context

        return ""


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
        example_dialogue = ""
        special_users = {}

        # Extract character name from title
        title_match = RE_TITLE.search(content)
        if title_match:
            char_name = title_match.group(1).strip()

        # Extract Persona section
        persona_match = RE_PERSONA.search(content)
        if persona_match:
            persona = persona_match.group(1).strip()

        # Extract Example Dialogue section
        dialogue_match = RE_DIALOGUE.search(content)
        if dialogue_match:
            example_dialogue = dialogue_match.group(1).strip()

        # Extract Special Users section
        special_match = RE_SPECIAL_USERS.search(content)
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

        character = Character(char_name, persona, special_users, example_dialogue)
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
        user_name: str = "",
        now: Optional[datetime] = None
    ) -> str:
        """Build system prompt (character section only)."""
        special_context = character.get_special_user_context(user_name)
        
        return self.prompt_manager.build_prompt(
            character_name=character.name,
            persona=character.persona,
            special_user_context=special_context,
            example_dialogue=character.example_dialogue,
            now=now
        )
    
    def build_chatroom_context(
        self,
        guild_name: str = "DM",
        character_name: str = "",
        emojis: str = "",
        lore: str = "",
        memories: str = "",
        user_name: str = "",
        active_users: list = None,
        mentioned_context: str = "",
        other_bot_names: list = None,
        mentionable_users: list = None,
        mentionable_bots: list = None,
        now: Optional[datetime] = None
    ) -> str:
        """Build chatroom context (injected between history and immediate).

        Args:
            guild_name: Server name or "DM"
            emojis: Available custom emojis
            lore: Channel/server lore
            memories: Relevant memories
            user_name: Current user being replied to
            active_users: List of active user names
            mentioned_context: Context about mentioned users
            other_bot_names: Names of other bots (to prevent impersonation)
            mentionable_users: List of users that can be @mentioned
            mentionable_bots: List of bots that can be @mentioned
        """

        # Add active users for social awareness
        active_users_context = ""
        if active_users and len(active_users) > 1:
            others = [u for u in active_users if u != user_name][:5]
            if others:
                active_users_context = f"Other active participants: {', '.join(others)}"

        # Add other bots awareness to prevent impersonation
        other_bots_context = ""
        if other_bot_names:
            other_bots_context = f"Other bot characters in this channel (you are NOT them, do not imitate): {', '.join(other_bot_names)}"

        # Add mentionable users context (for @mention feature)
        # Show @Username format (not raw <@id>) - AI learns to use @Name, we convert on output
        mentionable_users_context = ""
        if mentionable_users:
            user_list = [f"- @{u['name']}" for u in mentionable_users[:10]]
            mentionable_users_context = "Users you can @mention to get their attention:\n" + "\n".join(user_list)

        # Add mentionable bots context (for bot-to-bot @mention feature)
        mentionable_bots_context = ""
        if mentionable_bots:
            bot_list = [f"- @{b['character_name']}" for b in mentionable_bots if b.get('character_name')]
            if bot_list:
                mentionable_bots_context = "Other bots you can @mention to summon them:\n" + "\n".join(bot_list)

        return self.prompt_manager.build_chatroom_context(
            guild_name=guild_name,
            character_name=character_name,
            emojis=emojis,
            lore=lore,
            memories=memories,
            user_name=user_name,
            active_users=active_users_context,
            mentioned_context=mentioned_context,
            other_bots=other_bots_context,
            mentionable_users=mentionable_users_context,
            mentionable_bots=mentionable_bots_context,
            now=now
        )


# Global instance
character_manager = CharacterManager()
