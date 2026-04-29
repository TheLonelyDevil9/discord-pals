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
OTHER_PROMPTS_FILE = "other_prompts.md"
LEGACY_CHATROOM_CONTEXT_FILE = "chatroom_context.md"

# Pre-compiled regex patterns for character parsing
RE_TITLE = re.compile(r'^#\s+(.+)$', re.MULTILINE)
RE_SECTION_HEADING = re.compile(r'^##\s+(.+?)\s*$', re.MULTILINE)
RE_EMPTY_LINES = re.compile(r'\n{3,}')
RE_TEMPLATE_VAR = re.compile(r'{{\s*([a-zA-Z0-9_]+)\s*}}')

SECTION_ALIASES = {
    "system persona": "persona",
    "persona": "persona",
    "example dialogue": "example_dialogue",
    "user context": "special_users",
    "special user": "special_users",
    "special users": "special_users",
}

OTHER_PROMPT_SECTION_ALIASES = {
    "chatroom context": "chatroom_context",
    "reminder delivery context": "reminder_delivery_context",
    "reminder clarification": "reminder_clarification",
    "dm follow-up": "dm_followup",
    "dm follow up": "dm_followup",
    "time passage context": "time_passage_context",
}

DEFAULT_OTHER_PROMPTS = """# Other Prompts

## Chatroom Context

Server: {{GUILD_NAME}}
{{CURRENT_TIME_CONTEXT}}
{{TIME_PASSAGE_CONTEXT}}

{{LORE}}
{{MEMORIES}}
{{EMOJIS}}
{{ACTIVE_USERS}}
{{OTHER_BOTS}}

{{MENTIONABLE_USERS}}
{{MENTIONABLE_BOTS}}

{{MENTIONED_CONTEXT}}

--- CURRENT REPLY TARGET ---
Respond exclusively to: {{USER_NAME}}
Avoid confusing {{USER_NAME}} with other people in the chat history.
If someone replies to another character's message, respond as {{CHARACTER_NAME}}. Only simulate one conversation at a time, and only speak as {{CHARACTER_NAME}}.

## Time Passage Context

<time_passage_context>
Elapsed time: {{GAP_LABEL}}.
This {{CONVERSATION_KIND}} has resumed after real time passed.
Before the pause: {{BEFORE_AUTHOR}}: {{BEFORE_CONTENT}}
After the pause: {{AFTER_AUTHOR}}: {{AFTER_CONTENT}}
Let the world state breathe forward when it is natural. People may have arrived, settled, slept, changed tasks, changed clothes, eaten, or cooled off if the prior chat implied it.
Infer lightly and phrase uncertainty naturally. Do not invent exact unseen events, quote this block, or present guesses as certain facts.
</time_passage_context>

## Reminder Delivery Context

Scheduled reminder details:
- Event: {{EVENT_SUMMARY}}
- Delivery stage: {{REMINDER_STAGE}}
- Reminder time: {{REMINDER_TIME}}
- Current target user: {{USER_NAME}}

## Reminder Clarification

The user may want a reminder, but some details are missing.
Current reminder summary: {{EVENT_SUMMARY}}
Missing detail to clarify: {{CLARIFICATION_PROMPT}}
Ask exactly one short clarification question in character. Do not answer anything else.

## DM Follow-up

You are {{CHARACTER_NAME}}. {{USER_NAME}} has not replied in a while.

Silence gap: {{IDLE_HOURS}} hours
{{TIME_PASSAGE_CONTEXT}}

Recent conversation:
{{RECENT_CONVERSATION}}

Recent topic:
{{RECENT_TOPIC}}

Relevant memories:
{{MEMORIES_EXCERPT}}

Rules:
{{RULES}}

Your follow-up message:
"""


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


def _format_current_time_context(now: Optional[datetime] = None) -> str:
    """Build a human-readable current time context line for prompt templates."""
    time_vars = _get_time_variables(now)
    timezone_display = time_vars["timezone"]
    if time_vars["utc_offset"] and time_vars["utc_offset"] not in timezone_display:
        timezone_display = f"{timezone_display} {time_vars['utc_offset']}".strip()

    return (
        f"Current local date/time: {time_vars['weekday']}, {time_vars['date']} "
        f"at {time_vars['time']} ({timezone_display})"
    )


