"""Runtime Discord delivery adapters built on the typed delivery pipeline."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

import discord

import diagnostic_events
import logger as log
from delivery_pipeline import DeliveryOutcome, DeliveryPlan, DeliveryState, deliver_multipart_response


@dataclass(frozen=True)
class RuntimeDeliveryBatch:
    """Confirmed Discord records plus the typed delivery outcome behind them."""

    outcome: DeliveryOutcome
    sent_records: tuple[dict, ...]

    @property
    def persistable_visible_text(self) -> str:
        return self.outcome.persistable_visible_text


async def deliver_reply_multipart(
    *,
    bot_name: str,
    message: discord.Message,
    lines: list[str],
    req_id: str | None,
) -> RuntimeDeliveryBatch | None:
    """Deliver multipart text as a reply to the source Discord message."""

    if not lines:
        return None
    diagnostic_events.log_delivery_split(bot_name, req_id, getattr(message, "channel", None), lines)

    sent_messages = {}
    is_synthetic = hasattr(message, "_interaction") and message._interaction is not None

    async def send_part(part):
        i = part.part_index
        line = part.visible_text
        try:
            started = time.perf_counter()
            if i == 0:
                if is_synthetic:
                    sent_msg = await message._interaction.followup.send(line)
                else:
                    sent_msg = await message.reply(line)
            else:
                await asyncio.sleep(random.uniform(0.5, 1.0))
                sent_msg = await message.channel.send(line)
            sent_messages[i] = sent_msg
            diagnostic_events.log_discord_send(
                bot_name,
                req_id,
                getattr(message, "channel", None),
                sent_msg,
                part=i + 1,
                total_parts=len(lines),
                content_len=len(line),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            message_id = getattr(sent_msg, "id", None)
            return str(message_id) if message_id is not None else ""
        except discord.HTTPException as e:
            diagnostic_events.log_discord_send_failed(
                bot_name,
                req_id,
                getattr(message, "channel", None),
                e,
                part=i + 1,
                total_parts=len(lines),
            )
            raise

    plan = DeliveryPlan.from_parts(
        lines,
        correlation_id=req_id or log.new_request_id(),
        idempotency_key=f"{bot_name}:{getattr(message, 'id', 'unknown')}:{req_id or 'no-req'}",
    )
    outcome = await deliver_multipart_response(plan, send_part)
    _warn_if_incomplete(bot_name, req_id, outcome, "Response delivery", "delivery_partial", "delivery_failed")
    return _batch_from_outcome(outcome, sent_messages)


async def deliver_channel_multipart(
    *,
    bot_name: str,
    channel,
    lines: list[str],
    req_id: str | None,
) -> RuntimeDeliveryBatch | None:
    """Deliver multipart text directly to a Discord channel."""

    if not lines:
        return None
    diagnostic_events.log_delivery_split(bot_name, req_id, channel, lines, direct_channel=True)

    sent_messages = {}

    async def send_part(part):
        i = part.part_index
        line = part.visible_text
        try:
            started = time.perf_counter()
            if i > 0:
                await asyncio.sleep(random.uniform(0.5, 1.0))
            sent_msg = await channel.send(line)
            sent_messages[i] = sent_msg
            diagnostic_events.log_discord_send(
                bot_name,
                req_id,
                channel,
                sent_msg,
                part=i + 1,
                total_parts=len(lines),
                content_len=len(line),
                latency_ms=int((time.perf_counter() - started) * 1000),
                direct_channel=True,
            )
            message_id = getattr(sent_msg, "id", None)
            return str(message_id) if message_id is not None else ""
        except discord.HTTPException as e:
            diagnostic_events.log_discord_send_failed(bot_name, req_id, channel, e, part=i + 1, total_parts=len(lines))
            raise

    plan = DeliveryPlan.from_parts(
        lines,
        correlation_id=req_id or log.new_request_id(),
        idempotency_key=f"{bot_name}:{getattr(channel, 'id', 'direct')}:{req_id or 'no-req'}",
    )
    outcome = await deliver_multipart_response(plan, send_part)
    _warn_if_incomplete(bot_name, req_id, outcome, "Direct channel delivery", "delivery_partial", "delivery_failed")
    return _batch_from_outcome(outcome, sent_messages)


def _warn_if_incomplete(
    bot_name: str,
    req_id: str | None,
    outcome: DeliveryOutcome,
    label: str,
    partial_event: str,
    failed_event: str,
) -> None:
    if outcome.state == DeliveryState.SUCCESS:
        return
    log.warn(
        f"{label} completed without all parts confirmed",
        bot_name,
        component="delivery",
        event=partial_event if outcome.confirmed_parts else failed_event,
        req_id=req_id,
        state=outcome.state.value,
        confirmed_parts=len(outcome.confirmed_parts),
        failed_part_index=outcome.failed_part_index,
        ambiguous_part_index=outcome.ambiguous_part_index,
    )


def _batch_from_outcome(outcome: DeliveryOutcome, sent_messages: dict[int, object]) -> RuntimeDeliveryBatch:
    sent_records = tuple(
        {"message": sent_messages[part.part_index], "content": part.visible_text}
        for part in outcome.confirmed_parts
        if part.part_index in sent_messages
    )
    return RuntimeDeliveryBatch(outcome=outcome, sent_records=sent_records)
