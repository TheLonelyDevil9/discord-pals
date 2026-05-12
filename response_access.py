"""Runtime response access policy helpers."""

from __future__ import annotations

import logger as log
import runtime_config


_EMPTY_TRIGGERS = {
    "mentioned": False,
    "is_reply_to_bot": False,
    "is_autonomous": False,
    "name_triggered": False,
    "should_respond": False,
}


def empty_triggers() -> dict:
    """Return a fresh no-response trigger result."""
    return dict(_EMPTY_TRIGGERS)


def message_access(is_dm: bool, user_id, channel_id) -> tuple[bool, str | None]:
    """Check access for one Discord message location."""
    if is_dm:
        return runtime_config.is_dm_response_allowed(user_id)
    return runtime_config.is_server_response_allowed(channel_id)


def request_access(request: dict, message=None) -> tuple[bool, str | None, object, object]:
    """Check queued request access and return location fields for diagnostics."""
    channel_id = getattr(getattr(message, "channel", None), "id", request.get("channel_id"))
    user_id = request.get("user_id")
    allowed, reason = message_access(bool(request.get("is_dm")), user_id, channel_id)
    return allowed, reason, channel_id, user_id


def log_debug_skip(
    bot_name: str,
    message: str,
    *,
    component: str,
    event: str,
    reason: str | None,
    req_id: str | None = None,
    channel_id=None,
    user_id=None,
) -> None:
    """Log a blocked response access decision with consistent fields."""
    log.debug(
        f"{message}: {reason}",
        bot_name,
        component=component,
        event=event,
        req_id=req_id,
        reason=reason,
        channel_id=channel_id,
        user_id=user_id,
    )


def log_message_skip(bot_name: str, req_id: str, message, guild, is_dm: bool, channel_id, reason: str | None) -> None:
    """Log a routed Discord message skipped by response access policy."""
    log.diagnostic(
        "Message skipped by response access policy",
        bot_name,
        component="routing",
        event="message_skipped",
        req_id=req_id,
        channel_id=channel_id,
        guild_id=getattr(guild, "id", None),
        user_id=getattr(message.author, "id", None),
        message_id=getattr(message, "id", None),
        is_dm=is_dm,
        reason=reason,
    )


def log_if_dm_blocked(
    bot_name: str,
    user_id,
    action: str,
    *,
    component: str | None = None,
    event: str | None = None,
    req_id: str | None = None,
) -> bool:
    """Return True and log when DM response access is blocked for a user."""
    allowed, reason = runtime_config.is_dm_response_allowed(user_id)
    if allowed:
        return False
    if component and event:
        log_debug_skip(bot_name, f"{action} skipped by access policy", component=component, event=event, req_id=req_id, reason=reason, user_id=user_id)
    else:
        log.debug(f"{action} skipped for user {user_id}: {reason}", bot_name)
    return True


def log_if_server_blocked(bot_name: str, channel_id, action: str) -> bool:
    """Return True and log when server response access is blocked for a channel."""
    allowed, reason = runtime_config.is_server_response_allowed(channel_id)
    if allowed:
        return False
    log.debug(f"{action} skipped for channel {channel_id}: {reason}", bot_name)
    return True