def _normalize_section_name(section_name: str) -> str:
    """Normalize a markdown section heading for schema matching."""
    return " ".join((section_name or "").strip().lower().split())


def _extract_markdown_sections(content: str) -> list[tuple[str, str]]:
    """Extract top-level ## markdown sections in file order."""
    sections = []
    matches = list(RE_SECTION_HEADING.finditer(content or ""))
    for index, match in enumerate(matches):
        section_name = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        section_body = (content[start:end] or "").strip()
        sections.append((section_name, section_body))
    return sections


def _parse_special_user_blocks(section_body: str) -> tuple[dict[str, str], str]:
    """Parse ### username blocks from a User Context/Special Users section."""
    special_users = {}
    orphan_lines = []
    current_user = None
    current_context = []

    for raw_line in (section_body or "").splitlines():
        line = raw_line.rstrip()
        if line.startswith("### "):
            if current_user:
                special_users[current_user] = "\n".join(current_context).strip()
            current_user = line[4:].strip()
            current_context = []
            continue

        if current_user:
            current_context.append(line)
        elif line.strip():
            orphan_lines.append(line)

    if current_user:
        special_users[current_user] = "\n".join(current_context).strip()

    cleaned_users = {
        user_name: context.strip()
        for user_name, context in special_users.items()
        if user_name and context.strip()
    }
    orphan_content = "\n".join(orphan_lines).strip()
    return cleaned_users, orphan_content


def _parse_other_prompt_sections(content: str) -> dict[str, str]:
    """Parse named post-system prompt sections from other_prompts.md."""
    sections = {}
    for section_name, section_body in _extract_markdown_sections(content or ""):
        normalized_name = _normalize_section_name(section_name)
        canonical_name = OTHER_PROMPT_SECTION_ALIASES.get(normalized_name)
        if canonical_name:
            sections[canonical_name] = section_body.strip()
    return sections


def parse_character_content(name: str, content: str) -> "Character":
    """Parse a character markdown file into a Character object."""
    char_name = name.title()
    title_match = RE_TITLE.search(content or "")
    if title_match:
        char_name = title_match.group(1).strip()

    persona = ""
    example_dialogue = ""
    special_users: dict[str, str] = {}
    unused_sections: dict[str, str] = {}
    saw_explicit_section = False
    saw_legacy_section = False

    for section_name, section_body in _extract_markdown_sections(content or ""):
        normalized_name = _normalize_section_name(section_name)
        canonical_name = SECTION_ALIASES.get(normalized_name)

        if normalized_name in {"system persona", "user context"}:
            saw_explicit_section = True
        elif normalized_name in {"persona", "special user", "special users"}:
            saw_legacy_section = True

        if canonical_name == "persona":
            persona = f"{persona}\n\n{section_body}".strip() if persona and section_body else (section_body or persona).strip()
        elif canonical_name == "example_dialogue":
            example_dialogue = (
                f"{example_dialogue}\n\n{section_body}".strip()
                if example_dialogue and section_body else
                (section_body or example_dialogue).strip()
            )
        elif canonical_name == "special_users":
            parsed_users, orphan_content = _parse_special_user_blocks(section_body)
            for user_name, user_context in parsed_users.items():
                if user_name in special_users:
                    special_users[user_name] = f"{special_users[user_name]}\n\n{user_context}".strip()
                else:
                    special_users[user_name] = user_context
            if orphan_content:
                unused_sections[f"{section_name} (ungated)"] = orphan_content
        elif section_body:
            unused_sections[section_name] = section_body

    if saw_explicit_section and saw_legacy_section:
        schema_format = "mixed"
    elif saw_explicit_section:
        schema_format = "explicit"
    else:
        schema_format = "legacy"

    return Character(
        char_name,
        persona,
        special_users,
        example_dialogue,
        unused_sections=unused_sections,
        schema_format=schema_format,
    )


