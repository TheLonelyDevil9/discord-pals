"""Identity isolation policy shared by context building and generation guards."""

from __future__ import annotations

import re
from dataclasses import dataclass


IDENTITY_GUARD_RETRY_INSTRUCTION = (
    "Your previous draft was blocked because it structurally attributed speech "
    "or action to another participant. Reply only as the current character. Do not "
    "write another participant's dialogue, name-prefixed turn, or roleplay action."
)


@dataclass(frozen=True)
class IdentityViolation:
    """A structural attempt to speak as another participant."""

    name: str
    pattern: str
    preview: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "pattern": self.pattern,
            "preview": self.preview,
        }


class IdentityPolicy:
    """Owns bot identity wording and post-generation identity checks."""

    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled

    @staticmethod
    def current_speaker_context(
        *,
        speaker_name: str,
        speaker_is_bot: bool,
        target_user_name: str | None = None,
        direct_target_name: str | None = None,
        other_participant_names: list[str] | None = None,
    ) -> str:
        """Return code-owned context that anchors who the bot is answering now."""
        safe_speaker = " ".join(str(speaker_name or "Unknown").split())
        safe_target = " ".join(str(target_user_name or safe_speaker).split())
        safe_direct_target = " ".join(str(direct_target_name or "").split())

        def third_party_clause(addressed: str) -> str:
            others = []
            for name in other_participant_names or []:
                cleaned = " ".join(str(name or "").split())
                if cleaned and cleaned != addressed and cleaned not in others:
                    others.append(cleaned)
            others = others[:4]
            if others:
                return (
                    f" Only {addressed} is \"you\"; other participants such as {', '.join(others)} "
                    "are third parties - refer to them by name, never as \"you\"."
                )
            return f" Only {addressed} is \"you\"; everyone else in the transcript is a third party."

        if speaker_is_bot:
            return (
                f"Current Discord event author: {safe_speaker} (bot/app). "
                "Use this only as routing context unless explicitly instructed otherwise."
            )
        if safe_direct_target and safe_direct_target != safe_speaker:
            return (
                f"Current Discord message author: {safe_speaker}. "
                f"This split reply is addressed to {safe_direct_target}; address {safe_direct_target} directly as \"you\". "
                "Earlier third-person lines about the addressed user do not change who this split reply addresses."
                + third_party_clause(safe_direct_target)
            )
        return (
            f"Current Discord message author: {safe_speaker}. "
            f"The reply is addressed to {safe_target}; address {safe_target} directly as \"you\". "
            "Earlier third-person lines about the addressed user do not change who this reply addresses."
            + third_party_clause(safe_target)
        )

    def detect_violation(
        self,
        response: str,
        other_bot_names: list[str],
        human_participant_names: list[str] | None = None,
    ) -> dict | None:
        """Detect structural attempts to speak as another participant using exact names only.

        Covers both other bot characters and known human participants: a
        name-prefixed turn or narrated roleplay speech for either is a
        structural misattribution the current character must not produce.
        """
        if not self.enabled:
            return None
        candidates = list(other_bot_names or []) + list(human_participant_names or [])
        if not response or not candidates:
            return None

        seen: set[str] = set()
        for raw_name in candidates:
            name = str(raw_name or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            escaped = re.escape(name)
            patterns = (
                ("speaker_prefix", rf"(?im)^\s*(?:\[{escaped}\]|\*?{escaped}\*?)\s*:"),
                ("roleplay_speech", rf"(?im)^\s*\*+\s*\b{escaped}\b\s+(?:says?|whispers?|replies?|responds?|asks?|shouts?|murmurs?|mutters?)\b[^\n]*\*+\s*$"),
            )
            for pattern_name, pattern in patterns:
                match = re.search(pattern, response)
                if match:
                    return IdentityViolation(
                        name=name,
                        pattern=pattern_name,
                        preview=response[:160],
                    ).to_dict()
        return None
