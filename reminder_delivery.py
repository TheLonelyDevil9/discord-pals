"""Scheduled reminder delivery helpers."""

from __future__ import annotations

import discord

import logger as log
import response_access
from discord_utils import add_to_history, convert_emojis_in_text, store_multipart_response


async def send_scheduled_reminder(bot, reminder: dict, response: str) -> tuple[list, int | None]:
    """Send a scheduled reminder in the original location or DM fallback."""
    response = response.strip()
    if not response:
        return [], None

    source_type = reminder.get("source_type")
    user_id = int(reminder.get("target_user_id"))
    source_channel_id = int(reminder.get("source_channel_id"))
    get_guild = getattr(bot.client, "get_guild", None)
    guild = get_guild(reminder.get("source_guild_id")) if callable(get_guild) and reminder.get("source_guild_id") else None

    primary_channel = None
    allow_dm_fallback = source_type != "dm"
    if source_type == "dm":
        if response_access.log_if_dm_blocked(bot.name, user_id, "Reminder DM delivery"):
            return [], None
        primary_channel = await bot._resolve_dm_followup_channel(user_id, source_channel_id)
    else:
        if response_access.log_if_server_blocked(bot.name, source_channel_id, "Reminder server delivery"):
            allow_dm_fallback = False
        else:
            primary_channel = await bot._resolve_channel(source_channel_id)

    if allow_dm_fallback and primary_channel is None:
        if response_access.log_if_dm_blocked(bot.name, user_id, "Reminder DM fallback"):
            return [], None

    channels_to_try = []
    if allow_dm_fallback and primary_channel is None:
        fallback_dm = await bot._resolve_user_dm_channel(user_id)
        if fallback_dm:
            channels_to_try.append(("dm_fallback", fallback_dm))
    if primary_channel:
        channels_to_try.append(("primary", primary_channel))

    if source_type != "dm" and primary_channel is not None:
        fallback_dm = await bot._resolve_user_dm_channel(user_id)
        if fallback_dm and all(getattr(channel, "id", None) != getattr(fallback_dm, "id", None) for _, channel in channels_to_try):
            channels_to_try.append(("dm_fallback", fallback_dm))

    for channel_mode, channel in channels_to_try:
        if channel is None:
            continue
        sent_messages = []
        try:
            rendered_response = convert_emojis_in_text(response, guild) if guild else response
            lines = bot._split_response_for_delivery(rendered_response)
            for index, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                if channel_mode == "primary" and source_type != "dm" and index == 0:
                    line = f"<@{user_id}> {line}"
                sent_messages.append(await channel.send(line))

            if sent_messages:
                delivered = "\n\n".join(getattr(sent, "content", "") or "" for sent in sent_messages).strip()
                add_to_history(
                    getattr(channel, "id", source_channel_id),
                    "assistant",
                    delivered,
                    author_name=bot.character.name,
                    guild=getattr(channel, "guild", None),
                    timestamp=getattr(sent_messages[0], "created_at", None)
                )
                if len(sent_messages) > 1:
                    multipart_ids = [getattr(sent, "id", None) for sent in sent_messages if getattr(sent, "id", None) is not None]
                    if len(multipart_ids) > 1:
                        store_multipart_response(getattr(channel, "id", source_channel_id), multipart_ids, delivered)
                return sent_messages, getattr(channel, "id", source_channel_id)
        except discord.HTTPException as e:
            log.warn(f"Reminder send failed via {channel_mode}: {e}", bot.name)
            continue

    return [], None