class PromptManager:
    """Manages prompt templates."""
    
    def __init__(self):
        self.system_template = ""
        self.chatroom_context_template = ""
        self.other_prompt_templates: dict[str, str] = {}
        self._load_templates()
    
    def _load_templates(self):
        """Load prompt templates from files."""
        prompts_path = os.path.join(os.path.dirname(__file__), PROMPTS_DIR)
        
        # Load system template (character section only)
        system_path = os.path.join(prompts_path, "system.md")
        if os.path.exists(system_path):
            with open(system_path, 'r', encoding='utf-8') as f:
                self.system_template = f.read()

        # Load post-system conversation prompt templates.
        default_sections = _parse_other_prompt_sections(DEFAULT_OTHER_PROMPTS)
        other_prompts_path = os.path.join(prompts_path, OTHER_PROMPTS_FILE)
        loaded_sections = {}
        if os.path.exists(other_prompts_path):
            with open(other_prompts_path, 'r', encoding='utf-8') as f:
                loaded_sections = _parse_other_prompt_sections(f.read())

        self.other_prompt_templates = {**default_sections, **loaded_sections}

        # Compatibility fallback for older installs that only have chatroom_context.md.
        context_path = os.path.join(prompts_path, LEGACY_CHATROOM_CONTEXT_FILE)
        if os.path.exists(context_path):
            with open(context_path, 'r', encoding='utf-8') as f:
                self.chatroom_context_template = f.read()
        else:
            self.chatroom_context_template = self.other_prompt_templates.get("chatroom_context", "")
        self._loaded_chatroom_context_template = self.chatroom_context_template

        if "chatroom_context" not in loaded_sections and self.chatroom_context_template:
            self.other_prompt_templates["chatroom_context"] = self.chatroom_context_template
    
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
            "persona": f"<character_persona>\n{persona}\n</character_persona>" if persona else "",
            "special_user_context": f"<special_context>\n{special_user_context}\n</special_context>" if special_user_context else "",
            "example_dialogue": f"<example_dialogue>\n{example_dialogue}\n</example_dialogue>" if example_dialogue else "",
            "current_time_context": _format_current_time_context(now),
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
        time_passage_context: str = "",
        now: Optional[datetime] = None
    ) -> str:
        """Build chatroom context (injected between history and immediate messages)."""

        # Start with template
        context = self.other_prompt_templates.get("chatroom_context") or self.chatroom_context_template
        if (
            self.chatroom_context_template
            and self.chatroom_context_template != getattr(self, "_loaded_chatroom_context_template", "")
        ):
            context = self.chatroom_context_template

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
            "time_passage_context": time_passage_context,
            "current_time_context": _format_current_time_context(now),
        }

        context = _render_template_variables(context, replacements, now=now)

        # Clean up empty lines from unused placeholders
        context = RE_EMPTY_LINES.sub('\n\n', context)

        return context.strip()

    def build_other_prompt(
        self,
        section_name: str,
        replacements: Dict[str, str] | None = None,
        now: Optional[datetime] = None
    ) -> str:
        """Render one named post-system prompt section."""
        template = self.other_prompt_templates.get(section_name, "")
        context = _render_template_variables(template, replacements or {}, now=now)
        context = RE_EMPTY_LINES.sub('\n\n', context)
        return context.strip()

    def build_time_passage_context(
        self,
        signal: dict | None,
        *,
        is_dm: bool = False,
        now: Optional[datetime] = None
    ) -> str:
        """Render a lightweight elapsed-time cue for post-system context."""
        if not signal:
            return ""

        replacements = {
            "gap_label": signal.get("gap_label", ""),
            "conversation_kind": "DM" if is_dm else "channel conversation",
            "before_author": signal.get("before_author", "Someone"),
            "before_content": signal.get("before_content", ""),
            "after_author": signal.get("after_author", "Someone"),
            "after_content": signal.get("after_content", ""),
        }
        return self.build_other_prompt("time_passage_context", replacements, now=now)

    def build_reminder_delivery_context(
        self,
        *,
        event_summary: str,
        reminder_stage: str,
        reminder_time: str,
        user_name: str,
        now: Optional[datetime] = None
    ) -> str:
        """Render post-system context for in-character reminder delivery."""
        return self.build_other_prompt(
            "reminder_delivery_context",
            {
                "event_summary": event_summary,
                "reminder_stage": reminder_stage,
                "reminder_time": reminder_time,
                "user_name": user_name,
            },
            now=now,
        )

    def build_reminder_clarification_prompt(
        self,
        *,
        event_summary: str,
        clarification_prompt: str,
        now: Optional[datetime] = None
    ) -> str:
        """Render the user-visible reminder clarification instruction."""
        return self.build_other_prompt(
            "reminder_clarification",
            {
                "event_summary": event_summary or "Unknown event",
                "clarification_prompt": clarification_prompt or "the timing",
            },
            now=now,
        )

    def build_dm_followup_prompt(
        self,
        *,
        character_name: str,
        user_name: str,
        idle_hours: float,
        recent_conversation: str,
        recent_topic: str,
        memories_excerpt: str,
        rules: str,
        time_passage_context: str = "",
        now: Optional[datetime] = None
    ) -> str:
        """Render post-system prompt for autonomous DM follow-ups."""
        return self.build_other_prompt(
            "dm_followup",
            {
                "character_name": character_name,
                "user_name": user_name or "The user",
                "idle_hours": f"{idle_hours:.1f}",
                "recent_conversation": recent_conversation,
                "recent_topic": recent_topic,
                "memories_excerpt": memories_excerpt,
                "rules": rules,
                "time_passage_context": time_passage_context,
            },
            now=now,
        )


