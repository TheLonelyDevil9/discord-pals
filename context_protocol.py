"""
Discord Pals - Context Protocol
Deterministic context envelope and mention-handle utilities.
"""

import json
import re
from typing import Dict, List, Optional, Tuple


def _to_int(value) -> Optional[int]:
    """Best-effort integer conversion."""
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_alias(value: str) -> str:
    """Normalize mention aliases for stable matching."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip().lstrip("@"))


def _dedupe_aliases(values: List[str]) -> List[str]:
    """Case-insensitive alias dedupe while preserving order."""
    aliases = []
    seen = set()
    for value in values:
        alias = _normalize_alias(value)
        if not alias:
            continue
        key = alias.lower()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(alias)
    return aliases


def _build_user_handle(user_id: int) -> str:
    return f"@u_{user_id}"


def _build_bot_handle(user_id: int) -> str:
    return f"@b_{user_id}"


def attach_protocol_handles(
    mentionable_users: Optional[list] = None,
    mentionable_bots: Optional[list] = None
) -> Tuple[list, list]:
    """Attach deterministic protocol handles and aliases to mentionable entries."""
    users_out = []
    bots_out = []

    for user in mentionable_users or []:
        entry = dict(user)
        user_id = _to_int(entry.get("user_id"))
        aliases = list(entry.get("aliases") or [])
        aliases.extend([entry.get("name"), entry.get("username")])

        if user_id:
            handle = _build_user_handle(user_id)
            entry["handle"] = handle
            aliases.append(handle)

        entry["aliases"] = _dedupe_aliases(aliases)
        users_out.append(entry)

    for bot in mentionable_bots or []:
        entry = dict(bot)
        user_id = _to_int(entry.get("user_id"))
        aliases = list(entry.get("aliases") or [])
        aliases.extend([entry.get("character_name"), entry.get("name")])

        if user_id:
            handle = _build_bot_handle(user_id)
            entry["handle"] = handle
            aliases.append(handle)

        entry["aliases"] = _dedupe_aliases(aliases)
        bots_out.append(entry)

    return users_out, bots_out


def build_context_envelope(
    channel_id: int,
    guild_name: str,
    reply_target_user_id: Optional[int],
    reply_target_name: str,
    current_bot_name: str,
    mentionable_users: Optional[list] = None,
    mentionable_bots: Optional[list] = None,
    history_messages: Optional[list] = None,
    active_users: Optional[list] = None,
    max_recent_messages: int = 20
) -> dict:
    """Build a deterministic context envelope for multi-participant chat."""
    participants: Dict[int, dict] = {}
    mention_candidates: List[dict] = []
    alias_to_user_id: Dict[str, int] = {}

    for user in mentionable_users or []:
        user_id = _to_int(user.get("user_id"))
        if not user_id:
            continue
        handle = user.get("handle") or _build_user_handle(user_id)
        aliases = _dedupe_aliases(list(user.get("aliases") or []) + [user.get("name"), user.get("username")])
        participants[user_id] = {
            "user_id": user_id,
            "display_name": user.get("name") or user.get("username") or str(user_id),
            "username": user.get("username") or "",
            "is_bot": False,
            "mention_handle": handle
        }
        mention_candidates.append({
            "handle": handle,
            "user_id": user_id,
            "aliases": aliases,
            "priority": "user"
        })
        for alias in aliases:
            alias_to_user_id.setdefault(alias.lower(), user_id)

    for bot in mentionable_bots or []:
        user_id = _to_int(bot.get("user_id"))
        if not user_id:
            continue
        handle = bot.get("handle") or _build_bot_handle(user_id)
        aliases = _dedupe_aliases(list(bot.get("aliases") or []) + [bot.get("character_name"), bot.get("name")])
        participants[user_id] = {
            "user_id": user_id,
            "display_name": bot.get("character_name") or bot.get("name") or str(user_id),
            "username": bot.get("name") or "",
            "is_bot": True,
            "mention_handle": handle
        }
        mention_candidates.append({
            "handle": handle,
            "user_id": user_id,
            "aliases": aliases,
            "priority": "bot"
        })
        for alias in aliases:
            alias_to_user_id.setdefault(alias.lower(), user_id)

    target_id = _to_int(reply_target_user_id)
    if target_id and target_id not in participants:
        handle = _build_user_handle(target_id)
        participants[target_id] = {
            "user_id": target_id,
            "display_name": reply_target_name or str(target_id),
            "username": "",
            "is_bot": False,
            "mention_handle": handle
        }
        mention_candidates.append({
            "handle": handle,
            "user_id": target_id,
            "aliases": _dedupe_aliases([reply_target_name or "", handle]),
            "priority": "reply_target"
        })

    recent = []
    for raw in (history_messages or [])[-max_recent_messages:]:
        role = raw.get("role", "user")
        author_name = (raw.get("author") or "Unknown").strip()
        content = (raw.get("content") or "").strip()
        if not content:
            continue

        author_id = _to_int(raw.get("user_id"))
        if author_id is None and author_name:
            author_id = alias_to_user_id.get(author_name.lower())

        if role == "assistant" and author_name and current_bot_name and author_name.lower() != current_bot_name.lower():
            role = "user"

        entry = {
            "message_id": _to_int(raw.get("message_id")),
            "role": role,
            "author_id": author_id,
            "author_name": author_name,
            "content": content[:320],
            "reply_to_message_id": _to_int(raw.get("reply_to_message_id"))
        }
        recent.append(entry)

        if author_id and author_id not in participants:
            participants[author_id] = {
                "user_id": author_id,
                "display_name": author_name or str(author_id),
                "username": "",
                "is_bot": False,
                "mention_handle": _build_user_handle(author_id)
            }

    active_user_ids = []
    for active in active_users or []:
        active_norm = _normalize_alias(active).lower()
        if not active_norm:
            continue
        active_id = alias_to_user_id.get(active_norm)
        if active_id and active_id not in active_user_ids:
            active_user_ids.append(active_id)

    return {
        "version": "context_protocol_v1",
        "channel_id": channel_id,
        "reply_target": {
            "user_id": target_id,
            "display_name": reply_target_name or ""
        },
        "participants": sorted(participants.values(), key=lambda p: p.get("user_id") or 0),
        "mention_candidates": mention_candidates,
        "recent_messages": recent,
        "channel_facts": {
            "guild_name": guild_name,
            "active_user_ids": active_user_ids
        }
    }


def render_context_block(envelope: dict) -> str:
    """Render envelope into a compact deterministic system-context block."""
    if not envelope:
        return ""

    compact = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return (
        "Context Protocol v1:\n"
        "- Mention users with @u_<user_id>.\n"
        "- Mention bots with @b_<user_id>.\n"
        "- Never emit raw Discord syntax like <@...>.\n"
        "- If unsure who to tag, keep plaintext and continue.\n"
        f"CONTEXT_ENVELOPE_JSON:{compact}"
    )


def extract_and_resolve_mentions(text: str, envelope: Optional[dict], guild=None) -> str:
    """Resolve protocol mention handles to Discord mention syntax."""
    if not text or not envelope:
        return text

    lookup = {}
    alias_lookup: Dict[str, int] = {}
    for candidate in envelope.get("mention_candidates", []):
        handle = (candidate.get("handle") or "").strip()
        user_id = _to_int(candidate.get("user_id"))
        if handle and user_id:
            lookup[handle.lower()] = user_id
        if user_id:
            for alias in candidate.get("aliases") or []:
                alias_norm = _normalize_alias(alias).lower()
                if alias_norm:
                    alias_lookup.setdefault(alias_norm, user_id)
    for participant in envelope.get("participants", []):
        handle = (participant.get("mention_handle") or "").strip()
        user_id = _to_int(participant.get("user_id"))
        if handle and user_id and handle.lower() not in lookup:
            lookup[handle.lower()] = user_id
        if user_id:
            for alias_value in (
                participant.get("display_name"),
                participant.get("username"),
                participant.get("mention_handle"),
            ):
                alias_norm = _normalize_alias(str(alias_value or "")).lower()
                if alias_norm:
                    alias_lookup.setdefault(alias_norm, user_id)

    if not lookup and not alias_lookup:
        return text

    resolved = text

    # Convert bracketed protocol forms like "<@u_123>" first.
    def replace_bracketed(match: re.Match) -> str:
        handle = f"@{match.group(1)}_{match.group(2)}".lower()
        user_id = lookup.get(handle)
        return f"<@{user_id}>" if user_id else match.group(0)

    resolved = re.sub(r"<@([ub])_(\d{3,22})>", replace_bracketed, resolved, flags=re.IGNORECASE)

    # Convert plain or slightly malformed protocol handles, including
    # optional invisible characters after '@' that some models emit.
    def replace_plain(match: re.Match) -> str:
        handle = f"@{match.group(1)}_{match.group(2)}".lower()
        user_id = lookup.get(handle)
        return f"<@{user_id}>" if user_id else match.group(0)

    resolved = re.sub(
        r"@[\u200b\u2060\ufeff\s]*([ub])_(\d{3,22})(?=[^A-Za-z0-9_]|$)",
        replace_plain,
        resolved,
        flags=re.IGNORECASE
    )

    # Recover malformed non-numeric protocol handles such as "@u_seelewee".
    def replace_plain_alias(match: re.Match) -> str:
        raw_alias = (match.group(2) or "").strip()
        if not raw_alias:
            return ""

        candidate_aliases = [
            raw_alias,
            raw_alias.replace("_", " "),
            raw_alias.replace("-", " "),
            raw_alias.replace(".", " "),
        ]
        for alias in candidate_aliases:
            alias_norm = _normalize_alias(alias).lower()
            if not alias_norm:
                continue
            user_id = alias_lookup.get(alias_norm)
            if user_id:
                return f"<@{user_id}>"

        # Do not leak fake ping-like protocol text.
        return raw_alias

    resolved = re.sub(
        r"(?<![A-Za-z0-9_])@?[\u200b\u2060\ufeff\s]*([ub])_([A-Za-z][A-Za-z0-9_.\-']{1,63})(?=[^A-Za-z0-9_]|$)",
        replace_plain_alias,
        resolved,
        flags=re.IGNORECASE
    )

    # Convert exact plain protocol handles.
    for handle, user_id in sorted(lookup.items(), key=lambda item: len(item[0]), reverse=True):
        pattern = re.compile(
            r"(?<![A-Za-z0-9_])" + re.escape(handle) + r"(?=[^A-Za-z0-9_]|$)",
            re.IGNORECASE
        )
        resolved = pattern.sub(f"<@{user_id}>", resolved)

    return resolved
