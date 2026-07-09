"""Code-owned speaker attribution for model-facing history rendering.

Every surface that renders stored history into model context builds its
speaker prefixes here so the format stays identical across the role-based
path, the single-user flatten, /interact isolation, and extraction prompts.
Rendered turns carry ``attributed: True`` so downstream formatters never
have to sniff content for an existing ``Name:`` prefix.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

DEFAULT_USER_FALLBACK = "User"
DEFAULT_ASSISTANT_FALLBACK = "Assistant"
_MAX_AUTHOR_LEN = 64

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
# Column-0 "Name:" shapes on continuation lines read as turn boundaries.
# Leading \w covers unicode display names; the tail allows the separators
# Discord display names actually use.
_SPEAKER_LOOKALIKE_RE = re.compile(r"^(\w[\w .\-']{0,31}?):(\s|$)")


def normalize_author_name(author: Any, fallback: str = DEFAULT_USER_FALLBACK) -> str:
    """Collapse whitespace and trailing colons so the prefix stays one token."""
    name = " ".join(str(author or "").split())
    name = name.rstrip(":").strip()
    if not name:
        return fallback
    return name[:_MAX_AUTHOR_LEN]


def sanitize_speaker_lookalikes(content: str) -> str:
    """Neutralize column-0 ``Name:`` shapes on continuation lines of one message.

    A rendered turn is ``Author: content``. Any continuation line that itself
    starts with a name-colon shape (quoted dialogue, pasted logs, deliberate
    impersonation) would read as a new turn to the model, so the colon becomes
    an em dash. The first line is glued behind the real author prefix and
    never starts a line, so it stays untouched; fenced code blocks are also
    left alone.
    """
    content = str(content or "")
    if "\n" not in content:
        return content

    lines = content.split("\n")
    out = [lines[0]]
    in_fence = False
    for line in lines[1:]:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            line = _SPEAKER_LOOKALIKE_RE.sub(
                lambda m: f"{m.group(1)} —{m.group(2)}" if m.group(2) else f"{m.group(1)} —",
                line,
                count=1,
            )
        out.append(line)
    return "\n".join(out)


def render_attributed_content(
    author: Any,
    content: Any,
    *,
    fallback: str = DEFAULT_USER_FALLBACK,
) -> str:
    """Render one canonical ``Author: content`` turn with a sanitized body."""
    name = normalize_author_name(author, fallback)
    return f"{name}: {sanitize_speaker_lookalikes(str(content or ''))}"


def canonical_author_map(history: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    """Map human user_ids to one current display name for the rendered window.

    The most recent name per user_id wins, so a user who renamed mid-history
    appears under a single name. Distinct users sharing a display name get a
    deterministic numeric suffix in first-appearance order.
    """
    latest: dict[int, str] = {}
    order: list[int] = []
    for msg in history:
        if msg.get("role") != "user" or msg.get("is_bot"):
            continue
        uid = msg.get("user_id")
        author = normalize_author_name(msg.get("author"), "")
        if not uid or not author:
            continue
        if uid not in latest:
            order.append(uid)
        latest[uid] = author

    seen: dict[str, int] = {}
    result: dict[int, str] = {}
    for uid in order:
        name = latest[uid]
        key = name.lower()
        seen[key] = seen.get(key, 0) + 1
        result[uid] = name if seen[key] == 1 else f"{name} ({seen[key]})"
    return result


def resolve_author(
    msg: Mapping[str, Any],
    canonical_map: Mapping[int, str] | None = None,
) -> str:
    """Return the canonical author name for one stored history entry."""
    uid = msg.get("user_id")
    if canonical_map and uid in canonical_map:
        return canonical_map[uid]
    return normalize_author_name(msg.get("author"), "")