class Character:
    """Represents a loaded character definition."""

    def __init__(
        self,
        name: str,
        persona: str,
        special_users: Dict[str, str] = None,
        example_dialogue: str = "",
        *,
        unused_sections: Dict[str, str] = None,
        schema_format: str = "legacy",
    ):
        self.name = name
        self.persona = persona
        self.special_users = special_users or {}
        self.example_dialogue = example_dialogue
        self.unused_sections = unused_sections or {}
        self.schema_format = schema_format

    def match_special_user_context(self, user_name: str) -> tuple[str | None, str]:
        """Return the matched special-user key and content for a display name."""
        if not user_name:
            return None, ""

        # Exact match first
        if user_name in self.special_users:
            return user_name, self.special_users[user_name]

        user_lower = user_name.lower()
        for special_name, context in self.special_users.items():
            special_lower = special_name.lower()

            # Check if display name starts with special name + separator
            # "Kris WaWa" matches "Kris", "Kris_Alt" matches "Kris"
            if (user_lower.startswith(special_lower + " ") or
                user_lower.startswith(special_lower + "_") or
                user_lower == special_lower):
                return special_name, context

            # Check if special name appears as a complete word
            # Handles "The Real Kris" matching "Kris"
            if re.search(rf'\b{re.escape(special_lower)}\b', user_lower):
                return special_name, context

        return None, ""

    def get_special_user_context(self, user_name: str) -> str:
        """Get special context for a user with fuzzy matching for Discord display names."""
        return self.match_special_user_context(user_name)[1]

    def get_preview_data(self, user_name: str) -> dict:
        """Return structured preview data for dashboard gating and schema visibility."""
        matched_user, _ = self.match_special_user_context(user_name)
        return {
            "always_injected": [
                {
                    "label": "System Persona",
                    "tag": "character_persona",
                    "included": bool(self.persona.strip()),
                    "content": self.persona,
                },
                {
                    "label": "Example Dialogue",
                    "tag": "example_dialogue",
                    "included": bool(self.example_dialogue.strip()),
                    "content": self.example_dialogue,
                },
            ],
            "conditional_user_contexts": [
                {
                    "label": special_name,
                    "tag": "special_context",
                    "included": matched_user == special_name,
                    "content": context,
                }
                for special_name, context in self.special_users.items()
            ],
            "unused_sections": [
                {"label": section_name, "content": section_body}
                for section_name, section_body in self.unused_sections.items()
            ],
            "matched_user_context": matched_user,
        }


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

        character = parse_character_content(name, content)
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
        time_passage_context: str = "",
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
            time_passage_context=time_passage_context,
            now=now
        )

    def build_time_passage_context(self, signal: dict | None, *, is_dm: bool = False, now: Optional[datetime] = None) -> str:
        return self.prompt_manager.build_time_passage_context(signal, is_dm=is_dm, now=now)

    def build_reminder_delivery_context(self, **kwargs) -> str:
        return self.prompt_manager.build_reminder_delivery_context(**kwargs)

    def build_reminder_clarification_prompt(self, **kwargs) -> str:
        return self.prompt_manager.build_reminder_clarification_prompt(**kwargs)

    def build_dm_followup_prompt(self, **kwargs) -> str:
        return self.prompt_manager.build_dm_followup_prompt(**kwargs)


# Global instance
character_manager = CharacterManager()
