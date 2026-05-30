"""Typed ingress and trigger decisions for Discord message routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AttachmentMetadata:
    """Raw attachment facts captured at ingress."""

    id: int | None
    filename: str
    content_type: str | None
    size: int | None

    @classmethod
    def from_discord(cls, attachment: Any) -> "AttachmentMetadata":
        return cls(
            id=getattr(attachment, "id", None),
            filename=str(getattr(attachment, "filename", "") or ""),
            content_type=getattr(attachment, "content_type", None),
            size=getattr(attachment, "size", None),
        )


@dataclass(frozen=True)
class InboundMessage:
    """Raw Discord event facts only, with no model-context decisions."""

    correlation_id: str
    message_id: int | None
    channel_id: int | None
    guild_id: int | None
    user_id: int | None
    author_name: str
    author_is_bot: bool
    content: str
    is_dm: bool
    attachments: tuple[AttachmentMetadata, ...]
    mention_count: int
    reply_message_id: int | None

    @classmethod
    def from_discord(
        cls,
        message: Any,
        *,
        correlation_id: str,
        is_dm: bool,
        author_name: str | None = None,
    ) -> "InboundMessage":
        author = getattr(message, "author", None)
        channel = getattr(message, "channel", None)
        guild = getattr(message, "guild", None)
        reference = getattr(message, "reference", None)
        attachments = tuple(
            AttachmentMetadata.from_discord(attachment)
            for attachment in (getattr(message, "attachments", None) or ())
        )
        resolved_author_name = author_name
        if resolved_author_name is None:
            resolved_author_name = (
                getattr(author, "display_name", None)
                or getattr(author, "name", None)
                or "Unknown"
            )
        return cls(
            correlation_id=correlation_id,
            message_id=getattr(message, "id", None),
            channel_id=getattr(channel, "id", None),
            guild_id=getattr(guild, "id", None),
            user_id=getattr(author, "id", None),
            author_name=str(resolved_author_name or "Unknown"),
            author_is_bot=bool(getattr(author, "bot", False)),
            content=str(getattr(message, "content", "") or ""),
            is_dm=bool(is_dm),
            attachments=attachments,
            mention_count=len(getattr(message, "mentions", None) or ()),
            reply_message_id=getattr(reference, "message_id", None),
        )

    @property
    def content_len(self) -> int:
        return len(self.content)

    @property
    def attachment_count(self) -> int:
        return len(self.attachments)


@dataclass(frozen=True)
class TriggerDecision:
    """Typed response decision built from the legacy trigger detector."""

    mentioned: bool = False
    is_reply_to_bot: bool = False
    is_autonomous: bool = False
    name_triggered: bool = False
    pending_reminder_clarification: bool = False
    should_respond: bool = False

    @classmethod
    def from_legacy(cls, triggers: dict) -> "TriggerDecision":
        return cls(
            mentioned=bool(triggers.get("mentioned", False)),
            is_reply_to_bot=bool(triggers.get("is_reply_to_bot", False)),
            is_autonomous=bool(triggers.get("is_autonomous", False)),
            name_triggered=bool(triggers.get("name_triggered", False)),
            pending_reminder_clarification=bool(triggers.get("pending_reminder_clarification", False)),
            should_respond=bool(triggers.get("should_respond", False)),
        )

    def with_pending_reminder_clarification(self) -> "TriggerDecision":
        return TriggerDecision(
            mentioned=self.mentioned,
            is_reply_to_bot=self.is_reply_to_bot,
            is_autonomous=self.is_autonomous,
            name_triggered=self.name_triggered,
            pending_reminder_clarification=True,
            should_respond=True,
        )

    def to_legacy_dict(self) -> dict:
        return {
            "mentioned": self.mentioned,
            "is_reply_to_bot": self.is_reply_to_bot,
            "is_autonomous": self.is_autonomous,
            "name_triggered": self.name_triggered,
            "pending_reminder_clarification": self.pending_reminder_clarification,
            "should_respond": self.should_respond,
        }

    @property
    def reason_keys(self) -> list[str]:
        return [
            key
            for key, value in self.to_legacy_dict().items()
            if value and key != "should_respond"
        ]

    def allows_auto_reminders(self, *, is_dm: bool) -> bool:
        return (
            bool(is_dm)
            or self.mentioned
            or self.is_reply_to_bot
            or self.name_triggered
            or self.pending_reminder_clarification
        )
