"""Post-delivery side effects for confirmed Discord responses."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import diagnostic_events
import runtime_config
from delivery_pipeline import DeliveryOutcome
from discord_utils import add_to_history, store_multipart_response
from prometheus_metrics import metrics_manager
from scopes import memory_server_id


@dataclass(frozen=True)
class PostResponseTaskContext:
    """Visible, confirmed delivery facts used by post-response side effects."""

    channel_id: Any
    discord_channel_id: int
    guild_id: int | None
    guild: Any
    is_dm: bool
    user_id: int
    user_name: str
    content: str
    delivered_response: str
    sent_records: tuple[dict, ...]
    reactions: tuple[Any, ...]
    split_target: Any
    context: dict
    message: Any
    request: dict
    req_id: str | None
    delivery_outcome: DeliveryOutcome | None = None

    @property
    def persistable_visible_text(self) -> str:
        if self.delivery_outcome is not None:
            return self.delivery_outcome.persistable_visible_text
        return self.delivered_response


class PostResponseTasks:
    """Runs work that must happen only after visible delivery is confirmed."""

    def __init__(self, bot_instance: Any):
        self.bot = bot_instance

    def run_after_confirmed_delivery(self, task_context: PostResponseTaskContext) -> None:
        """Record confirmed visible output, then schedule non-blocking follow-ups."""
        bot = self.bot
        channel_id = task_context.channel_id
        delivered_response = task_context.persistable_visible_text

        bot._record_emoji_budget(channel_id, delivered_response)
        bot._remember_recent_response(channel_id, delivered_response)
        bot._reset_failures(channel_id)
        bot._record_response(channel_id)
        bot._update_mood(channel_id, task_context.content, delivered_response)

        for record in task_context.sent_records:
            sent_message = record["message"]
            add_to_history(
                channel_id,
                "assistant",
                record["content"],
                author_name=bot.character.name,
                guild=task_context.guild,
                message_id=getattr(sent_message, "id", None),
                timestamp=getattr(sent_message, "created_at", None),
                req_id=task_context.req_id,
            )

        runtime_config.update_last_activity(bot.name)
        metrics_manager.update_last_activity(bot_name=bot.name, timestamp=time.time())

        if len(task_context.sent_records) > 1:
            multipart_ids = [
                msg_id for msg_id in
                (getattr(record["message"], "id", None) for record in task_context.sent_records)
                if msg_id is not None
            ]
            if len(multipart_ids) > 1:
                store_multipart_response(channel_id, multipart_ids, delivered_response)

        diagnostic_events.log_delivery_complete(
            bot.name,
            task_context.req_id,
            channel_id=channel_id,
            user_id=task_context.user_id,
            sent_records=list(task_context.sent_records),
            delivered_response=delivered_response,
            reactions=list(task_context.reactions),
            split_target=task_context.split_target,
            delivery_outcome=task_context.delivery_outcome,
        )

        if task_context.reactions:
            asyncio.create_task(
                bot._send_staggered_reactions(
                    task_context.message,
                    list(task_context.reactions),
                    task_context.guild,
                )
            )

        memory_scope_id = memory_server_id(
            bot.name,
            task_context.guild_id,
            is_dm=task_context.is_dm,
        )
        asyncio.create_task(
            bot._maybe_auto_memory(
                channel_id,
                task_context.is_dm,
                memory_scope_id,
                task_context.user_id,
                task_context.content,
                task_context.user_name,
            )
        )
        asyncio.create_task(
            bot._maybe_handle_reminder_capture(
                task_context.context,
                task_context.message,
                task_context.request,
            )
        )
