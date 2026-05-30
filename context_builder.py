"""Typed context identity helpers for queued Discord requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from scopes import (
    LocalScopeId,
    MemoryScope,
    RequestContext,
    ScopeKey,
    channel_display_label,
    conversation_history_id,
    memory_server_id,
)


@dataclass(frozen=True)
class RequestTarget:
    """Resolved speaker and target identity for one queued request."""

    author_user_id: int | None
    author_user_name: str
    target_user_id: int
    target_user_name: str
    direct_target: Any = None
    forced_target_user_id: int | None = None
    forced_target_user_name: str | None = None


class ContextBuilder:
    """Builds typed scope and target identities for request context construction."""

    def __init__(self, bot_name: str):
        self.bot_name = bot_name

    @staticmethod
    def _discord_channel_id(message: Any, request: dict | None = None) -> int:
        channel = getattr(message, "channel", None)
        channel_id = getattr(channel, "id", None)
        if channel_id is None and request is not None:
            channel_id = request.get("discord_channel_id") or request.get("channel_id")
        if channel_id is None:
            channel_id = id(channel)
        return int(channel_id)

    @staticmethod
    def _guild_id(message: Any, guild: Any = None) -> int | None:
        resolved_guild = guild if guild is not None else getattr(message, "guild", None)
        guild_id = getattr(resolved_guild, "id", None)
        return int(guild_id) if guild_id is not None else None

    def history_id_for_message(self, message: Any, *, is_dm: bool, user_id: int | None) -> LocalScopeId:
        discord_channel_id = self._discord_channel_id(message)
        return conversation_history_id(
            self.bot_name,
            discord_channel_id,
            is_dm=is_dm,
            user_id=user_id,
        )

    def resolve_target(
        self,
        request: dict,
        *,
        display_name_for: Callable[[Any], str],
    ) -> RequestTarget:
        author_user_id = request.get("user_id")
        author_user_name = str(request.get("user_name") or "")
        direct_target = request.get("split_reply_target") or request.get("direct_target")
        forced_target_user_id = request.get("forced_target_user_id")
        forced_target_user_name = request.get("forced_target_user_name")

        if forced_target_user_id is not None:
            target_user_id = int(forced_target_user_id)
            target_user_name = str(forced_target_user_name or author_user_name)
            direct_target = None
        elif direct_target is not None:
            target_user_id = int(getattr(direct_target, "id"))
            target_user_name = display_name_for(direct_target)
        else:
            target_user_id = int(author_user_id)
            target_user_name = author_user_name

        return RequestTarget(
            author_user_id=int(author_user_id) if author_user_id is not None else None,
            author_user_name=author_user_name,
            target_user_id=target_user_id,
            target_user_name=target_user_name,
            direct_target=direct_target,
            forced_target_user_id=int(forced_target_user_id) if forced_target_user_id is not None else None,
            forced_target_user_name=forced_target_user_name,
        )

    def build_request_scope(
        self,
        *,
        request: dict,
        message: Any,
        guild: Any,
        target: RequestTarget,
    ) -> RequestContext:
        is_dm = bool(request.get("is_dm", False))
        discord_channel_id = self._discord_channel_id(message, request)
        guild_id = self._guild_id(message, guild)
        history_id = conversation_history_id(
            self.bot_name,
            discord_channel_id,
            is_dm=is_dm,
            user_id=target.author_user_id,
        )
        return RequestContext(
            bot_name=self.bot_name,
            user_id=target.target_user_id,
            user_name=target.target_user_name,
            discord_channel_id=discord_channel_id,
            history_id=history_id,
            memory_scope=MemoryScope(
                server_id=memory_server_id(self.bot_name, guild_id, is_dm=is_dm),
                user_id=target.target_user_id,
            ),
            display_label=channel_display_label(
                getattr(getattr(message, "channel", None), "name", "DM"),
                getattr(guild, "name", None),
                is_dm=is_dm,
            ),
            is_dm=is_dm,
            guild_id=guild_id,
        )

    def scope_key_for_message(
        self,
        message: Any,
        *,
        is_dm: bool,
        user_id: int | None,
        guild: Any = None,
    ) -> ScopeKey:
        discord_channel_id = self._discord_channel_id(message)
        if is_dm:
            return ScopeKey.for_dm(
                bot_name=self.bot_name,
                channel_id=discord_channel_id,
                user_id=user_id or 0,
            )
        return ScopeKey.for_channel(
            bot_name=self.bot_name,
            channel_id=discord_channel_id,
            guild_id=self._guild_id(message, guild),
        )

    def scope_key_for_request(self, request: dict, message: Any | None = None) -> ScopeKey:
        existing = request.get("scope_key")
        if isinstance(existing, ScopeKey):
            return existing

        resolved_message = message if message is not None else request.get("message")
        is_dm = bool(request.get("is_dm", False))
        user_id = request.get("user_id")
        guild = request.get("guild") or getattr(resolved_message, "guild", None)
        return self.scope_key_for_message(
            resolved_message,
            is_dm=is_dm,
            user_id=user_id,
            guild=guild,
        )
