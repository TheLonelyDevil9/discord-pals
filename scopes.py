"""
Discord Pals - Local scope/key helpers.

Centralizes local identifiers used for histories, memories, stats, and
cross-runtime request context so bot/user/channel boundaries stay explicit.
"""

from dataclasses import dataclass
import hashlib
import re
from typing import Optional


LocalScopeId = int | str


def safe_scope_part(value: object, *, default: str = "default", limit: int = 48) -> str:
    """Return a compact JSON/file-key-safe scope segment."""
    raw = str(value or default).strip()
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-")
    if normalized:
        return normalized[:limit]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def channel_history_id(channel_id: int | str) -> int:
    """Return the local history ID for a Discord channel."""
    return int(channel_id)


def dm_history_id(bot_name: str | None, user_id: int | str) -> str:
    """Return the local history ID for one user's DM with one bot."""
    return f"dm:{safe_scope_part(bot_name)}:user:{int(user_id)}"


def conversation_history_id(bot_name: str | None, channel_id: int | str, *, is_dm: bool, user_id: int | str | None = None) -> LocalScopeId:
    """Return the correct local conversation history ID for channel or DM context."""
    if is_dm:
        if user_id is None:
            raise ValueError("user_id is required for DM history scopes")
        return dm_history_id(bot_name, user_id)
    return channel_history_id(channel_id)


def dm_memory_server_id(bot_name: str | None) -> str:
    """Return the auto-memory namespace for one bot's DMs."""
    return f"dm:bot:{safe_scope_part(bot_name)}"


def server_memory_id(guild_id: int | str) -> int:
    """Return the auto-memory namespace for one Discord server."""
    return int(guild_id)


def memory_server_id(bot_name: str | None, guild_id: int | str | None, *, is_dm: bool) -> LocalScopeId:
    """Return the memory server ID for a server channel or bot/user DM."""
    if is_dm:
        return dm_memory_server_id(bot_name)
    if guild_id is None:
        raise ValueError("guild_id is required for server memory scopes")
    return server_memory_id(guild_id)


def auto_memory_key(server_id: int | str, user_id: int | str) -> str:
    """Return the unified auto-memory key for server or DM memory scope."""
    if isinstance(server_id, str) and server_id.startswith("dm:"):
        return f"{server_id}:user:{int(user_id)}"
    normalized_server_id = int(server_id or 0)
    if normalized_server_id:
        return f"server:{normalized_server_id}:user:{int(user_id)}"
    return f"dm:0:user:{int(user_id)}"


def dm_auto_memory_key(bot_name: str | None, user_id: int | str) -> str:
    """Return the auto-memory key for one user's DM memories with one bot."""
    return auto_memory_key(dm_memory_server_id(bot_name), user_id)


def stats_channel_id(channel_id: int | str) -> int:
    """Return the Discord channel ID used for aggregate stats."""
    return int(channel_id)


def channel_display_label(channel_name: str | None, guild_name: str | None = None, *, is_dm: bool = False) -> str:
    """Return a readable label for dashboard/history/stat displays."""
    if is_dm:
        return "DM"
    name = (channel_name or "unknown").strip() or "unknown"
    if guild_name:
        return f"#{name} ({guild_name})"
    return name if name.startswith("#") else f"#{name}"


@dataclass(frozen=True)
class MemoryScope:
    """Typed memory scope for server or DM context."""

    server_id: LocalScopeId
    user_id: int

    @property
    def auto_key(self) -> str:
        return auto_memory_key(self.server_id, self.user_id)


@dataclass(frozen=True)
class DeliveryTarget:
    """Typed target for outgoing Discord delivery."""

    channel_id: int
    history_id: LocalScopeId
    is_dm: bool = False


@dataclass(frozen=True)
class RequestContext:
    """Typed context values that must remain aligned during request handling."""

    bot_name: str
    user_id: int
    user_name: str
    discord_channel_id: int
    history_id: LocalScopeId
    memory_scope: MemoryScope
    display_label: str
    is_dm: bool = False
    guild_id: Optional[int] = None

