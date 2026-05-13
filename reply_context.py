"""Helpers for compact Discord reply-thread context."""

from discord_utils import resolve_discord_formatting, sanitize_discord_syntax_fallback, strip_discord_ooc_comments

CURRENT_BOT_REPLY_ANCHOR_LIMIT = 360


def summarize_reply_content(content: str, limit: int = 160) -> str:
    """Flatten referenced message content for compact reply context."""
    summarized = " ".join((content or "").split())
    if len(summarized) <= limit:
        return summarized
    return summarized[: limit - 3].rstrip() + "..."


def neutral_reply_reference(author_name: str) -> str:
    """Return an attribution-only Discord reply marker."""
    safe_author = " ".join(str(author_name or "another bot").split())
    return f"[Replying to {safe_author}'s message]"


def neutral_bot_event(author_name: str, event: str = "sent a message") -> str:
    """Return an attribution-only bot event marker."""
    safe_author = " ".join(str(author_name or "another bot").split())
    return f"[{safe_author} {event}]"


def is_current_bot_author(author, bot_user) -> bool:
    """Return True when a Discord author object is the active bot user."""
    if author is None or bot_user is None:
        return False
    author_id = getattr(author, "id", None)
    bot_id = getattr(bot_user, "id", None)
    if author_id is not None and bot_id is not None:
        return author_id == bot_id
    return author == bot_user


def current_bot_reply_anchor(previous_message: str | None) -> dict:
    """Build a model-visible anchor for direct replies to the current bot."""
    previous_message = summarize_reply_content(
        previous_message or "",
        limit=CURRENT_BOT_REPLY_ANCHOR_LIMIT,
    )
    content = (
        "The current user is replying directly to your previous Discord message. "
        "Treat their newest message as a follow-up to "
    )
    if previous_message:
        content += (
            "this prior answer; do not restart or re-answer older requests unless the user asks. "
            f"Your previous message: \"{previous_message}\""
        )
    else:
        content += "that prior answer; do not restart or re-answer older requests unless the user asks."

    return {"role": "system", "content": content, "kind": "current_bot_reply_anchor"}


async def build_current_bot_reply_anchor(
    *,
    message,
    guild,
    bot_user,
    current_message_is_bot: bool,
    resolve_reference,
) -> dict | None:
    """Expose one capped current-bot reply target so follow-ups do not restart old context."""
    if current_message_is_bot or not getattr(message, "reference", None):
        return None

    referenced_message = await resolve_reference(message)
    if not referenced_message or not is_current_bot_author(getattr(referenced_message, "author", None), bot_user):
        return None

    referenced_content = strip_discord_ooc_comments(getattr(referenced_message, "content", "") or "").strip()
    if not referenced_content:
        return current_bot_reply_anchor(None)

    if guild:
        referenced_content = resolve_discord_formatting(
            referenced_content,
            guild,
            mentioned_users=getattr(referenced_message, "mentions", None),
            mentioned_channels=getattr(referenced_message, "channel_mentions", None),
            mentioned_roles=getattr(referenced_message, "role_mentions", None),
        )
    else:
        referenced_content = sanitize_discord_syntax_fallback(referenced_content)
    return current_bot_reply_anchor(referenced_content)
