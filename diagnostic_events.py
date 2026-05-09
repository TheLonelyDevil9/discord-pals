"""
Structured diagnostic event helpers.

Keep verbose log-shaping code out of hot orchestration paths while preserving
the same local dashboard/file diagnostics.
"""

from __future__ import annotations

import logger as log


def log_delivery_split(bot_name: str, req_id: str | None, channel, lines: list[str], *, direct_channel: bool = False) -> None:
    """Record how a generated response was split before Discord sends."""
    log.diagnostic(
        "Channel response split for delivery" if direct_channel else "Response split for delivery",
        bot_name,
        component="delivery",
        event="delivery_split",
        req_id=req_id,
        channel_id=getattr(channel, "id", None),
        part_count=len(lines),
        part_lengths=[len(line) for line in lines],
    )


def log_discord_send(
    bot_name: str,
    req_id: str | None,
    channel,
    sent_msg,
    *,
    part: int,
    total_parts: int,
    content_len: int,
    latency_ms: int,
    direct_channel: bool = False,
) -> None:
    """Record one successful Discord message send."""
    log.diagnostic(
        "Discord channel message part sent" if direct_channel else "Discord message part sent",
        bot_name,
        component="delivery",
        event="discord_send",
        req_id=req_id,
        channel_id=getattr(channel, "id", None),
        message_id=getattr(sent_msg, "id", None),
        part=part,
        total_parts=total_parts,
        content_len=content_len,
        latency_ms=latency_ms,
    )


def log_discord_send_failed(
    bot_name: str,
    req_id: str | None,
    channel,
    error: Exception,
    *,
    part: int,
    total_parts: int,
) -> None:
    """Record a Discord send failure."""
    log.error(
        f"Failed to send: {error}",
        bot_name,
        component="delivery",
        event="discord_send_failed",
        req_id=req_id,
        channel_id=getattr(channel, "id", None),
        part=part,
        total_parts=total_parts,
    )


def log_mentions_processed(
    bot_name: str,
    req_id: str | None,
    channel_id: int,
    mentionable_users,
    mentionable_bots,
) -> None:
    """Record outgoing mention resolution inputs."""
    log.diagnostic(
        "Outgoing mentions processed",
        bot_name,
        component="delivery",
        event="mentions_processed",
        req_id=req_id,
        channel_id=channel_id,
        mentionable_users_count=len(mentionable_users or []),
        mentionable_bots_count=len(mentionable_bots or []),
    )


def log_delivery_complete(
    bot_name: str,
    req_id: str | None,
    *,
    channel_id: int,
    user_id: int,
    sent_records: list[dict],
    delivered_response: str,
    reactions: list,
    split_target,
) -> None:
    """Record successful delivery after history-safe Discord sends."""
    log.diagnostic(
        "Response delivery complete",
        bot_name,
        component="delivery",
        event="delivery_complete",
        req_id=req_id,
        channel_id=channel_id,
        user_id=user_id,
        parts_sent=len(sent_records),
        delivered_len=len(delivered_response),
        reaction_count=len(reactions),
        split_target_id=getattr(split_target, "id", None),
    )
