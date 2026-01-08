"""
Discord Pals - AI Providers
3-tier fallback system using OpenAI-compatible API with rate limit handling.
"""

from openai import AsyncOpenAI, RateLimitError, APIError
from typing import List, Dict, Optional
from config import PROVIDERS, API_TIMEOUT
from discord_utils import remove_thinking_tags
import asyncio
import logger as log

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    log.warn("PyYAML not installed - include_body/exclude_body features disabled")


MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff: 1s, 2s, 4s


def deep_merge_dict(base: dict, override: dict) -> None:
    """Deep merge override dict into base dict in-place."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge_dict(base[key], value)
        else:
            base[key] = value


def merge_yaml_to_dict(target: dict, yaml_string: str) -> None:
    """
    Merge YAML-formatted string into target dict (SillyTavern style).
    Supports both object notation and array of objects.
    Uses deep merge for nested objects (e.g., GLM thinking config).

    Example YAML:
        min_p: 0.1
        top_k: 40
    Or:
        - min_p: 0.1
        - top_k: 40
    Or nested:
        thinking:
          type: disabled
    """
    if not yaml_string or not yaml_string.strip():
        return
    if not YAML_AVAILABLE:
        return
    try:
        parsed = yaml.safe_load(yaml_string)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    deep_merge_dict(target, item)
        elif isinstance(parsed, dict):
            deep_merge_dict(target, parsed)
    except Exception as e:
        log.debug(f"Failed to parse include_body YAML: {e}")


def exclude_keys_by_yaml(target: dict, yaml_string: str) -> None:
    """
    Remove keys from dict based on YAML list or object keys (SillyTavern style).

    Example YAML:
        - frequency_penalty
        - presence_penalty
    Or as object:
        frequency_penalty: true
        presence_penalty: true
    """
    if not yaml_string or not yaml_string.strip():
        return
    if not YAML_AVAILABLE:
        return
    try:
        parsed = yaml.safe_load(yaml_string)
        if isinstance(parsed, list):
            for key in parsed:
                if isinstance(key, str):
                    target.pop(key, None)
        elif isinstance(parsed, dict):
            for key in parsed.keys():
                target.pop(key, None)
        elif isinstance(parsed, str):
            target.pop(parsed, None)
    except Exception as e:
        log.debug(f"Failed to parse exclude_body YAML: {e}")


def is_multimodal_content(content) -> bool:
    """Check if content is multimodal (list with image_url type)."""
    if not isinstance(content, list):
        return False
    return any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for part in content
    )


def validate_messages(messages: List[dict]) -> List[dict]:
    """
    Validate and sanitize messages before sending to API.
    Ensures no malformed JSON or None values are sent.
    Preserves multimodal content (list format with images).
    """
    validated = []
    for msg in messages:
        # Ensure role exists and is valid
        role = msg.get("role", "user")
        if role not in ("system", "user", "assistant"):
            role = "user"

        content = msg.get("content")

        # Preserve multimodal content (list with image_url)
        if is_multimodal_content(content):
            # Validate each part of multimodal content
            validated_parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text = part.get("text", "")
                        # Keep text parts even if empty (some APIs need them)
                        validated_parts.append({"type": "text", "text": text.replace("\x00", "") if text else ""})
                    elif part.get("type") == "image_url":
                        validated_parts.append(part)
            # Ensure we have at least one text part
            if not any(p.get("type") == "text" for p in validated_parts):
                validated_parts.insert(0, {"type": "text", "text": ""})
            validated.append({"role": role, "content": validated_parts})
        else:
            # Standard string content
            if content is None:
                content = ""
            elif not isinstance(content, str):
                content = str(content)

            # Remove any null bytes or invalid characters
            content = content.replace("\x00", "")
            validated.append({"role": role, "content": content})

    return validated


def has_multimodal_message(messages: List[dict]) -> bool:
    """Check if any message contains multimodal content."""
    return any(is_multimodal_content(msg.get("content")) for msg in messages)


def strip_images_from_messages(messages: List[dict]) -> List[dict]:
    """Convert multimodal messages to text-only for non-vision providers."""
    stripped = []
    for msg in messages:
        content = msg.get("content")
        if is_multimodal_content(content):
            # Extract only text parts
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        text_parts.append(text)
            # Add note about image
            text_parts.append("[User sent an image that this model cannot see]")
            stripped.append({"role": msg.get("role", "user"), "content": "\n".join(text_parts)})
        else:
            stripped.append(msg)
    return stripped


def format_as_single_user(messages: List[dict], system_prompt: str) -> List[dict]:
    """
    Format multi-message array as a single user message (SillyTavern style).

    Instead of sending:
        [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]

    We send everything as one user message:
        [{"role": "user", "content": "### Instructions\n...\nUser: ...\nAssistant: ..."}]

    This improves compatibility with various LLM backends and matches
    SillyTavern's "Single user message (no tools)" format.

    Note: Uses "Author: message" format (no brackets) to prevent LLMs from
    learning and outputting bracket patterns in their responses.

    Note: If messages contain multimodal content (images), this function
    returns None to signal that single-user mode should be bypassed.
    """
    # Check for multimodal content - cannot use single-user mode with images
    if has_multimodal_message(messages):
        return None

    parts = []

    # Add system prompt first
    if system_prompt and system_prompt.strip():
        parts.append(f"### Instructions\n{system_prompt.strip()}")

    # Add conversation messages
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            content = content.strip()

        if not content:
            continue

        if role == "system":
            parts.append(f"### Instructions\n{content}")
        elif role == "user":
            # Check if content already has Author: prefix (from format_history_split)
            # to avoid double-prefixing
            # Look for pattern like "Name: " at start (not brackets)
            if ": " in content[:50] and not content.startswith("("):
                first_colon = content.find(": ")
                prefix = content[:first_colon]
                # If prefix looks like a name (no special chars except spaces), it's already formatted
                if first_colon < 30 and prefix.replace(" ", "").isalnum():
                    parts.append(content)
                    continue
            # Add author prefix - use "author" key (not "author_name")
            author = msg.get("author", "User")
            parts.append(f"{author}: {content}")
        elif role == "assistant":
            # Bot's own messages - check for existing prefix
            if ": " in content[:50] and not content.startswith("("):
                first_colon = content.find(": ")
                prefix = content[:first_colon]
                if first_colon < 30 and prefix.replace(" ", "").isalnum():
                    parts.append(content)
                    continue
            # Always prefix assistant messages with author name
            author = msg.get("author", "Assistant")
            parts.append(f"{author}: {content}")
    
    # Combine all parts into a single user message
    combined = "\n\n".join(parts)
    
    return [{"role": "user", "content": combined}]


class AIProviderManager:
    """Manages 3-tier AI provider fallback with retry logic."""
    
    def __init__(self):
        self.providers: Dict[str, AsyncOpenAI] = {}
        self.status: Dict[str, str] = {"primary": "unknown", "secondary": "unknown", "fallback": "unknown"}
        
        for tier, cfg in PROVIDERS.items():
            key = cfg.get("key")
            if key:
                self.providers[tier] = AsyncOpenAI(
                    base_url=cfg["url"],
                    api_key=cfg["key"],
                    timeout=API_TIMEOUT
                )
    
    async def _try_generate(
        self,
        client: AsyncOpenAI,
        model: str,
        messages: List[dict],
        temperature: float,
        max_tokens: int,
        tier: str,
        extra_body: Optional[dict] = None,
        include_body: str = "",
        exclude_body: str = ""
    ) -> str | None:
        """Try to generate with retries on rate limit."""
        
        for attempt in range(MAX_RETRIES):
            try:
                # Build request kwargs
                request_kwargs = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
                
                # Apply SillyTavern-style YAML parameters (preferred)
                if include_body:
                    merge_yaml_to_dict(request_kwargs, include_body)
                    log.debug(f"[{tier}] Applied include_body YAML")
                
                # Remove keys specified in exclude_body
                if exclude_body:
                    exclude_keys_by_yaml(request_kwargs, exclude_body)
                    log.debug(f"[{tier}] Applied exclude_body YAML")
                
                # Legacy: Add extra_body if provided (for backward compatibility)
                if extra_body:
                    request_kwargs["extra_body"] = extra_body
                    log.debug(f"[{tier}] Using extra_body: {extra_body}")
                
                log.debug(f"[{tier}] Requesting {model} with {len(messages)} messages, max_tokens={max_tokens}")
                
                response = await asyncio.wait_for(
                    client.chat.completions.create(**request_kwargs),
                    timeout=API_TIMEOUT
                )
                
                # Check for valid response
                if response and response.choices and len(response.choices) > 0:
                    choice = response.choices[0]
                    content = choice.message.content
                    
                    # Strip reasoning_content if present (GLM thinking mode leaks)
                    if hasattr(choice.message, 'reasoning_content') and choice.message.reasoning_content:
                        log.debug(f"[{tier}] Stripped {len(choice.message.reasoning_content)} chars of reasoning_content")
                    
                    if not content or content.strip() == "":
                        # Detailed logging for empty responses
                        log.warn(f"[{tier}] Empty content from {model}")
                        log.warn(f"[{tier}] finish_reason={choice.finish_reason}")
                        if hasattr(choice.message, 'refusal') and choice.message.refusal:
                            log.warn(f"[{tier}] Refusal: {choice.message.refusal}")
                        if extra_body:
                            log.warn(f"[{tier}] extra_body was: {extra_body}")
                        return None  # Return None to trigger fallback to next provider
                    
                    log.ok(f"[{tier}] Got {len(content)} chars from {model}")

                    # Raw generation logging (when enabled)
                    import runtime_config
                    if runtime_config.get("raw_generation_logging", False):
                        # Log in chunks to avoid overwhelming logs
                        preview_len = 1000
                        if len(content) <= preview_len:
                            log.info(f"[RAW-GEN] {content}")
                        else:
                            log.info(f"[RAW-GEN] {content[:preview_len]}... ({len(content)} chars total)")

                    # Strip any reasoning/thinking content that leaked into output
                    content = remove_thinking_tags(content)

                    return content
                else:
                    log.warn(f"[{tier}] No choices in response from {model}")
                    if response:
                        log.warn(f"[{tier}] Response: {str(response)[:200]}")
                return None
                
            except RateLimitError as e:
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    log.warn(f"[{tier}] Rate limited, retrying in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    log.error(f"[{tier}] Rate limited, max retries exceeded")
                    raise
            except asyncio.TimeoutError:
                raise
            except APIError as e:
                if e.status_code == 429 and attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    log.warn(f"[{tier}] Rate limited (429), retrying in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    raise
        
        return None
    
    async def generate(
        self,
        messages: List[dict],
        system_prompt: str,
        temperature: float = None,
        max_tokens: int = None,
        use_single_user: bool = True  # SillyTavern-style by default
    ) -> str:
        """Generate response with automatic fallback and retry (3 full cycles).

        Args:
            messages: Conversation messages
            system_prompt: System prompt / character definition
            temperature: Response randomness (0-2). If None, uses per-provider config.
            max_tokens: Max response length. If None, uses per-provider config.
            use_single_user: If True (default), format as single user message
                             like SillyTavern's "Single user message (no tools)"
        """

        # Check if we have multimodal content
        has_images = has_multimodal_message(messages)

        # Format messages based on mode
        if use_single_user:
            # SillyTavern-style: combine everything into one user message
            # Returns None if messages contain multimodal content (images)
            full_messages = format_as_single_user(messages, system_prompt)
            if full_messages is None:
                # Multimodal content detected - fall back to multi-message format
                log.info("Multimodal content detected, using multi-message format")
                full_messages = [{"role": "system", "content": system_prompt}] + messages
                full_messages = validate_messages(full_messages)
        else:
            # Legacy multi-message format with roles
            full_messages = [{"role": "system", "content": system_prompt}] + messages
            full_messages = validate_messages(full_messages)

        # Prepare text-only version for non-vision providers
        text_only_messages = None
        if has_images:
            text_only_messages = strip_images_from_messages(full_messages)

        # Retry all providers up to 3 full cycles
        for cycle in range(3):
            for tier in ["primary", "secondary", "fallback"]:
                if tier not in self.providers:
                    self.status[tier] = "no key"
                    continue

                model = PROVIDERS[tier]["model"]
                extra_body = PROVIDERS[tier].get("extra_body", {})
                include_body = PROVIDERS[tier].get("include_body", "")
                exclude_body = PROVIDERS[tier].get("exclude_body", "")

                # Check if provider supports vision (default True, set false for text-only models)
                supports_vision = PROVIDERS[tier].get("supports_vision", True)

                # Use text-only messages for non-vision providers
                messages_to_send = full_messages
                if has_images and not supports_vision:
                    log.info(f"[{tier}] Provider doesn't support vision, using text-only")
                    messages_to_send = text_only_messages

                # Use per-provider settings, with fallback to function args or defaults
                provider_max_tokens = PROVIDERS[tier].get("max_tokens", 8192)
                provider_temperature = PROVIDERS[tier].get("temperature", 1.0)

                # Allow function args to override provider config
                effective_max_tokens = max_tokens if max_tokens is not None else provider_max_tokens
                effective_temperature = temperature if temperature is not None else provider_temperature

                try:
                    client = self.providers[tier]
                    result = await self._try_generate(
                        client, model, messages_to_send, effective_temperature, effective_max_tokens, tier,
                        extra_body=extra_body if extra_body else None,
                        include_body=include_body,
                        exclude_body=exclude_body
                    )
                    
                    if result:
                        self.status[tier] = "ok"
                        return result
                    else:
                        self.status[tier] = "empty response"
                        continue
                    
                except asyncio.TimeoutError:
                    self.status[tier] = "timeout"
                    log.error(f"[{tier}] Timeout after {API_TIMEOUT}s")
                    continue
                except RateLimitError:
                    self.status[tier] = "rate limited"
                    continue
                except Exception as e:
                    self.status[tier] = "error"
                    log.error(f"[{tier}] {str(e)[:100]}")
                    continue
            
            # Wait before next cycle
            if cycle < 2:
                await asyncio.sleep(2)
        
        # Silent fail - no public error message
        log.error("All providers failed after 3 cycles")
        return None
    
    def get_status(self) -> str:
        """Get formatted status of all providers."""
        lines = ["**Provider Status:**"]
        for tier in PROVIDERS.keys():
            name = PROVIDERS[tier]["name"]
            status = self.status.get(tier, "unknown")
            emoji = "✅" if status == "ok" else "❓" if status == "unknown" else "❌"
            lines.append(f"• {name}: {emoji} {status}")
        return "\n".join(lines)


# Global instance
provider_manager = AIProviderManager()
