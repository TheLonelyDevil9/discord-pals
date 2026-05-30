"""Typed request envelope for queued Discord response work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scopes import LocalScopeId, ScopeKey


def _coerce_local_scope_id(value: Any) -> LocalScopeId:
    """Preserve DM history strings while keeping numeric channel ids numeric."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
        return stripped
    return int(value)


@dataclass(frozen=True)
class RequestEnvelope:
    """Parsed queue payload with a legacy-dict adapter for current callers."""

    id: int
    correlation_id: str
    timestamp: float
    channel_id: LocalScopeId
    message: Any
    content: str
    content_stripped: str
    request_signature: tuple[str, int | None]
    guild: Any
    attachments: tuple[Any, ...]
    user_name: str
    is_dm: bool
    user_id: int
    scope_key: ScopeKey | None = None
    sticker_info: str | None = None
    from_interact_command: bool = False
    direct_target: Any = None
    forced_target_user_id: int | None = None
    forced_target_user_name: str | None = None
    allow_auto_reminders: bool = False
    pending_reminder_clarification: dict | None = None
    is_autonomous: bool = False
    dm_invite_requested: bool = False

    @property
    def req_id(self) -> str:
        return self.correlation_id

    @classmethod
    def from_legacy_dict(cls, request: dict) -> "RequestEnvelope":
        """Parse a legacy request dict into trusted internal shape."""
        return cls(
            id=int(request["id"]),
            correlation_id=str(request["req_id"]),
            timestamp=float(request["timestamp"]),
            channel_id=_coerce_local_scope_id(request["channel_id"]),
            scope_key=request.get("scope_key") if isinstance(request.get("scope_key"), ScopeKey) else None,
            message=request["message"],
            content=str(request.get("content") or ""),
            content_stripped=str(request.get("content_stripped") or ""),
            request_signature=tuple(request["request_signature"]),
            guild=request.get("guild"),
            attachments=tuple(request.get("attachments") or ()),
            user_name=str(request.get("user_name") or ""),
            is_dm=bool(request.get("is_dm", False)),
            user_id=int(request["user_id"]),
            sticker_info=request.get("sticker_info"),
            from_interact_command=bool(request.get("from_interact_command", False)),
            direct_target=request.get("split_reply_target") or request.get("direct_target"),
            forced_target_user_id=request.get("forced_target_user_id"),
            forced_target_user_name=request.get("forced_target_user_name"),
            allow_auto_reminders=bool(request.get("allow_auto_reminders", False)),
            pending_reminder_clarification=(
                dict(request["pending_reminder_clarification"])
                if request.get("pending_reminder_clarification")
                else None
            ),
            is_autonomous=bool(request.get("is_autonomous", False)),
            dm_invite_requested=bool(request.get("dm_invite_requested", False)),
        )

    def to_legacy_dict(self) -> dict:
        """Return the request shape expected by existing hot-path callers."""
        return {
            "id": self.id,
            "req_id": self.correlation_id,
            "timestamp": self.timestamp,
            "channel_id": self.channel_id,
            "scope_key": self.scope_key,
            "message": self.message,
            "content": self.content,
            "content_stripped": self.content_stripped,
            "request_signature": self.request_signature,
            "guild": self.guild,
            "attachments": list(self.attachments),
            "user_name": self.user_name,
            "is_dm": self.is_dm,
            "user_id": self.user_id,
            "sticker_info": self.sticker_info,
            "from_interact_command": self.from_interact_command,
            "split_reply_target": self.direct_target,
            "direct_target": self.direct_target,
            "forced_target_user_id": self.forced_target_user_id,
            "forced_target_user_name": self.forced_target_user_name,
            "allow_auto_reminders": self.allow_auto_reminders,
            "pending_reminder_clarification": (
                dict(self.pending_reminder_clarification)
                if self.pending_reminder_clarification
                else None
            ),
            "is_autonomous": self.is_autonomous,
            "dm_invite_requested": self.dm_invite_requested,
        }
