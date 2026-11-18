"""
Discord Pals - Image Attachments
Turns the images a user attaches into multimodal parts for the model.

While a reply is built, each image is in memory several times: the downloaded
bytes, their base64 text, and the provider request body. The size and count
limits keep one message with large images from using up a small host's memory.
"""

import base64
from typing import List, Optional

import logger as log
import runtime_config
from discord_utils import get_http_session, strip_discord_ooc_comments

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
_READ_CHUNK_BYTES = 64 * 1024
_MB = 1024 * 1024


class _ImageTooLarge(Exception):
    pass


async def _download_image(url: str, max_bytes: int) -> bytearray:
    """Return the image bytes, or raise _ImageTooLarge before more than max_bytes plus one chunk is read.

    The response's declared length is checked first and the running total while reading,
    because a declared length can be wrong or absent.
    """
    session = await get_http_session()
    async with session.get(url) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        if response.content_length is not None and response.content_length > max_bytes:
            raise _ImageTooLarge()
        data = bytearray()
        async for chunk in response.content.iter_chunked(_READ_CHUNK_BYTES):
            data.extend(chunk)
            if len(data) > max_bytes:
                raise _ImageTooLarge()
        return data


def _log_skipped(filename: str, detail: str, reason: str, bot_name: str | None, req_id: str | None) -> None:
    log.warn(
        f"Skipped image {filename}: {detail}",
        bot_name,
        component="context",
        event="image_skipped",
        req_id=req_id,
        reason=reason,
    )


async def process_attachments(message, *, bot_name: str | None = None,
                              req_id: str | None = None) -> Optional[List[dict]]:
    """Return the message text and its images as multimodal parts, or None when no image is sent.

    An image past the per-message count, over the size limit, or failing to download is
    skipped with one log line, and the reply goes ahead with the images that fit.
    """
    content_parts = []

    visible_content = strip_discord_ooc_comments(message.content or "")
    if visible_content and visible_content.strip():
        content_parts.append({"type": "text", "text": visible_content.strip()})

    max_mb = int(runtime_config.get("image_attachment_max_mb"))
    max_count = int(runtime_config.get("image_attachment_max_count"))
    max_bytes = max_mb * _MB
    included = 0
    for attachment in message.attachments:
        filename = attachment.filename
        if not filename.lower().endswith(IMAGE_EXTENSIONS):
            continue
        if included >= max_count:
            _log_skipped(filename, f"over the limit of {max_count} images per message", "count_limit", bot_name, req_id)
            continue
        declared_size = getattr(attachment, "size", None) or 0
        if declared_size > max_bytes:
            _log_skipped(
                filename,
                f"{declared_size / _MB:.1f} MB is over the {max_mb} MB image size limit",
                "size_limit",
                bot_name,
                req_id,
            )
            continue
        try:
            base64_data = base64.b64encode(await _download_image(attachment.url, max_bytes)).decode("ascii")
        except _ImageTooLarge:
            _log_skipped(filename, f"the download passed the {max_mb} MB image size limit", "size_limit", bot_name, req_id)
            continue
        except Exception as e:
            _log_skipped(filename, f"download failed: {e}", "download_failed", bot_name, req_id)
            continue
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/{filename.split('.')[-1]};base64,{base64_data}"}
        })
        included += 1

    # Return multimodal content if we have any images
    has_images = any(p.get("type") == "image_url" for p in content_parts)
    if has_images:
        # Ensure there's always a text part (required by some APIs)
        if not any(p.get("type") == "text" for p in content_parts):
            content_parts.insert(0, {"type": "text", "text": "(user sent an image)"})
        return content_parts
    return None
