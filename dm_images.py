"""Helpers for autonomous generated images in DMs."""

import io
import random
import time

import discord

import runtime_config
import diagnostic_events
import logger as log
from discord_utils import add_to_history
from provider_gateway import provider_gateway as provider_manager
from response_sanitizer import sanitize_response


def should_send_dm_image_followup() -> bool:
    """Return whether this follow-up should become a random generated image."""
    if not runtime_config.get("dm_image_generation_enabled", False):
        return False
    chance = runtime_config.get("dm_image_generation_chance", 0.25)
    return random.random() < chance


def build_dm_image_prompt(*, character_name: str, user_name: str, history: list[dict]) -> str:
    """Build a concise image prompt from configured style plus recent DM context."""
    base_prompt = runtime_config.get("dm_image_generation_prompt", "")
    if not str(base_prompt or "").strip():
        base_prompt = runtime_config.DEFAULTS["dm_image_generation_prompt"]

    recent_lines = []
    for item in history[-4:]:
        content = " ".join(str(item.get("content", "")).split())
        if not content:
            continue
        author = item.get("author") or ("You" if item.get("role") == "assistant" else user_name or "User")
        recent_lines.append(f"{author}: {content[:120]}")

    context = "\n".join(recent_lines) if recent_lines else "No recent DM context."
    return (
        f"Create one image {character_name} might casually send in a private Discord DM.\n"
        f"Style goal: {base_prompt}\n"
        "Make it funny, low-stakes, and a little contextless like a human sending a random picture. "
        "Avoid readable text, logos, UI screenshots, gore, sexual content, or anything that targets a real person.\n"
        f"Recent DM context for loose inspiration:\n{context}"
    )


def should_generate_dm_image_caption() -> bool:
    """Return whether a generated image should include a caption."""
    return random.random() < runtime_config.get("dm_image_generation_caption_chance", 0.85)


def build_dm_image_caption_prompt(*, character_name: str, user_name: str, image_prompt: str) -> tuple[str, str]:
    """Build the caption prompt and system prompt for one generated image."""
    prompt = (
        f"Write one very short Discord DM caption as {character_name} for an image they just sent to {user_name or 'the user'}.\n"
        "Keep it under 90 characters. Make it casual, in-character, and a little amused. "
        "Do not use speaker labels, hashtags, markdown, or explain the image.\n"
        f"Image idea: {image_prompt[:500]}"
    )
    system_prompt = (
        f"You are {character_name}. Write only the caption text. No roleplay narration, no labels, no alternatives."
    )
    return prompt, system_prompt


async def build_dm_image_caption(*, bot_name: str, character_name: str, user_name: str, image_prompt: str) -> str:
    """Optionally generate a short in-character caption for an autonomous image."""
    if not should_generate_dm_image_caption():
        return ""

    prompt, system_prompt = build_dm_image_caption_prompt(
        character_name=character_name,
        user_name=user_name,
        image_prompt=image_prompt,
    )
    try:
        caption = await provider_manager.generate(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            max_tokens=64,
        )
    except Exception as e:
        log.warn(f"DM image caption generation failed: {e}", bot_name)
        return ""

    if not caption or caption.startswith("❌"):
        return ""
    return sanitize_response(caption, character_name).replace("\n", " ").strip()[:180]


async def send_image_to_channel(
    *,
    bot_name: str,
    channel,
    image_result: dict,
    caption: str = "",
    req_id: str | None = None,
):
    """Send generated image bytes to a channel and return the sent Discord message."""
    image_bytes = image_result.get("bytes") if isinstance(image_result, dict) else None
    if not image_bytes:
        return None

    filename = image_result.get("filename") or "discord-pals-image.png"
    try:
        started = time.perf_counter()
        image_file = discord.File(io.BytesIO(image_bytes), filename=filename)
        sent_msg = await channel.send(content=caption or None, file=image_file)
        diagnostic_events.log_discord_send(
            bot_name,
            req_id,
            channel,
            sent_msg,
            part=1,
            total_parts=1,
            content_len=len(caption or ""),
            latency_ms=int((time.perf_counter() - started) * 1000),
            direct_channel=True,
        )
        return sent_msg
    except discord.HTTPException as e:
        diagnostic_events.log_discord_send_failed(bot_name, req_id, channel, e, part=1, total_parts=1)
        return None


async def generate_and_send_dm_image_followup(
    *,
    bot_name: str,
    channel,
    user_id: int,
    user_name: str,
    character_name: str,
    state: dict,
    history: list[dict],
    history_id,
    followups_sent: int,
    max_followups: int,
    now: float,
    req_id: str | None = None,
) -> bool:
    """Generate and send one autonomous DM image, then record history only after success."""
    image_prompt = build_dm_image_prompt(character_name=character_name, user_name=user_name, history=history)
    image_result = await provider_manager.generate_image(
        image_prompt,
        preferred_tier=runtime_config.get("dm_image_generation_preferred_tier", ""),
        req_id=req_id,
    )
    if not image_result:
        log.warn(f"DM image generation failed for user {user_id}", bot_name)
        return False

    caption = await build_dm_image_caption(
        bot_name=bot_name,
        character_name=character_name,
        user_name=user_name,
        image_prompt=image_prompt,
    )
    sent_msg = await send_image_to_channel(
        bot_name=bot_name,
        channel=channel,
        image_result=image_result,
        caption=caption,
        req_id=req_id,
    )
    if not sent_msg:
        log.warn(f"DM image send failed for user {user_id}", bot_name)
        return False

    add_to_history(
        history_id,
        "assistant",
        caption or "[Sent a generated image]",
        author_name=character_name,
        timestamp=getattr(sent_msg, "created_at", None),
    )
    state["followups_sent"] = followups_sent + 1
    state["last_followup"] = now
    log.info(f"Sent DM image follow-up to user {user_id} ({followups_sent + 1}/{max_followups})", bot_name)
    return True
