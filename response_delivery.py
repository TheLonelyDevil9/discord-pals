"""
Final response formatting before Discord delivery.

Keep this boundary deliberately conservative. It should preserve generated prose
unless the model used an explicit blank-line paragraph break or Discord requires
a hard length split.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from constants import MAX_MESSAGE_LENGTH, MAX_RESPONSE_MESSAGE_PARTS
from discord_utils import split_message


@dataclass(frozen=True)
class DeliveryFormatOptions:
    """Options for final response splitting."""

    max_message_length: int = MAX_MESSAGE_LENGTH
    max_parts: int = MAX_RESPONSE_MESSAGE_PARTS


def format_response_for_delivery(
    response: str,
    options: DeliveryFormatOptions | None = None,
) -> list[str]:
    """Turn one generated response into conservative Discord message parts."""
    options = options or DeliveryFormatOptions()
    normalized = _normalize_response_text(response)
    if not normalized:
        return []

    logical_parts = _cap_logical_parts(_split_explicit_paragraphs(normalized), options.max_parts)

    final_parts: list[str] = []
    for part in logical_parts:
        final_parts.extend(split_message(part, max_length=options.max_message_length))

    return [part.strip() for part in final_parts if part and part.strip()]


def _normalize_response_text(response: str) -> str:
    text = (response or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def _split_explicit_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n[ \t]*\n(?:[ \t]*\n)*", text) if part and part.strip()]


def _cap_logical_parts(parts: list[str], max_parts: int) -> list[str]:
    if max_parts <= 0 or len(parts) <= max_parts:
        return parts

    overflow = "\n\n".join(parts[max_parts - 1:])
    return parts[:max_parts - 1] + [overflow]
